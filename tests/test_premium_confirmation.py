"""Staff premium confirmation at Write Bond.

Mocked Mongo, a TEST- case, and a mocked plan function. No network, no
SwipeSimple, and no production writes.
"""
from __future__ import annotations

import asyncio
import copy
import os
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token
from dashboard.tenancy.context import bind_job_tenant
from tests._inmem_mongo import _set_path, matches

ACTOR = "office@example.invalid"
CASE_ID = "BC-TEST-1"
BOOKING = "TEST-1001"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "ci-not-a-real-secret")
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)
    monkeypatch.delenv("SWIPESIMPLE_LIVE", raising=False)
    monkeypatch.delenv("SWIPESIMPLE_DISPATCH_LIVE", raising=False)


def _cookie(role="staff", email=ACTOR):
    return _sign_token(email=email, role=role, agent_name="Office Staff")


class Col:
    def __init__(self):
        self.docs: list[dict] = []

    def find(self, flt=None, projection=None, **kwargs):
        rows = [copy.deepcopy(doc) for doc in self.docs if matches(doc, flt or {})]

        class _Cur:
            def __init__(self, found):
                self._rows = found

            async def to_list(self, length=None):
                return list(self._rows)

            def __aiter__(self):
                self._it = iter(self._rows)
                return self

            async def __anext__(self):
                try:
                    return next(self._it)
                except StopIteration:
                    raise StopAsyncIteration

        return _Cur(rows)

    async def find_one(self, flt=None, *args, **kwargs):
        for doc in self.docs:
            if matches(doc, flt or {}):
                return copy.deepcopy(doc)
        return None

    async def insert_one(self, doc, session=None, **kwargs):
        self.docs.append(copy.deepcopy(doc))

    def _apply(self, doc, update):
        for key, value in (update.get("$set") or {}).items():
            _set_path(doc, key, copy.deepcopy(value))
        for key, value in (update.get("$push") or {}).items():
            bucket = doc.get(key)
            if not isinstance(bucket, list):
                bucket = []
                doc[key] = bucket
            bucket.append(copy.deepcopy(value))

    async def update_many(self, flt, update, session=None, **kwargs):
        matched = 0
        for doc in self.docs:
            if matches(doc, flt or {}):
                self._apply(doc, update)
                matched += 1
        return SimpleNamespace(matched_count=matched, modified_count=matched)

    async def update_one(self, flt, update, upsert=False, session=None, **kwargs):
        return await self.update_many(flt, update, session=session)


class DB(dict):
    def __missing__(self, name):
        self[name] = Col()
        return self[name]


class TxnSession:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def with_transaction(self, callback):
        snap = {name: copy.deepcopy(col.docs) for name, col in self.db.items()}
        try:
            await callback(self)
        except Exception:
            for name, docs in snap.items():
                self.db[name].docs = docs
            for name in list(self.db):
                if name not in snap:
                    self.db[name].docs = []
            raise


class TxnClient:
    def __init__(self, db):
        self.db = db

    def start_session(self):
        return TxnSession(self.db)


def _scan():
    return {
        "success": True,
        "extracted": {
            "full_name": "Jamie Sample",
            "first_name": "Jamie",
            "last_name": "Sample",
            "dl_state": "FL",
        },
    }


def _case(**extra):
    doc = {
        "bond_case_id": CASE_ID,
        "booking_number": BOOKING,
        "packet_id": "PKT-TEST-1",
        "bond_amount": "15000.00",
        "indemnitor_name": "Jamie Sample",
        "indemnitors": [{"name": "Jamie Sample", "role": "indemnitor"}],
        "county": "Lee",
        "tenant_id": "shamrock",
    }
    doc.update(extra)
    return doc


def _seed(db, *, scan=True):
    case = _case()
    if scan:
        case["id_scan"] = _scan()
    db["bond_cases"].docs.append(case)
    db["paperwork_packets"].docs.append(_case())
    db["active_bonds"].docs.append(_case())
    db["audit_events"]
    return db


def _body(**extra):
    payload = {
        "bond_case_id": CASE_ID,
        "booking_number": BOOKING,
        "premium": "1500.00",
        "down": "500.00",
        "financed": "1000.00",
        "installments": 4,
        "frequency": "weekly",
        "premium_confirmed_by": "body-actor",
        "actor": "body-actor",
    }
    payload.update(extra)
    return payload


def _app(db, monkeypatch, plan):
    from dashboard.routers.paperwork import paperwork_bp
    from dashboard.tenancy.scope import apply_scope

    def get_collection(name):
        return apply_scope(db[name], name)

    async def txn_client():
        return TxnClient(db)

    monkeypatch.setattr("dashboard.extensions.get_collection", get_collection)
    monkeypatch.setattr(
        "dashboard.services.premium_confirmation._transaction_client",
        txn_client,
    )
    monkeypatch.setattr(
        "dashboard.services.premium_confirmation.load_plan_creator",
        lambda: plan,
    )
    app = FastAPI()
    app.include_router(paperwork_bp)
    return app


def _post(app, payload, *, cookie=True, raw=None):
    token = _cookie()

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            if cookie:
                client.cookies.set(COOKIE_NAME, token)
            with bind_job_tenant("shamrock"):
                if raw is not None:
                    return await client.post(
                        "/api/paperwork/confirm-premium",
                        content=raw,
                        headers={"Content-Type": "application/json"},
                    )
                return await client.post("/api/paperwork/confirm-premium", json=payload)

    return asyncio.run(go())


def _confirmed(doc):
    return doc.get("premium_confirmed_amount")


def _audits(db):
    return [doc for doc in db["audit_events"].docs if doc.get("event_type") == "premium_confirmed"]


async def _plan_ok(**kwargs):
    return {"plan_id": "plan-TEST-1", "created": True, "schedule": [{"n": 1, "amount": "250.00"}] * 4}


def test_confirm_writes_three_collections_audit_and_session_actor(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    db = _seed(DB())
    seen = []

    async def plan(**kwargs):
        seen.append(kwargs)
        return await _plan_ok(**kwargs)

    app = _app(db, monkeypatch, plan)
    response = _post(app, _body())
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True and body["idempotent"] is False
    assert body["premium_confirmed_by"] == ACTOR
    for name in ("paperwork_packets", "bond_cases", "active_bonds"):
        doc = db[name].docs[0]
        assert doc["premium_confirmed_amount"] == "1500.00"
        assert doc["premium_confirmed_by"] == ACTOR
        assert doc["premium_confirmed_at"]
        assert doc["premium_confirmed_down"] == "500.00"
        assert doc["premium_confirmed_financed"] == "1000.00"
        assert doc["tenant_id"] == "shamrock"
        assert "body-actor" not in str(doc["premium_confirmed_by"])
    audits = _audits(db)
    assert len(audits) == 1
    assert audits[0]["event_type"] == "premium_confirmed"
    assert audits[0]["actor"] == ACTOR
    assert audits[0]["agent_name"] == ACTOR
    assert audits[0]["tenant_id"] == "shamrock"
    assert len(seen) == 1
    call = seen[0]
    assert call["tenant_id"] == "shamrock"
    assert call["bond_case_id"] == CASE_ID
    assert call["financed"] == Decimal("1000.00")
    assert call["installments"] == 4
    assert call["frequency"] == "weekly"
    assert call["actor"] == ACTOR
    assert call["session"] is not None
    assert str(call["confirmation_id"]).startswith("pcf-")


def test_plan_called_once_and_replay_does_not_create(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    db = _seed(DB())
    seen = []

    async def plan(**kwargs):
        seen.append(kwargs)
        return {"plan_id": "plan-TEST-1", "created": True, "schedule": []}

    app = _app(db, monkeypatch, plan)
    first = _post(app, _body())
    again = _post(app, _body())
    assert first.status_code == 200 and again.status_code == 200
    assert again.json()["idempotent"] is True
    assert len(seen) == 1
    assert len(_audits(db)) == 1
    assert db["bond_cases"].docs[0]["premium_confirmed_amount"] == "1500.00"


def test_change_without_reason_is_409_and_reason_keeps_history(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    db = _seed(DB())
    app = _app(db, monkeypatch, _plan_ok)
    assert _post(app, _body()).status_code == 200
    changed = _body(premium="1600.00", down="600.00", financed="1000.00")
    refused = _post(app, changed)
    assert refused.status_code == 409
    assert refused.json()["error"] == "premium_change_requires_reason"
    assert db["bond_cases"].docs[0]["premium_confirmed_amount"] == "1500.00"
    assert "premium_confirmation_history" not in db["bond_cases"].docs[0]
    assert len(_audits(db)) == 1

    changed["reason"] = "Window adjusted the premium"
    accepted = _post(app, changed)
    assert accepted.status_code == 200
    case = db["bond_cases"].docs[0]
    assert case["premium_confirmed_amount"] == "1600.00"
    history = case["premium_confirmation_history"]
    assert history[0]["premium_confirmed_amount"] == "1500.00"
    assert history[0]["premium_confirmed_by"] == ACTOR
    for name in ("paperwork_packets", "active_bonds"):
        assert db[name].docs[0]["premium_confirmation_history"][0]["premium_confirmed_amount"] == "1500.00"
    assert len(_audits(db)) == 2


def test_subcent_amount_is_400(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    db = _seed(DB())
    seen = []

    async def plan(**kwargs):
        seen.append(kwargs)
        return await _plan_ok()

    app = _app(db, monkeypatch, plan)
    raw = (
        b'{"bond_case_id":"BC-TEST-1","premium":1500.001,"down":500.00,'
        b'"financed":1000.001,"installments":4,"frequency":"weekly"}'
    )
    response = _post(app, None, raw=raw)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_amount"
    assert _confirmed(db["bond_cases"].docs[0]) is None
    assert _audits(db) == []
    assert seen == []


def test_plan_terms_conflict_is_409(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    db = _seed(DB())

    class PlanTermsConflict(Exception):
        pass

    async def plan(**kwargs):
        raise PlanTermsConflict("terms differ")

    app = _app(db, monkeypatch, plan)
    response = _post(app, _body())
    assert response.status_code == 409
    assert response.json()["error"] == "plan_terms_conflict"
    assert _confirmed(db["bond_cases"].docs[0]) is None
    assert _confirmed(db["paperwork_packets"].docs[0]) is None
    assert _confirmed(db["active_bonds"].docs[0]) is None
    assert _audits(db) == []


def test_plan_not_implemented_rolls_back(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    db = _seed(DB())

    async def plan(**kwargs):
        raise NotImplementedError("stub")

    app = _app(db, monkeypatch, plan)
    response = _post(app, _body())
    assert response.status_code == 503
    assert response.json()["error"] == "plan_unavailable"
    assert _confirmed(db["bond_cases"].docs[0]) is None
    assert _confirmed(db["paperwork_packets"].docs[0]) is None
    assert _confirmed(db["active_bonds"].docs[0]) is None
    assert _audits(db) == []


def test_missing_plan_module_and_value_error_write_nothing(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    db = _seed(DB())
    app = _app(db, monkeypatch, None)
    missing = _post(app, _body())
    assert missing.status_code == 503
    assert missing.json()["error"] == "plan_unavailable"
    assert _confirmed(db["bond_cases"].docs[0]) is None

    async def bad(**kwargs):
        raise ValueError("installments do not divide the balance")

    app = _app(db, monkeypatch, bad)
    rejected = _post(app, _body())
    assert rejected.status_code == 400
    assert rejected.json()["error"] == "invalid_plan_terms"
    assert _confirmed(db["bond_cases"].docs[0]) is None
    assert _audits(db) == []


def test_transactions_unavailable_writes_nothing(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    db = _seed(DB())
    app = _app(db, monkeypatch, _plan_ok)

    async def down():
        return None

    monkeypatch.setattr("dashboard.services.premium_confirmation._transaction_client", down)
    response = _post(app, _body())
    assert response.status_code == 503
    assert response.json()["error"] == "transactions_unavailable"
    assert _confirmed(db["bond_cases"].docs[0]) is None
    assert _audits(db) == []


def test_identity_gate_failure_is_422(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    db = _seed(DB(), scan=False)
    app = _app(db, monkeypatch, _plan_ok)
    response = _post(app, _body())
    assert response.status_code == 422
    assert response.json()["error"] == "indemnitor_identity_unverified"
    assert _confirmed(db["bond_cases"].docs[0]) is None
    assert _audits(db) == []


def test_no_session_is_401(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    db = _seed(DB())
    app = _app(db, monkeypatch, _plan_ok)
    response = _post(app, _body(), cookie=False)
    assert response.status_code == 401
    assert response.json()["error"] == "auth_required"
    assert _confirmed(db["bond_cases"].docs[0]) is None
    assert _audits(db) == []


def test_below_filed_rate_requires_reason(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    db = _seed(DB())
    app = _app(db, monkeypatch, _plan_ok)
    low = _body(premium="50.00", down="50.00", financed="0.00", installments=0)
    refused = _post(app, low)
    assert refused.status_code == 400
    assert refused.json()["error"] == "reason_required"
    assert _confirmed(db["bond_cases"].docs[0]) is None
    low["reason"] = "Family rate approved at the counter"
    accepted = _post(app, low)
    assert accepted.status_code == 200
    assert db["bond_cases"].docs[0]["premium_confirmed_amount"] == "50.00"


def _completion_packet(**extra):
    doc = {
        "_id": "pkt1",
        "packet_id": "PKT-TEST-1",
        "bond_case_id": CASE_ID,
        "booking_number": BOOKING,
        "docuseal_completion": {"claim_token": "tok", "steps": {}},
    }
    doc.update(extra)
    return doc


def _run_completion(packet):
    from dashboard.services import docuseal_completion as dc
    from dashboard.services import swipesimple_invoice_service as ss
    from tests._inmem_mongo import FakeCollection

    col = FakeCollection()
    col.docs.append(packet)
    seen = []

    async def share(bond_id, **kwargs):
        seen.append({"bond_id": bond_id, **kwargs})
        return {"ok": True, "create": {"idempotent": False, "premium_amount": 500}, "dispatch": None}

    run = dc._Run(
        packet,
        get_col=lambda name: col,
        source=dc.SOURCE_WEBHOOK,
        submission_id="sub-1",
        event_type="submission.completed",
        cfg={},
    )

    async def go():
        with patch.object(ss, "maybe_issue_share_invoice_for_bond", share):
            await run.step_legacy_payment_link()
            await run.step_share_invoice()

    asyncio.run(go())
    steps = (col.docs[0].get("docuseal_completion") or {}).get("steps") or {}
    return steps, seen


def test_confirmed_completion_is_not_premium_unconfirmed_and_unconfirmed_still_is(monkeypatch):
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    confirmed = _completion_packet(
        premium_confirmed_amount="1500.00",
        premium_confirmed_at="2026-10-14T16:00:00+00:00",
        premium_confirmed_by=ACTOR,
        premium_confirmed_down="500.00",
    )
    steps, seen = _run_completion(confirmed)
    assert steps["legacy_payment_link"].get("reason") != "premium_unconfirmed"
    assert steps["share_invoice"].get("reason") != "premium_unconfirmed"
    assert steps["share_invoice"]["state"] == "done"
    assert len(seen) == 1
    assert seen[0]["dispatch"] is False
    assert seen[0]["amount"] == Decimal("500.00")
    assert (os.environ.get("SWIPESIMPLE_LIVE") or "") == ""
    assert (os.environ.get("SWIPESIMPLE_DISPATCH_LIVE") or "") == ""

    unconfirmed = _completion_packet(premium="1500.00", premium_amount="1500.00")
    steps, seen = _run_completion(unconfirmed)
    assert steps["legacy_payment_link"]["state"] == "skipped"
    assert steps["legacy_payment_link"]["reason"] == "premium_unconfirmed"
    assert steps["share_invoice"]["state"] == "skipped"
    assert steps["share_invoice"]["reason"] == "premium_unconfirmed"
    assert seen == []


def test_flag_off_does_not_change_behavior(monkeypatch):
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)
    db = _seed(DB())
    called = []

    async def plan(**kwargs):
        called.append(kwargs)
        return await _plan_ok()

    app = _app(db, monkeypatch, plan)
    response = _post(app, _body())
    assert response.status_code == 404
    assert response.json()["error"] == "premium_confirmation_disabled"
    assert _confirmed(db["bond_cases"].docs[0]) is None
    assert _confirmed(db["paperwork_packets"].docs[0]) is None
    assert _confirmed(db["active_bonds"].docs[0]) is None
    assert _audits(db) == []
    assert called == []

    packet = _completion_packet(premium="1500.00", premium_cents=150000)
    steps, seen = _run_completion(packet)
    assert steps["share_invoice"].get("reason") != "premium_unconfirmed"
    assert steps["share_invoice"]["state"] == "done"
    assert len(seen) == 1
    assert seen[0]["dispatch"] is False
    assert "amount" not in seen[0]
    assert steps["legacy_payment_link"]["reason"] == "switch_off"
