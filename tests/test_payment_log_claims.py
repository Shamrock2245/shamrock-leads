"""Fail-closed provider payment recording. No SwipeSimple, Gmail, or live Slack."""
from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from dashboard.routers import payment_plans as pp
from dashboard.routers.webhooks import webhooks_bp
from dashboard.services import payment_log_claims as plc
from dashboard.services.swipesimple_receipt_poller import SwipeSimpleReceiptPoller

_real_notify = plc._notify_staff_review
from tests.test_payment_plan_pay_dedupe import PLAN_ID, Coll, DB, _plan

TXN = "TX-REV-1"
BOOKING = "BK-1"


def _run(coro):
    return asyncio.run(coro)


class _Hello:
    async def command(self, name, *args, **kwargs):
        if name in ("hello", "ismaster"):
            return {"setName": "atlas-rs", "ok": 1}
        raise RuntimeError(name)


class CommitSession:
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
        tries = 0
        while tries < 4:
            tries += 1
            self.client.attempts += 1
            snap = {name: copy.deepcopy(coll.docs) for name, coll in self.client.db.items()}
            try:
                await callback(self)
                return
            except plc.RetryableTxnError:
                self._restore(snap)
                if self.client.plant:
                    self.client.plant()
                    self.client.plant = None
                continue
            except Exception:
                self._restore(snap)
                raise
        raise AssertionError("transaction retries exhausted")


class TxnClient:
    def __init__(self, db):
        self.db = db
        self.admin = _Hello()
        self.attempts = 0
        self.plant = None

    async def start_session(self):
        return CommitSession(self)


class ConflictColl(Coll):
    """The Nth lock upsert raises a transient write conflict. Default is the first."""

    def __init__(self, raise_on=1):
        super().__init__()
        self.raised = False
        self.raise_on = raise_on
        self.incs = 0

    async def update_one(self, flt, update, upsert=False, session=None, **kwargs):
        await super().update_one(flt, update, upsert=upsert, session=session)
        if (update.get("$inc") or {}).get("version"):
            self.incs += 1
            if not self.raised and self.incs == self.raise_on:
                self.raised = True
                raise plc.RetryableTxnError()


@pytest.fixture
def slack_alerts(monkeypatch):
    calls = []

    def fake(hit):
        calls.append(dict(hit))

    monkeypatch.setattr(plc, "_notify_staff_review", fake)
    monkeypatch.setattr(
        "writers.slack_notifier.SlackNotifier.notify_alert",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live slack")),
    )
    return calls


@pytest.fixture
def db(monkeypatch, slack_alerts):
    plc._index_ready = False
    plc._lock_index_ready = False
    plc._txn_support_cache.clear()
    database = DB()
    client = TxnClient(database)

    async def _txn():
        return client

    monkeypatch.setattr(plc, "_transaction_client", _txn)
    return database


def _bond(booking=BOOKING, **extra):
    doc = {"booking_number": booking}
    doc.update(extra)
    return doc


def _stamp(doc):
    return {
        "amount": doc.get("last_payment_amount"),
        "at": doc.get("last_payment_at"),
        "status": doc.get("last_payment_status"),
        "txn": doc.get("last_transaction_id"),
        "source": doc.get("last_payment_source"),
    }


async def _record(db, *, kind="charge", booking=BOOKING, txn=TXN, amount=100, status=None, event_type=None, plan_id=None):
    if kind == "refund":
        status = status or "refunded"
        event_type = event_type or "payment.refunded"
        source = "swipesimple_webhook"
    elif kind == "failure":
        status = status or "declined"
        event_type = event_type or "payment.failed"
        source = "swipesimple_webhook"
    else:
        status = status or "approved"
        event_type = event_type or "payment.completed"
        source = "swipesimple_webhook"
    extra = {"event_type": event_type}
    if plan_id:
        extra["plan_id"] = plan_id
    return await plc.record_provider_payment(
        db,
        source=source,
        transaction_id=txn,
        amount=amount,
        status=status,
        booking_number=booking,
        extra=extra,
    )


def _rows(db, kind=None):
    rows = db["payments"].docs
    if kind is None:
        return rows
    return [row for row in rows if row.get("event_kind") == kind]


def _audit(db, kind):
    matches = [doc for doc in db["audit_events"].docs if doc.get("event_kind") == kind]
    assert len(matches) == 1
    return matches[0]


def _pay_app(db, monkeypatch):
    pp._inflight_txns.clear()
    pp._txn_support_cache.clear()
    pp._payment_claim_index_ready = False
    plc._index_ready = False
    plc._lock_index_ready = False
    plc._txn_support_cache.clear()
    client = TxnClient(db)

    async def _txn():
        return client

    monkeypatch.setattr(pp, "get_collection", lambda name: db[name])
    monkeypatch.setattr(pp, "_payment_claim_index_ready", False)
    monkeypatch.setattr(pp, "_txn_client", _txn)
    monkeypatch.setattr(plc, "_transaction_client", _txn)
    app = FastAPI()
    app.state.txn_client = client
    app.include_router(pp.payment_plans_bp)
    return app


def _post_pay(app, txn=TXN, amount=100):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.post(
                f"/api/payments/plans/{PLAN_ID}/pay",
                json={"amount": amount, "transaction_id": txn},
            )
    return _run(go())


@pytest.fixture
def plan_db(db):
    db["payment_plans"].docs.append({
        "plan_id": PLAN_ID, "booking_number": BOOKING, "total_premium": 1000.0,
        "down_payment": 200.0, "balance_remaining": 800.0, "installment_amount": 100.0,
        "frequency": "weekly", "payments_made": 1, "total_paid": 200.0, "status": "active",
    })
    db["bond_cases"].docs.append(_bond())
    return db


def test_charge_then_refund_stamps_once_and_alerts_after_commit(db, slack_alerts, monkeypatch):
    db["bond_cases"].docs.append(_bond(
        last_payment_amount=5,
        last_payment_at="yesterday",
        last_payment_status="approved",
        last_transaction_id="OLD",
        last_payment_source="manual",
    ))
    # An unrelated plan must not be flagged: the charge names no plan_id.
    db["payment_plans"].docs.append({"plan_id": "other-plan", "balance_remaining": 800.0})

    recorded = _run(_record(db, kind="charge"))
    assert recorded["committed"] is True and recorded["event_kind"] == "charge"
    bond = db["bond_cases"].docs[0]
    assert bond["last_payment_amount"] == 100.0
    assert bond["last_transaction_id"] == TXN
    assert bond.get("needs_review") is not True
    audit = _audit(db, "charge")
    assert audit["stamped"] is True
    assert audit["_id"] == f"payment-log:{TXN}:charge"
    assert audit["old_last_payment"]["last_payment_amount"] == 5
    assert audit["old_last_payment"]["last_transaction_id"] == "OLD"
    assert audit["new_last_payment"]["last_payment_amount"] == 100.0
    assert audit["new_last_payment"]["last_transaction_id"] == TXN
    assert slack_alerts == []
    assert db["payments"].index_calls == 0 and db["payments"].unique == []
    assert ("tenant_id", "txn_key") in db["payment_txn_locks"].unique
    assert ("tenant_id", "txn_key", "event_kind") in db["payment_log_claims"].unique
    assert len(db["payment_log_claims"].docs) == 1

    def boom(hit):
        slack_alerts.append(dict(hit))
        raise RuntimeError("slack down")

    monkeypatch.setattr(plc, "_notify_staff_review", boom)
    refunded = _run(_record(db, kind="refund"))
    assert refunded["committed"] is True and refunded["event_kind"] == "refund"
    assert len(_rows(db, "refund")) == 1
    assert len(_rows(db, "charge")) == 1
    assert bond["last_payment_amount"] == 100.0
    assert bond["last_transaction_id"] == TXN
    assert bond["needs_review"] is True
    assert db["payment_plans"].docs[0].get("needs_review") is not True
    assert db["payment_plans"].docs[0]["balance_remaining"] == 800.0
    assert len(db["payment_log_claims"].docs) == 1  # refund writes no claim
    assert len(slack_alerts) == 1
    assert slack_alerts[0]["kind"] == "refund"
    assert slack_alerts[0]["plan_id"] == "none"
    again = _run(_record(db, kind="refund"))
    assert again["duplicate"] is True
    assert len(_rows(db, "refund")) == 1
    assert len(slack_alerts) == 1


def test_refund_then_charge_records_needs_review_without_stamp(db, slack_alerts):
    db["bond_cases"].docs.append(_bond())
    _run(_record(db, kind="refund"))
    bond = db["bond_cases"].docs[0]
    assert bond.get("last_payment_amount") is None
    assert bond["needs_review"] is True
    assert len(slack_alerts) == 1

    recorded = _run(_record(db, kind="charge"))
    assert recorded["committed"] is True
    charge = _rows(db, "charge")[0]
    assert charge["needs_review"] is True
    assert charge["needs_review_reason"] == "reversal_sibling"
    assert bond.get("last_payment_amount") is None
    assert bond.get("last_transaction_id") is None
    assert _audit(db, "charge")["stamped"] is False
    assert "old_last_payment" not in _audit(db, "charge")


def test_pay_then_refund_does_not_change_balance_or_claim(plan_db, slack_alerts, monkeypatch):
    app = _pay_app(plan_db, monkeypatch)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        paid = _post_pay(app)
    assert paid.status_code == 200 and paid.json()["idempotent"] is False
    assert _plan(plan_db)["balance_remaining"] == 700.0
    claim = plan_db["payment_plan_payment_claims"].docs[0]
    assert claim["status"] == "applied"

    _run(_record(plan_db, kind="refund"))
    assert _plan(plan_db)["balance_remaining"] == 700.0
    assert claim["status"] == "applied"
    assert _plan(plan_db)["needs_review"] is True
    assert plan_db["bond_cases"].docs[0]["needs_review"] is True
    assert plan_db["bond_cases"].docs[0].get("last_payment_amount") is None
    assert len(_rows(plan_db)) == 2
    assert len(slack_alerts) == 1
    assert slack_alerts[0]["plan_id"] == PLAN_ID
    assert len(plan_db["payment_log_claims"].docs) == 0


def test_refund_then_pay_retry_is_409_with_no_balance_change(plan_db, monkeypatch):
    app = _pay_app(plan_db, monkeypatch)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        paid = _post_pay(app)
        assert paid.status_code == 200
        balance = _plan(plan_db)["balance_remaining"]
        _run(_record(plan_db, kind="refund"))
        again = _post_pay(app)
    assert again.status_code == 409
    assert again.json()["error"] == "payment_needs_review"
    assert _plan(plan_db)["balance_remaining"] == balance
    assert plan_db["payment_plan_payment_claims"].docs[0]["status"] == "applied"
    assert len(_rows(plan_db)) == 2


def test_write_conflict_retry_rereads_the_refund_and_does_not_stamp(db, monkeypatch):
    db["bond_cases"].docs.append(_bond())
    locks = ConflictColl()
    db["payment_txn_locks"] = locks
    client = TxnClient(db)

    def plant():
        db["payments"].docs.append({
            "transaction_id": TXN,
            "event_kind": "refund",
            "booking_number": BOOKING,
            "amount": 100.0,
            "status": "refunded",
        })

    client.plant = plant

    async def _txn():
        return client

    monkeypatch.setattr(plc, "_transaction_client", _txn)
    plc._lock_index_ready = False
    plc._index_ready = False

    recorded = _run(_record(db, kind="charge"))
    assert recorded["committed"] is True
    assert client.attempts == 2
    assert locks.raised is True
    charge = _rows(db, "charge")[0]
    assert charge["needs_review"] is True
    assert charge["needs_review_reason"] == "reversal_sibling"
    assert db["bond_cases"].docs[0].get("last_payment_amount") is None
    assert _audit(db, "charge")["stamped"] is False


def test_cross_booking_charge_records_needs_review_without_stamp(db, slack_alerts):
    db["bond_cases"].docs.append(_bond(BOOKING))
    db["bond_cases"].docs.append(_bond("BK-2"))
    _run(_record(db, kind="refund", booking="BK-2"))
    _run(_record(db, kind="charge", booking=BOOKING))
    charge = _rows(db, "charge")[0]
    assert charge["booking_number"] == BOOKING
    assert charge["needs_review"] is True
    assert charge["needs_review_reason"] == "cross_booking"
    for bond in db["bond_cases"].docs:
        assert bond.get("last_payment_amount") is None
        assert bond.get("last_transaction_id") is None
    assert _audit(db, "charge")["stamped"] is False
    assert [hit["kind"] for hit in slack_alerts] == ["refund", "charge"]


def test_refund_and_failure_record_own_rows_without_balance_or_stamp(db, slack_alerts):
    """(a) A refund and a failure are their own rows plus needs_review. No balance or stamp change."""
    db["bond_cases"].docs.append(_bond(
        last_payment_amount=50,
        last_payment_at="yesterday",
        last_payment_status="approved",
        last_transaction_id="OLD",
        last_payment_source="manual",
    ))
    db["payment_plans"].docs.append({
        "plan_id": PLAN_ID, "balance_remaining": 800.0, "total_paid": 200.0, "payments_made": 1,
    })
    charged = _run(_record(db, kind="charge", plan_id=PLAN_ID))
    assert charged["committed"] is True
    bond = db["bond_cases"].docs[0]
    stamp = _stamp(bond)
    assert stamp["amount"] == 100.0 and stamp["txn"] == TXN
    balance = db["payment_plans"].docs[0]["balance_remaining"]

    refunded = _run(_record(db, kind="refund"))
    failed = _run(_record(db, kind="failure"))
    assert refunded["committed"] is True and failed["committed"] is True
    assert len(_rows(db, "refund")) == 1 and len(_rows(db, "failure")) == 1
    assert _rows(db, "refund")[0]["needs_review"] is True
    assert _rows(db, "failure")[0]["needs_review"] is True
    assert _rows(db, "failure")[0]["event_kind"] == "failure"
    assert _stamp(bond) == stamp
    plan = db["payment_plans"].docs[0]
    assert plan["balance_remaining"] == balance
    assert plan["total_paid"] == 200.0 and plan["payments_made"] == 1
    assert plan["needs_review"] is True
    assert bond["needs_review"] is True
    assert len(db["payment_log_claims"].docs) == 1
    assert [hit["kind"] for hit in slack_alerts] == ["refund", "failure"]


def test_charge_after_reversal_checks_ownership_and_does_not_stamp(db, slack_alerts):
    """(b) A charge after its reversal is needs_review, with no stamp, after the sibling booking is checked."""
    db["bond_cases"].docs.append(_bond(BOOKING, _id="bond-owned"))
    db["bond_cases"].docs.append(_bond("BK-2", _id="bond-other"))

    _run(_record(db, kind="refund", booking=BOOKING, txn="TX-OWN"))
    late = _run(_record(db, kind="charge", booking="BK-2", txn="TX-OWN"))
    assert late["committed"] is True
    crossed = [row for row in _rows(db, "charge") if row["transaction_id"] == "TX-OWN"][0]
    assert crossed["booking_number"] == "BK-2"
    assert crossed["needs_review"] is True
    assert crossed["needs_review_reason"] == "cross_booking"
    assert _audit(db, "charge")["stamped"] is False

    _run(_record(db, kind="failure", booking=BOOKING, txn="TX-SAME"))
    same = _run(_record(db, kind="charge", booking=BOOKING, txn="TX-SAME"))
    assert same["committed"] is True
    owned = [row for row in _rows(db, "charge") if row["transaction_id"] == "TX-SAME"][0]
    assert owned["booking_number"] == BOOKING
    assert owned["needs_review_reason"] == "reversal_sibling"
    for bond in db["bond_cases"].docs:
        assert bond.get("last_payment_amount") is None
        assert bond.get("last_transaction_id") is None


def test_pay_write_conflict_retry_sees_reversal_and_does_not_change_balance(plan_db, monkeypatch):
    """(d) /pay re-reads siblings after a payment_txn_locks write conflict and does not apply."""
    app = _pay_app(plan_db, monkeypatch)
    locks = ConflictColl(raise_on=2)
    plan_db["payment_txn_locks"] = locks
    client = app.state.txn_client

    def plant():
        plan_db["payments"].docs.append({
            "transaction_id": TXN,
            "event_kind": "refund",
            "booking_number": BOOKING,
            "amount": 100.0,
            "status": "refunded",
        })

    client.plant = plant
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        response = _post_pay(app)
    assert response.status_code == 409
    assert response.json()["error"] == "payment_needs_review"
    assert client.attempts == 3
    assert locks.raised is True
    plan = _plan(plan_db)
    assert plan["balance_remaining"] == 800.0
    assert plan["payments_made"] == 1 and plan["total_paid"] == 200.0
    assert "collected_transaction_ids" not in plan
    assert plan_db["bond_cases"].docs[0].get("last_payment_amount") is None
    claim = plan_db["payment_plan_payment_claims"].docs[0]
    assert claim["status"] == "failed"
    assert len(_rows(plan_db, "refund")) == 1


def test_review_state_audit_records_before_and_after(db, slack_alerts):
    """(e) Each needs_review write is audited with the old and new values."""
    db["bond_cases"].docs.append(_bond(
        _id="bond-1",
        needs_review=False,
        needs_review_reason="clear",
    ))
    db["payment_plans"].docs.append({
        "plan_id": PLAN_ID,
        "needs_review": False,
        "needs_review_reason": "clear",
        "balance_remaining": 800.0,
    })
    _run(_record(db, kind="charge", plan_id=PLAN_ID))
    _run(_record(db, kind="refund"))
    refund_audit = _audit(db, "refund")
    bond_review = refund_audit["bond_review"]
    assert bond_review["record_id"] == "bond-1"
    assert bond_review["old_needs_review"] is False
    assert bond_review["new_needs_review"] is True
    assert bond_review["old_needs_review_reason"] == "clear"
    assert bond_review["new_needs_review_reason"] == "provider_refund"
    plan_review = refund_audit["plan_review"]
    assert plan_review["plan_id"] == PLAN_ID
    assert plan_review["old_needs_review"] is False
    assert plan_review["new_needs_review"] is True
    assert plan_review["old_needs_review_reason"] == "clear"
    assert plan_review["new_needs_review_reason"] == "provider_refund"
    assert refund_audit["payment_review"] == {
        "old_needs_review": None,
        "new_needs_review": True,
        "old_needs_review_reason": None,
        "new_needs_review_reason": "provider_refund",
    }
    assert db["payment_plans"].docs[0]["balance_remaining"] == 800.0

    _run(_record(db, kind="failure"))
    failure_audit = _audit(db, "failure")
    assert failure_audit["bond_review"]["old_needs_review"] is True
    assert failure_audit["bond_review"]["old_needs_review_reason"] == "provider_refund"
    assert failure_audit["bond_review"]["new_needs_review"] is True
    assert failure_audit["bond_review"]["new_needs_review_reason"] == "provider_failure"
    assert failure_audit["plan_review"]["old_needs_review_reason"] == "provider_refund"
    assert failure_audit["plan_review"]["new_needs_review_reason"] == "provider_failure"
    assert failure_audit["payment_review"]["new_needs_review_reason"] == "provider_failure"


def test_mirrored_bond_documents_stamp_and_distinct_cases_do_not(db, slack_alerts):
    """Staff-chain mirrors share one booking and both receive last_payment_*."""
    case_id = "case-1"
    db["bond_cases"].docs.append(_bond(
        _id="bc-1", bond_case_id=case_id, Bond_Case_ID=case_id,
        last_payment_amount=5, last_transaction_id="OLD",
    ))
    db["active_bonds"].docs.append(_bond(
        _id="ab-1", bond_case_id=case_id, Bond_Case_ID=case_id,
    ))
    recorded = _run(_record(db, kind="charge"))
    assert recorded["committed"] is True
    assert db["bond_cases"].docs[0]["last_transaction_id"] == TXN
    assert db["bond_cases"].docs[0]["last_payment_amount"] == 100.0
    assert db["active_bonds"].docs[0]["last_transaction_id"] == TXN
    assert db["active_bonds"].docs[0]["last_payment_amount"] == 100.0
    assert _rows(db, "charge")[0].get("needs_review") is not True
    audit = _audit(db, "charge")
    assert audit["stamped"] is True
    assert audit["old_last_payment"]["last_transaction_id"] == "OLD"
    assert len(audit["stamped_bonds"]) == 2
    assert slack_alerts == []

    _run(_record(db, kind="refund"))
    assert db["bond_cases"].docs[0]["needs_review"] is True
    assert db["active_bonds"].docs[0]["needs_review"] is True
    assert db["bond_cases"].docs[0]["last_transaction_id"] == TXN
    assert db["active_bonds"].docs[0]["last_transaction_id"] == TXN
    assert len(_audit(db, "refund")["bond_reviews"]) == 2

    db["bond_cases"].docs.append(_bond(booking="BK-SPLIT", _id="bc-a", bond_case_id="case-a"))
    db["active_bonds"].docs.append(_bond(booking="BK-SPLIT", _id="ab-b", bond_case_id="case-b"))
    split = _run(_record(db, kind="charge", booking="BK-SPLIT", txn="TX-SPLIT"))
    assert split["committed"] is True
    row = [item for item in _rows(db, "charge") if item["transaction_id"] == "TX-SPLIT"][0]
    assert row["needs_review_reason"] == "booking_not_unique"
    assert db["bond_cases"].docs[-1].get("last_transaction_id") is None
    assert db["active_bonds"].docs[-1].get("last_transaction_id") is None

    db["bond_cases"].docs.append(_bond(booking="BK-TWO", _id="bc-3"))
    db["bond_cases"].docs.append(_bond(booking="BK-TWO", _id="bc-4"))
    doubled = _run(_record(db, kind="charge", booking="BK-TWO", txn="TX-TWO"))
    assert doubled["committed"] is True
    doubled_row = [item for item in _rows(db, "charge") if item["transaction_id"] == "TX-TWO"][0]
    assert doubled_row["needs_review_reason"] == "booking_not_unique"
    assert db["bond_cases"].docs[-1].get("last_payment_amount") is None
    assert db["bond_cases"].docs[-2].get("last_payment_amount") is None


def test_needs_review_charge_alerts_once_after_commit(db, slack_alerts, monkeypatch):
    """A blank receipt is marked read only after one staff alert for the review."""
    db["bond_cases"].docs.append(_bond(last_payment_amount=7, last_transaction_id="KEEP"))
    body = "Amount $100.00\nTransaction id: TX-POLL\n"
    read = []

    class Reader:
        is_configured = True

        def search_messages(self, query, max_results=20):
            return [{"message_id": "m-blank", "subject": "Receipt", "body": body}]

        def mark_as_read(self, mid):
            read.append(mid)

    monkeypatch.setattr(
        "dashboard.services.gmail_reader.GmailReaderService",
        lambda: Reader(),
    )
    result = _run(SwipeSimpleReceiptPoller(db=db).poll())
    assert result["processed"] == 1
    assert read == ["m-blank"]
    assert len(slack_alerts) == 1
    assert slack_alerts[0]["kind"] == "charge"
    assert slack_alerts[0]["txn_key"] == "TX-POLL"
    assert slack_alerts[0]["booking"] == "none"
    assert slack_alerts[0]["reason"] == "booking_not_provided"
    assert db["bond_cases"].docs[0]["last_transaction_id"] == "KEEP"
    charge = [row for row in db["payments"].docs if row.get("transaction_id") == "TX-POLL"][0]
    assert charge["needs_review"] is True

    read.clear()
    again = _run(SwipeSimpleReceiptPoller(db=db).poll())
    assert again["processed"] == 0
    assert read == ["m-blank"]
    assert len(slack_alerts) == 1


def test_applied_pay_retry_rechecks_reversal_inside_the_lock(plan_db, monkeypatch):
    """A refund revealed once the applied claim is read cannot return 200."""
    class RevealPayments(Coll):
        def __init__(self):
            super().__init__()
            self.claim_read = False
            self.revealed = False

        def find(self, flt=None, *a, **k):
            if self.claim_read and not self.revealed:
                self.revealed = True
                self.docs.append({
                    "transaction_id": TXN,
                    "event_kind": "refund",
                    "booking_number": BOOKING,
                    "amount": 100.0,
                    "status": "refunded",
                })
            return super().find(flt, *a, **k)

    class ClaimWatch(Coll):
        def __init__(self, reveal):
            super().__init__()
            self.reveal = reveal

        async def find_one(self, flt=None, *a, **k):
            doc = await super().find_one(flt, *a, **k)
            if doc and doc.get("status") == "applied":
                self.reveal.claim_read = True
            return doc

    reveal = RevealPayments()
    claims = ClaimWatch(reveal)
    claims.docs.append({
        "plan_id": PLAN_ID, "transaction_id": TXN, "status": "applied", "amount": 100,
    })
    plan_db["payments"] = reveal
    plan_db["payment_plan_payment_claims"] = claims
    _plan(plan_db)["balance_remaining"] = 700.0
    _plan(plan_db)["payments_made"] = 2
    _plan(plan_db)["total_paid"] = 300.0
    app = _pay_app(plan_db, monkeypatch)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        response = _post_pay(app)
    assert response.status_code == 409
    assert response.json()["error"] == "payment_needs_review"
    assert reveal.claim_read is True
    assert reveal.revealed is True
    plan = _plan(plan_db)
    assert plan["balance_remaining"] == 700.0
    assert plan["payments_made"] == 2 and plan["total_paid"] == 300.0
    assert claims.docs[0]["status"] == "applied"


def test_later_booking_enriches_blank_charge_or_conflicts(db, slack_alerts):
    """A booking that arrives later stamps the blank charge, or conflicts."""
    db["bond_cases"].docs.append(_bond(_id="bond-1"))
    first = _run(_record(db, kind="charge", booking=""))
    assert first["duplicate"] is False and first["committed"] is True
    assert _rows(db, "charge")[0]["needs_review_reason"] == "booking_not_provided"
    assert len(slack_alerts) == 1

    enriched = _run(_record(db, kind="charge", booking=BOOKING))
    assert enriched["committed"] is True and enriched["duplicate"] is False
    row = _rows(db, "charge")[0]
    assert row["booking_number"] == BOOKING
    assert "needs_review" not in row
    assert "needs_review_reason" not in row
    assert db["bond_cases"].docs[0]["last_transaction_id"] == TXN
    assert db["payment_log_claims"].docs[0]["booking_number"] == BOOKING
    audits = [doc for doc in db["audit_events"].docs if doc.get("reason") == "provider booking enriched"]
    assert len(audits) == 1
    assert audits[0]["old_booking_number"] == ""
    assert audits[0]["new_booking_number"] == BOOKING
    assert audits[0]["payment_review"]["old_needs_review"] is True
    assert audits[0]["payment_review"]["new_needs_review"] is False
    assert audits[0]["old_last_payment"]["last_transaction_id"] is None
    assert audits[0]["new_last_payment"]["last_transaction_id"] == TXN
    assert len(slack_alerts) == 1

    _run(_record(db, kind="charge", booking="", txn="TX-BLOCK"))
    _run(_record(db, kind="refund", booking=BOOKING, txn="TX-BLOCK"))
    with pytest.raises(plc.PaymentLogError) as raised:
        _run(_record(db, kind="charge", booking=BOOKING, txn="TX-BLOCK"))
    assert raised.value.status_code == 409
    assert raised.value.error == "payment_conflict"
    blocked = [item for item in _rows(db, "charge") if item["transaction_id"] == "TX-BLOCK"][0]
    assert blocked["booking_number"] == ""
    assert blocked["needs_review"] is True
    assert db["bond_cases"].docs[0]["last_transaction_id"] == TXN


def test_review_alert_includes_txn_key_and_resolved_booking(db, slack_alerts, monkeypatch):
    """Reversal alerts name the transaction and the booking the bond was flagged with."""
    db["bond_cases"].docs.append(_bond())
    db["payment_plans"].docs.append({"plan_id": PLAN_ID, "balance_remaining": 800.0})
    _run(_record(db, kind="charge", plan_id=PLAN_ID))
    assert slack_alerts == []
    _run(_record(db, kind="refund", booking=""))
    hit = slack_alerts[0]
    assert hit["kind"] == "refund"
    assert hit["txn_key"] == TXN
    assert hit["booking"] == BOOKING
    assert hit["plan_id"] == PLAN_ID
    captured = {}

    def notify_alert(self, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr("writers.slack_notifier.SlackNotifier.notify_alert", notify_alert)
    _real_notify(hit)
    assert TXN in captured["message"]
    assert BOOKING in captured["message"]
    assert captured["details"]["transaction_id"] == TXN
    assert captured["details"]["booking_number"] == BOOKING


def test_blank_booking_webhook_does_not_stamp(db, monkeypatch):
    db["bond_cases"].docs.append(_bond(last_payment_amount=7, last_transaction_id="KEEP"))
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("SWIPESIMPLE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("REQUIRE_DASHBOARD_PIN", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_LEADS", raising=False)
    monkeypatch.setattr("dashboard.extensions.get_db", lambda: db)

    app = FastAPI()
    app.include_router(webhooks_bp)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()), \
            patch("dashboard.services.bb_client.send_message_universal", new=AsyncMock()) as bb:
        transport = httpx.ASGITransport(app=app)
        async def go():
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                return await client.post("/api/webhooks/payment", json={
                    "event_type": "payment.completed",
                    "transaction_id": TXN,
                    "amount": 100,
                    "status": "approved",
                    "custom_fields": {},
                })
        response = _run(go())
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True and body["event_kind"] == "charge"
    charge = _rows(db, "charge")[0]
    assert charge["booking_number"] == ""
    assert charge["needs_review"] is True
    assert charge["needs_review_reason"] == "booking_not_provided"
    bond = db["bond_cases"].docs[0]
    assert bond["last_payment_amount"] == 7
    assert bond["last_transaction_id"] == "KEEP"
    assert bond.get("needs_review") is not True
    assert _audit(db, "charge")["stamped"] is False
    assert bb.await_count == 0
    assert db["payments"].index_calls == 0


def test_transactions_unavailable_writes_nothing(db, monkeypatch):
    db["bond_cases"].docs.append(_bond(last_payment_amount=3))

    async def _none():
        return None

    monkeypatch.setattr(plc, "_transaction_client", _none)
    with pytest.raises(plc.TransactionsUnavailable):
        _run(_record(db, kind="charge"))
    assert db["payments"].docs == []
    assert db["payment_log_claims"].docs == []
    assert db["payment_txn_locks"].docs == []
    assert db["audit_events"].docs == []
    assert db["bond_cases"].docs[0]["last_payment_amount"] == 3
    assert db["payments"].index_calls == 0
    assert db["payment_txn_locks"].index_calls == 0


def test_poller_marks_gmail_read_only_after_commit(db, monkeypatch):
    db["bond_cases"].docs.append(_bond())
    body = "Amount $100.00\nBooking: BK-1\nTransaction id: TX-POLL\n"
    read = []

    class Reader:
        is_configured = True

        def search_messages(self, query, max_results=20):
            return [{"message_id": "m1", "subject": "Receipt", "body": body}]

        def mark_as_read(self, mid):
            read.append(mid)

    monkeypatch.setattr(
        "dashboard.services.gmail_reader.GmailReaderService",
        lambda: Reader(),
    )
    result = _run(SwipeSimpleReceiptPoller(db=db).poll())
    assert result["processed"] == 1
    assert read == ["m1"]
    assert db["bond_cases"].docs[0]["last_transaction_id"] == "TX-POLL"
    assert db["bond_cases"].docs[0]["last_payment_amount"] == 100.0

    read.clear()

    async def _none():
        return None

    monkeypatch.setattr(plc, "_transaction_client", _none)
    plc._index_ready = False
    plc._lock_index_ready = False
    unread = _run(SwipeSimpleReceiptPoller(db=db).poll())
    assert unread["processed"] == 0
    assert unread["errors"] == 1
    assert read == []
