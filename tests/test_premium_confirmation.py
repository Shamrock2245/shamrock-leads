"""Staff premium confirmation at Write Bond. No Mongo, no SwipeSimple, no network."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token
from dashboard.routers.paperwork import paperwork_bp
from dashboard.services.premium_confirmation_service import (
    cents_to_amount,
    load_plan_creator,
    non_negative_cents,
)
from dashboard.services.swipesimple_invoice_service import (
    resolve_invoice_due,
    resolve_locked_premium,
)
from dashboard.tenancy.flag import multi_tenant_enabled

BOOKING = "TEST-1001"
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

    def find(self, query=None, projection=None, **kwargs):
        rows = [doc for doc in self.docs if self._match(doc, query or {})]

        class _Cur:
            def __init__(self, found):
                self._rows = found

            async def to_list(self, length=None):
                return list(self._rows)

        return _Cur(rows)

    async def find_one(self, query=None, projection=None, **kwargs):
        for doc in self.docs:
            if self._match(doc, query or {}):
                return doc
        return None

    async def update_one(self, query, update, upsert=False, **kwargs):
        doc = await self.find_one(query)
        if doc is None:
            return None
        for key, value in (update.get("$set") or {}).items():
            doc[key] = value
        for key, value in (update.get("$push") or {}).items():
            doc.setdefault(key, []).append(value)
        return None

    async def insert_one(self, doc, **kwargs):
        self.docs.append(dict(doc))
        return None


class _TxnSession:
    def __init__(self, stores):
        self.stores = stores

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def with_transaction(self, callback):
        import copy
        snap = {name: copy.deepcopy(col.docs) for name, col in self.stores.items()}
        try:
            return await callback(self)
        except Exception:
            for name, docs in snap.items():
                self.stores[name].docs = docs
            for name in list(self.stores):
                if name not in snap:
                    self.stores[name].docs = []
            raise


class _TxnClient:
    def __init__(self, stores):
        self.stores = stores

    def start_session(self):
        return _TxnSession(self.stores)


def _world():
    stores = {
        "paperwork_packets": _Col([{
            "packet_id": "PKT-TEST-1",
            "booking_number": BOOKING,
            "bond_case_id": "BC-TEST-1",
            "bond_amount": 15000,
            "surety_id": "osi",
            "defendant_name": "Casey Sample",
            "indemnitor_name": "Casey Sample",
        }]),
        "bond_cases": _Col([{
            "bond_case_id": "BC-TEST-1",
            "booking_number": BOOKING,
            "bond_amount": 15000,
            "surety_id": "osi",
            "defendant_name": "Casey Sample",
            "indemnitor_name": "Casey Sample",
            "id_scan": {
                "success": True,
                "extracted": {
                    "full_name": "Casey Sample",
                    "first_name": "Casey",
                    "last_name": "Sample",
                    "dl_state": "FL",
                },
            },
        }]),
        "active_bonds": _Col([{
            "booking_number": BOOKING,
            "bond_case_id": "BC-TEST-1",
            "bond_amount": 15000,
            "surety_id": "osi",
            "defendant_name": "Casey Sample",
            "indemnitor_name": "Casey Sample",
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
        "packet_id": "PKT-TEST-1",
        "bond_case_id": "BC-TEST-1",
        "premium_confirmed_by": "forged-from-body",
        "created_by": "forged-from-body",
    }
    payload.update(overrides)
    return payload


def _bind(monkeypatch, stores, get):
    monkeypatch.setattr("dashboard.extensions.get_collection", get)
    monkeypatch.setattr("dashboard.services.premium_confirmation_service.get_collection", get)

    async def txn():
        return _TxnClient(stores)

    monkeypatch.setattr(
        "dashboard.services.premium_confirmation_service._transaction_client",
        txn,
    )


def _confirmed(doc):
    return "premium_confirmed_amount" in doc or "premium_confirmed_at" in doc


def _silence_sends(monkeypatch):
    """Record every send or charge the confirmation must not make."""
    spies = {
        "swipe_http": AsyncMock(),
        "swipe_create": AsyncMock(),
        "swipe_dispatch": AsyncMock(),
        "docuseal": AsyncMock(),
        "imessage": AsyncMock(),
        "imessage_direct": AsyncMock(),
        "sms": AsyncMock(),
        "email": AsyncMock(),
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
        "dashboard.services.docuseal_service.DocuSealService.create_submission",
        spies["docuseal"],
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
    return spies


def _assert_no_sends(spies):
    for spy in spies.values():
        spy.assert_not_called()


def _install_plan_writer(monkeypatch, stores):
    """Stand in for Invoicing's plan function. This service does not insert plans."""

    def creator(*, tenant_id="", bond_case_id="", confirmation_id="", financed=None, installments=0, frequency="", actor="", session=None, **kwargs):
        count = int(installments)
        cents = int((Decimal(str(financed)) * 100).to_integral_value())
        base, extra = divmod(cents, count) if count else (0, 0)
        schedule = [
            {
                "n": index + 1,
                "amount_cents": base + (extra if index == count - 1 else 0),
                "status": "scheduled",
            }
            for index in range(count)
        ]
        plans = stores["payment_plans"].docs
        existing = plans[0] if plans else None
        if existing and existing.get("confirmation_id") == confirmation_id:
            return {"plan_id": existing["plan_id"], "created": False, "schedule": existing["installments"]}
        if existing:
            existing.update({
                "confirmation_id": confirmation_id,
                "bond_case_id": bond_case_id,
                "installments": schedule,
                "balance_remaining_cents": cents,
                "created_by": actor,
                "source": "premium_confirmation",
                "frequency": frequency,
            })
            return {"plan_id": existing["plan_id"], "created": False, "schedule": schedule}
        plan = {
            "plan_id": "plan-TEST-1",
            "booking_number": BOOKING,
            "confirmation_id": confirmation_id,
            "bond_case_id": bond_case_id,
            "source": "premium_confirmation",
            "installments": schedule,
            "balance_remaining_cents": cents,
            "created_by": actor,
            "payments_made": 0,
            "status": "active",
            "frequency": frequency,
        }
        if tenant_id:
            plan["tenant_id"] = tenant_id
        plans.append(plan)
        return {"plan_id": plan["plan_id"], "created": True, "schedule": schedule}

    monkeypatch.setattr(
        "dashboard.services.premium_confirmation_service.load_plan_creator",
        lambda: creator,
    )


def _hide_plan_module(monkeypatch):
    """Pretend ``payment_plan_creation`` is not importable.

    #211 lands that module. These cases still cover the fail-closed path
    the confirmation service uses when the writer is absent.
    """
    import importlib

    name = "dashboard.services.payment_plan_creation"
    real_import = importlib.import_module

    def _blocked(mod_name, package=None):
        if mod_name == name:
            raise ImportError(name)
        return real_import(mod_name, package)

    monkeypatch.setattr(importlib, "import_module", _blocked)


def _assert_nothing_written(stores):
    for name in ("paperwork_packets", "bond_cases", "active_bonds"):
        assert not _confirmed(stores[name].docs[0])
    assert stores["audit_events"].docs == []
    assert stores["payment_plans"].docs == []
    assert not any(doc.get("source") == "premium_confirmation" for doc in stores["payment_plans"].docs)


def test_confirm_writes_trio_audit_and_one_plan(monkeypatch):
    stores, get, client = _world()
    _bind(monkeypatch, stores, get)
    _install_plan_writer(monkeypatch, stores)
    spies = _silence_sends(monkeypatch)
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

    # The signing invoice is the down payment. Reconciliation uses the full premium.
    confirmed = stores["active_bonds"].docs[0]
    assert resolve_locked_premium(confirmed) == Decimal("1500.00")
    assert resolve_invoice_due(confirmed) == Decimal("500.00")
    _assert_no_sends(spies)


def test_amount_change_requires_reason_and_keeps_history(monkeypatch):
    stores, get, client = _world()
    _bind(monkeypatch, stores, get)
    _install_plan_writer(monkeypatch, stores)
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
    stores, get, client = _world()
    _bind(monkeypatch, stores, get)
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
    _bind(monkeypatch, stores, get)
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
    stores, get, client = _world()
    _bind(monkeypatch, stores, get)
    _install_plan_writer(monkeypatch, stores)
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
    _bind(monkeypatch, stores, get)
    _install_plan_writer(monkeypatch, stores)
    response = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert response.status_code == 200
    assert "tenant_id" not in stores["payment_plans"].docs[0]
    assert cents_to_amount(100000) == "1000.00"


def test_unverified_indemnitor_is_422_and_writes_nothing(monkeypatch):
    stores, get, client = _world()
    del stores["bond_cases"].docs[0]["id_scan"]
    _bind(monkeypatch, stores, get)
    spies = _silence_sends(monkeypatch)
    response = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert response.status_code == 422
    assert response.json()["error"] == "indemnitor_identity_unverified"
    for name in ("paperwork_packets", "bond_cases", "active_bonds"):
        assert not _confirmed(stores[name].docs[0])
    assert stores["audit_events"].docs == []
    assert stores["payment_plans"].docs == []
    _assert_no_sends(spies)


def test_paid_in_full_rolls_back_when_the_audit_write_fails(monkeypatch):
    stores, get, client = _world()

    class _Boom(_Col):
        async def insert_one(self, doc, **kwargs):
            raise RuntimeError("audit down")

    stores["audit_events"] = _Boom()
    _bind(monkeypatch, stores, get)
    response = client.post(
        "/api/paperwork/confirm-premium",
        json=_body(premium="1500.00", down="1500.00", financed="0.00", installments=0),
        cookies=_cookie(),
    )
    assert response.status_code == 503
    assert response.json()["error"] == "confirmation_audit_failed"
    for name in ("paperwork_packets", "bond_cases", "active_bonds"):
        assert not _confirmed(stores[name].docs[0])
    assert stores["payment_plans"].docs == []
    assert stores["audit_events"].docs == []


def test_plan_terms_conflict_is_409_and_rolls_back(monkeypatch):
    stores, get, client = _world()
    _bind(monkeypatch, stores, get)

    class PlanTermsConflict(Exception):
        pass

    def creator(**kwargs):
        raise PlanTermsConflict("financed terms differ")

    monkeypatch.setattr(
        "dashboard.services.premium_confirmation_service.load_plan_creator",
        lambda: creator,
    )
    response = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert response.status_code == 409
    assert response.json()["error"] == "plan_terms_conflict"
    for name in ("paperwork_packets", "bond_cases", "active_bonds"):
        assert not _confirmed(stores[name].docs[0])
    assert stores["payment_plans"].docs == []
    assert stores["audit_events"].docs == []


def test_missing_plan_module_writes_nothing(monkeypatch):
    _hide_plan_module(monkeypatch)
    stores, get, client = _world()
    _bind(monkeypatch, stores, get)
    assert load_plan_creator() is None
    spies = _silence_sends(monkeypatch)
    response = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert response.status_code == 503
    assert response.json()["error"] == "plan_unavailable"
    _assert_nothing_written(stores)
    again = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert again.status_code == 503
    _assert_nothing_written(stores)
    _assert_no_sends(spies)


def test_non_callable_plan_function_writes_nothing(monkeypatch):
    import sys
    import types

    module = types.ModuleType("dashboard.services.payment_plan_creation")
    module.ensure_payment_plan_for_confirmation = "not-callable"
    monkeypatch.setitem(sys.modules, "dashboard.services.payment_plan_creation", module)
    assert load_plan_creator() is None

    stores, get, client = _world()
    _bind(monkeypatch, stores, get)
    response = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert response.status_code == 503
    assert response.json()["error"] == "plan_unavailable"
    _assert_nothing_written(stores)


def test_paid_in_full_confirms_without_a_plan_module(monkeypatch):
    _hide_plan_module(monkeypatch)
    stores, get, client = _world()
    _bind(monkeypatch, stores, get)
    assert load_plan_creator() is None
    response = client.post(
        "/api/paperwork/confirm-premium",
        json=_body(premium="1500.00", down="1500.00", financed="0.00", installments=0),
        cookies=_cookie(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["financed_balance"] == "0.00"
    assert stores["active_bonds"].docs[0]["premium_confirmed_amount"] == "1500.00"
    assert stores["payment_plans"].docs == []
    assert len(stores["audit_events"].docs) == 1


def test_confirm_does_not_send_when_live_flags_are_on(monkeypatch):
    monkeypatch.setenv("SWIPESIMPLE_LIVE", "1")
    monkeypatch.setenv("SWIPESIMPLE_DISPATCH_LIVE", "1")
    stores, get, client = _world()
    _bind(monkeypatch, stores, get)
    _install_plan_writer(monkeypatch, stores)
    spies = _silence_sends(monkeypatch)
    response = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert response.status_code == 200, response.text
    _assert_no_sends(spies)


def test_split_identifiers_are_422_and_write_nothing(monkeypatch):
    stores, get, client = _world()
    stores["paperwork_packets"].docs.append({
        "packet_id": "PKT-TEST-2",
        "booking_number": "TEST-1002",
        "bond_case_id": "BC-TEST-2",
        "bond_amount": 15000,
        "indemnitor_name": "Casey Sample",
    })
    stores["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-2",
        "booking_number": "TEST-1002",
        "bond_amount": 15000,
        "indemnitor_name": "Casey Sample",
    })
    stores["active_bonds"].docs.append({
        "booking_number": "TEST-1002",
        "bond_case_id": "BC-TEST-2",
        "bond_amount": 15000,
    })
    _bind(monkeypatch, stores, get)
    mismatches = (
        {"packet_id": "PKT-TEST-1", "booking_number": "TEST-1002", "bond_case_id": "BC-TEST-1"},
        {"packet_id": "PKT-TEST-1", "booking_number": "TEST-1001", "bond_case_id": "BC-TEST-2"},
        {"packet_id": "PKT-TEST-2", "booking_number": "TEST-1001", "bond_case_id": "BC-TEST-2"},
    )
    for overrides in mismatches:
        response = client.post(
            "/api/paperwork/confirm-premium",
            json=_body(**overrides),
            cookies=_cookie(),
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"] == "case_identity_conflict"
    for name in ("paperwork_packets", "bond_cases", "active_bonds"):
        for doc in stores[name].docs:
            assert not _confirmed(doc)
    assert stores["audit_events"].docs == []
    assert stores["payment_plans"].docs == []


def test_down_payment_receipt_does_not_mark_the_premium_paid(monkeypatch):
    from dashboard.services.swipesimple_invoice_service import (
        SwipeSimpleInvoiceError,
        reconcile_payment,
    )

    bond = {
        "booking_number": "TEST-1001",
        "swipesimple_invoice_number": "TEST-1001",
        "premium_confirmed_amount": "1500.00",
        "premium_confirmed_at": "2026-10-15T12:00:00+00:00",
        "premium_confirmed_by": ACTOR,
        "down_payment_amount": "500.00",
        "financed_balance": "1000.00",
        "premium_paid": False,
    }
    active = dict(bond)
    stores = {"bond_cases": _Col([bond]), "active_bonds": _Col([active])}

    def get(name):
        if name not in stores:
            stores[name] = _Col()
        return stores[name]

    monkeypatch.setattr("dashboard.extensions.get_collection", get)
    monkeypatch.setattr("dashboard.services.swipesimple_invoice_service.get_collection", get)
    ledger = AsyncMock()
    monkeypatch.setattr("dashboard.services.ledger_service.LedgerService.add_entry", ledger)
    assert resolve_locked_premium(bond) == Decimal("1500.00")
    assert resolve_invoice_due(bond) == Decimal("500.00")
    receipt = {
        "reference_id": "TEST-1001",
        "status": "paid",
        "transaction_id": "txn-TEST-1",
        "amount": "500.00",
    }
    with pytest.raises(SwipeSimpleInvoiceError, match="receipt_partial_payment"):
        asyncio.run(reconcile_payment("TEST-1001", receipt))
    assert bond.get("premium_paid") is not True
    assert active.get("premium_paid") is not True
    ledger.assert_not_called()


def test_unimplemented_plan_is_503_and_rolls_back(monkeypatch):
    stores, get, client = _world()
    _bind(monkeypatch, stores, get)

    def creator(**kwargs):
        raise NotImplementedError("plan module is a stub")

    monkeypatch.setattr(
        "dashboard.services.premium_confirmation_service.load_plan_creator",
        lambda: creator,
    )
    response = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert response.status_code == 503
    assert response.json()["error"] == "plan_unavailable"
    for name in ("paperwork_packets", "bond_cases", "active_bonds"):
        assert not _confirmed(stores[name].docs[0])
    assert stores["payment_plans"].docs == []
    assert stores["audit_events"].docs == []


def test_transactions_unavailable_writes_nothing(monkeypatch):
    stores, get, client = _world()
    _bind(monkeypatch, stores, get)

    async def unavailable():
        return None

    monkeypatch.setattr(
        "dashboard.services.premium_confirmation_service._transaction_client",
        unavailable,
    )
    response = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert response.status_code == 503
    assert response.json()["error"] == "transactions_unavailable"
    for name in ("paperwork_packets", "bond_cases", "active_bonds"):
        assert not _confirmed(stores[name].docs[0])
    assert stores["payment_plans"].docs == []
    assert stores["audit_events"].docs == []


def test_confirm_without_a_packet_binds_the_bond_case(monkeypatch):
    stores, get, client = _world()
    stores["paperwork_packets"].docs.clear()
    _bind(monkeypatch, stores, get)
    _install_plan_writer(monkeypatch, stores)
    body = _body()
    body.pop("packet_id")
    response = client.post("/api/paperwork/confirm-premium", json=body, cookies=_cookie())
    assert response.status_code == 200, response.text
    assert stores["paperwork_packets"].docs == []
    assert stores["bond_cases"].docs[0]["premium_confirmed_amount"] == "1500.00"
    assert stores["active_bonds"].docs[0]["down_payment_amount"] == "500.00"
    assert len(stores["audit_events"].docs) == 1
    assert len(stores["payment_plans"].docs) == 1


def test_flag_on_does_not_update_another_tenant(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    stores, get, client = _world()
    for name in ("paperwork_packets", "bond_cases", "active_bonds"):
        stores[name].docs[0]["tenant_id"] = "shamrock"
    stores["bond_cases"].docs.append({
        "bond_case_id": "BC-TEST-2",
        "booking_number": "TEST-1002",
        "tenant_id": "otherco",
        "bond_amount": 15000,
        "indemnitor_name": "Casey Sample",
    })
    stores["active_bonds"].docs.append({
        "booking_number": "TEST-1002",
        "bond_case_id": "BC-TEST-2",
        "tenant_id": "otherco",
        "bond_amount": 15000,
    })
    _bind(monkeypatch, stores, get)
    _install_plan_writer(monkeypatch, stores)
    monkeypatch.setattr(
        "dashboard.services.premium_confirmation_service.current_tenant_id",
        lambda: "shamrock",
    )
    response = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert response.status_code == 200, response.text
    assert stores["payment_plans"].docs[0]["tenant_id"] == "shamrock"
    other_case = stores["bond_cases"].docs[1]
    other_bond = stores["active_bonds"].docs[1]
    assert not _confirmed(other_case)
    assert not _confirmed(other_bond)
    assert other_case["tenant_id"] == "otherco"


def test_later_packet_uses_the_bond_case_confirmation(monkeypatch):
    stores, get, client = _world()
    _bind(monkeypatch, stores, get)
    _install_plan_writer(monkeypatch, stores)
    spies = _silence_sends(monkeypatch)
    assert client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie()).status_code == 200
    packet = {
        "packet_id": "PKT-TEST-2",
        "booking_number": BOOKING,
        "bond_case_id": "BC-TEST-1",
        "docuseal_completion": {"claim_token": "lease-test"},
    }
    from dashboard.services.docuseal_completion import _Run
    from dashboard.services.swipesimple_invoice_service import SwipeSimpleLiveDisabled

    async def live_off(*args, **kwargs):
        raise SwipeSimpleLiveDisabled("SWIPESIMPLE_LIVE off")

    monkeypatch.setattr(
        "dashboard.services.swipesimple_invoice_service.maybe_issue_share_invoice_for_bond",
        live_off,
    )
    runner = _Run(
        packet,
        get_col=get,
        source="test",
        submission_id="sub-test",
        event_type="form.completed",
        cfg={},
    )
    asyncio.run(runner.step_share_invoice())
    step = runner.doc["docuseal_completion"]["steps"]["share_invoice"]
    assert step["state"] == "done"
    assert step.get("dry_run") is True
    assert step.get("reason") != "premium_unconfirmed"
    assert step.get("amount") == "500.00"
    assert step.get("dispatched") is False
    _assert_no_sends(spies)


def test_unconfirmed_packet_with_no_sibling_stays_unconfirmed(monkeypatch):
    stores, get, _client = _world()
    for name in ("paperwork_packets", "bond_cases", "active_bonds"):
        stores[name].docs.clear()
    _bind(monkeypatch, stores, get)
    from dashboard.services.docuseal_completion import _Run

    called = {"issue": 0}

    async def issue(*args, **kwargs):
        called["issue"] += 1
        raise AssertionError("unconfirmed packet must not invoice")

    monkeypatch.setattr(
        "dashboard.services.swipesimple_invoice_service.maybe_issue_share_invoice_for_bond",
        issue,
    )
    packet = {
        "packet_id": "PKT-TEST-2",
        "booking_number": BOOKING,
        "bond_case_id": "BC-TEST-1",
        "docuseal_completion": {"claim_token": "lease-test"},
    }
    runner = _Run(
        packet,
        get_col=get,
        source="test",
        submission_id="sub-test",
        event_type="form.completed",
        cfg={},
    )
    asyncio.run(runner.step_share_invoice())
    step = runner.doc["docuseal_completion"]["steps"]["share_invoice"]
    assert step["state"] == "skipped"
    assert step["reason"] == "premium_unconfirmed"
    assert called["issue"] == 0


def test_existing_invoice_amount_must_match_the_confirmation():
    from dashboard.services.swipesimple_invoice_service import (
        SwipeSimpleInvoiceError,
        create_locked_invoice,
    )

    confirmed = {
        "bond_id": "BOND-TEST-1",
        "booking_number": "TEST-1001",
        "premium_confirmed_amount": "1500.00",
        "premium_confirmed_at": "2026-10-15T12:00:00+00:00",
        "premium_confirmed_by": ACTOR,
        "down_payment_amount": "500.00",
        "financed_balance": "1000.00",
        "swipesimple_payment_link": "https://pay.example.invalid/TEST-1001",
        "swipesimple_invoice_bond_id": "BOND-TEST-1",
        "swipesimple_invoice_number": "TEST-1001",
    }

    async def run(bond):
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("SWIPESIMPLE_TEST_CASE_INVOICES", "1")
            http = AsyncMock()
            mp.setattr("dashboard.services.swipesimple_invoice_service._share_invoice_http", http)
            mp.setattr("dashboard.services.swipesimple_invoice_service._http_create_invoice", http)
            mp.setattr(
                "dashboard.services.swipesimple_invoice_service._load_bond_by_id",
                AsyncMock(return_value=bond),
            )
            try:
                result = await create_locked_invoice("BOND-TEST-1")
            except SwipeSimpleInvoiceError as exc:
                http.assert_not_called()
                return str(exc)
            http.assert_not_called()
            return result

    unproven = asyncio.run(run(dict(confirmed)))
    assert unproven == "invoice_amount_unproven"
    mismatch = asyncio.run(run({**confirmed, "swipesimple_invoice_amount_cents": 100}))
    assert mismatch == "invoice_amount_mismatch"
    matched = asyncio.run(run({**confirmed, "swipesimple_invoice_amount_cents": 50000}))
    assert matched["idempotent"] is True
    assert matched["payment_link"].endswith("TEST-1001")
    assert matched["premium_amount"] == 500.0


def test_reconfirm_same_amounts_passes_the_session(monkeypatch):
    """A retry joins the plan function to the confirmation session.

    Without that session the plan function opens its own transaction and
    returns 503 plan_write_failed when Mongo is not a replica set.
    """
    from dashboard.services.payment_plan_creation import ensure_payment_plan_for_confirmation

    stores, get, client = _world()
    _bind(monkeypatch, stores, get)
    monkeypatch.setattr("dashboard.services.payment_plan_creation.get_collection", get)
    monkeypatch.setenv("SWIPESIMPLE_LIVE", "1")
    monkeypatch.setenv("SWIPESIMPLE_DISPATCH_LIVE", "1")
    spies = _silence_sends(monkeypatch)
    results = []

    async def wrapped(**kwargs):
        result = await ensure_payment_plan_for_confirmation(**kwargs)
        results.append({"session": kwargs.get("session"), "created": result.get("created")})
        return result

    monkeypatch.setattr(
        "dashboard.services.premium_confirmation_service.load_plan_creator",
        lambda: wrapped,
    )

    def counts():
        return {
            "plans": len(stores["payment_plans"].docs),
            "premium_audits": len([
                row for row in stores["audit_events"].docs
                if row.get("action") == "premium_confirmed"
            ]),
            "plan_audits": len([
                row for row in stores["audit_events"].docs
                if row.get("action") == "payment_plan_created"
            ]),
        }

    first = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert first.status_code == 200, first.text
    assert first.json()["idempotent"] is False
    assert len(results) == 1
    assert results[0]["session"] is not None
    assert results[0]["created"] is True
    after_first = counts()
    assert after_first == {"plans": 1, "premium_audits": 1, "plan_audits": 1}

    second = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["idempotent"] is True
    assert body["plan_created"] is False
    assert len(results) == 2
    assert results[1]["session"] is not None
    assert results[1]["created"] is False
    assert counts() == after_first

    blocked = client.post(
        "/api/paperwork/confirm-premium",
        json=_body(premium="1600.00", down="600.00", financed="1000.00"),
        cookies=_cookie(),
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"] == "premium_change_requires_reason"
    assert len(results) == 2
    assert counts() == after_first
    assert stores["active_bonds"].docs[0]["premium_confirmed_amount"] == "1500.00"

    del stores["bond_cases"].docs[0]["id_scan"]
    denied = client.post("/api/paperwork/confirm-premium", json=_body(), cookies=_cookie())
    assert denied.status_code == 422
    assert denied.json()["error"] == "indemnitor_identity_unverified"
    assert len(results) == 2
    assert counts() == after_first
    for name in ("paperwork_packets", "active_bonds"):
        assert stores[name].docs[0]["premium_confirmed_amount"] == "1500.00"
    _assert_no_sends(spies)
