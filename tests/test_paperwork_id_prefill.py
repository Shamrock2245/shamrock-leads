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


def _install(monkeypatch, db):
    getter = _bind(db)
    monkeypatch.setattr("dashboard.routers.pin_portal.get_collection", getter)
    monkeypatch.setattr("dashboard.extensions.get_collection", getter)
    monkeypatch.setattr("dashboard.services.staff_chain_service.get_collection", getter)
    monkeypatch.setenv("PAPERWORK_SELF_SERVE_PREFILL", "1")
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
    assert case["status"] == "active"
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
    assert saved.json()["created_case"] is True
    assert len(db["bond_cases"].docs) == 1
    case = db["bond_cases"].docs[0]
    assert case["source"] == "self_serve"
    assert case["status"] == "pending_owner_review"
    assert case["created_by"] == "auto"
    assert not case.get("bond_amount")
    assert not case.get("premium")
    assert not case.get("poa_number")
    audits = [row for row in db["audit_events"].docs if row.get("event_type") == "self_serve_draft_bondcase"]
    assert len(audits) == 1
    assert audits[0]["actor"] == "auto"
    assert NAME not in str(audits[0])
    assert "TEST-DL-0000" not in str(audits[0])
    values = calls["kwargs"]["submitters"][1]["values"]
    assert values["indemnitor_name"] == NAME
    assert values["indemnitor_dob"] == DOB
    assert values["indemnitor_address"] == "10 Test Lane"
    assert values["indemnitor_dl"] == "TEST-DL-0000"
    assert values["indemnitor_dl_state"] == "FL"
    assert values.get("bond_amount") in ("", None)
    assert values.get("numeric_premium") in ("", None)
    assert values.get("poa_number") in ("", None)
    assert "100.00" not in str(values.get("numeric_premium") or "")
    assert calls["kwargs"]["send_email"] is False
    assert calls["kwargs"]["send_sms"] is False


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
    assert first.json()["packet_id"] == second.json()["packet_id"]
    assert first.json()["bond_case_id"] == second.json()["bond_case_id"]
    assert len(db["bond_cases"].docs) == 1
    assert len(db["paperwork_packets"].docs) == 1
    assert calls["n"] == 1
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
    import asyncio
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
    _install(monkeypatch, db)
    _scanner(monkeypatch)
    calls = {"n": 0, "kwargs": {}}
    _submission(monkeypatch, calls, status="unverified")
    saved = _confirm(_ocr(db).json()["scan_id"], _indemnitor_fields())
    assert saved.status_code == 200, saved.text
    assert saved.json()["signing_link"] == ""
    assert saved.json()["fallback"] is True
    assert db["paperwork_packets"].docs == []


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
    import asyncio
    pending = await get_pending_id_prefill("BC-TEST-1", "indemnitor", "IND-TEST-1")
    assert pending["fields"]["indemnitor_dl"] == "TEST-DL-0000"
    assert db["bond_cases"].docs[0]["poa_number"] == "TEST-POA-2"
    assert len(db["bond_cases"].docs[0]["pending_id_prefills"]) == 1
