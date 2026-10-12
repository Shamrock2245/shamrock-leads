"""Link, don't duplicate: plan /pay attaches plan_id to the payments row the
receipt poller already logged for that card transaction instead of inserting a
second row. Offline, in-memory Mongo double (shared with the dedupe tests)."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from tests.test_payment_plan_pay_dedupe import PLAN_ID, _plan, _post_many, rig  # noqa: F401


def _poller_row(db, txn="TX-CARD", amount=100.0, booking="TEST-PP-1", _id="pay-poll-1", **extra):
    row = {"_id": _id, "transaction_id": txn, "gmail_message_id": "gm-1",
           "source": "swipesimple_gmail_poll", "amount": amount, "status": "approved",
           "booking_number": booking, **extra}
    db["payments"].docs.append(row)
    return row


def _second_plan(db):
    db["payment_plans"].docs.append({**_plan(db), "plan_id": "plan-test-02",
                                     "balance_remaining": 800.0, "total_paid": 200.0,
                                     "payments_made": 1})
    return db["payment_plans"].docs[1]


def test_logged_card_payment_is_linked_not_duplicated(rig):
    db, app = rig
    row = _poller_row(db)
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    body = r.json()
    assert r.status_code == 200 and body["idempotent"] is False and body["linked_logged_payment"] is True
    assert len(db["payments"].docs) == 1                 # no second row
    assert row["plan_id"] == PLAN_ID and row["source"] == "swipesimple_gmail_poll"
    assert _plan(db)["balance_remaining"] == 700.0 and _plan(db)["payments_made"] == 2
    assert db["payment_plan_payment_claims"].docs[0]["status"] == "applied"


def test_linked_row_replay_is_idempotent(rig):
    db, app = rig
    _poller_row(db)
    _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 200 and r.json()["idempotent"] is True
    assert len(db["payments"].docs) == 1 and _plan(db)["balance_remaining"] == 700.0


def test_no_logged_row_still_inserts(rig):
    db, app = rig
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CASH-9"}])
    assert r.json()["linked_logged_payment"] is False
    assert len(db["payments"].docs) == 1 and db["payments"].docs[0]["plan_id"] == PLAN_ID


@pytest.mark.parametrize("logged", [99.99, 100.01, 900.0, None])
def test_logged_row_amount_mismatch_refused(rig, logged):
    db, app = rig
    row = _poller_row(db, amount=logged)
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_amount_mismatch"
    assert "plan_id" not in row and len(db["payments"].docs) == 1
    assert db["payment_plan_payment_claims"].docs == []  # refused before claiming
    assert _plan(db)["balance_remaining"] == 800.0


def test_logged_row_already_linked_to_other_plan_refused(rig):
    db, app = rig
    _second_plan(db)
    _poller_row(db, plan_id="plan-test-02")
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 409 and r.json()["error"] == "transaction_id_used_on_other_plan"
    assert len(db["payments"].docs) == 1 and _plan(db)["balance_remaining"] == 800.0


def test_logged_row_for_other_booking_refused(rig):
    db, app = rig
    row = _poller_row(db, booking="TEST-OTHER-9")
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_booking_mismatch"
    assert "plan_id" not in row and _plan(db)["balance_remaining"] == 800.0


def test_logged_row_without_booking_can_link(rig):
    db, app = rig
    row = _poller_row(db, booking="")
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 200 and row["plan_id"] == PLAN_ID


def test_two_unlinked_rows_same_txn_refused_as_ambiguous(rig):
    db, app = rig
    _poller_row(db)
    _poller_row(db, _id="pay-webhook-1", source="webhook")
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_ambiguous"
    assert all("plan_id" not in d for d in db["payments"].docs)


def test_concurrent_same_plan_links_once(rig):
    db, app = rig
    row = _poller_row(db)
    rs = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}] * 5)
    fresh = [r for r in rs if r.status_code == 200 and r.json()["idempotent"] is False]
    assert len(fresh) == 1
    assert all(r.status_code in (200, 409) for r in rs)
    assert len(db["payments"].docs) == 1 and row["plan_id"] == PLAN_ID
    assert _plan(db)["balance_remaining"] == 700.0 and _plan(db)["payments_made"] == 2


def test_concurrent_two_plans_link_once(rig):
    db, app = rig
    other = _second_plan(db)
    row = _poller_row(db)

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await asyncio.gather(
                c.post(f"/api/payments/plans/{PLAN_ID}/pay", json={"amount": 100, "transaction_id": "TX-CARD"}),
                c.post("/api/payments/plans/plan-test-02/pay", json={"amount": 100, "transaction_id": "TX-CARD"}),
            )
    rs = asyncio.run(go())
    assert sum(1 for r in rs if r.status_code == 200 and r.json().get("idempotent") is False) == 1
    assert len(db["payments"].docs) == 1 and row["plan_id"] in (PLAN_ID, "plan-test-02")
    assert _plan(db)["balance_remaining"] + other["balance_remaining"] == 1500.0


def test_row_linked_between_check_and_update_is_never_duplicated(rig):
    """Conditional link loses (row linked meanwhile, e.g. by a manual edit): 409, no insert, no balance move."""
    db, app = rig
    _second_plan(db)
    row = _poller_row(db)

    def steal(coll, flt):
        if flt.get("_id") == "pay-poll-1":
            row["plan_id"] = "plan-test-02"
    db["payments"].before_update = steal
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 409 and r.json()["error"] == "transaction_id_used_on_other_plan"
    assert len(db["payments"].docs) == 1 and row["plan_id"] == "plan-test-02"
    assert _plan(db)["balance_remaining"] == 800.0
    assert db["payment_plan_payment_claims"].docs[0]["status"] == "failed"


def test_row_amount_changed_between_check_and_update_needs_review(rig):
    db, app = rig
    row = _poller_row(db)

    def change(coll, flt):
        if flt.get("_id") == "pay-poll-1":
            row["amount"] = 999.0
    db["payments"].before_update = change
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 409 and r.json()["error"] == "payment_needs_review"
    assert "plan_id" not in row and len(db["payments"].docs) == 1
    assert _plan(db)["balance_remaining"] == 800.0
