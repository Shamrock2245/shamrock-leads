"""Provider payment recording: one transaction for the poller and the webhook.

Offline. An in-memory Mongo double plus a session shim stands in for a
replica set. No SwipeSimple, Gmail, or Slack calls.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from dashboard.routers.webhooks import webhooks_bp
from dashboard.services import payment_log_claims as plc
from dashboard.services.swipesimple_receipt_poller import SwipeSimpleReceiptPoller
from dashboard.tenancy.context import bind_job_tenant
from dashboard.tenancy.scope import TenantScopedCollection
from tests.test_payment_plan_pay_dedupe import Coll, DB

SECRET = "swipe-test-secret-not-real"
_COLLS = (
    "payments", "payment_log_claims", "payment_txn_locks",
    "bond_cases", "active_bonds", "audit_events",
)


class _Admin:
    def __init__(self, hello):
        self._hello = hello

    async def command(self, name, *args, **kwargs):
        if name in ("hello", "ismaster"):
            return self._hello
        raise RuntimeError(name)


class TxnSession:
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
        attempts = 0
        while True:
            attempts += 1
            self.client.attempts += 1
            snap = {name: copy.deepcopy(coll.docs) for name, coll in self.client.db.items()}
            try:
                await callback(self)
                return
            except plc.RetryableTxnError:
                self._restore(snap)
                if attempts >= 4:
                    raise
                continue
            except Exception:
                self._restore(snap)
                raise


class TxnClient:
    def __init__(self, db):
        self.db = db
        self.attempts = 0
        self.admin = _Admin({"setName": "atlas-rs"})

    async def start_session(self):
        return TxnSession(self)


class BoomAudit(Coll):
    async def insert_one(self, doc, session=None, **kwargs):
        if str(doc.get("_id", "")).startswith("payment-log:"):
            raise RuntimeError("audit down")
        await super().insert_one(doc, session=session)


class FlakyAudit(Coll):
    def __init__(self):
        super().__init__()
        self.left = 1

    async def insert_one(self, doc, session=None, **kwargs):
        if str(doc.get("_id", "")).startswith("payment-log:") and self.left:
            self.left -= 1
            raise plc.RetryableTxnError()
        await super().insert_one(doc, session=session)


class FakeReader:
    is_configured = True

    def __init__(self, emails):
        self.emails = emails
        self.read: list[str] = []

    def search_messages(self, query, max_results=20):
        return list(self.emails)

    def mark_as_read(self, message_id):
        self.read.append(message_id)
        return True


class ScopedDB:
    def __init__(self, raw):
        self.raw = raw

    def __getitem__(self, name):
        return TenantScopedCollection(self.raw[name], name)


def _receipt(amount="100.00", txn="TX-1", booking="BK-1", message_id="msg-1"):
    lines = [f"Amount ${amount}"]
    if txn is not None:
        lines.append(f"Transaction ID: {txn}")
    if booking is not None:
        lines.append(f"Booking: {booking}")
    return {
        "message_id": message_id,
        "subject": "Payment",
        "body": "\n".join(lines),
    }


def _touch(db):
    for name in _COLLS:
        db[name]
    return db


def _bonds(db, booking="BK-1"):
    db["bond_cases"].docs.append({"booking_number": booking})
    db["active_bonds"].docs.append({"booking_number": booking})


@pytest.fixture
def rig(monkeypatch):
    db = _touch(DB())
    _bonds(db)
    plc._index_ready = False
    plc._lock_index_ready = False
    plc._txn_support_cache.clear()
    client = TxnClient(db)

    async def _txn():
        return client

    monkeypatch.setattr(plc, "_transaction_client", _txn)
    monkeypatch.delenv("SAAS_MULTI_TENANT", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_LEADS", raising=False)
    monkeypatch.setattr("dashboard.extensions.get_db", lambda: db)
    app = FastAPI()
    app.include_router(webhooks_bp)
    yield db, app, client
    plc._index_ready = False
    plc._lock_index_ready = False
    plc._txn_support_cache.clear()


def _webhook(app, body, *, raw=None):
    payload = raw if raw is not None else json.dumps(body).encode()
    sig = hmac.new(SECRET.encode(), payload, hashlib.sha256).hexdigest()

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.post(
                "/api/webhooks/payment",
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-SwipeSimple-Signature": sig,
                },
            )

    return asyncio.run(go())


def _poll(db, emails):
    reader = FakeReader(emails)

    async def go():
        with patch(
            "dashboard.services.gmail_reader.GmailReaderService",
            return_value=reader,
        ):
            return await SwipeSimpleReceiptPoller(db=db).poll()

    result = asyncio.run(go())
    return result, reader


def _pay(**extra):
    body = {
        "event_type": "payment.completed",
        "transaction_id": "TX-1",
        "amount": "100.00",
        "status": "approved",
        "custom_fields": {"booking_number": "BK-1"},
    }
    body.update(extra)
    return body


def test_webhook_commit_normalizes_the_txn_and_writes_once(rig, monkeypatch):
    db, app, client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    published = AsyncMock()
    with patch("dashboard.routers.events.publish_event", new=published):
        resp = _webhook(app, _pay(transaction_id="  TX-1  ", amount="100.10"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True and body["duplicate"] is False
    assert body["transaction_id"] == "TX-1"
    assert client.attempts == 1
    assert len(db["payments"].docs) == 1
    row = db["payments"].docs[0]
    assert row["transaction_id"] == "TX-1"
    assert row["amount_cents"] == 10010
    assert plc.amount_cents(row["amount"]) == 10010
    assert row["booking_number"] == "BK-1" and row["status"] == "approved"
    assert row["source"] == "swipesimple_webhook"
    for field in plc._TOKEN_FIELDS:
        assert field not in row
    assert db["payment_log_claims"].docs[0]["txn_key"] == "TX-1"
    assert db["payment_log_claims"].docs[0]["event_kind"] == "charge"
    assert db["payment_log_claims"].docs[0]["status"] == "recorded"
    assert db["payment_log_claims"].unique == [("tenant_id", "txn_key", "event_kind")]
    assert db["payment_txn_locks"].unique == [("tenant_id", "txn_key")]
    assert db["payment_txn_locks"].docs[0]["txn_key"] == "TX-1"
    assert db["payment_txn_locks"].docs[0]["version"] == 1
    assert db["payments"].index_calls == 0 and db["payments"].unique == []
    audit = db["audit_events"].docs[0]
    assert audit["_id"] == "payment-log:TX-1:charge"
    assert "tenant_id" not in audit
    bond_stamp = next(row for row in audit["stamp_transitions"] if row.get("bond_case_id"))
    assert bond_stamp["bond_case_id"] == "BK-1"
    assert bond_stamp["old_last_payment"]["last_transaction_id"] is None
    assert bond_stamp["new_last_payment"]["last_transaction_id"] == "TX-1"
    assert bond_stamp["new_last_payment"]["last_payment_status"] == "approved"
    assert audit["payload"]["stamp_transitions"] == audit["stamp_transitions"]
    for bond in (db["bond_cases"].docs[0], db["active_bonds"].docs[0]):
        assert bond["last_transaction_id"] == "TX-1"
        assert bond["last_payment_status"] == "approved"
        assert plc.amount_cents(bond["last_payment_amount"]) == 10010
    assert published.await_count == 1


def test_poller_commit_marks_read_only_after_commit(rig):
    db, app, client = rig
    result, reader = _poll(db, [_receipt(amount="40.00", txn="TX-POLL")])
    assert result["errors"] == 0 and result["processed"] == 1
    assert reader.read == ["msg-1"]
    assert client.attempts == 1
    row = db["payments"].docs[0]
    assert row["transaction_id"] == "TX-POLL" and row["source"] == "swipesimple_gmail_poll"
    assert row["amount_cents"] == 4000 and row["booking_number"] == "BK-1"
    assert db["audit_events"].docs[0]["_id"] == "payment-log:TX-POLL:charge"
    assert db["bond_cases"].docs[0]["last_transaction_id"] == "TX-POLL"
    assert db["payments"].index_calls == 0 and db["payments"].unique == []
    for field in plc._TOKEN_FIELDS:
        assert field not in row


def test_duplicate_replay_marks_the_email_read_and_does_not_write(rig, monkeypatch):
    db, app, client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    published = AsyncMock()
    with patch("dashboard.routers.events.publish_event", new=published):
        first = _webhook(app, _pay())
        again = _webhook(app, _pay())
    assert first.status_code == 200 and first.json()["duplicate"] is False
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert len(db["payments"].docs) == 1
    assert len(db["payment_log_claims"].docs) == 1
    assert len(db["audit_events"].docs) == 1
    assert published.await_count == 1

    result, reader = _poll(db, [_receipt()])
    assert result["processed"] == 1 and result["errors"] == 0
    assert reader.read == ["msg-1"]
    assert result["payments"][0]["duplicate"] is True
    assert len(db["payments"].docs) == 1
    assert len(db["audit_events"].docs) == 1
    assert client.attempts == 1


def test_settled_completed_row_is_a_duplicate_not_a_conflict(rig, monkeypatch):
    db, app, _client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    db["payments"].docs.append({
        "transaction_id": "TX-1",
        "amount": 100,
        "status": "completed",
        "booking_number": "BK-1",
        "plan_id": "plan-1",
    })
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        resp = _webhook(app, _pay())
    assert resp.status_code == 200 and resp.json()["duplicate"] is True
    assert len(db["payments"].docs) == 1
    assert db["payments"].docs[0]["status"] == "completed"
    assert db["payment_log_claims"].docs == []
    assert "last_payment_amount" not in db["bond_cases"].docs[0]


def test_cross_tenant_same_txn_both_commit(rig, monkeypatch):
    raw, _app, _client = rig
    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    plc._index_ready = False
    plc._lock_index_ready = False
    plc._txn_support_cache.clear()
    for tenant in ("gulf_coast_bail", "other_agency"):
        raw["bond_cases"].docs.append({"booking_number": "BK-1", "tenant_id": tenant})
        raw["active_bonds"].docs.append({"booking_number": "BK-1", "tenant_id": tenant})
    # The unscoped bond rows from the fixture are not this test's book.
    raw["bond_cases"].docs = [d for d in raw["bond_cases"].docs if d.get("tenant_id")]
    raw["active_bonds"].docs = [d for d in raw["active_bonds"].docs if d.get("tenant_id")]
    scoped = ScopedDB(raw)
    client = TxnClient(raw)

    async def _txn():
        return client

    monkeypatch.setattr(plc, "_transaction_client", _txn)

    async def once(tenant):
        with bind_job_tenant(tenant):
            return await plc.record_provider_payment(
                scoped,
                source="swipesimple_webhook",
                transaction_id="TX-SHARED",
                amount="25.00",
                status="approved",
                booking_number="BK-1",
                extra={"event_type": "payment.completed"},
            )

    async def go():
        first = await once("gulf_coast_bail")
        second = await once("other_agency")
        replay = await once("gulf_coast_bail")
        return first, second, replay

    first, second, replay = asyncio.run(go())
    assert first["duplicate"] is False and second["duplicate"] is False
    assert replay["duplicate"] is True
    assert {row["tenant_id"] for row in raw["payments"].docs} == {"gulf_coast_bail", "other_agency"}
    assert {row["txn_key"] for row in raw["payment_log_claims"].docs} == {"TX-SHARED"}
    assert {row["tenant_id"] for row in raw["payment_log_claims"].docs} == {
        "gulf_coast_bail", "other_agency",
    }
    assert {row["_id"] for row in raw["audit_events"].docs} == {
        "payment-log:gulf_coast_bail:TX-SHARED:charge",
        "payment-log:other_agency:TX-SHARED:charge",
    }
    stamped = {row["tenant_id"] for row in raw["bond_cases"].docs if row.get("last_transaction_id")}
    assert stamped == {"gulf_coast_bail", "other_agency"}
    assert len(raw["payments"].docs) == 2
    assert raw["payments"].index_calls == 0 and raw["payments"].unique == []
    assert raw["payment_log_claims"].unique == [("tenant_id", "txn_key", "event_kind")]
    assert raw["payment_txn_locks"].unique == [("tenant_id", "txn_key")]
    assert {row["tenant_id"] for row in raw["payment_txn_locks"].docs} == {
        "gulf_coast_bail", "other_agency",
    }
    assert {row["txn_key"] for row in raw["payment_txn_locks"].docs} == {"TX-SHARED"}


def test_crash_leaves_nothing_and_the_email_stays_unread(rig):
    db, _app, _client = rig
    db["audit_events"] = BoomAudit()
    result, reader = _poll(db, [_receipt(txn="TX-CRASH")])
    assert reader.read == []
    assert result["errors"] == 1 and result["processed"] == 0
    assert db["payments"].docs == []
    assert db["payment_log_claims"].docs == []
    assert db["payment_txn_locks"].docs == []
    assert db["audit_events"].docs == []
    assert "last_payment_amount" not in db["bond_cases"].docs[0]
    assert "last_payment_amount" not in db["active_bonds"].docs[0]


def test_transient_retry_commits_once_and_marks_read_once(rig):
    db, _app, client = rig
    db["audit_events"] = FlakyAudit()
    result, reader = _poll(db, [_receipt(txn="TX-RETRY")])
    assert result["errors"] == 0 and result["processed"] == 1
    assert reader.read == ["msg-1"]
    assert client.attempts == 2
    assert len(db["payments"].docs) == 1
    assert len(db["payment_log_claims"].docs) == 1
    assert len(db["audit_events"].docs) == 1
    assert db["bond_cases"].docs[0]["last_transaction_id"] == "TX-RETRY"
    assert db["payment_txn_locks"].docs[0]["version"] == 1


def test_transactions_unavailable_writes_nothing_and_leaves_email_unread(rig, monkeypatch):
    db, app, _client = rig

    async def _none():
        return None

    monkeypatch.setattr(plc, "_transaction_client", _none)
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    resp = _webhook(app, _pay())
    assert resp.status_code == 503
    assert resp.json()["error"] == "transactions_unavailable"
    result, reader = _poll(db, [_receipt()])
    assert reader.read == []
    assert result["errors"] == 1 and result["processed"] == 0
    assert db["payments"].docs == []
    assert db["payment_log_claims"].docs == []
    assert db["audit_events"].docs == []
    assert db["payment_log_claims"].index_calls == 0
    assert "last_payment_amount" not in db["bond_cases"].docs[0]


def test_blank_txn_is_400_and_the_email_stays_unread(rig, monkeypatch):
    db, app, _client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)

    def _db():
        raise AssertionError("db opened for a blank transaction id")

    monkeypatch.setattr("dashboard.extensions.get_db", _db)
    resp = _webhook(app, _pay(transaction_id="   "))
    assert resp.status_code == 400
    assert resp.json()["error"] == "transaction_id_required"
    monkeypatch.setattr("dashboard.extensions.get_db", lambda: db)
    result, reader = _poll(db, [_receipt(txn=None)])
    assert reader.read == []
    assert result["errors"] == 1 and result["processed"] == 0
    assert db["payments"].docs == []
    assert db["payment_log_claims"].docs == []


def test_fractional_cents_are_400(rig, monkeypatch):
    db, app, _client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    raw = (
        b'{"event_type":"payment.completed","transaction_id":"TX-1",'
        b'"amount":100.001,"status":"approved",'
        b'"custom_fields":{"booking_number":"BK-1"}}'
    )
    resp = _webhook(app, {}, raw=raw)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_amount"
    assert db["payments"].docs == []
    assert db["payment_log_claims"].docs == []
    assert db["payment_log_claims"].index_calls == 0


@pytest.mark.parametrize("event_type,status,kind,reason", [
    ("payment.refunded", "refunded", "refund", "provider_refund_unreversed"),
    ("payment.failed", "failed", "failed", "provider_failure_unreversed"),
])
def test_refund_or_failure_after_charge_flags_review_and_alerts_once(
    rig, monkeypatch, event_type, status, kind, reason,
):
    """A later refund or failure shares the txn id and does not move the plan balance."""
    db, app, _client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    db["payment_plans"].docs.append({
        "plan_id": "plan-1",
        "booking_number": "BK-1",
        "balance_remaining": 800.0,
        "total_paid": 200.0,
        "payments_made": 1,
        "status": "active",
    })
    alerts = []

    def fake_alert(self, title, message="", level="warning", details=None, source=None):
        alerts.append({
            "title": title, "message": message, "level": level,
            "details": details, "source": source,
        })
        return True

    monkeypatch.setattr("writers.slack_notifier.SlackNotifier.notify_alert", fake_alert)
    db["bond_cases"].docs[0]["bond_case_id"] = "case-1"
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        charge = _webhook(app, _pay())
        db["payments"].docs[0]["plan_id"] = "plan-1"
        follow = _webhook(app, _pay(event_type=event_type, status=status))
        replay = _webhook(app, _pay(event_type=event_type, status=status))

    assert charge.status_code == 200 and charge.json()["duplicate"] is False
    assert follow.status_code == 200 and follow.json()["duplicate"] is False
    assert replay.status_code == 200 and replay.json()["duplicate"] is True
    assert len(db["payments"].docs) == 2
    assert {row["event_kind"] for row in db["payments"].docs} == {"charge", kind}
    assert {row["event_kind"] for row in db["payment_log_claims"].docs} == {"charge", kind}
    charge_row = next(row for row in db["payments"].docs if row["event_kind"] == "charge")
    other = next(row for row in db["payments"].docs if row["event_kind"] == kind)
    assert charge_row["status"] == "approved" and other["status"] == status
    assert other["transaction_id"] == "TX-1"
    plan = db["payment_plans"].docs[0]
    assert plan["balance_remaining"] == 800.0
    assert plan["total_paid"] == 200.0 and plan["payments_made"] == 1
    assert plan["status"] == "active"
    assert plan["needs_review"] is True and plan["needs_review_reason"] == reason
    for bond in (db["bond_cases"].docs[0], db["active_bonds"].docs[0]):
        assert bond["needs_review"] is True and bond["needs_review_reason"] == reason
        assert bond["last_payment_status"] == status
    assert len(alerts) == 1
    details = alerts[0]["details"]
    assert details == {
        "event_kind": kind,
        "amount": "100.00",
        "booking_number": "BK-1",
        "plan_id": "plan-1",
    }
    blob = json.dumps(alerts[0]).lower()
    assert "customer" not in blob and "@" not in blob and "card" not in blob
    assert len(db["audit_events"].docs) == 2
    review_audit = next(row for row in db["audit_events"].docs if row.get("event_kind") == kind)
    bond_change = next(row for row in review_audit["review_transitions"] if row.get("bond_case_id"))
    plan_change = next(row for row in review_audit["review_transitions"] if row.get("plan_id"))
    assert bond_change["bond_case_id"] == "case-1"
    assert bond_change["old_needs_review"] is None and bond_change["old_review_reason"] is None
    assert bond_change["new_needs_review"] is True and bond_change["new_review_reason"] == reason
    assert plan_change["plan_id"] == "plan-1"
    assert plan_change["old_needs_review"] is None and plan_change["new_needs_review"] is True
    assert plan_change["new_review_reason"] == reason
    stamp = next(row for row in review_audit["stamp_transitions"] if row.get("bond_case_id"))
    assert stamp["bond_case_id"] == "case-1"
    assert stamp["old_last_payment"]["last_payment_status"] == "approved"
    assert stamp["old_last_payment"]["last_transaction_id"] == "TX-1"
    assert stamp["new_last_payment"]["last_payment_status"] == status
    assert stamp["new_last_payment"]["last_transaction_id"] == "TX-1"
    assert review_audit["payload"]["stamp_transitions"] == review_audit["stamp_transitions"]
    assert db["payments"].index_calls == 0 and db["payments"].unique == []


def test_staff_alert_failure_leaves_the_refund_committed(rig, monkeypatch):
    db, app, _client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    db["payment_plans"].docs.append({
        "plan_id": "plan-1",
        "booking_number": "BK-1",
        "balance_remaining": 800.0,
        "status": "active",
    })

    def boom(self, *args, **kwargs):
        raise RuntimeError("slack down")

    monkeypatch.setattr("writers.slack_notifier.SlackNotifier.notify_alert", boom)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        _webhook(app, _pay())
        db["payments"].docs[0]["plan_id"] = "plan-1"
        refund = _webhook(app, _pay(event_type="payment.refunded", status="refunded"))
    assert refund.status_code == 200 and refund.json()["duplicate"] is False
    assert len(db["payments"].docs) == 2
    assert db["payment_plans"].docs[0]["balance_remaining"] == 800.0
    assert db["payment_plans"].docs[0]["needs_review"] is True


def _capture_alerts(monkeypatch):
    alerts = []

    def fake_alert(self, title, message="", level="warning", details=None, source=None):
        alerts.append({
            "title": title, "message": message, "level": level,
            "details": details, "source": source,
        })
        return True

    monkeypatch.setattr("writers.slack_notifier.SlackNotifier.notify_alert", fake_alert)
    return alerts


def _plans(db):
    db["payment_plans"].docs.extend([
        {
            "plan_id": "plan-1",
            "booking_number": "BK-1",
            "balance_remaining": 800.0,
            "status": "active",
        },
        {
            "plan_id": "plan-other",
            "booking_number": "OTHER-BK",
            "balance_remaining": 500.0,
            "status": "active",
        },
    ])
    db["bond_cases"].docs.append({"booking_number": "OTHER-BK"})
    db["active_bonds"].docs.append({"booking_number": "OTHER-BK"})


@pytest.mark.parametrize("event_type,status", [
    ("payment.refunded", "refunded"),
    ("payment.failed", "failed"),
])
def test_refund_booking_conflict_writes_nothing_and_does_not_alert(
    rig, monkeypatch, event_type, status,
):
    db, app, _client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    _plans(db)
    db["payments"].docs.append({
        "transaction_id": "TX-1",
        "event_kind": "charge",
        "booking_number": "BK-1",
        "amount": 100,
        "status": "approved",
        "plan_id": "plan-other",
    })
    before = copy.deepcopy(db["payments"].docs)
    alerts = _capture_alerts(monkeypatch)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()) as published:
        resp = _webhook(app, _pay(
            event_type=event_type,
            status=status,
            custom_fields={"booking_number": "OTHER-BK"},
        ))
    assert resp.status_code == 409
    assert resp.json()["error"] == "payment_needs_review"
    assert published.await_count == 0
    assert alerts == []
    assert db["payments"].docs == before
    assert db["payment_log_claims"].docs == []
    assert db["audit_events"].docs == []
    assert db["payment_log_claims"].index_calls == 0
    assert all(not plan.get("needs_review") for plan in db["payment_plans"].docs)
    assert all(plan["balance_remaining"] in (800.0, 500.0) for plan in db["payment_plans"].docs)
    for coll in ("bond_cases", "active_bonds"):
        for bond in db[coll].docs:
            assert not bond.get("needs_review")
            assert "last_payment_amount" not in bond


@pytest.mark.parametrize("event_booking", ["", "BK-1"])
def test_ambiguous_sibling_bookings_are_409(rig, monkeypatch, event_booking):
    db, app, _client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    _plans(db)
    db["payments"].docs.extend([
        {
            "transaction_id": "TX-1",
            "event_kind": "charge",
            "booking_number": "BK-1",
            "amount": 100,
            "status": "approved",
        },
        {
            "transaction_id": "TX-1",
            "event_kind": "charge",
            "booking_number": "BK-2",
            "amount": 100,
            "status": "approved",
        },
    ])
    before = copy.deepcopy(db["payments"].docs)
    alerts = _capture_alerts(monkeypatch)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        resp = _webhook(app, _pay(
            event_type="payment.refunded",
            status="refunded",
            custom_fields={"booking_number": event_booking},
        ))
    assert resp.status_code == 409
    assert resp.json()["error"] == "payment_needs_review"
    assert alerts == []
    assert db["payments"].docs == before
    assert db["payment_log_claims"].docs == []
    assert db["audit_events"].docs == []
    assert db["payment_log_claims"].index_calls == 0
    assert all(not plan.get("needs_review") for plan in db["payment_plans"].docs)


def test_blank_refund_booking_inherits_the_single_sibling(rig, monkeypatch):
    db, app, _client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    _plans(db)
    alerts = _capture_alerts(monkeypatch)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        charge = _webhook(app, _pay())
        db["payments"].docs[0]["plan_id"] = "plan-1"
        refund = _webhook(app, _pay(
            event_type="payment.refunded",
            status="refunded",
            custom_fields={"booking_number": ""},
        ))
    assert charge.status_code == 200 and charge.json()["duplicate"] is False
    assert refund.status_code == 200 and refund.json()["duplicate"] is False
    refund_row = next(row for row in db["payments"].docs if row["event_kind"] == "refund")
    assert refund_row["booking_number"] == "BK-1"
    plans = {plan["plan_id"]: plan for plan in db["payment_plans"].docs}
    assert plans["plan-1"]["needs_review"] is True
    assert plans["plan-1"]["needs_review_reason"] == "provider_refund_unreversed"
    assert plans["plan-1"]["balance_remaining"] == 800.0
    assert "needs_review" not in plans["plan-other"]
    assert plans["plan-other"]["balance_remaining"] == 500.0
    assert len(alerts) == 1
    assert alerts[0]["details"]["booking_number"] == "BK-1"
    assert alerts[0]["details"]["plan_id"] == "plan-1"
    other_bond = next(row for row in db["bond_cases"].docs if row["booking_number"] == "OTHER-BK")
    assert "needs_review" not in other_bond
    agreed = next(row for row in db["bond_cases"].docs if row["booking_number"] == "BK-1")
    assert agreed["needs_review"] is True


def test_two_plans_on_one_booking_flags_only_the_originating_plan(rig, monkeypatch):
    db, app, _client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    db["payment_plans"].docs.extend([
        {"plan_id": "plan-1", "booking_number": "BK-1", "balance_remaining": 800.0, "status": "active"},
        {"plan_id": "plan-2", "booking_number": "BK-1", "balance_remaining": 400.0, "status": "active"},
    ])
    db["bond_cases"].docs[0]["bond_case_id"] = "case-1"
    alerts = _capture_alerts(monkeypatch)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        charge = _webhook(app, _pay())
        db["payments"].docs[0]["plan_id"] = "plan-1"
        refund = _webhook(app, _pay(event_type="payment.refunded", status="refunded"))
    assert charge.status_code == 200
    assert refund.status_code == 200 and refund.json()["duplicate"] is False
    plans = {plan["plan_id"]: plan for plan in db["payment_plans"].docs}
    assert plans["plan-1"]["needs_review"] is True
    assert plans["plan-1"]["needs_review_reason"] == "provider_refund_unreversed"
    assert plans["plan-1"]["balance_remaining"] == 800.0
    assert "needs_review" not in plans["plan-2"]
    assert plans["plan-2"]["balance_remaining"] == 400.0
    assert db["bond_cases"].docs[0]["needs_review"] is True
    assert len(alerts) == 1
    assert alerts[0]["details"]["plan_id"] == "plan-1"
    assert alerts[0]["details"]["booking_number"] == "BK-1"
    review_audit = next(row for row in db["audit_events"].docs if row.get("event_kind") == "refund")
    bond_change = next(row for row in review_audit["review_transitions"] if row.get("bond_case_id"))
    plan_change = next(row for row in review_audit["review_transitions"] if row.get("plan_id"))
    assert bond_change["bond_case_id"] == "case-1"
    assert bond_change["old_needs_review"] is None and bond_change["old_review_reason"] is None
    assert bond_change["new_needs_review"] is True
    assert bond_change["new_review_reason"] == "provider_refund_unreversed"
    assert plan_change["plan_id"] == "plan-1"
    assert plan_change["old_needs_review"] is None and plan_change["old_review_reason"] is None
    assert plan_change["new_needs_review"] is True
    assert plan_change["new_review_reason"] == "provider_refund_unreversed"
    assert review_audit["payload"]["review_transitions"] == review_audit["review_transitions"]


def test_refund_without_a_plan_id_flags_only_the_bond(rig, monkeypatch):
    db, app, _client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    db["payment_plans"].docs.extend([
        {"plan_id": "plan-1", "booking_number": "BK-1", "balance_remaining": 800.0, "status": "active"},
        {"plan_id": "plan-2", "booking_number": "BK-1", "balance_remaining": 400.0, "status": "active"},
    ])
    db["bond_cases"].docs[0]["bond_case_id"] = "case-1"
    alerts = _capture_alerts(monkeypatch)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        _webhook(app, _pay())
        refund = _webhook(app, _pay(event_type="payment.refunded", status="refunded"))
    assert refund.status_code == 200 and refund.json()["duplicate"] is False
    assert db["bond_cases"].docs[0]["needs_review"] is True
    assert all("needs_review" not in plan for plan in db["payment_plans"].docs)
    assert {plan["balance_remaining"] for plan in db["payment_plans"].docs} == {800.0, 400.0}
    assert len(alerts) == 1
    assert alerts[0]["details"]["plan_id"] == "none"
    review_audit = next(row for row in db["audit_events"].docs if row.get("event_kind") == "refund")
    assert [row.get("plan_id") for row in review_audit["review_transitions"] if row.get("plan_id")] == []
    bond_change = next(row for row in review_audit["review_transitions"] if row.get("bond_case_id"))
    assert bond_change["bond_case_id"] == "case-1"
    assert bond_change["old_needs_review"] is None and bond_change["new_needs_review"] is True
    assert bond_change["old_review_reason"] is None
    assert bond_change["new_review_reason"] == "provider_refund_unreversed"


def test_conflicting_sibling_plan_ids_are_409(rig, monkeypatch):
    db, app, _client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    db["payment_plans"].docs.extend([
        {"plan_id": "plan-1", "booking_number": "BK-1", "balance_remaining": 800.0, "status": "active"},
        {"plan_id": "plan-2", "booking_number": "BK-1", "balance_remaining": 400.0, "status": "active"},
    ])
    db["payments"].docs.extend([
        {
            "transaction_id": "TX-1",
            "event_kind": "charge",
            "booking_number": "BK-1",
            "amount": 100,
            "status": "approved",
            "plan_id": "plan-1",
        },
        {
            "transaction_id": "TX-1",
            "event_kind": "charge",
            "booking_number": "BK-1",
            "amount": 100,
            "status": "approved",
            "plan_id": "plan-2",
        },
    ])
    before = copy.deepcopy(db["payments"].docs)
    alerts = _capture_alerts(monkeypatch)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        resp = _webhook(app, _pay(event_type="payment.refunded", status="refunded"))
    assert resp.status_code == 409
    assert resp.json()["error"] == "payment_needs_review"
    assert alerts == []
    assert db["payments"].docs == before
    assert db["payment_log_claims"].docs == []
    assert db["audit_events"].docs == []
    assert db["payment_log_claims"].index_calls == 0
    assert all("needs_review" not in plan for plan in db["payment_plans"].docs)
    assert "needs_review" not in db["bond_cases"].docs[0]


@pytest.mark.parametrize("seed,payload", [
    ({"amount": 50, "status": "approved", "booking_number": "BK-1"}, _pay(amount="100.00")),
    ({"amount": 100, "status": "approved", "booking_number": "OTHER-BK"}, _pay()),
])
def test_conflicting_payload_is_409_and_leaves_the_email_unread(rig, monkeypatch, seed, payload):
    db, app, _client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    db["payments"].docs.append({"transaction_id": "TX-1", **seed})
    before = copy.deepcopy(db["payments"].docs[0])
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()) as published:
        resp = _webhook(app, payload)
    assert resp.status_code == 409
    assert resp.json()["error"] == "payment_conflict"
    assert published.await_count == 0
    assert db["payments"].docs == [before]
    assert db["payment_log_claims"].docs == []
    assert db["audit_events"].docs == []
    assert "last_payment_amount" not in db["bond_cases"].docs[0]

    result, reader = _poll(db, [_receipt()])
    assert reader.read == []
    assert result["errors"] == 1 and result["processed"] == 0
    assert db["payments"].docs == [before]


class ConflictOnce(Coll):
    """The first lock upsert is a write conflict. Later upserts commit."""

    def __init__(self, exc_type):
        super().__init__()
        self.exc_type = exc_type
        self.raised = False

    async def update_one(self, flt, update, upsert=False, session=None, **kwargs):
        if not self.raised and upsert and "version" in (update.get("$inc") or {}):
            self.raised = True
            raise self.exc_type()
        await super().update_one(flt, update, upsert=upsert, session=session, **kwargs)


def test_refund_charge_write_conflict_retries_and_flags_the_booking(rig, monkeypatch):
    """A blank refund that overlaps the charge retries and inherits that booking."""
    db, app, _client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    db["payment_plans"].docs.extend([
        {"plan_id": "plan-1", "booking_number": "BK-1", "balance_remaining": 800.0, "status": "active"},
        {"plan_id": "plan-2", "booking_number": "BK-1", "balance_remaining": 400.0, "status": "active"},
    ])
    db["bond_cases"].docs[0]["bond_case_id"] = "case-1"
    db["active_bonds"].docs[0]["bond_case_id"] = "active-1"
    db["payment_txn_locks"] = ConflictOnce(plc.RetryableTxnError)

    async def commit_charge():
        plain = TxnClient(db)

        async def _plain():
            return plain

        previous = plc._transaction_client
        plc._transaction_client = _plain
        try:
            return await plc.record_provider_payment(
                db,
                source="swipesimple_webhook",
                transaction_id="TX-1",
                amount="100.00",
                status="approved",
                booking_number="BK-1",
                extra={"event_type": "payment.completed", "plan_id": "plan-1"},
            )
        finally:
            plc._transaction_client = previous

    class RaceSession(TxnSession):
        def __init__(self, client):
            super().__init__(client)
            self.injected = False

        async def with_transaction(self, callback):
            attempts = 0
            while True:
                attempts += 1
                self.client.attempts += 1
                snap = {name: copy.deepcopy(coll.docs) for name, coll in self.client.db.items()}
                try:
                    await callback(self)
                    return
                except plc.RetryableTxnError:
                    self._restore(snap)
                    if not self.injected:
                        self.injected = True
                        charged = await commit_charge()
                        assert charged["duplicate"] is False
                    if attempts >= 4:
                        raise
                    continue
                except Exception:
                    self._restore(snap)
                    raise

    class RaceClient(TxnClient):
        async def start_session(self):
            return RaceSession(self)

    race = RaceClient(db)

    async def _race():
        return race

    monkeypatch.setattr(plc, "_transaction_client", _race)
    alerts = _capture_alerts(monkeypatch)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        refund = _webhook(app, _pay(
            event_type="payment.refunded",
            status="refunded",
            custom_fields={"booking_number": ""},
        ))
    assert refund.status_code == 200 and refund.json()["duplicate"] is False
    assert race.attempts == 2
    refund_row = next(row for row in db["payments"].docs if row["event_kind"] == "refund")
    charge_row = next(row for row in db["payments"].docs if row["event_kind"] == "charge")
    assert charge_row["booking_number"] == "BK-1" and charge_row["plan_id"] == "plan-1"
    assert refund_row["booking_number"] == "BK-1"
    plans = {plan["plan_id"]: plan for plan in db["payment_plans"].docs}
    assert plans["plan-1"]["needs_review"] is True
    assert plans["plan-1"]["balance_remaining"] == 800.0
    assert "needs_review" not in plans["plan-2"]
    assert db["bond_cases"].docs[0]["needs_review"] is True
    assert db["bond_cases"].docs[0]["last_payment_status"] == "refunded"
    assert len(alerts) == 1 and alerts[0]["details"]["plan_id"] == "plan-1"
    assert alerts[0]["details"]["booking_number"] == "BK-1"
    review_audit = next(row for row in db["audit_events"].docs if row["event_kind"] == "refund")
    stamp = next(row for row in review_audit["stamp_transitions"] if row.get("bond_case_id"))
    active = next(row for row in review_audit["stamp_transitions"] if row.get("active_bond_id"))
    assert stamp["bond_case_id"] == "case-1"
    assert stamp["old_last_payment"]["last_payment_status"] == "approved"
    assert stamp["old_last_payment"]["last_transaction_id"] == "TX-1"
    assert stamp["new_last_payment"]["last_payment_status"] == "refunded"
    assert stamp["new_last_payment"]["last_transaction_id"] == "TX-1"
    assert active["active_bond_id"] == "active-1"
    assert active["old_last_payment"]["last_payment_status"] == "approved"
    assert active["new_last_payment"]["last_payment_status"] == "refunded"
    assert review_audit["payload"]["stamp_transitions"] == review_audit["stamp_transitions"]
    assert db["payment_txn_locks"].docs[0]["version"] == 2
    assert db["payment_txn_locks"].unique == [("tenant_id", "txn_key")]
    assert db["payments"].index_calls == 0 and db["payments"].unique == []


def test_refund_pay_write_conflict_retries_and_refuses(rig, monkeypatch):
    """/pay that overlaps a refund retries, then refuses without moving the balance."""
    from dashboard.routers import payment_plans as pp

    db, _app, _client = rig
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    db["payment_plans"].docs.append({
        "plan_id": "plan-1",
        "booking_number": "BK-1",
        "balance_remaining": 800.0,
        "total_paid": 200.0,
        "payments_made": 1,
        "status": "active",
        "frequency": "weekly",
    })
    db["bond_cases"].docs[0]["bond_case_id"] = "case-1"
    db["payment_txn_locks"] = ConflictOnce(pp.RetryableTxnError)
    pp._inflight_txns.clear()
    pp._txn_support_cache.clear()
    pp._payment_claim_index_ready = False
    plc._lock_index_ready = False
    plc._index_ready = False
    monkeypatch.setattr(pp, "get_collection", lambda name: db[name])

    async def commit_refund():
        return await plc.record_provider_payment(
            db,
            source="swipesimple_webhook",
            transaction_id="TX-1",
            amount="100.00",
            status="refunded",
            booking_number="BK-1",
            extra={"event_type": "payment.refunded"},
        )

    class RaceSession(TxnSession):
        def __init__(self, client):
            super().__init__(client)
            self.injected = False

        async def with_transaction(self, callback):
            attempts = 0
            while True:
                attempts += 1
                self.client.attempts += 1
                snap = {name: copy.deepcopy(coll.docs) for name, coll in self.client.db.items()}
                try:
                    await callback(self)
                    return
                except pp.RetryableTxnError:
                    self._restore(snap)
                    if not self.injected:
                        self.injected = True
                        refunded = await commit_refund()
                        assert refunded["duplicate"] is False
                    if attempts >= 4:
                        raise
                    continue
                except Exception:
                    self._restore(snap)
                    raise

    class RaceClient(TxnClient):
        async def start_session(self):
            return RaceSession(self)

    race = RaceClient(db)

    async def _race():
        return race

    monkeypatch.setattr(pp, "_txn_client", _race)
    pay_app = FastAPI()
    pay_app.include_router(pp.payment_plans_bp)
    alerts = _capture_alerts(monkeypatch)

    async def go():
        transport = httpx.ASGITransport(app=pay_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            return await http.post(
                "/api/payments/plans/plan-1/pay",
                json={"amount": "100.00", "transaction_id": "TX-1", "method": "Card"},
            )

    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        resp = asyncio.run(go())
    assert resp.status_code == 409
    assert resp.json()["error"] == "payment_needs_review"
    assert race.attempts == 2
    plan = db["payment_plans"].docs[0]
    assert plan["balance_remaining"] == 800.0
    assert plan["total_paid"] == 200.0 and plan["payments_made"] == 1
    assert "needs_review" not in plan
    assert len(db["payments"].docs) == 1
    assert db["payments"].docs[0]["event_kind"] == "refund"
    assert db["payments"].docs[0]["booking_number"] == "BK-1"
    assert db["bond_cases"].docs[0]["needs_review"] is True
    assert db["bond_cases"].docs[0]["bond_case_id"] == "case-1"
    claim = db["payment_plan_payment_claims"].docs[0]
    assert claim["status"] == "failed"
    review_audit = db["audit_events"].docs[0]
    stamp = next(row for row in review_audit["stamp_transitions"] if row.get("bond_case_id"))
    assert stamp["bond_case_id"] == "case-1"
    assert stamp["old_last_payment"]["last_transaction_id"] is None
    assert stamp["new_last_payment"]["last_payment_status"] == "refunded"
    assert stamp["new_last_payment"]["last_transaction_id"] == "TX-1"
    assert review_audit["payload"]["stamp_transitions"] == review_audit["stamp_transitions"]
    assert len(alerts) == 1
    assert db["payment_txn_locks"].docs[0]["version"] == 1
    assert db["payments"].index_calls == 0 and db["payments"].unique == []
    pp._inflight_txns.clear()
