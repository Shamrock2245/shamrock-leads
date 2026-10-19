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
    assert rejected.json()["error"] == "scan_image_required"
    assert "id_ocr" not in stores["paperwork_packets"].docs[0]

    mismatch = _upload(
        monkeypatch, client, token,
        {"full_name": "Jamie O'Neill", "dl_state": "FL", "dl_number": "D12345678"},
    )
    assert mismatch.status_code == 409
    assert _error_text(mismatch) == "name_mismatch"
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

    matched = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL", "dl_number": "D12345678"},
    )
    assert matched.status_code == 200, matched.text
    assert "DocuSeal" in matched.text
    assert "D12345678" not in matched.text
    body = client.get(f"/api/cosigner/{token}").json()
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

    short = {
        "event_type": "payment.completed",
        "status": "approved",
        "transaction_id": "TX-SHORT",
        "amount": "1.00",
        "custom_fields": {"booking_number": BOOKING, "reference_id": BOOKING},
    }
    # The recorder mock is installed below. A $1 charge must not mark paid.
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
    short_paid = client.post("/api/webhooks/payment", json=short)
    assert short_paid.status_code == 200, short_paid.text
    assert stores["bond_cases"].docs[0]["premium_paid"] is False
    assert not stores["cosigner_links"].docs[0].get("paid_transaction_id")
    assert [row["step"] for row in stores["cosigner_links"].docs[0]["timeline"]] == [
        "id_verified", "signed",
    ]
    assert len(_audits(stores, "paid")) == 0
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"

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
    assert calls["n"] == 3
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


def _upload(monkeypatch, client, token, extracted, *, success=True, status="", passed=None):
    """Public scans are an ID photo. The scanner is mocked; nothing is charged."""

    async def _scan(image_bytes, filename=""):
        assert image_bytes == b"id-photo"
        scanned = {"success": success, "status": status, "extracted": dict(extracted)}
        if passed is not None:
            scanned["passed"] = passed
        return scanned

    monkeypatch.setattr(
        "dashboard.services.id_scanner_service.IDScannerService.scan_id_image",
        _scan,
    )
    return client.post(
        f"/api/cosigner/{token}/scan",
        files={"id_image": ("id.jpg", b"id-photo", "image/jpeg")},
    )


def _error_text(response):
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        return response.json().get("error")
    return response.text


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
    failed = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
        success=False,
    )
    assert failed.status_code == 409
    assert _error_text(failed) == "scan_failed"
    unreadable = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "NY"},
        success=True,
        status="unreadable",
    )
    assert unreadable.status_code == 409
    assert _error_text(unreadable) == "scan_failed"
    _locked(client, stores, token)
    with pytest.raises(IndemnitorIdentityError) as raised:
        _gate(bond_data={"indemnitor_name": NAME, "booking_number": BOOKING})
    assert raised.value.issues[0]["reason"] == "no_scan"


def test_wrong_name_blocks_signing(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    mismatch = _upload(
        monkeypatch, client, token,
        {"full_name": "Jamie O'Neill", "issuing_state": "GA"},
    )
    assert mismatch.status_code == 409
    assert _error_text(mismatch) == "name_mismatch"
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
        blocked = _upload(monkeypatch, client, token, sample)
        assert blocked.status_code == 400, sample
        assert _error_text(blocked) == "no_issuing_state"
        _locked(client, stores, token)
    with pytest.raises(IndemnitorIdentityError) as raised:
        _gate(bond_data={"indemnitor_name": NAME, "booking_number": BOOKING})
    assert any(issue["reason"] == "no_scan" for issue in raised.value.issues)


def test_any_us_state_scan_unlocks_signing(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    matched = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "issuing_state": "Georgia"},
    )
    assert matched.status_code == 200, matched.text
    body = client.get(f"/api/cosigner/{token}").json()
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

    packet = stores["paperwork_packets"].docs[0]
    packet["docuseal_sign_url"] = "https://sign.example/s/indemnitor"
    packet["docuseal_submitters"] = [
        {"role": "indemnitor", "sign_url": "https://sign.example/s/indemnitor"},
        {
            "role": "coindemnitor",
            "sign_url": "https://sign.example/s/cosigner",
            "metadata": {"party_role": "co_indemnitor"},
        },
    ]
    indemnitor_id = _upload(
        monkeypatch, client, token,
        {"full_name": INDEMNITOR, "issuing_state": "FL"},
    )
    assert indemnitor_id.status_code == 409
    assert _error_text(indemnitor_id) == "name_mismatch"
    _locked(client, stores, token)

    country = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "issuing_country": "USA"},
    )
    assert country.status_code == 400
    assert _error_text(country) == "no_issuing_state"
    _locked(client, stores, token)

    matched = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "issuing_state": "New York"},
    )
    assert matched.status_code == 200, matched.text
    body = client.get(f"/api/cosigner/{token}").json()
    assert body["step"] == "sign"
    assert body["sign_url"] == "https://sign.example/s/cosigner"
    assert "https://sign.example/s/indemnitor" not in matched.text
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


def test_active_bond_scan_and_invoice_link_alias(monkeypatch):
    stores, client = _world(monkeypatch, cosigner=NAME)
    bond = stores["bond_cases"].docs.pop()
    stores["active_bonds"] = _Col([bond])
    packet = stores["paperwork_packets"].docs[0]
    packet["docuseal_sign_url"] = "https://sign.example/s/indemnitor"
    packet["docuseal_submitters"] = [
        {"role": "indemnitor", "sign_url": "https://sign.example/s/indemnitor"},
        {"role": "coindemnitor", "sign_url": "https://sign.example/s/cosigner"},
    ]
    packet["payment_link"] = "https://shamrockbailbonds.biz/payment?amount=10"
    token = _token(client)
    assert stores["cosigner_links"].docs[0]["signer_role"] == "co_indemnitor"
    uploaded = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "PR"},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert stores["bond_cases"].docs == []
    ocr = stores["active_bonds"].docs[0]["id_ocr"]
    assert ocr["role"] == "co_indemnitor"
    assert ocr["extracted"]["dl_state"] == "PR"
    state = client.get(f"/api/cosigner/{token}").json()
    assert state["sign_url"] == "https://sign.example/s/cosigner"

    packet["invoice_payment_link"] = "https://pay.example/down"
    packet["status"] = "completed"
    paying = client.get(f"/api/cosigner/{token}").json()
    assert paying["step"] == "pay"
    assert paying["payment"]["url"] == "https://pay.example/down"
    assert paying["payment"]["dispatched"] is False


def _scanner(monkeypatch, calls):
    async def _scan(image_bytes, filename=""):
        calls["n"] += 1
        return {
            "success": True,
            "extracted": {"full_name": NAME, "dl_state": "FL", "dl_number": "D12345678"},
        }

    monkeypatch.setattr(
        "dashboard.services.id_scanner_service.IDScannerService.scan_id_image",
        _scan,
    )


def _post_scan(client, token):
    return client.post(
        f"/api/cosigner/{token}/scan",
        files={"id_image": ("id.jpg", b"id-photo", "image/jpeg")},
    )


def test_unknown_token_does_not_run_ocr(monkeypatch):
    stores, client = _world(monkeypatch)
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    unknown = _post_scan(client, "not-a-real-token")
    assert unknown.status_code == 404
    assert unknown.json()["error"] == "link_not_found"
    assert calls["n"] == 0

    token = _token(client)
    stores["cosigner_links"].docs[0]["revoked"] = True
    revoked = _post_scan(client, token)
    assert revoked.status_code == 410
    assert revoked.json()["error"] == "link_revoked"
    assert calls["n"] == 0

    stores["cosigner_links"].docs[0]["revoked"] = False
    stores["cosigner_links"].docs[0]["expires_at"] = "2020-01-01T00:00:00+00:00"
    expired = _post_scan(client, token)
    assert expired.status_code == 410
    assert expired.json()["error"] == "link_expired"
    assert calls["n"] == 0
    assert "id_ocr" not in stores["paperwork_packets"].docs[0]


def test_voided_packet_hides_the_sign_url_and_refuses_scan(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL", "dl_number": "D12345678"},
    )
    assert opened.status_code == 200
    assert client.get(f"/api/cosigner/{token}").json()["sign_url"] == "https://sign.example/s/abc"

    packet = stores["paperwork_packets"].docs[0]
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    for marker in (
        {"voided": True, "status": "voided", "superseded": False},
        {"voided": False, "status": "canceled", "superseded": False},
        {"voided": False, "status": "cancelled", "superseded": False},
        {"voided": False, "status": "draft", "superseded": True},
        {"voided": False, "status": "superseded", "superseded": False},
    ):
        packet.update(marker)
        state = client.get(f"/api/cosigner/{token}")
        assert state.status_code == 409, marker
        assert state.json()["error"] == "packet_inactive"
        assert "sign.example" not in state.text
        page = client.get(f"/cosigner/{token}")
        assert "sign.example" not in page.text
        refused = _post_scan(client, token)
        assert refused.status_code == 409, marker
        assert refused.json()["error"] == "packet_inactive"
        assert "sign.example" not in refused.text
    assert calls["n"] == 0


def test_case_id_wins_and_ambiguous_booking_fails_closed(monkeypatch):
    stores, client = _world(monkeypatch)
    exact = stores["bond_cases"].docs[0]
    other = {
        "bond_case_id": "BC-TEST-OTHER",
        "booking_number": BOOKING,
        "status": "active",
        "indemnitor_name": "Pat Other",
        "surety_id": "osi",
        "premium_paid": False,
        **CONFIRMED,
    }
    stores["bond_cases"].docs.insert(0, other)
    token = _token(client)
    uploaded = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert "id_ocr" not in stores["bond_cases"].docs[0]
    assert stores["bond_cases"].docs[1]["bond_case_id"] == CASE
    assert stores["bond_cases"].docs[1]["id_ocr"]["extracted"]["full_name"] == NAME

    stores["paperwork_packets"].docs[0]["bond_case_id"] = ""
    state = client.get(f"/api/cosigner/{token}")
    assert state.status_code == 409
    assert state.json()["error"] == "bond_ambiguous"
    assert "sign.example" not in state.text
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    refused = _post_scan(client, token)
    assert refused.status_code == 409
    assert refused.json()["error"] == "scan_closed"
    assert calls["n"] == 0
    assert "id_ocr" not in stores["bond_cases"].docs[0]
    assert exact.get("id_ocr", {}).get("extracted", {}).get("full_name") == NAME

    fresh, fresh_client = _world(monkeypatch)
    fresh["bond_cases"].docs.insert(0, dict(other))
    open_token = _token(fresh_client)
    fresh["paperwork_packets"].docs[0]["bond_case_id"] = ""
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    ambiguous = _post_scan(fresh_client, open_token)
    assert ambiguous.status_code == 409
    assert _error_text(ambiguous) == "bond_ambiguous"
    assert calls["n"] == 1
    assert "id_ocr" not in fresh["paperwork_packets"].docs[0]
    assert "id_ocr" not in fresh["bond_cases"].docs[0]


def test_link_audits_record_old_and_new_state(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    uploaded = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL", "dl_number": "D12345678"},
    )
    assert uploaded.status_code == 200, uploaded.text
    verified = _audits(stores, "id_verified")
    assert len(verified) == 1
    assert verified[0]["old_state"]["id_verified"] is False
    assert verified[0]["old_state"]["step"] == "scan"
    assert verified[0]["new_state"]["id_verified"] is True
    assert verified[0]["new_state"]["step"] == "sign"
    assert "id_verified" not in verified[0]["old_state"]["timeline"]
    assert verified[0]["new_state"]["timeline"] == ["id_verified"]

    stores["paperwork_packets"].docs[0]["status"] = "completed"
    paying = client.get(f"/api/cosigner/{token}")
    assert paying.status_code == 200
    signed = _audits(stores, "signed")
    assert len(signed) == 1
    assert signed[0]["old_state"]["signed"] is False
    assert signed[0]["old_state"]["step"] == "sign"
    assert signed[0]["new_state"]["signed"] is True
    assert signed[0]["new_state"]["step"] == "pay"
    assert signed[0]["new_state"]["timeline"] == ["id_verified", "signed"]

    from dashboard.services.cosigner_link_service import note_cosigner_paid

    asyncio.run(note_cosigner_paid(BOOKING, "TX-PAY", "500.00"))
    paid = _audits(stores, "paid")
    assert len(paid) == 1
    assert paid[0]["old_state"]["paid"] is False
    assert paid[0]["new_state"]["paid"] is True
    assert paid[0]["new_state"]["step"] == "paid"
    assert "paid" not in paid[0]["old_state"]["timeline"]
    assert paid[0]["new_state"]["timeline"][-1] == "paid"
    dumped = str(stores["audit_events"].docs)
    assert "D12345678" not in dumped
    assert "dl_number" not in dumped


def _links(stores):
    return stores.get("cosigner_links").docs if stores.get("cosigner_links") else []


def test_link_requires_a_matching_bond(monkeypatch):
    stores, client = _world(monkeypatch)
    stores["bond_cases"].docs.clear()
    missing = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert missing.status_code == 409
    assert missing.json()["error"] == "bond_required"
    assert _links(stores) == []

    stores, client = _world(monkeypatch)
    stores["bond_cases"].docs[0]["indemnitor_name"] = "Pat Other"
    mismatched = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert mismatched.status_code == 409
    assert mismatched.json()["error"] == "signer_mismatch"
    assert _links(stores) == []
    assert "token" not in mismatched.text


def test_approved_charge_is_not_the_down_payment(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    uploaded = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert uploaded.status_code == 200, uploaded.text
    bond = stores["bond_cases"].docs[0]
    bond["last_payment_status"] = "approved"
    bond["payment_status"] = "approved"
    bond["last_payment_amount"] = "1.00"
    stores["paperwork_packets"].docs[0]["status"] = "completed"
    state = client.get(f"/api/cosigner/{token}")
    assert state.status_code == 200, state.text
    body = state.json()
    assert body["step"] == "pay"
    assert body["payment"]["amount"] == "500.00"
    assert len(_audits(stores, "paid")) == 0
    assert not stores["cosigner_links"].docs[0].get("paid_transaction_id")


def test_paid_bond_still_requires_a_signature(monkeypatch):
    stores, client = _world(monkeypatch)
    stores["bond_cases"].docs[0]["premium_paid"] = True
    token = _token(client)
    uploaded = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert uploaded.status_code == 200, uploaded.text
    signing = client.get(f"/api/cosigner/{token}").json()
    assert signing["step"] == "sign"
    assert signing["sign_url"] == "https://sign.example/s/abc"
    stores["paperwork_packets"].docs[0]["status"] = "completed"
    paid = client.get(f"/api/cosigner/{token}").json()
    assert paid["step"] == "paid"
    assert paid["sign_url"] == "https://sign.example/s/abc"


def test_verified_or_signed_packet_refuses_another_scan(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    uploaded = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL", "dl_number": "D12345678"},
    )
    assert uploaded.status_code == 200, uploaded.text
    original = dict(stores["paperwork_packets"].docs[0]["id_ocr"])
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    again = _post_scan(client, token)
    assert again.status_code == 409
    assert again.json()["error"] == "scan_closed"
    assert calls["n"] == 0
    assert stores["paperwork_packets"].docs[0]["id_ocr"] == original

    stores, client = _world(monkeypatch)
    token = _token(client)
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    for status in ("signed", "completed"):
        stores["paperwork_packets"].docs[0]["status"] = status
        assert stores["cosigner_links"].docs[0].get("id_verified") is not True
        refused = _post_scan(client, token)
        assert refused.status_code == 409, status
        assert refused.json()["error"] == "scan_closed"
        assert "id_ocr" not in stores["paperwork_packets"].docs[0]
        page = client.get(f"/api/cosigner/{token}")
        assert page.status_code == 200, status
        assert page.json()["step"] == "scan"
    assert calls["n"] == 0


def _gateway(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("SWIPESIMPLE_LIVE", "1")
    monkeypatch.delenv("SWIPESIMPLE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("REQUIRE_DASHBOARD_PIN", raising=False)
    seen = []

    def _db():
        return {"db": "mocked"}

    async def _record(db, **kwargs):
        extra = kwargs.get("extra") or {}
        event = str(extra.get("event_type") or "").lower()
        status = str(kwargs.get("status") or "").lower()
        if "refund" in event or "revers" in event or status in {"refunded", "refund"}:
            kind = "refund"
        elif status in {"failed", "declined", "voided"} or event.endswith(".failed"):
            kind = "failure"
        else:
            kind = "charge"
        txn = kwargs.get("transaction_id")
        seen.append(txn)
        first = seen.count(txn) == 1
        return {
            "committed": first,
            "duplicate": not first,
            "needs_review": kind in {"refund", "failure"},
            "transaction_id": txn,
            "event_kind": kind,
            "amount": kwargs.get("amount"),
        }

    monkeypatch.setattr("dashboard.extensions.get_db", _db)
    monkeypatch.setattr(
        "dashboard.services.payment_log_claims.record_provider_payment",
        _record,
    )


def _payment(client, *, txn, amount, kind):
    event = "payment.refunded" if kind == "refund" else "payment.completed"
    status = "refunded" if kind == "refund" else "approved"
    return client.post("/api/webhooks/payment", json={
        "event_type": event,
        "status": status,
        "transaction_id": txn,
        "amount": amount,
        "custom_fields": {"booking_number": BOOKING, "reference_id": BOOKING},
    })


def test_refund_reopens_the_down_payment(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    uploaded = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert uploaded.status_code == 200, uploaded.text
    stores["paperwork_packets"].docs[0]["status"] = "completed"
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    _gateway(monkeypatch)

    paid = _payment(client, txn="TX-PAY", amount="500.00", kind="charge")
    assert paid.status_code == 200, paid.text
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "paid"
    assert stores["bond_cases"].docs[0]["premium_paid"] is False

    refunded = _payment(client, txn="TX-PAY", amount="500.00", kind="refund")
    assert refunded.status_code == 200, refunded.text
    reopened = client.get(f"/api/cosigner/{token}").json()
    assert reopened["step"] == "pay"
    assert reopened["payment"]["amount"] == "500.00"
    link = stores["cosigner_links"].docs[0]
    assert link["payment_reversed"] is True
    assert not link.get("paid_transaction_id")
    assert len(_audits(stores, "payment_reversed")) == 1
    reversed_audit = _audits(stores, "payment_reversed")[0]
    assert reversed_audit["old_state"]["paid"] is True
    assert reversed_audit["new_state"]["paid"] is False
    assert reversed_audit["new_state"]["payment_reversed"] is True

    replay = _payment(client, txn="TX-PAY", amount="500.00", kind="refund")
    assert replay.status_code == 200
    assert len(_audits(stores, "payment_reversed")) == 1
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"

    again = _payment(client, txn="TX-PAY-2", amount="500.00", kind="charge")
    assert again.status_code == 200, again.text
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "paid"
    assert stores["cosigner_links"].docs[0]["paid_transaction_id"] == "TX-PAY-2"
    assert stores["cosigner_links"].docs[0]["payment_reversed"] is False
    assert stores["bond_cases"].docs[0]["premium_paid"] is False

    other = _payment(client, txn="TX-OTHER", amount="500.00", kind="refund")
    assert other.status_code == 200, other.text
    assert stores["cosigner_links"].docs[0]["paid_transaction_id"] == "TX-PAY-2"
    assert stores["cosigner_links"].docs[0]["payment_reversed"] is False
    assert len(_audits(stores, "payment_reversed")) == 1
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "paid"
