"""Tests for upgraded IntakeRecoveryService (3-touch closer) and ReviewHarvesterService."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from dashboard.services.automation_config import DEFAULT_CONFIG
from dashboard.services.intake_recovery_service import IntakeRecoveryService, build_resume_url
from dashboard.services.review_harvester_service import (
    ReviewHarvesterService,
    build_review_message,
)


class _AsyncCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *args, **kwargs):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeCol:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.inserts = []

    def _matches(self, doc, query):
        for k, v in (query or {}).items():
            if k == "$or":
                if not any(self._matches(doc, sub) for sub in v):
                    return False
            elif isinstance(v, dict):
                val = doc.get(k)
                if "$in" in v and val not in v["$in"]:
                    return False
                if "$gte" in v and (val is None or val < v["$gte"]):
                    return False
                if "$lt" in v and (val is None or val >= v["$lt"]):
                    return False
                if "$exists" in v and (k in doc) != bool(v["$exists"]):
                    return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    def find(self, query=None):
        return _AsyncCursor([d for d in self.docs if self._matches(d, query or {})])

    async def find_one(self, query=None):
        for d in self.docs:
            if self._matches(d, query or {}):
                return d
        return None

    async def insert_one(self, doc):
        self.docs.append(doc)
        self.inserts.append(doc)


class _FakeDB:
    def __init__(self, collections=None):
        self._cols = {k: _FakeCol(v) for k, v in (collections or {}).items()}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = _FakeCol()
        return self._cols[name]


def test_defaults_keep_review_mode():
    assert DEFAULT_CONFIG["intake_recovery"]["mode"] == "review"
    assert DEFAULT_CONFIG["intake_recovery"]["nudge_2_hours"] == 4
    assert DEFAULT_CONFIG["intake_recovery"]["nudge_3_hours"] == 24
    assert DEFAULT_CONFIG["review_harvester"]["mode"] == "review"
    assert DEFAULT_CONFIG["review_harvester"]["enabled"] is True


def test_build_resume_url_preserves_role_and_county():
    url = build_resume_url({"role": "indemnitor", "county": "Lee"})
    assert url == "https://www.shamrockbailbonds.biz/portal-landing?role=indemnitor&source=recovery&county=Lee"
    def_url = build_resume_url({"role": "defendant"})
    assert def_url == "https://www.shamrockbailbonds.biz/portal-landing?role=defendant&source=recovery"


@pytest.mark.asyncio
async def test_intake_recovery_skips_opt_out_and_converted_and_runs_3_touch():
    now = datetime(2026, 11, 21, 16, 0, tzinfo=timezone.utc)
    db = _FakeDB({
        "intake_queue": [
            {
                "intake_id": "INT-OPTOUT",
                "status": "pending",
                "phone": "2395550100",
                "do_not_contact": True,
                "updated_at": now - timedelta(hours=5),
            },
            {
                "intake_id": "INT-DONE",
                "status": "pending",
                "phone": "2395550101",
                "updated_at": now - timedelta(hours=5),
            },
            {
                "intake_id": "INT-ACTIVE",
                "status": "pending",
                "role": "indemnitor",
                "county": "Lee",
                "indemnitor_first_name": "Maria",
                "defendant_name": "Carlos Ruiz",
                "phone": "2395550199",
                "updated_at": now - timedelta(hours=1),
            },
        ],
        "paperwork_packets": [
            {"intake_id": "INT-DONE", "status": "signed"},
        ],
    })

    svc = IntakeRecoveryService(db)
    sent_calls = []

    async def fake_send(phone, message):
        sent_calls.append((phone, message))
        return {"success": True}

    with patch("dashboard.services.bb_client.send_message_universal", side_effect=fake_send):
        # Cycle 1 at T+1h (full_auto) -> touch_1 sent
        res1 = await svc.scan_and_recover({"mode": "full_auto"}, now=now)
        assert res1["skipped_opt_out"] == 1
        assert res1["skipped_converted"] == 1
        assert res1["recovered_sent"] == 1
        assert len(sent_calls) == 1
        assert "239) 955-0178" in sent_calls[0][1]
        assert "portal-landing?role=indemnitor" in sent_calls[0][1]

        # Cycle 2 at T+5h (after 4h gap) -> touch_2 sent
        db["intake_queue"].docs[2]["updated_at"] = now - timedelta(hours=5)
        res2 = await svc.scan_and_recover({"mode": "full_auto"}, now=now + timedelta(hours=4))
        assert res2["recovered_sent"] == 1
        assert db["intake_recovery_log"].inserts[-1]["touch"] == "touch_2"

        # Cycle 3 at T+30h (after 25h since touch_2) -> touch_3 sent
        db["intake_queue"].docs[2]["updated_at"] = now - timedelta(hours=30)
        res3 = await svc.scan_and_recover({"mode": "full_auto"}, now=now + timedelta(hours=29))
        assert res3["recovered_sent"] == 1
        assert db["intake_recovery_log"].inserts[-1]["touch"] == "touch_3"


@pytest.mark.asyncio
async def test_review_harvester_filters_unsafe_bonds_and_sends_privacy_safe_invite():
    # 11:00 AM ET = 16:00 UTC (inside daytime window)
    now = datetime(2026, 11, 21, 16, 0, tzinfo=timezone.utc)
    db = _FakeDB({
        "active_bonds": [
            {
                "booking_number": "BK-FORFEIT",
                "status": "forfeited",
                "indemnitor_name": "Bad Case",
                "indemnitor_phone": "2395550201",
                "created_at": now - timedelta(days=5),
            },
            {
                "booking_number": "BK-DELINQ",
                "status": "active",
                "indemnitor_name": "Late Payer",
                "indemnitor_phone": "2395550202",
                "created_at": now - timedelta(days=5),
            },
            {
                "booking_number": "BK-TOO-NEW",
                "status": "active",
                "indemnitor_name": "Brand New",
                "indemnitor_phone": "2395550203",
                "created_at": now - timedelta(hours=6),
            },
            {
                "booking_number": "BK-GOOD",
                "status": "discharged",
                "defendant_name": "SECRET DEFENDANT NAME",
                "charges": "TRAFFICKING",
                "indemnitor_name": "Sarah Connor",
                "indemnitor_phone": "(239) 555-0299",
                "discharged_at": now - timedelta(days=4),
            },
        ],
        "payment_plans": [
            {"booking_number": "BK-DELINQ", "delinquent": True},
        ],
    })

    svc = ReviewHarvesterService(db)
    # 1. Default review mode never sends outbound messages
    with patch("dashboard.routers.notifications.create_notification", new=AsyncMock()) as mock_notif:
        rev_res = await svc.scan_and_harvest({"mode": "review"}, now=now)
    assert rev_res["review_queued"] == 1
    assert rev_res["sent"] == 0
    assert mock_notif.await_count == 1

    # 2. Full auto skips review_queued-only block and sends once, privacy-safe
    sent_msgs = []

    async def fake_send(phone, msg):
        sent_msgs.append((phone, msg))
        return {"success": True}

    with patch("dashboard.services.bb_client.send_message_universal", side_effect=fake_send):
        auto_res = await svc.scan_and_harvest({"mode": "full_auto"}, now=now)
        assert auto_res["sent"] == 1
        # Repeat run dedupes permanently for 180 days
        dup_res = await svc.scan_and_harvest({"mode": "full_auto"}, now=now + timedelta(days=1))
        assert dup_res["sent"] == 0
        assert dup_res["skipped_dedup"] >= 1

    assert len(sent_msgs) == 1
    body = sent_msgs[0][1]
    assert "SECRET DEFENDANT NAME" not in body
    assert "TRAFFICKING" not in body
    assert "https://g.page/r/shamrockbailbonds/review" in body
    assert "Reply STOP to opt out" in body

    # 3. Nighttime ET (2 AM ET = 07:00 UTC) skips cleanly in full_auto
    night = datetime(2026, 11, 21, 7, 0, tzinfo=timezone.utc)
    night_res = await svc.scan_and_harvest({"mode": "full_auto"}, now=night)
    assert night_res["skipped"] is True
    assert night_res["reason"] == "outside_daytime_hours"


def test_build_review_message_contains_no_pii():
    msg = build_review_message("Alex", "https://g.page/r/shamrockbailbonds/review")
    assert "Alex" in msg
    assert "Reply STOP to opt out" in msg
