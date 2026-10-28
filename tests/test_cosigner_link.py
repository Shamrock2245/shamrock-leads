"""One co-signer link: ID scan, DocuSeal, then the down payment.

No Mongo, no SwipeSimple HTTP, no network. Names are samples.
O'Neill does not match O'Neal. A full ID number is rejected.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
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
DEFENDANT_ID = "TEST-DEF-COS-1"
INDEMNITOR_ID = "TEST-IND-COS-1"
CO_INDEMNITOR_ID = "TEST-COIND-COS-1"
ACTOR = "office@example.invalid"


def _swipe_link(slug: str) -> str:
    """A TEST invoice URL on the configured SwipeSimple host."""
    from dashboard.services.swipesimple_invoice_service import _DEFAULT_BASE_URL

    base = (os.getenv("SWIPESIMPLE_BASE_URL") or _DEFAULT_BASE_URL).strip().rstrip("/")
    return f"{base}/links/TEST-{slug}"
NAME = "Jamie O'Neal"
SUBMISSION = "DS-TEST-COS-1"
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


def _sign(slug):
    """The signer URL on the app's configured DocuSeal host."""
    from dashboard.services.cosigner_link_service import _configured_docuseal_origin

    return f"{_configured_docuseal_origin()}/s/{slug}"


def _bound_submitter(role, url, *, packet_id=PACKET, submission=SUBMISSION, external=None, name=""):
    party = "co_indemnitor" if role in {
        "coindemnitor", "co_indemnitor", "cosigner", "co_signer",
    } else "indemnitor"
    if external is None:
        suffix = "co" if party == "co_indemnitor" else "1"
        external = f"{packet_id}:indemnitor:{suffix}"
    return {
        "role": role,
        "name": name,
        "sign_url": url,
        "submission_id": submission,
        "external_id": external,
        "metadata": {
            "packet_id": packet_id,
            "party_role": party,
            "submission_id": submission,
        },
    }


def _sign_hidden(text):
    """Error bodies do not carry a stored or configured signer URL."""
    lowered = str(text or "").lower()
    for marker in (
        "sign.example",
        "evil.example",
        "/s/abc",
        "/s/indemnitor",
        "/s/cosigner",
        "/s/other",
        "shamrockbailbonds.biz/s/",
    ):
        if marker in lowered:
            return False
    return True


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
        self.matched_count = n
        self.modified_count = n
        self.deleted_count = n


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


def _assign(doc, key, value):
    if "." not in str(key):
        doc[key] = value
        return
    cur = doc
    parts = str(key).split(".")
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _remove(doc, key):
    if "." not in str(key):
        doc.pop(key, None)
        return
    cur = doc
    parts = str(key).split(".")
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return
        cur = cur[part]
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


def _lookup(doc, key):
    if "." not in str(key):
        return key in doc, doc.get(key)
    cur = doc
    for part in str(key).split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


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
        present, current = _lookup(doc, key)
        if isinstance(expected, dict):
            if "$in" in expected:
                if current not in expected["$in"]:
                    return False
            elif "$ne" in expected:
                if current == expected["$ne"]:
                    return False
            elif "$exists" in expected:
                if present != bool(expected["$exists"]):
                    return False
            elif "$lt" in expected:
                if not present or current is None:
                    current = 0
                try:
                    if not current < expected["$lt"]:
                        return False
                except TypeError:
                    return False
            elif "$lte" in expected:
                if not present or current in (None, ""):
                    return False
                try:
                    if not current <= expected["$lte"]:
                        return False
                except TypeError:
                    return False
            else:
                return False
        elif current != expected:
            return False
    return True


class _Col:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    async def find_one(self, query=None, projection=None, session=None, **kwargs):
        for doc in self.docs:
            if _match(doc, query or {}):
                return doc
        return None

    def find(self, query=None, projection=None):
        return _Cursor([doc for doc in self.docs if _match(doc, query or {})])

    async def update_one(self, query, update, upsert=False, session=None, **kwargs):
        doc = await self.find_one(query)
        if doc is None:
            return _Result(0)
        for key, value in (update.get("$inc") or {}).items():
            doc[key] = int(doc.get(key) or 0) + int(value)
        for key, value in (update.get("$set") or {}).items():
            _assign(doc, key, value)
        for key, value in (update.get("$push") or {}).items():
            bucket = doc.get(key)
            if not isinstance(bucket, list):
                bucket = []
                doc[key] = bucket
            bucket.append(value)
        for key in (update.get("$unset") or {}):
            _remove(doc, key)
        return _Result(1)

    async def delete_one(self, query, session=None, **kwargs):
        for index, doc in enumerate(self.docs):
            if _match(doc, query or {}):
                self.docs.pop(index)
                return _Result(1)
        return _Result(0)

    async def insert_one(self, doc, session=None, **kwargs):
        self.docs.append(doc)
        return None


def _world(monkeypatch, *, cosigner=""):
    indemnitor = INDEMNITOR if cosigner else NAME
    packet = {
        "packet_id": PACKET,
        "booking_number": BOOKING,
        "bond_case_id": CASE,
        "indemnitor_name": indemnitor,
        "docuseal_submission_id": SUBMISSION,
        "docuseal_sign_url": _sign("indemnitor") if cosigner else _sign("abc"),
        "docuseal_submitters": [
            _bound_submitter(
                "indemnitor",
                _sign("indemnitor") if cosigner else _sign("abc"),
                name=indemnitor,
            ),
        ],
        "status": "draft",
        "surety_id": "osi",
        "defendant_id": DEFENDANT_ID,
        "indemnitor_id": INDEMNITOR_ID,
        **CONFIRMED,
    }
    if cosigner:
        packet["docuseal_submitters"].append(
            _bound_submitter("coindemnitor", _sign("cosigner"), name=cosigner),
        )
    bond = {
        "bond_case_id": CASE,
        "booking_number": BOOKING,
        "status": "active",
        "indemnitor_name": indemnitor,
        "surety_id": "osi",
        "match_status": "validated",
        "case_number": "2026-TEST-001",
        "poa_number": "POA-TEST-COS-1",
        "defendant_id": DEFENDANT_ID,
        "indemnitor_id": INDEMNITOR_ID,
        "swipesimple_invoice_number": BOOKING,
        "premium_paid": False,
        **CONFIRMED,
    }
    if cosigner:
        packet["cosigner_name"] = cosigner
        bond["co_signer_name"] = cosigner
        people = [
            {"role": "indemnitor", "name": indemnitor, "indemnitor_id": INDEMNITOR_ID},
            {"role": "co_indemnitor", "name": cosigner, "indemnitor_id": CO_INDEMNITOR_ID},
        ]
        packet["indemnitors"] = people
        bond["indemnitors"] = people
    indemnitor_rows = [{"indemnitor_id": INDEMNITOR_ID, "Indemnitor_ID": INDEMNITOR_ID}]
    if cosigner:
        indemnitor_rows.append({
            "indemnitor_id": CO_INDEMNITOR_ID,
            "Indemnitor_ID": CO_INDEMNITOR_ID,
            "name": cosigner,
        })
    stores = {
        "paperwork_packets": _Col([packet]),
        "bond_cases": _Col([bond]),
        "defendants": _Col([{"defendant_id": DEFENDANT_ID, "Defendant_ID": DEFENDANT_ID}]),
        "indemnitors": _Col(indemnitor_rows),
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


def test_bond_stored_as_bond_case_id_alias_issues_a_link(monkeypatch):
    """A bond that only has Bond_Case_ID still gets a co-signer link."""
    stores, client = _world(monkeypatch)
    bond = stores["bond_cases"].docs[0]
    bond.pop("bond_case_id", None)
    bond["Bond_Case_ID"] = CASE
    token = _token(client)
    assert token
    assert "bond_case_id" not in bond
    link = stores["cosigner_links"].docs[0]
    assert link["token"] == token
    assert link["packet_id"] == PACKET
    assert link["bond_case_id"] == CASE


def test_bond_case_id_alias_revalidates_and_rejects_a_mismatch(monkeypatch):
    """Either case-id field keeps the link. A bond with neither id does not."""
    stores, client = _world(monkeypatch)
    bond = stores["bond_cases"].docs[0]
    bond.pop("bond_case_id", None)
    bond["Bond_Case_ID"] = CASE
    token = _token(client)
    packet = stores["paperwork_packets"].docs[0]
    packet.pop("bond_case_id", None)
    packet["Bond_Case_ID"] = CASE
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    state = client.get(f"/api/cosigner/{token}")
    assert state.status_code == 200, state.text
    assert state.json()["step"] == "scan"
    assert state.json()["sign_url"] is None
    page = client.get(f"/cosigner/{token}")
    assert page.status_code == 200
    assert "Link not found" not in page.text
    uploaded = _post_scan(client, token)
    assert uploaded.status_code == 200, uploaded.text
    assert calls["n"] == 1

    bond["bond_case_id"] = "BC-TEST-OTHER"
    still = client.get(f"/api/cosigner/{token}")
    assert still.status_code == 200, still.text
    assert still.json()["step"] == "sign"

    stores, client = _world(monkeypatch)
    bond = stores["bond_cases"].docs[0]
    bond.pop("bond_case_id", None)
    bond["Bond_Case_ID"] = CASE
    token = _token(client)
    packet = stores["paperwork_packets"].docs[0]
    packet["bond_case_id"] = ""
    bond["Bond_Case_ID"] = "BC-TEST-OTHER"
    bond["bond_case_id"] = "BC-TEST-OTHER-2"
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    refused = client.get(f"/api/cosigner/{token}")
    assert refused.status_code == 409
    assert refused.json()["error"] == "bond_mismatch"
    assert _sign_hidden(refused.text)
    page = client.get(f"/cosigner/{token}")
    assert page.status_code == 409
    assert "Link not found" in page.text
    scan = _post_scan(client, token)
    assert scan.status_code == 409
    assert _error_text(scan) == "bond_mismatch"
    assert calls["n"] == 0
    packet = stores["paperwork_packets"].docs[0]
    assert packet["packet_id"] == PACKET
    assert packet["status"] == "draft"
    assert "id_ocr" not in packet


def test_scan_writes_the_bond_named_by_bond_case_id(monkeypatch):
    """A Bond_Case_ID bond is updated. A sibling with the same booking is not."""
    stores, client = _world(monkeypatch)
    selected = stores["bond_cases"].docs[0]
    selected.pop("bond_case_id", None)
    selected["Bond_Case_ID"] = CASE
    other = {
        "bond_case_id": "BC-TEST-OTHER",
        "booking_number": BOOKING,
        "status": "active",
        "indemnitor_name": "Pat Other",
        "surety_id": "osi",
        "match_status": "validated",
        "case_number": "2026-TEST-002",
        "poa_number": "POA-TEST-COS-2",
        "premium_paid": False,
        **CONFIRMED,
    }
    stores["bond_cases"].docs.insert(0, other)
    seen = []
    original = stores["bond_cases"].update_one

    async def _watch(query, update, upsert=False, session=None, **kwargs):
        seen.append(query)
        return await original(query, update, upsert=upsert, session=session, **kwargs)

    stores["bond_cases"].update_one = _watch
    token = _token(client)
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    uploaded = _post_scan(client, token)
    assert uploaded.status_code == 200, uploaded.text
    assert calls["n"] == 1
    assert seen
    for query in seen:
        assert "booking_number" not in query
        assert CASE in str(query)
        assert "BC-TEST-OTHER" not in str(query)
    assert selected.get("id_ocr", {}).get("extracted", {}).get("full_name") == NAME
    assert "id_ocr" not in other
    assert not other.get("id_scan_records")


def test_poa_number_change_keeps_the_cosigner_link(monkeypatch):
    """A new POA number does not revoke the link or the packet."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    packet = stores["paperwork_packets"].docs[0]
    link = stores["cosigner_links"].docs[0]
    bond = stores["bond_cases"].docs[0]
    bond["poa_number"] = "POA-TEST-COS-2"
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    state = client.get(f"/api/cosigner/{token}")
    assert state.status_code == 200, state.text
    assert state.json()["step"] == "scan"
    assert state.json()["signing_locked"] is True
    assert state.json()["sign_url"] is None
    page = client.get(f"/cosigner/{token}")
    assert page.status_code == 200
    assert "Link not found" not in page.text
    uploaded = _post_scan(client, token)
    assert uploaded.status_code == 200, uploaded.text
    assert calls["n"] == 1
    assert packet["packet_id"] == PACKET
    assert packet["status"] == "draft"
    assert packet["bond_case_id"] == CASE
    assert packet["docuseal_sign_url"]
    assert link["token"] == token
    assert link.get("revoked") is not True
    assert bond["poa_number"] == "POA-TEST-COS-2"
    assert bond["status"] == "active"


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

    _age_scan_cooldown(stores, token)
    matched = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL", "dl_number": "D12345678"},
    )
    assert matched.status_code == 200, matched.text
    assert "DocuSeal" in matched.text
    assert "D12345678" not in matched.text
    body = client.get(f"/api/cosigner/{token}").json()
    assert body["step"] == "sign"
    assert body["sign_url"] == _sign("abc")
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

    pay_db = _install_payment_lock(monkeypatch)

    async def _record(db, **kwargs):
        assert db is pay_db
        calls["n"] += 1
        _remember_payment_row(db, **kwargs)
        return {
            "committed": calls["n"] == 1,
            "duplicate": calls["n"] > 1,
            "needs_review": False,
            "transaction_id": kwargs.get("transaction_id"),
            "event_kind": "charge",
            "amount": kwargs.get("amount"),
        }

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
    _age_scan_cooldown(stores, token)
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
        _age_scan_cooldown(stores, token)
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
    assert body["sign_url"] == _sign("abc")
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
    packet["docuseal_submission_id"] = SUBMISSION
    packet["docuseal_sign_url"] = _sign("indemnitor")
    packet["docuseal_submitters"] = [
        _bound_submitter("indemnitor", _sign("indemnitor"), name=INDEMNITOR),
        _bound_submitter("coindemnitor", _sign("cosigner"), name=NAME),
    ]
    indemnitor_id = _upload(
        monkeypatch, client, token,
        {"full_name": INDEMNITOR, "issuing_state": "FL"},
    )
    assert indemnitor_id.status_code == 409
    assert _error_text(indemnitor_id) == "name_mismatch"
    _locked(client, stores, token)

    _age_scan_cooldown(stores, token)
    country = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "issuing_country": "USA"},
    )
    assert country.status_code == 400
    assert _error_text(country) == "no_issuing_state"
    _locked(client, stores, token)

    _age_scan_cooldown(stores, token)
    matched = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "issuing_state": "New York"},
    )
    assert matched.status_code == 200, matched.text
    body = client.get(f"/api/cosigner/{token}").json()
    assert body["step"] == "sign"
    assert body["sign_url"] == _sign("cosigner")
    assert _sign("indemnitor") not in matched.text
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
    packet["docuseal_submission_id"] = SUBMISSION
    packet["docuseal_sign_url"] = _sign("indemnitor")
    packet["docuseal_submitters"] = [
        _bound_submitter("indemnitor", _sign("indemnitor"), name=INDEMNITOR),
        _bound_submitter("coindemnitor", _sign("cosigner"), name=NAME),
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
    assert state["sign_url"] == _sign("cosigner")

    packet["invoice_payment_link"] = _swipe_link("down")
    packet["swipesimple_invoice_amount_cents"] = 50000
    packet["swipesimple_invoice_number"] = BOOKING
    packet["swipesimple_invoice_bond_id"] = CASE
    packet["status"] = "completed"
    paying = client.get(f"/api/cosigner/{token}").json()
    assert paying["step"] == "pay"
    assert paying["payment"]["url"] == _swipe_link("down")
    assert paying["payment"]["dispatched"] is False
    assert "error" not in paying["payment"]


def test_unproven_invoice_amount_hides_the_pay_link(monkeypatch):
    """A stored invoice URL is shown only when its cents equal the amount due."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    packet = stores["paperwork_packets"].docs[0]
    packet["status"] = "completed"
    packet["invoice_payment_link"] = _swipe_link("down")
    packet["swipesimple_invoice_amount_cents"] = 30000

    short = client.get(f"/api/cosigner/{token}")
    assert short.status_code == 200, short.text
    assert short.json()["step"] == "pay"
    assert short.json()["payment"]["url"] is None
    assert short.json()["payment"]["error"] == "invoice_amount_unproven"
    assert short.json()["payment"]["amount"] == "500.00"
    assert _swipe_link("down") not in short.text
    page = client.get(f"/cosigner/{token}")
    assert page.status_code == 200
    assert _swipe_link("down") not in page.text
    assert ">Pay</a>" not in page.text

    packet["swipesimple_invoice_amount_cents"] = 50000
    packet["swipesimple_invoice_number"] = BOOKING
    packet["swipesimple_invoice_bond_id"] = CASE
    matched = client.get(f"/api/cosigner/{token}")
    assert matched.status_code == 200, matched.text
    assert matched.json()["payment"]["url"] == _swipe_link("down")
    assert "error" not in matched.json()["payment"]
    assert _swipe_link("down") in client.get(f"/cosigner/{token}").text

    packet.pop("swipesimple_invoice_amount_cents", None)
    missing = client.get(f"/api/cosigner/{token}")
    assert missing.status_code == 200, missing.text
    assert missing.json()["payment"]["url"] is None
    assert missing.json()["payment"]["error"] == "invoice_amount_unproven"
    assert _swipe_link("down") not in missing.text
    assert _swipe_link("down") not in client.get(f"/cosigner/{token}").text


def test_invoice_url_must_belong_to_this_bond(monkeypatch):
    """A matching amount is not enough. The invoice must be this bond."""
    foreign = _swipe_link("other-bond")
    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    packet = stores["paperwork_packets"].docs[0]
    packet["status"] = "completed"
    packet["invoice_payment_link"] = foreign
    packet["swipesimple_invoice_amount_cents"] = 50000
    packet["swipesimple_invoice_number"] = "TEST-COS-OTHER"
    packet["swipesimple_invoice_bond_id"] = "BC-TEST-OTHER"

    def _hidden(body):
        assert body.status_code == 200, body.text
        payload = body.json()
        assert payload["step"] in {"pay", "paid"}
        assert payload["payment"]["url"] is None
        assert payload["payment"]["error"] == "invoice_unbound"
        assert payload["payment"]["amount"] == "500.00"
        assert foreign not in body.text
        page = client.get(f"/cosigner/{token}")
        assert page.status_code == 200
        assert foreign not in page.text
        assert ">Pay</a>" not in page.text

    _hidden(client.get(f"/api/cosigner/{token}"))

    packet["swipesimple_invoice_number"] = BOOKING
    _hidden(client.get(f"/api/cosigner/{token}"))

    packet.pop("swipesimple_invoice_number", None)
    packet["swipesimple_invoice_bond_id"] = CASE
    _hidden(client.get(f"/api/cosigner/{token}"))

    packet["swipesimple_invoice_number"] = BOOKING
    owned = client.get(f"/api/cosigner/{token}")
    assert owned.status_code == 200, owned.text
    assert owned.json()["step"] == "pay"
    assert owned.json()["payment"]["url"] == foreign
    assert "error" not in owned.json()["payment"]
    assert foreign in client.get(f"/cosigner/{token}").text

    packet["swipesimple_invoice_amount_cents"] = 30000
    packet["swipesimple_invoice_bond_id"] = "BC-TEST-OTHER"
    short = client.get(f"/api/cosigner/{token}")
    assert short.status_code == 200, short.text
    assert short.json()["payment"]["url"] is None
    assert short.json()["payment"]["error"] == "invoice_amount_unproven"
    assert foreign not in short.text

    packet.pop("invoice_payment_link", None)
    packet["swipesimple_invoice_amount_cents"] = 50000
    bond = stores["bond_cases"].docs[0]
    bond["invoice_payment_link"] = foreign
    bond["swipesimple_invoice_amount_cents"] = 50000
    bond["swipesimple_invoice_number"] = BOOKING
    bond["swipesimple_invoice_bond_id"] = "BC-TEST-OTHER"
    _hidden(client.get(f"/api/cosigner/{token}"))

    stores, client = _world(monkeypatch, cosigner=NAME)
    token = _token(client)
    uploaded = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert uploaded.status_code == 200, uploaded.text
    packet = stores["paperwork_packets"].docs[0]
    packet["status"] = "completed"
    packet["invoice_payment_link"] = foreign
    packet["swipesimple_invoice_amount_cents"] = 50000
    packet["swipesimple_invoice_number"] = "TEST-COS-OTHER"
    packet["swipesimple_invoice_bond_id"] = "BC-TEST-OTHER"
    cosigned = client.get(f"/api/cosigner/{token}")
    assert cosigned.status_code == 200, cosigned.text
    assert cosigned.json()["payment"]["url"] is None
    assert cosigned.json()["payment"]["error"] == "invoice_unbound"
    assert foreign not in cosigned.text
    assert foreign not in client.get(f"/cosigner/{token}").text

    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    packet = stores["paperwork_packets"].docs[0]
    packet["status"] = "completed"
    packet["invoice_payment_link"] = foreign
    packet["swipesimple_invoice_amount_cents"] = 50000
    packet["swipesimple_invoice_number"] = "TEST-COS-OTHER"
    packet["swipesimple_invoice_bond_id"] = "BC-TEST-OTHER"
    stores["bond_cases"].docs[0]["premium_paid"] = True
    paid = client.get(f"/api/cosigner/{token}")
    assert paid.status_code == 200, paid.text
    assert paid.json()["step"] == "paid"
    assert paid.json()["payment"]["url"] is None
    assert paid.json()["payment"]["error"] == "invoice_unbound"
    assert foreign not in paid.text
    assert foreign not in client.get(f"/cosigner/{token}").text


def test_off_site_invoice_url_is_hidden(monkeypatch):
    """A matching invoice on another https host is not a pay URL."""
    offsite = "https://evil.example/pay"
    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    packet = stores["paperwork_packets"].docs[0]
    packet["status"] = "completed"
    packet["invoice_payment_link"] = offsite
    packet["swipesimple_invoice_amount_cents"] = 50000
    packet["swipesimple_invoice_number"] = BOOKING
    packet["swipesimple_invoice_bond_id"] = CASE

    hidden = client.get(f"/api/cosigner/{token}")
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["payment"]["url"] is None
    assert hidden.json()["payment"]["error"] == "invoice_origin"
    assert hidden.json()["payment"]["amount"] == "500.00"
    assert "evil.example" not in hidden.text
    page = client.get(f"/cosigner/{token}")
    assert page.status_code == 200
    assert "evil.example" not in page.text
    assert ">Pay</a>" not in page.text

    owned = _swipe_link("down")
    packet["invoice_payment_link"] = owned
    shown = client.get(f"/api/cosigner/{token}")
    assert shown.status_code == 200, shown.text
    assert shown.json()["payment"]["url"] == owned
    assert "error" not in shown.json()["payment"]
    assert owned in client.get(f"/cosigner/{token}").text


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


def _age_scan_cooldown(stores, token=None):
    """Place the last attempt outside the cooldown so another upload can run."""
    from datetime import datetime, timedelta, timezone

    from dashboard.services.cosigner_link_service import SCAN_COOLDOWN

    stale = datetime.now(timezone.utc) - SCAN_COOLDOWN - timedelta(seconds=1)
    links = stores.get("cosigner_links")
    if links is None:
        return
    for link in links.docs:
        if token is not None and link.get("token") != token:
            continue
        if link.get("scan_attempts"):
            link["scan_attempted_at"] = stale


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


def test_scan_attempts_stop_before_another_ocr_charge(monkeypatch):
    """A failed scan counts, the next one waits, and the cap does not call OCR."""
    from datetime import datetime, timedelta, timezone

    from dashboard.services.cosigner_link_service import SCAN_ATTEMPT_CAP, SCAN_COOLDOWN

    stores, client = _world(monkeypatch)
    token = _token(client)
    calls = {"n": 0}

    async def _mismatch(image_bytes, filename=""):
        calls["n"] += 1
        return {
            "success": True,
            "extracted": {"full_name": "Jamie O'Neill", "dl_state": "FL"},
        }

    monkeypatch.setattr(
        "dashboard.services.id_scanner_service.IDScannerService.scan_id_image",
        _mismatch,
    )
    typed = client.post(f"/api/cosigner/{token}/scan", json={"full_name": NAME})
    assert typed.status_code == 400
    assert typed.json()["error"] == "scan_image_required"
    assert calls["n"] == 0
    link = stores["cosigner_links"].docs[0]
    assert not link.get("scan_attempts")

    failed = _post_scan(client, token)
    assert failed.status_code == 409
    assert _error_text(failed) == "name_mismatch"
    assert calls["n"] == 1
    assert link["scan_attempts"] == 1

    cooled = _post_scan(client, token)
    assert cooled.status_code == 429
    assert cooled.json()["error"] == "scan_cooldown"
    assert calls["n"] == 1
    assert link["scan_attempts"] == 1
    assert _audits(stores, "scan_attempt_cap") == []

    stale = datetime.now(timezone.utc) - SCAN_COOLDOWN - timedelta(seconds=1)
    for expected in range(2, SCAN_ATTEMPT_CAP + 1):
        link["scan_attempted_at"] = stale
        again = _post_scan(client, token)
        assert again.status_code == 409, expected
        assert _error_text(again) == "name_mismatch"
        assert link["scan_attempts"] == expected
        assert calls["n"] == expected

    link["scan_attempted_at"] = stale
    blocked = _post_scan(client, token)
    assert blocked.status_code == 429
    assert blocked.json()["error"] == "scan_attempt_cap"
    assert calls["n"] == SCAN_ATTEMPT_CAP
    assert link["scan_attempts"] == SCAN_ATTEMPT_CAP
    audits = _audits(stores, "scan_attempt_cap")
    assert len(audits) == 1
    assert audits[0]["packet_id"] == PACKET
    assert token not in str(audits[0])

    async def _match(image_bytes, filename=""):
        calls["n"] += 1
        return {
            "success": True,
            "extracted": {"full_name": NAME, "dl_state": "FL"},
        }

    monkeypatch.setattr(
        "dashboard.services.id_scanner_service.IDScannerService.scan_id_image",
        _match,
    )
    fresh = _token(client)
    opened = _post_scan(client, fresh)
    assert opened.status_code == 200, opened.text
    fresh_link = next(row for row in stores["cosigner_links"].docs if row["token"] == fresh)
    assert fresh_link["scan_attempts"] == 1
    assert fresh_link["id_verified"] is True
    assert calls["n"] == SCAN_ATTEMPT_CAP + 1


def test_voided_packet_hides_the_sign_url_and_refuses_scan(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL", "dl_number": "D12345678"},
    )
    assert opened.status_code == 200
    assert client.get(f"/api/cosigner/{token}").json()["sign_url"] == _sign("abc")

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
        assert _sign_hidden(state.text)
        page = client.get(f"/cosigner/{token}")
        assert _sign_hidden(page.text)
        refused = _post_scan(client, token)
        assert refused.status_code == 409, marker
        assert refused.json()["error"] == "packet_inactive"
        assert _sign_hidden(refused.text)
    assert calls["n"] == 0


def test_unbound_docuseal_url_is_not_returned(monkeypatch):
    """A stored signer URL is returned only when it is bound to this packet."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    assert _sign("abc") in opened.text
    ready = client.get(f"/api/cosigner/{token}")
    assert ready.status_code == 200
    assert ready.json()["sign_url"] == _sign("abc")

    packet = stores["paperwork_packets"].docs[0]
    row = packet["docuseal_submitters"][0]
    good_url = row["sign_url"]
    good_submission = row["submission_id"]
    good_external = row["external_id"]

    def _refuses(stored):
        state = client.get(f"/api/cosigner/{token}")
        assert state.status_code == 409
        assert state.json() == {"error": "sign_link_unbound"}
        assert stored not in state.text
        assert _sign_hidden(state.text)
        page = client.get(f"/cosigner/{token}")
        assert page.status_code == 409
        assert stored not in page.text
        assert _sign_hidden(page.text)

    row["sign_url"] = "https://evil.example/s/abc"
    packet["docuseal_sign_url"] = "https://evil.example/s/abc"
    _refuses("https://evil.example/s/abc")

    row["sign_url"] = good_url
    packet["docuseal_sign_url"] = good_url
    row["submission_id"] = "DS-TEST-OTHER"
    row["metadata"]["submission_id"] = "DS-TEST-OTHER"
    _refuses(good_url)

    row["submission_id"] = good_submission
    row["metadata"]["submission_id"] = good_submission
    row["external_id"] = f"{PACKET}:defendant"
    _refuses(good_url)

    row["external_id"] = good_external
    restored = client.get(f"/api/cosigner/{token}")
    assert restored.status_code == 200
    assert restored.json()["sign_url"] == good_url


def test_sign_url_matches_the_verified_signer(monkeypatch):
    """A bound DocuSeal row is returned only for this link's verified signer."""
    stores, client = _world(monkeypatch, cosigner=NAME)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "issuing_state": "NY"},
    )
    assert opened.status_code == 200, opened.text
    ready = client.get(f"/api/cosigner/{token}")
    assert ready.status_code == 200
    assert ready.json()["sign_url"] == _sign("cosigner")
    assert _sign("indemnitor") not in ready.text

    packet = stores["paperwork_packets"].docs[0]
    row = next(
        item for item in packet["docuseal_submitters"]
        if item.get("role") == "coindemnitor"
    )
    row["name"] = "Pat Other"
    mismatched = client.get(f"/api/cosigner/{token}")
    assert mismatched.status_code == 409
    assert mismatched.json() == {"error": "sign_link_mismatch"}
    assert _sign("cosigner") not in mismatched.text
    assert _sign_hidden(mismatched.text)
    page = client.get(f"/cosigner/{token}")
    assert _sign_hidden(page.text)

    packet["docuseal_submitters"] = [
        _bound_submitter("indemnitor", _sign("indemnitor"), name=INDEMNITOR),
    ]
    packet["docuseal_sign_url"] = _sign("indemnitor")
    stale = client.get(f"/api/cosigner/{token}")
    assert stale.status_code == 409
    assert stale.json()["error"] == "sign_link_unbound"
    assert _sign("indemnitor") not in stale.text
    assert _sign_hidden(stale.text)
    assert _sign_hidden(client.get(f"/cosigner/{token}").text)

    packet["docuseal_submitters"] = [
        _bound_submitter("indemnitor", _sign("indemnitor"), name=INDEMNITOR),
        _bound_submitter("coindemnitor", _sign("cosigner"), name=NAME),
    ]
    restored = client.get(f"/api/cosigner/{token}")
    assert restored.status_code == 200
    assert restored.json()["sign_url"] == _sign("cosigner")

    row = packet["docuseal_submitters"][1]
    row["email"] = "other@example.invalid"
    stores["bond_cases"].docs[0]["co_signer_email"] = "signer@example.invalid"
    emailed = client.get(f"/api/cosigner/{token}")
    assert emailed.status_code == 409
    assert emailed.json() == {"error": "sign_link_mismatch"}
    assert _sign("cosigner") not in emailed.text


def test_coindemnitor_email_blocks_a_different_docuseal_row(monkeypatch):
    """The kiosk coindemnitor_email alias rejects another recipient's URL."""
    stores, client = _world(monkeypatch, cosigner=NAME)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "issuing_state": "NY"},
    )
    assert opened.status_code == 200, opened.text
    packet = stores["paperwork_packets"].docs[0]
    bond = stores["bond_cases"].docs[0]
    for doc in (packet, bond):
        doc.pop("co_signer_email", None)
        doc.pop("cosigner_email", None)
        doc.pop("co_indemnitor_email", None)
        for person in doc.get("indemnitors") or []:
            if isinstance(person, dict):
                person.pop("email", None)
    packet["coindemnitor_email"] = "signer@example.invalid"
    row = next(
        item for item in packet["docuseal_submitters"]
        if item.get("role") == "coindemnitor"
    )
    row["email"] = "other@example.invalid"
    mismatched = client.get(f"/api/cosigner/{token}")
    assert mismatched.status_code == 409
    assert mismatched.json() == {"error": "sign_link_mismatch"}
    assert _sign("cosigner") not in mismatched.text
    assert _sign_hidden(mismatched.text)
    row["email"] = "signer@example.invalid"
    matched = client.get(f"/api/cosigner/{token}")
    assert matched.status_code == 200, matched.text
    assert matched.json()["sign_url"] == _sign("cosigner")


def test_pay_step_hides_an_unbound_sign_url(monkeypatch):
    """After signing, a bad DocuSeal URL does not block the invoice page."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    packet = stores["paperwork_packets"].docs[0]
    row = packet["docuseal_submitters"][0]
    row["sign_url"] = "https://evil.example/s/abc"
    packet["docuseal_sign_url"] = "https://evil.example/s/abc"
    locked = client.get(f"/api/cosigner/{token}")
    assert locked.status_code == 409
    assert locked.json()["error"] == "sign_link_unbound"
    assert "evil.example" not in locked.text

    packet["status"] = "completed"
    paying = client.get(f"/api/cosigner/{token}")
    assert paying.status_code == 200, paying.text
    assert paying.json()["step"] == "pay"
    assert paying.json()["sign_url"] is None
    assert "evil.example" not in paying.text
    assert _sign_hidden(paying.text)
    page = client.get(f"/cosigner/{token}")
    assert page.status_code == 200
    assert "evil.example" not in page.text
    assert "500.00" in page.text

    stores["bond_cases"].docs[0]["premium_paid"] = True
    paid = client.get(f"/api/cosigner/{token}")
    assert paid.status_code == 200, paid.text
    assert paid.json()["step"] == "paid"
    assert paid.json()["sign_url"] is None
    assert "evil.example" not in paid.text


def test_signed_signer_mismatch_keeps_the_invoice_link(monkeypatch):
    """After signing, a name or email mismatch does not hide the invoice."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    packet = stores["paperwork_packets"].docs[0]
    packet["status"] = "completed"
    packet["invoice_payment_link"] = _swipe_link("down")
    packet["swipesimple_invoice_amount_cents"] = 50000
    packet["swipesimple_invoice_number"] = BOOKING
    packet["swipesimple_invoice_bond_id"] = CASE
    bond = stores["bond_cases"].docs[0]
    row = packet["docuseal_submitters"][0]

    def _pay_keeps_invoice(body):
        assert body.status_code == 200, body.text
        payload = body.json()
        assert payload["step"] == "pay"
        assert payload["sign_url"] is None
        assert payload["payment"]["url"] == _swipe_link("down")
        assert _sign("abc") not in body.text
        assert _sign_hidden(body.text)
        page = client.get(f"/cosigner/{token}")
        assert page.status_code == 200
        assert _sign_hidden(page.text)
        assert _swipe_link("down") in page.text

    row["name"] = "Pat Other"
    _pay_keeps_invoice(client.get(f"/api/cosigner/{token}"))

    row["name"] = NAME
    row["email"] = "other@example.invalid"
    bond["indemnitor_email"] = "signer@example.invalid"
    _pay_keeps_invoice(client.get(f"/api/cosigner/{token}"))

    bond["premium_paid"] = True
    paid = client.get(f"/api/cosigner/{token}")
    assert paid.status_code == 200, paid.text
    assert paid.json()["step"] == "paid"
    assert paid.json()["sign_url"] is None
    assert paid.json()["payment"]["url"] == _swipe_link("down")
    assert _sign("abc") not in paid.text
    assert _sign_hidden(paid.text)
    page = client.get(f"/cosigner/{token}")
    assert page.status_code == 200
    assert _sign_hidden(page.text)
    assert ">Pay</a>" not in page.text


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
    assert _sign_hidden(state.text)
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
    assert calls["n"] == 0
    assert "id_ocr" not in fresh["paperwork_packets"].docs[0]
    assert "id_ocr" not in fresh["bond_cases"].docs[0]


def _legacy_booking_only(bond):
    """Store the bond's booking only under the legacy ``Booking_Number``."""
    bond.pop("booking_number", None)
    bond["Booking_Number"] = BOOKING
    return bond


def test_legacy_booking_bond_without_packet_case_id_gets_a_link(monkeypatch):
    """A packet with no case id finds a bond stored only as ``Booking_Number``."""
    stores, client = _world(monkeypatch)
    bond = _legacy_booking_only(stores["bond_cases"].docs[0])
    stores["paperwork_packets"].docs[0]["bond_case_id"] = ""
    token = _token(client)
    link = stores["cosigner_links"].docs[0]
    assert link["token"] == token
    assert link["booking_number"] == BOOKING
    assert link["bond_case_id"] == CASE
    assert "booking_number" not in bond
    state = client.get(f"/api/cosigner/{token}")
    assert state.status_code == 200, state.text
    assert state.json()["step"] == "scan"
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    uploaded = _post_scan(client, token)
    assert uploaded.status_code == 200, uploaded.text
    assert calls["n"] == 1
    assert bond["id_ocr"]["extracted"]["full_name"] == NAME
    assert "booking_number" not in bond
    assert bond["Booking_Number"] == BOOKING


def test_legacy_booking_bond_without_any_case_id_scans_onto_that_bond(monkeypatch):
    """The scan write filters on the legacy booking and ``_id``, not a missing field."""
    stores, client = _world(monkeypatch)
    bond = _legacy_booking_only(stores["bond_cases"].docs[0])
    bond.pop("bond_case_id", None)
    bond["_id"] = "BOND-LEGACY"
    decoy = {
        "_id": "BOND-DECOY",
        "Booking_Number": "TEST-COS-DECOY",
        "status": "active",
        "indemnitor_name": "Pat Other",
    }
    stores["bond_cases"].docs.insert(0, decoy)
    stores["paperwork_packets"].docs[0]["bond_case_id"] = ""
    token = _token(client)
    link = stores["cosigner_links"].docs[0]
    assert link["booking_number"] == BOOKING
    assert link["bond_case_id"] == ""
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    uploaded = _post_scan(client, token)
    assert uploaded.status_code == 200, uploaded.text
    assert bond["id_ocr"]["extracted"]["full_name"] == NAME
    assert "id_ocr" not in decoy
    assert "id_scan_records" not in decoy
    assert "booking_number" not in bond


def test_booking_shared_across_both_spellings_fails_closed(monkeypatch):
    """A lowercase bond and a legacy bond on one booking are two cases."""
    stores, client = _world(monkeypatch)
    other = _legacy_booking_only({
        "bond_case_id": "BC-TEST-OTHER",
        "status": "active",
        "indemnitor_name": NAME,
        "surety_id": "osi",
        "premium_paid": False,
        **CONFIRMED,
    })
    stores["bond_cases"].docs.append(other)
    stores["paperwork_packets"].docs[0]["bond_case_id"] = ""
    created = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert created.status_code == 409, created.text
    assert _error_text(created) == "bond_ambiguous"
    assert not _links(stores)

    stores, client = _world(monkeypatch)
    _legacy_booking_only(stores["bond_cases"].docs[0])
    stores["active_bonds"] = _Col([{
        "bond_case_id": "BC-TEST-OTHER",
        "booking_number": BOOKING,
        "status": "active",
        "indemnitor_name": NAME,
    }])
    stores["paperwork_packets"].docs[0]["bond_case_id"] = ""
    created = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert created.status_code == 409, created.text
    assert _error_text(created) == "bond_ambiguous"
    assert not _links(stores)


def test_bond_with_both_booking_spellings_counts_once(monkeypatch):
    """One document under both fields is one bond, even if a find returns it twice."""
    from dashboard.services import cosigner_link_service as svc

    stores, client = _world(monkeypatch)
    bond = stores["bond_cases"].docs[0]
    bond["_id"] = "BOND-BOTH"
    bond["Booking_Number"] = BOOKING
    stores["paperwork_packets"].docs[0]["bond_case_id"] = ""
    token = _token(client)
    assert stores["cosigner_links"].docs[0]["token"] == token
    assert stores["cosigner_links"].docs[0]["bond_case_id"] == CASE

    class _Twice(_Col):
        def find(self, query=None, projection=None):
            rows = [doc for doc in self.docs if _match(doc, query or {})]
            return _Cursor(rows + rows)

    twice = {"bond_cases": _Twice([bond]), "active_bonds": _Col()}
    monkeypatch.setattr(svc, "get_collection", lambda name: twice.get(name) or _Col())
    found = asyncio.run(svc._bonds_for_booking(BOOKING))
    assert [doc["_id"] for doc in found] == ["BOND-BOTH"]
    assert asyncio.run(svc._one_case_id(BOOKING)) == CASE


def test_stale_legacy_booking_does_not_match_a_reassigned_bond(monkeypatch):
    """``booking_number`` is canonical. A stale ``Booking_Number`` is ignored."""
    from dashboard.services import cosigner_link_service as svc

    stores, client = _world(monkeypatch)
    stale = {
        "bond_case_id": "BC-TEST-STALE",
        "booking_number": "TEST-COS-REASSIGNED",
        "Booking_Number": BOOKING,
        "status": "active",
        "indemnitor_name": NAME,
    }
    stores["bond_cases"].docs.insert(0, stale)
    stores["paperwork_packets"].docs[0]["bond_case_id"] = ""
    token = _token(client)
    link = stores["cosigner_links"].docs[0]
    assert link["token"] == token
    assert link["bond_case_id"] == CASE
    assert asyncio.run(svc._one_case_id(BOOKING)) == CASE

    stores, client = _world(monkeypatch)
    bond = stores["bond_cases"].docs[0]
    bond["Booking_Number"] = BOOKING
    bond["booking_number"] = "TEST-COS-REASSIGNED"
    stores["paperwork_packets"].docs[0]["bond_case_id"] = ""
    created = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert created.status_code == 409, created.text
    assert _error_text(created) == "bond_required"
    assert not _links(stores)
    assert asyncio.run(svc._one_case_id(BOOKING)) == ""


def test_lowercase_booking_bond_without_packet_case_id_is_unchanged(monkeypatch):
    """The lowercase booking fallback still issues the link and resolves the case."""
    from dashboard.services import cosigner_link_service as svc

    stores, client = _world(monkeypatch)
    bond = stores["bond_cases"].docs[0]
    stores["paperwork_packets"].docs[0]["bond_case_id"] = ""
    token = _token(client)
    link = stores["cosigner_links"].docs[0]
    assert link["token"] == token
    assert link["booking_number"] == BOOKING
    assert link["bond_case_id"] == CASE
    assert "Booking_Number" not in bond
    assert client.get(f"/api/cosigner/{token}").status_code == 200
    assert asyncio.run(svc._one_case_id(BOOKING)) == CASE


def test_legacy_booking_bond_link_is_stamped_paid(monkeypatch):
    """The paid stamp resolves a legacy-booking bond's case and marks its link."""
    from dashboard.services.cosigner_link_service import note_cosigner_paid

    stores, client = _world(monkeypatch)
    _legacy_booking_only(stores["bond_cases"].docs[0])
    stores["paperwork_packets"].docs[0]["bond_case_id"] = ""
    token = _token(client)
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    assert _post_scan(client, token).status_code == 200
    stores["paperwork_packets"].docs[0]["status"] = "completed"
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    pay_db = _install_payment_lock(monkeypatch)
    _remember_payment_row(
        pay_db,
        transaction_id="TX-LEGACY",
        amount="500.00",
        status="approved",
    )
    asyncio.run(note_cosigner_paid(BOOKING, "TX-LEGACY", "500.00"))
    assert stores["cosigner_links"].docs[0]["paid_transaction_id"] == "TX-LEGACY"
    assert len(_audits(stores, "paid")) == 1
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "paid"


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

    pay_db = _install_payment_lock(monkeypatch)
    _remember_payment_row(
        pay_db,
        transaction_id="TX-PAY",
        amount="500.00",
        status="approved",
    )
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


def _drop_party_ids(doc, *keys):
    for key in keys:
        doc.pop(key, None)


def _clear_co_indemnitor_ids(doc):
    for person in doc.get("indemnitors") or []:
        if str(person.get("role") or "").replace("-", "_") in {
            "co_indemnitor", "coindemnitor", "cosigner", "co_signer",
        }:
            person.pop("indemnitor_id", None)
            person.pop("Indemnitor_ID", None)
            person.pop("id", None)
    for key in ("co_indemnitor_id", "coindemnitor_id", "Co_Indemnitor_ID"):
        doc.pop(key, None)


def test_cosigner_bound_to_their_own_indemnitor_record(monkeypatch):
    """A co-signer with their own indemnitor row can sign. The pay link stays hidden."""
    stores, client = _world(monkeypatch, cosigner=NAME)
    token = _token(client)
    assert stores["cosigner_links"].docs[0]["signer_role"] == "co_indemnitor"
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    signing = client.get(f"/api/cosigner/{token}")
    assert signing.status_code == 200, signing.text
    assert signing.json()["step"] == "sign"
    assert signing.json()["sign_url"] == _sign("cosigner")
    assert signing.json()["payment"] is None
    assert _swipe_link("down") not in signing.text

    for person in stores["bond_cases"].docs[0]["indemnitors"]:
        if person.get("role") == "co_indemnitor":
            person["indemnitor_id"] = INDEMNITOR_ID
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    refused = client.get(f"/api/cosigner/{token}")
    assert refused.status_code == 409
    assert refused.json()["error"] == "co_indemnitor_mismatch"
    assert _sign_hidden(refused.text)
    assert _swipe_link("down") not in refused.text
    scan = _post_scan(client, token)
    assert scan.status_code == 409
    assert calls["n"] == 0


def test_cosigner_without_their_own_record_gets_no_link(monkeypatch):
    """A co-signer named on the bond, with no id of their own, gets no link."""
    stores, client = _world(monkeypatch, cosigner=NAME)
    packet = stores["paperwork_packets"].docs[0]
    bond = stores["bond_cases"].docs[0]
    _clear_co_indemnitor_ids(packet)
    _clear_co_indemnitor_ids(bond)
    stores["indemnitors"].docs = [
        row for row in stores["indemnitors"].docs
        if row.get("indemnitor_id") != CO_INDEMNITOR_ID
    ]
    refused = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert refused.status_code == 409
    assert refused.json() == {"error": "co_indemnitor_required"}
    assert "token" not in refused.json()
    assert _links(stores) == []
    assert _sign("cosigner") not in refused.text


def test_cosigner_record_of_the_primary_indemnitor_gets_no_link(monkeypatch):
    """The primary indemnitor id is not the co-signer's record."""
    stores, client = _world(monkeypatch, cosigner=NAME)
    for person in stores["bond_cases"].docs[0]["indemnitors"]:
        if person.get("role") == "co_indemnitor":
            person["indemnitor_id"] = INDEMNITOR_ID
    refused = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert refused.status_code == 409
    assert refused.json() == {"error": "co_indemnitor_mismatch"}
    assert "token" not in refused.json()
    assert _links(stores) == []
    assert _sign("cosigner") not in refused.text
    assert _swipe_link("down") not in refused.text


def test_cosigner_record_must_name_the_selected_signer(monkeypatch):
    """The packet and the bond say Alice Sample. Another record gets no link."""
    stores, client = _world(monkeypatch, cosigner="Alice Sample")
    for row in stores["indemnitors"].docs:
        if row.get("indemnitor_id") == CO_INDEMNITOR_ID:
            row["name"] = "Bob Other"
    refused = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert refused.status_code == 409
    assert refused.json() == {"error": "co_indemnitor_mismatch"}
    assert "token" not in refused.json()
    assert _links(stores) == []
    assert _sign("cosigner") not in refused.text
    assert _swipe_link("down") not in refused.text


def test_conflicting_signer_emails_get_no_sign_link(monkeypatch):
    """A bond email and a different packet email do not choose the bond."""
    stores, client = _world(monkeypatch)
    packet = stores["paperwork_packets"].docs[0]
    bond = stores["bond_cases"].docs[0]
    packet["indemnitor_email"] = "packet@example.invalid"
    bond["indemnitor_email"] = "bond@example.invalid"
    refused = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert refused.status_code == 409
    assert refused.json() == {"error": "signer_email_mismatch"}
    assert "token" not in refused.json()
    assert _links(stores) == []
    assert _sign("abc") not in refused.text
    assert _swipe_link("down") not in refused.text

    packet.pop("indemnitor_email")
    bond.pop("indemnitor_email")
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    packet["indemnitor_email"] = "packet@example.invalid"
    bond["indemnitor_email"] = "bond@example.invalid"
    blocked = client.get(f"/api/cosigner/{token}")
    assert blocked.status_code == 409
    assert blocked.json() == {"error": "signer_email_mismatch"}
    assert _sign("abc") not in blocked.text
    assert _sign_hidden(blocked.text)
    assert _swipe_link("down") not in blocked.text
    page = client.get(f"/cosigner/{token}")
    assert _sign_hidden(page.text)
    assert _swipe_link("down") not in page.text


def test_matching_or_one_sided_signer_email_still_signs(monkeypatch):
    """The same email, or an email on only one side, still opens the sign step."""
    stores, client = _world(monkeypatch)
    packet = stores["paperwork_packets"].docs[0]
    bond = stores["bond_cases"].docs[0]
    packet["indemnitor_email"] = "  Signer@Example.invalid "
    bond["indemnitor_email"] = "signer@example.invalid"
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    matched = client.get(f"/api/cosigner/{token}")
    assert matched.status_code == 200, matched.text
    assert matched.json()["step"] == "sign"
    assert matched.json()["sign_url"] == _sign("abc")
    assert matched.json()["payment"] is None

    packet.pop("indemnitor_email")
    bond_only = client.get(f"/api/cosigner/{token}")
    assert bond_only.status_code == 200, bond_only.text
    assert bond_only.json()["sign_url"] == _sign("abc")

    bond.pop("indemnitor_email")
    packet["indemnitor_email"] = "signer@example.invalid"
    packet_only = client.get(f"/api/cosigner/{token}")
    assert packet_only.status_code == 200, packet_only.text
    assert packet_only.json()["sign_url"] == _sign("abc")
    assert packet_only.json()["payment"] is None


def test_link_requires_bound_party_records(monkeypatch):
    """No public link until the packet and bond name the same stored parties."""
    stores, client = _world(monkeypatch)
    created = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert created.status_code == 200, created.text

    stores, client = _world(monkeypatch)
    _drop_party_ids(stores["paperwork_packets"].docs[0], "defendant_id", "Defendant_ID")
    missing = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert missing.status_code == 409
    assert missing.json() == {"error": "defendant_required"}
    assert _links(stores) == []

    stores, client = _world(monkeypatch)
    packet = stores["paperwork_packets"].docs[0]
    bond = stores["bond_cases"].docs[0]
    _drop_party_ids(packet, "defendant_id", "Defendant_ID")
    _drop_party_ids(bond, "defendant_id", "Defendant_ID")
    packet["Defendant_ID"] = DEFENDANT_ID
    bond["defendant_id"] = DEFENDANT_ID
    packet["indemnitor_id"] = "TEST-IND-OTHER"
    aliased = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert aliased.status_code == 409
    assert aliased.json() == {"error": "party_mismatch"}
    assert _links(stores) == []

    stores, client = _world(monkeypatch)
    packet = stores["paperwork_packets"].docs[0]
    bond = stores["bond_cases"].docs[0]
    _drop_party_ids(packet, "defendant_id", "Defendant_ID", "indemnitor_id", "Indemnitor_ID")
    _drop_party_ids(bond, "defendant_id", "Defendant_ID", "indemnitor_id", "Indemnitor_ID")
    packet["Defendant_ID"] = DEFENDANT_ID
    bond["defendant_id"] = DEFENDANT_ID
    packet["Indemnitor_ID"] = INDEMNITOR_ID
    bond["indemnitor_id"] = INDEMNITOR_ID
    issued = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert issued.status_code == 200, issued.text
    token = issued.json()["token"]

    stores["indemnitors"].docs.clear()
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    refused = client.get(f"/api/cosigner/{token}")
    assert refused.status_code == 409
    assert refused.json()["error"] == "party_unresolved"
    assert _sign_hidden(refused.text)
    scan = _post_scan(client, token)
    assert scan.status_code == 409
    assert scan.json()["error"] == "party_unresolved"
    assert calls["n"] == 0
    assert "id_ocr" not in stores["paperwork_packets"].docs[0]


def test_expired_verification_claim_can_be_replaced(monkeypatch):
    """A live claim is not replaced. An expired claim is, and the scan stays."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    uploaded = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert uploaded.status_code == 200, uploaded.text
    packet = stores["paperwork_packets"].docs[0]
    assert packet["cosigner_verified_token"] == token
    first_scans = [dict(row) for row in packet["id_scan_records"]]
    assert first_scans
    blocked = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert blocked.status_code == 409
    assert blocked.json() == {"error": "verification_open"}
    assert len(_links(stores)) == 1
    assert packet["cosigner_verified_token"] == token
    assert packet["id_scan_records"] == first_scans

    stores["cosigner_links"].docs[0]["expires_at"] = "2020-01-01T00:00:00+00:00"
    replaced = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert replaced.status_code == 200, replaced.text
    new_token = replaced.json()["token"]
    assert new_token != token
    assert packet.get("cosigner_verified_token") in {None, ""}
    assert packet["id_scan_records"] == first_scans
    assert len(_links(stores)) == 2
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    scanned = _post_scan(client, new_token)
    assert scanned.status_code == 200, scanned.text
    assert calls["n"] == 1
    assert packet["id_scan_records"][0]["scan_id"] == first_scans[0]["scan_id"]
    assert len(packet["id_scan_records"]) == 2
    assert packet["cosigner_verified_token"] == new_token


def test_incomplete_bond_does_not_get_a_link(monkeypatch):
    """No token until the bond has a validated match, case number, surety, and POA."""
    stores, client = _world(monkeypatch)
    created = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert created.status_code == 200, created.text
    assert created.json()["token"]
    assert len(_links(stores)) == 1

    for field, value, code in (
        ("match_status", "pending", "match_unvalidated"),
        ("case_number", "   ", "case_number_required"),
        ("surety_id", "", "surety_required"),
        ("poa_number", "", "poa_required"),
    ):
        stores, client = _world(monkeypatch)
        stores["bond_cases"].docs[0][field] = value
        refused = client.post(
            "/api/paperwork/cosigner-link",
            json={"packet_id": PACKET},
            cookies=_cookie(),
        )
        assert refused.status_code == 409, code
        assert refused.json() == {"error": code}
        assert "token" not in refused.json()
        assert _links(stores) == []
        issued = stores.get("audit_events")
        assert issued is None or _audits(stores, "link_issued") == []


def test_bearer_request_rechecks_bond_completeness(monkeypatch):
    """A later request fails closed once the match or POA is no longer on the bond."""
    for field, value, code in (
        ("poa_number", "", "poa_required"),
        ("match_status", "pending", "match_unvalidated"),
    ):
        stores, client = _world(monkeypatch)
        token = _token(client)
        stores["bond_cases"].docs[0][field] = value
        calls = {"n": 0}
        _scanner(monkeypatch, calls)
        state = client.get(f"/api/cosigner/{token}")
        assert state.status_code == 409, code
        assert state.json()["error"] == code
        assert _sign_hidden(state.text)
        page = client.get(f"/cosigner/{token}")
        assert page.status_code == 409
        assert _sign_hidden(page.text)
        refused = _post_scan(client, token)
        assert refused.status_code == 409, code
        assert refused.json()["error"] == code
        assert calls["n"] == 0
        assert "id_ocr" not in stores["paperwork_packets"].docs[0]


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


def test_unsigned_packet_hides_the_pay_link(monkeypatch):
    """A verified signer who has not signed does not get a pay URL."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    packet = stores["paperwork_packets"].docs[0]
    pay = _swipe_link("down")
    packet["invoice_payment_link"] = pay
    packet["swipesimple_invoice_amount_cents"] = 50000
    packet["swipesimple_invoice_number"] = BOOKING
    packet["swipesimple_invoice_bond_id"] = CASE
    assert packet["status"] == "draft"
    signing = client.get(f"/api/cosigner/{token}")
    assert signing.status_code == 200, signing.text
    assert signing.json()["step"] == "sign"
    assert signing.json()["sign_url"] == _sign("abc")
    assert signing.json()["payment"] is None
    assert pay not in signing.text
    page = client.get(f"/cosigner/{token}")
    assert page.status_code == 200
    assert pay not in page.text
    assert ">Pay</a>" not in page.text

    packet["status"] = "completed"
    paying = client.get(f"/api/cosigner/{token}")
    assert paying.status_code == 200, paying.text
    assert paying.json()["step"] == "pay"
    assert paying.json()["payment"]["url"] == pay


def test_completed_packet_hides_the_sign_url(monkeypatch):
    """Pay and paid do not return the DocuSeal URL, even when it is valid."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    signing = client.get(f"/api/cosigner/{token}")
    assert signing.status_code == 200, signing.text
    assert signing.json()["step"] == "sign"
    assert signing.json()["sign_url"] == _sign("abc")
    sign_url = _sign("abc")

    packet = stores["paperwork_packets"].docs[0]
    packet["status"] = "completed"
    packet["invoice_payment_link"] = _swipe_link("down")
    packet["swipesimple_invoice_amount_cents"] = 50000
    packet["swipesimple_invoice_number"] = BOOKING
    packet["swipesimple_invoice_bond_id"] = CASE
    paying = client.get(f"/api/cosigner/{token}")
    assert paying.status_code == 200, paying.text
    assert paying.json()["step"] == "pay"
    assert paying.json()["sign_url"] is None
    assert sign_url not in paying.text
    page = client.get(f"/cosigner/{token}")
    assert page.status_code == 200
    assert sign_url not in page.text

    stores["bond_cases"].docs[0]["premium_paid"] = True
    paid = client.get(f"/api/cosigner/{token}")
    assert paid.status_code == 200, paid.text
    assert paid.json()["step"] == "paid"
    assert paid.json()["sign_url"] is None
    assert sign_url not in paid.text
    done = client.get(f"/cosigner/{token}")
    assert done.status_code == 200
    assert sign_url not in done.text


def test_payment_url_is_escaped_on_the_page(monkeypatch):
    """A quote in a stored pay URL cannot break out of the href."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    packet = stores["paperwork_packets"].docs[0]
    pay = _swipe_link("down") + "' onclick='alert(1)"
    packet["status"] = "completed"
    packet["invoice_payment_link"] = pay
    packet["swipesimple_invoice_amount_cents"] = 50000
    packet["swipesimple_invoice_number"] = BOOKING
    packet["swipesimple_invoice_bond_id"] = CASE
    page = client.get(f"/cosigner/{token}")
    assert page.status_code == 200, page.text
    assert "' onclick='" not in page.text
    assert "&#x27; onclick=&#x27;" in page.text
    assert ">Pay</a>" in page.text
    paying = client.get(f"/api/cosigner/{token}")
    assert paying.status_code == 200, paying.text
    assert paying.json()["payment"]["url"] == pay


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
    assert signing["sign_url"] == _sign("abc")
    stores["paperwork_packets"].docs[0]["status"] = "completed"
    paid = client.get(f"/api/cosigner/{token}").json()
    assert paid["step"] == "paid"
    assert paid["sign_url"] is None


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


def _remember_payment_row(db, **kwargs):
    """A readable payments row, so the pay-step note can see what was recorded."""
    extra = kwargs.get("extra") or {}
    event = str(extra.get("event_type") or kwargs.get("event_type") or "").lower()
    status = str(kwargs.get("status") or "").lower()
    if "refund" in event or "revers" in event or status in {"refunded", "refund"}:
        kind = "refund"
    elif status in {"failed", "declined", "voided"} or event.endswith(".failed"):
        kind = "failure"
    else:
        kind = "charge"
    db["payments"].docs.append({
        "transaction_id": str(kwargs.get("transaction_id") or ""),
        "event_kind": kind,
        "status": status or "approved",
        "needs_review": bool(kwargs.get("needs_review")),
        "amount": kwargs.get("amount"),
        "booking_number": str(kwargs.get("booking_number") or ""),
    })


def _install_payment_lock(monkeypatch):
    """The in-memory transaction client the co-signer note locks on."""
    import dashboard.services.payment_log_claims as payment_log
    from tests.test_payment_plan_pay_dedupe import DB, TxnClient

    db = DB()
    txn_client = TxnClient(db)

    async def _txn():
        return txn_client

    monkeypatch.setattr(payment_log, "_transaction_client", _txn)
    monkeypatch.setattr(payment_log, "_lock_index_ready", False)
    payment_log._txn_support_cache.clear()
    monkeypatch.setattr("dashboard.extensions.get_db", lambda: db)
    return db


def _gateway(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("SWIPESIMPLE_LIVE", "1")
    monkeypatch.delenv("SWIPESIMPLE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("REQUIRE_DASHBOARD_PIN", raising=False)
    _install_payment_lock(monkeypatch)
    seen = []

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
        _remember_payment_row(db, **kwargs)
        return {
            "committed": first,
            "duplicate": not first,
            "needs_review": kind in {"refund", "failure"},
            "transaction_id": txn,
            "event_kind": kind,
            "amount": kwargs.get("amount"),
        }

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


def test_refund_without_reference_reopens_by_transaction_id(monkeypatch):
    """A reversal with no reference_id reopens the link stamped with that txn."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    uploaded = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert uploaded.status_code == 200, uploaded.text
    stores["paperwork_packets"].docs[0]["status"] = "completed"
    _gateway(monkeypatch)

    paid = _payment(client, txn="TX-PAY", amount="500.00", kind="charge")
    assert paid.status_code == 200, paid.text
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "paid"

    refunded = client.post("/api/webhooks/payment", json={
        "event_type": "payment.refunded",
        "status": "refunded",
        "transaction_id": "TX-PAY",
        "amount": "500.00",
        "custom_fields": {"booking_number": BOOKING},
    })
    assert refunded.status_code == 200, refunded.text
    reopened = client.get(f"/api/cosigner/{token}").json()
    assert reopened["step"] == "pay"
    assert reopened["payment"]["amount"] == "500.00"
    link = stores["cosigner_links"].docs[0]
    assert link["payment_reversed"] is True
    assert not link.get("paid_transaction_id")
    assert len(_audits(stores, "payment_reversed")) == 1

    again = _payment(client, txn="TX-PAY-2", amount="500.00", kind="charge")
    assert again.status_code == 200, again.text
    bare = client.post("/api/webhooks/payment", json={
        "event_type": "payment.failed",
        "status": "declined",
        "transaction_id": "TX-OTHER",
        "amount": "500.00",
    })
    assert bare.status_code == 200, bare.text
    assert stores["cosigner_links"].docs[0]["paid_transaction_id"] == "TX-PAY-2"
    assert stores["cosigner_links"].docs[0]["payment_reversed"] is False
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "paid"

    no_booking = client.post("/api/webhooks/payment", json={
        "event_type": "payment.refunded",
        "status": "refunded",
        "transaction_id": "TX-PAY-2",
        "amount": "500.00",
    })
    assert no_booking.status_code == 200, no_booking.text
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    assert not stores["cosigner_links"].docs[0].get("paid_transaction_id")


# ── Payment side with the real fail-closed recorder (#187 / #182) ──────────
# The recorder runs on the in-memory transaction fake. Its bond row is the
# same case as the co-signer world, so a clean charge is not needs_review.

DOWN_CENTS = 50000
PLAN_BALANCE_CENTS = 100000


def _recorder_world(monkeypatch, *, drop_packet_case_id=False):
    import copy
    import socket

    import dashboard.services.payment_log_claims as payment_log
    from tests.test_payment_plan_pay_dedupe import DB, TxnClient

    stores, client = _world(monkeypatch)
    if drop_packet_case_id:
        stores["paperwork_packets"].docs[0]["bond_case_id"] = ""
    token = _token(client)
    uploaded = _upload(monkeypatch, client, token, {"full_name": NAME, "dl_state": "FL"})
    assert uploaded.status_code == 200, uploaded.text
    stores["paperwork_packets"].docs[0]["status"] = "completed"
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"

    db = DB()
    db["bond_cases"].docs.append(copy.deepcopy(stores["bond_cases"].docs[0]))
    db["payment_plans"].docs.append({
        "plan_id": "PLAN-TEST-COS-1",
        "booking_number": BOOKING,
        "balance_remaining_cents": PLAN_BALANCE_CENTS,
        "total_paid_cents": 0,
        "payments_made": 0,
        "status": "active",
    })
    txn_client = TxnClient(db)

    async def _txn():
        return txn_client

    alerts = []
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("SWIPESIMPLE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("REQUIRE_DASHBOARD_PIN", raising=False)
    monkeypatch.setattr(payment_log, "_transaction_client", _txn)
    monkeypatch.setattr(payment_log, "_lock_index_ready", False)
    monkeypatch.setattr(payment_log, "_index_ready", False)
    monkeypatch.setattr(payment_log, "_notify_staff_review", alerts.append)
    payment_log._txn_support_cache.clear()
    monkeypatch.setattr("dashboard.extensions.get_db", lambda: db)
    monkeypatch.setattr("dashboard.routers.events.publish_event", AsyncMock())

    def _blocked(self, address):
        raise AssertionError(f"real network connect blocked: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    return stores, client, token, db


def _webhook(client, *, txn, amount, event="payment.completed", status="approved",
             booking=BOOKING, reference=BOOKING):
    body = {
        "event_type": event,
        "status": status,
        "transaction_id": txn,
        "custom_fields": {"booking_number": booking, "reference_id": reference},
    }
    if amount is not None:
        body["amount"] = amount
    return client.post("/api/webhooks/payment", json=body)


def _cents(value) -> int:
    from decimal import Decimal

    return int(Decimal(str(value)) * 100)


def test_only_exact_settled_down_payment_on_this_booking_marks_paid(monkeypatch):
    stores, client, token, db = _recorder_world(monkeypatch)

    refused = [
        dict(txn="TX-COS-LOW", amount="499.99"),
        dict(txn="TX-COS-HIGH", amount="500.01"),
        dict(txn="TX-COS-OTHER", amount="500.00", booking="TEST-COS-OTHER", reference="TEST-COS-OTHER"),
        dict(txn="TX-COS-NOREF", amount="500.00", reference=""),
        dict(txn="TX-COS-INST", amount="500.00", reference=f"{BOOKING}-I1"),
        dict(txn="TX-COS-DECL", amount="500.00", event="payment.failed", status="declined"),
        dict(txn="TX-COS-PEND", amount="500.00", status="pending"),
    ]
    for kwargs in refused:
        response = _webhook(client, **kwargs)
        assert response.status_code in (200, 409), (kwargs, response.text)
        state = client.get(f"/api/cosigner/{token}").json()
        assert state["step"] == "pay", kwargs
        assert not stores["cosigner_links"].docs[0].get("paid_transaction_id"), kwargs
    missing = _webhook(client, txn="TX-COS-NOAMT", amount=None)
    assert missing.status_code == 400, missing.text
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    assert len(_audits(stores, "paid")) == 0

    exact = _webhook(client, txn="TX-COS-DOWN", amount="500.00")
    assert exact.status_code == 200, exact.text
    assert exact.json()["needs_review"] is False
    state = client.get(f"/api/cosigner/{token}").json()
    assert state["step"] == "paid"
    assert _cents(state["payment"]["amount"]) == DOWN_CENTS
    assert stores["cosigner_links"].docs[0]["paid_transaction_id"] == "TX-COS-DOWN"
    assert len(_audits(stores, "paid")) == 1
    # The down payment is not the full premium and not a plan payment.
    assert stores["bond_cases"].docs[0]["premium_paid"] is False
    plan = db["payment_plans"].docs[0]
    assert plan["balance_remaining_cents"] == PLAN_BALANCE_CENTS
    assert plan["total_paid_cents"] == 0


@pytest.mark.parametrize("status", ["captured", "paid", "succeeded"])
def test_settled_charge_status_marks_the_pay_step(monkeypatch, status):
    """Settled charge statuses mark paid. The set is the recorder's."""
    from dashboard.services.payment_log_claims import REVERSAL_KINDS, SETTLED_CHARGE_STATUSES

    assert status in SETTLED_CHARGE_STATUSES
    assert {"refund", "failure"} == set(REVERSAL_KINDS)
    stores, client, token, _db = _recorder_world(monkeypatch)
    response = _webhook(
        client, txn="TX-SETTLED", amount="500.00",
        event=f"payment.{status}", status=status,
    )
    assert response.status_code == 200, response.text
    assert response.json()["event_kind"] == "charge"
    assert response.json()["needs_review"] is False
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "paid"
    assert stores["cosigner_links"].docs[0]["paid_transaction_id"] == "TX-SETTLED"
    assert len(_audits(stores, "paid")) == 1


def test_refund_before_charge_does_not_mark_paid(monkeypatch):
    """A charge committed with needs_review does not mark the pay step.

    The refund row is already stored, so the charge is needs_review. A
    booking that is not one bond is the same hold. Calling the note again
    still sees the reversal sibling.
    """
    stores, client, token, db = _recorder_world(monkeypatch)

    refunded = _webhook(
        client, txn="TX-OOO", amount="500.00",
        event="payment.refunded", status="refunded",
    )
    assert refunded.status_code == 200, refunded.text
    charged = _webhook(client, txn="TX-OOO", amount="500.00")
    assert charged.status_code == 200, charged.text
    assert charged.json()["needs_review"] is True
    assert charged.json()["event_kind"] == "charge"
    state = client.get(f"/api/cosigner/{token}").json()
    assert state["step"] == "pay"
    assert not stores["cosigner_links"].docs[0].get("paid_transaction_id")
    assert len(_audits(stores, "paid")) == 0

    from dashboard.services.cosigner_link_service import note_cosigner_paid

    asyncio.run(note_cosigner_paid(BOOKING, "TX-OOO", "500.00"))
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    assert len(_audits(stores, "paid")) == 0

    stores, client, token, db = _recorder_world(monkeypatch)
    db["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-COS-2",
        "booking_number": BOOKING,
        "status": "active",
        "premium_paid": False,
    })
    ambiguous = _webhook(client, txn="TX-AMB", amount="500.00")
    assert ambiguous.status_code == 200, ambiguous.text
    assert ambiguous.json()["needs_review"] is True
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    assert len(_audits(stores, "paid")) == 0
    asyncio.run(note_cosigner_paid(BOOKING, "TX-AMB", "500.00"))
    assert not stores["cosigner_links"].docs[0].get("paid_transaction_id")


def test_down_payment_replays_mark_once_and_write_one_row(monkeypatch):
    from dashboard.services.swipesimple_receipt_poller import SwipeSimpleReceiptPoller

    stores, client, token, db = _recorder_world(monkeypatch)

    # The poller sees the receipt first, then the webhook arrives twice.
    poller = SwipeSimpleReceiptPoller(db=db)
    polled = asyncio.run(poller._apply_bond_payment(
        db,
        {"amount": "500.00", "booking_number": BOOKING, "transaction_id": "TX-COS-DOWN"},
        gmail_message_id="gm-test-cos-1",
        raw_subject="Payment receipt",
    ))
    assert polled["success"] is True
    assert polled["committed"] is True
    for _ in range(2):
        replay = _webhook(client, txn="TX-COS-DOWN", amount="500.00")
        assert replay.status_code == 200, replay.text
        assert replay.json()["duplicate"] is True
    again = asyncio.run(poller._apply_bond_payment(
        db,
        {"amount": "500.00", "booking_number": BOOKING, "transaction_id": "TX-COS-DOWN"},
        gmail_message_id="gm-test-cos-2",
        raw_subject="Payment receipt",
    ))
    assert again["duplicate"] is True

    rows = [row for row in db["payments"].docs if row.get("transaction_id") == "TX-COS-DOWN"]
    assert len(rows) == 1
    assert _cents(rows[0]["amount"]) == DOWN_CENTS
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "paid"
    assert stores["cosigner_links"].docs[0]["paid_transaction_id"] == "TX-COS-DOWN"
    assert len(_audits(stores, "paid")) == 1
    paid_rows = [
        row for row in stores["cosigner_links"].docs[0]["timeline"]
        if row.get("step") == "paid"
    ]
    assert len(paid_rows) == 1
    assert stores["bond_cases"].docs[0]["premium_paid"] is False


def _poll_down_payment(db, *, txn, amount, reference=""):
    from dashboard.services.swipesimple_receipt_poller import SwipeSimpleReceiptPoller

    poller = SwipeSimpleReceiptPoller(db=db)
    return asyncio.run(poller._apply_bond_payment(
        db,
        {
            "amount": amount,
            "booking_number": BOOKING,
            "transaction_id": txn,
            "reference_id": reference,
        },
        gmail_message_id="gm-" + txn,
        raw_subject="Payment receipt",
    ))


def test_poller_down_payment_marks_paid_once(monkeypatch):
    """A Gmail receipt for the down payment marks the pay step once."""
    stores, client, token, db = _recorder_world(monkeypatch)
    polled = _poll_down_payment(db, txn="TX-POLL", amount="500.00")
    assert polled["success"] is True
    assert polled["committed"] is True
    state = client.get(f"/api/cosigner/{token}").json()
    assert state["step"] == "paid"
    assert stores["cosigner_links"].docs[0]["paid_transaction_id"] == "TX-POLL"
    assert len(_audits(stores, "paid")) == 1
    assert stores["bond_cases"].docs[0]["premium_paid"] is False

    again = _poll_down_payment(db, txn="TX-POLL", amount="500.00")
    assert again["duplicate"] is True
    assert len(_audits(stores, "paid")) == 1
    paid_rows = [
        row for row in stores["cosigner_links"].docs[0]["timeline"]
        if row.get("step") == "paid"
    ]
    assert len(paid_rows) == 1
    assert db["payments"].docs[0]["source"] == "swipesimple_gmail_poll"


def test_poller_review_hold_wrong_amount_and_reversal_do_not_mark_paid(monkeypatch):
    """A poller row that is in review, the wrong amount, or already reversed stays on pay."""
    stores, client, token, db = _recorder_world(monkeypatch)
    low = _poll_down_payment(db, txn="TX-POLL-LOW", amount="499.99")
    assert low["success"] is True
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    assert not stores["cosigner_links"].docs[0].get("paid_transaction_id")
    assert len(_audits(stores, "paid")) == 0

    stores, client, token, db = _recorder_world(monkeypatch)
    refunded = _webhook(
        client, txn="TX-POLL-REV", amount="500.00",
        event="payment.refunded", status="refunded",
    )
    assert refunded.status_code == 200, refunded.text
    held = _poll_down_payment(db, txn="TX-POLL-REV", amount="500.00")
    assert held["success"] is True
    assert held["needs_review"] is True
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    assert not stores["cosigner_links"].docs[0].get("paid_transaction_id")
    assert len(_audits(stores, "paid")) == 0

    stores, client, token, db = _recorder_world(monkeypatch)
    marked = _poll_down_payment(db, txn="TX-POLL-OK", amount="500.00")
    assert marked["committed"] is True
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "paid"
    refunded = _webhook(
        client, txn="TX-POLL-OK", amount="500.00",
        event="payment.refunded", status="refunded",
    )
    assert refunded.status_code == 200, refunded.text
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    replay = _poll_down_payment(db, txn="TX-POLL-OK", amount="500.00")
    assert replay["needs_review"] is True
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    assert stores["cosigner_links"].docs[0]["payment_reversed"] is True
    assert not stores["cosigner_links"].docs[0].get("paid_transaction_id")
    assert len(_audits(stores, "paid")) == 1


def test_refund_reopens_pay_step_without_moving_plan_balance(monkeypatch):
    stores, client, token, db = _recorder_world(monkeypatch)

    paid = _webhook(client, txn="TX-COS-DOWN", amount="500.00")
    assert paid.status_code == 200, paid.text
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "paid"

    refunded = _webhook(
        client, txn="TX-COS-DOWN", amount="500.00",
        event="payment.refunded", status="refunded",
    )
    assert refunded.status_code == 200, refunded.text
    assert refunded.json()["event_kind"] == "refund"
    assert refunded.json()["needs_review"] is True
    state = client.get(f"/api/cosigner/{token}").json()
    assert state["step"] == "pay"
    assert _cents(state["payment"]["amount"]) == DOWN_CENTS
    link = stores["cosigner_links"].docs[0]
    assert link["payment_reversed"] is True
    assert not link.get("paid_transaction_id")
    assert len(_audits(stores, "payment_reversed")) == 1

    # A replay of the original charge after its refund is a review hold.
    replay = _webhook(client, txn="TX-COS-DOWN", amount="500.00")
    assert replay.status_code == 200, replay.text
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    assert len(_audits(stores, "paid")) == 1

    rows = [row for row in db["payments"].docs if row.get("transaction_id") == "TX-COS-DOWN"]
    assert sorted(row["status"] for row in rows) == ["approved", "refunded"]
    plan = db["payment_plans"].docs[0]
    assert plan["balance_remaining_cents"] == PLAN_BALANCE_CENTS
    assert plan["total_paid_cents"] == 0
    assert plan["payments_made"] == 0
    assert stores["bond_cases"].docs[0]["premium_paid"] is False


def _extra_link(stores, *, packet_id, case_id, token):
    packet = {
        "packet_id": packet_id,
        "booking_number": BOOKING,
        "bond_case_id": case_id,
        "status": "completed",
        "indemnitor_name": NAME,
        "surety_id": "osi",
        "docuseal_submission_id": SUBMISSION,
        "docuseal_sign_url": _sign("other"),
        "docuseal_submitters": [
            _bound_submitter("indemnitor", _sign("other"), packet_id=packet_id, name=NAME),
        ],
        **CONFIRMED,
    }
    stores["paperwork_packets"].docs.append(packet)
    stores["cosigner_links"].docs.append({
        "token": token,
        "packet_id": packet_id,
        "booking_number": BOOKING,
        "bond_case_id": case_id,
        "signer_name": NAME,
        "timeline": [
            {"step": "id_verified", "at": "2026-10-20T00:00:00+00:00"},
            {"step": "signed", "at": "2026-10-20T00:00:00+00:00"},
        ],
        "id_verified": True,
    })


def test_cosigner_scan_keeps_an_existing_indemnitor_scan(monkeypatch):
    """A co-signer scan does not replace an indemnitor scan already on the bond."""
    import dashboard.services.cosigner_link_service as svc

    stores, client = _world(monkeypatch, cosigner=NAME)
    prior = {
        "success": True,
        "role": "indemnitor",
        "extracted": {"full_name": INDEMNITOR, "dl_state": "FL"},
    }
    packet = stores["paperwork_packets"].docs[0]
    bond = stores["bond_cases"].docs[0]
    packet["docuseal_submission_id"] = SUBMISSION
    packet["docuseal_submitters"] = [
        _bound_submitter("indemnitor", _sign("indemnitor"), name=INDEMNITOR),
        _bound_submitter("coindemnitor", _sign("cosigner"), name=NAME),
    ]
    for doc in (packet, bond):
        doc["id_ocr"] = {
            "success": True,
            "role": "indemnitor",
            "extracted": {"full_name": INDEMNITOR, "dl_state": "FL"},
        }
        doc["id_ocr_role"] = "indemnitor"
    token = _token(client)
    uploaded = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "issuing_state": "NY"},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert packet["id_ocr_role"] == "indemnitor"
    assert packet["id_ocr"]["extracted"]["full_name"] == INDEMNITOR
    assert bond["id_ocr"]["extracted"]["full_name"] == INDEMNITOR
    assert packet["co_indemnitor"]["id_ocr_role"] == "co_indemnitor"
    assert packet["co_indemnitor"]["id_ocr"]["extracted"]["full_name"] == NAME
    assert bond["co_indemnitor"]["id_ocr"]["extracted"]["full_name"] == NAME
    kept = _gate(
        bond_data=bond,
        parties=[
            {"role": "indemnitor", "name": INDEMNITOR},
            {"role": "co_indemnitor", "name": NAME},
        ],
    )
    assert kept["ok"] is True

    stores, client = _world(monkeypatch, cosigner=NAME)
    packet = stores["paperwork_packets"].docs[0]
    bond = stores["bond_cases"].docs[0]
    for doc in (packet, bond):
        doc["id_ocr"] = dict(prior)
        doc["id_ocr_role"] = "indemnitor"
        doc["id_ocr"]["extracted"] = dict(prior["extracted"])
    token = _token(client)
    original = svc._save_link

    async def _fail_verified(link):
        if link.get("id_verified"):
            raise RuntimeError("link save failed")
        return await original(link)

    monkeypatch.setattr(svc, "_save_link", _fail_verified)
    _scanner(monkeypatch, {"n": 0})
    with pytest.raises(RuntimeError, match="link save failed"):
        _post_scan(client, token)
    assert packet["id_ocr_role"] == "indemnitor"
    assert packet["id_ocr"]["extracted"]["full_name"] == INDEMNITOR
    assert bond["id_ocr"]["extracted"]["full_name"] == INDEMNITOR
    assert "co_indemnitor" not in packet
    assert "co_indemnitor" not in bond
    assert packet.get("cosigner_verified_token") in {None, ""}


def _scan_rows(doc):
    rows = doc.get("id_scan_records")
    assert isinstance(rows, list)
    return rows


def test_concurrent_scans_both_survive(monkeypatch):
    """Two overlapping scans each append a row. Neither row is replaced."""
    import dashboard.services.cosigner_link_service as svc

    stores, _client = _world(monkeypatch, cosigner=NAME)
    packet = stores["paperwork_packets"].docs[0]
    bond = stores["bond_cases"].docs[0]
    release = asyncio.Event()
    entered = asyncio.Event()
    state = {"held": False}

    def _wrap(col):
        orig = col.update_one

        async def _update(query, update, upsert=False, session=None, **kwargs):
            pointer = (update or {}).get("$set") or {}
            if (
                not state["held"]
                and pointer.get("id_ocr_role") == "indemnitor"
                and "id_ocr" in pointer
            ):
                state["held"] = True
                entered.set()
                await release.wait()
            return await orig(query, update, upsert=upsert, session=session, **kwargs)

        col.update_one = _update

    _wrap(stores["paperwork_packets"])
    _wrap(stores["bond_cases"])

    async def _go():
        first = asyncio.create_task(
            svc._store_scan(packet, bond, INDEMNITOR, "FL", "indemnitor", []),
        )
        await entered.wait()
        await svc._store_scan(packet, bond, NAME, "NY", "co_indemnitor", [])
        release.set()
        await first

    asyncio.run(_go())
    for doc in (packet, bond):
        rows = _scan_rows(doc)
        assert len(rows) == 2
        by_role = {row["role"]: row for row in rows}
        assert set(by_role) == {"indemnitor", "co_indemnitor"}
        assert by_role["indemnitor"]["signer_name"] == INDEMNITOR
        assert by_role["co_indemnitor"]["signer_name"] == NAME
        assert by_role["indemnitor"]["scan_id"] != by_role["co_indemnitor"]["scan_id"]
        assert all(row["bond_case_id"] == CASE for row in rows)
        assert all(row["booking_number"] == BOOKING for row in rows)
        assert all(row["packet_id"] == PACKET for row in rows)
        assert all(row["recorded_at"] for row in rows)
        assert doc["indemnitor"]["id_ocr"]["scan_id"] == by_role["indemnitor"]["scan_id"]
        assert doc["co_indemnitor"]["id_ocr"]["scan_id"] == by_role["co_indemnitor"]["scan_id"]


def test_newer_scan_keeps_the_older_record(monkeypatch):
    """A later scan appends. The earlier scan row stays as it was stored."""
    import dashboard.services.cosigner_link_service as svc

    stores, _client = _world(monkeypatch, cosigner=NAME)
    packet = stores["paperwork_packets"].docs[0]
    bond = stores["bond_cases"].docs[0]
    asyncio.run(svc._store_scan(packet, bond, INDEMNITOR, "FL", "indemnitor", []))
    older = {
        doc_name: dict(_scan_rows(doc)[0])
        for doc_name, doc in (("packet", packet), ("bond", bond))
    }
    asyncio.run(svc._store_scan(packet, bond, NAME, "NY", "co_indemnitor", []))
    for doc_name, doc in (("packet", packet), ("bond", bond)):
        rows = _scan_rows(doc)
        first = older[doc_name]
        assert len(rows) == 2
        assert rows[0]["scan_id"] == first["scan_id"]
        assert rows[0]["signer_name"] == INDEMNITOR
        assert rows[0]["extracted"] == first["extracted"]
        assert rows[0]["recorded_at"] == first["recorded_at"]
        assert rows[1]["signer_name"] == NAME
        assert rows[1]["role"] == "co_indemnitor"
        assert doc["id_ocr"]["scan_id"] == first["scan_id"]
        assert doc["co_indemnitor"]["id_ocr"]["scan_id"] == rows[1]["scan_id"]


def test_rollback_removes_only_the_pointer_it_added(monkeypatch):
    """A failed verification drops its pointer and leaves both scan rows."""
    import dashboard.services.cosigner_link_service as svc

    stores, client = _world(monkeypatch, cosigner=NAME)
    packet = stores["paperwork_packets"].docs[0]
    bond = stores["bond_cases"].docs[0]
    packet["docuseal_submitters"] = [
        _bound_submitter("indemnitor", _sign("indemnitor"), name=INDEMNITOR),
        _bound_submitter("coindemnitor", _sign("cosigner"), name=NAME),
    ]
    asyncio.run(svc._store_scan(packet, bond, INDEMNITOR, "FL", "indemnitor", []))
    older_ids = {
        id(doc): _scan_rows(doc)[0]["scan_id"]
        for doc in (packet, bond)
    }
    token = _token(client)
    original = svc._save_link

    async def _fail_verified(link):
        if link.get("id_verified"):
            raise RuntimeError("link save failed")
        return await original(link)

    monkeypatch.setattr(svc, "_save_link", _fail_verified)
    _scanner(monkeypatch, {"n": 0})
    with pytest.raises(RuntimeError, match="link save failed"):
        _post_scan(client, token)
    for doc in (packet, bond):
        rows = _scan_rows(doc)
        assert [row["signer_name"] for row in rows] == [INDEMNITOR, NAME]
        assert rows[0]["scan_id"] == older_ids[id(doc)]
        assert rows[1]["role"] == "co_indemnitor"
        assert rows[1]["recorded_at"]
        assert doc["id_ocr"]["scan_id"] == older_ids[id(doc)]
        assert doc["id_ocr_role"] == "indemnitor"
        assert "co_indemnitor" not in doc
    assert packet.get("cosigner_verified_token") in {None, ""}


def test_failed_same_role_retry_restores_the_previous_pointer(monkeypatch):
    """A failed retry puts the previous same-role pointer back."""
    import dashboard.services.cosigner_link_service as svc

    stores, client = _world(monkeypatch)
    packet = stores["paperwork_packets"].docs[0]
    bond = stores["bond_cases"].docs[0]
    asyncio.run(svc._store_scan(packet, bond, NAME, "FL", "indemnitor", []))
    older = {
        id(doc): _scan_rows(doc)[0]["scan_id"]
        for doc in (packet, bond)
    }
    token = _token(client)
    original = svc._save_link

    async def _fail_verified(link):
        if link.get("id_verified"):
            raise RuntimeError("link save failed")
        return await original(link)

    monkeypatch.setattr(svc, "_save_link", _fail_verified)
    _scanner(monkeypatch, {"n": 0})
    with pytest.raises(RuntimeError, match="link save failed"):
        _post_scan(client, token)
    for doc in (packet, bond):
        rows = _scan_rows(doc)
        assert len(rows) == 2
        assert rows[0]["scan_id"] == older[id(doc)]
        assert rows[1]["scan_id"] != older[id(doc)]
        assert rows[1]["role"] == "indemnitor"
        assert doc["id_ocr"]["scan_id"] == older[id(doc)]
        assert doc["id_ocr_role"] == "indemnitor"
        assert doc["indemnitor"]["id_ocr"]["scan_id"] == older[id(doc)]
        assert doc["indemnitor"]["id_ocr_role"] == "indemnitor"
    assert packet.get("cosigner_verified_token") in {None, ""}


def test_failed_pointer_write_rolls_back_the_party_scan(monkeypatch):
    """A pointer write that fails still drops the party pointer it stored."""
    stores, client = _world(monkeypatch)
    packet = stores["paperwork_packets"].docs[0]
    bond = stores["bond_cases"].docs[0]

    def _wrap(col):
        orig = col.update_one

        async def _update(query, update, upsert=False, session=None, **kwargs):
            pointer = (update or {}).get("$set") or {}
            if "id_ocr" in pointer and "id_ocr_role" in pointer:
                raise RuntimeError("top pointer failed")
            return await orig(query, update, upsert=upsert, session=session, **kwargs)

        col.update_one = _update

    _wrap(stores["paperwork_packets"])
    _wrap(stores["bond_cases"])
    token = _token(client)
    _scanner(monkeypatch, {"n": 0})
    with pytest.raises(RuntimeError, match="top pointer failed"):
        _post_scan(client, token)
    rows = _scan_rows(packet)
    assert len(rows) == 1
    assert rows[0]["signer_name"] == NAME
    assert rows[0]["role"] == "indemnitor"
    assert "id_ocr" not in packet
    assert "indemnitor" not in packet
    assert not bond.get("id_scan_records")
    assert "id_ocr" not in bond
    from dashboard.services.identity_verification_service import scans_from_document

    assert [row for row in scans_from_document(packet) if row.get("passed") == "1"] == []
    assert packet.get("cosigner_verified_token") in {None, ""}


def test_reassigned_packet_does_not_keep_the_issued_link(monkeypatch):
    """A packet moved to another open case does not keep the old bearer link."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    stores["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-OTHER",
        "booking_number": BOOKING,
        "status": "active",
        "indemnitor_name": NAME,
        "surety_id": "osi",
        "premium_paid": False,
        **CONFIRMED,
    })
    packet = stores["paperwork_packets"].docs[0]
    packet["bond_case_id"] = "BC-TEST-OTHER"
    packet["docuseal_sign_url"] = "https://sign.example/s/other"
    state = client.get(f"/api/cosigner/{token}")
    assert state.status_code == 409
    assert state.json()["error"] == "bond_mismatch"
    assert _sign_hidden(state.text)
    page = client.get(f"/cosigner/{token}")
    assert page.status_code == 409
    assert _sign_hidden(page.text)
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    refused = _post_scan(client, token)
    assert refused.status_code == 409
    assert refused.json()["error"] == "bond_mismatch"
    assert calls["n"] == 0
    assert "id_ocr" not in packet
    assert "id_ocr" not in stores["bond_cases"].docs[1]


def test_booking_fallback_link_records_the_down_payment(monkeypatch):
    """A packet with no case id still stores the resolved case and can be paid.

    An older link that kept an empty case id is found by the same booking
    when that booking is exactly one bond case. Two cases stamp nothing.
    """
    stores, client, token, _db = _recorder_world(monkeypatch, drop_packet_case_id=True)
    packet = stores["paperwork_packets"].docs[0]
    link = stores["cosigner_links"].docs[0]
    assert packet["bond_case_id"] == ""
    assert link["bond_case_id"] == CASE
    assert link["booking_number"] == BOOKING
    link["bond_case_id"] = ""
    paid = _webhook(client, txn="TX-BOOK", amount="500.00")
    assert paid.status_code == 200, paid.text
    assert paid.json()["needs_review"] is False
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "paid"
    assert link["paid_transaction_id"] == "TX-BOOK"
    assert len(_audits(stores, "paid")) == 1

    stores, client, token, _db = _recorder_world(monkeypatch, drop_packet_case_id=True)
    stores["cosigner_links"].docs[0]["bond_case_id"] = ""
    stores["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-OTHER",
        "booking_number": BOOKING,
        "status": "active",
        "indemnitor_name": "Pat Other",
        "surety_id": "osi",
        "premium_paid": False,
        **CONFIRMED,
    })
    ambiguous = _webhook(client, txn="TX-BOOK-TWO", amount="500.00")
    assert ambiguous.status_code == 200, ambiguous.text
    assert not stores["cosigner_links"].docs[0].get("paid_transaction_id")
    assert len(_audits(stores, "paid")) == 0


def test_booking_fallback_reconciles_a_bond_mirror(monkeypatch):
    """A booking-only packet accepts one agreeing mirror and refuses the rest."""
    stores, client = _world(monkeypatch)
    stores["paperwork_packets"].docs[0]["bond_case_id"] = ""
    _mirror_of(stores)
    token = _token(client)
    state = client.get(f"/api/cosigner/{token}")
    assert state.status_code == 200, state.text
    assert state.json()["step"] == "scan"

    stores["active_bonds"].docs[0]["status"] = "exonerated"
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    closed = client.get(f"/api/cosigner/{token}")
    assert closed.status_code == 409
    assert closed.json()["error"] == "bond_mirror_mismatch"
    assert _sign_hidden(closed.text)
    refused = _post_scan(client, token)
    assert refused.status_code == 409
    assert refused.json()["error"] == "bond_mirror_mismatch"
    assert calls["n"] == 0

    stores, client = _world(monkeypatch)
    stores["paperwork_packets"].docs[0]["bond_case_id"] = ""
    extra = dict(stores["bond_cases"].docs[0])
    extra.pop("_id", None)
    extra["bond_case_id"] = "BC-TEST-OTHER"
    stores["bond_cases"].docs.append(extra)
    ambiguous = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert ambiguous.status_code == 409
    assert ambiguous.json()["error"] == "bond_ambiguous"
    assert _links(stores) == []


def test_down_payment_is_scoped_to_one_packet(monkeypatch):
    """A booking charge stamps the bond's open packet, not every link on the booking."""
    stores, client, _token, _db = _recorder_world(monkeypatch)
    _extra_link(stores, packet_id="PKT-TEST-COS-2", case_id="BC-TEST-OTHER", token="other-token")
    paid = _webhook(client, txn="TX-SCOPE", amount="500.00")
    assert paid.status_code == 200, paid.text
    assert paid.json()["needs_review"] is False
    links = stores["cosigner_links"].docs
    assert links[0]["bond_case_id"] == CASE
    assert links[0]["paid_transaction_id"] == "TX-SCOPE"
    assert links[1]["bond_case_id"] == "BC-TEST-OTHER"
    assert not links[1].get("paid_transaction_id")
    assert len(_audits(stores, "paid")) == 1

    stores, client, _token, _db = _recorder_world(monkeypatch)
    _extra_link(stores, packet_id="PKT-TEST-COS-2", case_id=CASE, token="second-packet")
    ambiguous = _webhook(client, txn="TX-TWO", amount="500.00")
    assert ambiguous.status_code == 200, ambiguous.text
    assert ambiguous.json()["needs_review"] is False
    assert all(not link.get("paid_transaction_id") for link in stores["cosigner_links"].docs)
    assert len(_audits(stores, "paid")) == 0
    stores["paperwork_packets"].docs[1]["status"] = "superseded"
    stores["paperwork_packets"].docs[1]["superseded"] = True
    opened = _webhook(client, txn="TX-TWO", amount="500.00")
    assert opened.status_code == 200, opened.text
    assert stores["cosigner_links"].docs[0]["paid_transaction_id"] == "TX-TWO"
    assert not stores["cosigner_links"].docs[1].get("paid_transaction_id")
    assert len(_audits(stores, "paid")) == 1

    stores, client, _token, _db = _recorder_world(monkeypatch)
    _extra_link(stores, packet_id=PACKET, case_id=CASE, token="resent-token")
    resent = _webhook(client, txn="TX-RESEND", amount="500.00")
    assert resent.status_code == 200, resent.text
    assert stores["cosigner_links"].docs[0]["paid_transaction_id"] == "TX-RESEND"
    assert stores["cosigner_links"].docs[1]["paid_transaction_id"] == "TX-RESEND"
    assert len(_audits(stores, "paid")) == 2


def test_charge_refund_race_does_not_leave_step_paid(monkeypatch):
    """Concurrent charge and refund cannot leave the pay step paid.

    Two concurrent copies of the same charge write one paid audit. The link
    update holds payment_txn_locks and is a conditional write.
    """
    import httpx

    import dashboard.services.payment_log_claims as payment_log
    from tests.test_payment_log_claims import _LockColl, _OneRowClient

    stores, client, token, db = _recorder_world(monkeypatch)
    locker = _OneRowClient(db)
    db["payment_txn_locks"] = _LockColl(locker)

    async def _txn():
        return locker

    monkeypatch.setattr(payment_log, "_transaction_client", _txn)
    monkeypatch.setattr(payment_log, "_lock_index_ready", False)
    payment_log._txn_support_cache.clear()

    def _body(txn, *, refund=False):
        return {
            "event_type": "payment.refunded" if refund else "payment.completed",
            "status": "refunded" if refund else "approved",
            "transaction_id": txn,
            "amount": "500.00",
            "custom_fields": {"booking_number": BOOKING, "reference_id": BOOKING},
        }

    async def _both(bodies):
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            return await asyncio.gather(*[
                http.post("/api/webhooks/payment", json=body) for body in bodies
            ])

    charge, refund = asyncio.run(_both([
        _body("TX-RACE"),
        _body("TX-RACE", refund=True),
    ]))
    assert charge.status_code == 200, charge.text
    assert refund.status_code == 200, refund.text
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    raced = stores["cosigner_links"].docs[0]
    assert not raced.get("paid_transaction_id")
    assert len(_audits(stores, "paid")) <= 1
    assert db["payment_txn_locks"].docs
    assert db["payment_txn_locks"].docs[0]["txn_key"] == "TX-RACE"

    before = len(_audits(stores, "paid"))
    first, second = asyncio.run(_both([_body("TX-ONCE"), _body("TX-ONCE")]))
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "paid"
    assert stores["cosigner_links"].docs[0]["paid_transaction_id"] == "TX-ONCE"
    assert len(_audits(stores, "paid")) - before == 1
    paid_rows = [
        row for row in stores["cosigner_links"].docs[0]["timeline"]
        if row.get("step") == "paid"
    ]
    assert len(paid_rows) == 1


def test_scan_and_state_recheck_the_current_bond(monkeypatch):
    def _closed(stores):
        stores["bond_cases"].docs[0]["status"] = "exonerated"

    def _renamed(stores):
        stores["bond_cases"].docs[0]["indemnitor_name"] = "Pat Other"

    for mutate, code in (
        (lambda stores: stores["bond_cases"].docs.clear(), "bond_required"),
        (_closed, "bond_inactive"),
        (_renamed, "signer_mismatch"),
    ):
        stores, client = _world(monkeypatch)
        token = _token(client)
        mutate(stores)
        calls = {"n": 0}
        _scanner(monkeypatch, calls)
        state = client.get(f"/api/cosigner/{token}")
        assert state.status_code == 409, code
        assert state.json()["error"] == code
        assert _sign_hidden(state.text)
        page = client.get(f"/cosigner/{token}")
        assert _sign_hidden(page.text)
        refused = _post_scan(client, token)
        assert refused.status_code == 409, code
        assert refused.json()["error"] == code
        assert calls["n"] == 0
        assert "id_ocr" not in stores["paperwork_packets"].docs[0]

    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    assert "DocuSeal" in opened.text
    stores["bond_cases"].docs[0]["indemnitor_name"] = "Pat Other"
    hidden = client.get(f"/api/cosigner/{token}")
    assert hidden.status_code == 409
    assert hidden.json()["error"] == "signer_mismatch"
    assert _sign_hidden(hidden.text)
    assert _sign_hidden(client.get(f"/cosigner/{token}").text)


def _mirror_of(stores, **changes):
    mirror = dict(stores["bond_cases"].docs[0])
    mirror.pop("_id", None)
    mirror.update(changes)
    stores["active_bonds"] = _Col([mirror])


def test_bond_mirror_and_duplicate_rows_fail_closed(monkeypatch):
    """A disagreeing active-bond mirror, or two case rows, closes the link."""
    def _terminal(stores):
        _mirror_of(stores, status="exonerated")

    def _renamed(stores):
        _mirror_of(stores, indemnitor_name="Pat Other")

    def _duplicate(stores):
        extra = dict(stores["bond_cases"].docs[0])
        extra.pop("_id", None)
        stores["bond_cases"].docs.append(extra)

    for mutate, code in (
        (_terminal, "bond_mirror_mismatch"),
        (_renamed, "bond_mirror_mismatch"),
        (_duplicate, "bond_ambiguous"),
    ):
        stores, client = _world(monkeypatch)
        token = _token(client)
        mutate(stores)
        calls = {"n": 0}
        _scanner(monkeypatch, calls)
        state = client.get(f"/api/cosigner/{token}")
        assert state.status_code == 409, code
        assert state.json()["error"] == code
        assert _sign_hidden(state.text)
        page = client.get(f"/cosigner/{token}")
        assert page.status_code == 409
        assert _sign_hidden(page.text)
        refused = _post_scan(client, token)
        assert refused.status_code == 409, code
        assert refused.json()["error"] == code
        assert calls["n"] == 0
        assert "id_ocr" not in stores["paperwork_packets"].docs[0]

    stores, client = _world(monkeypatch)
    token = _token(client)
    _mirror_of(stores)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    assert client.get(f"/api/cosigner/{token}").json()["sign_url"] == _sign("abc")


def test_mirror_party_ids_must_match(monkeypatch):
    """A mirror with a different defendant or indemnitor id is not this bond."""
    stores, client = _world(monkeypatch)
    bond = stores["bond_cases"].docs[0]
    bond.pop("defendant_id", None)
    bond["Defendant_ID"] = DEFENDANT_ID
    _mirror_of(stores)
    mirror = stores["active_bonds"].docs[0]
    mirror.pop("Defendant_ID", None)
    mirror.pop("defendant_id", None)
    mirror["defendant_id"] = DEFENDANT_ID
    token = _token(client)
    agreed = client.get(f"/api/cosigner/{token}")
    assert agreed.status_code == 200, agreed.text
    assert agreed.json()["step"] == "scan"
    assert agreed.json()["sign_url"] is None

    for field, value in (
        ("defendant_id", "TEST-DEF-OTHER"),
        ("indemnitor_id", "TEST-IND-OTHER"),
    ):
        stores, client = _world(monkeypatch)
        _mirror_of(stores, **{field: value})
        calls = {"n": 0}
        _scanner(monkeypatch, calls)
        refused = client.post(
            "/api/paperwork/cosigner-link",
            json={"packet_id": PACKET},
            cookies=_cookie(),
        )
        assert refused.status_code == 409, field
        assert refused.json()["error"] == "bond_mirror_mismatch"
        assert "token" not in refused.json()
        assert _links(stores) == []
        assert calls["n"] == 0

    stores, client = _world(monkeypatch)
    stores["paperwork_packets"].docs[0]["bond_case_id"] = ""
    _mirror_of(stores, Defendant_ID="TEST-DEF-OTHER")
    stores["active_bonds"].docs[0].pop("defendant_id", None)
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    drifted = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert drifted.status_code == 409
    assert drifted.json()["error"] == "bond_mirror_mismatch"
    scan = _post_scan(client, "TEST-MISSING-TOKEN")
    assert scan.status_code == 404
    assert calls["n"] == 0


def test_failed_issuance_audit_leaves_no_link(monkeypatch):
    """A failed issuance audit removes the link and does not store the token."""
    stores, client = _world(monkeypatch)
    links = _Col()
    audits = _Col()
    stores["cosigner_links"] = links
    stores["audit_events"] = audits
    seen = []
    original_link_insert = links.insert_one
    original_audit_insert = audits.insert_one

    async def _watch_link(doc, session=None, **kwargs):
        seen.append(str(doc.get("token") or ""))
        return await original_link_insert(doc, session=session, **kwargs)

    async def _fail_issued(doc, session=None, **kwargs):
        if doc.get("event_type") == "link_issued":
            raise RuntimeError("audit store unavailable")
        return await original_audit_insert(doc, session=session, **kwargs)

    links.insert_one = _watch_link
    audits.insert_one = _fail_issued
    with pytest.raises(RuntimeError, match="audit store unavailable"):
        client.post(
            "/api/paperwork/cosigner-link",
            json={"packet_id": PACKET},
            cookies=_cookie(),
        )
    assert seen
    token = seen[0]
    assert links.docs == []
    assert token not in str(audits.docs)
    assert token not in str(links.docs)
    rolled = [row for row in audits.docs if row.get("event_type") == "link_issuance_rolled_back"]
    assert len(rolled) == 1
    assert rolled[0]["link_ref"] == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert "token" not in rolled[0]
    missing = client.get(f"/api/cosigner/{token}")
    assert missing.status_code == 404
    assert missing.json()["error"] == "link_not_found"

    audits.insert_one = original_audit_insert
    created = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert created.status_code == 200, created.text
    assert created.json()["token"] != token
    assert token not in str(audits.docs)
    assert token not in str(links.docs)


def test_mirror_chain_must_match(monkeypatch):
    """A mirror that diverges on the case chain is not this bond."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    _mirror_of(stores)
    agreed = client.get(f"/api/cosigner/{token}")
    assert agreed.status_code == 200, agreed.text
    assert agreed.json()["step"] == "scan"

    divergences = (
        {"poa_number": ""},
        {"match_status": "pending"},
        {"case_number": "2026-TEST-OTHER"},
        {"surety_id": "palmetto"},
        {"match_id": "MATCH-OTHER"},
        {"booking_number": "TEST-COS-OTHER"},
        {"defendant_name": "Pat Other"},
    )
    for changes in divergences:
        stores, client = _world(monkeypatch)
        token = _token(client)
        _mirror_of(stores, **changes)
        calls = {"n": 0}
        _scanner(monkeypatch, calls)
        state = client.get(f"/api/cosigner/{token}")
        assert state.status_code == 409, changes
        assert state.json()["error"] == "bond_mirror_mismatch"
        assert _sign_hidden(state.text)
        refused = _post_scan(client, token)
        assert refused.status_code == 409, changes
        assert refused.json()["error"] == "bond_mirror_mismatch"
        assert calls["n"] == 0
        assert "id_ocr" not in stores["paperwork_packets"].docs[0]

    stores, client = _world(monkeypatch)
    stores["paperwork_packets"].docs[0]["bond_case_id"] = ""
    _mirror_of(stores)
    token = _token(client)
    stores["active_bonds"].docs[0]["case_number"] = "2026-TEST-OTHER"
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    drifted = client.get(f"/api/cosigner/{token}")
    assert drifted.status_code == 409
    assert drifted.json()["error"] == "bond_mirror_mismatch"
    assert _sign_hidden(drifted.text)
    refused = _post_scan(client, token)
    assert refused.status_code == 409
    assert refused.json()["error"] == "bond_mirror_mismatch"
    assert calls["n"] == 0


def test_first_verified_scan_closes_sibling_links(monkeypatch):
    stores, client = _world(monkeypatch)
    first = _token(client)
    second = _token(client)
    assert first != second
    opened = _upload(
        monkeypatch, client, first,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    assert stores["paperwork_packets"].docs[0]["cosigner_verified_token"] == first
    sibling = next(row for row in stores["cosigner_links"].docs if row["token"] == second)
    assert sibling["revoked"] is True
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    refused = _post_scan(client, second)
    assert refused.status_code == 410
    assert refused.json()["error"] == "link_revoked"
    assert calls["n"] == 0
    hidden = client.get(f"/api/cosigner/{second}")
    assert hidden.status_code == 410
    assert _sign_hidden(hidden.text)
    assert len(_audits(stores, "id_verified")) == 1
    assert stores["paperwork_packets"].docs[0]["id_ocr"]["extracted"]["full_name"] == NAME

    fresh, fresh_client = _world(monkeypatch)
    token = _token(fresh_client)
    fresh["paperwork_packets"].docs[0]["cosigner_verified_token"] = "other-token"
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    claimed = _post_scan(fresh_client, token)
    assert claimed.status_code == 409
    assert claimed.json()["error"] == "scan_closed"
    assert calls["n"] == 0
    assert "id_ocr" not in fresh["paperwork_packets"].docs[0]


def test_link_creation_writes_an_issuance_audit(monkeypatch):
    stores, client = _world(monkeypatch)
    created = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert created.status_code == 200, created.text
    token = created.json()["token"]
    issued = _audits(stores, "link_issued")
    assert len(issued) == 1
    assert issued[0]["actor"] == ACTOR
    assert issued[0]["confidence"] == 1.0
    assert issued[0]["old_state"]["step"] == ""
    assert issued[0]["old_state"]["id_verified"] is False
    assert issued[0]["new_state"]["step"] == "scan"
    assert issued[0]["new_state"]["id_verified"] is False
    assert token not in str(issued)

    stores["bond_cases"].docs[0]["status"] = "exonerated"
    closed = client.post(
        "/api/paperwork/cosigner-link",
        json={"packet_id": PACKET},
        cookies=_cookie(),
    )
    assert closed.status_code == 409
    assert closed.json()["error"] == "bond_inactive"
    assert len(_links(stores)) == 1
    assert len(_audits(stores, "link_issued")) == 1


def test_foreign_address_state_does_not_unlock_signing(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    foreign = _upload(
        monkeypatch, client, token,
        {
            "full_name": NAME,
            "dl_state": "FL",
            "state": "FL",
            "issuing_country": "CAN",
            "country": "CAN",
        },
    )
    assert foreign.status_code == 400
    assert _error_text(foreign) == "no_issuing_state"
    assert _sign_hidden(foreign.text)
    assert "id_ocr" not in stores["paperwork_packets"].docs[0]
    assert client.get(f"/api/cosigner/{token}").json()["sign_url"] is None

    _age_scan_cooldown(stores, token)
    domestic = _upload(
        monkeypatch, client, token,
        {
            "full_name": NAME,
            "dl_state": "FL",
            "state": "FL",
            "issuing_country": "US",
        },
    )
    assert domestic.status_code == 200, domestic.text
    assert "DocuSeal" in domestic.text
    assert client.get(f"/api/cosigner/{token}").json()["sign_url"] == _sign("abc")


def test_receipt_poller_marks_the_down_payment(monkeypatch):
    from dashboard.services.swipesimple_receipt_poller import SwipeSimpleReceiptPoller

    stores, client, token, db = _recorder_world(monkeypatch)
    poller = SwipeSimpleReceiptPoller(db=db)
    short = asyncio.run(poller._apply_bond_payment(
        db,
        {"amount": "1.00", "booking_number": BOOKING, "transaction_id": "TX-COS-POLL-SHORT"},
        gmail_message_id="gm-test-cos-short",
        raw_subject="Payment receipt",
    ))
    assert short["success"] is True
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    assert len(_audits(stores, "paid")) == 0

    polled = asyncio.run(poller._apply_bond_payment(
        db,
        {"amount": "500.00", "booking_number": BOOKING, "transaction_id": "TX-COS-POLL"},
        gmail_message_id="gm-test-cos-poll",
        raw_subject="Payment receipt",
    ))
    assert polled["success"] is True
    assert polled["committed"] is True
    state = client.get(f"/api/cosigner/{token}").json()
    assert state["step"] == "paid"
    assert stores["cosigner_links"].docs[0]["paid_transaction_id"] == "TX-COS-POLL"
    assert len(_audits(stores, "paid")) == 1
    assert stores["bond_cases"].docs[0]["premium_paid"] is False
    replay = asyncio.run(poller._apply_bond_payment(
        db,
        {"amount": "500.00", "booking_number": BOOKING, "transaction_id": "TX-COS-POLL"},
        gmail_message_id="gm-test-cos-poll-2",
        raw_subject="Payment receipt",
    ))
    assert replay["duplicate"] is True
    assert len(_audits(stores, "paid")) == 1


def test_in_flight_claim_refuses_a_second_scan(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    stores["paperwork_packets"].docs[0]["cosigner_verified_token"] = token
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    refused = _post_scan(client, token)
    assert refused.status_code == 409
    assert refused.json()["error"] == "scan_closed"
    assert calls["n"] == 0
    assert "id_ocr" not in stores["paperwork_packets"].docs[0]
    assert len(_audits(stores, "id_verified")) == 0
    assert client.get(f"/api/cosigner/{token}").json()["sign_url"] is None


def test_confirmed_zero_down_payment_finishes_after_signing(monkeypatch):
    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    for row in (stores["paperwork_packets"].docs[0], stores["bond_cases"].docs[0]):
        row["down_payment_amount"] = "0.00"
    packet = stores["paperwork_packets"].docs[0]
    packet["status"] = "completed"
    packet["invoice_payment_link"] = "https://pay.example/zero"
    state = client.get(f"/api/cosigner/{token}")
    assert state.status_code == 200, state.text
    body = state.json()
    assert body["step"] == "paid"
    assert body["sign_url"] is None
    assert _sign("abc") not in state.text
    assert body["payment"]["amount"] == "0.00"
    assert body["payment"]["url"] is None
    assert "pay.example/zero" not in state.text
    page = client.get(f"/cosigner/{token}")
    assert "pay.example/zero" not in page.text
    assert stores["bond_cases"].docs[0]["premium_paid"] is False
    assert len(_audits(stores, "paid")) == 1


def test_zero_down_payment_audits_paid_without_a_charge(monkeypatch):
    """A confirmed zero down payment writes paid, and does not store the token."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    for row in (stores["paperwork_packets"].docs[0], stores["bond_cases"].docs[0]):
        row["down_payment_amount"] = "0.00"
    packet = stores["paperwork_packets"].docs[0]
    packet["status"] = "completed"
    state = client.get(f"/api/cosigner/{token}")
    assert state.status_code == 200, state.text
    body = state.json()
    assert body["step"] == "paid"
    assert body["sign_url"] is None
    assert body["payment"]["amount"] == "0.00"
    assert body["payment"]["url"] is None
    assert stores["bond_cases"].docs[0]["premium_paid"] is False
    assert stores["bond_cases"].docs[0]["down_payment_amount"] == "0.00"
    link = stores["cosigner_links"].docs[0]
    assert "paid" not in [row.get("step") for row in link["timeline"]]
    assert "nothing_due" in [row.get("step") for row in link["timeline"]]
    assert "paid_transaction_id" not in link
    paid = _audits(stores, "paid")
    assert len(paid) == 1
    assert paid[0]["old_state"]["paid"] is False
    assert paid[0]["old_state"]["signed"] is True
    assert paid[0]["new_state"]["paid"] is False
    assert token not in str(paid)
    assert "token" not in paid[0]
    again = client.get(f"/api/cosigner/{token}")
    assert again.status_code == 200
    assert len(_audits(stores, "paid")) == 1


def test_zero_down_reconfirm_shows_the_pay_link(monkeypatch):
    """A $0 finish is live. A later amount above zero is not stored as paid."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    packet = stores["paperwork_packets"].docs[0]
    bond = stores["bond_cases"].docs[0]
    for row in (packet, bond):
        row["down_payment_amount"] = "0.00"
    packet["status"] = "completed"
    done = client.get(f"/api/cosigner/{token}")
    assert done.status_code == 200, done.text
    assert done.json()["step"] == "paid"
    assert done.json()["payment"]["amount"] == "0.00"
    assert done.json()["payment"]["url"] is None
    assert "paid_transaction_id" not in stores["cosigner_links"].docs[0]
    assert len(_audits(stores, "paid")) == 1

    pay = _swipe_link("down")
    for row in (packet, bond):
        row["down_payment_amount"] = "500.00"
    packet["invoice_payment_link"] = pay
    packet["swipesimple_invoice_amount_cents"] = 50000
    packet["swipesimple_invoice_number"] = BOOKING
    packet["swipesimple_invoice_bond_id"] = CASE
    reconfirmed = client.get(f"/api/cosigner/{token}")
    assert reconfirmed.status_code == 200, reconfirmed.text
    body = reconfirmed.json()
    assert body["step"] == "pay"
    assert body["payment"]["url"] == pay
    assert body["payment"]["amount"] == "500.00"
    assert pay in reconfirmed.text
    link = stores["cosigner_links"].docs[0]
    assert "paid_transaction_id" not in link
    assert "paid" not in [row.get("step") for row in link["timeline"]]
    assert bond["premium_paid"] is False
    assert len(_audits(stores, "paid")) == 1
    page = client.get(f"/cosigner/{token}")
    assert pay in page.text

    link["paid_transaction_id"] = "TX-TEST-COS-1"
    stamped = client.get(f"/api/cosigner/{token}")
    assert stamped.status_code == 200, stamped.text
    assert stamped.json()["step"] == "paid"
    assert link["paid_transaction_id"] == "TX-TEST-COS-1"
    assert bond["premium_paid"] is False


def test_invoice_url_on_another_port_is_hidden(monkeypatch):
    """The pay host allowlist includes the port. 443 still matches."""
    from urllib.parse import urlparse

    stores, client = _world(monkeypatch)
    token = _token(client)
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    packet = stores["paperwork_packets"].docs[0]
    packet["status"] = "completed"
    owned = _swipe_link("down")
    parsed = urlparse(owned)
    other_port = f"https://{parsed.hostname}:8443{parsed.path}"
    packet["invoice_payment_link"] = other_port
    packet["swipesimple_invoice_amount_cents"] = 50000
    packet["swipesimple_invoice_number"] = BOOKING
    packet["swipesimple_invoice_bond_id"] = CASE
    hidden = client.get(f"/api/cosigner/{token}")
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["step"] == "pay"
    assert hidden.json()["payment"]["url"] is None
    assert hidden.json()["payment"]["error"] == "invoice_origin"
    assert "8443" not in hidden.text
    assert ">Pay</a>" not in client.get(f"/cosigner/{token}").text

    explicit = f"https://{parsed.hostname}:443{parsed.path}"
    packet["invoice_payment_link"] = explicit
    same_port = client.get(f"/api/cosigner/{token}")
    assert same_port.status_code == 200, same_port.text
    assert same_port.json()["payment"]["url"] == explicit

    packet["invoice_payment_link"] = owned
    shown = client.get(f"/api/cosigner/{token}")
    assert shown.json()["payment"]["url"] == owned


def test_only_an_open_bond_status_keeps_the_link(monkeypatch):
    """A missing, unknown, or inactive status is bond_inactive. The open set is not."""
    for status in ("", "inactive", "posted", "unknown"):
        stores, client = _world(monkeypatch)
        token = _token(client)
        stores["bond_cases"].docs[0]["status"] = status
        calls = {"n": 0}
        _scanner(monkeypatch, calls)
        state = client.get(f"/api/cosigner/{token}")
        assert state.status_code == 409, status
        assert state.json()["error"] == "bond_inactive"
        assert _sign_hidden(state.text)
        refused = _post_scan(client, token)
        assert refused.status_code == 409, status
        assert refused.json()["error"] == "bond_inactive"
        assert calls["n"] == 0
        assert "id_ocr" not in stores["paperwork_packets"].docs[0]

    stores, client = _world(monkeypatch)
    token = _token(client)
    stores["bond_cases"].docs[0]["status"] = "monitoring"
    opened = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    assert client.get(f"/api/cosigner/{token}").json()["sign_url"] == _sign("abc")


def test_failed_verification_releases_the_scan_claim(monkeypatch):
    """A store failure after the claim does not leave the scan closed."""
    import dashboard.services.cosigner_link_service as svc

    stores, client = _world(monkeypatch)
    token = _token(client)
    _scanner(monkeypatch, {"n": 0})
    original = svc._store_scan
    calls = {"n": 0}

    async def _fail_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("scan store failed")
        return await original(*args, **kwargs)

    monkeypatch.setattr(svc, "_store_scan", _fail_once)
    with pytest.raises(RuntimeError, match="scan store failed"):
        _post_scan(client, token)
    assert stores["paperwork_packets"].docs[0].get("cosigner_verified_token") in {None, ""}
    assert "id_ocr" not in stores["paperwork_packets"].docs[0]
    assert len(_audits(stores, "id_verified")) == 0

    _age_scan_cooldown(stores, token)
    opened = _post_scan(client, token)
    assert opened.status_code == 200, opened.text
    assert "DocuSeal" in opened.text
    assert stores["paperwork_packets"].docs[0]["cosigner_verified_token"] == token
    assert len(_audits(stores, "id_verified")) == 1


def test_signed_packet_does_not_get_a_public_link(monkeypatch):
    """A signed or completed packet is not issued a link that can never scan."""
    for status in ("signed", "completed", "complete"):
        stores, client = _world(monkeypatch)
        stores["paperwork_packets"].docs[0]["status"] = status
        created = client.post(
            "/api/paperwork/cosigner-link",
            json={"packet_id": PACKET},
            cookies=_cookie(),
        )
        assert created.status_code == 409, status
        assert created.json()["error"] == "packet_signed"
        assert "token" not in created.json()
        assert _links(stores) == []
        issued = stores.get("audit_events")
        assert issued is None or _audits(stores, "link_issued") == []


def _ready_to_pay(monkeypatch):
    """A signed link on the pay step, with the payment lock installed."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    uploaded = _upload(
        monkeypatch, client, token,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert uploaded.status_code == 200, uploaded.text
    stores["paperwork_packets"].docs[0]["status"] = "completed"
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    pay_db = _install_payment_lock(monkeypatch)
    return stores, client, token, pay_db


def test_issued_link_expires_in_seven_days(monkeypatch):
    """A new bearer link carries a bounded expiry and still opens before then."""
    from datetime import datetime, timedelta, timezone

    stores, client = _world(monkeypatch)
    token = _token(client)
    link = stores["cosigner_links"].docs[0]
    created = datetime.fromisoformat(link["created_at"])
    expires = datetime.fromisoformat(link["expires_at"])
    assert expires - created == timedelta(days=7)
    assert expires > datetime.now(timezone.utc)
    assert client.get(f"/api/cosigner/{token}").status_code == 200
    assert _sign_hidden(client.get(f"/api/cosigner/{token}").text)


def test_inactive_packet_does_not_get_a_public_link(monkeypatch):
    """A voided, canceled, or superseded packet is not issued a bearer link."""
    cases = (
        {"voided": True, "status": "voided"},
        {"status": "canceled"},
        {"status": "cancelled"},
        {"status": "superseded"},
        {"superseded": True, "status": "draft"},
    )
    for patch in cases:
        stores, client = _world(monkeypatch)
        stores["paperwork_packets"].docs[0].update(patch)
        created = client.post(
            "/api/paperwork/cosigner-link",
            json={"packet_id": PACKET},
            cookies=_cookie(),
        )
        assert created.status_code == 409, patch
        assert created.json()["error"] == "packet_inactive"
        assert "token" not in created.json()
        assert _links(stores) == []
        issued = stores.get("audit_events")
        assert issued is None or _audits(stores, "link_issued") == []


def test_audit_outage_releases_the_verification_claim(monkeypatch):
    """A failed audit write still clears the claim so the same link can retry."""
    stores, client = _world(monkeypatch)
    token = _token(client)
    packet = stores["paperwork_packets"].docs[0]
    audits = stores["audit_events"]
    original = audits.insert_one

    async def _down(doc, session=None, **kwargs):
        raise RuntimeError("audit store unavailable")

    audits.insert_one = _down
    calls = {"n": 0}
    _scanner(monkeypatch, calls)
    with pytest.raises(RuntimeError, match="audit store unavailable"):
        _post_scan(client, token)
    assert calls["n"] == 1
    assert packet.get("cosigner_verified_token") in {None, ""}

    audits.insert_one = original
    _age_scan_cooldown(stores, token)
    calls["n"] = 0
    opened = _post_scan(client, token)
    assert opened.status_code == 200, opened.text
    assert calls["n"] == 1
    assert packet.get("cosigner_verified_token") == token


def test_failed_verification_removes_the_stored_scan(monkeypatch):
    """A save failure after the scan does not leave id_ocr or a revoked sibling."""
    import dashboard.services.cosigner_link_service as svc

    stores, client = _world(monkeypatch)
    winner = _token(client)
    sibling = _token(client)
    original = svc._save_link

    async def _fail_verified(link):
        if link.get("id_verified"):
            raise RuntimeError("link save failed")
        return await original(link)

    monkeypatch.setattr(svc, "_save_link", _fail_verified)
    _scanner(monkeypatch, {"n": 0})
    with pytest.raises(RuntimeError, match="link save failed"):
        _post_scan(client, winner)
    packet = stores["paperwork_packets"].docs[0]
    assert "id_ocr" not in packet
    assert "id_ocr" not in stores["bond_cases"].docs[0]
    assert packet.get("cosigner_verified_token") in {None, ""}
    other = next(row for row in stores["cosigner_links"].docs if row["token"] == sibling)
    assert other.get("revoked") is not True
    verified = _audits(stores, "id_verified")
    revoked = _audits(stores, "link_revoked")
    assert len(verified) == 1
    assert len(revoked) == 1
    rolled = _audits(stores, "verification_rolled_back")
    assert len(rolled) == 1
    link_ref = hashlib.sha256(sibling.encode("utf-8")).hexdigest()
    assert rolled[0]["restored_links"] == [{
        "link_ref": link_ref,
        "revoked": False,
        "status": "",
    }]
    assert rolled[0]["new_state"]["restored_links"] == [link_ref]
    assert sibling not in str(rolled)
    assert winner not in str(rolled)
    assert winner in [row["token"] for row in stores["cosigner_links"].docs]

    monkeypatch.setattr(svc, "_save_link", original)
    _age_scan_cooldown(stores, winner)
    opened = _post_scan(client, winner)
    assert opened.status_code == 200, opened.text
    assert other.get("revoked") is True
    assert len(_audits(stores, "id_verified")) == 2
    assert len(_audits(stores, "link_revoked")) == 2
    assert len(_audits(stores, "verification_rolled_back")) == 1


def test_sibling_revocation_writes_an_audit(monkeypatch):
    """Each sibling closed by the first passing scan gets its own audit."""
    stores, client = _world(monkeypatch)
    first = _token(client)
    second = _token(client)
    opened = _upload(
        monkeypatch, client, first,
        {"full_name": NAME, "dl_state": "FL"},
    )
    assert opened.status_code == 200, opened.text
    revoked = _audits(stores, "link_revoked")
    assert len(revoked) == 1
    assert revoked[0]["actor"] == "cosigner_link"
    assert revoked[0]["reason"] == "link_revoked"
    assert revoked[0]["old_state"]["revoked"] is False
    assert revoked[0]["new_state"]["revoked"] is True
    assert revoked[0]["confidence"] == 1.0
    assert second not in str(revoked)
    assert first not in str(revoked)


def test_stale_link_save_keeps_the_payment_stamp(monkeypatch):
    """A scan-style save does not clear or restore the payment stamp."""
    from dashboard.services.cosigner_link_service import _save_link

    stores, client, token, _db = _ready_to_pay(monkeypatch)
    link = stores["cosigner_links"].docs[0]
    link["paid_transaction_id"] = "TX-STALE"
    link["payment_reversed"] = False
    stale = {
        "token": link["token"],
        "timeline": list(link.get("timeline") or []),
        "id_verified": True,
        "lang": "es",
        "id_verified_at": link.get("id_verified_at") or "",
        "paid_transaction_id": "",
        "payment_reversed": True,
    }
    asyncio.run(_save_link(stale))
    saved = stores["cosigner_links"].docs[0]
    assert saved["paid_transaction_id"] == "TX-STALE"
    assert saved["payment_reversed"] is False
    assert saved["lang"] == "es"
    assert client.get(f"/api/cosigner/{token}").json()["lang"] == "es"

    saved["paid_transaction_id"] = ""
    saved["payment_reversed"] = True
    restore = dict(stale)
    restore["paid_transaction_id"] = "TX-STALE"
    restore["payment_reversed"] = False
    restore["lang"] = "en"
    asyncio.run(_save_link(restore))
    assert saved["paid_transaction_id"] == ""
    assert saved["payment_reversed"] is True
    assert saved["lang"] == "en"


def test_unreadable_or_empty_payment_rows_do_not_mark_paid(monkeypatch):
    """No readable charge row means the pay step stays unpaid.

    A read error, a missing transaction, and an empty payments collection
    each write no paid audit. The poller does not hand a needs_review
    charge to that note.
    """
    from dashboard.services.cosigner_link_service import note_cosigner_paid
    from dashboard.services.swipesimple_receipt_poller import _note_cosigner_down_payment

    stores, client, token, pay_db = _ready_to_pay(monkeypatch)
    asyncio.run(note_cosigner_paid(BOOKING, "TX-EMPTY", "500.00", status="approved"))
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    assert not stores["cosigner_links"].docs[0].get("paid_transaction_id")
    assert len(_audits(stores, "paid")) == 0

    stores, client, token, pay_db = _ready_to_pay(monkeypatch)
    _remember_payment_row(
        pay_db,
        transaction_id="TX-OTHER",
        amount="500.00",
        status="approved",
    )
    asyncio.run(note_cosigner_paid(BOOKING, "TX-MISSING", "500.00", status="approved"))
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    assert len(_audits(stores, "paid")) == 0

    stores, client, token, pay_db = _ready_to_pay(monkeypatch)
    _remember_payment_row(
        pay_db,
        transaction_id="TX-ERR",
        amount="500.00",
        status="approved",
    )

    async def _unreadable(*_args, **_kwargs):
        raise RuntimeError("payments unreadable")

    monkeypatch.setattr(
        "dashboard.services.payment_log_claims._read_rows",
        _unreadable,
    )
    asyncio.run(note_cosigner_paid(BOOKING, "TX-ERR", "500.00", status="approved"))
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    assert not stores["cosigner_links"].docs[0].get("paid_transaction_id")
    assert len(_audits(stores, "paid")) == 0

    calls = {"n": 0}

    async def _would_stamp(*_args, **_kwargs):
        calls["n"] += 1

    monkeypatch.setattr(
        "dashboard.services.cosigner_link_service.note_cosigner_paid",
        _would_stamp,
    )
    asyncio.run(_note_cosigner_down_payment(
        {
            "event_kind": "charge",
            "committed": True,
            "needs_review": True,
            "transaction_id": "TX-HOLD",
            "status": "approved",
            "amount": "500.00",
        },
        booking=BOOKING,
        reference="",
        amount="500.00",
    ))
    assert calls["n"] == 0


def test_paid_audit_reads_the_link_on_the_payment_session(monkeypatch):
    """The post-CAS read uses the payment transaction, for paid and reversal."""
    stores, client, token, _db = _recorder_world(monkeypatch)
    seen = []
    links = stores["cosigner_links"]
    original = links.find_one

    async def _find_one(query=None, projection=None, session=None, **kwargs):
        if isinstance(query, dict) and query.get("token"):
            seen.append(session)
        return await original(query, projection)

    links.find_one = _find_one

    paid = _webhook(client, txn="TX-SESSION", amount="500.00")
    assert paid.status_code == 200, paid.text
    assert seen and type(seen[-1]).__name__ == "TxnSession"
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "paid"
    paid_audit = _audits(stores, "paid")[-1]
    assert paid_audit["new_state"]["paid"] is True
    assert paid_audit["old_state"]["paid"] is False

    seen.clear()
    refunded = _webhook(
        client, txn="TX-SESSION", amount="500.00",
        event="payment.refunded", status="refunded",
    )
    assert refunded.status_code == 200, refunded.text
    assert seen and type(seen[-1]).__name__ == "TxnSession"
    assert client.get(f"/api/cosigner/{token}").json()["step"] == "pay"
    reversed_audit = _audits(stores, "payment_reversed")[-1]
    assert reversed_audit["new_state"]["paid"] is False
    assert reversed_audit["new_state"]["payment_reversed"] is True
