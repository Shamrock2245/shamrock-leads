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


@pytest.mark.parametrize("booking", ["", None])
def test_logged_row_without_booking_is_refused(rig, booking):
    """Codex P1 @2ad7c3b: an unmatched receipt (booking "") has no verified owner."""
    db, app = rig
    row = _poller_row(db, booking=booking)
    before = dict(row)
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_unowned"
    assert row == before and _plan(db)["balance_remaining"] == 800.0
    assert db["payment_plan_payment_claims"].docs == []


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


def test_linked_row_counts_once_in_status_filtered_revenue(rig):
    """Reports sum status in (completed, paid, success) by timestamp: the linked
    card payment must still be counted, exactly once."""
    db, app = rig
    row = _poller_row(db)
    _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    counted = [d for d in db["payments"].docs
               if d.get("status") in ("completed", "paid", "success") and d.get("timestamp")]
    assert len(counted) == 1 and counted[0]["amount"] == 100.0
    assert row["logged_status"] == "approved" and row["type"] == "installment"
    assert row["gmail_message_id"] == "gm-1" and row["source"] == "swipesimple_gmail_poll"


# ── Codex P1s on b69809f ─────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["declined", "refunded", "voided", "void", "pending", "failed", "", None, "DECLINED"])
def test_unsettled_logged_row_is_never_linked(rig, status):
    """(a) Only settled card payments may be linked; everything else 409, before any claim."""
    db, app = rig
    row = _poller_row(db, status=status)
    if status is None:
        row.pop("status")
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_not_settled"
    assert "plan_id" not in row and len(db["payments"].docs) == 1
    assert db["payment_plan_payment_claims"].docs == []
    assert _plan(db)["balance_remaining"] == 800.0
    assert db["audit_events"].docs == []


@pytest.mark.parametrize("status", ["approved", "paid", "completed", "success", " Approved "])
def test_settled_statuses_link(rig, status):
    db, app = rig
    row = _poller_row(db, status=status)
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 200 and row["plan_id"] == PLAN_ID


@pytest.mark.parametrize("event", ["payment.failed", "payment.refunded", "payment.voided", ""])
def test_webhook_row_with_non_completed_event_refused(rig, event):
    db, app = rig
    row = _poller_row(db, source=None, status="approved", raw_payload={"event_type": event})
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_not_settled"
    assert "plan_id" not in row


def test_webhook_completed_event_links(rig):
    db, app = rig
    row = _poller_row(db, source=None, status="approved", raw_payload={"event_type": "payment.completed"})
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 200 and row["plan_id"] == PLAN_ID


def test_link_write_exception_fails_claim_and_leaves_balance(rig):
    """(c) Link write raises: clean 500, claim 'failed', no balance change; retry -> needs_review."""
    db, app = rig
    row = _poller_row(db)

    def boom(coll, flt):
        raise RuntimeError("transient write failure")
    db["payments"].before_update = boom
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 500 and r.json()["error"] == "payment_record_failed"
    claim = db["payment_plan_payment_claims"].docs[0]
    assert claim["status"] == "failed" and claim["failed_reason"] == "logged_payment_link_error"
    assert _plan(db)["balance_remaining"] == 800.0 and _plan(db)["payments_made"] == 1
    assert "plan_id" not in row and db["audit_events"].docs == []
    (r2,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r2.status_code == 409 and r2.json()["error"] == "payment_needs_review"


def test_successful_link_writes_audit_event(rig):
    """(d) Immutable audit_events record for the association, ar_service._audit shape."""
    db, app = rig
    _poller_row(db)

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.post(f"/api/payments/plans/{PLAN_ID}/pay",
                                json={"amount": 100, "transaction_id": "TX-CARD"},
                                headers={"X-Actor": "desk-test"})
    r = asyncio.run(go())
    assert r.status_code == 200
    (ev,) = db["audit_events"].docs
    assert ev["event_type"] == "payment_plan_logged_payment_linked"
    assert ev["entity_type"] == "payment_plan" and ev["entity_id"] == PLAN_ID
    assert ev["actor"] == "desk-test" and ev["agent_name"] == "desk-test"
    assert ev["transaction_id"] == "TX-CARD" and ev["payment_id"] == "pay-poll-1"
    assert ev["plan_id_before"] is None and ev["plan_id_after"] == PLAN_ID
    assert ev["status_before"] == "approved" and ev["status_after"] == "completed"
    assert ev["amount"] == 100.0 and ev["timestamp"] and ev["created_at"]


def test_no_audit_for_cash_insert_or_lost_link(rig):
    db, app = rig
    _post_many(app, [{"amount": 100, "transaction_id": "TX-CASH-1"}])
    assert db["audit_events"].docs == []


def test_audit_write_failure_reverts_link_and_reports_error(rig):
    """Codex P1 (0a6581b): never report success without a durable audit record."""
    db, app = rig
    row = _poller_row(db)
    before = dict(row)
    db["audit_events"].fail_insert = True
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 503 and r.json()["error"] == "audit_write_failed"
    assert r.json().get("success") is not True
    assert row == before                                   # link undone exactly
    assert _plan(db)["balance_remaining"] == 800.0 and _plan(db)["payments_made"] == 1
    claim = db["payment_plan_payment_claims"].docs[0]
    assert claim["status"] == "failed" and claim["failed_reason"] == "audit_write_failed"
    db["audit_events"].fail_insert = False
    (r2,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r2.status_code == 409 and r2.json()["error"] == "payment_needs_review"


def test_audit_failure_revert_does_not_touch_row_relinked_by_someone_else(rig):
    db, app = rig
    _second_plan(db)
    row = _poller_row(db)
    db["audit_events"].fail_insert = True
    orig = db["audit_events"].insert_one

    async def steal_then_fail(doc):
        row["plan_id"] = "plan-test-02"
        row["plan_linked_at"] = "someone-else"
        return await orig(doc)
    db["audit_events"].insert_one = steal_then_fail
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 503
    assert row["plan_id"] == "plan-test-02"                # revert was conditional on our link
    assert _plan(db)["balance_remaining"] == 800.0


@pytest.mark.parametrize("new_status", ["reconciled", "refunded", "declined", "pending"])
def test_status_changed_between_check_and_link_fails_closed(rig, new_status):
    """Codex P1 (0a6581b): the conditional link pins the validated status."""
    db, app = rig
    row = _poller_row(db)

    def change(coll, flt):
        if flt.get("_id") == "pay-poll-1":
            row["status"] = new_status
    db["payments"].before_update = change
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 409 and r.json()["error"] == "payment_needs_review"
    assert row["status"] == new_status and "plan_id" not in row
    assert _plan(db)["balance_remaining"] == 800.0 and db["audit_events"].docs == []


def test_webhook_event_changed_between_check_and_link_fails_closed(rig):
    db, app = rig
    row = _poller_row(db, source=None, status="approved", raw_payload={"event_type": "payment.completed"})

    def change(coll, flt):
        if flt.get("_id") == "pay-poll-1":
            row["raw_payload"]["event_type"] = "payment.refunded"
    db["payments"].before_update = change
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 409 and "plan_id" not in row
    assert _plan(db)["balance_remaining"] == 800.0


def test_event_added_between_check_and_link_fails_closed(rig):
    db, app = rig
    row = _poller_row(db)

    def change(coll, flt):
        if flt.get("_id") == "pay-poll-1":
            row["raw_payload"] = {"event_type": "payment.refunded"}
    db["payments"].before_update = change
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 409 and "plan_id" not in row


# ── Codex P1s on bd0f4a3 ────────────────────────────────────────────────────


def _fail_plan_inc(db):
    real = db["payment_plans"].update_one

    async def boom(flt, update, upsert=False):
        if "$inc" in update:
            raise RuntimeError("plan write failed")
        return await real(flt, update, upsert)
    db["payment_plans"].update_one = boom


def test_plan_update_failure_after_link_reverts_link(rig):
    db, app = rig
    row = _poller_row(db)
    before = dict(row)
    _fail_plan_inc(db)
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 500 and r.json()["error"] == "payment_record_failed"
    assert row == before                                   # not counted as plan revenue
    assert _plan(db)["balance_remaining"] == 800.0
    claim = db["payment_plan_payment_claims"].docs[0]
    assert claim["status"] == "failed" and claim["failed_reason"] == "plan_update_error"
    kinds = [e["event_type"] for e in db["audit_events"].docs]
    assert kinds == ["payment_plan_logged_payment_linked", "payment_plan_logged_payment_unlinked"]
    assert all(e.get("reason") and e.get("confidence") == 1.0 for e in db["audit_events"].docs)


def test_plan_update_failure_on_insert_path_removes_row(rig):
    db, app = rig
    _fail_plan_inc(db)
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CASH"}])
    assert r.status_code == 500
    assert db["payments"].docs == [] and _plan(db)["balance_remaining"] == 800.0


def test_paid_in_full_clamp_failure_does_not_undo_applied_payment(rig):
    """A clamp write that raises is ignored when the read-back balance is still positive."""
    db, app = rig
    real = db["payment_plans"].update_one

    async def clamp_boom(flt, update, upsert=False):
        if "balance_remaining" in flt:
            raise RuntimeError("clamp failed")
        return await real(flt, update, upsert)
    db["payment_plans"].update_one = clamp_boom
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CASH"}])
    body = r.json()
    assert r.status_code == 200 and body["balance_remaining"] == 700.0 and body["status"] == "active"
    assert _plan(db)["balance_remaining"] == 700.0 and _plan(db)["status"] == "active"
    claim = db["payment_plan_payment_claims"].docs[0]
    assert claim["status"] == "applied" and claim["balance_after"] == 700.0


def test_revert_does_not_overwrite_concurrent_reconciliation_status(rig):
    db, app = rig
    row = _poller_row(db)
    orig = db["audit_events"].insert_one

    async def reconcile_then_fail(doc):
        row["status"] = "reconciled"            # reconciliation service wrote meanwhile
        raise RuntimeError("audit down")
    db["audit_events"].insert_one = reconcile_then_fail
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 503
    assert row["status"] == "reconciled"         # newer state kept, not restored to 'approved'
    assert db["payment_plan_payment_claims"].docs[0]["status"] == "failed"
    assert _plan(db)["balance_remaining"] == 800.0
    del orig


def test_poller_row_arriving_during_insert_is_detected_and_undone(rig):
    db, app = rig
    real = db["payments"].insert_one

    async def insert_then_poller(doc):
        await real(doc)
        db["payments"].docs.append({"_id": "late-poll", "transaction_id": "TX-LATE",
                                    "amount": 100.0, "status": "approved"})
    db["payments"].insert_one = insert_then_poller
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-LATE"}])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_ambiguous"
    assert [d["_id"] for d in db["payments"].docs] == ["late-poll"]   # our row removed
    assert _plan(db)["balance_remaining"] == 800.0
    assert db["payment_plan_payment_claims"].docs[0]["failed_reason"] == "logged_payment_ambiguous"


def test_second_logged_row_arriving_during_link_is_detected_and_undone(rig):
    db, app = rig
    row = _poller_row(db)
    before = dict(row)

    def add_webhook_row(coll, flt):
        coll.docs.append({"_id": "late-hook", "transaction_id": "TX-CARD", "amount": 100.0,
                          "status": "completed", "raw_payload": {"event_type": "payment.completed"}})
    db["payments"].before_update = add_webhook_row
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_ambiguous"
    assert row == before and _plan(db)["balance_remaining"] == 800.0
    kinds = [e["event_type"] for e in db["audit_events"].docs]
    assert kinds == ["payment_plan_logged_payment_linked", "payment_plan_logged_payment_unlinked"]
    link, unlink = db["audit_events"].docs
    assert link["plan_id_after"] == PLAN_ID and unlink["plan_id_before"] == PLAN_ID
    assert unlink["plan_id_after"] is None and unlink["reason"] and link["reason"]


def test_link_audit_has_reason_and_confidence(rig):
    db, app = rig
    _poller_row(db)
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 200
    (ev,) = db["audit_events"].docs
    assert ev["reason"] and ev["confidence"] == 1.0 and ev["match_method"] == "exact_transaction_id"


# ── Codex findings on 2ad7c3b ───────────────────────────────────────────────


def test_link_write_applied_but_driver_raised_is_reverted(rig):
    """Unknown write outcome: the update persisted, then the driver raised."""
    db, app = rig
    row = _poller_row(db)
    before = dict(row)
    real = type(db["payments"]).update_one
    state = {"first": True}

    async def applied_then_raise(flt, update, upsert=False):
        res = await real(db["payments"], flt, update, upsert)
        if state["first"]:
            state["first"] = False
            raise RuntimeError("write concern timeout")
        return res
    db["payments"].update_one = applied_then_raise
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 500
    assert row == before                                  # link undone
    assert _plan(db)["balance_remaining"] == 800.0
    claim = db["payment_plan_payment_claims"].docs[0]
    assert claim["status"] == "failed" and claim["failed_reason"] == "logged_payment_link_error"


def test_link_write_unknown_and_revert_fails_is_flagged(rig):
    db, app = rig
    row = _poller_row(db)
    real = type(db["payments"]).update_one

    async def applied_then_raise(flt, update, upsert=False):
        if "$unset" in update:
            raise RuntimeError("revert failed")
        await real(db["payments"], flt, update, upsert)
        raise RuntimeError("write concern timeout")
    db["payments"].update_one = applied_then_raise
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 500 and _plan(db)["balance_remaining"] == 800.0
    claim = db["payment_plan_payment_claims"].docs[0]
    assert claim["failed_reason"] == "logged_payment_link_unreverted"
    assert row["plan_id"] == PLAN_ID                      # left for staff review, flagged


def test_booking_reassigned_between_check_and_link_fails_closed(rig):
    db, app = rig
    row = _poller_row(db)

    def reassign(coll, flt):
        row["booking_number"] = "TEST-OTHER"
    db["payments"].before_update = reassign
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 409
    assert row["booking_number"] == "TEST-OTHER" and "plan_id" not in row
    assert _plan(db)["balance_remaining"] == 800.0


def test_link_keeps_provider_created_at_as_timestamp(rig):
    db, app = rig
    row = _poller_row(db, created_at="2026-09-01T12:00:00+00:00")
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 200 and row["timestamp"] == "2026-09-01T12:00:00+00:00"


# ── Codex findings on dbb24d0 ───────────────────────────────────────────────


def _fail_plan_reads_after_inc(db, fails_left):
    """Raise on plan find_one after the balance $inc, ``fails_left`` times."""
    real_find = db["payment_plans"].find_one
    real_update = db["payment_plans"].update_one
    state = {"armed": False, "fails_left": fails_left}

    async def arm_after_inc(flt, update, upsert=False):
        res = await real_update(flt, update, upsert)
        if "$inc" in update:
            state["armed"] = True
        return res

    async def fail_readback(flt=None, *a, **k):
        if state["armed"] and state["fails_left"]:
            state["fails_left"] -= 1
            raise RuntimeError("readback failed")
        return await real_find(flt, *a, **k)

    db["payment_plans"].update_one = arm_after_inc
    db["payment_plans"].find_one = fail_readback


def test_settling_clamp_failure_fails_closed_without_undo(rig):
    """Codex P1: a payment that clears the balance must not report success while the plan stays active and negative."""
    db, app = rig
    real = db["payment_plans"].update_one

    async def clamp_boom(flt, update, upsert=False):
        if "balance_remaining" in flt:
            raise RuntimeError("clamp failed")
        return await real(flt, update, upsert)
    db["payment_plans"].update_one = clamp_boom
    (r,) = _post_many(app, [{"amount": 900, "transaction_id": "TX-SETTLE"}])
    body = r.json()
    assert r.status_code == 500 and body["error"] == "paid_in_full_clamp_failed"
    assert body.get("success") is not True
    assert "balance_remaining" not in body and "status" not in body
    plan = _plan(db)
    assert plan["balance_remaining"] == -100.0 and plan["status"] == "active"
    assert len(db["payments"].docs) == 1
    claim = db["payment_plan_payment_claims"].docs[0]
    assert claim["status"] == "failed" and claim["failed_reason"] == "paid_in_full_clamp_failed"
    assert "balance_after" not in claim
    (r2,) = _post_many(app, [{"amount": 900, "transaction_id": "TX-SETTLE"}])
    assert r2.status_code == 409 and r2.json()["error"] == "payment_needs_review"
    assert _plan(db)["balance_remaining"] == -100.0 and len(db["payments"].docs) == 1


def test_settling_clamp_retries_then_confirms(rig):
    """The clamp is tried again after a failed write; a later success is what the response reports."""
    db, app = rig
    real = db["payment_plans"].update_one
    state = {"n": 0}

    async def flaky_clamp(flt, update, upsert=False):
        if "balance_remaining" in flt:
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("clamp failed")
        return await real(flt, update, upsert)
    db["payment_plans"].update_one = flaky_clamp
    (r,) = _post_many(app, [{"amount": 900, "transaction_id": "TX-SETTLE"}])
    body = r.json()
    assert r.status_code == 200 and body["status"] == "paid_in_full" and body["balance_remaining"] == 0
    assert state["n"] == 2
    assert _plan(db)["status"] == "paid_in_full" and _plan(db)["balance_remaining"] == 0
    claim = db["payment_plan_payment_claims"].docs[0]
    assert claim["status"] == "applied" and claim["balance_after"] == 0


def test_clamp_timeout_after_apply_is_reconciled_by_readback(rig):
    """A clamp that persisted and then raised is confirmed from the plan, not treated as a failure."""
    db, app = rig
    real = db["payment_plans"].update_one
    state = {"raised": False}

    async def apply_then_raise(flt, update, upsert=False):
        res = await real(flt, update, upsert)
        if "balance_remaining" in flt and not state["raised"]:
            state["raised"] = True
            raise RuntimeError("clamp timeout")
        return res
    db["payment_plans"].update_one = apply_then_raise
    (r,) = _post_many(app, [{"amount": 900, "transaction_id": "TX-SETTLE"}])
    body = r.json()
    assert r.status_code == 200 and body["status"] == "paid_in_full" and body["balance_remaining"] == 0
    assert _plan(db)["status"] == "paid_in_full" and _plan(db)["balance_remaining"] == 0
    assert db["payment_plan_payment_claims"].docs[0]["balance_after"] == 0


def test_plan_readback_failure_does_not_fabricate_state(rig):
    """Codex P1: a failed post-update read must not claim balance 0 or status active."""
    db, app = rig
    _fail_plan_reads_after_inc(db, fails_left=2)
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CASH"}])
    body = r.json()
    assert r.status_code == 500 and body["error"] == "plan_readback_failed"
    assert body.get("success") is not True
    assert "balance_remaining" not in body and "status" not in body
    assert _plan(db)["balance_remaining"] == 700.0
    assert len(db["payments"].docs) == 1
    claim = db["payment_plan_payment_claims"].docs[0]
    assert claim["status"] == "failed" and claim["failed_reason"] == "plan_readback_failed"
    assert "balance_after" not in claim
    (r2,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CASH"}])
    assert r2.status_code == 409 and r2.json()["error"] == "payment_needs_review"


def test_plan_readback_retries_then_returns_real_balance(rig):
    db, app = rig
    _fail_plan_reads_after_inc(db, fails_left=1)
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CASH"}])
    body = r.json()
    assert r.status_code == 200 and body["balance_remaining"] == 700.0 and body["status"] == "active"
    assert db["payment_plan_payment_claims"].docs[0]["balance_after"] == 700.0


def test_clash_without_link_audit_does_not_emit_orphan_unlink(rig):
    """If the link audit cannot be saved, the compensation revert writes no unlink event."""
    db, app = rig
    row = _poller_row(db)
    before = dict(row)

    def add_webhook_row(coll, flt):
        coll.docs.append({"_id": "late-hook", "transaction_id": "TX-CARD", "amount": 100.0,
                          "status": "completed", "raw_payload": {"event_type": "payment.completed"}})
    db["payments"].before_update = add_webhook_row
    db["audit_events"].fail_insert = True
    (r,) = _post_many(app, [{"amount": 100, "transaction_id": "TX-CARD"}])
    assert r.status_code == 503 and r.json()["error"] == "audit_write_failed"
    assert row == before and _plan(db)["balance_remaining"] == 800.0
    assert db["audit_events"].docs == []
    assert db["payment_plan_payment_claims"].docs[0]["failed_reason"] == "audit_write_failed"
