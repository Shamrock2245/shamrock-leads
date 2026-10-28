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


class _UpdateResult:
    def __init__(self, matched: int):
        self.matched_count = matched
        self.modified_count = matched


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

    async def insert_one(self, doc, session=None, **kwargs):
        await asyncio.sleep(0)
        if self.fail_insert:
            raise RuntimeError("write failed")
        for fields in self.unique:
            key = tuple(doc.get(f) for f in fields)
            if any(tuple(d.get(f) for f in fields) == key for d in self.docs):
                raise DuplicateKeyError("dup")
        # Mongo's ``_id`` index is always unique. Missing ``_id`` is not a key
        # here (the double does not assign one).
        explicit_id = doc.get("_id")
        if explicit_id is not None and any(d.get("_id") == explicit_id for d in self.docs):
            raise DuplicateKeyError("dup _id")
        self.docs.append(copy.deepcopy(doc))

    async def find_one(self, flt=None, *a, **k):
        await asyncio.sleep(0)
        for d in self.docs:
            if matches(d, flt or {}):
                return copy.deepcopy(d)
        return None

    def find(self, flt=None, *a, **k):
        docs = [copy.deepcopy(d) for d in self.docs if matches(d, flt or {})]

        class _Cur:
            def __aiter__(self):
                self._it = iter(docs)
                return self

            async def __anext__(self):
                try:
                    return next(self._it)
                except StopIteration:
                    raise StopAsyncIteration

        return _Cur()

    async def update_one(self, flt, update, upsert=False, session=None, **kwargs):
        await asyncio.sleep(0)
        for d in self.docs:
            if matches(d, flt):
                for k, v in (update.get("$inc") or {}).items():
                    d[k] = d.get(k, 0) + v
                d.update(update.get("$set") or {})
                for key in (update.get("$unset") or {}):
                    d.pop(key, None)
                return _UpdateResult(1)
        if not upsert:
            return _UpdateResult(0)
        doc = {}
        for key, expected in (flt or {}).items():
            if not isinstance(expected, dict):
                doc[key] = expected
        doc.update(update.get("$setOnInsert") or {})
        for key, value in (update.get("$inc") or {}).items():
            doc[key] = doc.get(key, 0) + value
        doc.update(update.get("$set") or {})
        for fields in self.unique:
            key = tuple(doc.get(field) for field in fields)
            if any(tuple(existing.get(field) for field in fields) == key for existing in self.docs):
                raise DuplicateKeyError("dup")
        explicit_id = doc.get("_id")
        if explicit_id is not None and any(existing.get("_id") == explicit_id for existing in self.docs):
            raise DuplicateKeyError("dup _id")
        self.docs.append(copy.deepcopy(doc))
        return _UpdateResult(1)

    async def delete_one(self, flt, session=None, **kwargs):
        await asyncio.sleep(0)
        kept = []
        deleted = 0
        for d in self.docs:
            if matches(d, flt or {}):
                deleted += 1
            else:
                kept.append(d)
        self.docs[:] = kept

        class _Result:
            def __init__(self, count):
                self.deleted_count = count

        return _Result(deleted)

    async def delete_one(self, flt, session=None, **kwargs):
        await asyncio.sleep(0)
        kept = []
        deleted = 0
        for d in self.docs:
            if matches(d, flt or {}):
                deleted += 1
            else:
                kept.append(d)
        self.docs[:] = kept

        class _Result:
            def __init__(self, count):
                self.deleted_count = count

        return _Result(deleted)


class DB(dict):
    def __missing__(self, name):
        self[name] = Coll()
        return self[name]


PLAN_ID = "plan-test-01"


class _Hello:
    async def command(self, name, *args, **kwargs):
        if name in ("hello", "ismaster"):
            return {"setName": "atlas-rs", "ok": 1}
        raise RuntimeError(name)


class TxnSession:
    """Commits on success. Any other error restores the snapshot taken at the start."""

    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def _restore(self, snap):
        for name, docs in snap.items():
            self.client.db[name].docs = copy.deepcopy(docs)

    async def with_transaction(self, callback):
        while True:
            snap = {name: copy.deepcopy(coll.docs) for name, coll in self.client.db.items()}
            try:
                await callback(self)
                return
            except pp.RetryableTxnError:
                self._restore(snap)
                continue
            except Exception:
                self._restore(snap)
                raise


class TxnClient:
    def __init__(self, db):
        self.db = db
        self.admin = _Hello()

    async def start_session(self):
        return TxnSession(self)


@pytest.fixture
def rig(monkeypatch):
    db = DB()
    db["payment_plans"].docs.append({
        "plan_id": PLAN_ID, "booking_number": "TEST-PP-1", "total_premium": 1000.0,
        "down_payment": 200.0, "balance_remaining": 800.0, "installment_amount": 100.0,
        "frequency": "weekly", "payments_made": 1, "total_paid": 200.0, "status": "active",
    })
    pp._inflight_txns.clear()
    pp._txn_support_cache.clear()

    client = TxnClient(db)

    async def _txn():
        return client

    monkeypatch.setattr(pp, "get_collection", lambda name: db[name])
    monkeypatch.setattr(pp, "_payment_claim_index_ready", False)
    monkeypatch.setattr(pp, "_txn_client", _txn)
    import dashboard.services.payment_log_claims as payment_log
    monkeypatch.setattr(payment_log, "_lock_index_ready", False)
    monkeypatch.setattr(payment_log, "_index_ready", False)
    payment_log._txn_support_cache.clear()
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
    applied = [r for r in rs if r.status_code == 200 and r.json()["idempotent"] is False]
    assert len(applied) == 1
    for r in rs:
        if r is applied[0]:
            continue
        # Losers never report a fresh application; mid-write they get 409 in progress.
        assert (r.status_code == 409 and r.json()["error"] == "payment_in_progress") or (
            r.status_code == 200 and r.json()["idempotent"] is True)
    assert len(db["payments"].docs) == 1
    assert _plan(db)["balance_remaining"] == 700.0
    assert _plan(db)["payments_made"] == 2
    # Once the winner finished, a retry is an idempotent success.
    (again,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-RACE"}])
    assert again.status_code == 200 and again.json()["idempotent"] is True


def test_stale_claimed_claim_is_not_resumed(rig):
    """A claim left claimed is not applied on retry. There is no step resume."""
    db, app = rig
    db["payment_plan_payment_claims"].docs.append(
        {"plan_id": PLAN_ID, "transaction_id": "TX-STUCK", "status": "claimed", "amount": 100})
    db["payment_plan_payment_claims"].unique.append(("tenant_id", "transaction_id"))
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-STUCK"}])
    assert r.status_code == 409 and r.json()["error"] == "payment_in_progress"
    assert db["payments"].docs == []
    assert _plan(db)["balance_remaining"] == 800.0
    assert db["payment_plan_payment_claims"].docs[0]["status"] == "claimed"


@pytest.mark.parametrize("claim_status", ["weird", None])
def test_only_applied_claims_count_as_success(rig, claim_status):
    db, app = rig
    doc = {"plan_id": PLAN_ID, "transaction_id": "TX-S"}
    if claim_status is not None:
        doc["status"] = claim_status
    db["payment_plan_payment_claims"].docs.append(doc)
    db["payment_plan_payment_claims"].unique.append(("tenant_id", "transaction_id"))
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-S"}])
    assert r.status_code == 409 and r.json()["error"] == "payment_in_progress"


def test_concurrent_different_txns_all_apply_without_lost_updates(rig):
    db, app = rig
    rs = _post_many(app, [{"amount": 100, "transaction_id": f"TX-{i}"} for i in range(3)])
    assert all(r.status_code == 200 and r.json()["idempotent"] is False for r in rs)
    assert len(db["payments"].docs) == 3
    assert _plan(db)["balance_remaining"] == 500.0
    assert _plan(db)["total_paid"] == 500.0 and _plan(db)["payments_made"] == 4


def _post_plan(app, plan_id, body):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.post(f"/api/payments/plans/{plan_id}/pay", json=body)
    return asyncio.run(go())


def _add_second_plan(db):
    db["payment_plans"].docs.append({**_plan(db), "plan_id": "plan-test-02",
                                     "balance_remaining": 800.0, "total_paid": 200.0,
                                     "payments_made": 1})
    return db["payment_plans"].docs[1]


def test_same_txn_on_another_plan_is_refused(rig):
    """One card txn / receipt # is one payment: never applied to two plans (Codex P1 #3)."""
    db, app = rig
    _post_many(app, [{"amount": 100, "transaction_id": "TX-1"}])
    other = _add_second_plan(db)
    r = _post_plan(app, "plan-test-02", {"amount": 100, "transaction_id": "TX-1"})
    assert r.status_code == 409 and r.json()["error"] == "transaction_id_used_on_other_plan"
    assert len(db["payments"].docs) == 1
    assert other["balance_remaining"] == 800.0 and _plan(db)["balance_remaining"] == 700.0


def test_same_txn_two_plans_concurrently_applies_once(rig):
    db, app = rig
    other = _add_second_plan(db)

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await asyncio.gather(
                c.post(f"/api/payments/plans/{PLAN_ID}/pay", json={"amount": 100, "transaction_id": "TX-X"}),
                c.post("/api/payments/plans/plan-test-02/pay", json={"amount": 100, "transaction_id": "TX-X"}),
            )
    rs = asyncio.run(go())
    assert sum(1 for r in rs if r.status_code == 200 and r.json().get("idempotent") is False) == 1
    assert all(r.status_code in (200, 409) for r in rs)
    assert len(db["payments"].docs) == 1
    assert _plan(db)["balance_remaining"] + other["balance_remaining"] == 1500.0


def test_legacy_row_on_other_plan_blocks_reuse(rig):
    db, app = rig
    _add_second_plan(db)
    db["payments"].docs.append({"plan_id": "plan-test-02", "transaction_id": "TX-OLD2", "amount": 100})
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-OLD2"}])
    assert r.status_code == 409 and r.json()["error"] == "transaction_id_used_on_other_plan"
    assert _plan(db)["balance_remaining"] == 800.0


def test_unsettled_poller_row_is_refused(rig):
    """A logged row with no settled status is not a second insert and does not move the balance."""
    db, app = rig
    db["payments"].docs.append({"transaction_id": "TX-CARD", "amount": 100, "booking_number": "TEST-PP-1"})
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_not_settled"
    assert _plan(db)["balance_remaining"] == 800.0
    assert len(db["payments"].docs) == 1 and not db["payments"].docs[0].get("plan_id")
    assert db["payment_plan_payment_claims"].docs == []


@pytest.mark.parametrize("amount", [900, 99.99, 100.01])
def test_reused_txn_with_different_amount_refused(rig, amount):
    """Applied $100, then same id with another amount: refuse, don't confirm (Codex P1 #4)."""
    db, app = rig
    _post_many(app, [{"amount": 100, "transaction_id": "TX-AMT"}])
    (r,) = _post_many(app, [{"amount": amount, "transaction_id": "TX-AMT"}])
    assert r.status_code == 409 and r.json()["error"] == "transaction_id_amount_mismatch"
    assert r.json().get("success") is not True
    assert len(db["payments"].docs) == 1 and _plan(db)["balance_remaining"] == 700.0


def test_reused_txn_same_amount_as_string_is_idempotent(rig):
    db, app = rig
    _post_many(app, [{"amount": 100, "transaction_id": "TX-AMT2"}])
    (r,) = _post_many(app, [{"amount": "100.00", "transaction_id": "TX-AMT2"}])
    assert r.status_code == 200 and r.json()["idempotent"] is True


def test_legacy_row_with_different_amount_refused(rig):
    db, app = rig
    db["payments"].docs.append({"plan_id": PLAN_ID, "transaction_id": "TX-L", "amount": 50})
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-L"}])
    assert r.status_code == 409 and r.json()["error"] == "transaction_id_amount_mismatch"


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
    assert claims.index_calls == 1 and claims.unique == [("tenant_id", "transaction_id")]


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


class _FailingPlanUpdate:
    """Wrap the plans collection so update_one raises (payment row already written)."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def update_one(self, *a, **k):
        raise RuntimeError("plan write failed")


def test_payment_row_from_failed_attempt_is_not_legacy_success(rig, monkeypatch):
    """A balance write that raises aborts the transaction. The row and the balance stay put.

    The claim is marked failed. A retry is 409, not a second application and not
    an already-paid legacy row.
    """
    db, app = rig
    failing = _FailingPlanUpdate(db["payment_plans"])
    monkeypatch.setattr(pp, "get_collection",
                        lambda name: failing if name == "payment_plans" else db[name])
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-HALF"}])
    assert r.status_code == 500
    assert r.json().get("success") is not True
    assert db["payments"].docs == []
    assert db["payment_plan_payment_claims"].docs[0]["status"] == "failed"
    assert _plan(db)["balance_remaining"] == 800.0
    monkeypatch.setattr(pp, "get_collection", lambda name: db[name])
    (r2,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-HALF"}])
    assert r2.status_code == 409 and r2.json()["error"] == "payment_needs_review"
    assert db["payments"].docs == [] and _plan(db)["balance_remaining"] == 800.0


def test_unfinished_claim_is_not_resumed(rig):
    """A still-claimed id is not finished on retry, even if a payment row is already present."""
    db, app = rig
    db["payments"].docs.append({
        "plan_id": PLAN_ID, "transaction_id": "TX-MID", "amount": 100,
        "booking_number": "TEST-PP-1", "status": "completed", "type": "installment",
    })
    db["payment_plan_payment_claims"].docs.append(
        {"plan_id": PLAN_ID, "transaction_id": "TX-MID", "status": "claimed", "amount": 100})
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-MID"}])
    assert r.status_code == 409 and r.json()["error"] == "payment_in_progress"
    assert len(db["payments"].docs) == 1 and _plan(db)["balance_remaining"] == 800.0
    assert db["payment_plan_payment_claims"].docs[0]["status"] == "claimed"


def test_payment_row_with_applied_claim_is_idempotent(rig):
    db, app = rig
    _post_many(app, [{"amount": 100, "transaction_id": "TX-OK"}])
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-OK"}])
    assert r.status_code == 200 and r.json()["idempotent"] is True
    assert len(db["payments"].docs) == 1 and _plan(db)["balance_remaining"] == 700.0


@pytest.mark.parametrize("amount", ["100.001", 100.004, "1e-3", "NaN", "Infinity", True, "", "  "])
def test_fractional_cent_or_non_numeric_amount_refused(rig, amount):
    """Codex P2: no fractional cents, so idempotency compares the exact applied amount."""
    db, app = rig
    (r,) = _post_many(app, [{"amount": amount, "transaction_id": "TX-FC"}])
    assert r.status_code == 400 and r.json()["error"] == "invalid_amount"
    assert db["payments"].docs == [] and db["payment_plan_payment_claims"].docs == []
    assert _plan(db)["balance_remaining"] == 800.0


def test_retry_matches_exact_cents(rig):
    db, app = rig
    _post_many(app, [{"amount": "100.10", "transaction_id": "TX-C"}])
    (same,) = _post_many(app, [{"amount": 100.1, "transaction_id": "TX-C"}])
    assert same.status_code == 200 and same.json()["idempotent"] is True
    (diff,) = _post_many(app, [{"amount": "100.11", "transaction_id": "TX-C"}])
    assert diff.status_code == 409 and diff.json()["error"] == "transaction_id_amount_mismatch"
    assert len(db["payments"].docs) == 1


def test_legacy_row_with_fractional_cents_is_not_confirmed(rig):
    db, app = rig
    db["payments"].docs.append({"plan_id": PLAN_ID, "transaction_id": "TX-LF", "amount": 100.004})
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-LF"}])
    assert r.status_code == 409 and r.json()["error"] == "transaction_id_amount_mismatch"


@pytest.mark.parametrize("amount", [
    "1.00000000000000000000000000001",       # Codex P2: 28-digit context used to round this to 1.00
    "100.000000000000000000000000000001",
    "0.001", "1e-3", "1e400", "12345678901234567890.12", "1" * 70,
])
def test_high_precision_or_huge_amounts_refused_without_rounding(rig, amount):
    db, app = rig
    (r,) = _post_many(app, [{"amount": amount, "transaction_id": "TX-HP"}])
    assert r.status_code == 400 and r.json()["error"] == "invalid_amount"
    assert db["payments"].docs == [] and db["payment_plan_payment_claims"].docs == []


@pytest.mark.parametrize("amount,cents", [
    ("1.00", 100), ("1.000000000000000000000000000000000", 100), ("1E+2", 10000),
    ("1e-2", 1), (99.99, 9999), ("5e1", 5000),
])
def test_exact_cents_parsing(amount, cents):
    assert pp._amount_cents(amount) == cents


def test_high_precision_replay_is_not_treated_as_same_payment(rig):
    db, app = rig
    _post_many(app, [{"amount": "1.00", "transaction_id": "TX-HP2"}])
    (r,) = _post_many(app, [{"amount": "1.00000000000000000000000000001", "transaction_id": "TX-HP2"}])
    assert r.status_code == 400
    assert len(db["payments"].docs) == 1


def _post_raw(app, raw: bytes):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.post(f"/api/payments/plans/{PLAN_ID}/pay", content=raw,
                                headers={"Content-Type": "application/json"})
    return asyncio.run(go())


@pytest.mark.parametrize("token", [
    "1.00000000000000000000000000001",      # Codex P2: unquoted JSON number used to become float 1.0
    "100.000000000000000000000001", "0.001", "1e-3", "1e400", "NaN", "Infinity", "-Infinity",
])
def test_unquoted_json_number_with_fractional_cents_refused(rig, token):
    db, app = rig
    raw = ('{"amount": %s, "transaction_id": "TX-RAW"}' % token).encode()
    r = _post_raw(app, raw)
    assert r.status_code == 400
    assert db["payments"].docs == [] and db["payment_plan_payment_claims"].docs == []


@pytest.mark.parametrize("token,balance", [("100", 700.0), ("100.10", 699.9), ("1E+2", 700.0), ("99.99", 700.01)])
def test_unquoted_json_number_exact_cents_accepted(rig, token, balance):
    db, app = rig
    r = _post_raw(app, ('{"amount": %s, "transaction_id": "TX-RAW2"}' % token).encode())
    assert r.status_code == 200
    assert round(_plan(db)["balance_remaining"], 2) == balance


def test_malformed_json_body_refused(rig):
    db, app = rig
    assert _post_raw(app, b'{"amount": 1,,}').status_code == 400
    assert _post_raw(app, b"").status_code == 400
