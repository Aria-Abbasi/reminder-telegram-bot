from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
from typing import AsyncIterator
from aiogram import Bot, Dispatcher
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from backend.api import router as api_router
from backend.bot import register_handlers
from backend.config import settings
from backend.database import init_db
from backend.scheduler import ReminderScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("reminder_app")

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 1. Initialize SQLite Database
    await init_db()

    bot: Bot | None = None
    dp: Dispatcher | None = None
    scheduler: ReminderScheduler | None = None
    bot_task: asyncio.Task | None = None

    # 2. Start Bot and Scheduler if bot token is provided
    if settings.telegram_bot_token and not settings.telegram_bot_token.startswith("your_"):
        logger.info("Initializing Telegram Bot...")
        bot = Bot(token=settings.telegram_bot_token)
        dp = Dispatcher()
        register_handlers(dp)

        scheduler = ReminderScheduler(bot, poll_interval_seconds=5.0)
        await scheduler.start()

        # Start aiogram polling as background task
        bot_task = asyncio.create_task(dp.start_polling(bot))
        logger.info("Telegram Bot polling started")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not provided or using placeholder. Running in API-only/demo mode.")

    yield

    # Shutdown
    if scheduler:
        await scheduler.stop()
    if bot and dp:
        logger.info("Shutting down Telegram Bot...")
        await dp.stop_polling()
        await bot.session.close()
    if bot_task:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Telegram Reminder Bot & Mini App",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware for WebApp access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API endpoints
app.include_router(api_router)


# Frontend static files and SPA route
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


@app.get("/")
async def serve_index() -> FileResponse:
    index_file = frontend_dir / "index.html"
    if not index_file.exists():
        return FileResponse(Path(__file__).resolve().parent / "fallback.html")
    return FileResponse(index_file)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "app": "Telegram Reminder Bot & Mini App"}


def start() -> None:
    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    start()
