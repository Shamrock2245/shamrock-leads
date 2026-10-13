"""POST /api/payments/plans/{plan_id}/pay links a committed logged row, or inserts one.

Offline: the in-memory Mongo double from the dedupe tests, plus a session shim
that commits or rolls back a multi-document transaction. No network, no
SwipeSimple, Gmail, or Slack.
"""
from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from dashboard.routers import payment_plans as pp
from tests.test_payment_plan_pay_dedupe import PLAN_ID, Coll, DB, _plan


def _app(db, monkeypatch):
    pp._inflight_txns.clear()
    pp._txn_support_cache.clear()

    async def _no_txn():
        return None

    monkeypatch.setattr(pp, "get_collection", lambda name: db[name])
    monkeypatch.setattr(pp, "_payment_claim_index_ready", False)
    monkeypatch.setattr(pp, "_txn_client", _no_txn)
    app = FastAPI()
    app.include_router(pp.payment_plans_bp)
    return app


@pytest.fixture
def rig(monkeypatch):
    db = DB()
    db["payment_plans"].docs.append({
        "plan_id": PLAN_ID, "booking_number": "TEST-PP-1", "total_premium": 1000.0,
        "down_payment": 200.0, "balance_remaining": 800.0, "installment_amount": 100.0,
        "frequency": "weekly", "payments_made": 1, "total_paid": 200.0, "status": "active",
    })
    app = _app(db, monkeypatch)
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        yield db, app
    pp._inflight_txns.clear()


def _post(app, body, plan_id=PLAN_ID):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.post(f"/api/payments/plans/{plan_id}/pay", json=body)
    return asyncio.run(go())


def _logged(**extra):
    row = {
        "transaction_id": "TX-LOG",
        "amount": 100,
        "status": "approved",
        "booking_number": "TEST-PP-1",
        "source": "swipesimple_gmail_poll",
    }
    row.update(extra)
    return row


class _Admin:
    def __init__(self, hello):
        self._hello = hello

    async def command(self, name, *args, **kwargs):
        if name in ("hello", "ismaster"):
            return self._hello
        raise RuntimeError(name)


class _Client:
    def __init__(self, hello):
        self.admin = _Admin(hello)


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
        while True:
            snap = {name: copy.deepcopy(coll.docs) for name, coll in self.client.db.items()}
            self.client.attempts += 1
            try:
                await callback(self)
                return
            except pp.RetryableTxnError:
                self._restore(snap)
                if self.client.attempts >= 4:
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


class FlakyAudit(Coll):
    def __init__(self, remaining, exc):
        super().__init__()
        self.remaining = remaining
        self.exc = exc

    async def insert_one(self, doc, session=None, **kwargs):
        if doc.get("event_type") == pp._AUDIT_EVENT and self.remaining:
            self.remaining -= 1
            raise self.exc
        await super().insert_one(doc, session=session)


def test_transaction_support_detects_replica_set_and_atlas():
    pp._txn_support_cache.clear()
    assert asyncio.run(pp._supports_transactions(_Client({"setName": "atlas-shard-0"}))) is True
    pp._txn_support_cache.clear()
    assert asyncio.run(pp._supports_transactions(_Client({"msg": "isdbgrid"}))) is True
    pp._txn_support_cache.clear()
    assert asyncio.run(pp._supports_transactions(_Client({"ismaster": True, "ok": 1}))) is False
    pp._txn_support_cache.clear()


def test_link_happy_path_counts_once_and_creates_no_payments_index(rig):
    db, app = rig
    db["payments"].docs.append(_logged())
    r = _post(app, {"amount": "100.00", "transaction_id": "TX-LOG"})
    assert r.status_code == 200 and r.json()["idempotent"] is False
    assert _plan(db)["balance_remaining"] == 700.0
    assert _plan(db)["total_paid"] == 300.0 and _plan(db)["payments_made"] == 2
    assert len(db["payments"].docs) == 1
    row = db["payments"].docs[0]
    assert row["plan_id"] == PLAN_ID
    assert row["status"] == "completed" and row["logged_status"] == "approved"
    assert row["type"] == "installment" and row.get("timestamp")
    for field in pp._TOKEN_FIELDS:
        assert field not in row
    assert db["payment_plan_payment_claims"].docs[0]["status"] == "applied"
    assert len(db["audit_events"].docs) == 1
    audit = db["audit_events"].docs[0]
    assert audit["event_type"] == pp._AUDIT_EVENT
    assert audit["old_state"] == {"balance_remaining": 800.0, "status": "active"}
    assert audit["new_state"] == {"balance_remaining": 700.0, "status": "active"}
    assert db["payments"].index_calls == 0 and db["payments"].unique == []
    assert pp._last_apply_path == "fallback"
    again = _post(app, {"amount": 100, "transaction_id": "TX-LOG"})
    assert again.status_code == 200 and again.json()["idempotent"] is True
    assert len(db["payments"].docs) == 1 and _plan(db)["balance_remaining"] == 700.0


def test_link_paid_in_full(rig):
    db, app = rig
    db["payments"].docs.append(_logged(amount=900, transaction_id="TX-FULL"))
    r = _post(app, {"amount": 900, "transaction_id": "TX-FULL"})
    assert r.status_code == 200
    assert r.json()["status"] == "paid_in_full" and r.json()["balance_remaining"] == 0
    assert _plan(db)["status"] == "paid_in_full" and _plan(db)["balance_remaining"] == 0
    assert len(db["payments"].docs) == 1
    audit = db["audit_events"].docs[0]
    assert audit["old_state"] == {"balance_remaining": 800.0, "status": "active"}
    assert audit["new_state"] == {"balance_remaining": 0, "status": "paid_in_full"}


def test_insert_when_no_logged_row_has_no_token_fields(rig):
    db, app = rig
    r = _post(app, {"amount": 100, "transaction_id": "TX-NEW", "method": "Cash"})
    assert r.status_code == 200 and r.json()["idempotent"] is False
    row = db["payments"].docs[0]
    assert row["plan_id"] == PLAN_ID and row["status"] == "completed"
    assert row["booking_number"] == "TEST-PP-1"
    for field in pp._TOKEN_FIELDS:
        assert field not in row
    assert db["audit_events"].docs[0]["linked_existing_row"] is False


@pytest.mark.parametrize("extra,error", [
    ({"log_claim_token": "tok"}, "logged_payment_uncommitted"),
    ({"claim_token": "tok"}, "logged_payment_uncommitted"),
    ({"payment_claim_token": "tok"}, "logged_payment_uncommitted"),
    ({"status": "declined"}, "logged_payment_not_settled"),
    ({"status": "refunded"}, "logged_payment_not_settled"),
    ({"status": "pending"}, "logged_payment_not_settled"),
    ({"status": ""}, "logged_payment_not_settled"),
    ({"raw_payload": {"event_type": "payment.refunded"}}, "logged_payment_not_settled"),
    ({"amount": 50}, "logged_payment_amount_mismatch"),
    ({"amount": 100.004}, "logged_payment_amount_mismatch"),
    ({"booking_number": "OTHER-BK"}, "logged_payment_booking_mismatch"),
    ({"booking_number": ""}, "logged_payment_booking_mismatch"),
    ({"booking_number": "   "}, "logged_payment_booking_mismatch"),
])
def test_link_refusals_do_not_move_the_balance(rig, extra, error):
    db, app = rig
    db["payments"].docs.append(_logged(**extra))
    r = _post(app, {"amount": 100, "transaction_id": "TX-LOG"})
    assert r.status_code == 409 and r.json()["error"] == error
    assert r.json().get("success") is not True
    assert _plan(db)["balance_remaining"] == 800.0
    assert db["payments"].docs[0].get("plan_id") in (None, "")
    assert db["payment_plan_payment_claims"].docs == []
    assert db["audit_events"].docs == []


def test_blank_plan_booking_is_refused(rig):
    db, app = rig
    _plan(db)["booking_number"] = ""
    db["payments"].docs.append(_logged(booking_number=""))
    r = _post(app, {"amount": 100, "transaction_id": "TX-LOG"})
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_booking_mismatch"
    assert _plan(db)["balance_remaining"] == 800.0
    assert db["payment_plan_payment_claims"].docs == []


def test_two_logged_rows_are_ambiguous(rig):
    db, app = rig
    db["payments"].docs.append(_logged())
    db["payments"].docs.append(_logged(status="approved"))
    r = _post(app, {"amount": 100, "transaction_id": "TX-LOG"})
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_ambiguous"
    assert _plan(db)["balance_remaining"] == 800.0
    assert all(not row.get("plan_id") for row in db["payments"].docs)
    assert db["payment_plan_payment_claims"].docs == []


def test_token_row_beside_a_committed_row_is_ambiguous(rig):
    db, app = rig
    db["payments"].docs.append(_logged())
    db["payments"].docs.append(_logged(log_claim_token="tok"))
    r = _post(app, {"amount": 100, "transaction_id": "TX-LOG"})
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_ambiguous"
    assert _plan(db)["balance_remaining"] == 800.0


def test_settled_event_type_still_links(rig):
    db, app = rig
    db["payments"].docs.append(_logged(raw_payload={"event_type": "payment.approved"}))
    r = _post(app, {"amount": 100, "transaction_id": "TX-LOG"})
    assert r.status_code == 200
    assert db["payments"].docs[0]["plan_id"] == PLAN_ID
    assert _plan(db)["balance_remaining"] == 700.0


def test_pin_miss_returns_409_without_a_balance_change(rig):
    db, app = rig
    db["payments"].docs.append(_logged())
    payments = db["payments"]
    original = payments.update_one

    async def wrapped(flt, update, upsert=False, session=None, **kwargs):
        if update.get("$set", {}).get("plan_id") == PLAN_ID and flt.get("status") == "approved":
            payments.docs[0]["status"] = "refunded"
        return await original(flt, update, upsert=upsert, session=session)

    payments.update_one = wrapped
    r = _post(app, {"amount": 100, "transaction_id": "TX-LOG"})
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_link_lost"
    assert _plan(db)["balance_remaining"] == 800.0
    assert "collected_transaction_ids" not in _plan(db)
    assert db["payments"].docs[0].get("plan_id") in (None, "")
    assert db["audit_events"].docs == []


def test_claimed_amount_mismatch_does_not_resume(rig):
    db, app = rig
    db["payment_plan_payment_claims"].docs.append({
        "plan_id": PLAN_ID, "transaction_id": "TX-A", "status": "claimed", "amount": 100,
    })
    r = _post(app, {"amount": 40, "transaction_id": "TX-A"})
    assert r.status_code == 409 and r.json()["error"] == "transaction_id_amount_mismatch"
    assert db["payments"].docs == [] and _plan(db)["balance_remaining"] == 800.0


def test_live_inflight_claim_is_not_resumed(rig):
    db, app = rig
    db["payment_plan_payment_claims"].docs.append({
        "plan_id": PLAN_ID, "transaction_id": "TX-LIVE", "status": "claimed", "amount": 100,
    })
    pp._inflight_txns.add("TX-LIVE")
    r = _post(app, {"amount": 100, "transaction_id": "TX-LIVE"})
    assert r.status_code == 409 and r.json()["error"] == "payment_in_progress"
    assert db["payments"].docs == [] and _plan(db)["balance_remaining"] == 800.0


def _seed_claim(db, step, **extra):
    doc = {
        "plan_id": PLAN_ID, "transaction_id": "TX-STEP", "status": "claimed",
        "amount": 100, "step": step, "booking_number": "TEST-PP-1",
    }
    if step in ("balance_applied", "audited"):
        doc.update({
            "balance_before": 800.0, "status_before": "active",
            "balance_after": 700.0, "status_after": "active",
        })
    doc.update(extra)
    db["payment_plan_payment_claims"].docs.append(doc)


def _seed_linked_row(db):
    db["payments"].docs.append({
        "transaction_id": "TX-STEP", "plan_id": PLAN_ID, "amount": 100,
        "status": "completed", "logged_status": "approved", "booking_number": "TEST-PP-1",
        "type": "installment", "timestamp": "2026-10-13T00:00:00+00:00",
    })


@pytest.mark.parametrize("step", ["row_linked", "balance_applied", "audited"])
def test_fallback_resumes_after_each_checkpoint(rig, step):
    db, app = rig
    _seed_linked_row(db)
    _seed_claim(db, step)
    if step in ("balance_applied", "audited"):
        _plan(db)["balance_remaining"] = 700.0
        _plan(db)["total_paid"] = 300.0
        _plan(db)["payments_made"] = 2
        _plan(db)["collected_transaction_ids"] = ["TX-STEP"]
    if step == "audited":
        db["audit_events"].docs.append({
            "event_type": pp._AUDIT_EVENT, "entity_id": PLAN_ID, "transaction_id": "TX-STEP",
        })
    r = _post(app, {"amount": 100, "transaction_id": "TX-STEP"})
    assert r.status_code == 200
    assert pp._last_apply_path == "fallback"
    assert len(db["payments"].docs) == 1
    assert _plan(db)["balance_remaining"] == 700.0
    assert _plan(db)["payments_made"] == 2
    assert db["payment_plan_payment_claims"].docs[0]["status"] == "applied"
    audits = [d for d in db["audit_events"].docs if d.get("event_type") == pp._AUDIT_EVENT]
    assert len(audits) == 1
    if step == "balance_applied":
        assert audits[0]["old_state"] == {"balance_remaining": 800.0, "status": "active"}
        assert audits[0]["new_state"] == {"balance_remaining": 700.0, "status": "active"}
    again = _post(app, {"amount": 100, "transaction_id": "TX-STEP"})
    assert again.status_code == 200 and again.json()["idempotent"] is True
    assert _plan(db)["balance_remaining"] == 700.0


def test_resume_refuses_a_blank_row_booking(rig):
    db, app = rig
    db["payments"].docs.append({
        "transaction_id": "TX-BLANK", "plan_id": PLAN_ID, "amount": 100,
        "status": "completed", "logged_status": "approved", "booking_number": "",
        "type": "installment",
    })
    db["payment_plan_payment_claims"].docs.append({
        "plan_id": PLAN_ID, "transaction_id": "TX-BLANK", "status": "claimed",
        "step": "row_linked", "amount": 100,
    })
    r = _post(app, {"amount": 100, "transaction_id": "TX-BLANK"})
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_booking_mismatch"
    assert _plan(db)["balance_remaining"] == 800.0
    assert "collected_transaction_ids" not in _plan(db)
    row = db["payments"].docs[0]
    assert row.get("plan_id") in (None, "")
    assert row["status"] == "approved"
    assert db["audit_events"].docs == []


def test_fallback_reverses_a_link_that_no_longer_matches(rig):
    db, app = rig
    db["payments"].docs.append({
        "transaction_id": "TX-REV", "plan_id": PLAN_ID, "amount": 100,
        "status": "completed", "logged_status": "approved", "booking_number": "OTHER-BK",
        "type": "installment",
    })
    db["payment_plan_payment_claims"].docs.append({
        "plan_id": PLAN_ID, "transaction_id": "TX-REV", "status": "claimed",
        "step": "row_linked", "amount": 100,
    })
    r = _post(app, {"amount": 100, "transaction_id": "TX-REV"})
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_booking_mismatch"
    assert _plan(db)["balance_remaining"] == 800.0
    assert "collected_transaction_ids" not in _plan(db)
    row = db["payments"].docs[0]
    assert row.get("plan_id") in (None, "")
    assert row["status"] == "approved"
    assert db["payment_plan_payment_claims"].docs[0]["status"] == "failed"
    assert db["audit_events"].docs == []


def _use_transactions(monkeypatch, db):
    client = TxnClient(db)

    async def _client():
        return client

    monkeypatch.setattr(pp, "_txn_client", _client)
    return client


def test_transaction_links_once(rig, monkeypatch):
    db, app = rig
    db["payments"].docs.append(_logged())
    client = _use_transactions(monkeypatch, db)
    r = _post(app, {"amount": 100, "transaction_id": "TX-LOG"})
    assert r.status_code == 200 and r.json()["idempotent"] is False
    assert pp._last_apply_path == "transaction"
    assert client.attempts == 1
    assert len(db["payments"].docs) == 1 and db["payments"].docs[0]["plan_id"] == PLAN_ID
    assert _plan(db)["balance_remaining"] == 700.0
    assert db["payment_plan_payment_claims"].docs[0]["status"] == "applied"


def test_transaction_retries_a_transient_error_once(rig, monkeypatch):
    db, app = rig
    db["payments"].docs.append(_logged())
    db["audit_events"] = FlakyAudit(1, pp.RetryableTxnError())
    client = _use_transactions(monkeypatch, db)
    r = _post(app, {"amount": 100, "transaction_id": "TX-LOG"})
    assert r.status_code == 200
    assert client.attempts == 2
    assert len(db["payments"].docs) == 1
    assert db["payments"].docs[0]["plan_id"] == PLAN_ID
    assert _plan(db)["balance_remaining"] == 700.0
    assert _plan(db)["payments_made"] == 2
    assert len([d for d in db["audit_events"].docs if d.get("event_type") == pp._AUDIT_EVENT]) == 1


def test_transaction_abort_leaves_nothing(rig, monkeypatch):
    db, app = rig
    db["payments"].docs.append(_logged())
    db["audit_events"] = FlakyAudit(1, RuntimeError("audit down"))
    _use_transactions(monkeypatch, db)
    r = _post(app, {"amount": 100, "transaction_id": "TX-LOG"})
    assert r.status_code == 500 and r.json().get("success") is not True
    assert pp._last_apply_path == "transaction"
    assert _plan(db)["balance_remaining"] == 800.0
    assert _plan(db)["total_paid"] == 200.0 and _plan(db)["payments_made"] == 1
    assert "collected_transaction_ids" not in _plan(db)
    row = db["payments"].docs[0]
    assert row.get("plan_id") in (None, "")
    assert row["status"] == "approved"
    assert db["audit_events"].docs == []
    assert db["payment_plan_payment_claims"].docs[0]["status"] != "applied"
    again = _post(app, {"amount": 100, "transaction_id": "TX-LOG"})
    assert again.status_code == 409
    assert _plan(db)["balance_remaining"] == 800.0


def test_transaction_abort_on_insert_leaves_no_payment_row(rig, monkeypatch):
    db, app = rig
    db["audit_events"] = FlakyAudit(1, RuntimeError("audit down"))
    _use_transactions(monkeypatch, db)
    r = _post(app, {"amount": 100, "transaction_id": "TX-INS"})
    assert r.status_code == 500
    assert db["payments"].docs == []
    assert _plan(db)["balance_remaining"] == 800.0
    assert db["audit_events"].docs == []
    assert db["payment_plan_payment_claims"].docs[0]["status"] != "applied"


def test_transaction_resume_from_row_linked(rig, monkeypatch):
    db, app = rig
    _seed_linked_row(db)
    _seed_claim(db, "row_linked")
    _use_transactions(monkeypatch, db)
    r = _post(app, {"amount": 100, "transaction_id": "TX-STEP"})
    assert r.status_code == 200 and pp._last_apply_path == "transaction"
    assert _plan(db)["balance_remaining"] == 700.0
    assert len(db["payments"].docs) == 1
    assert db["payment_plan_payment_claims"].docs[0]["status"] == "applied"
