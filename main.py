from fastapi import FastAPI, BackgroundTasks
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import uvicorn
import logging
from src.gateway.telegram_bot import TelegramTrendBot, send_telegram_digest
from src.ingestion.main import get_trends

logger = logging.getLogger(__name__)


scheduler = AsyncIOScheduler()

async def daily_trend_job():
    trends = get_trends()
    bot = TelegramTrendBot()
    await bot.send_telegram_digest(trends)
    selected_trends = bot.selected_trends

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(daily_trend_job, "cron", hour=0, minute=0)
    scheduler.start()
    logger.info("Scheduler started and daily trend job scheduled.")
    yield
    scheduler.shutdown()
    logger.info("Scheduler shut down.")

app = FastAPI(title="Daily Trends", lifespan=lifespan)

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