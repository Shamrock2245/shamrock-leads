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
from tests._inmem_mongo import matches


class Coll:
    def __init__(self):
        self.docs: list[dict] = []
        self.unique: list[tuple[str, ...]] = []
        self.fail_insert = False
        self.fail_update = False
        self.before_update = None

    async def create_index(self, keys, unique=False, name=None, **_):
        await asyncio.sleep(0)
        fields = tuple(k for k, _ in keys)
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

    def find(self, flt=None, *a, **k):
        docs = [copy.deepcopy(d) for d in self.docs if matches(d, flt or {})]

        async def gen():
            await asyncio.sleep(0)
            for d in docs:
                yield d
        return gen()

    async def update_one(self, flt, update, upsert=False):
        await asyncio.sleep(0)
        if self.before_update:
            self.before_update(self, flt, update)
        if self.fail_update:
            raise RuntimeError("update failed")
        for d in self.docs:
            if matches(d, flt):
                d.update(update.get("$set", {}))
                for key in update.get("$unset", {}):
                    d.pop(key, None)
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

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


@pytest.fixture
def rig(monkeypatch):
    db = DB()
    monkeypatch.setattr(pp, "get_collection", lambda name: db[name])
    monkeypatch.setattr(pp, "_payment_claim_index_ready", False)
    app = FastAPI()
    app.include_router(pp.payment_plans_bp)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        yield db, app


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

    async def boom(self, flt, update, upsert=False):
        if (update.get("$set") or {}).get("status") == "applied":
            raise RuntimeError("claim write failed")
        return await real(self, flt, update, upsert)
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


def _assert_plan_retained_for_changed_payment(db, response):
    """Payment still points at a plan that exists, marked for review."""
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "payment_needs_review"
    assert "Nothing was recorded" not in body["message"]
    row = db["payments"].docs[0]
    assert row["status"] == "reconciled" and row.get("plan_id")
    (plan,) = db["payment_plans"].docs
    assert plan["plan_id"] == row["plan_id"] == body["plan_id"]
    assert plan["status"] == "needs_review"
    assert plan["review_reason"] == "plan_create_rollback_incomplete"
    (claim,) = _claims(db)
    assert claim["status"] == "failed" and claim["failed_reason"] == "plan_create_rollback_incomplete"
    assert claim["plan_id"] == plan["plan_id"]


def test_rollback_keeps_newer_reconciliation_status_and_blocks_txn(rig):
    db, app = rig
    db["payments"].docs.append(_poller())

    async def reconcile_then_fail(doc):
        db["payments"].docs[0]["status"] = "reconciled"
        raise RuntimeError("audit down")
    db["audit_events"].insert_one = reconcile_then_fail
    (r,) = _post(app, [_body()])
    _assert_plan_retained_for_changed_payment(db, r)
    # The booking already has a plan that needs review; a second one is refused.
    (again,) = _post(app, [_body(txn="SS-OTHER")])
    assert again.status_code == 409 and again.json()["error"] == "payment_needs_review"
    assert len(db["payment_plans"].docs) == 1


def test_claim_finalization_failure_retains_plan_when_payment_changed(rig):
    db, app = rig
    db["payments"].docs.append(_poller())
    claims = db["payment_plan_payment_claims"]
    real = Coll.update_one

    async def reconcile_then_fail(self, flt, update, upsert=False):
        if (update.get("$set") or {}).get("status") == "applied":
            db["payments"].docs[0]["status"] = "reconciled"
            raise RuntimeError("claim write failed")
        return await real(self, flt, update, upsert)
    claims.update_one = reconcile_then_fail.__get__(claims)
    (r,) = _post(app, [_body()])
    _assert_plan_retained_for_changed_payment(db, r)


def _fail_applied_claim(db):
    """The claim's applied write raises, so undo runs after the plan exists."""
    claims = db["payment_plan_payment_claims"]
    real = Coll.update_one

    async def boom(self, flt, update, upsert=False):
        if (update.get("$set") or {}).get("status") == "applied":
            raise RuntimeError("claim write failed")
        return await real(self, flt, update, upsert)
    claims.update_one = boom.__get__(claims)


def _assert_incomplete_undo(db, response):
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "payment_needs_review"
    assert body["transaction_id"] == "SS-77"
    assert "Nothing was recorded" not in body["message"]
    (plan,) = db["payment_plans"].docs
    assert plan["status"] == "needs_review"
    assert plan["review_reason"] == "plan_create_rollback_incomplete"
    assert plan["plan_id"] == body["plan_id"]
    assert plan["total_paid"] == 200
    (claim,) = _claims(db)
    assert claim["status"] == "failed"
    assert claim["failed_reason"] == "plan_create_rollback_incomplete"
    assert claim["plan_id"] == plan["plan_id"]
    return plan


def test_incomplete_undo_when_payment_delete_throws(rig):
    db, app = rig
    _fail_applied_claim(db)
    payments = db["payments"]

    async def boom(self, flt):
        raise RuntimeError("delete failed")
    payments.delete_one = boom.__get__(payments)
    (r,) = _post(app, [_body()])
    plan = _assert_incomplete_undo(db, r)
    (pay,) = db["payments"].docs
    assert pay["plan_id"] == plan["plan_id"]


def test_incomplete_undo_when_plan_delete_throws(rig):
    db, app = rig
    _fail_applied_claim(db)
    plans = db["payment_plans"]

    async def boom(self, flt):
        raise RuntimeError("delete failed")
    plans.delete_one = boom.__get__(plans)
    (r,) = _post(app, [_body()])
    _assert_incomplete_undo(db, r)
    assert db["payments"].docs == []


def test_incomplete_undo_when_delete_matches_nothing(rig):
    """delete_one reports nothing deleted and leaves the plan and payment."""
    db, app = rig
    _fail_applied_claim(db)
    payments = db["payments"]
    plans = db["payment_plans"]

    async def miss(self, flt):
        return SimpleNamespace(deleted_count=0)
    payments.delete_one = miss.__get__(payments)
    plans.delete_one = miss.__get__(plans)
    (r,) = _post(app, [_body()])
    plan = _assert_incomplete_undo(db, r)
    (pay,) = db["payments"].docs
    assert pay["plan_id"] == plan["plan_id"]


def test_poller_row_arriving_during_insert_rolls_back(rig):
    db, app = rig
    real = db["payments"].insert_one

    async def insert_then_poller(doc):
        await real(doc)
        db["payments"].docs.append(_poller(_id="late"))
    db["payments"].insert_one = insert_then_poller
    (r,) = _post(app, [_body()])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_ambiguous"
    assert [d["_id"] for d in db["payments"].docs] == ["late"]
    assert db["payment_plans"].docs == [] and _claims(db) == []


# ── Codex findings on bbee713 ───────────────────────────────────────────────


def test_booking_reassigned_between_check_and_link_fails_closed(rig):
    db, app = rig
    db["payments"].docs.append(_poller())

    def reassign(coll, flt, update):
        coll.docs[0]["booking_number"] = "BK-OTHER"
    db["payments"].before_update = reassign
    (r,) = _post(app, [_body()])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_changed"
    row = db["payments"].docs[0]
    assert row["booking_number"] == "BK-OTHER" and "plan_id" not in row
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
