from __future__ import annotations

import asyncio
import datetime
import os
import tempfile
import pytest
from backend.config import settings
from backend.database import (
    advance_reminder,
    cancel_reminder,
    complete_reminder,
    create_reminder,
    delete_reminder,
    get_due_reminders,
    get_or_create_user,
    get_reminder,
    get_user_reminders,
    get_user_timezone,
    init_db,
    set_user_timezone,
    snooze_reminder,
)
from backend.omniroute_ai import parse_interval_string


@pytest.fixture(autouse=True)
def test_db_setup(monkeypatch):
    # Use temporary sqlite database for tests
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        test_db_path = tmp.name
    monkeypatch.setattr(settings, "database_path", test_db_path)
    
    asyncio.run(init_db())
    yield
    
    if os.path.exists(test_db_path):
        os.remove(test_db_path)


def test_interval_parsing():
    assert parse_interval_string("Take pills every 8 hours")[0] == 8 * 3600
    assert parse_interval_string("Take pills every 6h")[0] == 6 * 3600
    assert parse_interval_string("Water plants every 7 days")[0] == 7 * 86400
    assert parse_interval_string("Check report daily")[0] == 24 * 3600
    assert parse_interval_string("One time task")[0] is None


@pytest.mark.asyncio
async def test_user_and_timezone():
    user = await get_or_create_user(12345, "testuser", "Test")
    assert user["user_id"] == 12345
    assert user["timezone"] == "UTC"

    await set_user_timezone(12345, "America/New_York")
    tz = await get_user_timezone(12345)
    assert tz == "America/New_York"


@pytest.mark.asyncio
async def test_create_and_query_reminder():
    user_id = 999
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    start_time = (now_utc + datetime.timedelta(hours=2)).isoformat()
    end_time = (now_utc + datetime.timedelta(days=5)).isoformat()

    # Create recurring reminder with 8 hours interval
    rem = await create_reminder(
        user_id=user_id,
        chat_id=user_id,
        title="Check server metrics",
        start_time=start_time,
        interval_seconds=8 * 3600,
        interval_label="Every 8 hours",
        end_time=end_time,
    )
    assert rem["id"] is not None
    assert rem["title"] == "Check server metrics"
    assert rem["interval_seconds"] == 28800
    assert rem["interval_label"] == "Every 8 hours"
    assert rem["status"] == "active"

    # Query
    list_active = await get_user_reminders(user_id, status="active")
    assert len(list_active) == 1
    assert list_active[0]["id"] == rem["id"]


@pytest.mark.asyncio
async def test_snooze_and_complete():
    user_id = 888
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    start_time = now_utc.isoformat()

    rem = await create_reminder(
        user_id=user_id,
        chat_id=user_id,
        title="Quick task",
        start_time=start_time,
    )

    # Snooze +15m (900s)
    snoozed = await snooze_reminder(rem["id"], 900)
    assert snoozed is not None
    assert snoozed["next_run_at"] > start_time

    # Complete
    completed = await complete_reminder(rem["id"])
    assert completed["status"] == "completed"

    # Delete
    deleted = await delete_reminder(rem["id"], user_id=user_id)
    assert deleted is True
    assert await get_reminder(rem["id"]) is None


@pytest.mark.asyncio
async def test_due_reminders_and_recurrence_advance():
    user_id = 777
    past_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat()
    future_end = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)).isoformat()

    # Recurring reminder due in past
    rem = await create_reminder(
        user_id=user_id,
        chat_id=user_id,
        title="Recurring due task",
        start_time=past_time,
        interval_seconds=6 * 3600,
        interval_label="Every 6 hours",
        end_time=future_end,
    )

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    due = await get_due_reminders(now_iso)
    assert any(r["id"] == rem["id"] for r in due)

    # Advance by 6 hours
    next_time = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=6)).isoformat()
    await advance_reminder(rem["id"], next_time, mark_completed=False)

    updated = await get_reminder(rem["id"])
    assert updated["status"] == "active"
    assert updated["run_count"] == 1
    assert updated["next_run_at"] == next_time

    # Test end_time expiration
    await advance_reminder(rem["id"], None, mark_completed=True)
    expired = await get_reminder(rem["id"])
    assert expired["status"] == "completed"
