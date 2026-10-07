"""Contract tests for bond-workflow entries and exits.

External services are fakes. These tests never call Twilio, ElevenLabs,
DocuSeal, SwipeSimple, Slack, Google Sheets, or BlueBubbles.
"""
from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.auth.pin_middleware import PinAuthMiddleware
from dashboard.routers.intake import intake_bp
from dashboard.services import intake_fanout as fo
from dashboard.services import payment_links as pl
from tests._inmem_mongo import FakeDB

WIX_SECRET = "workflow-test-wix-secret"
GAS_KEY = "workflow-test-gas-key"


def _run(coro):
    return asyncio.run(coro)


class _Json:
    def __init__(self, body):
        self._body = body

    async def json(self):
        if self._body is _RAISE:
            raise ValueError("bad json")
        return self._body


_RAISE = object()


@pytest.fixture
def intake_app():
    db = FakeDB()
    engine = MagicMock()
    engine.match_intake = AsyncMock(return_value={"auto_linked": False, "confidence": 0, "strategy": None})
    scheduled: list = []

    def _schedule(doc):
        scheduled.append(doc)
        return None

    with patch("dashboard.routers.intake.get_collection", side_effect=db.get_collection), \
         patch("dashboard.routers.intake.get_db", return_value=db), \
         patch("dashboard.services.matching_engine.MatchingEngine", return_value=engine), \
         patch("dashboard.services.intake_fanout.schedule_after_save", side_effect=_schedule):
        app = FastAPI()
        app.include_router(intake_bp)
        yield TestClient(app), db, scheduled, engine


def _person(source: str, **extra) -> dict:
    body = {
        "source": source,
        "indemnitorName": "Amy Roe",
        "indemnitorPhone": "2395550101",
        "defendantName": "Bob Roe",
        "bookingNumber": "2026-4401",
    }
    body.update(extra)
    return body


LEAD_SOURCES = [
    "telegram",
    "telegram_mini_app",
    "walk_in",
    "manual_entry",
    "phone_call",
    "bookmarklet",
    "elevenlabs_voice",
    "shannon",
    "shamrock-leads-dashboard",
]


@pytest.mark.parametrize("source", LEAD_SOURCES)
def test_each_lead_source_saves_and_hands_off(intake_app, source):
    client, db, scheduled, engine = intake_app
    resp = client.post("/api/intake/submit", json=_person(source))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["source"] == source
    assert body["payment_link"] == pl.payment_link_for(source)
    assert body["payment_link"].startswith("https://")
    if source.startswith("telegram"):
        assert "lnk_07a13eb" in body["payment_link"]
    else:
        assert "lnk_b6bf996f" in body["payment_link"]

    [doc] = db["intake_queue"].docs
    assert doc["intake_id"] == body["intake_id"]
    assert doc["status"] == "pending"
    assert doc["defendant_name"] == "Bob Roe"
    assert doc["indemnitor_name"] == "Amy Roe"
    assert doc["defendant_booking_number"] == "2026-4401"
    # No invented county, state, or surety.
    assert doc["defendant"]["county"] == ""
    assert doc["defendant"]["state"] == ""
    assert doc["indemnitor"]["state"] == ""
    assert doc["surety_id"] is None
    assert scheduled and scheduled[0]["intake_id"] == doc["intake_id"]
    if source in ("elevenlabs_voice", "shannon"):
        engine.match_intake.assert_not_awaited()
    else:
        engine.match_intake.assert_awaited()


def test_saved_intake_reaches_sheets_and_slack_fakes(intake_app):
    client, _db, scheduled, _engine = intake_app
    resp = client.post("/api/intake/submit", json=_person("walk_in"))
    assert resp.status_code == 200
    doc = scheduled[0]
    outbox = FakeDB()
    with patch("dashboard.extensions.get_collection", side_effect=outbox.get_collection), \
         patch.object(fo, "_send_sheets", AsyncMock(return_value=(True, "", False))) as sheets, \
         patch.object(fo, "_send_slack", AsyncMock(return_value=(True, "", False))) as slack:
        _run(fo.enqueue(doc))
        result = _run(fo.process_intake(doc["intake_id"]))
    assert result == {"sheets": "sent", "slack": "sent"}
    sheets.assert_awaited_once()
    slack.assert_awaited_once()
    payloads = [row["payload"] for row in outbox[fo.OUTBOX].docs]
    blob = str(payloads)
    assert "2395550101" not in blob
    assert "0101" in blob
    assert "walk-in intake" in blob


def test_intake_submit_fails_closed_on_bad_input(intake_app):
    client, db, scheduled, _engine = intake_app
    empty = client.post("/api/intake/submit", json={})
    assert empty.status_code == 400 and empty.json()["success"] is False
    nameless = client.post("/api/intake/submit", json={"source": "walk_in"})
    assert nameless.status_code == 400
    bad = client.post(
        "/api/intake/submit",
        content=b"not-json",
        headers={"content-type": "application/json"},
    )
    assert bad.status_code == 400
    assert db["intake_queue"].docs == []
    assert scheduled == []


def test_phone_only_walk_in_is_a_lead_without_invented_county(intake_app):
    client, db, scheduled, _engine = intake_app
    resp = client.post("/api/intake/submit", json={"source": "walk_in", "phone": "2395550199"})
    assert resp.status_code == 200, resp.text
    [doc] = db["intake_queue"].docs
    assert doc["indemnitor_phone"] == "2395550199"
    assert doc["defendant"]["county"] == ""
    assert doc["defendant_name"] == "Unknown"
    assert scheduled


def test_wix_portal_submit_fails_closed_and_strips_secret(intake_app):
    client, db, scheduled, _engine = intake_app
    body = _person("wix_portal", secret="should-not-store", apiKey="also-no")
    missing = client.post("/api/intake/submit", json=body)
    assert missing.status_code == 503
    assert db["intake_queue"].docs == []

    with patch.dict(os.environ, {"WIX_WEBHOOK_SECRET": WIX_SECRET}):
        denied = client.post(
            "/api/intake/submit",
            json=body,
            headers={"X-Wix-Webhook-Secret": "wrong-secret"},
        )
        assert denied.status_code == 401
        ok = client.post(
            "/api/intake/submit",
            json=body,
            headers={"X-Wix-Webhook-Secret": WIX_SECRET},
        )
    assert ok.status_code == 200, ok.text
    [doc] = db["intake_queue"].docs
    assert doc["source"] == "wix_portal"
    assert "secret" not in doc["_raw"] and "apiKey" not in doc["_raw"]
    assert "should-not-store" not in str(doc["_raw"])
    assert scheduled
    assert ok.json()["payment_link"] == pl.payment_link_for("wix_portal")


def test_public_intake_requires_staff_session_or_machine_key():
    """Telegram / Shannon callers are not a public form. PIN or a machine key."""
    db = FakeDB()
    scheduled: list = []
    with patch.dict(os.environ, {"DASHBOARD_PIN": "2468", "GAS_API_KEY": GAS_KEY, "ENV": "test"}), \
         patch("dashboard.routers.intake.get_collection", side_effect=db.get_collection), \
         patch("dashboard.routers.intake.get_db", return_value=db), \
         patch("dashboard.services.matching_engine.MatchingEngine") as engine_cls, \
         patch("dashboard.services.intake_fanout.schedule_after_save", side_effect=lambda doc: scheduled.append(doc)):
        engine = MagicMock()
        engine.match_intake = AsyncMock(return_value={"auto_linked": False})
        engine_cls.return_value = engine
        app = FastAPI()
        app.add_middleware(PinAuthMiddleware)
        app.include_router(intake_bp)
        client = TestClient(app)
        anon = client.post("/api/intake/submit", json=_person("telegram"))
        assert anon.status_code == 401
        assert db["intake_queue"].docs == []
        authed = client.post(
            "/api/intake/submit",
            json=_person("telegram"),
            headers={"X-API-Key": GAS_KEY},
        )
    assert authed.status_code == 200, authed.text
    assert db["intake_queue"].docs[0]["source"] == "telegram"
    assert scheduled


def test_promote_refuses_undersized_poa_then_seeds_court_on_fit():
    from dashboard.routers.intake import intake_promote

    db = FakeDB()
    db["intake_queue"].docs.append({
        "intake_id": "WI-1",
        "status": "pending",
        "matched_booking_number": "2026-4401",
        "matched_county": "Collier",
        "match_confidence": 0.95,
        "defendant_name": "Bob Roe",
        "indemnitor_name": "Amy Roe",
        "indemnitor_phone": "2395550101",
        "defendant": {"bondAmount": "5000", "charges": "petit theft"},
        "indemnitor": {"phone": "2395550101"},
        "source": "walk_in",
    })
    db["poa_inventory"].docs.append({
        "poa_number": "OSI-SMALL",
        "poa_full": "OSI-SMALL",
        "surety_id": "osi",
        "status": "available",
        "max_bond_value": 1000,
    })
    seed = AsyncMock(return_value={"success": True, "reason": "seeded"})
    with patch("dashboard.routers.intake.get_collection", side_effect=db.get_collection), \
         patch("dashboard.routers.intake._promote_auto_payment_link", AsyncMock(return_value={"skipped": True})), \
         patch("dashboard.services.bond_court_seed_service.seed_court_calendar_for_bond", seed), \
         patch("dashboard.services.auto_osint_trigger.trigger_auto_osint_profiling", AsyncMock()), \
         patch.dict(os.environ, {"SLACK_WEBHOOK_LEADS": "", "SLACK_WEBHOOK_URL": "", "SWIPESIMPLE_SHARE_INVOICE_ON_PROMOTE": ""}):
        denied = _run(intake_promote(_Json({"surety": "osi", "court_date": "2026-11-02"}), "WI-1"))
        assert denied.status_code == 422
        assert db["active_bonds"].docs == []
        assert db["poa_inventory"].docs[0]["status"] == "available"

        db["poa_inventory"].docs.append({
            "poa_number": "OSI-FIT",
            "poa_full": "OSI-FIT",
            "surety_id": "osi",
            "status": "available",
            "max_bond_value": 10000,
        })
        db["poa_inventory"].docs.append({
            "poa_number": "OSI-BIG",
            "poa_full": "OSI-BIG",
            "surety_id": "osi",
            "status": "available",
            "max_bond_value": 50000,
        })
        ok = _run(intake_promote(_Json({"surety": "osi", "court_date": "2026-11-02"}), "WI-1"))

    assert ok["success"] is True
    assert ok["poa_number"] == "OSI-FIT"
    assert ok["booking_number"] == "2026-4401"
    [bond] = db["active_bonds"].docs
    assert bond["status"] == "active"
    assert bond["source"] == "intake_promotion"
    assert bond["agent_name"] == "Dashboard"
    assert bond["court_date"] == "2026-11-02"
    assert bond["payment_status"] == "pending"
    assigned = {row["poa_number"]: row["status"] for row in db["poa_inventory"].docs}
    assert assigned["OSI-FIT"] == "assigned"
    assert assigned["OSI-SMALL"] == "available"
    assert assigned["OSI-BIG"] == "available"
    assert db["intake_queue"].docs[0]["status"] == "promoted"
    seed.assert_awaited()
    assert seed.await_args.kwargs["source"] == "intake_promote"


def test_promote_fails_closed_without_match_or_surety():
    from dashboard.routers.intake import intake_promote

    db = FakeDB()
    db["intake_queue"].docs.append({
        "intake_id": "WI-2",
        "status": "pending",
        "defendant_name": "Bob Roe",
        "defendant": {},
        "indemnitor": {},
    })
    with patch("dashboard.routers.intake.get_collection", side_effect=db.get_collection):
        no_match = _run(intake_promote(_Json({"surety": "osi"}), "WI-2"))
        missing = _run(intake_promote(_Json({"surety": "osi"}), "NO-SUCH"))
    assert no_match.status_code == 422
    assert missing.status_code == 404
    assert db["active_bonds"].docs == []


def test_id_scan_does_not_create_a_lead_and_rejects_empty_upload():
    from dashboard.routers.indemnitors import router as indemnitors_router

    db = FakeDB()
    scanner = AsyncMock(return_value={"success": True, "extracted": {"full_name": "Amy Roe", "dl": "R1"}})
    with patch("dashboard.routers.indemnitors.IDScannerService.scan_id_image", scanner), \
         patch("dashboard.routers.indemnitors.get_collection", side_effect=db.get_collection):
        app = FastAPI()
        app.include_router(indemnitors_router)
        client = TestClient(app)
        empty = client.post("/api/id/scan-ocr")
        assert empty.status_code == 400
        filled = client.post(
            "/api/id/scan-ocr",
            files={"file": ("id.jpg", b"\xff\xd8fakejpeg", "image/jpeg")},
        )
    assert filled.status_code == 200
    assert filled.json()["extracted"]["full_name"] == "Amy Roe"
    assert db["intake_queue"].docs == []
    scanner.assert_awaited_once()


def test_kiosk_confirm_rejects_bad_token_without_writing():
    from dashboard.routers.pin_portal import pin_portal_router

    db = FakeDB()
    with patch("dashboard.routers.pin_portal.get_collection", side_effect=db.get_collection):
        app = FastAPI()
        app.include_router(pin_portal_router)
        client = TestClient(app)
        resp = client.post("/api/portal/kiosk-id-confirm", json={"scan_token": "not-a-real-token"})
    assert resp.status_code == 400
    assert resp.json()["success"] is False
    assert db["paperwork_packets"].docs == []
    assert db["intake_queue"].docs == []


def test_hydrate_fails_closed_without_booking_or_identity():
    from dashboard.routers.paperwork import hydrate_from_booking

    missing = _run(hydrate_from_booking(_Json({})))
    assert missing.status_code == 400

    async def _no_person(**_kwargs):
        return {"sources": ["arrests"], "defendant": {"name": ""}}

    with patch("dashboard.services.packet_builder_service.resolve_case_context", _no_person):
        unknown = _run(hydrate_from_booking(_Json({"booking_number": "999"})))
    assert unknown.status_code == 404
    assert unknown.body
    assert b"will not invent" in unknown.body


def test_twilio_sms_webhook_does_not_open_a_bond_lead():
    """727 voice and 239 texts are not this route. It audits SMS and stops."""
    from dashboard.routers.webhooks import webhooks_bp

    db = FakeDB()
    with patch.dict(os.environ, {"ENV": "test", "TWILIO_AUTH_TOKEN": ""}), \
         patch("dashboard.routers.webhooks.get_collection", side_effect=db.get_collection):
        app = FastAPI()
        app.include_router(webhooks_bp)
        client = TestClient(app)
        resp = client.post(
            "/api/webhooks/twilio",
            data={"From": "+17272952245", "Body": "need a bond"},
        )
    assert resp.status_code == 200
    assert db["intake_queue"].docs == []
    assert db["active_bonds"].docs == []
    assert db["audit_events"].docs[0]["event_type"] == "inbound_sms"


def test_bluebubbles_unmatched_text_is_review_not_a_new_lead():
    from dashboard.routers.bb_webhook_receiver import bb_webhook_bp

    db = FakeDB()
    with patch("dashboard.extensions.get_db", return_value=db), \
         patch("dashboard.extensions.get_collection", side_effect=db.get_collection), \
         patch("dashboard.routers.bb_webhook_receiver.get_collection", side_effect=db.get_collection), \
         patch("dashboard.routers.bb_webhook_receiver.get_bb_server", return_value=None), \
         patch("dashboard.routers.bb_webhook_receiver._BB_WEBHOOK_SECRET", "bb-test-secret"):
        app = FastAPI()
        app.include_router(bb_webhook_bp)
        client = TestClient(app)
        forged = client.post(
            "/api/webhooks/bluebubbles",
            json={"type": "new-message", "data": {"text": "hi", "isFromMe": False}},
            headers={"x-bb-signature": "deadbeef"},
        )
        assert forged.status_code == 401
        assert db["imessage_outreach"].docs == []
        assert db["intake_queue"].docs == []

    with patch("dashboard.extensions.get_db", return_value=db), \
         patch("dashboard.extensions.get_collection", side_effect=db.get_collection), \
         patch("dashboard.routers.bb_webhook_receiver.get_collection", side_effect=db.get_collection), \
         patch("dashboard.routers.bb_webhook_receiver.get_bb_server", return_value=None), \
         patch("dashboard.routers.bb_webhook_receiver._BB_WEBHOOK_SECRET", ""):
        app = FastAPI()
        app.include_router(bb_webhook_bp)
        client = TestClient(app)
        resp = client.post("/api/webhooks/bluebubbles", json={
            "type": "new-message",
            "data": {
                "guid": "WF-UNMATCHED-1",
                "text": "I need a bond",
                "isFromMe": False,
                "handle": {"address": "+12399550178"},
                "chats": [{"guid": "any;-;+12399550178"}],
                "dateCreated": 1790000000000,
            },
        })
    assert resp.status_code == 200
    assert resp.json()["result"]["matched"] is False
    assert db["intake_queue"].docs == []
    assert db["active_bonds"].docs == []
    assert db["imessage_outreach"].docs[0]["status"] == "unmatched"


def _arrest(booking, county, state="FL", name="Bob Roe", dob="1990-01-15"):
    return {
        "booking_number": booking,
        "county": county,
        "state": state,
        "full_name": name,
        "dob": dob,
        "defendant_id": f"DEF-{booking}",
    }


def _name_dob_intake(**extra):
    doc = {
        "intake_id": "WI-MATCH",
        "status": "pending",
        "defendant_name": "Bob Roe",
        "defendant": {"name": "Bob Roe", "dob": "1990-01-15", "county": "", "state": ""},
        "indemnitor": {"name": "Amy Roe"},
        "indemnitor_name": "Amy Roe",
    }
    doc.update(extra)
    return doc


def test_name_dob_without_county_stays_in_staff_review():
    """Same person in two counties must not auto-link, and neither may a single global hit."""
    from dashboard.services.matching_engine import AUTO_LINK_THRESHOLD, MatchingEngine

    db = FakeDB()
    db["arrests"].docs.extend([
        _arrest("LEE-1", "Lee", "FL"),
        _arrest("COL-2", "Collier", "FL"),
    ])
    intake = _name_dob_intake()
    db["intake_queue"].docs.append(intake)
    engine = MatchingEngine(db)
    result = _run(engine.match_intake(intake, prior_bonds=[]))
    assert result["auto_linked"] is False
    assert result["confidence"] < AUTO_LINK_THRESHOLD
    bookings = {row["booking_number"] for row in result["candidates"]}
    assert bookings == {"LEE-1", "COL-2"}
    counties = {row["county"] for row in result["candidates"]}
    assert counties == {"Lee", "Collier"}
    assert db["intake_queue"].docs[0]["status"] == "pending"
    assert not db["intake_queue"].docs[0].get("matched_booking_number")

    # State alone is not a county. Still staff review.
    state_only = _name_dob_intake(intake_id="WI-STATE")
    state_only["defendant"]["state"] = "FL"
    db["intake_queue"].docs.append(state_only)
    state_result = _run(engine.match_intake(state_only, prior_bonds=[]))
    assert state_result["auto_linked"] is False
    assert {row["booking_number"] for row in state_result["candidates"]} == {"LEE-1", "COL-2"}

    # Exactly one arrest anywhere in the file still does not auto-link.
    solo = FakeDB()
    solo["arrests"].docs.append(_arrest("ONLY-1", "Sarasota", "FL"))
    one = _name_dob_intake(intake_id="WI-ONE")
    solo["intake_queue"].docs.append(one)
    one_result = _run(MatchingEngine(solo).match_intake(one, prior_bonds=[]))
    assert one_result["auto_linked"] is False
    assert one_result["confidence"] < AUTO_LINK_THRESHOLD
    assert one_result["strategy"] == "name_dob_review"
    assert solo["intake_queue"].docs[0]["status"] == "pending"


def test_name_dob_with_county_still_auto_links_that_county():
    from dashboard.services.matching_engine import MatchingEngine

    db = FakeDB()
    db["arrests"].docs.extend([
        _arrest("LEE-1", "Lee", "FL"),
        _arrest("COL-2", "Collier", "FL"),
    ])
    intake = _name_dob_intake()
    intake["defendant"]["county"] = "Lee"
    intake["defendant"]["state"] = "FL"
    db["intake_queue"].docs.append(intake)
    result = _run(MatchingEngine(db).match_intake(intake, prior_bonds=[]))
    assert result["auto_linked"] is True
    assert result["confidence"] == 95
    assert result["strategy"] == "name_dob_county"
    assert result["best_match"]["booking_number"] == "LEE-1"
    saved = db["intake_queue"].docs[0]
    assert saved["status"] == "matched"
    assert saved["matched_booking_number"] == "LEE-1"
    assert saved["matched_county"] == "Lee"
    assert saved["matched_state"] == "FL"
    assert saved["match_strategy"] == "name_dob_county"
    assert saved["match_timestamp"]


def test_sheets_fanout_uses_persisted_post_match_intake():
    from dashboard.routers.intake import intake_submit
    from dashboard.services import intake_fanout as fo

    db = FakeDB()
    db["arrests"].docs.append(_arrest("LEE-9", "Lee", "FL"))
    captured = []

    def _schedule(doc):
        captured.append(doc)
        return None

    with patch("dashboard.routers.intake.get_collection", side_effect=db.get_collection), \
         patch("dashboard.routers.intake.get_db", return_value=db), \
         patch("dashboard.services.past_bond_search.search_past_bonds", AsyncMock(return_value=[])), \
         patch("dashboard.services.intake_fanout.schedule_after_save", side_effect=_schedule):
        result = _run(intake_submit(_Json({
            "source": "walk_in",
            "indemnitorName": "Amy Roe",
            "indemnitorPhone": "2395550101",
            "defendantName": "Bob Roe",
            "defendantDOB": "1990-01-15",
            "county": "Lee",
            "defendantState": "FL",
        })))

    assert result["success"] is True
    assert result["match"]["auto_linked"] is True
    [saved] = db["intake_queue"].docs
    assert saved["status"] == "matched"
    [fanout] = captured
    assert fanout["status"] == "matched"
    assert fanout["matched_county"] == "Lee"
    assert fanout["matched_state"] == "FL"
    assert fanout["match_strategy"] == "name_dob_county"
    assert fanout["match_timestamp"]
    assert fanout["matched_booking_number"] == "LEE-9"
    row = fo.ledger_row(fanout)
    assert row["status"] == "matched"
    assert row["county"] == "Lee"
    assert row["state"] == "FL"
    assert row["match_strategy"] == "name_dob_county"
    assert row["match_timestamp"]
    assert row["matched_booking_number"] == "LEE-9"
    assert "1990-01-15" not in str(row)


def test_promote_refuses_blank_or_zero_bond_without_taking_a_power():
    from dashboard.routers.intake import intake_promote

    amounts = ["", "0", "$0", "0.00", None]
    for amount in amounts:
        db = FakeDB()
        defendant = {"charges": "petit theft"}
        if amount is not None:
            defendant["bondAmount"] = amount
        db["intake_queue"].docs.append({
            "intake_id": "WI-ZERO",
            "status": "pending",
            "matched_booking_number": "2026-4401",
            "matched_county": "Collier",
            "defendant_name": "Bob Roe",
            "defendant": defendant,
            "indemnitor": {},
        })
        db["poa_inventory"].docs.append({
            "poa_number": "OSI-FIT",
            "surety_id": "osi",
            "status": "available",
            "max_bond_value": 10000,
        })
        with patch("dashboard.routers.intake.get_collection", side_effect=db.get_collection):
            denied = _run(intake_promote(_Json({"surety": "osi"}), "WI-ZERO"))
        body = json.loads(denied.body)
        assert denied.status_code == 422, amount
        assert "bond amount" in body["error"].lower()
        assert db["active_bonds"].docs == []
        assert db["poa_inventory"].docs[0]["status"] == "available"
        assert db["intake_queue"].docs[0]["status"] == "pending"


def test_forfeiture_releases_poa_and_opens_pending_recovery_review():
    from dashboard.services.recovery_case_service import (
        list_cases_for_recovery,
        queue_forfeiture_review,
        share_forfeiture_case,
    )
    from dashboard.services.state_machine import BondStateMachine

    db = FakeDB()
    db["active_bonds"].docs.append({
        "booking_number": "2026-4401",
        "status": "active",
        "poa_number": "OSI-FIT",
        "county": "Lee",
        "state": "FL",
        "defendant_name": "Bob Roe",
    })
    release = AsyncMock()
    with patch("dashboard.services.state_machine.get_db", return_value=db), \
         patch("dashboard.services.recovery_case_service.get_collection", side_effect=db.get_collection), \
         patch("dashboard.services.audit_service.AuditService.log_event", AsyncMock()), \
         patch("dashboard.services.poa_service.auto_release_poa", release), \
         patch("dashboard.services.task_engine.TaskEngine.cancel_pending_tasks", AsyncMock()):
        result = _run(BondStateMachine.transition_bond(
            "2026-4401", "forfeited", "Dashboard", "fta",
        ))
        again = _run(queue_forfeiture_review(booking_number="2026-4401", actor="Dashboard"))
        pending_status = db["recovery_case_shares"].docs[0]["status"]
        hidden = _run(list_cases_for_recovery("R-1"))
        confirmed = _run(share_forfeiture_case(booking_number="2026-4401", actor="Dashboard"))

    assert result["success"] is True
    assert result["poa_released"] is True
    assert result["poa_number"] == "OSI-FIT"
    assert db["active_bonds"].docs[0]["status"] == "forfeited"
    assert again["already_open"] is True
    assert pending_status == "pending_review"
    [share] = [row for row in db["recovery_case_shares"].docs]
    assert share["status"] == "active"
    assert share["confirmed_from"] == "pending_review"
    assert confirmed["confirmed_pending_review"] is True
    assert confirmed["share_id"] == share["share_id"]
    assert hidden == []
    assert share["recovery_agent_id"] == ""
    release.assert_awaited()

    cleared = FakeDB()
    cleared["active_bonds"].docs.append({
        "booking_number": "2026-4402",
        "status": "active",
        "poa_number": "OSI-FIT",
        "county": "Lee",
        "state": "FL",
    })
    with patch("dashboard.services.state_machine.get_db", return_value=cleared), \
         patch("dashboard.services.recovery_case_service.get_collection", side_effect=cleared.get_collection), \
         patch("dashboard.services.audit_service.AuditService.log_event", AsyncMock()), \
         patch("dashboard.services.poa_service.auto_release_poa", AsyncMock()), \
         patch("dashboard.services.task_engine.TaskEngine.cancel_pending_tasks", AsyncMock()):
        exo = _run(BondStateMachine.transition_bond(
            "2026-4402", "exonerated", "Dashboard", "court discharged",
        ))
    assert exo["poa_released"] is True
    assert cleared["recovery_case_shares"].docs == []
