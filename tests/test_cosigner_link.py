"""One co-signer link: ID scan, DocuSeal, then the down payment.

No Mongo, no SwipeSimple HTTP, no network. Names are samples.
O'Neill does not match O'Neal. A full ID number is rejected.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.auth.pin_middleware import COOKIE_NAME, OPEN_PREFIXES, _sign_token
from dashboard.routers.cosigner_link import cosigner_bp
from dashboard.routers.webhooks import webhooks_bp
from dashboard.services.identity_verification_service import (
    IndemnitorIdentityError,
    require_verified_indemnitors,
    summarize_open_bond_cases,
)

BOOKING = "TEST-COS-1"
INDEMNITOR = "Casey Sample"
PACKET = "PKT-TEST-COS-1"
CASE = "BC-TEST-COS-1"
ACTOR = "office@example.invalid"
NAME = "Jamie O'Neal"
CONFIRMED = {
    "premium_confirmed_amount": "1500.00",
    "premium_confirmed_at": "2026-10-14T12:00:00+00:00",
    "premium_confirmed_by": ACTOR,
    "down_payment_amount": "500.00",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "ci-not-a-real-secret")
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)
    monkeypatch.delenv("SWIPESIMPLE_DISPATCH_LIVE", raising=False)
    monkeypatch.delenv("SWIPESIMPLE_LIVE", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_LEADS", raising=False)


def _silence_sends(monkeypatch):
    """DocuSeal, SwipeSimple, and customer sends stay mocked for every test."""
    spies = {
        "swipe_http": AsyncMock(),
        "swipe_create": AsyncMock(),
        "swipe_dispatch": AsyncMock(),
        "swipe_reconcile": AsyncMock(),
        "docuseal": AsyncMock(),
        "docuseal_http": AsyncMock(),
        "imessage": AsyncMock(),
        "imessage_direct": AsyncMock(),
        "sms": AsyncMock(),
        "email": AsyncMock(),
        "receipt": AsyncMock(),
    }
    monkeypatch.setattr(
        "dashboard.services.swipesimple_invoice_service._share_invoice_http",
        spies["swipe_http"],
    )
    monkeypatch.setattr(
        "dashboard.services.swipesimple_invoice_service._http_create_invoice",
        spies["swipe_create"],
    )
    monkeypatch.setattr(
        "dashboard.services.swipesimple_invoice_service.dispatch_invoice",
        spies["swipe_dispatch"],
    )
    monkeypatch.setattr(
        "dashboard.services.swipesimple_invoice_service.reconcile_payment",
        spies["swipe_reconcile"],
    )
    monkeypatch.setattr(
        "dashboard.services.docuseal_service.DocuSealService.create_submission",
        spies["docuseal"],
    )
    monkeypatch.setattr(
        "dashboard.services.docuseal_service.DocuSealService._request",
        spies["docuseal_http"],
    )
    monkeypatch.setattr(
        "dashboard.services.bb_client.send_message_universal",
        spies["imessage"],
    )
    monkeypatch.setattr(
        "dashboard.services.bb_client.send_imessage",
        spies["imessage_direct"],
    )
    monkeypatch.setattr(
        "dashboard.services.twilio_service.TwilioService.send_sms",
        spies["sms"],
    )
    monkeypatch.setattr(
        "dashboard.services.gmail_reader.GmailReaderService.send_email",
        spies["email"],
    )
    monkeypatch.setattr(
        "dashboard.services.payment_log_claims.send_customer_receipt_once",
        spies["receipt"],
    )
    return spies


@pytest.fixture(autouse=True)
def _no_live_sends(monkeypatch):
    spies = _silence_sends(monkeypatch)
    yield spies
    for spy in spies.values():
        spy.assert_not_called()


def _cookie(role="staff", email=ACTOR, name="Office Staff"):
    token = _sign_token(
        email=email,
        role=role,
        agent_name=name,
        is_admin=role in ("god_admin", "admin"),
    )
    return {COOKIE_NAME: token}


class _Result:
    def __init__(self, n):
        self.modified_count = n


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, length=None):
        return list(self._docs)

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _match(doc, query):
    if not query:
        return True
    if "$or" in query:
        rest = {key: value for key, value in query.items() if key != "$or"}
        return any(_match(doc, clause) for clause in query["$or"]) and _match(doc, rest)
    if "$and" in query:
        rest = {key: value for key, value in query.items() if key != "$and"}
        return all(_match(doc, clause) for clause in query["$and"]) and _match(doc, rest)
    for key, expected in query.items():
        if isinstance(expected, dict):
            if "$in" in expected:
                if doc.get(key) not in expected["$in"]:
                    return False
            elif "$ne" in expected:
                if doc.get(key) == expected["$ne"]:
                    return False
            else:
                return False
        elif doc.get(key) != expected:
            return False
    return True


class _Col:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    async def find_one(self, query=None, projection=None):
        for doc in self.docs:
            if _match(doc, query or {}):
                return doc
        return None

    def find(self, query=None, projection=None):
        return _Cursor([doc for doc in self.docs if _match(doc, query or {})])

    async def update_one(self, query, update, upsert=False):
        doc = await self.find_one(query)
        if doc is None:
            return _Result(0)
        for key, value in (update.get("$set") or {}).items():
            doc[key] = value
        return _Result(1)

    async def insert_one(self, doc):
        self.docs.append(doc)
        return None


def _world(monkeypatch, *, cosigner=""):
    indemnitor = INDEMNITOR if cosigner else NAME
    packet = {
        "packet_id": PACKET,
        "booking_number": BOOKING,
        "bond_case_id": CASE,
        "indemnitor_name": indemnitor,
        "docuseal_sign_url": "https://sign.example/s/abc",
        "status": "draft",
        "surety_id": "osi",
        **CONFIRMED,
    }
    bond = {
        "bond_case_id": CASE,
        "booking_number": BOOKING,
        "status": "active",
        "indemnitor_name": indemnitor,
        "surety_id": "osi",
        "swipesimple_invoice_number": BOOKING,
        "premium_paid": False,
        **CONFIRMED,
    }
    if cosigner:
        packet["cosigner_name"] = cosigner
        bond["co_signer_name"] = cosigner
        people = [
            {"role": "indemnitor", "name": indemnitor},
            {"role": "co_indemnitor", "name": cosigner},
        ]
        packet["indemnitors"] = people
        bond["indemnitors"] = people
    stores = {
        "paperwork_packets": _Col([packet]),
        "bond_cases": _Col([bond]),
    }

    def get(name):
        if name not in stores:
            stores[name] = _Col()
        return stores[name]

    for target in (
        "dashboard.extensions.get_collection",
        "dashboard.services.cosigner_link_service.get_collection",
        "dashboard.services.swipesimple_invoice_service.get_collection",
        "dashboard.routers.webhooks.get_collection",
    ):
        monkeypatch.setattr(target, get)
    app = FastAPI()
    app.include_router(cosigner_bp)
    app.include_router(webhooks_bp)
    return stores, TestClient(app)


def _audits(stores, event_type):
    return [
        row for row in stores["audit_events"].docs
        if row.get("event_type") == event_type
    ]


def test_public_paths_stay_open_and_staff_create_stays_gated():
    assert "/cosigner/" in OPEN_PREFIXES
    assert "/api/cosigner/" in OPEN_PREFIXES
    staff = "/api/paperwork/cosigner-link"
    assert not any(staff.startswith(prefix) for prefix in OPEN_PREFIXES)


def test_create_requires_staff(monkeypatch):
    _stores, client = _world(monkeypatch)
    missing = client.post("/api/paperwork/cosigner-link", json={"packet_id": PACKET})
    assert missing.status_code == 401
    recovery = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(role="recovery", email="recovery@example.invalid"),
    )
    assert recovery.status_code == 403


def test_scan_locks_oneill_then_sign_and_pay(monkeypatch):
    stores, client = _world(monkeypatch)
    created = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert created.status_code == 200, created.text
    token = created.json()["token"]
    assert created.json()["url"] == f"/cosigner/{token}"

    locked = client.get(f"/api/cosigner/{token}")
    assert locked.status_code == 200
    assert locked.json()["step"] == "scan"
    assert locked.json()["signing_locked"] is True
    assert locked.json()["sign_url"] is None
    english = client.get(f"/cosigner/{token}")
    assert english.status_code == 200
    assert "Español" in english.text
    assert "type='file'" in english.text
    assert "name='full_name'" not in english.text
    spanish = client.get(f"/cosigner/{token}?lang=es")
    assert "Escanee" in spanish.text
    assert "English" in spanish.text
    assert "type='file'" in spanish.text
    client.get(f"/cosigner/{token}?lang=en")

    rejected = client.post(
        f"/api/cosigner/{token}/scan",
        json={"full_name": NAME, "issuing_state": "FL", "success": True, "id_number": "D12345678"},
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"] == "attestation_id_number_rejected"
    assert "id_ocr" not in stores["paperwork_packets"].docs[0]

    mismatch = client.post(
        f"/api/cosigner/{token}/scan",
        json={"full_name": "Jamie O'Neill", "issuing_state": "FL", "success": True},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"] == "name_mismatch"
    assert "id_ocr" not in stores["paperwork_packets"].docs[0]
    assert "id_ocr" not in stores["bond_cases"].docs[0]
    still = client.get(f"/api/cosigner/{token}").json()
    assert still["sign_url"] is None
    assert still["step"] == "scan"

    bond = stores["bond_cases"].docs[0]
    before = summarize_open_bond_cases([bond])
    assert before["total_open"] == 0
    assert before["blocked_no_scan"] == 0
    assert before["verified_by_scan"] == 0

    import asyncio

    async def _gate():
        return await require_verified_indemnitors(
            bond_data={"indemnitor_name": NAME, "booking_number": BOOKING},
            bond_case_id=CASE,
            booking_number=BOOKING,
            packet_id=PACKET,
        )

    with pytest.raises(IndemnitorIdentityError) as raised:
        asyncio.run(_gate())
    assert raised.value.code == "indemnitor_identity_unverified"

    matched = client.post(
        f"/api/cosigner/{token}/scan",
        json={"full_name": NAME, "issuing_state": "FL", "success": True},
    )
    assert matched.status_code == 200, matched.text
    body = matched.json()
    assert body["step"] == "sign"
    assert body["sign_url"] == "https://sign.example/s/abc"
    assert body["signing_locked"] is False
    assert "id_verified" in body["timeline"]
    for collection in ("paperwork_packets", "bond_cases"):
        ocr = stores[collection].docs[0]["id_ocr"]
        assert ocr["extracted"]["full_name"] == NAME
        assert ocr["extracted"]["dl_state"] == "FL"
        assert "dl_number" not in ocr["extracted"]
    decision = asyncio.run(_gate())
    assert decision["ok"] is True
    assert decision["methods"] == ["scan"]

    after = summarize_open_bond_cases([stores["bond_cases"].docs[0]])
    assert after["total_open"] == 0
    assert after["verified_by_scan"] == 0

    stores["paperwork_packets"].docs[0]["status"] = "completed"
    paying = client.get(f"/api/cosigner/{token}").json()
    assert paying["step"] == "pay"
    assert paying["payment"]["dry_run"] is True
    assert paying["payment"]["amount"] == "500.00"
    assert paying["payment"]["dispatched"] is False
    assert paying["timeline"] == ["id_verified", "signed"]

    spanish_pay = client.get(f"/cosigner/{token}?lang=es")
    assert "Pague el enganche" in spanish_pay.text
    assert "500.00" in spanish_pay.text

    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("SWIPESIMPLE_LIVE", "1")
    monkeypatch.setenv("SWIPESIMPLE_DISPATCH_LIVE", "1")
    monkeypatch.delenv("SWIPESIMPLE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("REQUIRE_DASHBOARD_PIN", raising=False)
    calls = {"n": 0}

    def _db():
        return {"db": "mocked"}

    async def _record(db, **kwargs):
        assert db == {"db": "mocked"}
        calls["n"] += 1
        return {
            "committed": calls["n"] == 1,
            "duplicate": calls["n"] > 1,
            "needs_review": False,
            "transaction_id": kwargs.get("transaction_id"),
            "event_kind": "charge",
            "amount": kwargs.get("amount"),
        }

    monkeypatch.setattr("dashboard.extensions.get_db", _db)
    monkeypatch.setattr(
        "dashboard.services.payment_log_claims.record_provider_payment",
        _record,
    )
    payload = {
        "event_type": "payment.completed",
        "status": "approved",
        "transaction_id": "TX-PAY",
        "amount": "500.00",
        "custom_fields": {"booking_number": BOOKING, "reference_id": BOOKING},
    }
    paid = client.post("/api/webhooks/payment", json=payload)
    assert paid.status_code == 200, paid.text
    bond = stores["bond_cases"].docs[0]
    assert bond["premium_paid"] is False
    assert "last_payment_source" not in bond
    link = stores["cosigner_links"].docs[0]
    assert link["paid_transaction_id"] == "TX-PAY"
    assert [row["step"] for row in link["timeline"]] == ["id_verified", "signed", "paid"]
    assert len(_audits(stores, "paid")) == 1
    assert "payments" not in stores

    replay = client.post("/api/webhooks/payment", json=payload)
    assert replay.status_code == 200
    assert stores["bond_cases"].docs[0]["premium_paid"] is False
    assert len(_audits(stores, "paid")) == 1
    assert calls["n"] == 2
    assert "payments" not in stores
    done = client.get(f"/api/cosigner/{token}").json()
    assert done["step"] == "paid"
    assert done["timeline"] == ["id_verified", "signed", "paid"]


def test_page_upload_scans_on_the_server(monkeypatch):
    stores, client = _world(monkeypatch)
    created = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET, "lang": "es"},
        cookies=_cookie(),
    )
    token = created.json()["token"]
    typed = client.post(
        f"/api/cosigner/{token}/scan",
        data={"full_name": NAME, "issuing_state": "FL", "success": "true"},
    )
    assert typed.status_code == 400
    assert typed.json()["error"] == "scan_image_required"
    assert "id_ocr" not in stores["paperwork_packets"].docs[0]

    async def _scan(image_bytes, filename=""):
        assert image_bytes == b"id-photo"
        return {
            "success": True,
            "extracted": {
                "full_name": NAME,
                "dl_state": "FL",
                "dl_number": "D12345678",
            },
        }

    monkeypatch.setattr(
        "dashboard.services.id_scanner_service.IDScannerService.scan_id_image",
        _scan,
    )
    uploaded = client.post(
        f"/api/cosigner/{token}/scan",
        files={"id_image": ("id.jpg", b"id-photo", "image/jpeg")},
        data={"lang": "es"},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert "DocuSeal" in uploaded.text
    assert "Firme la fianza" in uploaded.text
    ocr = stores["paperwork_packets"].docs[0]["id_ocr"]
    assert ocr["extracted"]["full_name"] == NAME
    assert "dl_number" not in ocr["extracted"]
    assert "D12345678" not in uploaded.text


def _token(client):
    created = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert created.status_code == 200, created.text
    return created.json()["token"]


def _locked(client, stores, token):
    body = client.get(f"/api/cosigner/{token}").json()
    assert body["step"] == "scan"
    assert body["signing_locked"] is True
    assert body["sign_url"] is None
    assert "id_ocr" not in stores["paperwork_packets"].docs[0]
    assert "id_ocr" not in stores["bond_cases"].docs[0]
    return body


def _gate(**kwargs):
    async def _run():
        return await require_verified_indemnitors(
            bond_case_id=CASE,
            booking_number=BOOKING,
            packet_id=PACKET,
            **kwargs,
        )

    return asyncio.run(_run())


def test_no_scan_blocks_signing(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    _locked(client, stores, token)
    with pytest.raises(IndemnitorIdentityError) as raised:
        _gate(bond_data={"indemnitor_name": NAME, "booking_number": BOOKING})
    assert raised.value.code == "indemnitor_identity_unverified"
    assert raised.value.issues[0]["reason"] == "no_scan"


def test_failed_scan_blocks_signing(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    failed = client.post(
        f"/api/cosigner/{token}/scan",
        json={"full_name": NAME, "issuing_state": "FL", "success": False},
    )
    assert failed.status_code == 409
    assert failed.json()["error"] == "scan_failed"
    unreadable = client.post(
        f"/api/cosigner/{token}/scan",
        json={"full_name": NAME, "issuing_state": "NY", "success": True, "status": "unreadable"},
    )
    assert unreadable.status_code == 409
    assert unreadable.json()["error"] == "scan_failed"
    _locked(client, stores, token)
    with pytest.raises(IndemnitorIdentityError) as raised:
        _gate(bond_data={"indemnitor_name": NAME, "booking_number": BOOKING})
    assert raised.value.issues[0]["reason"] == "no_scan"


def test_wrong_name_blocks_signing(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    mismatch = client.post(
        f"/api/cosigner/{token}/scan",
        json={"full_name": "Jamie O'Neill", "issuing_state": "GA", "success": True},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"] == "name_mismatch"
    _locked(client, stores, token)


def test_country_only_scan_blocks_signing(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    samples = (
        {"full_name": NAME, "issuing_state": "US", "success": True},
        {"full_name": NAME, "issuing_state": "USA", "success": True},
        {"full_name": NAME, "issuing_country": "US", "success": True},
        {"full_name": NAME, "country": "United States", "state": "FL", "success": True},
    )
    for sample in samples:
        blocked = client.post(f"/api/cosigner/{token}/scan", json=sample)
        assert blocked.status_code == 400, sample
        assert blocked.json()["error"] == "no_issuing_state"
        _locked(client, stores, token)
    with pytest.raises(IndemnitorIdentityError) as raised:
        _gate(bond_data={"indemnitor_name": NAME, "booking_number": BOOKING})
    assert any(issue["reason"] == "no_scan" for issue in raised.value.issues)


def test_any_us_state_scan_unlocks_signing(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    matched = client.post(
        f"/api/cosigner/{token}/scan",
        json={"full_name": NAME, "issuing_state": "Georgia", "success": True},
    )
    assert matched.status_code == 200, matched.text
    body = matched.json()
    assert body["step"] == "sign"
    assert body["signing_locked"] is False
    assert body["sign_url"] == "https://sign.example/s/abc"
    ocr = stores["paperwork_packets"].docs[0]["id_ocr"]
    assert ocr["role"] == "indemnitor"
    assert ocr["extracted"]["dl_state"] == "GA"
    assert ocr["extracted"]["full_name"] == NAME
    decision = _gate(bond_data={"indemnitor_name": NAME, "booking_number": BOOKING})
    assert decision["ok"] is True
    assert decision["methods"] == ["scan"]


def test_cosigner_different_from_indemnitor(monkeypatch):
    stores, client = _world(monkeypatch, cosigner=NAME)
    token = _token(client)
    link = stores["cosigner_links"].docs[0]
    assert link["signer_name"] == NAME
    assert link["signer_role"] == "co_indemnitor"
    assert link["indemnitor_name"] == INDEMNITOR

    indemnitor_id = client.post(
        f"/api/cosigner/{token}/scan",
        json={"full_name": INDEMNITOR, "issuing_state": "FL", "success": True},
    )
    assert indemnitor_id.status_code == 409
    assert indemnitor_id.json()["error"] == "name_mismatch"
    _locked(client, stores, token)

    country = client.post(
        f"/api/cosigner/{token}/scan",
        json={"full_name": NAME, "issuing_country": "USA", "success": True},
    )
    assert country.status_code == 400
    assert country.json()["error"] == "no_issuing_state"
    _locked(client, stores, token)

    matched = client.post(
        f"/api/cosigner/{token}/scan",
        json={"full_name": NAME, "issuing_state": "New York", "success": True},
    )
    assert matched.status_code == 200, matched.text
    body = matched.json()
    assert body["step"] == "sign"
    assert body["sign_url"] == "https://sign.example/s/abc"
    ocr = stores["bond_cases"].docs[0]["id_ocr"]
    assert ocr["role"] == "co_indemnitor"
    assert ocr["extracted"]["full_name"] == NAME
    assert ocr["extracted"]["dl_state"] == "NY"
    assert "dl_number" not in ocr["extracted"]

    bond = stores["bond_cases"].docs[0]
    with pytest.raises(IndemnitorIdentityError) as raised:
        _gate(bond_data=bond)
    reasons = {(issue["role"], issue["reason"]) for issue in raised.value.issues}
    assert ("indemnitor", "no_scan") in reasons
    assert "co_indemnitor" not in {issue["role"] for issue in raised.value.issues}

    co_only = _gate(parties=[{"role": "co_indemnitor", "name": NAME}])
    assert co_only["ok"] is True
    assert co_only["methods"] == ["scan"]
