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
    # Same cents: one count. The plan-linked row is preferred, then a completed row.
    assert S([poller("T1", amount=2.0), plan_pay("T1", amount=2.0)])["total"] == 2.0
    assert S([plan_pay("T1", amount=2.0), poller("T1", amount=2.0)])["count"] == 1
    completed = {"_id": "c", "transaction_id": "T2", "amount": 5.0, "status": "completed", "timestamp": RECENT}
    assert S([poller("T2", amount=5.0), completed])["total"] == 5.0


def test_kpi_settled_statuses_match_recorder():
    """Each recorder charge status counts once. Reversals and needs_review do not.

    The status set is the recorder's constant, not a second literal. A
    ``payment.captured`` webhook counts with status ``captured``.
    """
    from dashboard.services.payment_log_claims import SETTLED_CHARGE_STATUSES, event_kind

    assert aa.SETTLED_CHARGE_STATUSES is SETTLED_CHARGE_STATUSES
    assert aa._PAYMENT_AGENT_FIELDS["needs_review"] == 1
    assert aa._PAYMENT_AGENT_FIELDS["event_kind"] == 1
    assert aa._PAYMENT_AGENT_FIELDS["event_type"] == 1
    charge_events = {
        "approved": "payment.approved",
        "paid": "payment.paid",
        "completed": "payment.completed",
        "complete": "payment.complete",
        "success": "payment.success",
        "succeeded": "payment.succeeded",
        "captured": "payment.captured",
        "settled": "payment.settled",
    }
    assert set(charge_events) == set(SETTLED_CHARGE_STATUSES)
    rows = [
        webhook(f"CHG-{status}", status=status, event=charge_events[status], amount=25.0)
        for status in sorted(SETTLED_CHARGE_STATUSES)
    ]
    out = S(rows)
    assert out["count"] == len(SETTLED_CHARGE_STATUSES)
    assert out["total"] == 25.0 * len(SETTLED_CHARGE_STATUSES)
    assert out["unsettled_skipped"] == 0 and out["reversed_skipped"] == 0
    once = S([
        webhook("DUP", status="captured", event="payment.captured", amount=25.0),
        poller("DUP", status="captured", amount=25.0),
    ])
    assert once["count"] == 1 and once["total"] == 25.0 and once["duplicates_skipped"] == 1

    excluded = []
    for status in (
        "declined", "failed", "failure", "denied", "voided", "error",
        "refunded", "refund", "reversed",
    ):
        assert status not in SETTLED_CHARGE_STATUSES
        assert event_kind(status, None) != "charge"
        excluded.append(poller(f"BAD-{status}", status=status, amount=25.0))
    excluded.append(webhook(
        "NR", status="approved", event="payment.completed", amount=25.0, needs_review=True,
    ))
    excluded.append(webhook(
        "REF-KIND", status="approved", event="payment.completed", amount=25.0, event_kind="refund",
    ))
    excluded.append(webhook(
        "FAIL-KIND", status="captured", event="payment.captured", amount=25.0, event_kind="failure",
    ))
    bad = S(excluded)
    assert bad["count"] == 0 and bad["total"] == 0.0


def test_conflicting_amounts_for_one_transaction_are_excluded():
    """Codex P2: settled rows that share a transaction id but disagree on the
    amount are left out. A $1 provider row is not reported as the plan's $2."""
    out = S([poller("T1", amount=1.0), plan_pay("T1", amount=2.0), poller("T9", amount=30.0)])
    assert out["count"] == 1 and out["total"] == 30.0 and out["amount_conflicts"] == 1
    swapped = S([plan_pay("T1", amount=2.0), poller("T1", amount=1.0)])
    assert swapped["count"] == 0 and swapped["total"] == 0.0 and swapped["amount_conflicts"] == 1
    completed = {"_id": "c", "transaction_id": "T2", "amount": 5.0, "status": "completed", "timestamp": RECENT}
    disagree = S([poller("T2", amount=3.0), completed])
    assert disagree["count"] == 0 and disagree["amount_conflicts"] == 1
    agreed = S([poller("T3", amount=100.0), plan_pay("T3", amount=100.0)])
    assert agreed["count"] == 1 and agreed["total"] == 100.0 and agreed["amount_conflicts"] == 0


@pytest.mark.parametrize("status", ["declined", "refunded", "voided", "pending", "failed", "", None])
def test_unsettled_statuses_excluded(status):
    row = poller("T1", status=status)
    if status is None:
        row.pop("status")
    out = S([row])
    assert out["count"] == 0 and out["total"] == 0
    assert out["unsettled_skipped"] + out["reversed_skipped"] == 1


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


def test_kpi_excludes_txn_with_failure_or_refund_sibling():
    """A failure or refund sibling drops that transaction's settled charge.

    The sibling is classified with the recorder's ``row_kind`` /
    ``REVERSAL_KINDS``, so failed and declined count as well as refunds.
    """
    from dashboard.services.payment_log_claims import REVERSAL_KINDS, row_kind

    siblings = [
        webhook("FAIL", status="failed", event="payment.failed", amount=40.0),
        webhook("DECL", status="declined", event="payment.declined", amount=40.0),
        webhook("REF", status="refunded", event="payment.refunded", amount=40.0),
        webhook("LATE", status="approved", event="payment.failed", amount=40.0),
        webhook("KIND", status="approved", event="payment.completed", amount=40.0, event_kind="failure"),
    ]
    rows = [poller("KEEP", amount=15.0)]
    for sibling in siblings:
        assert row_kind(sibling) in REVERSAL_KINDS
        txn = sibling["transaction_id"]
        rows.append(poller(txn, amount=40.0))
        rows.append(sibling)
    out = S(rows)
    assert out["count"] == 1 and out["total"] == 15.0
    assert out["reversed_skipped"] == len(siblings)




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


def _payment_agent(monkeypatch, rows):
    db = _DB()
    db["payments"] = _Coll(rows)
    monkeypatch.setattr(aa, "get_db", lambda: db)
    out = asyncio.run(aa.agent_performance(days="30"))
    body = out if isinstance(out, dict) else __import__("json").loads(out.body)
    agents = body.get("agents") or body.get("results") or body
    return next(a for a in agents if a.get("agent_id") == "the_payment_agent")


def test_future_paid_at_is_outside_the_report_window(monkeypatch):
    """P2: AR stores paid_at on payments.timestamp. A future date is not this period."""
    future = (NOW + timedelta(days=10)).isoformat()
    agent = _payment_agent(monkeypatch, [
        plan_pay("T1", 40.0),
        {"_id": "future-ar", "transaction_id": "AR-FUTURE", "amount": 500.0,
         "status": "completed", "source": "ar_manual", "type": "payment",
         "timestamp": future},
        {"_id": "future-created", "transaction_id": "AR-CREATED", "amount": 80.0,
         "status": "completed", "created_at": future},
    ])
    assert agent["kpis"]["payments_recorded"] == 1
    assert agent["kpis"]["total_collected"] == 40.0


def test_timestamp_and_created_at_inside_the_window_still_count(monkeypatch):
    agent = _payment_agent(monkeypatch, [
        plan_pay("T1", 25.0),
        poller("T2", amount=15.0),
    ])
    assert agent["kpis"]["payments_recorded"] == 2
    assert agent["kpis"]["total_collected"] == 40.0


# ── Codex P2s on 8f765fb ─────────────────────────────────────────────────────

@pytest.mark.parametrize("reversal", [
    {"status": "refunded"}, {"status": "voided"}, {"status": "chargeback"},
    {"status": "approved", "raw_payload": {"event_type": "payment.refunded"}},
    {"status": "approved", "raw_payload": {"event_type": "payment.voided"}},
    {"status": "approved", "raw_payload": {"event_type": "payment.chargeback"}},
])
def test_later_refund_or_chargeback_cancels_settled_transaction(reversal):
    """P2: group by transaction first; a reversal row cancels the earlier settled row(s)."""
    rows = [webhook("T1"), poller("T1"),
            {"_id": "rev", "transaction_id": "T1", "amount": 100.0, "created_at": RECENT, **reversal},
            poller("T2", amount=30.0)]
    out = S(rows)
    assert out["count"] == 1 and out["total"] == 30.0 and out["reversed_skipped"] == 1


def test_refund_cancels_even_plan_linked_row():
    out = S([plan_pay("T1", 100.0), webhook("T1", event="payment.refunded")])
    assert out["count"] == 0 and out["total"] == 0


def test_declined_sibling_excludes_settled_charge():
    out = S([poller("T1", status="declined"), poller("T1", amount=100.0, _id="ok")])
    assert out["count"] == 0 and out["total"] == 0.0 and out["reversed_skipped"] == 1


def _down(amount=200.0, booking="BK-1", when=RECENT, txn=None, **kw):
    row = {"_id": f"dp-{booking}-{amount}", "booking_number": booking, "plan_id": "plan-1",
           "amount": amount, "type": "down_payment", "status": "completed", "timestamp": when,
           "method": "Card", **kw}
    if txn:
        row["transaction_id"] = txn
    return row


def test_untracked_down_payment_matched_to_poller_row_counts_once():
    """P2: card down payment logged by /payments/plans (no txn) and by the poller."""
    out = S([_down(200.0), poller("SS-9", amount=200.0, booking_number="BK-1")])
    assert out["count"] == 1 and out["total"] == 200.0 and out["down_payments_matched"] == 1


def test_untracked_down_payment_matched_to_webhook_row():
    out = S([_down(150.0), webhook("SS-8", amount=150.0, booking_number="BK-1")])
    assert out["count"] == 1 and out["total"] == 150.0


@pytest.mark.parametrize("card", [
    lambda: poller("SS-1", amount=199.99, booking_number="BK-1"),            # amount differs
    lambda: poller("SS-1", amount=200.0, booking_number="BK-OTHER"),         # booking differs
    lambda: {**poller("SS-1", amount=200.0, booking_number="BK-1"),
             "created_at": (NOW - timedelta(days=10)).isoformat()},           # outside window
])
def test_down_payment_not_matched_counts_both(card):
    out = S([_down(200.0), card()])
    assert out["count"] == 2 and out["down_payments_matched"] == 0


def test_one_card_row_absorbs_only_one_down_payment():
    out = S([_down(200.0), {**_down(200.0), "_id": "dp-2"}, poller("SS-1", amount=200.0, booking_number="BK-1")])
    assert out["count"] == 2 and out["total"] == 400.0 and out["down_payments_matched"] == 1


def test_down_payment_with_txn_dedupes_by_txn_preferring_plan_row():
    out = S([_down(200.0, txn="SS-7"), poller("SS-7", amount=200.0, booking_number="BK-1")])
    assert out["count"] == 1 and out["total"] == 200.0 and out["duplicates_skipped"] == 1


def test_legacy_card_down_payment_excluded_when_its_charge_is_refunded():
    """Codex P2 @e975130: the matching card charge was refunded -> fail closed."""
    out = S([_down(200.0), poller("SS-5", amount=200.0, booking_number="BK-1"),
             webhook("SS-5", event="payment.refunded", booking_number="BK-1")])
    assert out["count"] == 0 and out["total"] == 0.0
    assert out["reversed_skipped"] == 1 and out["down_payments_reversed"] == 1


@pytest.mark.parametrize("event", ["payment.chargeback", "payment.voided"])
def test_legacy_card_down_payment_excluded_on_chargeback_or_void(event):
    out = S([_down(200.0), webhook("SS-6", amount=200.0, booking_number="BK-1"),
             webhook("SS-6", amount=200.0, event=event, booking_number="BK-1", _id="rev")])
    assert out["count"] == 0 and out["down_payments_reversed"] == 1


def test_refund_of_other_booking_does_not_drop_down_payment():
    out = S([_down(200.0), poller("SS-5", amount=200.0, booking_number="BK-2"),
             webhook("SS-5", event="payment.refunded", booking_number="BK-2")])
    assert out["count"] == 1 and out["total"] == 200.0 and out["down_payments_reversed"] == 0


@pytest.mark.parametrize("method", ["Cash", "Check", "", None, "Zelle"])
def test_non_card_down_payment_never_absorbed_by_card_row(method):
    """Codex P2 @e975130: a distinct cash down payment is real money, keep it."""
    out = S([_down(200.0, method=method), poller("SS-9", amount=200.0, booking_number="BK-1")])
    assert out["count"] == 2 and out["total"] == 400.0 and out["down_payments_matched"] == 0


@pytest.mark.parametrize("method", ["Cash", None])
def test_non_card_down_payment_kept_when_card_charge_refunded(method):
    out = S([_down(200.0, method=method), poller("SS-5", amount=200.0, booking_number="BK-1"),
             webhook("SS-5", event="payment.refunded", booking_number="BK-1")])
    assert out["count"] == 1 and out["total"] == 200.0


@pytest.mark.parametrize("method", ["Card", "credit card", "Debit", "SwipeSimple"])
def test_card_methods_are_matched(method):
    out = S([_down(200.0, method=method), poller("SS-9", amount=200.0, booking_number="BK-1")])
    assert out["count"] == 1 and out["down_payments_matched"] == 1


def test_endpoint_projection_loads_method():
    assert aa._PAYMENT_AGENT_FIELDS.get("method") == 1
