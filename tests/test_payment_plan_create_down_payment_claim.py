"""POST /api/payments/plans with down_payment_transaction_id — the card txn of
the down payment is claimed in payment_plan_payment_claims (uniq_tenant_txn)
before the plan is created, same one-application rule as /pay.

Offline in-memory Mongo double with a real unique index and an ``await``
between every read and write so concurrent requests interleave.
"""
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from pymongo.errors import DuplicateKeyError

from dashboard.routers import payment_plans as pp
from dashboard.services import payment_log_claims as plc
from tests._inmem_mongo import matches


class Coll:
    def __init__(self):
        self.docs: list[dict] = []
        self.unique: list[tuple[str, ...]] = []
        self.fail_insert = False
        self.fail_update = False
        self.before_update = None
        self.index_calls = 0

    async def create_index(self, keys, unique=False, name=None, **_):
        await asyncio.sleep(0)
        self.index_calls += 1
        fields = tuple(k for k, _ in keys)
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

        async def gen():
            await asyncio.sleep(0)
            for d in docs:
                yield d
        return gen()

    async def update_one(self, flt, update, upsert=False, session=None, **kwargs):
        await asyncio.sleep(0)
        if self.before_update:
            self.before_update(self, flt, update)
        if self.fail_update:
            raise RuntimeError("update failed")
        for d in self.docs:
            if matches(d, flt):
                for key, value in update.get("$inc", {}).items():
                    d[key] = d.get(key, 0) + value
                d.update(update.get("$set", {}))
                for key in update.get("$unset", {}):
                    d.pop(key, None)
                return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)
        if upsert:
            doc = {}
            doc.update(update.get("$setOnInsert") or {})
            doc.update(update.get("$set") or {})
            for key, value in update.get("$inc", {}).items():
                doc[key] = doc.get(key, 0) + value
            for fields in self.unique:
                key = tuple(doc.get(f) for f in fields)
                if any(tuple(d.get(f) for f in fields) == key for d in self.docs):
                    raise DuplicateKeyError("dup")
            self.docs.append(copy.deepcopy(doc))
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=doc.get("_id"))
        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)

    async def delete_one(self, flt):
        await asyncio.sleep(0)
        for i, d in enumerate(self.docs):
            if matches(d, flt):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, flt):
        await asyncio.sleep(0)
        keep = [d for d in self.docs if not matches(d, flt)]
        n = len(self.docs) - len(keep)
        self.docs[:] = keep
        return SimpleNamespace(deleted_count=n)


class DB(dict):
    def __missing__(self, name):
        self[name] = Coll()
        return self[name]


class _Admin:
    async def command(self, name, *args, **kwargs):
        if name in ("hello", "ismaster"):
            return {"setName": "atlas-rs", "ok": 1}
        raise RuntimeError(name)


class TxnSession:
    """Commits on success. Any other error restores the snapshot, matching with_transaction."""

    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def _restore(self, snap):
        # A row marked ``_external`` was committed by another writer during the
        # attempt. The abort drops our uncommitted writes and leaves that row.
        kept = []
        for name, coll in self.client.db.items():
            for doc in coll.docs:
                if doc.get("_external"):
                    kept.append((name, copy.deepcopy(doc)))
        for name in list(self.client.db.keys()):
            if name not in snap:
                self.client.db[name].docs = []
        for name, docs in snap.items():
            self.client.db[name].docs = copy.deepcopy(docs)
        for name, doc in kept:
            present = [d.get("_id") for d in self.client.db[name].docs]
            if doc.get("_id") not in present:
                self.client.db[name].docs.append(doc)

    async def with_transaction(self, callback):
        while True:
            snap = {name: copy.deepcopy(coll.docs) for name, coll in self.client.db.items()}
            self.client.attempts += 1
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
        self.attempts = 0
        self.admin = _Admin()

    async def start_session(self):
        return TxnSession(self)


@pytest.fixture
def rig(monkeypatch):
    db = DB()
    monkeypatch.setattr(pp, "get_collection", lambda name: db[name])
    monkeypatch.setattr(pp, "_payment_claim_index_ready", False)
    plc._lock_index_ready = False
    pp._txn_support_cache.clear()
    client = TxnClient(db)

    async def _txn():
        return client

    monkeypatch.setattr(pp, "_txn_client", _txn)
    db.txn_client = client
    app = FastAPI()
    app.include_router(pp.payment_plans_bp)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        yield db, app
    pp._txn_support_cache.clear()


def _body(booking="BK-1", txn="SS-77", dp=200, **kw):
    b = {"booking_number": booking, "total_premium": 1000, "down_payment": dp,
         "payment_method": "Card", "down_payment_transaction_id": txn}
    b.update(kw)
    return b


def _post(app, bodies, *, cookies=None, headers=None):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", cookies=cookies, headers=headers,
        ) as c:
            return await asyncio.gather(*[c.post("/api/payments/plans", json=b) for b in bodies])
    return asyncio.run(go())


def _poller(txn="SS-77", amount=200.0, status="approved", **kw):
    row = {"_id": f"row-{txn}-{status}", "transaction_id": txn, "amount": amount,
           "status": status, "source": "swipesimple_gmail_poll", "booking_number": "BK-1",
           "created_at": "2026-10-12T10:00:00+00:00"}
    row.update(kw)
    return row


def _claims(db):
    return db["payment_plan_payment_claims"].docs


def test_new_txn_claims_and_writes_one_down_payment_row(rig):
    db, app = rig
    (r,) = _post(app, [_body(txn=" SS-77 ")])
    assert r.status_code == 201
    plan_id = r.json()["plan"]["plan_id"]
    (row,) = db["payments"].docs
    assert row["type"] == "down_payment" and row["transaction_id"] == "SS-77"
    assert row["plan_id"] == plan_id
    (claim,) = _claims(db)
    assert claim["transaction_id"] == "SS-77" and claim["plan_id"] == plan_id
    assert claim["status"] == "applied" and claim["kind"] == "down_payment"


def test_no_txn_keeps_old_behaviour_without_claim(rig):
    db, app = rig
    b = _body(txn="")
    (r,) = _post(app, [b])
    assert r.status_code == 201
    assert len(db["payments"].docs) == 1 and _claims(db) == []


def test_concurrent_creates_same_txn_exactly_one_winner(rig):
    db, app = rig
    rs = _post(app, [_body(booking=f"BK-{i}") for i in range(5)])
    codes = sorted(r.status_code for r in rs)
    assert codes == [201, 409, 409, 409, 409]
    for r in rs:
        if r.status_code == 409:
            assert r.json()["error"] == "transaction_id_used_on_other_plan"
    assert len(db["payment_plans"].docs) == 1
    assert len(db["payments"].docs) == 1 and len(_claims(db)) == 1
    assert _claims(db)[0]["plan_id"] == db["payment_plans"].docs[0]["plan_id"]


def test_concurrent_creates_linking_same_poller_row_one_winner(rig):
    db, app = rig
    db["payments"].docs.append(_poller())
    rs = _post(app, [_body(booking="BK-1"), _body(booking="BK-1b")])
    assert sorted(r.status_code for r in rs) == [201, 409]
    assert len(db["payment_plans"].docs) == 1 and len(db["payments"].docs) == 1


def test_txn_already_claimed_by_pay_is_409(rig):
    db, app = rig
    _claims(db).append({"plan_id": "other", "transaction_id": "SS-77", "status": "applied",
                        "amount": 200.0})
    (r,) = _post(app, [_body()])
    assert r.status_code == 409 and r.json()["error"] == "transaction_id_used_on_other_plan"
    assert db["payment_plans"].docs == [] and db["payments"].docs == []


def test_legacy_plan_row_with_txn_is_409(rig):
    db, app = rig
    db["payments"].docs.append({"_id": "x", "transaction_id": "SS-77", "plan_id": "old",
                                "amount": 200.0, "status": "completed"})
    (r,) = _post(app, [_body()])
    assert r.status_code == 409 and r.json()["error"] == "transaction_id_used_on_other_plan"
    assert db["payment_plans"].docs == [] and _claims(db) == []


def test_links_settled_poller_row_instead_of_duplicating(rig):
    db, app = rig
    db["payments"].docs.append(_poller())
    (r,) = _post(app, [_body()])
    assert r.status_code == 201
    (row,) = db["payments"].docs
    assert row["plan_id"] == r.json()["plan"]["plan_id"]
    assert row["type"] == "down_payment" and row["status"] == "completed"
    assert row["logged_status"] == "approved" and row["booking_number"] == "BK-1"
    assert _claims(db)[0]["status"] == "applied"


def test_down_payment_accepts_all_recorder_settled_events(rig):
    """Every recorder settled charge event can be the down payment.

    ``payment.captured``, ``payment.succeeded``, and ``payment.settled`` link.
    A refund or failure event is still ``logged_payment_not_settled``.
    """
    from dashboard.services.payment_log_claims import (
        SETTLED_CHARGE_EVENTS,
        SETTLED_CHARGE_STATUSES,
    )

    db, app = rig
    assert SETTLED_CHARGE_EVENTS == frozenset(
        f"payment.{status}" for status in SETTLED_CHARGE_STATUSES
    )
    for status in sorted(SETTLED_CHARGE_STATUSES):
        event = f"payment.{status}"
        txn = f"OK-{status}"
        booking = f"BK-{status}"
        db["payments"].docs.append(_poller(
            txn=txn, amount=200.0, status=status, booking_number=booking,
            raw_payload={"event_type": event},
        ))
        (response,) = _post(app, [_body(txn=txn, booking=booking)])
        assert response.status_code == 201, (status, response.text)
        linked = [row for row in db["payments"].docs if row.get("transaction_id") == txn]
        assert len(linked) == 1 and linked[0].get("plan_id")

    refused = (
        ("payment.failed", "failed"),
        ("payment.declined", "declined"),
        ("payment.refunded", "refunded"),
        ("payment.failed", "approved"),
        ("payment.refunded", "approved"),
        ("payment.voided", "voided"),
        ("payment.reversed", "reversed"),
    )
    for event, status in refused:
        txn = f"NO-{status}-{event.split('.')[-1]}"
        db["payments"].docs.append(_poller(
            txn=txn, status=status, raw_payload={"event_type": event},
        ))
        (response,) = _post(app, [_body(txn=txn)])
        assert response.status_code == 409, (event, status, response.text)
        assert response.json()["error"] == "logged_payment_not_settled"
        assert not any(row.get("plan_id") for row in db["payments"].docs if row.get("transaction_id") == txn)


def test_links_settled_webhook_row(rig):
    db, app = rig
    db["payments"].docs.append(_poller(status="completed", source="swipesimple_webhook",
                                       raw_payload={"event_type": "payment.completed"}))
    (r,) = _post(app, [_body()])
    assert r.status_code == 201 and len(db["payments"].docs) == 1


@pytest.mark.parametrize("row,error", [
    (_poller(status="declined"), "logged_payment_not_settled"),
    (_poller(status="refunded"), "logged_payment_not_settled"),
    (_poller(status="approved", raw_payload={"event_type": "payment.refunded"}),
     "logged_payment_not_settled"),
    (_poller(amount=150.0), "transaction_id_amount_mismatch"),
    (_poller(booking_number="BK-OTHER"), "logged_payment_booking_mismatch"),
])
def test_unlinkable_logged_row_is_409_and_nothing_written(rig, row, error):
    db, app = rig
    db["payments"].docs.append(row)
    before = copy.deepcopy(db["payments"].docs)
    (r,) = _post(app, [_body()])
    assert r.status_code == 409 and r.json()["error"] == error
    assert db["payment_plans"].docs == [] and _claims(db) == []
    assert db["payments"].docs == before


def test_ambiguous_logged_rows_409(rig):
    db, app = rig
    db["payments"].docs += [_poller(), _poller(_id="row-2")]
    (r,) = _post(app, [_body()])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_ambiguous"
    assert db["payment_plans"].docs == [] and _claims(db) == []


def test_plan_insert_failure_leaves_no_half_state(rig):
    db, app = rig
    db["payment_plans"].fail_insert = True
    (r,) = _post(app, [_body()])
    assert r.status_code == 500 and r.json()["error"] == "payment_record_failed"
    assert db["payment_plans"].docs == [] and db["payments"].docs == []
    assert _claims(db) == []
    db["payment_plans"].fail_insert = False
    (r2,) = _post(app, [_body()])  # txn is free again
    assert r2.status_code == 201


def test_payment_row_failure_rolls_back_plan_and_claim(rig):
    db, app = rig
    db["payments"].fail_insert = True
    (r,) = _post(app, [_body()])
    assert r.status_code == 500
    assert db["payment_plans"].docs == [] and db["payments"].docs == [] and _claims(db) == []


def test_logged_row_changed_mid_link_rolls_back(rig):
    db, app = rig
    db["payments"].docs.append(_poller())

    def flip(coll, flt, update):
        coll.docs[0]["status"] = "refunded"
    db["payments"].before_update = flip
    (r,) = _post(app, [_body()])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_changed"
    assert db["payment_plans"].docs == [] and _claims(db) == []
    assert "plan_id" not in db["payments"].docs[0]


def test_claim_mark_applied_failure_unlinks_row(rig):
    db, app = rig
    db["payments"].docs.append(_poller())
    claims = db["payment_plan_payment_claims"]
    real = Coll.update_one

    async def boom(self, flt, update, upsert=False, session=None, **kwargs):
        if (update.get("$set") or {}).get("status") == "applied":
            raise RuntimeError("claim write failed")
        return await real(self, flt, update, upsert=upsert, session=session)
    claims.update_one = boom.__get__(claims)
    (r,) = _post(app, [_body()])
    assert r.status_code == 500
    assert db["payment_plans"].docs == [] and _claims(db) == []
    row = db["payments"].docs[0]
    assert "plan_id" not in row and row["status"] == "approved" and "type" not in row


def test_dedupe_unavailable_is_503_and_no_plan(rig, monkeypatch):
    db, app = rig

    async def down():
        raise RuntimeError("no index")
    monkeypatch.setattr(pp, "_payment_claims_collection", down)
    (r,) = _post(app, [_body()])
    assert r.status_code == 503 and db["payment_plans"].docs == []


def test_link_writes_audit_with_reason_and_confidence(rig):
    db, app = rig
    db["payments"].docs.append(_poller())
    (r,) = _post(app, [_body()])
    assert r.status_code == 201
    (ev,) = db["audit_events"].docs
    assert ev["event_type"] == "payment_plan_logged_payment_linked"
    assert ev["reason"] and ev["confidence"] == 1.0 and ev["transaction_id"] == "SS-77"


def test_audit_failure_rolls_back_link_plan_and_claim(rig):
    db, app = rig
    db["payments"].docs.append(_poller())
    before = copy.deepcopy(db["payments"].docs)
    db["audit_events"].fail_insert = True
    (r,) = _post(app, [_body()])
    assert r.status_code == 500
    assert db["payments"].docs == before
    assert db["payment_plans"].docs == [] and _claims(db) == []


def test_rollback_keeps_newer_reconciliation_status_and_blocks_txn(rig):
    """A payment that still carries this plan_id is not detached.

    The route's transaction aborts our own uncommitted link. This calls the
    undo directly for the case the abort cannot see: a newer write already
    replaced the fields we set and left ``plan_id`` in place.
    """
    db, app = rig
    plan_id = "plan-kept"
    row = _poller()
    row.update({
        "plan_id": plan_id, "type": "down_payment", "status": "reconciled",
        "logged_status": "approved", "timestamp": row["created_at"],
        "plan_linked_at": "2026-10-12T12:00:00+00:00",
    })
    db["payment_plans"].docs.append({
        "plan_id": plan_id, "booking_number": "BK-1", "status": "active", "total_paid": 200,
    })
    db["payments"].docs.append(row)
    _claims(db).append({
        "plan_id": plan_id, "transaction_id": "SS-77", "status": "claimed", "amount": 200.0,
    })
    link_set = {
        "plan_id": plan_id, "booking_number": "BK-1", "type": "down_payment",
        "logged_status": "approved", "status": "completed",
        "timestamp": row["created_at"], "plan_linked_at": "2026-10-12T12:00:00+00:00",
    }
    outcome = asyncio.run(pp._undo_plan_create(
        db["payment_plans"], db["payments"], db["payment_plan_payment_claims"],
        plan_id, "SS-77", _poller(), link_set, None, True,
        actor="Kayla Lukesic", compensate=True, amount=200.0, booking="BK-1",
        payment_id=row["_id"],
    ))
    assert outcome == "retained"
    assert db["payments"].docs[0]["status"] == "reconciled"
    assert db["payments"].docs[0]["plan_id"] == plan_id
    (plan,) = db["payment_plans"].docs
    assert plan["status"] == "needs_review"
    assert plan["review_reason"] == "plan_create_rollback_incomplete"
    (claim,) = _claims(db)
    assert claim["status"] == "failed"
    (ev,) = db["audit_events"].docs
    assert ev["event_type"] == pp._CREATE_ROLLBACK_AUDIT
    assert ev["new_state"]["outcome"] == "retained"
    (again,) = _post(app, [_body(txn="SS-OTHER")])
    assert again.status_code == 409 and again.json()["error"] == "payment_needs_review"
    assert len(db["payment_plans"].docs) == 1


def _fail_applied_claim(db):
    """The claim's applied write raises, so undo runs after the plan exists."""
    claims = db["payment_plan_payment_claims"]
    real = Coll.update_one

    async def boom(self, flt, update, upsert=False, session=None, **kwargs):
        if (update.get("$set") or {}).get("status") == "applied":
            raise RuntimeError("claim write failed")
        return await real(self, flt, update, upsert=upsert, session=session)
    claims.update_one = boom.__get__(claims)


def _assert_incomplete_undo(db, response):
    """Delete failed during undo. The transaction already dropped our writes.

    The claim stays failed, the response does not say nothing was recorded,
    and the compensating audit says the rollback is incomplete.
    """
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "payment_needs_review"
    assert body["transaction_id"] == "SS-77"
    assert "Nothing was recorded" not in body["message"]
    assert db["payment_plans"].docs == []
    assert db["payments"].docs == []
    (claim,) = _claims(db)
    assert claim["status"] == "failed"
    assert claim["failed_reason"] == "plan_create_rollback_incomplete"
    (ev,) = [d for d in db["audit_events"].docs if d["event_type"] == pp._CREATE_ROLLBACK_AUDIT]
    assert ev["new_state"]["outcome"] == "incomplete"
    assert ev["_id"] == f"plan-create-rollback:{ev['plan_id']}:SS-77"
    assert ev["actor"] and ev["amount"] == 200
    return claim


def test_incomplete_undo_when_payment_delete_throws(rig):
    db, app = rig
    _fail_applied_claim(db)
    payments = db["payments"]

    async def boom(self, flt, session=None, **kwargs):
        raise RuntimeError("delete failed")
    payments.delete_one = boom.__get__(payments)
    (r,) = _post(app, [_body()])
    _assert_incomplete_undo(db, r)


def test_incomplete_undo_when_plan_delete_throws(rig):
    db, app = rig
    _fail_applied_claim(db)
    plans = db["payment_plans"]

    async def boom(self, flt, session=None, **kwargs):
        raise RuntimeError("delete failed")
    plans.delete_one = boom.__get__(plans)
    (r,) = _post(app, [_body()])
    _assert_incomplete_undo(db, r)
    assert db["payments"].docs == []


def test_incomplete_undo_when_delete_matches_nothing(rig):
    """A delete that matches nothing after the transaction abort is a finished undo.

    The plan and the inserted payment are already gone. ``find_one`` sees that,
    so the claim is released and the compensating audit says ``undone``.
    """
    db, app = rig
    _fail_applied_claim(db)
    payments = db["payments"]
    plans = db["payment_plans"]

    async def miss(self, flt, session=None, **kwargs):
        return SimpleNamespace(deleted_count=0)
    payments.delete_one = miss.__get__(payments)
    plans.delete_one = miss.__get__(plans)
    (r,) = _post(app, [_body()])
    assert r.status_code == 500 and r.json()["error"] == "payment_record_failed"
    assert db["payment_plans"].docs == [] and db["payments"].docs == [] and _claims(db) == []
    (ev,) = db["audit_events"].docs
    assert ev["event_type"] == pp._CREATE_ROLLBACK_AUDIT
    assert ev["new_state"]["outcome"] == "undone"


def test_poller_row_arriving_during_insert_rolls_back(rig):
    db, app = rig
    real = db["payments"].insert_one

    async def insert_then_poller(doc, session=None, **kwargs):
        await real(doc, session=session)
        db["payments"].docs.append(_poller(_id="late", _external=True))
    db["payments"].insert_one = insert_then_poller
    (r,) = _post(app, [_body()])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_ambiguous"
    assert [d["_id"] for d in db["payments"].docs] == ["late"]
    assert db["payment_plans"].docs == [] and _claims(db) == []


# ── Codex findings on bbee713 ───────────────────────────────────────────────


def test_booking_reassigned_between_check_and_link_fails_closed(rig):
    db, app = rig
    db["payments"].docs.append(_poller())
    seen = {}

    def reassign(coll, flt, update):
        seen["booking_number"] = flt.get("booking_number")
        coll.docs[0]["booking_number"] = "BK-OTHER"
    db["payments"].before_update = reassign
    (r,) = _post(app, [_body()])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_changed"
    # The conditional update pinned the booking read beforehand. The in-flight
    # reassignment aborted with the transaction, and the row was not linked.
    assert seen["booking_number"] == "BK-1"
    row = db["payments"].docs[0]
    assert row["booking_number"] == "BK-1" and "plan_id" not in row
    assert db["payment_plans"].docs == [] and _claims(db) == []


def test_link_keeps_provider_created_at_as_timestamp(rig):
    db, app = rig
    db["payments"].docs.append(_poller(created_at="2026-09-01T12:00:00+00:00"))
    (r,) = _post(app, [_body()])
    assert r.status_code == 201
    assert db["payments"].docs[0]["timestamp"] == "2026-09-01T12:00:00+00:00"


@pytest.mark.parametrize("booking", ["", None])
def test_unowned_logged_row_is_refused(rig, booking):
    db, app = rig
    db["payments"].docs.append(_poller(booking_number=booking))
    (r,) = _post(app, [_body()])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_unowned"
    assert db["payment_plans"].docs == [] and _claims(db) == []


# ── Codex P1 on c0cbce7: audit actor is the session, not created_by ────────


def _session_cookie(monkeypatch, *, email, agent_name=None, role="staff"):
    monkeypatch.setenv("SECRET_KEY", "test-session-secret-not-production")
    from dashboard.auth.pin_middleware import COOKIE_NAME, _sign_token
    token = _sign_token(email=email, role=role, agent_name=agent_name)
    return {COOKIE_NAME: token}


def test_link_audit_ignores_body_created_by(rig):
    db, app = rig
    db["payments"].docs.append(_poller())
    (r,) = _post(app, [_body(created_by="Someone Else")])
    assert r.status_code == 201
    (ev,) = db["audit_events"].docs
    assert ev["actor"] == "dashboard" and ev["agent_name"] == "dashboard"


def test_link_audit_actor_is_session_agent_not_created_by(rig, monkeypatch):
    db, app = rig
    db["payments"].docs.append(_poller())
    cookies = _session_cookie(
        monkeypatch, email="kayla@shamrockbailbonds.biz", agent_name="Kayla Lukesic",
    )
    (r,) = _post(app, [_body(created_by="Someone Else")], cookies=cookies)
    assert r.status_code == 201
    (ev,) = db["audit_events"].docs
    assert ev["actor"] == "Kayla Lukesic" and ev["agent_name"] == "Kayla Lukesic"


def test_link_audit_actor_falls_back_to_session_email(rig, monkeypatch):
    db, app = rig
    db["payments"].docs.append(_poller())
    cookies = _session_cookie(monkeypatch, email="Office@ShamrockBailBonds.biz")
    (r,) = _post(app, [_body(created_by="Someone Else")], cookies=cookies)
    assert r.status_code == 201
    (ev,) = db["audit_events"].docs
    assert ev["actor"] == "office@shamrockbailbonds.biz"
    assert ev["agent_name"] == "office@shamrockbailbonds.biz"


def test_link_audit_actor_is_machine_key_for_trusted_machine_call(rig, monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "machine-secret-not-real")
    monkeypatch.delenv("LEADS_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    db, app = rig
    db["payments"].docs.append(_poller())
    (r,) = _post(
        app, [_body(created_by="Someone Else")],
        headers={"X-API-Key": "machine-secret-not-real"},
    )
    assert r.status_code == 201
    (ev,) = db["audit_events"].docs
    assert ev["actor"] == "machine_key" and ev["agent_name"] == "machine_key"


def test_session_actor_wins_over_machine_key(rig, monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "machine-secret-not-real")
    db, app = rig
    db["payments"].docs.append(_poller())
    cookies = _session_cookie(
        monkeypatch, email="kayla@shamrockbailbonds.biz", agent_name="Kayla Lukesic",
    )
    (r,) = _post(
        app, [_body(created_by="Someone Else")],
        cookies=cookies, headers={"X-API-Key": "machine-secret-not-real"},
    )
    assert r.status_code == 201
    (ev,) = db["audit_events"].docs
    assert ev["actor"] == "Kayla Lukesic" and ev["agent_name"] == "Kayla Lukesic"


# ── Codex P1s on #182 @a51123e: insert audit, and a compensating undo audit ──


def test_inserted_down_payment_audit_is_tenant_scoped(rig, monkeypatch):
    """A new down-payment row written from ``down_payment_transaction_id`` is audited.

    The ``audit_events`` ``_id`` uses the #185 rule: ``plan-create:{plan}:{txn}``
    with the flag off, and ``plan-create:{tenant}:{plan}:{txn}`` with the flag
    on. The actor is the signed session. If the audit cannot be saved, the
    plan, the payment, and the claim are not left behind. The first write in
    the transaction is the shared ``payment_txn_locks`` upsert. No index is
    added on ``payments``.
    """
    from dashboard.tenancy.context import bind_job_tenant
    from dashboard.tenancy.scope import TenantScopedCollection

    db, app = rig
    cookies = _session_cookie(
        monkeypatch, email="kayla@shamrockbailbonds.biz", agent_name="Kayla Lukesic",
    )
    (r,) = _post(app, [_body(created_by="Someone Else")], cookies=cookies)
    assert r.status_code == 201
    plan_id = r.json()["plan"]["plan_id"]
    (row,) = db["payments"].docs
    (ev,) = db["audit_events"].docs
    assert ev["event_type"] == pp._DOWN_PAYMENT_AUDIT
    assert ev["_id"] == f"plan-create:{plan_id}:SS-77"
    assert ev["actor"] == "Kayla Lukesic" and ev["agent_name"] == "Kayla Lukesic"
    assert ev["amount"] == 200
    assert ev["payment_id"] == str(row["_id"]) and ev["plan_id"] == plan_id
    assert ev["transaction_id"] == "SS-77" and ev["booking_number"] == "BK-1"
    assert ev["old_state"] == {"plan_id": None, "status": None, "payment_id": None}
    assert ev["new_state"] == {
        "plan_id": plan_id, "status": "completed", "payment_id": str(row["_id"]),
    }
    assert ev["confidence"] == 1.0 and ev["reason"]
    (lock,) = db["payment_txn_locks"].docs
    assert lock["txn_key"] == "SS-77" and lock["version"] == 1 and lock["tenant_id"] is None
    assert db["payments"].unique == [] and db["payments"].index_calls == 0
    assert pp._last_apply_path == "transaction"

    real_insert = Coll.insert_one

    async def fail_down(self, doc, session=None, **kwargs):
        if doc.get("event_type") == pp._DOWN_PAYMENT_AUDIT:
            raise RuntimeError("audit down")
        await real_insert(self, doc, session=session)

    db["audit_events"].insert_one = fail_down.__get__(db["audit_events"])
    (bad,) = _post(app, [_body(booking="BK-FAIL", txn="SS-FAIL", created_by="Someone Else")], cookies=cookies)
    assert bad.status_code == 500 and bad.json()["error"] == "payment_record_failed"
    assert [d.get("transaction_id") for d in db["payments"].docs] == ["SS-77"]
    assert all(c.get("transaction_id") != "SS-FAIL" for c in _claims(db))
    assert all(p.get("booking_number") != "BK-FAIL" for p in db["payment_plans"].docs)
    assert all(a.get("transaction_id") != "SS-FAIL" for a in db["audit_events"].docs)

    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    monkeypatch.setattr(pp, "_payment_claim_index_ready", False)
    plc._lock_index_ready = False
    pp._txn_support_cache.clear()
    db2 = DB()
    client = TxnClient(db2)

    async def _txn():
        return client

    monkeypatch.setattr(pp, "_txn_client", _txn)
    monkeypatch.setattr(pp, "get_collection", lambda name: TenantScopedCollection(db2[name], name))
    app2 = FastAPI()
    app2.include_router(pp.payment_plans_bp)

    async def both():
        transport = httpx.ASGITransport(app=app2)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            out = []
            for tenant, booking in (("gulf_coast_bail", "BK-A"), ("other_agency", "BK-B")):
                with bind_job_tenant(tenant):
                    out.append(await http.post(
                        "/api/payments/plans",
                        json=_body(booking=booking, txn="SS-SHARED"),
                    ))
            return out

    rs = asyncio.run(both())
    assert [r.status_code for r in rs] == [201, 201]
    plans = {p["plan_id"]: p["tenant_id"] for p in db2["payment_plans"].docs}
    assert {a["_id"] for a in db2["audit_events"].docs} == {
        f"plan-create:{tenant}:{plan}:SS-SHARED" for plan, tenant in plans.items()
    }
    assert {a.get("tenant_id") for a in db2["audit_events"].docs} == {"gulf_coast_bail", "other_agency"}
    assert {row["tenant_id"] for row in db2["payment_txn_locks"].docs} == {"gulf_coast_bail", "other_agency"}
    assert {row["txn_key"] for row in db2["payment_txn_locks"].docs} == {"SS-SHARED"}
    assert db2["payments"].unique == []


def test_plan_create_undo_writes_compensating_audit(rig, monkeypatch):
    """Finalization failure after the link audit writes a compensating audit.

    The claim update raises after the link audit was inserted. The transaction
    aborts that audit. ``_undo_plan_create`` appends ``payment_plan_create_rolled_back``
    with the signed-in actor, the record ids, the amount, and the state change.
    The logged row is not left pointing at a plan. The rollback ``_id`` uses the
    same tenant rule as #185.
    """
    from dashboard.tenancy.context import bind_job_tenant

    db, app = rig
    db["payments"].docs.append(_poller())
    _fail_applied_claim(db)
    cookies = _session_cookie(
        monkeypatch, email="kayla@shamrockbailbonds.biz", agent_name="Kayla Lukesic",
    )
    (r,) = _post(app, [_body(created_by="Someone Else")], cookies=cookies)
    assert r.status_code == 500 and r.json()["error"] == "payment_record_failed"
    row = db["payments"].docs[0]
    assert "plan_id" not in row and row["status"] == "approved" and "type" not in row
    assert db["payment_plans"].docs == [] and _claims(db) == []
    (ev,) = db["audit_events"].docs
    assert ev["event_type"] == pp._CREATE_ROLLBACK_AUDIT
    assert ev["_id"] == f"plan-create-rollback:{ev['plan_id']}:SS-77"
    assert ev["actor"] == "Kayla Lukesic" and ev["agent_name"] == "Kayla Lukesic"
    assert ev["amount"] == 200 and ev["payment_id"] == str(row["_id"])
    assert ev["transaction_id"] == "SS-77" and ev["booking_number"] == "BK-1"
    assert ev["linked_existing_row"] is True and ev["confidence"] == 1.0
    assert ev["old_state"]["plan_id"] == ev["plan_id"]
    assert ev["old_state"]["status"] == "completed" and ev["old_state"]["claim_status"] == "claimed"
    assert ev["new_state"] == {
        "outcome": "undone", "plan_id": None, "plan_status": None, "claim_status": "deleted",
    }
    assert all(d["event_type"] != "payment_plan_logged_payment_linked" for d in db["audit_events"].docs)
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    with bind_job_tenant("gulf_coast_bail"):
        assert pp._scoped_audit_id("plan-create-rollback", ev["plan_id"], "SS-77") == (
            f"plan-create-rollback:gulf_coast_bail:{ev['plan_id']}:SS-77"
        )


def test_rollback_audit_matches_final_claim_state(rig, monkeypatch):
    """The compensating audit's claim_status is the claim row after it is finalized.

    A finished undo deletes the claim, then records ``deleted``. When that
    delete raises, the claim is stored ``failed`` and the audit says
    ``failed``, not ``deleted``.
    """
    db, app = rig
    db["payments"].docs.append(_poller())
    _fail_applied_claim(db)
    (ok,) = _post(app, [_body()])
    assert ok.status_code == 500 and ok.json()["error"] == "payment_record_failed"
    assert _claims(db) == []
    (ev,) = [d for d in db["audit_events"].docs if d["event_type"] == pp._CREATE_ROLLBACK_AUDIT]
    assert ev["new_state"]["claim_status"] == "deleted"
    assert ev["new_state"]["outcome"] == "undone"

    db["payments"].docs.append(_poller(txn="SS-88"))
    claims = db["payment_plan_payment_claims"]

    async def delete_fails(flt):
        raise RuntimeError("claim delete failed")

    claims.delete_one = delete_fails
    (bad,) = _post(app, [_body(txn="SS-88")])
    assert bad.status_code == 500
    stored = [c for c in _claims(db) if c.get("transaction_id") == "SS-88"]
    assert len(stored) == 1 and stored[0]["status"] == "failed"
    audits = [
        d for d in db["audit_events"].docs
        if d["event_type"] == pp._CREATE_ROLLBACK_AUDIT and d.get("transaction_id") == "SS-88"
    ]
    assert len(audits) == 1
    assert audits[0]["new_state"]["claim_status"] == stored[0]["status"] == "failed"
    assert audits[0]["new_state"]["outcome"] == "incomplete"


def test_create_with_down_payment_txn_unavailable_writes_nothing(rig, monkeypatch):
    db, app = rig

    async def _none():
        return None

    monkeypatch.setattr(pp, "_txn_client", _none)
    pp._txn_support_cache.clear()
    (r,) = _post(app, [_body()])
    assert r.status_code == 503 and r.json()["error"] == "transactions_unavailable"
    assert db["payment_plans"].docs == [] and db["payments"].docs == [] and _claims(db) == []
    assert db["audit_events"].docs == [] and db["payment_txn_locks"].docs == []
    assert db["payment_plan_payment_claims"].index_calls == 0


def test_create_down_payment_retries_transient_transaction_error(rig):
    db, app = rig
    locks = db["payment_txn_locks"]
    real = Coll.update_one
    calls = {"n": 0}

    async def once(self, flt, update, upsert=False, session=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise pp.RetryableTxnError()
        return await real(self, flt, update, upsert=upsert, session=session)

    locks.update_one = once.__get__(locks)
    (r,) = _post(app, [_body()])
    assert r.status_code == 201
    assert db.txn_client.attempts == 2 and calls["n"] == 2
    assert len(db["payments"].docs) == 1 and len(db["audit_events"].docs) == 1
    assert locks.docs[0]["version"] == 1 and locks.docs[0]["txn_key"] == "SS-77"
