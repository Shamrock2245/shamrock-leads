"""Receipt poller + POST /api/webhooks/payment: one payments row per charge.

Offline in-memory Mongo double (real unique index, an ``await`` between every
read and write) so concurrent writers interleave. No SwipeSimple / Gmail /
Slack / BlueBubbles calls.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

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
    async def update_one(self, flt, update, upsert=False):
        await asyncio.sleep(0)
        for d in self.docs:
            if matches(d, flt):
                d.update(update.get("$set", {}))
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
    rs = _hooks(app, [_hook()] * 5)
    assert all(r.status_code == 200 for r in rs)
    assert sum(1 for r in rs if r.json().get("duplicate")) == 4
    assert len(_rows(db)) == 1


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
    assert sum(1 for o in outs if not o.get("duplicate")) == 1
    assert len(_rows(db)) == 1


def test_plan_claim_blocks_both_writers(rig):
    db, app, bb = rig
    db[plc.PLAN_CLAIMS_COLLECTION].docs.append(
        {"transaction_id": "SS-1", "plan_id": "plan-1", "status": "claimed"})
    out = asyncio.run(_poll_one(db))
    assert out["duplicate"] is True
    (r,) = _hooks(app, [_hook()])
    assert r.status_code == 200
    assert _rows(db) == []


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
