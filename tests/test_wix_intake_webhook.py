"""POST /api/webhooks/wix-intake — nested wizard adapter, no defaults, Mongo first,
auto-match, non-blocking fan-out (owner rule 2026-09-27)."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.webhooks import webhooks_bp
from dashboard.services import wix_wizard_adapter as wiz
from tests._inmem_mongo import FakeDB

SECRET = "wix-test-secret-not-real"

DEFENDANT_WIZARD = {
    "defendant": {"firstName": "Jane", "lastName": "Doe", "alias": "", "address": "1 Palm St",
                  "city": "Naples", "state": "FL", "zip": "34102", "phone": "2395551234",
                  "email": "jane@example.com", "dob": "1990-01-02", "ssn": "123-45-6789"},
    "physical": {"height": "5'6", "weight": "140", "race": "W"},
    "employment": {"employer": "Publix", "phone": "2395550000", "address": "2 Main"},
    "family": {"married": False},
    "references": {"parent": {"name": "Mom Doe", "phone": "2395559999", "address": "3 Oak"},
                   "friend": {"name": "", "phone": "", "address": ""}},
    "legal": {}, "vehicle": {"year": "2020", "make": "Honda", "model": "Civic", "color": "Blue", "plate": "ABC123"},
    "identification": {"dl": "D123", "dlState": "FL"},
    "consents": {"truthfulness": {"granted": True}, "electronicSignature": {"granted": True}},
    "formType": "defendant_appearance_bond",
}

INDEMNITOR_WIZARD = {
    "defendant": {"firstName": "Bob", "lastName": "Roe", "county": "Collier", "dob": "",
                  "phone": "", "address": "", "bookingNumber": "2026-0001"},
    "indemnitor": {"firstName": "Amy", "middleName": "", "lastName": "Roe", "relationship": "Sister",
                   "dob": "1985-05-05", "phone": "2395550101", "email": "amy@example.com",
                   "street": "9 Bay Rd", "city": "Fort Myers", "state": "FL", "zip": "33901",
                   "dl": "R555", "dlState": "FL", "ssn": "987654321", "employer": "Lee Health",
                   "employerPhone": "2395550202", "employerAddress": "100 Health Blvd"},
    "vehicle": {}, "homeowner": {"isHomeowner": False}, "spouse": {"isMarried": False},
    "references": [{"name": "Ref One", "relation": "Friend", "phone": "2395550303", "address": "x"},
                   {"name": "Ref Two", "relation": "Cousin", "phone": "2395550404", "address": "y"}],
    "meta": {"source": "indemnitor-wizard"},
}


def test_flatten_defendant_wizard():
    flat, meta = wiz.flatten({"formType": "defendant", "payload": DEFENDANT_WIZARD, "clientNonce": "n1"})
    assert meta["role"] == "defendant" and meta["form_type"] == wiz.DEFENDANT_FORM
    assert flat["DefFirstName"] == "Jane" and flat["defendantCity"] == "Naples"
    assert flat["defendantEmployer"] == "Publix" and flat["defendantVehiclePlate"] == "ABC123"
    assert flat["defendantEmergencyName"] == "Mom Doe"
    assert flat["DefSSN"] == "123-45-6789" and flat["DefDL"] == "D123" and flat["DefDLState"] == "FL"
    assert flat["defendantStreetAddress"] == "1 Palm St"
    assert "DefCounty" not in flat and "surety_id" not in flat
    assert flat["intakeId"].startswith("WX-")
    assert meta["application"]["defendant"]["ssn"] == "***-**-6789"


def test_flatten_indemnitor_wizard_legacy_envelope():
    flat, meta = wiz.flatten({"type": "indemnitor-submit-phase1", "wizardData": INDEMNITOR_WIZARD})
    assert meta["role"] == "indemnitor"
    assert flat["IndFirstName"] == "Amy" and flat["IndAddress"] == "9 Bay Rd"
    assert flat["DefCounty"] == "Collier" and flat["defendantArrestNumber"] == "2026-0001"
    assert flat["Ref2Name"] == "Ref Two"
    assert flat["IndEmployerAddress"] == "100 Health Blvd"
    assert "intakeId" not in flat  # no nonce → server-generated id


@pytest.fixture
def env():
    db = FakeDB()
    engine = MagicMock()
    engine.match_intake = AsyncMock(return_value={"matched": False, "auto_linked": False, "confidence": 0})
    sched = MagicMock()
    with patch.dict(os.environ, {"WIX_WEBHOOK_SECRET": SECRET}), \
         patch("dashboard.routers.webhooks.get_collection", side_effect=db.get_collection), \
         patch("dashboard.routers.intake.get_collection", side_effect=db.get_collection), \
         patch("dashboard.extensions.get_db", return_value=db), \
         patch("dashboard.services.matching_engine.MatchingEngine", return_value=engine), \
         patch("dashboard.services.intake_fanout.schedule_after_save", sched):
        app = FastAPI()
        app.include_router(webhooks_bp)
        yield TestClient(app), db, engine, sched


def _post(client, body, secret=SECRET):
    return client.post("/api/webhooks/wix-intake", json=body, headers={"X-Wix-Webhook-Secret": secret})


def test_rejects_bad_secret(env):
    client, db, *_ = env
    r = _post(client, {"payload": DEFENDANT_WIZARD}, secret="nope")
    assert r.status_code == 401 and r.json()["success"] is False
    assert db["intake_queue"].docs == []


def test_defendant_wizard_saved_without_defaults(env):
    client, db, engine, sched = env
    r = _post(client, {"formType": "defendant", "role": "defendant", "clientNonce": "abc", "payload": DEFENDANT_WIZARD})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["success"] is True and body["duplicate"] is False
    assert "lnk_b6bf996f" in body["payment_link"]
    [doc] = db["intake_queue"].docs
    assert doc["intake_id"] == body["intake_id"]
    assert doc["submitted_by_role"] == "defendant" and doc["form_type"] == wiz.DEFENDANT_FORM
    assert doc["surety_id"] is None
    assert doc["defendant"]["county"] == ""      # no Lee default
    assert doc["defendant"]["state"] == "FL"     # from the form, not a default
    assert doc["defendant_name"] == "Jane Doe"
    assert doc["defendant"]["ssn"] == "123-45-6789"
    assert doc["defendant"]["dl"] == "D123" and doc["defendant"]["dlState"] == "FL"
    assert doc["defendant"]["address"] == "1 Palm St" and doc["defendant"]["street"] == "1 Palm St"
    assert doc["indemnitor"]["ssn"] == ""       # defendant SSN must not copy onto the cosigner
    assert doc["application"]["defendant"]["ssn"] == "***-**-6789"
    from dashboard.services.intake_fanout import ledger_row
    assert "123-45-6789" not in str(ledger_row(doc))
    engine.match_intake.assert_awaited_once()
    sched.assert_called_once()
    # audit copy is redacted
    [audit] = db["audit_events"].docs
    assert "123-45-6789" not in str(audit)


def test_indemnitor_wizard_no_state_default(env):
    client, db, *_ = env
    wd = {**INDEMNITOR_WIZARD, "indemnitor": {**INDEMNITOR_WIZARD["indemnitor"], "state": "", "dlState": ""}}
    r = _post(client, {"formType": "indemnitor", "payload": wd})
    assert r.status_code == 201
    [doc] = db["intake_queue"].docs
    assert doc["submitted_by_role"] == "indemnitor"
    assert doc["indemnitor"]["state"] == "" and doc["indemnitor"]["dlState"] == ""
    assert doc["defendant"]["county"] == "Collier"
    assert doc["defendant"]["ssn"] == ""
    assert doc["indemnitor"]["employerAddress"] == "100 Health Blvd"
    assert doc["indemnitor"]["ssn"] == "987654321"
    assert doc["indemnitor_name"] == "Amy Roe"


def test_idempotent_on_client_nonce(env):
    client, db, engine, sched = env
    body = {"formType": "defendant", "clientNonce": "same", "payload": DEFENDANT_WIZARD}
    r1 = _post(client, body)
    r2 = _post(client, body)
    assert r1.json()["intake_id"] == r2.json()["intake_id"]
    assert r2.json()["duplicate"] is True and r2.status_code == 200
    assert len(db["intake_queue"].docs) == 1
    assert sched.call_count == 1


def test_match_or_fanout_failure_never_fails_intake(env):
    client, db, engine, sched = env
    engine.match_intake.side_effect = RuntimeError("match down")
    sched.side_effect = RuntimeError("fanout down")
    r = _post(client, {"formType": "defendant", "payload": DEFENDANT_WIZARD})
    assert r.status_code == 201 and r.json()["success"] is True
    assert len(db["intake_queue"].docs) == 1


def test_unknown_surety_kept_for_staff_not_coerced(env):
    client, db, *_ = env
    r = _post(client, {"formType": "indemnitor", "surety": "Acme Bail", "payload": INDEMNITOR_WIZARD})
    assert r.status_code == 201
    [doc] = db["intake_queue"].docs
    assert doc["surety_id"] is None and doc["surety_unrecognized"] == "acme bail"


def test_save_failure_returns_success_false(env):
    client, db, *_ = env
    with patch("dashboard.routers.intake.get_collection", side_effect=RuntimeError("mongo down")):
        r = _post(client, {"formType": "defendant", "payload": DEFENDANT_WIZARD})
    assert r.status_code == 500 and r.json()["success"] is False
