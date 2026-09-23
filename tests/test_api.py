from __future__ import annotations

import datetime
import pytest
from httpx import ASGITransport, AsyncClient
from backend.database import init_db
from backend.main import app


@pytest.mark.asyncio
async def test_api_endpoints():
    await init_db()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Test health check
        health = await ac.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        # Test GET /api/me (in dev mode)
        me = await ac.get("/api/me")
        assert me.status_code == 200
        data = me.json()
        assert data["user_id"] == 12345678
        assert "meal_times" in data
        assert data["meal_times"]["breakfast"] == "08:30"

        # Test PATCH /api/me/meal-times
        patch_res = await ac.patch(
            "/api/me/meal-times",
            json={"breakfast": "07:45", "dinner": "21:15"},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["meal_times"]["breakfast"] == "07:45"
        assert patch_res.json()["meal_times"]["dinner"] == "21:15"
        assert patch_res.json()["meal_times"]["lunch"] == "12:30"

        # Test POST /api/me/meal-times/reset
        reset_res = await ac.post("/api/me/meal-times/reset")
        assert reset_res.status_code == 200
        assert reset_res.json()["meal_times"]["breakfast"] == "08:30"

        # Test POST /api/reminders
        start_time = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat()
        payload = {
            "title": "Drink water",
            "start_time": start_time,
            "interval_seconds": 7200,
            "interval_label": "Every 2 hours",
        }
        res = await ac.post("/api/reminders", json=payload)
        assert res.status_code == 201
        rem = res.json()
        assert rem["title"] == "Drink water"
        assert rem["interval_seconds"] == 7200

        # Test GET /api/reminders
        reminders_res = await ac.get("/api/reminders")
        assert reminders_res.status_code == 200
        reminders = reminders_res.json()
        assert len(reminders) >= 1

        # Test Snooze
        rem_id = rem["id"]
        snooze_res = await ac.post(f"/api/reminders/{rem_id}/snooze", json={"seconds": 900})
        assert snooze_res.status_code == 200

        # Test Complete
        comp_res = await ac.post(f"/api/reminders/{rem_id}/complete")
        assert comp_res.status_code == 200
        assert comp_res.json()["status"] == "completed"

        # Test Delete
        del_res = await ac.delete(f"/api/reminders/{rem_id}")
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "deleted"

        # Test POST /api/reminders/batch
        batch_payload = [
            {"title": "Task 1", "start_time": start_time},
            {"title": "Task 2", "start_time": start_time, "interval_seconds": 3600, "interval_label": "Every hour"},
        ]
        batch_res = await ac.post("/api/reminders/batch", json=batch_payload)
        assert batch_res.status_code == 201
        created_batch = batch_res.json()
        assert len(created_batch) == 2
        assert created_batch[0]["title"] == "Task 1"
        assert created_batch[1]["title"] == "Task 2"

        # Test POST /api/ai/parse
        ai_res = await ac.post("/api/ai/parse", json={"prompt": "Remind me to call John and also wash car"})
        assert ai_res.status_code == 200
        ai_data = ai_res.json()
        assert "reminders" in ai_data
        assert len(ai_data["reminders"]) >= 1


@pytest.mark.asyncio
async def test_admin_whitelist():
    from backend.database import add_admin_user, is_admin_user, list_admin_users, remove_admin_user
    await init_db()
    test_user_id = 9876543210
    assert not await is_admin_user(test_user_id)
    await add_admin_user(test_user_id)
    assert await is_admin_user(test_user_id)
    admins = await list_admin_users()
    assert test_user_id in admins
    await remove_admin_user(test_user_id)
    assert not await is_admin_user(test_user_id)

