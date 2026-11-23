"""Tests for upgraded IntakeRecoveryService (3-touch closer) and ReviewHarvesterService."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.cron import CRON_REGISTRY
from dashboard.routers.automation_control import ALL_SERVICE_KEYS, SERVICE_META
from dashboard.routers.automation_sweeps import automation_bp
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
            if k == "$and":
                if not all(self._matches(doc, sub) for sub in v):
                    return False
            elif k == "$or":
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

    async def fake_send(phone, message, purpose=None, **kwargs):
        sent_calls.append((phone, message, purpose))
        return {"success": True}

    with patch("dashboard.services.bb_client.send_message_universal", side_effect=fake_send), patch(
        "dashboard.services.sms_consent_ledger.check_send_allowed",
        new=AsyncMock(return_value=None),
    ):
        # Cycle 1 at T+1h (full_auto) -> touch_1 sent
        res1 = await svc.scan_and_recover({"mode": "full_auto"}, now=now)
        assert res1["skipped_opt_out"] == 1
        assert res1["skipped_converted"] == 1
        assert res1["recovered_sent"] == 1
        assert len(sent_calls) == 1
        assert sent_calls[0][2] == "intake_recovery"
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
    with patch("dashboard.routers.notifications.create_notification", new=AsyncMock()) as mock_notif, patch(
        "dashboard.services.sms_consent_ledger.check_send_allowed",
        new=AsyncMock(return_value=None),
    ):
        rev_res = await svc.scan_and_harvest({"mode": "review"}, now=now)
    assert rev_res["review_queued"] == 1
    assert rev_res["sent"] == 0
    assert mock_notif.await_count == 1

    # 2. Full auto skips review_queued-only block and sends once, privacy-safe
    sent_msgs = []

    async def fake_send(phone, msg, purpose=None, **kwargs):
        sent_msgs.append((phone, msg, purpose))
        return {"success": True}

    with patch("dashboard.services.bb_client.send_message_universal", side_effect=fake_send), patch(
        "dashboard.services.sms_consent_ledger.check_send_allowed",
        new=AsyncMock(return_value=None),
    ):
        auto_res = await svc.scan_and_harvest({"mode": "full_auto"}, now=now)
        assert auto_res["sent"] == 1
        # Repeat run dedupes permanently for 180 days
        dup_res = await svc.scan_and_harvest({"mode": "full_auto"}, now=now + timedelta(days=1))
        assert dup_res["sent"] == 0
        assert dup_res["skipped_dedup"] >= 1

    assert len(sent_msgs) == 1
    assert sent_msgs[0][2] == "review_harvester"
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


def test_review_harvester_cron_and_service_meta_registration():
    matches = [c for c in CRON_REGISTRY if c.name == "review_harvester"]
    assert len(matches) == 1
    assert matches[0].always_on is False
    assert matches[0].default_enabled is True
    assert "review_harvester" in ALL_SERVICE_KEYS
    assert "review_harvester" in SERVICE_META


def test_review_harvester_endpoint_auth_mode_validation_and_run(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "test-gas-key-review")
    app = FastAPI()
    app.include_router(automation_bp)
    client = TestClient(app)

    # 1. Missing auth -> 401
    unauth = client.post("/api/automation/review-harvester", json={})
    assert unauth.status_code == 401

    # 2. Invalid mode -> 400
    with patch("dashboard.extensions.get_db", return_value=_FakeDB()), patch(
        "dashboard.services.automation_config.get_automation_config",
        new=AsyncMock(return_value={"review_harvester": {"mode": "review"}}),
    ):
        bad_mode = client.post(
            "/api/automation/review-harvester",
            headers={"X-API-Key": "test-gas-key-review"},
            json={"mode": "bogus"},
        )
    assert bad_mode.status_code == 400
    assert bad_mode.json()["error"] == "invalid_mode"

    # 3. Valid auth -> 200
    with patch("dashboard.extensions.get_db", return_value=_FakeDB()), patch(
        "dashboard.services.automation_config.get_automation_config",
        new=AsyncMock(return_value={"review_harvester": {"mode": "review"}}),
    ), patch(
        "dashboard.services.review_harvester_service.ReviewHarvesterService.scan_and_harvest",
        new=AsyncMock(return_value={"scanned": 2, "review_queued": 1, "sent": 0, "mode": "review"}),
    ):
        ok = client.post(
            "/api/automation/review-harvester",
            headers={"X-API-Key": "test-gas-key-review"},
            json={"mode": "review"},
        )
    assert ok.status_code == 200, ok.text
    payload = ok.json()
    assert payload["ok"] is True
    assert payload["action"] == "review_harvester"
    assert payload["review_queued"] == 1


@pytest.mark.asyncio
async def test_review_harvester_invalid_url_fails_closed_and_status_delinquent_skipped():
    now = datetime(2026, 11, 21, 16, 0, tzinfo=timezone.utc)
    svc_bad_url = ReviewHarvesterService(_FakeDB())
    bad_res = await svc_bad_url.scan_and_harvest(
        {"mode": "review", "review_url": "http://insecure.invalid/review"},
        now=now,
    )
    assert bad_res["skipped"] is True
    assert bad_res["reason"] == "invalid_review_url"

    db = _FakeDB({
        "active_bonds": [
            {
                "booking_number": "BK-STATUS-DELINQ",
                "status": "active",
                "indemnitor_name": "Delinquent Plan",
                "indemnitor_phone": "2395550333",
                "created_at": now - timedelta(days=5),
            }
        ],
        "payment_plans": [
            {"booking_number": "BK-STATUS-DELINQ", "status": "delinquent"},
        ],
    })
    svc = ReviewHarvesterService(db)
    with patch("dashboard.services.sms_consent_ledger.check_send_allowed", new=AsyncMock(return_value=None)):
        res = await svc.scan_and_harvest({"mode": "review"}, now=now)
    assert res["skipped_ineligible"] == 1
    assert res["review_queued"] == 0


@pytest.mark.asyncio
async def test_global_sms_consent_ledger_blocks_both_services():
    now = datetime(2026, 11, 21, 16, 0, tzinfo=timezone.utc)
    blocked_doc = {"blocked": True, "reason": "recipient_opted_out", "purpose": "test"}

    # 1. IntakeRecoveryService respects global STOP ledger in full_auto
    rec_db = _FakeDB({
        "intake_queue": [
            {
                "intake_id": "INT-LEDGER-STOP",
                "status": "pending",
                "phone": "2395550444",
                "updated_at": now - timedelta(hours=2),
            }
        ]
    })
    rec_svc = IntakeRecoveryService(rec_db)
    with patch(
        "dashboard.services.sms_consent_ledger.check_send_allowed",
        new=AsyncMock(return_value=blocked_doc),
    ), patch(
        "dashboard.services.bb_client.send_message_universal",
        new=AsyncMock(),
    ) as mock_rec_send:
        rec_res = await rec_svc.scan_and_recover({"mode": "full_auto"}, now=now)
    assert rec_res["skipped_opt_out"] == 1
    assert rec_res["recovered_sent"] == 0
    assert mock_rec_send.await_count == 0

    # 2. ReviewHarvesterService respects global STOP ledger in full_auto & review modes
    rev_db = _FakeDB({
        "active_bonds": [
            {
                "booking_number": "BK-LEDGER-STOP",
                "status": "posted",
                "indemnitor_name": "Stopped Client",
                "indemnitor_phone": "2395550445",
                "created_at": now - timedelta(days=5),
            }
        ]
    })
    rev_svc = ReviewHarvesterService(rev_db)
    with patch(
        "dashboard.services.sms_consent_ledger.check_send_allowed",
        new=AsyncMock(return_value=blocked_doc),
    ), patch(
        "dashboard.services.bb_client.send_message_universal",
        new=AsyncMock(),
    ) as mock_rev_send:
        rev_res = await rev_svc.scan_and_harvest({"mode": "full_auto"}, now=now)
    assert rev_res["skipped_opt_out"] == 1
    assert rev_res["sent"] == 0
    assert mock_rev_send.await_count == 0
