from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.parse
from typing import Any, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from backend.config import settings
from backend.database import (
    cancel_reminder,
    complete_reminder,
    create_reminder,
    delete_reminder,
    get_or_create_user,
    get_reminder,
    get_user_reminders,
    get_user_timezone,
    set_user_timezone,
    snooze_reminder,
)
from backend.omniroute_ai import parse_reminder_with_omniroute

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# Pydantic Schemas
class CreateReminderRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    start_time: str
    interval_seconds: Optional[int] = None
    interval_label: Optional[str] = None
    end_time: Optional[str] = None


class SnoozeRequest(BaseModel):
    seconds: int = Field(default=900, ge=60, le=86400 * 7)


class TimezoneRequest(BaseModel):
    timezone: str


class AIParseRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=1000)


def validate_telegram_init_data(init_data: str) -> dict[str, Any]:
    """
    Validates Telegram WebApp initData string using bot token HMAC-SHA-256.
    Returns the parsed user dict if valid.
    """
    if not init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Telegram initData header",
        )

    parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)

    # For local developer testing / standalone browser preview without Telegram:
    if not settings.telegram_bot_token or settings.telegram_bot_token.startswith("your_"):
        if "user" in parsed:
            try:
                return json.loads(parsed["user"])
            except Exception:
                pass
        return {"id": 12345678, "first_name": "Demo User", "username": "demouser"}

    if not received_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram initData: missing hash",
        )

    # Data check string: alphabetical order of key=value joined by \n
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", settings.telegram_bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telegram initData signature verification failed",
        )

    user_raw = parsed.get("user")
    if not user_raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing user object in initData",
        )
    return json.loads(user_raw)


async def get_current_user(
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
) -> dict[str, Any]:
    if not x_telegram_init_data:
        # Check if in demo/dev mode
        if not settings.telegram_bot_token or settings.telegram_bot_token.startswith("your_"):
            user_data = {"id": 12345678, "first_name": "Demo User", "username": "demouser"}
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="X-Telegram-Init-Data header required",
            )
    else:
        user_data = validate_telegram_init_data(x_telegram_init_data)

    user_id = int(user_data["id"])
    username = user_data.get("username")
    first_name = user_data.get("first_name")
    return await get_or_create_user(user_id, username, first_name)


# Endpoints
@router.get("/me")
async def get_me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    reminders = await get_user_reminders(user["user_id"], status="active")
    return {
        "user_id": user["user_id"],
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "timezone": user.get("timezone", settings.default_timezone),
        "active_count": len(reminders),
    }


@router.patch("/me/timezone")
async def update_timezone(
    req: TimezoneRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    await set_user_timezone(user["user_id"], req.timezone)
    return {"status": "ok", "timezone": req.timezone}


@router.get("/reminders")
async def list_reminders(
    status_filter: Optional[str] = Query(None, alias="status"),
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    return await get_user_reminders(user["user_id"], status=status_filter)


@router.post("/reminders", status_code=status.HTTP_201_CREATED)
async def create_new_reminder(
    req: CreateReminderRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    reminder = await create_reminder(
        user_id=user["user_id"],
        chat_id=user["user_id"],  # Default chat_id is the user's private chat
        title=req.title,
        start_time=req.start_time,
        interval_seconds=req.interval_seconds,
        interval_label=req.interval_label,
        end_time=req.end_time,
    )
    return reminder


@router.post("/reminders/{reminder_id}/snooze")
async def snooze(
    reminder_id: int,
    req: SnoozeRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    rem = await get_reminder(reminder_id)
    if not rem or rem["user_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="Reminder not found")
    updated = await snooze_reminder(reminder_id, req.seconds)
    return updated or {}


@router.post("/reminders/{reminder_id}/complete")
async def complete(
    reminder_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    rem = await get_reminder(reminder_id)
    if not rem or rem["user_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="Reminder not found")
    updated = await complete_reminder(reminder_id)
    return updated or {}


@router.post("/reminders/{reminder_id}/cancel")
async def cancel_recurrence(
    reminder_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    rem = await get_reminder(reminder_id)
    if not rem or rem["user_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="Reminder not found")
    updated = await cancel_reminder(reminder_id)
    return updated or {}


@router.delete("/reminders/{reminder_id}")
async def delete(
    reminder_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    success = await delete_reminder(reminder_id, user_id=user["user_id"])
    if not success:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return {"status": "deleted"}


@router.post("/ai/parse")
async def ai_parse(
    req: AIParseRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    tz = await get_user_timezone(user["user_id"])
    parsed = await parse_reminder_with_omniroute(req.prompt, user_timezone=tz)
    return parsed.to_dict()
