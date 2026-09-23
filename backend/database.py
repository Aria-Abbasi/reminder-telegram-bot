from __future__ import annotations

import datetime
import logging
from typing import Any, Optional
import aiosqlite
from backend.config import settings

logger = logging.getLogger(__name__)


from contextlib import asynccontextmanager

def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@asynccontextmanager
async def get_db_connection() -> Any:
    async with aiosqlite.connect(str(settings.db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute("PRAGMA foreign_keys = ON")
        yield conn


async def init_db() -> None:
    async with get_db_connection() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                timezone TEXT DEFAULT 'Asia/Tehran',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                start_time TEXT NOT NULL,
                interval_seconds INTEGER,
                interval_label TEXT,
                end_time TEXT,
                next_run_at TEXT NOT NULL,
                last_run_at TEXT,
                run_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_reminders_due 
            ON reminders(status, next_run_at)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_reminders_user 
            ON reminders(user_id, status)
        """)

        # Admins table for persistent admin storage
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_at TEXT NOT NULL
            )
        """)

        # Migration: Ensure meal time columns exist on users table
        for col, default_val in [
            ("breakfast_time", "'08:30'"),
            ("lunch_time", "'12:30'"),
            ("afternoon_time", "'17:00'"),
            ("dinner_time", "'20:30'"),
        ]:
            try:
                await conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT {default_val}")
            except Exception:
                pass

        # Migration: Update existing users with UTC or empty timezone to Asia/Tehran
        try:
            await conn.execute("UPDATE users SET timezone = 'Asia/Tehran' WHERE timezone = 'UTC' OR timezone IS NULL OR timezone = ''")
        except Exception:
            pass

        # Seed admins from environment if specified
        now = utc_now_iso()
        for admin_id in settings.env_admin_ids:
            try:
                await conn.execute("INSERT OR IGNORE INTO admins (user_id, added_at) VALUES (?, ?)", (admin_id, now))
            except Exception:
                pass

        await conn.commit()
    logger.info("Database initialized successfully at %s", settings.db_path)


async def get_or_create_user(
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
) -> dict[str, Any]:
    now = utc_now_iso()
    async with get_db_connection() as conn:
        async with conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                # Update username/first_name if changed
                await conn.execute(
                    "UPDATE users SET username = ?, first_name = ?, updated_at = ? WHERE user_id = ?",
                    (username or row["username"], first_name or row["first_name"], now, user_id),
                )
                await conn.commit()
                return dict(row)

        await conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, timezone, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, username, first_name, settings.default_timezone, now, now),
        )
        await conn.commit()
        return {
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "timezone": settings.default_timezone,
            "created_at": now,
            "updated_at": now,
        }


async def set_user_timezone(user_id: int, timezone: str) -> None:
    now = utc_now_iso()
    async with get_db_connection() as conn:
        await conn.execute(
            "UPDATE users SET timezone = ?, updated_at = ? WHERE user_id = ?",
            (timezone, now, user_id),
        )
        await conn.commit()


async def get_user_timezone(user_id: int) -> str:
    async with get_db_connection() as conn:
        async with conn.execute("SELECT timezone FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row and row["timezone"]:
                return str(row["timezone"])
    return settings.default_timezone


DEFAULT_MEAL_TIMES: dict[str, str] = {
    "breakfast": "08:30",
    "lunch": "12:30",
    "afternoon": "17:00",
    "dinner": "20:30",
}


async def get_user_meal_times(user_id: int) -> dict[str, str]:
    async with get_db_connection() as conn:
        async with conn.execute(
            "SELECT breakfast_time, lunch_time, afternoon_time, dinner_time FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "breakfast": row["breakfast_time"] or DEFAULT_MEAL_TIMES["breakfast"],
                    "lunch": row["lunch_time"] or DEFAULT_MEAL_TIMES["lunch"],
                    "afternoon": row["afternoon_time"] or DEFAULT_MEAL_TIMES["afternoon"],
                    "dinner": row["dinner_time"] or DEFAULT_MEAL_TIMES["dinner"],
                }
    return dict(DEFAULT_MEAL_TIMES)


async def set_user_meal_times(
    user_id: int,
    breakfast: Optional[str] = None,
    lunch: Optional[str] = None,
    afternoon: Optional[str] = None,
    dinner: Optional[str] = None,
) -> dict[str, str]:
    current = await get_user_meal_times(user_id)
    new_breakfast = breakfast or current["breakfast"]
    new_lunch = lunch or current["lunch"]
    new_afternoon = afternoon or current["afternoon"]
    new_dinner = dinner or current["dinner"]
    now = utc_now_iso()

    async with get_db_connection() as conn:
        await conn.execute(
            """
            UPDATE users 
            SET breakfast_time = ?, lunch_time = ?, afternoon_time = ?, dinner_time = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (new_breakfast, new_lunch, new_afternoon, new_dinner, now, user_id),
        )
        await conn.commit()

    return {
        "breakfast": new_breakfast,
        "lunch": new_lunch,
        "afternoon": new_afternoon,
        "dinner": new_dinner,
    }


async def reset_user_meal_times(user_id: int) -> dict[str, str]:
    now = utc_now_iso()
    async with get_db_connection() as conn:
        await conn.execute(
            """
            UPDATE users 
            SET breakfast_time = ?, lunch_time = ?, afternoon_time = ?, dinner_time = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (
                DEFAULT_MEAL_TIMES["breakfast"],
                DEFAULT_MEAL_TIMES["lunch"],
                DEFAULT_MEAL_TIMES["afternoon"],
                DEFAULT_MEAL_TIMES["dinner"],
                now,
                user_id,
            ),
        )
        await conn.commit()
    return dict(DEFAULT_MEAL_TIMES)


async def is_admin_user(user_id: int) -> bool:
    from backend.config import is_token_configured
    # If running tests or standalone without configured bot token, allow dev mock user
    if not is_token_configured() and user_id in (12345678, 999999):
        return True

    # Fast check in environment admin list
    if user_id in settings.env_admin_ids:
        return True

    # Check SQLite admins table
    async with get_db_connection() as conn:
        async with conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return bool(row)


async def add_admin_user(user_id: int) -> None:
    now = utc_now_iso()
    async with get_db_connection() as conn:
        await conn.execute("INSERT OR IGNORE INTO admins (user_id, added_at) VALUES (?, ?)", (user_id, now))
        await conn.commit()


async def remove_admin_user(user_id: int) -> None:
    async with get_db_connection() as conn:
        await conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await conn.commit()


async def list_admin_users() -> list[int]:
    async with get_db_connection() as conn:
        async with conn.execute("SELECT user_id FROM admins") as cur:
            rows = await cur.fetchall()
            db_ids = {r["user_id"] for r in rows}
    return sorted(list(db_ids | settings.env_admin_ids))


async def create_reminder(
    *,
    user_id: int,
    chat_id: int,
    title: str,
    start_time: str,
    interval_seconds: Optional[int] = None,
    interval_label: Optional[str] = None,
    end_time: Optional[str] = None,
) -> dict[str, Any]:
    now = utc_now_iso()
    # Ensure user exists in users table
    await get_or_create_user(user_id)
    next_run_at = start_time

    async with get_db_connection() as conn:
        cursor = await conn.execute(
            """
            INSERT INTO reminders (
                user_id, chat_id, title, start_time, interval_seconds, 
                interval_label, end_time, next_run_at, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                user_id,
                chat_id,
                title.strip(),
                start_time,
                interval_seconds,
                interval_label,
                end_time,
                next_run_at,
                now,
                now,
            ),
        )
        reminder_id = cursor.lastrowid
        await conn.commit()

        async with conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else {}


async def get_reminder(reminder_id: int) -> Optional[dict[str, Any]]:
    async with get_db_connection() as conn:
        async with conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_user_reminders(user_id: int, status: Optional[str] = None) -> list[dict[str, Any]]:
    async with get_db_connection() as conn:
        if status:
            query = "SELECT * FROM reminders WHERE user_id = ? AND status = ? ORDER BY next_run_at ASC"
            params = (user_id, status)
        else:
            query = "SELECT * FROM reminders WHERE user_id = ? ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, next_run_at ASC"
            params = (user_id,)

        async with conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def snooze_reminder(reminder_id: int, snooze_seconds: int) -> Optional[dict[str, Any]]:
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    new_next_run = (now_dt + datetime.timedelta(seconds=snooze_seconds)).isoformat()
    now = utc_now_iso()

    async with get_db_connection() as conn:
        await conn.execute(
            """
            UPDATE reminders 
            SET next_run_at = ?, status = 'active', updated_at = ?
            WHERE id = ?
            """,
            (new_next_run, now, reminder_id),
        )
        await conn.commit()
    return await get_reminder(reminder_id)


async def complete_reminder(reminder_id: int) -> Optional[dict[str, Any]]:
    now = utc_now_iso()
    async with get_db_connection() as conn:
        await conn.execute(
            "UPDATE reminders SET status = 'completed', updated_at = ? WHERE id = ?",
            (now, reminder_id),
        )
        await conn.commit()
    return await get_reminder(reminder_id)


async def cancel_reminder(reminder_id: int) -> Optional[dict[str, Any]]:
    now = utc_now_iso()
    async with get_db_connection() as conn:
        await conn.execute(
            "UPDATE reminders SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (now, reminder_id),
        )
        await conn.commit()
    return await get_reminder(reminder_id)


async def delete_reminder(reminder_id: int, user_id: Optional[int] = None) -> bool:
    async with get_db_connection() as conn:
        if user_id is not None:
            cursor = await conn.execute(
                "DELETE FROM reminders WHERE id = ? AND user_id = ?",
                (reminder_id, user_id),
            )
        else:
            cursor = await conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        await conn.commit()
        return cursor.rowcount > 0


async def get_due_reminders(current_time_iso: str) -> list[dict[str, Any]]:
    async with get_db_connection() as conn:
        async with conn.execute(
            """
            SELECT * FROM reminders 
            WHERE status = 'active' AND next_run_at <= ?
            ORDER BY next_run_at ASC
            LIMIT 50
            """,
            (current_time_iso,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def advance_reminder(
    reminder_id: int,
    next_run_at: Optional[str],
    mark_completed: bool = False,
) -> None:
    now = utc_now_iso()
    async with get_db_connection() as conn:
        if mark_completed or next_run_at is None:
            await conn.execute(
                """
                UPDATE reminders 
                SET status = 'completed', last_run_at = ?, run_count = run_count + 1, updated_at = ?
                WHERE id = ?
                """,
                (now, now, reminder_id),
            )
        else:
            await conn.execute(
                """
                UPDATE reminders 
                SET next_run_at = ?, last_run_at = ?, run_count = run_count + 1, updated_at = ?
                WHERE id = ?
                """,
                (next_run_at, now, now, reminder_id),
            )
        await conn.commit()
