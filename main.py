import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, FastAPI

from src.dal.db import init_db
from src.dal.models import VideoStatus
from src.dal.repository import (
    claim_next_pending_video,
    create_video,
    log_event,
    requeue_processing_videos,
    set_video_error,
    update_video_status,
)
from src.gateway.telegram_bot import TelegramTrendBot
from src.ingestion.main import get_trends
from src.media.compiler.assemble import compile_video
from src.media.compiler.music import pick_music
from src.media.compiler.timeline import build_timeline
from src.media.main import build_media
from src.media.paths import JobWorkspace
from src.media.schemas import JobArtifacts, ScriptPayload
from src.publisher.queue import mark_rendered
from src.publisher.scheduler import tick as publisher_tick
from src.upload.routes import router as youtube_auth_router

logger = logging.getLogger(__name__)


scheduler = AsyncIOScheduler()
publisher_tick_lock = Lock()


def run_publisher_tick() -> None:
    """Run one serialized publisher pass."""
    try:
        with publisher_tick_lock:
            result = publisher_tick()
        logger.info("publisher tick result: %s", result)
    except Exception:  # noqa: BLE001
        logger.exception("publisher tick failed; rendered jobs remain queued")


async def daily_trend_job(bot: TelegramTrendBot):
    trends = await asyncio.to_thread(get_trends)
    chat_id = int(os.getenv("CHAT_ID", "0"))
    if chat_id:
        await bot.send_telegram_digest(trends, update=None, chat_id=chat_id)
    else:
        logger.warning("CHAT_ID not set; skipping trend digest push.")


def generate_videos_for_topics(topics: list[str]) -> list[str]:
    """Persist selected topics as pending jobs for the media worker."""
    queued: list[str] = []
    for topic in dict.fromkeys(topics):
        job_id = str(uuid4())
        try:
            create_video(job_id=job_id, topic=topic, status=VideoStatus.PENDING)
            log_event("job.queued", job_id=job_id, detail=topic)
            queued.append(job_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("failed to queue topic %r: %s", topic, exc)
    logger.info("queued %d/%d topic(s)", len(queued), len(topics))
    return queued


async def enqueue_confirmed_topics(chat_id: int, topics: list[str]) -> None:
    job_ids = await asyncio.to_thread(generate_videos_for_topics, topics)
    if not job_ids:
        await app.state.telegram_bot.app.bot.send_message(
            chat_id=chat_id,
            text="No jobs could be queued. Check the application logs.",
        )
        return
    await app.state.telegram_bot.app.bot.send_message(
        chat_id=chat_id,
        text=f"Queued {len(job_ids)} video job(s):\n" + "\n".join(job_ids),
    )


async def process_video_job(job) -> None:
    """Run agents, media generation, and final MP4 compilation for one job."""
    from agents.test import run_over_topics

    plans = await asyncio.to_thread(
        run_over_topics,
        selected_topics=[job.topic],
        base_config={
            "platform": "youtube_shorts",
            "duration_seconds": 45,
            "audience": "general",
            "tone": "engaging",
            "goal": "educate and entertain",
        },
        feed_title="Google Trends Daily",
        published=datetime.now(timezone.utc).date().isoformat(),
    )
    if not plans:
        raise RuntimeError("Agent workflow returned no final plan")

    plan = plans[0]
    if plan.get("decision") != "APPROVE" or not plan.get("script"):
        update_video_status(
            job.job_id,
            VideoStatus.SKIPPED,
            error=f"Agent decision: {plan.get('decision', 'unknown')}",
        )
        log_event("job.skipped", job_id=job.job_id, detail=str(plan.get("decision")))
        return

    script = ScriptPayload.model_validate(plan["script"])
    workspace = JobWorkspace(job.job_id).create()
    artifacts = await build_media(
        script,
        workspace,
        artifacts=JobArtifacts(job_id=job.job_id, topic=job.topic),
    )
    if not artifacts.audio or not artifacts.subtitles or not artifacts.visuals:
        raise RuntimeError("Media pipeline did not produce all required artifacts")

    timeline = build_timeline(artifacts.audio, artifacts.visuals)
    video_path = await asyncio.to_thread(
        compile_video,
        timeline,
        artifacts.audio,
        artifacts.subtitles,
        workspace,
        script.language,
        pick_music(script.tone),
    )
    artifacts.final_video_path = str(video_path)
    artifacts.status = "done"
    workspace.meta_path.write_text(
        json.dumps(artifacts.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    mark_rendered(
        job_id=job.job_id,
        video_path=video_path,
        srt_path=artifacts.subtitles.srt_path,
        description=script.description,
        tags=script.hashtags,
        hashtags=script.hashtags,
        sources=plan.get("sources", []),
        duration_seconds=artifacts.audio.total_duration,
        width=1080,
        height=1920,
        language=script.language,
    )
    await asyncio.to_thread(run_publisher_tick)


async def video_worker(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        job = await asyncio.to_thread(claim_next_pending_video)
        if job is None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=2)
            except TimeoutError:
                pass
            continue

        try:
            log_event("job.processing", job_id=job.job_id, detail=job.topic)
            await process_video_job(job)
            logger.info("video job %s completed", job.job_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("video job %s failed", job.job_id)
            set_video_error(job.job_id, str(exc))
            log_event("job.failed", job_id=job.job_id, detail=str(exc)[:2000])


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log_event("app.startup", detail=os.getenv("ENVIRONMENT", "dev"))
    recovered = requeue_processing_videos()
    if recovered:
        logger.warning("Requeued %d interrupted video job(s)", recovered)

    bot = TelegramTrendBot(on_topics_confirmed=enqueue_confirmed_topics)
    app.state.telegram_bot = bot
    await bot.app.initialize()
    await bot.app.start()
    if bot.app.updater is None:
        raise RuntimeError("Telegram updater is unavailable")
    #start polling for Telegram updates in the background
    await bot.app.updater.start_polling()

    worker_stop = asyncio.Event()
    worker_task = asyncio.create_task(video_worker(worker_stop), name="video-worker")
    scheduler.add_job(daily_trend_job, "cron", hour=0, minute=0, args=[bot])
    scheduler.add_job(
        run_publisher_tick,
        "interval",
        minutes=15,
        id="publisher-tick",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("DB ready; Telegram polling, video worker, and scheduler started.")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        worker_stop.set()
        await worker_task
        await bot.app.updater.stop()
        await bot.app.stop()
        await bot.app.shutdown()
        logger.info("Telegram polling, video worker, and scheduler stopped.")

app = FastAPI(title="Daily Trends", lifespan=lifespan)
app.include_router(youtube_auth_router)


@app.post("/generate-videos")
async def generate_videos(topics: list[str], background_tasks: BackgroundTasks):
    background_tasks.add_task(generate_videos_for_topics, topics)
    logger.info(f"Video generation started for topics: {topics}")
    return {"message": "Video generation started in the background."}

@app.get("/")
def read_root():
    return {"Liveness": "Check OK"}

@app.get("/fetch-trends")
async def fetch_trends():
    trends = get_trends()
    return {"trends": trends}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=7000)