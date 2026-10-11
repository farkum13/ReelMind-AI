import logging
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, FastAPI

from src.dal.db import init_db
from src.dal.models import VideoStatus
from src.dal.repository import create_video, log_event
from src.gateway.telegram_bot import TelegramTrendBot
from src.ingestion.main import get_trends
from src.upload.routes import router as youtube_auth_router

logger = logging.getLogger(__name__)


scheduler = AsyncIOScheduler()

async def daily_trend_job():
    trends = get_trends()
    bot = TelegramTrendBot()
    chat_id = int(os.getenv("CHAT_ID", "0"))
    if chat_id:
        await bot.send_telegram_digest(trends, chat_id=chat_id)
    else:
        logger.warning("CHAT_ID not set; skipping trend digest push.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log_event("app.startup", detail=os.getenv("ENVIRONMENT", "dev"))
    scheduler.add_job(daily_trend_job, "cron", hour=0, minute=0)
    scheduler.start()
    logger.info("DB ready; scheduler started and daily trend job scheduled.")
    yield
    scheduler.shutdown()
    logger.info("Scheduler shut down.")

app = FastAPI(title="Daily Trends", lifespan=lifespan)
app.include_router(youtube_auth_router)


def generate_videos_for_topics(topics: list[str]) -> list[str]:
    """Queue topics as PENDING video jobs. Creation of the actual videos is
    handled by the media pipeline (Module 4) picked up by the queue runner
    (Module 5)."""
    queued: list[str] = []
    for topic in topics:
        job_id = str(uuid4())
        try:
            create_video(job_id=job_id, topic=topic, status=VideoStatus.PENDING)
            log_event("job.queued", job_id=job_id, detail=topic)
            queued.append(job_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("failed to queue topic %r: %s", topic, exc)
    logger.info("queued %d/%d topic(s)", len(queued), len(topics))
    return queued


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