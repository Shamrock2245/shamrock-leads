"""POST /api/payments/plans/{plan_id}/pay — one application per (plan, transaction id).

Offline: an in-memory Mongo double with a real unique-index check and an
``await`` between every read and write, so concurrent requests interleave.
"""
from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from pymongo.errors import DuplicateKeyError

from dashboard.routers import payment_plans as pp
from tests._inmem_mongo import matches


class Coll:
    def __init__(self):
        self.docs: list[dict] = []
        self.unique: list[tuple[str, ...]] = []
        self.index_calls = 0
        self.fail_index = False
        self.fail_insert = False

    async def create_index(self, keys, unique=False, name=None, **_):
        await asyncio.sleep(0)
        self.index_calls += 1
        if self.fail_index:
            raise RuntimeError("index unavailable")
        fields = tuple(k for k, _ in keys) if isinstance(keys, list) else (keys,)
        if unique and fields not in self.unique:
            self.unique.append(fields)
        return name

    async def insert_one(self, doc):
        await asyncio.sleep(0)
        if self.fail_insert:
            raise RuntimeError("write failed")
        for fields in self.unique:
            key = tuple(doc.get(f) for f in fields)
            if any(tuple(d.get(f) for f in fields) == key for d in self.docs):
                raise DuplicateKeyError("dup")
        self.docs.append(copy.deepcopy(doc))

    async def find_one(self, flt=None, *a, **k):
        await asyncio.sleep(0)
        for d in self.docs:
            if matches(d, flt or {}):
                return copy.deepcopy(d)
        return None

    async def update_one(self, flt, update, upsert=False):
        await asyncio.sleep(0)
        for d in self.docs:
            if matches(d, flt):
                for k, v in update.get("$inc", {}).items():
                    d[k] = d.get(k, 0) + v
                d.update(update.get("$set", {}))
                return
        return


class DB(dict):
    def __missing__(self, name):
        self[name] = Coll()
        return self[name]


PLAN_ID = "plan-test-01"


@pytest.fixture
def rig(monkeypatch):
    db = DB()
    db["payment_plans"].docs.append({
        "plan_id": PLAN_ID, "booking_number": "TEST-PP-1", "total_premium": 1000.0,
        "down_payment": 200.0, "balance_remaining": 800.0, "installment_amount": 100.0,
        "frequency": "weekly", "payments_made": 1, "total_paid": 200.0, "status": "active",
    })
    monkeypatch.setattr(pp, "get_collection", lambda name: db[name])
    monkeypatch.setattr(pp, "_payment_claim_index_ready", False)
    app = FastAPI()
    app.include_router(pp.payment_plans_bp)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        yield db, app


def _post_many(app, bodies):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await asyncio.gather(*[
                c.post(f"/api/payments/plans/{PLAN_ID}/pay", json=b) for b in bodies
            ])
    return asyncio.run(go())


def _plan(db):
    return db["payment_plans"].docs[0]


def test_first_payment_applies(rig):
    db, app = rig
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-1"}])
    assert r.status_code == 200 and r.json()["idempotent"] is False
    assert _plan(db)["balance_remaining"] == 700.0
    assert _plan(db)["total_paid"] == 300.0 and _plan(db)["payments_made"] == 2
    assert len(db["payments"].docs) == 1
    claim = db["payment_plan_payment_claims"].docs[0]
    assert claim["status"] == "applied" and claim["transaction_id"] == "TX-1"


def test_repeat_txn_is_idempotent_no_second_payment(rig):
    db, app = rig
    _post_many(app, [{"amount": 100, "transaction_id": "TX-1"}])
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": " TX-1 "}])
    assert r.status_code == 200
    body = r.json()
    assert body["idempotent"] is True and body["balance_remaining"] == 700.0
    assert len(db["payments"].docs) == 1
    assert _plan(db)["total_paid"] == 300.0 and _plan(db)["payments_made"] == 2


def test_concurrent_same_txn_applies_once(rig):
    db, app = rig
    rs = _post_many(app, [{"amount": 100, "transaction_id": "TX-RACE"}] * 5)
    assert all(r.status_code == 200 for r in rs)
    assert sorted(r.json()["idempotent"] for r in rs) == [False, True, True, True, True]
    assert len(db["payments"].docs) == 1
    assert _plan(db)["balance_remaining"] == 700.0
    assert _plan(db)["payments_made"] == 2


def test_concurrent_different_txns_all_apply_without_lost_updates(rig):
    db, app = rig
    rs = _post_many(app, [{"amount": 100, "transaction_id": f"TX-{i}"} for i in range(3)])
    assert all(r.status_code == 200 and r.json()["idempotent"] is False for r in rs)
    assert len(db["payments"].docs) == 3
    assert _plan(db)["balance_remaining"] == 500.0
    assert _plan(db)["total_paid"] == 500.0 and _plan(db)["payments_made"] == 4


def test_same_txn_on_another_plan_is_separate(rig):
    db, app = rig
    _post_many(app, [{"amount": 100, "transaction_id": "TX-1"}])
    db["payment_plans"].docs.append({**_plan(db), "plan_id": "plan-test-02", "balance_remaining": 800.0})

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.post("/api/payments/plans/plan-test-02/pay",
                                json={"amount": 100, "transaction_id": "TX-1"})
    r = asyncio.run(go())
    assert r.json()["idempotent"] is False
    assert len(db["payments"].docs) == 2


@pytest.mark.parametrize("txn", [None, "", "   "])
def test_missing_transaction_id_refused(rig, txn):
    db, app = rig
    body = {"amount": 100}
    if txn is not None:
        body["transaction_id"] = txn
    (r,) = _post_many(app, [body])
    assert r.status_code == 400 and r.json()["error"] == "transaction_id_required"
    assert db["payments"].docs == [] and _plan(db)["balance_remaining"] == 800.0


@pytest.mark.parametrize("amount", [0, -5, "abc", None])
def test_bad_amount_refused(rig, amount):
    db, app = rig
    (r,) = _post_many(app, [{"amount": amount, "transaction_id": "TX-A"}])
    assert r.status_code == 400
    assert db["payments"].docs == [] and db["payment_plan_payment_claims"].docs == []


def test_legacy_payment_row_without_claim_is_not_reapplied(rig):
    db, app = rig
    db["payments"].docs.append({"plan_id": PLAN_ID, "transaction_id": "TX-OLD", "amount": 100})
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-OLD"}])
    assert r.json()["idempotent"] is True
    assert len(db["payments"].docs) == 1 and _plan(db)["balance_remaining"] == 800.0


def test_index_unavailable_fails_closed(rig):
    db, app = rig
    db["payment_plan_payment_claims"].fail_index = True
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-1"}])
    assert r.status_code == 503 and r.json()["error"] == "payment_dedupe_unavailable"
    assert db["payments"].docs == [] and _plan(db)["balance_remaining"] == 800.0


def test_index_is_created_lazily_once(rig):
    db, app = rig
    _post_many(app, [{"amount": 10, "transaction_id": "TX-1"}])
    _post_many(app, [{"amount": 10, "transaction_id": "TX-2"}])
    claims = db["payment_plan_payment_claims"]
    assert claims.index_calls == 1 and claims.unique == [("plan_id", "transaction_id")]


def test_failed_write_marks_claim_and_retry_needs_review(rig):
    db, app = rig
    db["payments"].fail_insert = True
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-F"}])
    assert r.status_code == 500
    assert db["payment_plan_payment_claims"].docs[0]["status"] == "failed"
    db["payments"].fail_insert = False
    (r2,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-F"}])
    assert r2.status_code == 409 and r2.json()["error"] == "payment_needs_review"
    assert db["payments"].docs == [] and _plan(db)["balance_remaining"] == 800.0


def test_overpayment_clamps_to_zero_and_paid_in_full(rig):
    db, app = rig
    (r,) = _post_many(app, [{"amount": 900, "transaction_id": "TX-BIG"}])
    assert r.json()["status"] == "paid_in_full" and r.json()["balance_remaining"] == 0
    assert _plan(db)["status"] == "paid_in_full"


def test_unknown_plan_404_no_claim(rig):
    db, app = rig

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.post("/api/payments/plans/nope/pay", json={"amount": 1, "transaction_id": "T"})
    assert asyncio.run(go()).status_code == 404
    assert db["payment_plan_payment_claims"].docs == []
