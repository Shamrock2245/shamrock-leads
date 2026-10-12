"""Receipt poller + POST /api/webhooks/payment: one payments row per charge.

Offline in-memory Mongo double (real unique index, an ``await`` between every
read and write) so concurrent writers interleave. No SwipeSimple / Gmail /
Slack / BlueBubbles calls.
"""
from __future__ import annotations

import asyncio
import copy
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from pymongo.errors import DuplicateKeyError

import httpx
import pytest
from fastapi import FastAPI

from dashboard.routers import webhooks as wh
from dashboard.services import payment_log_claims as plc
from dashboard.services.swipesimple_receipt_poller import SwipeSimpleReceiptPoller
from types import SimpleNamespace

from tests._inmem_mongo import matches
from tests.test_payment_plan_pay_dedupe import Coll as _BaseColl


class Coll(_BaseColl):
    async def insert_one(self, doc):
        await asyncio.sleep(0)
        if self.fail_insert:
            raise RuntimeError("write failed")
        if doc.get("_id") is not None and any(d.get("_id") == doc["_id"] for d in self.docs):
            raise DuplicateKeyError("dup _id")
        for fields in self.unique:
            key = tuple(doc.get(f) for f in fields)
            if any(tuple(d.get(f) for f in fields) == key for d in self.docs):
                raise DuplicateKeyError("dup")
        self.docs.append(copy.deepcopy(doc))

    async def update_one(self, flt, update, upsert=False):
        await asyncio.sleep(0)
        for d in self.docs:
            if matches(d, flt):
                for key in (update.get("$unset") or {}):
                    d.pop(key, None)
                d.update(update.get("$set") or {})
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def delete_one(self, flt):
        await asyncio.sleep(0)
        for i, d in enumerate(self.docs):
            if matches(d, flt):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class DB(dict):
    def __missing__(self, name):
        self[name] = Coll()
        return self[name]


@pytest.fixture
def rig(monkeypatch):
    db = DB()
    monkeypatch.setattr(plc, "_index_ready", False)
    monkeypatch.setattr(wh, "get_collection", lambda name: db[name])
    for k in ("SWIPESIMPLE_WEBHOOK_SECRET", "SLACK_WEBHOOK_URL", "SLACK_WEBHOOK_LEADS",
              "ENVIRONMENT", "REQUIRE_DASHBOARD_PIN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ENV", "development")
    app = FastAPI()
    app.include_router(wh.webhooks_bp)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()), \
         patch("dashboard.services.bb_client.send_message_universal", new=AsyncMock()) as bb:
        yield db, app, bb


def _hook(txn="SS-1", event="payment.completed", status="approved", amount=200.0, **kw):
    body = {"event_type": event, "transaction_id": txn, "status": status, "amount": amount,
            "custom_fields": {"booking_number": "BK-1"}}
    body.update(kw)
    return body


async def _post_hooks(app, bodies):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await asyncio.gather(*[c.post("/api/webhooks/payment", json=b) for b in bodies])


def _hooks(app, bodies):
    return asyncio.run(_post_hooks(app, bodies))


async def _poll_one(db, txn="SS-1", mid="gm-1", amount=200.0):
    poller = SwipeSimpleReceiptPoller(db)
    return await poller._apply_bond_payment(
        db, {"amount": amount, "transaction_id": txn, "booking_number": "BK-1"},
        gmail_message_id=mid, raw_subject="Receipt")


def _rows(db, txn="SS-1"):
    return [d for d in db["payments"].docs if d.get("transaction_id") == txn]


def test_webhook_logs_once_and_retry_is_duplicate(rig):
    db, app, bb = rig
    (r1,) = _hooks(app, [_hook(indemnitor_phone=None)])
    (r2,) = _hooks(app, [_hook()])
    assert r1.status_code == 200 and r1.json().get("duplicate") is not True
    assert r2.status_code == 200 and r2.json()["duplicate"] is True
    assert len(_rows(db)) == 1
    (claim,) = db[plc.LOG_CLAIMS_COLLECTION].docs
    assert claim["kind"] == "charge" and claim["status"] == "logged"


def test_concurrent_webhooks_same_txn_one_row(rig):
    db, app, bb = rig
    rs = _hooks(app, [_hook(custom_fields={"booking_number": "BK-1", "indemnitor_phone": "+10000000000"})] * 5)
    fresh = [r for r in rs if r.status_code == 200 and not r.json().get("duplicate")]
    assert len(fresh) == 1
    for r in rs:
        if r is not fresh[0]:
            assert r.json().get("duplicate") is True or (
                r.status_code == 409 and r.json()["error"] == "payment_in_progress")
    assert len(_rows(db)) == 1
    assert bb.await_count == 1                      # customer texted once


def test_refund_event_is_its_own_row_once(rig):
    db, app, bb = rig
    _hooks(app, [_hook()])
    _hooks(app, [_hook(event="payment.refunded", status="refunded")])
    (r,) = _hooks(app, [_hook(event="payment.refunded", status="refunded")])
    assert r.json()["duplicate"] is True
    events = sorted(d["raw_payload"]["event_type"] for d in _rows(db))
    assert events == ["payment.completed", "payment.refunded"]


def test_poller_then_webhook_attaches_not_duplicates(rig):
    db, app, bb = rig
    out = asyncio.run(_poll_one(db))
    assert out["success"] and not out.get("duplicate")
    (r,) = _hooks(app, [_hook()])
    assert r.status_code == 200 and r.json().get("duplicate") is not True   # side effects run once
    (row,) = _rows(db)
    assert row["source"] == "swipesimple_gmail_poll"
    assert row["webhook_event_type"] == "payment.completed" and "raw_payload" not in row
    (r2,) = _hooks(app, [_hook()])                                         # retry
    assert r2.json()["duplicate"] is True and len(_rows(db)) == 1


def test_webhook_then_poller_skips(rig):
    db, app, bb = rig
    _hooks(app, [_hook()])
    out = asyncio.run(_poll_one(db))
    assert out["duplicate"] is True and len(_rows(db)) == 1


def test_concurrent_poller_and_webhook_one_row(rig):
    db, app, bb = rig

    async def go():
        return await asyncio.gather(_poll_one(db), _post_hooks(app, [_hook()]))
    asyncio.run(go())
    assert len(_rows(db)) == 1


def test_concurrent_pollers_one_row(rig):
    db, app, bb = rig

    async def go():
        return await asyncio.gather(*[_poll_one(db, mid=f"gm-{i}") for i in range(4)])
    outs = asyncio.run(go())
    assert sum(1 for o in outs if o.get("success") and not o.get("duplicate")) == 1
    for o in outs:   # losers: duplicate, or in progress (email stays unread for next run)
        assert o.get("success") or o.get("duplicate") or o.get("error") == "payment_in_progress"
    assert len(_rows(db)) == 1


def test_applied_plan_claim_blocks_both_writers(rig):
    """An applied plan claim (or a plan payment row) is the recorded charge."""
    db, app, bb = rig
    db[plc.PLAN_CLAIMS_COLLECTION].docs.append(
        {"transaction_id": "SS-1", "plan_id": "plan-1", "status": "applied"})
    out = asyncio.run(_poll_one(db))
    assert out["duplicate"] is True
    (r,) = _hooks(app, [_hook()])
    assert r.status_code == 200
    assert _rows(db) == []


@pytest.mark.parametrize("claim_status", ["claimed", "weird", None])
def test_in_progress_plan_claim_is_not_a_recorded_charge(rig, claim_status):
    """Plan /pay still claimed, or an unknown status: do not insert, and do not
    mark the receipt done. Only ``applied`` (or a payment row) suppresses it."""
    db, app, bb = rig
    doc = {"transaction_id": "SS-1", "plan_id": "plan-1"}
    if claim_status is not None:
        doc["status"] = claim_status
    db[plc.PLAN_CLAIMS_COLLECTION].docs.append(doc)
    out = asyncio.run(_poll_one(db))
    assert out["success"] is False and out["error"] == "payment_in_progress"
    (r,) = _hooks(app, [_hook()])
    assert r.status_code == 409 and r.json()["error"] == "payment_in_progress"
    assert _rows(db) == []


def test_failed_plan_claim_lets_logger_record_the_charge_once(rig):
    """Plan /pay failed before its payment row: the provider charge is logged once."""
    db, app, bb = rig
    db[plc.PLAN_CLAIMS_COLLECTION].docs.append(
        {"transaction_id": "SS-1", "plan_id": "plan-1", "status": "failed"})
    out = asyncio.run(_poll_one(db))
    assert out["success"] is True and out.get("duplicate") is not True
    (r,) = _hooks(app, [_hook()])
    assert r.status_code == 200
    assert len(_rows(db)) == 1
    assert db[plc.PLAN_CLAIMS_COLLECTION].docs[0]["status"] == "failed"
    # Poller audit plus one webhook audit. The charge itself is a single row.
    sources = sorted(d["source"] for d in db["audit_events"].docs)
    assert sources == ["swipesimple_gmail_poll", "swipesimple_webhook"]


def test_failed_plan_claim_webhook_then_poller_one_row(rig):
    db, app, bb = rig
    db[plc.PLAN_CLAIMS_COLLECTION].docs.append(
        {"transaction_id": "SS-1", "plan_id": "plan-1", "status": "failed"})
    (r,) = _hooks(app, [_hook()])
    assert r.status_code == 200 and r.json().get("duplicate") is not True
    out = asyncio.run(_poll_one(db))
    assert out["duplicate"] is True
    assert len(_rows(db)) == 1
    assert len(db["audit_events"].docs) == 1


def test_failed_plan_claim_concurrent_writers_one_row(rig):
    db, app, bb = rig
    db[plc.PLAN_CLAIMS_COLLECTION].docs.append(
        {"transaction_id": "SS-1", "plan_id": "plan-1", "status": "failed"})

    async def go():
        return await asyncio.gather(_poll_one(db), _post_hooks(app, [_hook()]))
    asyncio.run(go())
    assert len(_rows(db)) == 1


def test_existing_plan_row_blocks_both_writers(rig):
    db, app, bb = rig
    db["payments"].docs.append({"_id": "pp", "transaction_id": "SS-1", "plan_id": "plan-1",
                                "status": "completed", "amount": 200.0})
    assert asyncio.run(_poll_one(db))["duplicate"] is True
    _hooks(app, [_hook()])
    (row,) = _rows(db)
    assert row["_id"] == "pp" and "webhook_event_type" not in row   # plan row is not touched


def test_plan_row_landing_during_poller_insert_wins(rig):
    db, app, bb = rig
    real = db["payments"].insert_one

    async def insert_then_plan_row(doc):
        await real(doc)
        db["payments"].docs.append({"_id": "pp", "transaction_id": "SS-1", "plan_id": "plan-1",
                                    "status": "completed", "amount": 200.0})
    db["payments"].insert_one = insert_then_plan_row
    out = asyncio.run(_poll_one(db))
    assert out["duplicate"] is True
    assert [d["_id"] for d in _rows(db)] == ["pp"]


def test_insert_failure_releases_claim(rig):
    db, app, bb = rig
    db["payments"].fail_insert = True
    with pytest.raises(RuntimeError):
        asyncio.run(_poll_one(db))
    assert db[plc.LOG_CLAIMS_COLLECTION].docs == []
    db["payments"].fail_insert = False
    out = asyncio.run(_poll_one(db))
    assert out["success"] and not out.get("duplicate") and len(_rows(db)) == 1


def test_claims_unavailable_fails_closed(rig):
    db, app, bb = rig
    db[plc.LOG_CLAIMS_COLLECTION].fail_index = True
    out = asyncio.run(_poll_one(db))
    assert out["success"] is False and out["error"] == "payment_dedupe_unavailable"
    (r,) = _hooks(app, [_hook()])
    assert r.status_code == 503
    assert _rows(db) == []


def test_legacy_duplicate_rows_still_skip(rig):
    """No unique index on payments: historical rows are only read, never changed."""
    db, app, bb = rig
    db["payments"].docs.append({"_id": "old", "transaction_id": "SS-1", "status": "approved",
                                "source": "swipesimple_gmail_poll"})
    assert asyncio.run(_poll_one(db, mid="gm-new"))["duplicate"] is True
    assert db["payments"].unique == []


# ── Codex findings on 37ef4c0 ───────────────────────────────────────────────


def test_poller_failure_before_insert_releases_claim(rig):
    db, app, bb = rig

    async def boom(*a, **k):
        raise RuntimeError("bond lookup failed")
    db["bond_cases"].find_one = boom
    with pytest.raises(RuntimeError):
        asyncio.run(_poll_one(db))
    assert db[plc.LOG_CLAIMS_COLLECTION].docs == [] and _rows(db) == []
    del db["bond_cases"]
    out = asyncio.run(_poll_one(db))
    assert out["success"] and not out.get("duplicate") and len(_rows(db)) == 1


def test_webhook_failure_before_insert_releases_claim(rig):
    db, app, bb = rig

    async def boom(*a, **k):
        raise RuntimeError("bond update failed")
    db["bond_cases"].update_one = boom
    with pytest.raises(RuntimeError):
        _hooks(app, [_hook()])
    assert db[plc.LOG_CLAIMS_COLLECTION].docs == [] and _rows(db) == []
    del db["bond_cases"]
    (r,) = _hooks(app, [_hook()])
    assert r.status_code == 200 and not r.json().get("duplicate") and len(_rows(db)) == 1


def test_webhook_retry_resumes_missing_event_and_audit(rig):
    db, app, bb = rig
    body = _hook(custom_fields={"booking_number": "BK-1", "indemnitor_phone": "+10000000000"})
    calls = {"n": 0}

    async def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("sse down")
    with patch("dashboard.routers.events.publish_event", new=flaky):
        with pytest.raises(RuntimeError):
            _hooks(app, [body])
        assert len(_rows(db)) == 1 and db["audit_events"].docs == []
        (r2,) = _hooks(app, [body])                    # provider retry
    assert r2.status_code == 200 and not r2.json().get("duplicate")
    assert calls["n"] == 2 and len(db["audit_events"].docs) == 1
    assert len(_rows(db)) == 1 and bb.await_count == 1   # no second row, no second text
    (r3,) = _hooks(app, [body])
    assert r3.json()["duplicate"] is True and len(db["audit_events"].docs) == 1


def test_poller_then_webhook_texts_once_across_retries(rig):
    db, app, bb = rig
    asyncio.run(_poll_one(db))
    body = _hook(custom_fields={"booking_number": "BK-1", "indemnitor_phone": "+10000000000"})
    _hooks(app, [body, body, body])
    assert bb.await_count == 1 and len(_rows(db)) == 1


def test_poller_in_progress_claim_leaves_email_unread(rig):
    db, app, bb = rig
    db[plc.LOG_CLAIMS_COLLECTION].docs.append(
        {"transaction_id": "SS-1", "kind": "charge", "source": "swipesimple_webhook", "status": "claimed"})
    out = asyncio.run(_poll_one(db))
    assert out["success"] is False and out["error"] == "payment_in_progress"


def _stale_claimed_at():
    return (datetime.now(timezone.utc) - plc.STALE_AFTER - timedelta(seconds=5)).isoformat()


def _seed_stale_logger_claim(db, claimed_at=None):
    db[plc.LOG_CLAIMS_COLLECTION].docs.append({
        "transaction_id": "SS-1", "kind": "charge", "source": "swipesimple_webhook",
        "status": "claimed", "claimed_at": _stale_claimed_at() if claimed_at is None else claimed_at,
    })


def test_fresh_logger_claim_is_not_taken_over(rig):
    db, app, bb = rig
    _seed_stale_logger_claim(db, claimed_at=datetime.now(timezone.utc).isoformat())
    out = asyncio.run(_poll_one(db))
    assert out["success"] is False and out["error"] == "payment_in_progress"
    (r,) = _hooks(app, [_hook()])
    assert r.status_code == 409 and r.json()["error"] == "payment_in_progress"
    assert _rows(db) == []


def test_unparseable_claimed_at_is_not_taken_over(rig):
    db, app, bb = rig
    _seed_stale_logger_claim(db, claimed_at="not-a-timestamp")
    out = asyncio.run(_poll_one(db))
    assert out["error"] == "payment_in_progress" and _rows(db) == []


def test_stale_logger_claim_poller_records_once(rig):
    db, app, bb = rig
    _seed_stale_logger_claim(db)
    out = asyncio.run(_poll_one(db))
    assert out["success"] is True and out.get("duplicate") is not True
    assert len(_rows(db)) == 1
    assert db[plc.LOG_CLAIMS_COLLECTION].docs[0]["status"] == "logged"
    again = asyncio.run(_poll_one(db, mid="gm-2"))
    assert again["duplicate"] is True and len(_rows(db)) == 1


def test_stale_logger_claim_webhook_records_once_with_audit(rig):
    db, app, bb = rig
    _seed_stale_logger_claim(db)
    (r,) = _hooks(app, [_hook()])
    assert r.status_code == 200 and r.json().get("duplicate") is not True
    assert len(_rows(db)) == 1 and len(db["audit_events"].docs) == 1
    assert db[plc.LOG_CLAIMS_COLLECTION].docs[0]["status"] == "logged"
    (r2,) = _hooks(app, [_hook()])
    assert r2.json()["duplicate"] is True
    assert len(_rows(db)) == 1 and len(db["audit_events"].docs) == 1


def test_stale_claim_with_row_resumes_audit_without_second_row(rig):
    db, app, bb = rig
    db["payments"].docs.append({
        "_id": "p1", "transaction_id": "SS-1", "status": "approved",
        "source": "swipesimple_gmail_poll",
    })
    _seed_stale_logger_claim(db)
    (r,) = _hooks(app, [_hook()])
    assert r.status_code == 200 and r.json().get("duplicate") is not True
    assert [d["_id"] for d in _rows(db)] == ["p1"]
    assert len(db["audit_events"].docs) == 1
    assert db[plc.LOG_CLAIMS_COLLECTION].docs[0]["status"] == "logged"


def test_concurrent_stale_reclaim_one_insert_token(rig):
    db, app, bb = rig
    _seed_stale_logger_claim(db)

    async def go():
        return await asyncio.gather(*[
            plc.begin(db, "SS-1", plc.CHARGE, "swipesimple_gmail_poll") for _ in range(5)
        ])
    gates = asyncio.run(go())
    inserts = [g for g in gates if g["action"] == "insert"]
    assert len(inserts) == 1 and inserts[0].get("claim_token")
    assert all(g["action"] == "in_progress" for g in gates if g is not inserts[0])
    assert sum(1 for d in db[plc.LOG_CLAIMS_COLLECTION].docs if d.get("kind") == "charge") == 1


def test_concurrent_stale_reclaim_one_payment_row(rig):
    db, app, bb = rig
    _seed_stale_logger_claim(db)

    async def go():
        return await asyncio.gather(*[_poll_one(db, mid=f"gm-{i}") for i in range(4)])
    outs = asyncio.run(go())
    assert sum(1 for o in outs if o.get("success") and not o.get("duplicate")) == 1
    for o in outs:
        assert o.get("success") or o.get("duplicate") or o.get("error") == "payment_in_progress"
    assert len(_rows(db)) == 1


def test_lost_claim_token_does_not_keep_a_second_row(rig):
    """Takeover lands during insert: only the winner's row remains."""
    db, app, bb = rig
    real = db["payments"].insert_one

    async def steal(doc):
        await real(doc)
        db[plc.LOG_CLAIMS_COLLECTION].docs[0]["claimed_at"] = "winner-token"
        db["payments"].docs.append(
            {"_id": "winner", "transaction_id": "SS-1", "status": "approved"})
    db["payments"].insert_one = steal
    out = asyncio.run(_poll_one(db))
    assert out["duplicate"] is True
    assert [d["_id"] for d in _rows(db)] == ["winner"]


def _seed_logged_charge(db, event_type="payment.completed"):
    db["payments"].docs.append({
        "_id": "legacy",
        "transaction_id": "SS-1",
        "status": "approved",
        "raw_payload": {"event_type": event_type, "transaction_id": "SS-1", "status": "approved"},
    })
    db[plc.LOG_CLAIMS_COLLECTION].docs.append({
        "transaction_id": "SS-1",
        "kind": plc.claim_kind(event_type),
        "source": "swipesimple_webhook",
        "status": "logged",
    })


def test_concurrent_retries_publish_and_audit_once(rig):
    """Two retries of a logged-but-incomplete webhook must not double the audit."""
    db, app, bb = rig
    _seed_logged_charge(db)
    calls = {"n": 0}

    async def count(*_a, **_k):
        calls["n"] += 1
        await asyncio.sleep(0)

    body = _hook()
    with patch("dashboard.routers.events.publish_event", new=count):
        rs = _hooks(app, [body, body])
    assert calls["n"] == 1
    assert len(db["audit_events"].docs) == 1
    assert len(_rows(db)) == 1
    assert all(r.status_code in (200, 409) for r in rs)
    assert any(r.status_code == 200 and not r.json().get("duplicate") for r in rs)
    (again,) = _hooks(app, [body])
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert calls["n"] == 1 and len(db["audit_events"].docs) == 1


def test_audit_redelivery_after_insert_stays_one_row(rig):
    """The audit id is deterministic, so a retry that lost the done flag does not insert again."""
    db, app, bb = rig
    (r,) = _hooks(app, [_hook()])
    assert r.status_code == 200 and len(db["audit_events"].docs) == 1
    claim = db[plc.LOG_CLAIMS_COLLECTION].docs[0]
    for key in ("wh_audit", "wh_audit_hold", "wh_completed"):
        claim.pop(key, None)
    (again,) = _hooks(app, [_hook()])
    assert again.status_code == 200
    assert len(db["audit_events"].docs) == 1
    assert db["audit_events"].docs[0]["_id"] == "wh:shamrock:SS-1:charge:audit"


def test_two_tenants_same_txn_each_keep_an_audit(rig, monkeypatch):
    """Same provider transaction and kind, two agencies: each audit row is kept.

    Claims are unique per tenant, but ``audit_events._id`` is collection-wide.
    A shared id made the second insert a DuplicateKeyError that was swallowed,
    and that tenant's claim was marked complete with no audit.
    """
    from dashboard.tenancy.context import bind_job_tenant
    from dashboard.tenancy.scope import apply_scope

    db, app, bb = rig
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    monkeypatch.setattr(wh, "get_collection", lambda name: apply_scope(db[name], name))
    body = _hook()
    with bind_job_tenant("agency_a"):
        (ra,) = _hooks(app, [body])
    with bind_job_tenant("agency_b"):
        (rb,) = _hooks(app, [body])
    assert ra.status_code == 200 and ra.json().get("duplicate") is not True
    assert rb.status_code == 200 and rb.json().get("duplicate") is not True
    assert sorted(d["_id"] for d in db["audit_events"].docs) == [
        "wh:agency_a:SS-1:charge:audit",
        "wh:agency_b:SS-1:charge:audit",
    ]
    assert {d.get("tenant_id") for d in db["audit_events"].docs} == {"agency_a", "agency_b"}
    claims = db[plc.LOG_CLAIMS_COLLECTION].docs
    assert {c.get("tenant_id") for c in claims} == {"agency_a", "agency_b"}
    assert all(c.get("wh_audit") and c.get("wh_completed") for c in claims)
    assert len(_rows(db)) == 2

    agency_a = next(c for c in claims if c.get("tenant_id") == "agency_a")
    for key in ("wh_audit", "wh_audit_hold", "wh_completed"):
        agency_a.pop(key, None)
    with bind_job_tenant("agency_a"):
        (again,) = _hooks(app, [body])
    assert again.status_code == 200
    assert sorted(d["_id"] for d in db["audit_events"].docs) == [
        "wh:agency_a:SS-1:charge:audit",
        "wh:agency_b:SS-1:charge:audit",
    ]
    assert agency_a.get("wh_audit") and agency_a.get("wh_completed")


@pytest.mark.parametrize("stored,redelivered,kind_event", [
    ("PAYMENT.REFUNDED", "Payment.Refunded", "payment.refunded"),
    ("Payment.Failed", "PAYMENT.FAILED", "payment.failed"),
    ("Payment.Completed", "PAYMENT.COMPLETED", "payment.completed"),
])
def test_legacy_event_type_case_does_not_insert_again(rig, stored, redelivered, kind_event):
    db, app, bb = rig
    db["payments"].docs.append({
        "_id": "legacy",
        "transaction_id": "SS-1",
        "status": "approved",
        "raw_payload": {"event_type": stored, "transaction_id": "SS-1"},
    })
    body = _hook(event=redelivered, status="refunded" if "refund" in kind_event else "approved")
    (r,) = _hooks(app, [body])
    assert r.status_code == 200
    assert [d["_id"] for d in _rows(db)] == ["legacy"]
    (again,) = _hooks(app, [body])
    assert again.json().get("duplicate") is True
    assert [d["_id"] for d in _rows(db)] == ["legacy"]
