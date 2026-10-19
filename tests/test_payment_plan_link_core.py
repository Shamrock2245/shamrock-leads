"""POST /api/payments/plans/{plan_id}/pay links a committed logged row, or inserts one.

Offline: the in-memory Mongo double from the dedupe tests, plus a session shim
that commits or rolls back a multi-document transaction. No network, no
SwipeSimple, Gmail, or Slack.
"""
from __future__ import annotations

import asyncio
import copy
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from dashboard.routers import payment_plans as pp
from tests.test_payment_plan_pay_dedupe import PLAN_ID, Coll, DB, _plan


def _app(db, monkeypatch):
    pp._inflight_txns.clear()
    pp._txn_support_cache.clear()
    client = TxnClient(db)

    async def _txn():
        return client

    monkeypatch.setattr(pp, "get_collection", lambda name: db[name])
    monkeypatch.setattr(pp, "_payment_claim_index_ready", False)
    monkeypatch.setattr(pp, "_txn_client", _txn)
    import dashboard.services.payment_log_claims as payment_log
    monkeypatch.setattr(payment_log, "_lock_index_ready", False)
    monkeypatch.setattr(payment_log, "_index_ready", False)
    payment_log._txn_support_cache.clear()
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
    assert audit["_id"] == f"plan-pay:{PLAN_ID}:TX-LOG"
    assert "tenant_id" not in audit
    assert audit["old_state"] == {"balance_remaining": 800.0, "status": "active"}
    assert audit["new_state"] == {"balance_remaining": 700.0, "status": "active"}
    assert db["payments"].index_calls == 0 and db["payments"].unique == []
    assert pp._last_apply_path == "transaction"
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
    ({"status": "declined"}, "payment_needs_review"),
    ({"status": "refunded"}, "payment_needs_review"),
    ({"status": "pending"}, "logged_payment_not_settled"),
    ({"status": ""}, "logged_payment_not_settled"),
    ({"raw_payload": {"event_type": "payment.refunded"}}, "payment_needs_review"),
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


def test_blank_booking_with_no_logged_row_writes_nothing(rig):
    db, app = rig
    _plan(db)["booking_number"] = "   "
    r = _post(app, {"amount": 100, "transaction_id": "TX-NEW"})
    assert r.status_code == 409 and r.json()["error"] == "logged_payment_booking_mismatch"
    assert db["payments"].docs == []
    assert db["payment_plan_payment_claims"].docs == []
    assert db["audit_events"].docs == []
    assert _plan(db)["balance_remaining"] == 800.0
    assert "collected_transaction_ids" not in _plan(db)


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
    pp._inflight_txns.add(pp._inflight_key("TX-LIVE"))
    r = _post(app, {"amount": 100, "transaction_id": "TX-LIVE"})
    assert r.status_code == 409 and r.json()["error"] == "payment_in_progress"
    assert db["payments"].docs == [] and _plan(db)["balance_remaining"] == 800.0


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
    # The reversal gate is its own committed transaction, then the apply runs.
    assert client.attempts == 2
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
    # Gate commits once, then the apply hits one transient error and retries.
    assert client.attempts == 3
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


def test_transactions_unavailable_returns_503_with_no_writes(rig, monkeypatch):
    db, app = rig

    async def _none():
        return None

    monkeypatch.setattr(pp, "_txn_client", _none)
    r = _post(app, {"amount": 100, "transaction_id": "TX-NEW"})
    assert r.status_code == 503 and r.json()["error"] == "transactions_unavailable"
    assert db["payments"].docs == []
    assert db["payment_plan_payment_claims"].docs == []
    assert db["audit_events"].docs == []
    assert _plan(db)["balance_remaining"] == 800.0
    assert db["payment_plan_payment_claims"].index_calls == 0


def test_standalone_server_returns_503_with_no_writes(rig, monkeypatch):
    db, app = rig

    class _Standalone:
        def __init__(self):
            self.admin = _Admin({"ismaster": True, "ok": 1})
            self.sessions = 0

        async def start_session(self):
            self.sessions += 1
            raise AssertionError("session opened without a replica set")

    standalone = _Standalone()

    async def _client():
        return standalone

    monkeypatch.setattr(pp, "_txn_client", _client)
    pp._txn_support_cache.clear()
    r = _post(app, {"amount": 100, "transaction_id": "TX-NEW"})
    assert r.status_code == 503 and r.json()["error"] == "transactions_unavailable"
    assert standalone.sessions == 0
    assert db["payments"].docs == []
    assert db["payment_plan_payment_claims"].docs == []
    assert _plan(db)["balance_remaining"] == 800.0


def test_transaction_probe_failure_returns_503_with_no_writes(rig, monkeypatch):
    db, app = rig

    async def _boom():
        raise RuntimeError("hello failed")

    monkeypatch.setattr(pp, "_txn_client", _boom)
    r = _post(app, {"amount": 100, "transaction_id": "TX-NEW"})
    assert r.status_code == 503 and r.json()["error"] == "transactions_unavailable"
    assert db["payments"].docs == []
    assert db["payment_plan_payment_claims"].docs == []
    assert _plan(db)["balance_remaining"] == 800.0


_TENANT_A = "gulf_coast_bail"
_TENANT_B = "other_agency"
_SHARED_TXN = "TX-SHARED"


def _tenant_plan(tenant: str) -> dict:
    return {
        "plan_id": PLAN_ID,
        "tenant_id": tenant,
        "booking_number": "TEST-PP-1",
        "total_premium": 1000.0,
        "down_payment": 200.0,
        "balance_remaining": 800.0,
        "installment_amount": 100.0,
        "frequency": "weekly",
        "payments_made": 1,
        "total_paid": 200.0,
        "status": "active",
    }


class _ClaimGate(Coll):
    """Hold each new claim insert until both tenants have inserted one.

    The in-flight guard is taken after that insert returns and before the
    next await. Waiting here forces the two holds to overlap.
    """

    def __init__(self):
        super().__init__()
        self.gate: asyncio.Event | None = None
        self.arrivals = 0

    async def insert_one(self, doc, session=None, **kwargs):
        await super().insert_one(doc, session=session, **kwargs)
        if doc.get("status") == "claimed" and doc.get("transaction_id") == _SHARED_TXN:
            if self.gate is None:
                self.gate = asyncio.Event()
            self.arrivals += 1
            if self.arrivals >= 2:
                self.gate.set()
            await asyncio.wait_for(self.gate.wait(), timeout=5)


def test_two_tenants_same_txn_both_commit_and_replay_is_idempotent(monkeypatch):
    """Flag on: the same transaction id on two agencies both commit.

    Each keeps its own claim and its own audit row. A second post from the
    first agency does not move either balance.
    """
    from dashboard.tenancy.context import bind_job_tenant
    from dashboard.tenancy.scope import TenantScopedCollection

    monkeypatch.setenv("SAAS_MULTI_TENANT", "1")
    db = DB()
    db["payment_plans"].docs.extend([_tenant_plan(_TENANT_A), _tenant_plan(_TENANT_B)])
    claims = _ClaimGate()
    db["payment_plan_payment_claims"] = claims
    pp._inflight_txns.clear()
    pp._txn_support_cache.clear()
    monkeypatch.setattr(pp, "_payment_claim_index_ready", False)

    client = TxnClient(db)

    async def _txn():
        return client

    monkeypatch.setattr(pp, "get_collection", lambda name: TenantScopedCollection(db[name], name))
    monkeypatch.setattr(pp, "_txn_client", _txn)
    app = FastAPI()
    app.include_router(pp.payment_plans_bp)

    async def both():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            async def one(tenant):
                with bind_job_tenant(tenant):
                    return await http.post(
                        f"/api/payments/plans/{PLAN_ID}/pay",
                        json={"amount": 100, "transaction_id": _SHARED_TXN},
                    )
            return await asyncio.gather(one(_TENANT_A), one(_TENANT_B))

    async def replay():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            with bind_job_tenant(_TENANT_A):
                return await http.post(
                    f"/api/payments/plans/{PLAN_ID}/pay",
                    json={"amount": 100, "transaction_id": _SHARED_TXN},
                )

    try:
        with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
            rs = asyncio.run(both())
            again = asyncio.run(replay())
    finally:
        pp._inflight_txns.clear()

    assert claims.arrivals == 2
    assert all(r.status_code == 200 and r.json()["idempotent"] is False for r in rs)
    assert {c["tenant_id"] for c in claims.docs} == {_TENANT_A, _TENANT_B}
    assert all(
        c["status"] == "applied" and c["transaction_id"] == _SHARED_TXN and c["plan_id"] == PLAN_ID
        for c in claims.docs
    )
    audits = db["audit_events"].docs
    assert {a["_id"] for a in audits} == {
        f"plan-pay:{_TENANT_A}:{PLAN_ID}:{_SHARED_TXN}",
        f"plan-pay:{_TENANT_B}:{PLAN_ID}:{_SHARED_TXN}",
    }
    assert {a.get("tenant_id") for a in audits} == {_TENANT_A, _TENANT_B}
    assert len(db["payments"].docs) == 2
    assert {row["tenant_id"] for row in db["payments"].docs} == {_TENANT_A, _TENANT_B}
    assert db["payments"].index_calls == 0 and db["payments"].unique == []
    for plan in db["payment_plans"].docs:
        assert plan["balance_remaining"] == 700.0
        assert plan["payments_made"] == 2 and plan["total_paid"] == 300.0

    assert again.status_code == 200 and again.json()["idempotent"] is True
    assert again.json()["balance_remaining"] == 700.0
    assert len(claims.docs) == 2 and len(db["audit_events"].docs) == 2 and len(db["payments"].docs) == 2
    balances = {plan["tenant_id"]: plan["balance_remaining"] for plan in db["payment_plans"].docs}
    assert balances == {_TENANT_A: 700.0, _TENANT_B: 700.0}


# ci.yml lists test modules explicitly. This file is already on that list, so
# the creation suite runs in CI without a workflow edit. Skip when that module
# was collected directly.
def test_payment_plan_creation_suite_passes(request):
    root = Path(__file__).resolve().parents[1]
    target = root / "tests" / "test_payment_plan_creation.py"
    collected = {Path(str(item.fspath)).resolve() for item in request.session.items}
    if target.resolve() in collected:
        pytest.skip("payment plan creation suite already collected")
    env = dict(os.environ)
    env.setdefault("ENV", "test")
    env.setdefault("SECRET_KEY", "ci-not-a-real-secret")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", str(target)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (proc.stdout[-4000:] + proc.stderr[-2000:])
