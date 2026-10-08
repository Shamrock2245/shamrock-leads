"""Staff test-case mode for Write Bond finalize.

No DocuSeal, CRM, Slack, or inventory network. Parties are sample names.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token
from dashboard.services.docuseal_service import BOND_AGENTS, DocuSealService
from dashboard.services.staff_test_case import (
    DEFAULT_SIGNER_EMAIL,
    packet_is_staff_test,
)

HOUSE_LICENSE = "P139768"
HOUSE_NAME = "Brendan O'Neal"
KAYLA_LICENSE = "G356764"
KAYLA_NAME = "Kayla Lukesic"
FAKE_PHONE = "5550100199"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "ci-not-a-real-secret")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_OSI", "1")
    monkeypatch.setenv("DOCUSEAL_TEMPLATE_ID_PALMETTO", "5")
    monkeypatch.delenv("STAFF_TEST_CASE_MODE", raising=False)
    monkeypatch.delenv("STAFF_TEST_CASE_REAL_POWER", raising=False)
    monkeypatch.delenv("STAFF_TEST_CASE_SIGNER_EMAIL", raising=False)
    monkeypatch.delenv("BOND_AGENT_NAME", raising=False)
    monkeypatch.delenv("BOND_AGENT_LICENSE", raising=False)


def _cookie(role, name="", license_no=""):
    token = _sign_token(
        email="office@example.invalid",
        role=role,
        agent_name=name or None,
        license_number=license_no or None,
        is_admin=role in ("god_admin", "admin"),
    )
    return {COOKIE_NAME: token}


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, length=None):
        return list(self._docs)


class _Store:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.finds = []
        self.updates = []
        self.inserts = []

    async def find_one(self, query, projection=None):
        self.finds.append(query)
        if isinstance(query, dict) and "packet_id" in query and "$or" not in query:
            for doc in self.docs:
                if doc.get("packet_id") == query["packet_id"]:
                    return doc
            return None
        if isinstance(query, dict) and "$or" in query:
            for doc in self.docs:
                for clause in query["$or"]:
                    if not isinstance(clause, dict):
                        continue
                    for key, value in clause.items():
                        if doc.get(key) == value:
                            return doc
            return None
        return None

    def find(self, query, projection=None):
        self.finds.append(("find", query))
        return _Cursor(self.docs)

    async def insert_one(self, doc):
        self.inserts.append(doc)
        self.docs.append(doc)
        return None

    async def update_one(self, query, update, upsert=False):
        self.updates.append((query, update))
        return type("R", (), {"matched_count": 1})()


def _stores(seed=None):
    stores = {}
    for name, docs in (seed or {}).items():
        stores[name] = _Store(docs)

    def collections(name):
        if name not in stores:
            stores[name] = _Store()
        return stores[name]

    return stores, collections


def _client():
    from dashboard.routers.paperwork import paperwork_bp

    app = FastAPI()
    app.include_router(paperwork_bp)
    return TestClient(app)


def _ctx(**extra):
    data = {
        "defendant": {"name": "Sample Party One", "email": "one@example.invalid"},
        "indemnitor": {"name": "Sample Party Two", "email": "two@example.invalid", "phone": FAKE_PHONE},
        "county": "Lee",
        "state": "FL",
        "bond_amount": 5000,
        "premium_amount": 500,
        "match_status": "validated",
        "bond_case_id": "BC-1",
        "match_id": "M-1",
        "defendant_id": "D-1",
        "indemnitor_id": "I-1",
        "case_number": "26CF1",
        "poa_number": "POA1",
        "booking_number": "B1",
        "surety_id": "osi",
        "charges": "SAMPLE",
    }
    data.update(extra)
    return data


def _service(captured):
    svc = DocuSealService(
        base_url="https://sign.example.invalid",
        api_key="test-not-a-real-key",
    )

    async def _create(self, **kwargs):
        captured["kwargs"] = kwargs
        return [
            {"id": 1, "submission_id": 44, "role": "bondsman", "slug": "b", "email": DEFAULT_SIGNER_EMAIL},
            {"id": 2, "submission_id": 44, "role": "indemnitor", "slug": "i", "email": DEFAULT_SIGNER_EMAIL},
            {"id": 3, "submission_id": 44, "role": "defendant", "slug": "d", "email": DEFAULT_SIGNER_EMAIL},
        ]

    svc.create_submission = _create.__get__(svc, DocuSealService)
    return svc


def _bondsman(submitters):
    for item in submitters:
        if item.get("role") == "bondsman":
            return item
    raise AssertionError("bondsman submitter missing")


def test_flag_off_finalize_still_hits_override_poa_and_chain_gates(monkeypatch):
    captured = {}
    svc = _service(captured)
    resolve = AsyncMock(return_value=_ctx())
    ensure = AsyncMock(return_value={"success": False, "error": "chain_blocked", "status_code": 409})
    delivery = AsyncMock()
    stores, collections = _stores()
    client = _client()
    client.cookies.update(_cookie("god_admin"))

    with patch("dashboard.services.packet_builder_service.resolve_case_context", new=resolve), \
         patch("dashboard.services.packet_builder_service.resolve_client_esign_provider", new=AsyncMock(return_value="docuseal")), \
         patch("dashboard.routers.helpers.reject_unless_write_book", new=AsyncMock(return_value=None)), \
         patch("dashboard.services.staff_chain_service.ensure_match_bondcase", new=ensure), \
         patch("dashboard.routers.paperwork.get_collection", side_effect=collections), \
         patch("dashboard.extensions.get_collection", side_effect=collections), \
         patch("dashboard.services.docuseal_service.get_docuseal_service", return_value=svc), \
         patch("dashboard.services.docuseal_initial_delivery.deliver_initial_docuseal_links", new=delivery):
        blocked = client.post(
            "/api/paperwork/packet/finalize",
            json={"surety_id": "osi", "field_overrides": {"defendant_name": "Someone Else"}},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"] == "packet_binding_override_blocked"
        assert "kwargs" not in captured

        poa = client.post("/api/paperwork/packet/finalize", json={"surety_id": "osi", "packet_id": "PKT-GATE"})
        assert poa.status_code == 422, poa.text
        assert poa.json()["error"] == "docuseal_poa_not_assigned"
        assert stores["poa_inventory"].finds

        resolve.return_value = _ctx(match_status="pending", bond_case_id="", match_id="")
        chain = client.post(
            "/api/paperwork/packet/finalize",
            json={"surety_id": "osi", "booking_number": "B1"},
        )
        assert chain.status_code == 409, chain.text
        assert chain.json()["error"] == "chain_blocked"
        assert ensure.await_count == 1

        disabled = client.post(
            "/api/paperwork/packet/finalize",
            json={"test_case": True, "booking_number": "TEST-SMOKE1", "surety_id": "osi"},
        )
    assert disabled.status_code == 403, disabled.text
    assert disabled.json()["error"] == "staff_test_case_disabled"
    assert delivery.await_count == 0
    assert "kwargs" not in captured


def test_test_mode_requires_staff_session(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    client = _client()
    body = {"test_case": True, "booking_number": "TEST-SMOKE1", "surety_id": "osi"}
    missing = client.post("/api/paperwork/packet/finalize", json=body)
    assert missing.status_code == 401
    assert missing.json()["error"] == "staff_session_required"

    client.cookies.update(_cookie("sub_agent", "Sample Agent", "W000000"))
    sub = client.post("/api/paperwork/packet/finalize", json=body)
    assert sub.status_code == 401
    assert sub.json()["error"] == "staff_session_required"


def test_test_mode_rejects_real_booking_and_real_arrest(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    stores, collections = _stores({
        "arrests": [{"booking_number": "TEST-REAL1", "is_test": False}],
    })
    client = _client()
    client.cookies.update(_cookie("admin"))
    with patch("dashboard.extensions.get_collection", side_effect=collections), \
         patch("dashboard.routers.paperwork.get_collection", side_effect=collections):
        real = client.post(
            "/api/paperwork/packet/finalize",
            json={"test_case": True, "booking_number": "1099999", "surety_id": "osi"},
        )
        assert real.status_code == 409, real.text
        assert real.json()["error"] == "test_booking_required"
        assert "poa_inventory" not in stores

        arrest = client.post(
            "/api/paperwork/packet/finalize",
            json={
                "test_case": True,
                "booking_number": "TEST-REAL1",
                "case_number": "TEST-CASE-REAL1",
                "surety_id": "osi",
            },
        )
    assert arrest.status_code == 409, arrest.text
    assert arrest.json()["error"] == "real_arrest_refused"
    assert stores["arrests"].updates == []
    assert stores["arrests"].inserts == []
    assert "poa_inventory" not in stores


def test_test_mode_does_not_consume_a_real_power(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    stores, collections = _stores()
    client = _client()
    client.cookies.update(_cookie("staff"))
    with patch("dashboard.extensions.get_collection", side_effect=collections), \
         patch("dashboard.routers.paperwork.get_collection", side_effect=collections):
        refused = client.post(
            "/api/paperwork/packet/finalize",
            json={
                "test_case": True,
                "booking_number": "TEST-SMOKE1",
                "case_number": "TEST-CASE-SMOKE1",
                "poa_number": "OSI-100",
                "allow_real_power": True,
                "surety_id": "osi",
            },
        )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"] == "real_power_not_authorized"
    assert "poa_inventory" not in stores or stores["poa_inventory"].finds == []


def test_test_mode_packet_forces_staff_email_and_agent_pairs(monkeypatch):
    monkeypatch.setenv("STAFF_TEST_CASE_MODE", "1")
    assert BOND_AGENTS[HOUSE_LICENSE]["agent_name"] == HOUSE_NAME
    assert BOND_AGENTS[KAYLA_LICENSE]["agent_name"] == KAYLA_NAME
    captured = {}
    svc = _service(captured)
    delivery = AsyncMock()
    payment = AsyncMock()
    ensure = AsyncMock()
    stores, collections = _stores()
    client = _client()
    client.cookies.update(_cookie("god_admin"))
    body = {
        "test_case": True,
        "surety_id": "osi",
        "booking_number": "TEST-SMOKE1",
        "case_number": "TEST-CASE-SMOKE1",
        "packet_id": "PKT-TEST-SMOKE1",
        "defendant_name": "Sample Party One",
        "indemnitor_name": "Sample Party Two",
        "indemnitor_email": "not-a-person@example.invalid",
        "indemnitor_phone": FAKE_PHONE,
        "bond_amount": 5000,
        "county": "Lee",
        "state": "FL",
        "send_email": True,
        "send_sms": True,
    }
    with patch("dashboard.services.packet_builder_service.resolve_client_esign_provider", new=AsyncMock(return_value="docuseal")), \
         patch("dashboard.services.staff_chain_service.ensure_match_bondcase", new=ensure), \
         patch("dashboard.routers.paperwork.get_collection", side_effect=collections), \
         patch("dashboard.extensions.get_collection", side_effect=collections), \
         patch("dashboard.services.docuseal_service.get_docuseal_service", return_value=svc), \
         patch("dashboard.services.docuseal_initial_delivery.deliver_initial_docuseal_links", new=delivery), \
         patch("dashboard.routers.paperwork._finalize_auto_payment_link", new=payment):
        house = client.post("/api/paperwork/packet/finalize", json=body)
        assert house.status_code == 200, house.text
        house_body = house.json()
        assert house_body["is_test"] is True
        assert house_body["packet_id"] == "PKT-TEST-SMOKE1"
        assert house_body["real_power_consumed"] is False
        kwargs = captured["kwargs"]
        assert kwargs["send_email"] is False
        assert kwargs["send_sms"] is False
        blob = json.dumps(kwargs)
        assert FAKE_PHONE not in blob
        assert "not-a-person@example.invalid" not in blob
        kayla_email = BOND_AGENTS[KAYLA_LICENSE].get("agent_email") or ""
        if kayla_email and kayla_email != DEFAULT_SIGNER_EMAIL:
            assert kayla_email not in blob
        for submitter in kwargs["submitters"]:
            assert submitter["email"] == DEFAULT_SIGNER_EMAIL
            assert not submitter.get("phone")
            assert submitter["send_email"] is False
            assert submitter["send_sms"] is False
        bondsman = _bondsman(kwargs["submitters"])
        assert bondsman["name"] == HOUSE_NAME
        assert bondsman["values"]["agent_license"] == HOUSE_LICENSE
        assert bondsman["values"]["agent_name"] == HOUSE_NAME

        captured.clear()
        kayla_body = dict(body)
        kayla_body["packet_id"] = "PKT-TEST-SMOKE2"
        kayla_body["booking_number"] = "TEST-SMOKE2"
        kayla_body["case_number"] = "TEST-CASE-SMOKE2"
        kayla_body["license_number"] = KAYLA_LICENSE
        kayla = client.post("/api/paperwork/packet/finalize", json=kayla_body)
        assert kayla.status_code == 200, kayla.text
        kayla_bond = _bondsman(captured["kwargs"]["submitters"])
        assert kayla_bond["name"] == KAYLA_NAME
        assert kayla_bond["values"]["agent_license"] == KAYLA_LICENSE
        assert kayla_bond["email"] == DEFAULT_SIGNER_EMAIL
        assert not kayla_bond.get("phone")

    assert delivery.await_count == 0
    assert payment.await_count == 0
    assert ensure.await_count == 0
    assert stores["poa_inventory"].finds == [] if "poa_inventory" in stores else True
    assert "poa_inventory" not in stores
    audits = stores["audit_events"].inserts
    assert len(audits) == 2
    for row in audits:
        assert row["is_test"] is True
        assert row["test_case"] is True
        assert str(row["packet_id"]).startswith("PKT-TEST-")
        assert str(row["booking_number"]).startswith("TEST-")
    for packet in stores["paperwork_packets"].inserts:
        assert packet["is_test"] is True
        assert packet["premium_amount"] == 0
        assert not packet.get("indemnitor_phone")
        assert not packet.get("defendant_phone")
        assert packet["indemnitor_email"] == DEFAULT_SIGNER_EMAIL


def _sig(body: bytes, secret: str) -> str:
    ts = int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"{ts}.{mac}"


def test_docuseal_webhook_tags_test_packet_audit(monkeypatch):
    monkeypatch.setenv("DOCUSEAL_WEBHOOK_SECRET", "whsec_test-not-real")
    monkeypatch.setenv("ENV", "test")
    from dashboard.routers.webhooks import webhooks_bp

    packet = {
        "packet_id": "PKT-TEST-HOOK1",
        "is_test": True,
        "booking_number": "TEST-HOOK1",
        "docuseal_submission_id": 77,
    }
    stores, collections = _stores({"paperwork_packets": [packet]})
    app = FastAPI()
    app.include_router(webhooks_bp)
    client = TestClient(app)
    payload = {
        "event_type": "form.viewed",
        "data": {
            "id": 77,
            "external_id": "PKT-TEST-HOOK1:indemnitor:0",
            "metadata": {"packet_id": "PKT-TEST-HOOK1"},
        },
    }
    raw = json.dumps(payload).encode()
    with patch("dashboard.routers.webhooks.get_collection", side_effect=collections):
        response = client.post(
            "/api/webhooks/docuseal",
            content=raw,
            headers={"X-DocuSeal-Signature": _sig(raw, "whsec_test-not-real"), "content-type": "application/json"},
        )
    assert response.status_code == 200, response.text
    audits = stores["audit_events"].inserts
    assert len(audits) == 1
    assert audits[0]["is_test"] is True
    assert audits[0]["test_case"] is True
    assert audits[0]["packet_id"] == "PKT-TEST-HOOK1"
    assert audits[0]["booking_number"] == "TEST-HOOK1"
    assert packet_is_staff_test(packet) is True
    assert packet_is_staff_test({"packet_id": "pkt-test-0002", "booking_number": "TEST-BK-0002"}) is False


@pytest.mark.asyncio
async def test_completion_skips_customer_steps_for_test_packet():
    from dashboard.services.docuseal_completion import _Run

    doc = {
        "_id": "oid-test",
        "packet_id": "PKT-TEST-HOOK1",
        "is_test": True,
        "booking_number": "TEST-HOOK1",
        "docuseal_completion": {"claim_token": "tok"},
    }
    col = _Store([doc])

    def get_col(name):
        return col

    run = _Run(
        doc,
        get_col=get_col,
        source="webhook",
        submission_id=77,
        event_type="submission.completed",
        cfg={},
    )

    async def _forbidden(self):
        raise AssertionError("customer step ran")

    for name in (
        "step_drive",
        "step_packet",
        "step_bond_cases",
        "step_event",
        "step_legacy_payment_link",
        "step_share_invoice",
        "step_court",
        "step_slack",
    ):
        setattr(run, name, _forbidden.__get__(run, _Run))

    result = await run.execute()
    assert result["staff_test_case"] is True
    reasons = []
    for _query, update in col.updates:
        sets = update.get("$set") or {}
        for key, value in sets.items():
            if key.endswith(".reason"):
                reasons.append(value)
    assert reasons
    assert set(reasons) == {"staff_test_case"}
