from __future__ import annotations

import asyncio
import datetime
import json
import logging
from typing import Any
import pytz
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from backend.config import settings
from backend.database import (
    cancel_reminder,
    complete_reminder,
    create_reminder,
    delete_reminder,
    get_or_create_user,
    get_reminder,
    get_user_meal_times,
    get_user_reminders,
    get_user_timezone,
    reset_user_meal_times,
    set_user_meal_times,
    set_user_timezone,
    snooze_reminder,
)
from backend.omniroute_ai import parse_reminder_with_omniroute
import re

logger = logging.getLogger(__name__)

# Temporary in-memory cache for pending AI reminder confirmations: { "user_id": { ...parsed_data } }
_pending_confirmations: dict[int, dict[str, Any]] = {}

MEAL_ALIASES = {
    "breakfast": "breakfast",
    "morning": "breakfast",
    "صبح": "breakfast",
    "lunch": "lunch",
    "noon": "lunch",
    "ظهر": "lunch",
    "ناهار": "lunch",
    "afternoon": "afternoon",
    "عصر": "afternoon",
    "dinner": "dinner",
    "night": "dinner",
    "شب": "dinner",
    "شام": "dinner",
}

MEAL_NAMES_FA = {
    "breakfast": "صبح (Breakfast)",
    "lunch": "ظهر (Lunch)",
    "afternoon": "عصر (Afternoon)",
    "dinner": "شب و شام (Dinner)",
}


def format_dt(dt_iso: str, tz_name: str) -> str:
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.UTC
    dt = datetime.datetime.fromisoformat(dt_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    local_dt = dt.astimezone(tz)
    return local_dt.strftime("%Y-%m-%d %H:%M %Z")


def get_start_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    if settings.webapp_url:
        buttons.append([
            InlineKeyboardButton(
                text="📱 Open Mini App Dashboard",
                web_app=WebAppInfo(url=settings.webapp_url),
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="📋 My Reminders", callback_data="btn_list"),
        InlineKeyboardButton(text="🍽 Meal Times", callback_data="btn_mealtimes_menu"),
    ])
    buttons.append([
        InlineKeyboardButton(text="🌍 Timezone", callback_data="btn_tz_menu"),
        InlineKeyboardButton(text="💡 Help & Guide", callback_data="btn_help"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_mealtimes_keyboard(meals: dict[str, str]) -> InlineKeyboardMarkup:
    b = meals.get("breakfast", "08:30")
    l = meals.get("lunch", "12:30")
    a = meals.get("afternoon", "17:00")
    d = meals.get("dinner", "20:30")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🌅 Morning (صبح): {b}", callback_data="meal_pick:breakfast"),
                InlineKeyboardButton(text=f"☀️ Lunch (ظهر): {l}", callback_data="meal_pick:lunch"),
            ],
            [
                InlineKeyboardButton(text=f"🌇 Afternoon (عصر): {a}", callback_data="meal_pick:afternoon"),
                InlineKeyboardButton(text=f"🌙 Dinner (شب): {d}", callback_data="meal_pick:dinner"),
            ],
            [
                InlineKeyboardButton(text="🔄 Reset Defaults", callback_data="meal_reset"),
                InlineKeyboardButton(text="🔙 Main Menu", callback_data="btn_main_menu"),
            ],
        ]
    )


def get_quick_time_keyboard(meal_key: str) -> InlineKeyboardMarkup:
    presets = {
        "breakfast": ["07:00", "07:30", "08:00", "08:30", "09:00", "09:30"],
        "lunch": ["12:00", "12:30", "13:00", "13:30", "14:00", "14:30"],
        "afternoon": ["16:00", "16:30", "17:00", "17:30", "18:00", "18:30"],
        "dinner": ["19:30", "20:00", "20:30", "21:00", "21:30", "22:00"],
    }
    times = presets.get(meal_key, ["08:00", "12:00", "18:00", "20:00"])
    rows = []
    for i in range(0, len(times), 3):
        row = [
            InlineKeyboardButton(text=f"⏰ {t}", callback_data=f"meal_set:{meal_key}:{t}")
            for t in times[i : i + 3]
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔙 Back to Meals", callback_data="btn_mealtimes_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_timezone_keyboard() -> InlineKeyboardMarkup:
    tz_list = [
        ("UTC", "UTC"),
        ("New York (EST/EDT)", "America/New_York"),
        ("London (GMT/BST)", "Europe/London"),
        ("Berlin / Paris (CET)", "Europe/Berlin"),
        ("Tehran (IRST)", "Asia/Tehran"),
        ("Dubai (GST)", "Asia/Dubai"),
        ("Tokyo (JST)", "Asia/Tokyo"),
    ]
    keyboard = []
    for label, tz in tz_list:
        keyboard.append([InlineKeyboardButton(text=f"📍 {label}", callback_data=f"set_tz:{tz}")])
    keyboard.append([InlineKeyboardButton(text="🔙 Back", callback_data="btn_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def register_handlers(dp: Dispatcher) -> None:
    @dp.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        user = message.from_user
        if not user:
            return
        await get_or_create_user(user.id, user.username, user.first_name)
        tz = await get_user_timezone(user.id)

        welcome_text = (
            f"👋 *Welcome, {user.first_name or 'there'}!*\n\n"
            f"I am your *AI-powered Reminder Assistant*.\n"
            f"🕒 Current timezone: `{tz}`\n\n"
            f"✨ *How to use:*\n"
            f"• Tap *Open Mini App* for the full visual dashboard.\n"
            f"• Or simply *send any message* like:\n"
            f'  _“Remind me to take vitamins every 8 hours starting tomorrow at 9am until Friday”_\n'
            f'  _“Call Alex at 5pm”_\n'
            f'  _“Water plants every 7 days”_\n\n'
            f"What would you like to do?"
        )
        await message.answer(
            welcome_text,
            parse_mode="Markdown",
            reply_markup=get_start_keyboard(),
        )

    @dp.message(Command("timezone"))
    async def cmd_timezone(message: Message) -> None:
        user = message.from_user
        if not user:
            return
        args = message.text.split(maxsplit=1) if message.text else []
        if len(args) > 1:
            tz_name = args[1].strip()
            try:
                pytz.timezone(tz_name)
                await set_user_timezone(user.id, tz_name)
                await message.answer(f"✅ Timezone updated to *{tz_name}*", parse_mode="Markdown")
                return
            except Exception:
                await message.answer("⚠️ Invalid timezone. Pick from the list below:", reply_markup=get_timezone_keyboard())
                return

        await message.answer("🌍 *Select your timezone:*", parse_mode="Markdown", reply_markup=get_timezone_keyboard())

    @dp.message(Command("mealtimes"))
    @dp.message(Command("meals"))
    async def cmd_mealtimes(message: Message) -> None:
        user = message.from_user
        if not user:
            return
        meals = await get_user_meal_times(user.id)
        text = (
            "🍽 *Your Routine & Meal Times:*\n\n"
            f"🌅 *Morning / Breakfast (صبح):* `{meals['breakfast']}`\n"
            f"☀️ *Noon / Lunch (ظهر):* `{meals['lunch']}`\n"
            f"🌇 *Afternoon (عصر):* `{meals['afternoon']}`\n"
            f"🌙 *Evening / Dinner (شب / شام):* `{meals['dinner']}`\n\n"
            "💡 _When you mention 'صبح', 'ظهر', or 'شب' in your reminders, OmniRoute AI automatically uses these times._\n\n"
            "Tap a meal button below to change it, or type:\n"
            "`/setmeal <meal> <HH:MM>` (e.g. `/setmeal breakfast 08:00` or `/setmeal شام 21:00`)"
        )
        await message.answer(text, parse_mode="Markdown", reply_markup=get_mealtimes_keyboard(meals))

    @dp.message(Command("setmeal"))
    async def cmd_setmeal(message: Message) -> None:
        user = message.from_user
        if not user or not message.text:
            return
        parts = message.text.strip().split()
        if len(parts) < 3:
            await message.answer(
                "ℹ️ *Usage:* `/setmeal <meal> <HH:MM>`\n\n"
                "Examples:\n"
                "• `/setmeal breakfast 08:00`\n"
                "• `/setmeal lunch 13:00`\n"
                "• `/setmeal شام 21:00`\n"
                "• `/setmeal صبح 07:30`",
                parse_mode="Markdown",
            )
            return

        meal_arg = parts[1].lower()
        time_arg = parts[2].strip()

        meal_key = MEAL_ALIASES.get(meal_arg)
        if not meal_key:
            await message.answer(
                "⚠️ Unknown meal name. Supported: `breakfast (صبح)`, `lunch (ظهر)`, `afternoon (عصر)`, `dinner (شب/شام)`.",
                parse_mode="Markdown",
            )
            return

        if not re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", time_arg):
            await message.answer(
                "⚠️ Invalid time format. Please use 24-hour format `HH:MM` (e.g. `08:30`, `13:15`, `21:00`).",
                parse_mode="Markdown",
            )
            return

        kwargs = {meal_key: time_arg}
        updated = await set_user_meal_times(user.id, **kwargs)
        meal_display = MEAL_NAMES_FA.get(meal_key, meal_key)
        await message.answer(
            f"✅ *{meal_display}* time updated to `{time_arg}`!\n\nAll subsequent reminders mentioning this time will trigger at `{time_arg}`.",
            parse_mode="Markdown",
            reply_markup=get_mealtimes_keyboard(updated),
        )

    @dp.message(Command("list"))
    async def cmd_list(message: Message) -> None:
        user = message.from_user
        if not user:
            return
        reminders = await get_user_reminders(user.id, status="active")
        tz = await get_user_timezone(user.id)

        if not reminders:
            await message.answer(
                "📭 You have no active reminders.\nType a task or open the Mini App to create one!",
                reply_markup=get_start_keyboard(),
            )
            return

        text = "📋 *Your Active Reminders:*\n\n"
        buttons = []
        for r in reminders[:8]:
            start_str = format_dt(r["next_run_at"], tz)
            interval_str = f" ({r['interval_label']})" if r.get("interval_label") else ""
            text += f"• *{r['title']}*\n  ⏱ Next: `{start_str}`{interval_str}\n\n"
            buttons.append([
                InlineKeyboardButton(text=f"✅ Done: {r['title'][:15]}", callback_data=f"done:{r['id']}"),
                InlineKeyboardButton(text="🗑 Delete", callback_data=f"del:{r['id']}"),
            ])

        if settings.webapp_url:
            buttons.append([
                InlineKeyboardButton(text="📱 Manage All in Mini App", web_app=WebAppInfo(url=settings.webapp_url))
            ])

        await message.answer(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    @dp.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        help_text = (
            "💡 *Reminder Bot Guide*\n\n"
            "• *Start Time*: The exact date and time the reminder triggers first.\n"
            "• *Interval*: Optional recurrence (e.g. 6 hours, 8 hours, 1 day, 7 days, or custom).\n"
            "• *End Time*: Optional cut-off time when recurring reminders stop.\n\n"
            "🤖 *OmniRoute AI Integration:*\n"
            "Just type what you want in plain text! Examples:\n"
            '• _"Check oven in 25 minutes"_\n'
            '• _"Take medicine every 8 hours starting 8am until next Monday"_\n'
            '• _"Weekly team meeting every Monday at 10am"_\n\n'
            "📱 *Telegram Mini App:*\n"
            "Tap *Open Mini App Dashboard* anytime to view calendar, snooze, edit, or configure custom intervals."
        )
        await message.answer(help_text, parse_mode="Markdown", reply_markup=get_start_keyboard())

    @dp.callback_query(F.data.startswith("set_tz:"))
    async def cb_set_tz(callback: CallbackQuery) -> None:
        if not callback.data:
            return
        tz_name = callback.data.split(":", 1)[1]
        user_id = callback.from_user.id
        await set_user_timezone(user_id, tz_name)
        await callback.answer("Timezone saved!")
        await callback.message.edit_text(
            f"✅ Timezone set to *{tz_name}*.\nAll reminder alerts will now be aligned to your local time.",
            parse_mode="Markdown",
            reply_markup=get_start_keyboard(),
        )

    @dp.callback_query(F.data == "btn_tz_menu")
    async def cb_tz_menu(callback: CallbackQuery) -> None:
        await callback.answer()
        try:
            await callback.message.edit_text(
                "🌍 *Choose your timezone:*",
                parse_mode="Markdown",
                reply_markup=get_timezone_keyboard(),
            )
        except TelegramBadRequest:
            pass

    @dp.callback_query(F.data == "btn_mealtimes_menu")
    async def cb_mealtimes_menu(callback: CallbackQuery) -> None:
        await callback.answer()
        meals = await get_user_meal_times(callback.from_user.id)
        text = (
            "🍽 *Your Routine & Meal Times:*\n\n"
            f"🌅 *Morning / Breakfast (صبح):* `{meals['breakfast']}`\n"
            f"☀️ *Noon / Lunch (ظهر):* `{meals['lunch']}`\n"
            f"🌇 *Afternoon (عصر):* `{meals['afternoon']}`\n"
            f"🌙 *Evening / Dinner (شب / شام):* `{meals['dinner']}`\n\n"
            "💡 _When you mention 'صبح', 'ظهر', or 'شب' in your reminders, OmniRoute AI automatically uses these times._\n\n"
            "Tap a meal button below to change it, or type:\n"
            "`/setmeal <meal> <HH:MM>`"
        )
        try:
            await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_mealtimes_keyboard(meals))
        except TelegramBadRequest:
            pass

    @dp.callback_query(F.data.startswith("meal_pick:"))
    async def cb_meal_pick(callback: CallbackQuery) -> None:
        await callback.answer()
        meal_key = callback.data.split(":")[1]
        meals = await get_user_meal_times(callback.from_user.id)
        current_val = meals.get(meal_key, "08:30")
        meal_display = MEAL_NAMES_FA.get(meal_key, meal_key)
        text = (
            f"⚙️ *Configure {meal_display}*\n\n"
            f"Current time: `{current_val}`\n\n"
            "Choose a quick preset below, or reply with:\n"
            f"`/setmeal {meal_key} <HH:MM>`"
        )
        try:
            await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_quick_time_keyboard(meal_key))
        except TelegramBadRequest:
            pass

    @dp.callback_query(F.data.startswith("meal_set:"))
    async def cb_meal_set(callback: CallbackQuery) -> None:
        parts = callback.data.split(":")
        meal_key = parts[1]
        new_time = parts[2]
        kwargs = {meal_key: new_time}
        updated = await set_user_meal_times(callback.from_user.id, **kwargs)
        meal_display = MEAL_NAMES_FA.get(meal_key, meal_key)
        await callback.answer(f"Saved: {new_time}")
        text = (
            f"✅ *{meal_display}* time updated to `{new_time}`!\n\n"
            "🍽 *Current Routine Times:*\n"
            f"🌅 *Morning (صبح):* `{updated['breakfast']}`\n"
            f"☀️ *Noon (ظهر):* `{updated['lunch']}`\n"
            f"🌇 *Afternoon (عصر):* `{updated['afternoon']}`\n"
            f"🌙 *Evening (شب):* `{updated['dinner']}`\n"
        )
        try:
            await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_mealtimes_keyboard(updated))
        except TelegramBadRequest:
            pass

    @dp.callback_query(F.data == "meal_reset")
    async def cb_meal_reset(callback: CallbackQuery) -> None:
        defaults = await reset_user_meal_times(callback.from_user.id)
        await callback.answer("Reset to default times!")
        text = (
            "🔄 *Meal times reset to defaults:*\n\n"
            f"🌅 *Morning (صبح):* `{defaults['breakfast']}`\n"
            f"☀️ *Noon (ظهر):* `{defaults['lunch']}`\n"
            f"🌇 *Afternoon (عصر):* `{defaults['afternoon']}`\n"
            f"🌙 *Evening (شب):* `{defaults['dinner']}`\n"
        )
        try:
            await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_mealtimes_keyboard(defaults))
        except TelegramBadRequest:
            pass

    @dp.callback_query(F.data == "btn_main_menu")
    async def cb_main_menu(callback: CallbackQuery) -> None:
        await callback.answer()
        try:
            await callback.message.edit_text(
                "👋 What would you like to do?",
                parse_mode="Markdown",
                reply_markup=get_start_keyboard(),
            )
        except TelegramBadRequest:
            pass

    @dp.callback_query(F.data == "btn_list")
    async def cb_btn_list(callback: CallbackQuery) -> None:
        await callback.answer()
        user_id = callback.from_user.id
        reminders = await get_user_reminders(user_id, status="active")
        tz = await get_user_timezone(user_id)

        if not reminders:
            try:
                await callback.message.edit_text(
                    "📭 You have no active reminders.\nType a task or open the Mini App to create one!",
                    reply_markup=get_start_keyboard(),
                )
            except TelegramBadRequest:
                pass
            return

        text = "📋 *Your Active Reminders:*\n\n"
        buttons = []
        for r in reminders[:8]:
            start_str = format_dt(r["next_run_at"], tz)
            interval_str = f" ({r['interval_label']})" if r.get("interval_label") else ""
            text += f"• *{r['title']}*\n  ⏱ Next: `{start_str}`{interval_str}\n\n"
            buttons.append([
                InlineKeyboardButton(text=f"✅ Done: {r['title'][:15]}", callback_data=f"done:{r['id']}"),
                InlineKeyboardButton(text="🗑 Delete", callback_data=f"del:{r['id']}"),
            ])

        if settings.webapp_url:
            buttons.append([
                InlineKeyboardButton(text="📱 Manage All in Mini App", web_app=WebAppInfo(url=settings.webapp_url))
            ])
        buttons.append([InlineKeyboardButton(text="🔙 Back", callback_data="btn_main_menu")])

        try:
            await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        except TelegramBadRequest:
            pass

    @dp.callback_query(F.data == "btn_help")
    async def cb_btn_help(callback: CallbackQuery) -> None:
        await callback.answer()
        help_text = (
            "💡 *Reminder Bot Guide*\n\n"
            "• *Start Time*: The exact date and time the reminder triggers first.\n"
            "• *Interval*: Optional recurrence (e.g. 6 hours, 8 hours, 1 day, 7 days, or custom).\n"
            "• *End Time*: Optional cut-off time when recurring reminders stop.\n\n"
            "🤖 *OmniRoute AI Integration:*\n"
            "Just type what you want in plain text! Or tap *Open Mini App* for the visual dashboard."
        )
        try:
            await callback.message.edit_text(
                help_text,
                parse_mode="Markdown",
                reply_markup=get_start_keyboard(),
            )
        except TelegramBadRequest:
            pass

    @dp.callback_query(F.data.startswith("done:"))
    async def cb_done(callback: CallbackQuery) -> None:
        rem_id = int(callback.data.split(":")[1])
        await complete_reminder(rem_id)
        await callback.answer("✅ Marked as completed!")
        try:
            await callback.message.edit_text(
                f"{callback.message.text}\n\n✅ *Status:* Completed!",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    @dp.callback_query(F.data.startswith("snooze:"))
    async def cb_snooze(callback: CallbackQuery) -> None:
        parts = callback.data.split(":")
        rem_id = int(parts[1])
        snooze_sec = int(parts[2])
        mins = snooze_sec // 60
        await snooze_reminder(rem_id, snooze_sec)
        await callback.answer(f"💤 Snoozed for {mins} minutes!")
        try:
            await callback.message.edit_text(
                f"{callback.message.text}\n\n💤 *Snoozed for {mins} minutes.*",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    @dp.callback_query(F.data.startswith("cancel:"))
    async def cb_cancel_recurrence(callback: CallbackQuery) -> None:
        rem_id = int(callback.data.split(":")[1])
        await cancel_reminder(rem_id)
        await callback.answer("🛑 Recurrence stopped!")
        try:
            await callback.message.edit_text(
                f"{callback.message.text}\n\n🛑 *Recurrence cancelled.*",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    @dp.callback_query(F.data.startswith("del:"))
    async def cb_delete(callback: CallbackQuery) -> None:
        rem_id = int(callback.data.split(":")[1])
        user_id = callback.from_user.id
        await delete_reminder(rem_id, user_id=user_id)
        await callback.answer("🗑 Reminder deleted!")
        # Refresh active list
        await cb_btn_list(callback)

    @dp.callback_query(F.data == "confirm_ai_reminder")
    async def cb_confirm_ai(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        items = _pending_confirmations.pop(user_id, [])
        if not items:
            await callback.answer("Expired confirmation. Please try again.")
            return

        chat_id = callback.message.chat.id
        tz = await get_user_timezone(user_id)
        created_list = []
        for data in items:
            rem = await create_reminder(
                user_id=user_id,
                chat_id=chat_id,
                title=data["title"],
                start_time=data["start_time"],
                interval_seconds=data.get("interval_seconds"),
                interval_label=data.get("interval_label"),
                end_time=data.get("end_time"),
            )
            created_list.append(rem)

        await callback.answer("Scheduled!")
        if len(created_list) == 1:
            r = created_list[0]
            start_str = format_dt(r["next_run_at"], tz)
            confirm_text = (
                f"🎉 *Reminder Scheduled Successfully!*\n\n"
                f"📌 *Task:* {r['title']}\n"
                f"⏰ *First Run:* `{start_str}`\n"
                f"🔄 *Interval:* {r['interval_label'] or 'None (One-time)'}\n"
            )
            if r.get("end_time"):
                confirm_text += f"🏁 *End Time:* `{format_dt(r['end_time'], tz)}`\n"
        else:
            confirm_text = f"🎉 *{len(created_list)} Reminders Scheduled Successfully!*\n\n"
            for i, r in enumerate(created_list, 1):
                start_str = format_dt(r["next_run_at"], tz)
                interval_str = f" ({r['interval_label']})" if r.get("interval_label") else ""
                confirm_text += f"{i}️⃣ *{r['title']}* — `{start_str}`{interval_str}\n"

        buttons = []
        if settings.webapp_url:
            buttons.append([InlineKeyboardButton(text="📱 View in Mini App", web_app=WebAppInfo(url=settings.webapp_url))])

        try:
            await callback.message.edit_text(
                confirm_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None,
            )
        except Exception:
            try:
                await callback.message.edit_text(
                    confirm_text.replace("*", "").replace("`", ""),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None,
                )
            except TelegramBadRequest:
                pass

    @dp.callback_query(F.data == "cancel_ai_reminder")
    async def cb_cancel_ai(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        _pending_confirmations.pop(user_id, None)
        await callback.answer("Cancelled")
        try:
            await callback.message.edit_text("❌ Reminder creation cancelled.")
        except TelegramBadRequest:
            pass

    # Natural Language Handler via OmniRoute Combo (Supports Single & Multiple Reminders)
    @dp.message(F.text)
    async def handle_natural_language(message: Message) -> None:
        user = message.from_user
        if not user or not message.text:
            return

        # Keep sending typing action while AI processes
        typing_active = True

        async def _keep_typing() -> None:
            while typing_active:
                try:
                    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
                except Exception:
                    pass
                await asyncio.sleep(4.5)

        typing_task = asyncio.create_task(_keep_typing())
        try:
            tz = await get_user_timezone(user.id)
            user_meals = await get_user_meal_times(user.id)
            parsed_list = await parse_reminder_with_omniroute(message.text, user_timezone=tz, meal_times=user_meals)
        except Exception as err:
            logger.exception("Error parsing reminder via OmniRoute: %s", err)
            await message.answer("⚠️ An error occurred while parsing your message. Please try again.")
            return
        finally:
            typing_active = False
            typing_task.cancel()

        if not parsed_list:
            await message.answer("⚠️ Could not parse any reminders from your message. Please try rephrasing.")
            return

        # Store in pending confirmations
        _pending_confirmations[user.id] = [p.to_dict() for p in parsed_list]

        if len(parsed_list) == 1:
            p = parsed_list[0]
            start_str = format_dt(p.start_time_iso, tz)
            interval_str = p.interval_label or "None (One-time)"
            end_str = format_dt(p.end_time_iso, tz) if p.end_time_iso else "None"

            preview_text = (
                f"🤖 *OmniRoute AI Parsed Reminder:*\n\n"
                f"📌 *Task:* {p.title}\n"
                f"⏰ *Start Time:* `{start_str}`\n"
                f"🔄 *Interval:* `{interval_str}`\n"
            )
            if p.end_time_iso:
                preview_text += f"🏁 *End Time:* `{end_str}`\n"
            preview_text += "\nConfirm scheduling this reminder?"
            btn_label = "✅ Confirm & Schedule"
        else:
            preview_text = f"🤖 *OmniRoute AI Parsed {len(parsed_list)} Reminders:*\n\n"
            for i, p in enumerate(parsed_list, 1):
                start_str = format_dt(p.start_time_iso, tz)
                interval_str = f" ({p.interval_label})" if p.interval_label else ""
                end_str = f" (Until {format_dt(p.end_time_iso, tz)})" if p.end_time_iso else ""
                preview_text += f"{i}️⃣ *{p.title}*\n   ⏰ `{start_str}`{interval_str}{end_str}\n\n"
            preview_text += f"Confirm scheduling all {len(parsed_list)} reminders?"
            btn_label = f"✅ Confirm All ({len(parsed_list)})"

        buttons = [
            [
                InlineKeyboardButton(text=btn_label, callback_data="confirm_ai_reminder"),
                InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_ai_reminder"),
            ]
        ]
        if settings.webapp_url:
            buttons.append([
                InlineKeyboardButton(text="✏️ Open Mini App", web_app=WebAppInfo(url=settings.webapp_url))
            ])

        try:
            await message.answer(
                preview_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            )
        except Exception:
            await message.answer(
                preview_text.replace("*", "").replace("`", ""),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            )

