from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Optional
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from backend.config import settings
from backend.database import advance_reminder, get_due_reminders, utc_now_iso

logger = logging.getLogger(__name__)


def build_reminder_keyboard(reminder: dict) -> InlineKeyboardMarkup:
    rem_id = reminder["id"]
    buttons = [
        [
            InlineKeyboardButton(text="✅ Done", callback_data=f"done:{rem_id}"),
            InlineKeyboardButton(text="💤 +15m", callback_data=f"snooze:{rem_id}:900"),
            InlineKeyboardButton(text="💤 +1h", callback_data=f"snooze:{rem_id}:3600"),
        ]
    ]

    # If it's recurring, add a stop recurrence button
    if reminder.get("interval_seconds"):
        buttons.append([
            InlineKeyboardButton(
                text="🛑 Stop Recurrence",
                callback_data=f"cancel:{rem_id}",
            )
        ])

    # If webapp_url is configured, add Open Mini App button
    if settings.webapp_url:
        buttons.append([
            InlineKeyboardButton(
                text="📱 Open Mini App Dashboard",
                web_app=WebAppInfo(url=settings.webapp_url),
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def process_due_reminders(bot: Bot) -> None:
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now_dt.isoformat()

    due_list = await get_due_reminders(now_iso)
    if not due_list:
        return

    logger.info("Found %d due reminders to dispatch", len(due_list))

    for reminder in due_list:
        rem_id = reminder["id"]
        chat_id = reminder["chat_id"]
        title = reminder["title"]
        interval_seconds = reminder.get("interval_seconds")
        interval_label = reminder.get("interval_label")
        end_time_str = reminder.get("end_time")

        # Format message
        recurrence_text = f"\n🔄 *Recurrence:* {interval_label or f'Every {interval_seconds}s'}" if interval_seconds else ""
        msg_text = (
            f"🔔 *REMINDER ALERT*\n\n"
            f"📌 *Task:* {title}{recurrence_text}\n"
            f"⏱ *Scheduled for:* {now_dt.strftime('%H:%M UTC')}"
        )

        keyboard = build_reminder_keyboard(reminder)

        # Dispatch via Telegram Bot
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=msg_text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error("Failed to send reminder %d to chat %s: %s", rem_id, chat_id, e)

        # Calculate next run time or complete
        if interval_seconds and interval_seconds > 0:
            # Advance to next interval
            # Base next run on scheduled start or previous next_run_at to prevent drift
            current_scheduled_dt = datetime.datetime.fromisoformat(reminder["next_run_at"])
            if current_scheduled_dt.tzinfo is None:
                current_scheduled_dt = current_scheduled_dt.replace(tzinfo=datetime.timezone.utc)

            next_dt = current_scheduled_dt + datetime.timedelta(seconds=interval_seconds)
            
            # If next_dt is still in past, advance forward to next upcoming occurrence
            while next_dt <= now_dt:
                next_dt += datetime.timedelta(seconds=interval_seconds)

            # Check optional end_time
            if end_time_str:
                end_dt = datetime.datetime.fromisoformat(end_time_str)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=datetime.timezone.utc)
                if next_dt > end_dt:
                    logger.info("Reminder %d reached end_time %s. Marking completed.", rem_id, end_time_str)
                    await advance_reminder(rem_id, None, mark_completed=True)
                    continue

            await advance_reminder(rem_id, next_dt.isoformat(), mark_completed=False)
        else:
            # One-off reminder is complete
            await advance_reminder(rem_id, None, mark_completed=True)


class ReminderScheduler:
    def __init__(self, bot: Bot, poll_interval_seconds: float = 5.0):
        self.bot = bot
        self.poll_interval = poll_interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Reminder scheduler started with interval %.1fs", self.poll_interval)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Reminder scheduler stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await process_due_reminders(self.bot)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in scheduler loop: %s", e, exc_info=True)
            await asyncio.sleep(self.poll_interval)
