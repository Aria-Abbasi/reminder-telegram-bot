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
