"""Staff premium confirmation at Write Bond. No Mongo, no SwipeSimple, no network."""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token
from dashboard.routers.paperwork import paperwork_bp
from dashboard.services.premium_confirmation_service import (
    cents_to_amount,
    non_negative_cents,
)
from dashboard.services.swipesimple_invoice_service import resolve_locked_premium
from dashboard.tenancy.flag import multi_tenant_enabled

BOOKING = "BK-CONFIRM-1"
ACTOR = "office@example.invalid"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "ci-not-a-real-secret")
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)


def _cookie(role="staff", email=ACTOR, name="Office Staff"):
    token = _sign_token(
        email=email,
        role=role,
        agent_name=name,
        is_admin=role in ("god_admin", "admin"),
    )
    return {COOKIE_NAME: token}


class _Col:
    def __init__(self, docs=None):
        self.docs = [dict(doc) for doc in (docs or [])]

    def _match(self, doc, query):
        if not query:
            return True
        if "$or" in query:
            return any(self._match(doc, clause) for clause in query["$or"])
        for key, expected in query.items():
            if isinstance(expected, dict) and "$in" in expected:
                if doc.get(key) not in expected["$in"]:
                    return False
            elif doc.get(key) != expected:
                return False
        return True

    async def find_one(self, query=None, projection=None):
        for doc in self.docs:
            if self._match(doc, query or {}):
                return doc
        return None

    async def update_one(self, query, update, upsert=False):
        doc = await self.find_one(query)
        if doc is None:
            return None
        for key, value in (update.get("$set") or {}).items():
            doc[key] = value
        for key, value in (update.get("$push") or {}).items():
            doc.setdefault(key, []).append(value)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return None


def _world():
    stores = {
        "paperwork_packets": _Col([{
            "packet_id": "PKT-1",
            "booking_number": BOOKING,
            "bond_case_id": "BC-1",
            "bond_amount": 15000,
            "surety_id": "osi",
            "defendant_name": "Casey Sample",
        }]),
        "bond_cases": _Col([{
            "bond_case_id": "BC-1",
            "booking_number": BOOKING,
            "bond_amount": 15000,
            "surety_id": "osi",
            "defendant_name": "Casey Sample",
        }]),
        "active_bonds": _Col([{
            "booking_number": BOOKING,
            "bond_amount": 15000,
            "surety_id": "osi",
            "defendant_name": "Casey Sample",
            "premium_cents": 150000,
        }]),
        "payment_plans": _Col(),
        "audit_events": _Col(),
    }

    def get(name):
        if name not in stores:
            stores[name] = _Col()
        return stores[name]

    app = FastAPI()
    app.include_router(paperwork_bp)
    return stores, get, TestClient(app)


def _body(**overrides):
    payload = {
        "premium": "1500.00",
        "down": "500.00",
        "financed": "1000.00",
        "installments": 4,
        "frequency": "weekly",
        "booking_number": BOOKING,
        "packet_id": "PKT-1",
        "bond_case_id": "BC-1",
        "premium_confirmed_by": "forged-from-body",
        "created_by": "forged-from-body",
    }
    payload.update(overrides)
    return payload


def test_confirm_writes_trio_audit_and_one_plan(monkeypatch):
    stores, get, client = _world()
    monkeypatch.setattr("dashboard.extensions.get_collection", get)
    monkeypatch.setattr("dashboard.services.premium_confirmation_service.get_collection", get)
    first = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["premium_confirmed_by"] == ACTOR
    assert body["premium_confirmed_amount"] == "1500.00"
    assert body["down_payment_amount"] == "500.00"
    assert body["financed_balance"] == "1000.00"
    assert body["idempotent"] is False

    for name in ("paperwork_packets", "bond_cases", "active_bonds"):
        doc = stores[name].docs[0]
        assert doc["premium_confirmed_amount"] == "1500.00"
        assert doc["premium_confirmed_at"]
        assert doc["premium_confirmed_by"] == ACTOR
        assert doc["down_payment_amount"] == "500.00"
        assert doc["premium_is_estimate"] is False

    audits = [row for row in stores["audit_events"].docs if row.get("action") == "premium_confirmed"]
    assert len(audits) == 1
    assert audits[0]["actor"] == ACTOR
    assert "forged-from-body" not in str(audits[0])

    plans = stores["payment_plans"].docs
    assert len(plans) == 1
    plan = plans[0]
    assert plan["source"] == "premium_confirmation"
    assert len(plan["installments"]) == 4
    assert sum(row["amount_cents"] for row in plan["installments"]) == 100000
    assert plan["balance_remaining_cents"] == 100000
    assert plan["created_by"] == ACTOR
    assert plan["payments_made"] == 0

    second = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert second.status_code == 200, second.text
    assert second.json()["idempotent"] is True
    assert len(stores["payment_plans"].docs) == 1
    assert len([row for row in stores["audit_events"].docs if row.get("action") == "premium_confirmed"]) == 1

    # The signing invoice is the down payment, not the financed balance.
    due = resolve_locked_premium(stores["active_bonds"].docs[0])
    assert due == Decimal("500.00")


def test_amount_change_requires_reason_and_keeps_history(monkeypatch):
    stores, get, client = _world()
    monkeypatch.setattr("dashboard.services.premium_confirmation_service.get_collection", get)
    assert client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie()).status_code == 200
    blocked = client.post(
        "/api/paperwork/confirm-premium",
        json=_body(premium="1600.00", down="600.00", financed="1000.00"),
        cookies=_cookie(),
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"] == "premium_change_requires_reason"
    assert stores["active_bonds"].docs[0]["premium_confirmed_amount"] == "1500.00"

    changed = client.post(
        "/api/paperwork/confirm-premium",
        json=_body(premium="1600.00", down="600.00", financed="1000.00", reason="court reset the bond"),
        cookies=_cookie(),
    )
    assert changed.status_code == 200, changed.text
    doc = stores["active_bonds"].docs[0]
    assert doc["premium_confirmed_amount"] == "1600.00"
    assert doc["premium_confirmation_history"][0]["premium_confirmed_amount"] == "1500.00"
    assert len(stores["payment_plans"].docs) == 1
    assert len([row for row in stores["audit_events"].docs if row.get("action") == "premium_confirmed"]) == 2


def test_sub_cent_and_unbalanced_amounts_are_400(monkeypatch):
    _stores, get, client = _world()
    monkeypatch.setattr("dashboard.services.premium_confirmation_service.get_collection", get)
    sub = client.post(
        "/api/paperwork/confirm-premium",
        json=_body(premium="1500.001"),
        cookies=_cookie(),
    )
    assert sub.status_code == 400
    assert sub.json()["error"] == "invalid_amount"
    assert non_negative_cents("1500.001") is None
    assert non_negative_cents("1500.00") == 150000

    bad = client.post(
        "/api/paperwork/confirm-premium",
        json=_body(financed="999.99"),
        cookies=_cookie(),
    )
    assert bad.status_code == 400
    assert bad.json()["error"] == "amounts_do_not_balance"


def test_below_filed_rate_needs_a_reason(monkeypatch):
    stores, get, client = _world()
    monkeypatch.setattr("dashboard.services.premium_confirmation_service.get_collection", get)
    low = client.post(
        "/api/paperwork/confirm-premium",
        json=_body(premium="100.00", down="100.00", financed="0.00", installments=0),
        cookies=_cookie(),
    )
    assert low.status_code == 409
    assert low.json()["error"] == "premium_below_filed_rate"
    allowed = client.post(
        "/api/paperwork/confirm-premium",
        json=_body(
            premium="100.00", down="100.00", financed="0.00",
            reason="family hardship, owner approved",
        ),
        cookies=_cookie(),
    )
    assert allowed.status_code == 200, allowed.text
    assert stores["payment_plans"].docs == []
    assert allowed.json()["below_filed_rate"] is True


def test_auth_and_recovery_fail_closed(monkeypatch):
    _stores, get, client = _world()
    monkeypatch.setattr("dashboard.services.premium_confirmation_service.get_collection", get)
    missing = client.post("/api/paperwork/confirm-premium", json=_body())
    assert missing.status_code == 401
    recovery = client.post(
        "/api/paperwork/confirm-premium",
        json=_body(),
        cookies=_cookie(role="recovery", email="recovery@example.invalid"),
    )
    assert recovery.status_code == 403
    sub = client.post(
        "/api/paperwork/confirm-premium",
        json=_body(),
        cookies=_cookie(role="sub_agent", email="desk@example.invalid", name="Desk Agent"),
    )
    assert sub.status_code == 200, sub.text
    assert sub.json()["premium_confirmed_by"] == "desk@example.invalid"


def test_tenant_flag_off_does_not_require_tenant_id(monkeypatch):
    assert multi_tenant_enabled() is False
    stores, get, client = _world()
    assert "tenant_id" not in stores["active_bonds"].docs[0]
    monkeypatch.setattr("dashboard.services.premium_confirmation_service.get_collection", get)
    response = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert response.status_code == 200
    assert "tenant_id" not in stores["payment_plans"].docs[0]
    assert cents_to_amount(100000) == "1000.00"
