"""the_payment_agent KPIs: settled statuses only, one count per transaction id,
plan /pay rows (timestamp) and poller/webhook rows (created_at) both counted."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from dashboard.routers import agent_analytics as aa
from tests._inmem_mongo import matches

NOW = datetime.now(timezone.utc)
RECENT = (NOW - timedelta(days=2)).isoformat()
OLD = (NOW - timedelta(days=90)).isoformat()


def poller(txn, amount=100.0, status="approved", **kw):
    return {"_id": f"p-{txn}-{status}", "transaction_id": txn, "amount": amount, "status": status,
            "source": "swipesimple_gmail_poll", "created_at": RECENT, **kw}


def webhook(txn, amount=100.0, status="approved", event="payment.completed", **kw):
    return {"_id": f"w-{txn}-{event}", "transaction_id": txn, "amount": amount, "status": status,
            "raw_payload": {"event_type": event}, "created_at": RECENT, **kw}


def plan_pay(txn, amount=100.0, plan_id="plan-1", **kw):
    return {"_id": f"pp-{txn}", "transaction_id": txn, "amount": amount, "status": "completed",
            "type": "installment", "plan_id": plan_id, "timestamp": RECENT, **kw}


S = aa.summarize_settled_payments


def test_poller_plus_webhook_same_txn_counts_once():
    out = S([poller("T1"), webhook("T1")])
    assert out["count"] == 1 and out["total"] == 100.0 and out["duplicates_skipped"] == 1


def test_poller_plus_plan_pay_same_txn_counts_once_prefers_plan_row():
    out = S([poller("T1", amount=100.0), plan_pay("T1", amount=100.0)])
    assert out["count"] == 1 and out["total"] == 100.0


def test_preference_linked_then_completed():
    # Mismatched amounts make the chosen row visible in the total.
    assert S([poller("T1", amount=1.0), plan_pay("T1", amount=2.0)])["total"] == 2.0
    assert S([plan_pay("T1", amount=2.0), poller("T1", amount=1.0)])["total"] == 2.0
    completed = {"_id": "c", "transaction_id": "T2", "amount": 5.0, "status": "completed", "timestamp": RECENT}
    assert S([poller("T2", amount=3.0), completed])["total"] == 5.0


@pytest.mark.parametrize("status", ["declined", "refunded", "voided", "pending", "failed", "", None])
def test_unsettled_statuses_excluded(status):
    row = poller("T1", status=status)
    if status is None:
        row.pop("status")
    out = S([row])
    assert out["count"] == 0 and out["total"] == 0 and out["unsettled_skipped"] == 1


@pytest.mark.parametrize("event", ["payment.failed", "payment.refunded"])
def test_webhook_failed_or_refunded_event_excluded_even_if_status_approved(event):
    out = S([webhook("T1", event=event)])
    assert out["count"] == 0


@pytest.mark.parametrize("status", ["approved", "paid", "completed", "success", "Completed", " PAID "])
def test_settled_statuses_counted(status):
    assert S([poller("T1", status=status)])["count"] == 1


def test_plan_pay_rows_counted():
    out = S([plan_pay("T1", 50.0), plan_pay("T2", 25.5, plan_id="plan-2")])
    assert out["count"] == 2 and out["total"] == 75.5


def test_rows_without_txn_count_individually_and_bad_amounts_are_zero():
    rows = [{"_id": "a", "amount": 10, "status": "completed", "timestamp": RECENT},
            {"_id": "b", "amount": 10, "status": "completed", "timestamp": RECENT},
            {"_id": "c", "amount": "abc", "status": "completed", "timestamp": RECENT},
            {"_id": "d", "amount": float("nan"), "status": "completed", "timestamp": RECENT}]
    out = S(rows)
    assert out["count"] == 4 and out["total"] == 20.0


def test_declined_duplicate_does_not_displace_settled_row():
    out = S([poller("T1", status="declined", amount=999.0), poller("T1", amount=100.0)])
    assert out["count"] == 1 and out["total"] == 100.0


# ── Endpoint wiring ───────────────────────────────────────────────────────────

class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, n=None):
        return list(self.docs)


class _Coll:
    def __init__(self, docs=None):
        self.docs = docs or []

    async def count_documents(self, flt=None, *a, **k):
        return 0

    async def distinct(self, *a, **k):
        return []

    def find(self, flt=None, projection=None, *a, **k):
        return _Cursor([d for d in self.docs if matches(d, flt or {})])

    def aggregate(self, *a, **k):
        return _Cursor([])


class _DB(dict):
    def __missing__(self, name):
        self[name] = _Coll()
        return self[name]


def test_agent_performance_payment_agent_kpis(monkeypatch):
    db = _DB()
    db["payments"] = _Coll([
        poller("T1"), webhook("T1"),                        # duplicate card payment
        poller("T2", amount=40.0), plan_pay("T2", 40.0),    # linked/duplicated with plan pay
        plan_pay("T3", 60.0),                               # plan pay only (timestamp, no created_at)
        poller("T4", status="declined"),                    # unsettled
        webhook("T5", event="payment.refunded"),            # refunded event
        {**poller("T6"), "created_at": OLD},                # outside window
    ])
    monkeypatch.setattr(aa, "get_db", lambda: db)
    out = asyncio.run(aa.agent_performance(days="30"))
    body = out if isinstance(out, dict) else __import__("json").loads(out.body)
    agents = body.get("agents") or body.get("results") or body
    agent = next(a for a in agents if a.get("agent_id") == "the_payment_agent")
    assert agent["kpis"]["payments_recorded"] == 3
    assert agent["kpis"]["total_collected"] == 200.0
