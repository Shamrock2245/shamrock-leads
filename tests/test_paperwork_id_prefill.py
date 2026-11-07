"""Self-serve ID confirm fills the signer role and opens a packet.

Synthetic TEST- records only. Signer email is the staff test address.
No DocuSeal send, no SMS, and no SwipeSimple charge.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from dashboard.main import app
from dashboard.services.paperwork_self_serve import (
    get_pending_id_prefill,
    restrict_to_role,
    role_fields_from_scan,
)
from tests._inmem_mongo import FakeDB

client = TestClient(app)

EMAIL = "admin@shamrockbailbonds.biz"
PHONE = "2395550100"
TOKEN = "PORTAL-TEST-PREFILL"
FUTURE = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
NAME = "Test Indemnitor"
DOB = "1900-01-01"
OCR = {
    "full_name": NAME,
    "first_name": "Test",
    "last_name": "Indemnitor",
    "dob": DOB,
    "address": "10 Test Lane",
    "city": "Testville",
    "state": "FL",
    "zip": "00000",
    "dl_number": "TEST-DL-0000",
    "dl_state": "FL",
    "expiration_date": "1900-01-01",
    "sex": "X",
}
PAPERWORK = {"host": "paperwork.shamrockbailbonds.biz"}


def _bind(db):
    wrapped = set()

    def get_collection(name):
        col = db[name]
        if name not in wrapped:
            orig = col.find

            def find(flt=None, projection=None, _orig=orig):
                cursor = _orig(flt, projection)

                class _List:
                    def __init__(self, inner):
                        self._inner = inner

                    async def to_list(self, length=500):
                        return [row async for row in self._inner]

                return _List(cursor)

            col.find = find
            wrapped.add(name)
        return col

    return get_collection


def _install(monkeypatch, db, *, real_templates=False):
    getter = _bind(db)
    monkeypatch.setattr("dashboard.routers.pin_portal.get_collection", getter)
    monkeypatch.setattr("dashboard.extensions.get_collection", getter)
    monkeypatch.setattr("dashboard.services.staff_chain_service.get_collection", getter)
    monkeypatch.setenv("PAPERWORK_SELF_SERVE_PREFILL", "1")
    if real_templates:
        monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_OSI", "101")
        monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID", "101")
        monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_PALMETTO", "505")
    else:
        monkeypatch.setattr(
            "dashboard.services.docuseal_service.resolve_template_id_for_surety",
            lambda *_a, **_k: "5",
        )
    return getter


def _session(**extra):
    doc = {
        "session_token": TOKEN,
        "verified": True,
        "expires_at": FUTURE,
        "role": "indemnitor",
        "phone": PHONE,
    }
    doc.update(extra)
    return doc


def _scanner(monkeypatch, extracted=None):
    payload = dict(OCR if extracted is None else extracted)

    async def _scan(_image, filename=""):
        return {"success": True, "extracted": dict(payload), "portrait_jpeg_b64": ""}

    monkeypatch.setattr(
        "dashboard.services.id_scanner_service.IDScannerService.scan_id_image",
        _scan,
    )


def _submission(monkeypatch, calls, *, status="pending"):
    async def _create(self, **kwargs):
        calls["n"] += 1
        calls["kwargs"] = kwargs
        return {
            "id": 1,
            "submitters": [
                {"id": 8, "role": "bondsman", "slug": "TEST-BOND", "email": EMAIL, "status": "pending"},
                {
                    "id": 9,
                    "role": kwargs["submitters"][1]["role"],
                    "slug": "TEST-SLUG",
                    "email": EMAIL,
                    "status": status,
                },
            ],
        }

    async def _full(self, **kwargs):
        raise AssertionError("full packet builder")

    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.create_submission", _create)
    monkeypatch.setattr(
        "dashboard.services.docuseal_service.DocuSealService.create_submission_for_packet",
        _full,
    )

    async def _update(self, submitter_id, **kwargs):
        return {"id": submitter_id}

    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.update_submitter", _update)


def _ocr(db):
    response = client.post(
        "/api/portal/id-ocr",
        files={"file": ("id.jpg", b"test-id-photo", "image/jpeg")},
        data={"session_token": TOKEN},
    )
    return response


def _confirm(scan_id, fields, *, role="indemnitor"):
    body = {
        "session_token": TOKEN,
        "scan_id": scan_id,
        "role": role,
        "email": EMAIL,
        "staff_review_acknowledged": True,
        "address_confirmed": True,
        "fields": fields,
    }
    return client.post("/api/portal/remaining-fields", json=body)


def _indemnitor_fields(**overrides):
    fields = role_fields_from_scan(OCR, "indemnitor")
    fields.update(overrides)
    return fields


@pytest.mark.parametrize("role", ["indemnitor", "coindemnitor", "defendant"])
def test_each_ocr_field_maps_only_to_that_role(role):
    fields = role_fields_from_scan(OCR, role)
    prefix = {"defendant": "defendant_", "coindemnitor": "coindemnitor_"}[role] if role != "indemnitor" else "indemnitor_"
    assert fields[prefix + "name"] == NAME
    assert fields[prefix + "dob"] == DOB
    assert fields[prefix + "address"] == "10 Test Lane"
    assert fields[prefix + "city"] == "Testville"
    assert fields[prefix + "state"] == "FL"
    assert fields[prefix + "zip"] == "00000"
    assert fields[prefix + "dl"] == "TEST-DL-0000"
    assert fields[prefix + "dl_state"] == "FL"
    assert fields[prefix + "dl_expiration"] == "1900-01-01"
    assert fields[prefix + "sex"] == "X"
    others = {"indemnitor", "coindemnitor", "defendant"} - {role}
    for other in others:
        assert not any(key.startswith(other + "_") or key.startswith(other) for key in fields if key.startswith(other))


def test_confirm_edit_does_not_cross_roles():
    edited = restrict_to_role(
        {
            "indemnitor_address": "20 Test Lane",
            "defendant_name": "Test Defendant",
            "defendant_dl": "TEST-DL-9999",
            "coindemnitor_name": "Test Cosigner",
        },
        "indemnitor",
    )
    assert edited["indemnitor_address"] == "20 Test Lane"
    assert "defendant_name" not in edited
    assert "defendant_dl" not in edited
    assert "coindemnitor_name" not in edited


@pytest.mark.asyncio
async def test_flag_off_keeps_the_old_copy_and_does_not_open_a_case(monkeypatch):
    monkeypatch.delenv("PAPERWORK_SELF_SERVE_PREFILL", raising=False)
    html = client.get("/api/portal/portal-ui?mode=inperson", headers=PAPERWORK).text
    assert "const SELF_SERVE_PREFILL = false;" in html
    assert "A Shamrock agent will text you when your paperwork is ready" in html
    db = FakeDB()
    monkeypatch.setattr("dashboard.routers.pin_portal.get_collection", db.get_collection)
    from dashboard.routers.pin_portal import _resolve_packet_for_client

    message = (await _resolve_packet_for_client(PHONE))["message"]
    assert "No e-sign packet is on file for this phone yet" in message
    assert db["bond_cases"].docs == []


@pytest.mark.asyncio
async def test_flag_on_replaces_the_dead_end_copy(monkeypatch):
    monkeypatch.setenv("PAPERWORK_SELF_SERVE_PREFILL", "1")
    html = client.get("/api/portal/portal-ui?mode=inperson", headers=PAPERWORK).text
    assert "const SELF_SERVE_PREFILL = true;" in html
    assert "A signing link is not available yet" in html
    db = FakeDB()
    monkeypatch.setattr("dashboard.routers.pin_portal.get_collection", db.get_collection)
    from dashboard.routers.pin_portal import _resolve_packet_for_client

    message = (await _resolve_packet_for_client(PHONE))["message"]
    assert "No e-sign packet is on file" not in message
    assert "Scan your ID" in message


def test_invalid_token_is_refused_before_ocr(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session(verified=False))
    _install(monkeypatch, db)
    calls = {"n": 0}

    async def _scan(*_a, **_k):
        calls["n"] += 1
        return {"success": True, "extracted": dict(OCR)}

    monkeypatch.setattr("dashboard.services.id_scanner_service.IDScannerService.scan_id_image", _scan)
    response = client.post(
        "/api/portal/id-ocr",
        files={"file": ("id.jpg", b"test-id-photo", "image/jpeg")},
        data={"session_token": TOKEN},
    )
    assert response.status_code == 401
    assert calls["n"] == 0
    assert "id_scan_records" not in db["portal_pins"].docs[0]


def test_json_without_an_image_is_not_a_scan(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    _install(monkeypatch, db)
    calls = {"n": 0}

    async def _scan(*_a, **_k):
        calls["n"] += 1
        return {"success": True, "extracted": dict(OCR)}

    monkeypatch.setattr("dashboard.services.id_scanner_service.IDScannerService.scan_id_image", _scan)
    response = client.post(
        "/api/portal/id-ocr",
        json={"session_token": TOKEN, "full_name": "Not From The Server", "dl_number": "TEST-DL-9999"},
    )
    assert response.status_code == 400
    assert calls["n"] == 0


def test_confirm_without_a_scan_does_not_call_docuseal(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    _install(monkeypatch, db)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    response = _confirm("SCAN-NOT-ON-FILE", _indemnitor_fields())
    assert response.status_code == 400
    assert response.json()["signing_link"] == ""
    assert calls["n"] == 0
    assert db["bond_cases"].docs == []
    assert "swipesimple" not in response.text.lower()


def test_dedupe_hit_reuses_the_case_and_keeps_amounts(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-1",
        "indemnitor_id": "IND-TEST-1",
        "indemnitor_phone": PHONE,
        "indemnitor_name": NAME,
        "indemnitor_dob": DOB,
        "poa_number": "TEST-POA-KEEP",
        "bond_amount": 5000,
        "premium": 500,
        "surety_id": "palmetto",
        "status": "active",
        "source": "staff",
    })
    db["indemnitors"].docs.append({
        "indemnitor_id": "IND-TEST-1",
        "name": NAME,
        "phone": PHONE,
        "dob": DOB,
    })
    _install(monkeypatch, db)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    uploaded = _ocr(db)
    assert uploaded.status_code == 200, uploaded.text
    scan_id = uploaded.json()["scan_id"]
    fields = _indemnitor_fields(indemnitor_address="20 Test Lane")
    saved = _confirm(scan_id, fields)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["signing_link"].endswith("/s/TEST-SLUG")
    assert body["reused_case"] is True
    assert body["created_case"] is False
    assert body["bond_case_id"] == "BC-TEST-1"
    assert len(db["bond_cases"].docs) == 1
    case = db["bond_cases"].docs[0]
    assert case["poa_number"] == "TEST-POA-KEEP"
    assert case["bond_amount"] == 5000
    assert case["premium"] == 500
    assert case["surety_id"] == "palmetto"
    assert case["status"] == "active"
    assert db["paperwork_packets"].docs[0]["surety_id"] == "palmetto"
    assert case["indemnitor_address"] == "20 Test Lane"
    assert "defendant_name" not in case
    assert calls["kwargs"]["send_email"] is False
    assert calls["kwargs"]["send_sms"] is False
    assert "swipesimple" not in saved.text.lower()


def test_dedupe_miss_creates_a_draft_and_an_audit(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    _install(monkeypatch, db)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    scan_id = _ocr(db).json()["scan_id"]
    saved = _confirm(scan_id, _indemnitor_fields())
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["created_case"] is True
    assert body["signing_link"] == ""
    assert body["fallback"] is True
    assert len(db["bond_cases"].docs) == 1
    assert db["paperwork_packets"].docs == []
    case = db["bond_cases"].docs[0]
    assert case["source"] == "self_serve"
    assert case["status"] == "pending_owner_review"
    assert case["created_by"] == "auto"
    assert not case.get("surety_id")
    assert not case.get("bond_amount")
    assert not case.get("premium")
    assert not case.get("poa_number")
    audits = [row for row in db["audit_events"].docs if row.get("event_type") == "self_serve_draft_bondcase"]
    assert len(audits) == 1
    assert audits[0]["actor"] == "auto"
    assert NAME not in str(audits[0])
    assert "TEST-DL-0000" not in str(audits[0])
    assert calls["n"] == 0
    pending = case["pending_id_prefills"][-1]["fields"]
    assert pending["indemnitor_name"] == NAME
    assert pending["indemnitor_dl"] == "TEST-DL-0000"
    assert "swipesimple" not in saved.text.lower()


def test_double_submit_creates_one_case_and_one_packet(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    _install(monkeypatch, db)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    scan_id = _ocr(db).json()["scan_id"]
    fields = _indemnitor_fields()
    first = _confirm(scan_id, fields)
    second = _confirm(scan_id, fields)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["signing_link"] == ""
    assert second.json()["signing_link"] == ""
    assert first.json()["bond_case_id"] == second.json()["bond_case_id"]
    assert len(db["bond_cases"].docs) == 1
    assert db["paperwork_packets"].docs == []
    assert calls["n"] == 0
    assert len(db["portal_pins"].docs[0]["id_confirmed_records"]) == 2
    stored = db["portal_pins"].docs[0]["id_scan_records"][0]["extracted"]
    assert stored == {"full_name": NAME, "dl_state": "FL"}
    assert "dl_number" not in stored


def test_edit_is_a_new_record_and_the_scan_stays(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    _install(monkeypatch, db)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    scan_id = _ocr(db).json()["scan_id"]
    original = copy.deepcopy(db["portal_pins"].docs[0]["id_ocr_snapshots"][0])
    saved = _confirm(scan_id, _indemnitor_fields(indemnitor_address="20 Test Lane"))
    assert saved.status_code == 200, saved.text
    session = db["portal_pins"].docs[0]
    assert session["id_ocr_snapshots"][0] == original
    assert session["id_ocr_snapshots"][0]["extracted"]["address"] == "10 Test Lane"
    confirmed = session["id_confirmed_records"][0]
    assert confirmed["scan_id"] == scan_id
    assert confirmed["fields"]["indemnitor_address"] == "20 Test Lane"
    assert confirmed["ocr_fields"]["address"] == "10 Test Lane"
    assert "defendant_name" not in confirmed["fields"]


def test_existing_packet_is_updated_and_not_duplicated(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-1",
        "indemnitor_id": "IND-TEST-1",
        "indemnitor_phone": PHONE,
        "indemnitor_name": NAME,
        "indemnitor_dob": DOB,
        "status": "active",
        "source": "staff",
        "client_fields": {"defendant_name": "Test Defendant"},
    })
    db["paperwork_packets"].docs.append({
        "packet_id": "PKT-TEST-EXISTING",
        "bond_case_id": "BC-TEST-1",
        "voided": False,
        "status": "pending_signature",
        "sign_url": "https://sign.shamrockbailbonds.biz/s/TEST-EXISTING",
        "client_fields": {"defendant_name": "Test Defendant"},
        "docuseal_submitters": [{
            "id": 44,
            "role": "indemnitor",
            "status": "pending",
            "sign_url": "https://sign.shamrockbailbonds.biz/s/TEST-EXISTING",
            "email": EMAIL,
        }],
    })
    _install(monkeypatch, db)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    updates = {"n": 0, "values": {}}

    async def _update(self, submitter_id, **kwargs):
        updates["n"] += 1
        updates["id"] = submitter_id
        updates["values"] = kwargs.get("values") or {}
        assert kwargs.get("send_email") is False
        assert kwargs.get("send_sms") is False
        return {"id": submitter_id}

    _submission(monkeypatch, calls)
    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.update_submitter", _update)
    scan_id = _ocr(db).json()["scan_id"]
    saved = _confirm(scan_id, _indemnitor_fields())
    assert saved.status_code == 200, saved.text
    assert saved.json()["signing_link"].endswith("/s/TEST-EXISTING")
    assert saved.json()["packet_id"] == "PKT-TEST-EXISTING"
    assert len(db["paperwork_packets"].docs) == 1
    assert calls["n"] == 0
    assert updates["n"] == 1
    assert updates["id"] == 44
    assert updates["values"]["indemnitor_dl"] == "TEST-DL-0000"
    assert db["paperwork_packets"].docs[0]["client_fields"]["defendant_name"] == "Test Defendant"
    assert db["paperwork_packets"].docs[0]["client_fields"]["indemnitor_name"] == NAME


@pytest.mark.asyncio
async def test_builder_failure_keeps_prefill_and_returns_no_url(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    _install(monkeypatch, db)
    _scanner(monkeypatch)

    async def _boom(self, **_kwargs):
        raise RuntimeError("builder down")

    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.create_submission", _boom)
    scan_id = _ocr(db).json()["scan_id"]
    saved = _confirm(scan_id, _indemnitor_fields())
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["signing_link"] == ""
    assert body["fallback"] is True
    assert "signing link is not available" in body["message"].lower()
    assert "agent will text you" not in body["message"].lower()
    assert db["paperwork_packets"].docs == []
    case_id = body["bond_case_id"]
    person_id = body["person_id"]
    case = db["bond_cases"].docs[0]
    case["poa_number"] = "TEST-POA-LATER"
    pending = await get_pending_id_prefill(case_id, "indemnitor", person_id)
    assert pending["fields"]["indemnitor_name"] == NAME
    assert pending["fields"]["indemnitor_dl"] == "TEST-DL-0000"
    assert "poa" not in pending["fields"]
    assert pending["case_id"] == case_id


def test_voided_packet_url_is_not_returned(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-1",
        "indemnitor_id": "IND-TEST-1",
        "indemnitor_phone": PHONE,
        "indemnitor_name": NAME,
        "indemnitor_dob": DOB,
        "surety_id": "palmetto",
        "status": "active",
        "source": "staff",
    })
    voided = {
        "packet_id": "PKT-TEST-VOID",
        "bond_case_id": "BC-TEST-1",
        "voided": True,
        "status": "voided",
        "sign_url": "https://sign.shamrockbailbonds.biz/s/TEST-VOID",
        "docuseal_submitters": [{
            "id": 3,
            "role": "indemnitor",
            "status": "voided",
            "sign_url": "https://sign.shamrockbailbonds.biz/s/TEST-VOID",
        }],
    }
    db["paperwork_packets"].docs.append(voided)
    _install(monkeypatch, db)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    saved = _confirm(_ocr(db).json()["scan_id"], _indemnitor_fields())
    assert saved.status_code == 200, saved.text
    assert "TEST-VOID" not in saved.json()["signing_link"]
    assert db["paperwork_packets"].docs[0]["sign_url"].endswith("/s/TEST-VOID")
    assert db["paperwork_packets"].docs[0]["voided"] is True


def test_unverified_signer_gets_no_url(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-1",
        "indemnitor_id": "IND-TEST-1",
        "indemnitor_phone": PHONE,
        "indemnitor_name": NAME,
        "indemnitor_dob": DOB,
        "surety_id": "palmetto",
        "status": "active",
        "source": "staff",
    })
    _install(monkeypatch, db)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls, status="unverified")
    saved = _confirm(_ocr(db).json()["scan_id"], _indemnitor_fields())
    assert saved.status_code == 200, saved.text
    assert saved.json()["signing_link"] == ""
    assert saved.json()["fallback"] is True
    assert calls["n"] == 1
    assert len(db["paperwork_packets"].docs) == 1
    shell = db["paperwork_packets"].docs[0]
    assert shell["status"] == "build_failed"
    assert shell["sign_url"] == ""
    assert shell["docuseal_started"] is True


@pytest.mark.asyncio
async def test_poa_change_does_not_drop_pending_prefill(monkeypatch):
    db = FakeDB()
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-1",
        "poa_number": "TEST-POA-1",
        "pending_id_prefills": [{
            "case_id": "BC-TEST-1",
            "role": "indemnitor",
            "person_id": "IND-TEST-1",
            "scan_id": "SCAN-1",
            "confirm_id": "CONFIRM-1",
            "fields": {"indemnitor_name": NAME, "indemnitor_dl": "TEST-DL-0000"},
        }],
    })
    _install(monkeypatch, db)
    db["bond_cases"].docs[0]["poa_number"] = "TEST-POA-2"
    pending = await get_pending_id_prefill("BC-TEST-1", "indemnitor", "IND-TEST-1")
    assert pending["fields"]["indemnitor_dl"] == "TEST-DL-0000"
    assert db["bond_cases"].docs[0]["poa_number"] == "TEST-POA-2"
    assert len(db["bond_cases"].docs[0]["pending_id_prefills"]) == 1


def test_draft_packet_stays_pending_review_and_records_the_url(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-1",
        "indemnitor_id": "IND-TEST-1",
        "indemnitor_phone": PHONE,
        "indemnitor_name": NAME,
        "indemnitor_dob": DOB,
        "surety_id": "palmetto",
        "match_status": "pending_owner_review",
        "status": "pending_owner_review",
        "source": "self_serve",
    })
    db["audit_events"].docs.append({"event_id": "TEST-AUDIT-KEEP", "event_type": "earlier_review"})
    _install(monkeypatch, db)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    saved = _confirm(_ocr(db).json()["scan_id"], _indemnitor_fields())
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["signing_link"].endswith("/s/TEST-SLUG")
    assert "swipesimple" not in saved.text.lower()
    assert "payment_link" not in body
    packet = db["paperwork_packets"].docs[0]
    assert packet["status"] == "pending_owner_review"
    assert packet["self_serve_unvalidated_draft"] is True
    assert packet["send_email"] is False
    assert packet["send_sms"] is False
    assert packet["surety_id"] == "palmetto"
    issued = [row for row in db["audit_events"].docs if row.get("event_type") == "self_serve_draft_signing_url"]
    assert len(issued) == 1
    assert issued[0]["actor"] == "auto"
    assert issued[0]["reason"] == "signing_url_issued_for_unvalidated_draft"
    assert issued[0]["new_state"]["signing_url_issued"] is True
    assert "sign_url" not in issued[0]
    assert db["audit_events"].docs[0]["event_id"] == "TEST-AUDIT-KEEP"


def test_exact_packet_key_wins_over_a_later_role_match(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-1",
        "indemnitor_id": "IND-TEST-1",
        "indemnitor_phone": PHONE,
        "indemnitor_name": NAME,
        "indemnitor_dob": DOB,
        "status": "active",
        "source": "staff",
    })
    exact = {
        "packet_id": "PKT-TEST-EXACT",
        "bond_case_id": "BC-TEST-1",
        "self_serve_packet_key": "BC-TEST-1|indemnitor|IND-TEST-1",
        "person_id": "IND-TEST-1",
        "voided": False,
        "status": "pending_signature",
        "docuseal_submitters": [{
            "id": 11,
            "role": "indemnitor",
            "status": "pending",
            "email": EMAIL,
            "phone": "+1" + PHONE,
            "sign_url": "https://sign.shamrockbailbonds.biz/s/TEST-EXACT",
        }],
    }
    other = {
        "packet_id": "PKT-TEST-OTHER",
        "bond_case_id": "BC-TEST-1",
        "person_id": "IND-TEST-OTHER",
        "voided": False,
        "status": "pending_signature",
        "docuseal_submitters": [{
            "id": 22,
            "role": "indemnitor",
            "status": "pending",
            "email": "other@shamrockbailbonds.biz",
            "sign_url": "https://sign.shamrockbailbonds.biz/s/TEST-OTHER",
        }],
    }
    db["paperwork_packets"].docs.extend([exact, other])
    _install(monkeypatch, db)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    updates = {"ids": []}

    async def _update(self, submitter_id, **kwargs):
        updates["ids"].append(submitter_id)
        assert kwargs.get("send_email") is False
        return {"id": submitter_id}

    _submission(monkeypatch, calls)
    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.update_submitter", _update)
    saved = _confirm(_ocr(db).json()["scan_id"], _indemnitor_fields())
    assert saved.status_code == 200, saved.text
    assert saved.json()["signing_link"].endswith("/s/TEST-EXACT")
    assert "TEST-OTHER" not in saved.json()["signing_link"]
    assert updates["ids"] == [11]
    assert calls["n"] == 0
    assert db["paperwork_packets"].docs[1]["docuseal_submitters"][0]["id"] == 22


def test_unbound_packet_does_not_return_another_signers_url(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-1",
        "indemnitor_id": "IND-TEST-1",
        "indemnitor_phone": PHONE,
        "indemnitor_name": NAME,
        "indemnitor_dob": DOB,
        "surety_id": "palmetto",
        "status": "active",
        "source": "staff",
    })
    stale = {
        "packet_id": "PKT-TEST-STALE",
        "bond_case_id": "BC-TEST-1",
        "person_id": "IND-TEST-STALE",
        "voided": False,
        "status": "pending_signature",
        "sign_url": "https://sign.shamrockbailbonds.biz/s/TEST-STALE",
        "docuseal_submitters": [{
            "id": 77,
            "role": "indemnitor",
            "status": "pending",
            "email": "other@shamrockbailbonds.biz",
            "phone": "+12395550199",
            "sign_url": "https://sign.shamrockbailbonds.biz/s/TEST-STALE",
        }],
    }
    before = dict(stale)
    db["paperwork_packets"].docs.append(stale)
    _install(monkeypatch, db)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    saved = _confirm(_ocr(db).json()["scan_id"], _indemnitor_fields())
    assert saved.status_code == 200, saved.text
    assert "TEST-STALE" not in saved.json()["signing_link"]
    assert saved.json()["packet_id"] != "PKT-TEST-STALE"
    assert db["paperwork_packets"].docs[0]["sign_url"] == before["sign_url"]
    assert db["paperwork_packets"].docs[0]["person_id"] == "IND-TEST-STALE"
    assert calls["n"] == 1


def test_reused_case_writes_an_identity_audit(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-1",
        "indemnitor_id": "IND-TEST-1",
        "indemnitor_phone": PHONE,
        "indemnitor_name": NAME,
        "indemnitor_dob": DOB,
        "indemnitor_address": "1 Old Lane",
        "status": "active",
        "source": "staff",
    })
    db["indemnitors"].docs.append({
        "indemnitor_id": "IND-TEST-1",
        "name": NAME,
        "phone": PHONE,
        "dob": DOB,
        "address": "1 Old Lane",
    })
    db["audit_events"].docs.append({"event_id": "TEST-AUDIT-KEEP", "event_type": "earlier_review", "reason": "kept"})
    _install(monkeypatch, db)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    saved = _confirm(_ocr(db).json()["scan_id"], _indemnitor_fields(indemnitor_address="20 Test Lane"))
    assert saved.status_code == 200, saved.text
    audits = [row for row in db["audit_events"].docs if row.get("event_type") == "self_serve_identity_updated"]
    assert len(audits) == 1
    assert audits[0]["actor"] == "auto"
    assert audits[0]["reason"] == "self_serve_identity_confirmed"
    assert audits[0]["old_state"]["indemnitor_address"] == "1 Old Lane"
    assert audits[0]["new_state"]["indemnitor_address"] == "20 Test Lane"
    assert db["audit_events"].docs[0]["event_id"] == "TEST-AUDIT-KEEP"
    assert len([row for row in db["audit_events"].docs if row.get("event_id") == "TEST-AUDIT-KEEP"]) == 1


@pytest.mark.asyncio
async def test_same_person_in_another_role_opens_a_new_draft(monkeypatch):
    from dashboard.services.staff_chain_service import ensure_self_serve_bondcase

    db = FakeDB()
    _install(monkeypatch, db)
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-DEF",
        "defendant_id": "DEF-TEST-1",
        "defendant_phone": PHONE,
        "defendant_name": NAME,
        "defendant_dob": DOB,
        "status": "active",
        "source": "staff",
    })
    db["defendants"].docs.append({
        "defendant_id": "DEF-TEST-1",
        "name": NAME,
        "phone": PHONE,
        "dob": DOB,
        "bond_case_id": "BC-TEST-DEF",
    })
    result = await ensure_self_serve_bondcase(
        phone=PHONE,
        name=NAME,
        dob=DOB,
        role="indemnitor",
        email=EMAIL,
        identity=role_fields_from_scan(OCR, "indemnitor"),
        scan_blob={"scan_id": "SCAN-TEST-1", "full_name": NAME, "dl_state": "FL"},
        actor="auto",
    )
    assert result["success"] is True
    assert result["created"] is True
    assert result["bond_case_id"] != "BC-TEST-DEF"
    original = next(row for row in db["bond_cases"].docs if row["bond_case_id"] == "BC-TEST-DEF")
    assert "indemnitor_name" not in original
    assert original["defendant_name"] == NAME
    assert len(db["bond_cases"].docs) == 2


@pytest.mark.asyncio
async def test_defendant_booking_does_not_override_primary_contact(monkeypatch):
    from dashboard.services.staff_chain_service import ensure_self_serve_bondcase

    db = FakeDB()
    _install(monkeypatch, db)
    seen = {}

    async def _ensure(**kwargs):
        seen.update(kwargs)
        return {"success": False, "error": "arrest_missing", "status_code": 404}

    monkeypatch.setattr("dashboard.services.staff_chain_service.ensure_match_bondcase", _ensure)
    result = await ensure_self_serve_bondcase(
        phone=PHONE,
        name=NAME,
        dob=DOB,
        role="defendant",
        email=EMAIL,
        identity=role_fields_from_scan(OCR, "defendant"),
        scan_blob={"scan_id": "SCAN-TEST-DEF", "full_name": NAME, "dl_state": "FL"},
        booking_number="TEST-BOOK-1",
        actor="auto",
    )
    assert result["success"] is True
    assert "indemnitor_email" not in seen
    assert "indemnitor_phone" not in seen
    assert "indemnitor_name" not in seen
    assert seen["booking_number"] == "TEST-BOOK-1"
    case = db["bond_cases"].docs[0]
    assert case.get("indemnitor_email", "") in ("", None)
    assert case.get("indemnitor_phone", "") in ("", None)


def test_signed_packet_is_replaced_not_edited(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-1",
        "indemnitor_id": "IND-TEST-1",
        "indemnitor_phone": PHONE,
        "indemnitor_name": NAME,
        "indemnitor_dob": DOB,
        "surety_id": "palmetto",
        "status": "active",
        "source": "staff",
    })
    signed = {
        "packet_id": "PKT-TEST-SIGNED",
        "bond_case_id": "BC-TEST-1",
        "self_serve_packet_key": "BC-TEST-1|indemnitor|IND-TEST-1",
        "person_id": "IND-TEST-1",
        "voided": False,
        "status": "signed",
        "docuseal_status": "completed",
        "sign_url": "https://sign.shamrockbailbonds.biz/s/TEST-SIGNED",
        "client_fields": {"indemnitor_name": "Keep Signed"},
        "docuseal_submitters": [{
            "id": 90,
            "role": "indemnitor",
            "status": "completed",
            "email": EMAIL,
            "sign_url": "https://sign.shamrockbailbonds.biz/s/TEST-SIGNED",
        }],
    }
    db["paperwork_packets"].docs.append(signed)
    _install(monkeypatch, db)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    updates = {"n": 0}

    async def _update(self, submitter_id, **kwargs):
        updates["n"] += 1
        return {"id": submitter_id}

    _submission(monkeypatch, calls)
    monkeypatch.setattr("dashboard.services.docuseal_service.DocuSealService.update_submitter", _update)
    saved = _confirm(_ocr(db).json()["scan_id"], _indemnitor_fields())
    assert saved.status_code == 200, saved.text
    assert "TEST-SIGNED" not in saved.json()["signing_link"]
    assert saved.json()["packet_id"] != "PKT-TEST-SIGNED"
    assert updates["n"] == 0
    assert db["paperwork_packets"].docs[0]["status"] == "signed"
    assert db["paperwork_packets"].docs[0]["client_fields"]["indemnitor_name"] == "Keep Signed"
    assert db["paperwork_packets"].docs[0]["docuseal_status"] == "completed"
    replaced = [row for row in db["audit_events"].docs if row.get("event_type") == "self_serve_packet_replacement"]
    assert len(replaced) == 1
    assert replaced[0]["actor"] == "auto"
    assert replaced[0]["reason"] == "prior_packet_finalized"
    assert replaced[0]["old_state"]["packet_id"] == ["PKT-TEST-SIGNED"]
    assert "swipesimple" not in saved.text.lower()


@pytest.mark.asyncio
async def test_parallel_confirms_create_one_case_and_one_packet(monkeypatch):
    """Same-process FakeDB only. Two processes are covered separately."""
    import asyncio
    from dashboard.services.paperwork_self_serve import accept_confirmed_id

    db = FakeDB()
    scan_id = "SCAN-TEST-PARALLEL"
    session = _session()
    session["id_scan_records"] = [{
        "scan_id": scan_id,
        "role": "indemnitor",
        "success": True,
        "extracted": {"full_name": NAME, "dl_state": "FL"},
    }]
    session["id_ocr_snapshots"] = [{
        "scan_id": scan_id,
        "role": "indemnitor",
        "extracted": dict(OCR),
    }]
    db["portal_pins"].docs.append(session)
    _install(monkeypatch, db)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)

    async def _once():
        return await accept_confirmed_id(
            session=session,
            scan_id=scan_id,
            role="indemnitor",
            edits=_indemnitor_fields(),
            email=EMAIL,
            phone=PHONE,
        )

    first, second = await asyncio.gather(_once(), _once())
    assert first["bond_case_id"] == second["bond_case_id"]
    assert first["signing_link"] == ""
    assert second["signing_link"] == ""
    assert len(db["bond_cases"].docs) == 1
    assert db["paperwork_packets"].docs == []
    assert calls["n"] == 0
    assert len([row for row in db["audit_events"].docs if row.get("event_type") == "self_serve_draft_bondcase"]) == 1


def test_bound_path_requires_bond_and_premium_separately():
    from dashboard.services.paperwork_self_serve import _case_fully_bound

    base = {
        "bond_case_id": "BC-TEST-1",
        "match_id": "MATCH-TEST-1",
        "match_status": "validated",
        "defendant_id": "DEF-TEST-1",
        "indemnitor_id": "IND-TEST-1",
        "case_number": "TEST-CASE-1",
        "poa_number": "TEST-POA-1",
        "booking_number": "TEST-BOOK-1",
        "surety_id": "palmetto",
    }
    assert _case_fully_bound(base) is False
    assert _case_fully_bound({**base, "premium": 500}) is False
    assert _case_fully_bound({**base, "bond_amount": 5000}) is False
    assert _case_fully_bound({**base, "bond_amount": 5000, "premium": 500}) is True


@pytest.mark.asyncio
async def test_different_phone_does_not_overwrite_the_role(monkeypatch):
    from dashboard.services.staff_chain_service import ensure_self_serve_bondcase

    db = FakeDB()
    _install(monkeypatch, db)
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-DEF",
        "defendant_id": "DEF-TEST-1",
        "defendant_phone": "2395550199",
        "defendant_name": NAME,
        "defendant_dob": DOB,
        "booking_number": "TEST-BOOK-1",
        "status": "active",
        "source": "staff",
    })

    async def _ensure(**kwargs):
        return {"success": True, "bond_case_id": "BC-TEST-DEF"}

    monkeypatch.setattr("dashboard.services.staff_chain_service.ensure_match_bondcase", _ensure)
    before = dict(db["bond_cases"].docs[0])
    result = await ensure_self_serve_bondcase(
        phone=PHONE,
        name=NAME,
        dob=DOB,
        role="defendant",
        email=EMAIL,
        identity=role_fields_from_scan(OCR, "defendant"),
        scan_blob={"scan_id": "SCAN-TEST-OTHER", "full_name": NAME, "dl_state": "FL"},
        booking_number="TEST-BOOK-1",
        actor="auto",
    )
    assert result["success"] is False
    assert result["error"] == "role_phone_mismatch"
    assert db["bond_cases"].docs[0]["defendant_phone"] == before["defendant_phone"]
    assert db["bond_cases"].docs[0]["defendant_name"] == NAME
    assert "defendant_dl" not in db["bond_cases"].docs[0]
    assert len(db["bond_cases"].docs) == 1


def test_failed_scan_stays_and_a_later_success_can_verify(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_session())
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-1",
        "indemnitor_id": "IND-TEST-1",
        "indemnitor_phone": PHONE,
        "indemnitor_name": NAME,
        "indemnitor_dob": DOB,
        "surety_id": "palmetto",
        "status": "active",
        "source": "staff",
    })
    db["indemnitors"].docs.append({
        "indemnitor_id": "IND-TEST-1",
        "bond_case_id": "BC-TEST-1",
        "name": NAME,
        "phone": PHONE,
        "dob": DOB,
        "id_scan": {"success": False, "full_name": NAME, "dl_state": "FL", "role": "indemnitor"},
    })
    _install(monkeypatch, db)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    saved = _confirm(_ocr(db).json()["scan_id"], _indemnitor_fields())
    assert saved.status_code == 200, saved.text
    assert saved.json()["signing_link"].endswith("/s/TEST-SLUG")
    person = db["indemnitors"].docs[0]
    assert person["id_scan"]["success"] is False
    assert person["id_scan_records"][-1]["success"] is True
    assert person["id_scan_records"][-1]["extracted"] == {"full_name": NAME, "dl_state": "FL"}
    assert calls["n"] == 1
    assert "swipesimple" not in saved.text.lower()


@pytest.mark.asyncio
async def test_retry_after_a_partial_draft_keeps_one_case(monkeypatch):
    db = FakeDB()
    _install(monkeypatch, db)
    from dashboard.services.staff_chain_service import ensure_self_serve_bondcase

    kwargs = dict(
        phone=PHONE,
        name=NAME,
        dob=DOB,
        role="indemnitor",
        email=EMAIL,
        identity=_indemnitor_fields(),
        scan_blob={"scan_id": "SCAN-TEST-RETRY", "full_name": NAME, "dl_state": "FL"},
        actor="auto",
    )
    first = await ensure_self_serve_bondcase(**kwargs)
    assert first["success"] is True
    assert db["bond_cases"].docs[0]["_id"] == first["bond_case_id"]
    assert not db["bond_cases"].docs[0].get("surety_id")
    db["matches"].docs.clear()
    second = await ensure_self_serve_bondcase(**kwargs)
    assert second["bond_case_id"] == first["bond_case_id"]
    assert len(db["bond_cases"].docs) == 1
    assert len(db["matches"].docs) == 1
    drafts = [row for row in db["audit_events"].docs if row.get("event_type") == "self_serve_draft_bondcase"]
    assert len(drafts) == 1
    assert NAME not in str(drafts[0])


@pytest.mark.asyncio
async def test_started_packet_claim_is_not_submitted_again(monkeypatch):
    from dashboard.services.paperwork_self_serve import build_self_serve_packet, self_serve_packet_id

    db = FakeDB()
    _install(monkeypatch, db)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    packet_id = self_serve_packet_id("BC-TEST-1", "indemnitor", "IND-TEST-1", [])
    db["paperwork_packets"].docs.append({
        "_id": packet_id,
        "packet_id": packet_id,
        "bond_case_id": "BC-TEST-1",
        "self_serve_packet_key": "BC-TEST-1|indemnitor|IND-TEST-1",
        "person_id": "IND-TEST-1",
        "role": "indemnitor",
        "status": "building",
        "docuseal_started": True,
        "sign_url": "",
        "source": "self_serve",
    })

    async def _ok(**kwargs):
        return {"ok": True}

    async def _list(self, **kwargs):
        return {"data": []}

    monkeypatch.setattr(
        "dashboard.services.identity_verification_service.require_verified_indemnitors",
        _ok,
    )
    monkeypatch.setattr(
        "dashboard.services.docuseal_service.DocuSealService.list_submitters",
        _list,
    )
    with pytest.raises(RuntimeError, match="recovery_required"):
        await build_self_serve_packet(
            case={"bond_case_id": "BC-TEST-1", "indemnitor_id": "IND-TEST-1", "surety_id": "palmetto"},
            role="indemnitor",
            person_id="IND-TEST-1",
            fields=_indemnitor_fields(),
            email=EMAIL,
            phone=PHONE,
            scan_id="SCAN-TEST-1",
        )
    assert calls["n"] == 0
    assert len(db["paperwork_packets"].docs) == 1
    assert db["paperwork_packets"].docs[0]["sign_url"] == ""


@pytest.mark.asyncio
async def test_unstarted_packet_shell_is_finished_once(monkeypatch):
    from dashboard.services.paperwork_self_serve import build_self_serve_packet, self_serve_packet_id

    db = FakeDB()
    _install(monkeypatch, db)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    packet_id = self_serve_packet_id("BC-TEST-1", "indemnitor", "IND-TEST-1", [])
    db["paperwork_packets"].docs.append({
        "_id": packet_id,
        "packet_id": packet_id,
        "bond_case_id": "BC-TEST-1",
        "self_serve_packet_key": "BC-TEST-1|indemnitor|IND-TEST-1",
        "person_id": "IND-TEST-1",
        "role": "indemnitor",
        "status": "building",
        "saga_owned": True,
        "docuseal_started": False,
        "sign_url": "",
        "source": "self_serve",
        "voided": False,
    })

    async def _ok(**kwargs):
        return {"ok": True}

    monkeypatch.setattr(
        "dashboard.services.identity_verification_service.require_verified_indemnitors",
        _ok,
    )
    built = await build_self_serve_packet(
        case={"bond_case_id": "BC-TEST-1", "indemnitor_id": "IND-TEST-1", "surety_id": "palmetto"},
        role="indemnitor",
        person_id="IND-TEST-1",
        fields=_indemnitor_fields(),
        email=EMAIL,
        phone=PHONE,
        scan_id="SCAN-TEST-1",
    )
    assert built["signing_link"].endswith("/s/TEST-SLUG")
    assert calls["n"] == 1
    assert len(db["paperwork_packets"].docs) == 1
    assert db["paperwork_packets"].docs[0]["docuseal_started"] is True
    assert "swipesimple" not in built["signing_link"].lower()


def _walk_in_session(**extra):
    return _session(intake_channel="inperson", **extra)


def test_new_walk_in_without_surety_opens_the_osi_packet(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_walk_in_session())
    _install(monkeypatch, db, real_templates=True)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    saved = _confirm(_ocr(db).json()["scan_id"], _indemnitor_fields())
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["created_case"] is True
    assert body["signing_link"].endswith("/s/TEST-SLUG")
    assert "swipesimple" not in body["signing_link"].lower()
    assert "/pay" not in body["signing_link"].lower()
    case = db["bond_cases"].docs[0]
    assert case["surety_id"] == "osi"
    assert case["intake_channel"] == "inperson"
    assert case["source"] == "self_serve"
    assert not case.get("bond_amount")
    assert not case.get("premium")
    assert not case.get("poa_number")
    assert not case.get("booking_number")
    assert calls["n"] == 1
    assert calls["kwargs"]["template_id"] == "101"
    assert calls["kwargs"]["send_email"] is False
    assert calls["kwargs"]["send_sms"] is False
    assert db["paperwork_packets"].docs[0]["surety_id"] == "osi"
    audits = [row for row in db["audit_events"].docs if row.get("event_type") == "self_serve_draft_bondcase"]
    assert len(audits) == 1
    assert audits[0]["new_state"]["surety_id"] == "osi"
    assert NAME not in str(audits[0])


def test_assigned_palmetto_and_osi_stay_on_a_walk_in_confirm(monkeypatch):
    for surety, template in (("palmetto", "505"), ("osi", "101")):
        db = FakeDB()
        db["portal_pins"].docs.append(_walk_in_session())
        db["bond_cases"].docs.append({
            "bond_case_id": "BC-TEST-1",
            "indemnitor_id": "IND-TEST-1",
            "indemnitor_phone": PHONE,
            "indemnitor_name": NAME,
            "indemnitor_dob": DOB,
            "surety_id": surety,
            "status": "active",
            "source": "staff",
        })
        db["indemnitors"].docs.append({
            "indemnitor_id": "IND-TEST-1",
            "name": NAME,
            "phone": PHONE,
            "dob": DOB,
        })
        _install(monkeypatch, db, real_templates=True)
        _scanner(monkeypatch)
        calls = {"n": 0, "kwargs": {}}
        _submission(monkeypatch, calls)
        saved = _confirm(_ocr(db).json()["scan_id"], _indemnitor_fields())
        assert saved.status_code == 200, saved.text
        assert saved.json()["reused_case"] is True
        assert saved.json()["signing_link"].endswith("/s/TEST-SLUG")
        assert db["bond_cases"].docs[0]["surety_id"] == surety
        assert calls["kwargs"]["template_id"] == template
        assert len(db["bond_cases"].docs) == 1


def test_existing_unassigned_case_is_not_given_osi(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_walk_in_session())
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-1",
        "indemnitor_id": "IND-TEST-1",
        "indemnitor_phone": PHONE,
        "indemnitor_name": NAME,
        "indemnitor_dob": DOB,
        "status": "active",
        "source": "staff",
    })
    _install(monkeypatch, db, real_templates=True)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    saved = _confirm(_ocr(db).json()["scan_id"], _indemnitor_fields())
    assert saved.status_code == 200, saved.text
    assert saved.json()["signing_link"] == ""
    assert saved.json()["fallback"] is True
    assert "surety_id" not in db["bond_cases"].docs[0]
    assert calls["n"] == 0
    assert len(db["bond_cases"].docs) == 1


def test_remote_session_without_a_channel_does_not_pick_osi(monkeypatch):
    db = FakeDB()
    session = _session(mode="inperson", intake_channel="1")
    db["portal_pins"].docs.append(session)
    _install(monkeypatch, db, real_templates=True)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    saved = _confirm(_ocr(db).json()["scan_id"], _indemnitor_fields())
    assert saved.status_code == 200, saved.text
    assert saved.json()["signing_link"] == ""
    assert not db["bond_cases"].docs[0].get("surety_id")
    assert calls["n"] == 0


@pytest.mark.parametrize("surety", ["lexington", "not-a-carrier"])
def test_explicit_invalid_carrier_is_not_replaced_with_osi(monkeypatch, surety):
    db = FakeDB()
    db["portal_pins"].docs.append(_walk_in_session())
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-1",
        "indemnitor_id": "IND-TEST-1",
        "indemnitor_phone": PHONE,
        "indemnitor_name": NAME,
        "indemnitor_dob": DOB,
        "surety_id": surety,
        "status": "active",
        "source": "staff",
    })
    _install(monkeypatch, db, real_templates=True)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    saved = _confirm(_ocr(db).json()["scan_id"], _indemnitor_fields())
    assert saved.status_code == 200, saved.text
    assert saved.json()["signing_link"] == ""
    assert db["bond_cases"].docs[0]["surety_id"] == surety
    assert calls["n"] == 0


def test_walk_in_proof_rejects_a_raw_channel(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "ci-not-a-real-secret")
    from dashboard.services.paperwork_self_serve import consume_walk_in_proof, issue_walk_in_proof

    assert issue_walk_in_proof("1") == ""
    assert issue_walk_in_proof("true") == ""
    assert consume_walk_in_proof("inperson") == ""
    assert consume_walk_in_proof("kiosk") == ""
    proof = issue_walk_in_proof("ipad")
    assert proof
    assert consume_walk_in_proof(proof) == "ipad"


def test_send_pin_stores_the_signed_channel_only(monkeypatch):
    db = FakeDB()
    _install(monkeypatch, db)

    async def _send(*_args, **_kwargs):
        return {"success": True, "sent": True, "queued": False, "channel": "imessage"}

    monkeypatch.setattr("dashboard.services.bb_client.send_message_universal", _send)
    from dashboard.services.paperwork_self_serve import issue_walk_in_proof

    proof = issue_walk_in_proof("kiosk")
    accepted = client.post("/api/portal/send-pin", json={
        "phone": PHONE,
        "intake_proof": proof,
        "intake_channel": "palmetto",
    })
    assert accepted.status_code == 200, accepted.text
    assert db["portal_pins"].docs[-1]["intake_channel"] == "kiosk"
    rejected = client.post("/api/portal/send-pin", json={
        "phone": PHONE,
        "intake_proof": "inperson",
    })
    assert rejected.status_code == 200, rejected.text
    assert db["portal_pins"].docs[-1]["intake_channel"] == ""


def test_portal_page_signs_only_an_explicit_walk_in_mode():
    kiosk = client.get("/api/portal/portal-ui?mode=kiosk")
    assert kiosk.status_code == 200
    assert 'const WALK_IN_INTAKE_PROOF = "";' not in kiosk.text
    ambiguous = client.get("/api/portal/portal-ui?mode=1")
    assert 'const WALK_IN_INTAKE_PROOF = "";' in ambiguous.text
    query = client.get("/api/portal/portal-ui?inperson=1")
    assert 'const WALK_IN_INTAKE_PROOF = "";' in query.text


@pytest.mark.asyncio
async def test_unsigned_osi_packet_does_not_expose_a_pay_link(monkeypatch):
    db = FakeDB()
    db["portal_pins"].docs.append(_walk_in_session())
    _install(monkeypatch, db, real_templates=True)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls)
    saved = _confirm(_ocr(db).json()["scan_id"], _indemnitor_fields())
    packet_id = saved.json()["packet_id"]
    from dashboard.services.paperwork_done import load_done_state

    state = await load_done_state(packet_id, "indemnitor")
    assert not state.get("pay_url")
    assert not state.get("pay_amount")
