"""Sheets ledger + Slack fan-out after the Mongo save: non-blocking, logged, retried."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from dashboard.services import intake_fanout as fo
from tests._inmem_mongo import FakeDB

DOC = {
    "intake_id": "WX-TEST1",
    "source": "wix_webhook",
    "form_type": "indemnitor_application",
    "submitted_by_role": "indemnitor",
    "defendant_name": "Bob Roe",
    "indemnitor_name": "Amy Q Roe",
    "indemnitor_phone": "(239) 555-0101",
    "indemnitor_email": "amy@example.com",
    "defendant_county": "Collier",
    "defendant_booking_number": "2026-0001",
    "defendant": {"county": "Collier", "bondAmount": "5000", "dob": "1990-01-01"},
    "indemnitor": {"ssn": "987654321", "dob": "1985-05-05", "address": "9 Bay Rd"},
    "surety_id": None,
    "status": "pending",
    "created_at": datetime(2026, 9, 27, 22, 30, tzinfo=timezone.utc),
}


def test_ledger_row_minimal_pii():
    row = fo.ledger_row(DOC)
    assert row["date_et"] == "2026-09-27" and row["time_et"] == "18:30:00"
    assert row["contact_phone_last4"] == "0101"
    text = str(row) + fo.slack_text(DOC)
    for secret in ("987654321", "1985-05-05", "9 Bay Rd", "1990-01-01", "555-0101"):
        assert secret not in text
    assert "Amy R." in fo.slack_text(DOC)


@pytest.fixture
def db():
    d = FakeDB()
    with patch("dashboard.extensions.get_collection", side_effect=d.get_collection):
        yield d


def _run(coro):
    return asyncio.run(coro)


def test_success_path(db):
    with patch.object(fo, "_send_sheets", AsyncMock(return_value=(True, "", False))) as sh, \
         patch.object(fo, "_send_slack", AsyncMock(return_value=(True, "", False))) as sl:
        async def go():
            await fo.enqueue(DOC)
            return await fo.process_intake("WX-TEST1")
        out = _run(go())
    assert out == {"sheets": "sent", "slack": "sent"}
    sh.assert_awaited_once(); sl.assert_awaited_once()
    # re-processing does not resend
    with patch.object(fo, "_send_sheets", AsyncMock()) as sh2, patch.object(fo, "_send_slack", AsyncMock()) as sl2:
        _run(fo.process_intake("WX-TEST1"))
        sh2.assert_not_awaited(); sl2.assert_not_awaited()


def test_failure_is_recorded_and_retried_with_backoff(db):
    with patch.object(fo, "_send_sheets", AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(fo, "_send_slack", AsyncMock(return_value=(False, "", True))):
        async def go():
            await fo.enqueue(DOC)
            return await fo.process_intake("WX-TEST1")
        out = _run(go())  # _deliver catches the exception
    assert out == {"sheets": "failed", "slack": "skipped_unconfigured"}
    rows = {r["target"]: r for r in db[fo.OUTBOX].docs}
    assert rows["sheets"]["attempts"] == 1 and "boom" in rows["sheets"]["last_error"]
    assert rows["sheets"]["next_attempt_at"] > datetime.now(timezone.utc) + timedelta(seconds=30)

    # not due yet → cron does nothing
    with patch.object(fo, "_send_sheets", AsyncMock(return_value=(True, "", False))) as sh:
        assert _run(fo.retry_due()) == {}
        sh.assert_not_awaited()
    # make it due → cron retries and succeeds
    for r in db[fo.OUTBOX].docs:
        r["next_attempt_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    with patch.object(fo, "_send_sheets", AsyncMock(return_value=(True, "", False))), \
         patch.object(fo, "_send_slack", AsyncMock(return_value=(True, "", False))):
        counts = _run(fo.retry_due())
    assert counts == {"sent": 2}


def test_dead_after_max_attempts(db):
    with patch.object(fo, "_send_sheets", AsyncMock(return_value=(False, "500", False))), \
         patch.object(fo, "_send_slack", AsyncMock(return_value=(True, "", False))):
        _run(fo.enqueue(DOC))
        for r in db[fo.OUTBOX].docs:
            if r["target"] == "sheets":
                r["attempts"] = fo.MAX_ATTEMPTS - 1
        out = _run(fo.process_intake("WX-TEST1"))
    assert out["sheets"] == "dead"


def test_kill_switch(db):
    with patch.dict(os.environ, {"INTAKE_FANOUT_DISABLED": "1"}), \
         patch.object(fo, "_send_sheets", AsyncMock()) as sh:
        _run(fo.enqueue(DOC))
        _run(fo.process_intake("WX-TEST1"))
        sh.assert_not_awaited()
    assert all(r["status"] == "pending" for r in db[fo.OUTBOX].docs)


def test_enqueue_never_raises_when_mongo_down():
    with patch("dashboard.extensions.get_collection", side_effect=RuntimeError("down")):
        _run(fo.enqueue(DOC))
        assert _run(fo.process_intake("WX-TEST1")) == {}
        assert _run(fo.retry_due()) == {}


def test_send_sheets_unconfigured():
    with patch.dict(os.environ, {"GAS_WEB_APP_URL": "", "GAS_API_KEY": ""}):
        ok, err, unconf = _run(fo._send_sheets({}))
    assert not ok and unconf


def test_schedule_after_save_is_fire_and_forget(db):
    started = asyncio.Event()

    async def slow_sheets(row):
        started.set()
        await asyncio.sleep(0.05)
        return True, "", False

    async def go():
        with patch.object(fo, "_send_sheets", slow_sheets), \
             patch.object(fo, "_send_slack", AsyncMock(return_value=(True, "", False))):
            task = fo.schedule_after_save(DOC)
            assert task is not None and not task.done()  # returned before delivery
            await task
    _run(go())
    assert {r["status"] for r in db[fo.OUTBOX].docs} == {"sent"}
