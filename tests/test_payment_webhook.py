"""Fail-closed tests for POST /api/webhooks/payment."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.webhooks import webhooks_bp

SECRET = "swipe-test-secret-not-real"


@pytest.fixture
def payment_app():
    app = FastAPI()
    app.include_router(webhooks_bp)
    return app


def test_payment_prod_env_unset_secret_503(payment_app):
    with patch.dict(os.environ, {"ENV": "production"}, clear=False):
        os.environ.pop("SWIPESIMPLE_WEBHOOK_SECRET", None)
        os.environ.pop("ENVIRONMENT", None)
        os.environ["ENV"] = "production"
        client = TestClient(payment_app)
        resp = client.post(
            "/api/webhooks/payment",
            content=b'{"event_type":"payment.completed","transaction_id":"t1"}',
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 503


def test_payment_bad_signature_401(payment_app):
    body = json.dumps(
        {"event_type": "payment.completed", "transaction_id": "t2"}
    ).encode("utf-8")
    with patch.dict(
        os.environ,
        {"ENV": "production", "SWIPESIMPLE_WEBHOOK_SECRET": SECRET},
        clear=False,
    ):
        client = TestClient(payment_app)
        resp = client.post(
            "/api/webhooks/payment",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-SwipeSimple-Signature": "deadbeef",
            },
        )
        assert resp.status_code == 401
        assert "signature" in resp.json().get("error", "").lower()


def test_payment_valid_signature_reaches_payload_gate(payment_app):
    """Valid HMAC should pass auth; incomplete body still fails field validation."""
    body = json.dumps({"event_type": "payment.completed"}).encode("utf-8")
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    with patch.dict(
        os.environ,
        {"ENV": "test", "SWIPESIMPLE_WEBHOOK_SECRET": SECRET},
        clear=False,
    ):
        client = TestClient(payment_app)
        resp = client.post(
            "/api/webhooks/payment",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-SwipeSimple-Signature": sig,
            },
        )
        assert resp.status_code == 400
        assert "Missing required fields" in resp.json().get("error", "")


# ── Unset secret: fail closed everywhere except ENV exactly "development" ────

_PROBE = b'{"event_type":"payment.completed","transaction_id":"t-unset"}'


def _post_unset_secret(app, monkeypatch, env=None, environment=None):
    monkeypatch.delenv("SWIPESIMPLE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("REQUIRE_DASHBOARD_PIN", raising=False)
    for key, val in (("ENV", env), ("ENVIRONMENT", environment)):
        if val is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, val)
    return TestClient(app).post(
        "/api/webhooks/payment", content=_PROBE,
        headers={"Content-Type": "application/json"},
    )


@pytest.mark.parametrize("env", [
    None, "", "   ", "production", "prod", "staging", "test", "dev",
    "Development", "DEVELOPMENT", " development", "development ", "develop",
])
def test_unset_secret_fails_closed_unless_env_exactly_development(payment_app, monkeypatch, env):
    with patch("dashboard.routers.webhooks.get_collection") as gc:
        resp = _post_unset_secret(payment_app, monkeypatch, env=env)
    assert resp.status_code == 503
    assert resp.json() == {"error": "Webhook secret not configured"}
    gc.assert_not_called()  # no DB touched


@pytest.mark.parametrize("environment", [None, "staging", "test", "dev", "Development"])
def test_environment_alias_fails_closed(payment_app, monkeypatch, environment):
    resp = _post_unset_secret(payment_app, monkeypatch, env=None, environment=environment)
    assert resp.status_code == 503


def test_env_takes_precedence_over_environment(payment_app, monkeypatch):
    resp = _post_unset_secret(payment_app, monkeypatch, env="staging", environment="development")
    assert resp.status_code == 503


def test_unset_secret_rejected_before_body_is_read(payment_app, monkeypatch):
    monkeypatch.delenv("SWIPESIMPLE_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENV", "staging")
    resp = TestClient(payment_app).post(
        "/api/webhooks/payment", content=b"not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 503  # not the 400 the JSON parser would give


def test_require_dashboard_pin_closes_development(payment_app, monkeypatch):
    monkeypatch.setenv("REQUIRE_DASHBOARD_PIN", "1")
    monkeypatch.delenv("SWIPESIMPLE_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENV", "development")
    resp = TestClient(payment_app).post(
        "/api/webhooks/payment", content=_PROBE, headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 503


@pytest.mark.parametrize("env,environment", [("development", None), (None, "development"), ("", "development")])
def test_explicit_development_still_reaches_payload_gate(payment_app, monkeypatch, env, environment):
    monkeypatch.delenv("SWIPESIMPLE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("REQUIRE_DASHBOARD_PIN", raising=False)
    for key, val in (("ENV", env), ("ENVIRONMENT", environment)):
        if val is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, val)
    resp = TestClient(payment_app).post(
        "/api/webhooks/payment", content=b'{"event_type":"payment.completed"}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400  # past auth; payload gate still applies


@pytest.mark.parametrize("env", ["staging", "test", None, "development"])
def test_secret_set_hmac_unchanged_in_every_env(payment_app, monkeypatch, env):
    monkeypatch.setenv("SWIPESIMPLE_WEBHOOK_SECRET", SECRET)
    if env is None:
        monkeypatch.delenv("ENV", raising=False)
    else:
        monkeypatch.setenv("ENV", env)
    client = TestClient(payment_app)
    missing = client.post("/api/webhooks/payment", content=_PROBE,
                          headers={"Content-Type": "application/json"})
    assert missing.status_code == 401
    body = b'{"event_type":"payment.completed"}'
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    ok = client.post("/api/webhooks/payment", content=body,
                     headers={"Content-Type": "application/json", "X-SwipeSimple-Signature": sig})
    assert ok.status_code == 400  # valid HMAC passes auth


# ── Later lifecycle events update the existing row (Codex P1 @72932a8) ────────


def _lifecycle_client(payment_app, monkeypatch):
    from dashboard.services import payment_txn_dedupe as dedupe
    from tests.test_payment_plan_pay_dedupe import DB

    db = DB()
    monkeypatch.setattr(dedupe, "_index_ready", False)
    monkeypatch.setattr("dashboard.routers.webhooks.get_collection", lambda name: db[name])
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("SWIPESIMPLE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("REQUIRE_DASHBOARD_PIN", raising=False)
    return db, TestClient(payment_app)


def _seed_linked_payment(db, *, balance=700.0, plan_status="active", plan_id="plan-test-01"):
    db["payment_log_claims"].docs.append({
        "transaction_id": "TX-LIFE", "status": "logged", "source": "payment_webhook",
    })
    db["payments"].docs.append({
        "_id": "pay-life",
        "transaction_id": "TX-LIFE",
        "amount": 100.0,
        "status": "completed",
        "plan_id": plan_id,
        "booking_number": "TEST-PP-1",
        "raw_payload": {"event_type": "payment.completed", "status": "approved", "amount": 100},
        "timestamp": "2026-10-01T00:00:00+00:00",
    })
    db["payment_plans"].docs.append({
        "plan_id": plan_id,
        "balance_remaining": balance,
        "total_paid": 300.0,
        "payments_made": 2,
        "status": plan_status,
        "applied_transaction_ids": ["TX-LIFE"],
    })


def _post_lifecycle(client, event_type, status, amount=100):
    return client.post(
        "/api/webhooks/payment",
        json={
            "event_type": event_type,
            "transaction_id": "TX-LIFE",
            "amount": amount,
            "status": status,
        },
    )


@pytest.mark.parametrize("event,status,stored", [
    ("payment.refunded", "refunded", "refunded"),
    ("payment.failed", "declined", "declined"),
    ("payment.failed", "", "failed"),
])
def test_later_lifecycle_updates_linked_row_and_restores_plan(payment_app, monkeypatch, event, status, stored):
    db, client = _lifecycle_client(payment_app, monkeypatch)
    _seed_linked_payment(db)
    with patch("dashboard.services.bb_client.send_message_universal") as bb:
        resp = _post_lifecycle(client, event, status)
        bb.assert_not_called()
    body = resp.json()
    assert resp.status_code == 200 and body["lifecycle_updated"] is True and body["status"] == stored
    row = db["payments"].docs[0]
    assert row["status"] == stored and row["plan_id"] == "plan-test-01"
    assert len(db["payments"].docs) == 1
    plan = db["payment_plans"].docs[0]
    assert plan["balance_remaining"] == 800.0
    assert plan["total_paid"] == 200.0 and plan["payments_made"] == 1
    assert "TX-LIFE" not in plan["applied_transaction_ids"]
    (ev,) = db["audit_events"].docs
    assert ev["event_type"] == event and ev["status_before"] == "completed"
    assert ev["status_after"] == stored and ev["balance_reversed"] is True
    assert ev["confidence"] == 1.0 and ev["transaction_id"] == "TX-LIFE"
    again = _post_lifecycle(client, event, status)
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert db["payment_plans"].docs[0]["balance_remaining"] == 800.0
    assert len(db["audit_events"].docs) == 1


def test_completed_replay_stays_duplicate(payment_app, monkeypatch):
    db, client = _lifecycle_client(payment_app, monkeypatch)
    _seed_linked_payment(db)
    resp = _post_lifecycle(client, "payment.completed", "approved")
    assert resp.status_code == 200 and resp.json() == {"success": True, "duplicate": True}
    assert db["payments"].docs[0]["status"] == "completed"
    assert db["payment_plans"].docs[0]["balance_remaining"] == 700.0
    (ev,) = db["audit_events"].docs
    assert ev["event_type"] == "payment.approved" and ev["transaction_id"] == "TX-LIFE"
    again = _post_lifecycle(client, "payment.completed", "approved")
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert len(db["audit_events"].docs) == 1
    assert len(db["payments"].docs) == 1


def test_lifecycle_amount_mismatch_does_not_move_the_plan(payment_app, monkeypatch):
    db, client = _lifecycle_client(payment_app, monkeypatch)
    _seed_linked_payment(db)
    resp = _post_lifecycle(client, "payment.refunded", "refunded", amount=90)
    assert resp.status_code == 409 and resp.json()["error"] == "payment_lifecycle_amount_mismatch"
    assert db["payments"].docs[0]["status"] == "completed"
    assert db["payment_plans"].docs[0]["balance_remaining"] == 700.0
    assert db["audit_events"].docs == []


def test_refund_of_paid_in_full_reopens_the_plan(payment_app, monkeypatch):
    db, client = _lifecycle_client(payment_app, monkeypatch)
    _seed_linked_payment(db, balance=0.0, plan_status="paid_in_full")
    resp = _post_lifecycle(client, "payment.refunded", "refunded")
    assert resp.status_code == 200 and resp.json()["lifecycle_updated"] is True
    plan = db["payment_plans"].docs[0]
    assert plan["balance_remaining"] == 100.0 and plan["status"] == "active"
    assert db["payments"].docs[0]["status"] == "refunded"


def test_lifecycle_audit_failure_is_not_success(payment_app, monkeypatch):
    db, client = _lifecycle_client(payment_app, monkeypatch)
    _seed_linked_payment(db)
    db["audit_events"].fail_insert = True
    resp = _post_lifecycle(client, "payment.refunded", "refunded")
    assert resp.status_code == 503 and resp.json()["error"] == "payment_lifecycle_update_failed"
    assert resp.json().get("success") is not True
    db["audit_events"].fail_insert = False
    again = _post_lifecycle(client, "payment.refunded", "refunded")
    assert again.status_code == 200 and again.json()["lifecycle_updated"] is True
    assert db["payments"].docs[0]["status"] == "refunded"
    assert db["payment_plans"].docs[0]["balance_remaining"] == 800.0
    assert len(db["audit_events"].docs) == 1


def test_unlinked_refund_updates_status_without_a_plan_reversal(payment_app, monkeypatch):
    db, client = _lifecycle_client(payment_app, monkeypatch)
    db["payment_log_claims"].docs.append({
        "transaction_id": "TX-LIFE", "status": "logged", "source": "swipesimple_gmail_poll",
    })
    db["payments"].docs.append({
        "_id": "pay-life",
        "transaction_id": "TX-LIFE",
        "amount": 100.0,
        "status": "approved",
        "raw_payload": {"event_type": "payment.completed", "amount": 100},
    })
    resp = _post_lifecycle(client, "payment.refunded", "refunded")
    assert resp.status_code == 200 and resp.json()["status"] == "refunded"
    assert db["payments"].docs[0]["status"] == "refunded"
    assert db["payment_plans"].docs == []
    assert db["audit_events"].docs[0]["balance_reversed"] is False


def test_refund_of_preexisting_row_does_not_insert_a_second_payment(payment_app, monkeypatch):
    """A payments row that predates payment_log_claims is updated, not copied."""
    db, client = _lifecycle_client(payment_app, monkeypatch)
    db["payments"].docs.append({
        "_id": "pay-old",
        "transaction_id": "TX-LIFE",
        "amount": 100.0,
        "status": "completed",
        "plan_id": "plan-test-01",
        "booking_number": "TEST-PP-1",
        "raw_payload": {"event_type": "payment.completed", "status": "approved", "amount": 100},
    })
    db["payment_plans"].docs.append({
        "plan_id": "plan-test-01",
        "balance_remaining": 700.0,
        "total_paid": 300.0,
        "payments_made": 2,
        "status": "active",
        "applied_transaction_ids": ["TX-LIFE"],
    })
    resp = _post_lifecycle(client, "payment.refunded", "refunded")
    assert resp.status_code == 200 and resp.json()["lifecycle_updated"] is True
    assert len(db["payments"].docs) == 1
    assert db["payments"].docs[0]["_id"] == "pay-old"
    assert db["payments"].docs[0]["status"] == "refunded"
    assert db["payment_plans"].docs[0]["balance_remaining"] == 800.0
    assert "TX-LIFE" not in db["payment_plans"].docs[0]["applied_transaction_ids"]
    assert db["payment_log_claims"].docs[0]["transaction_id"] == "TX-LIFE"


def test_refund_reverses_a_plan_linked_during_the_update(payment_app, monkeypatch):
    db, client = _lifecycle_client(payment_app, monkeypatch)
    db["payment_log_claims"].docs.append({
        "transaction_id": "TX-LIFE", "status": "logged", "source": "payment_webhook",
    })
    db["payments"].docs.append({
        "_id": "pay-life",
        "transaction_id": "TX-LIFE",
        "amount": 100.0,
        "status": "approved",
        "booking_number": "TEST-PP-1",
        "raw_payload": {"event_type": "payment.completed", "status": "approved", "amount": 100},
    })
    db["payment_plans"].docs.append({
        "plan_id": "plan-test-01",
        "balance_remaining": 800.0,
        "total_paid": 200.0,
        "payments_made": 1,
        "status": "active",
        "applied_transaction_ids": [],
    })

    def link_before_status_write(coll, flt):
        for row in coll.docs:
            if row.get("_id") == "pay-life":
                row["plan_id"] = "plan-test-01"
                row["status"] = "completed"
        plan = db["payment_plans"].docs[0]
        plan["balance_remaining"] = 700.0
        plan["total_paid"] = 300.0
        plan["payments_made"] = 2
        plan["applied_transaction_ids"] = ["TX-LIFE"]

    db["payments"].before_update = link_before_status_write
    resp = _post_lifecycle(client, "payment.refunded", "refunded")
    assert resp.status_code == 200 and resp.json()["lifecycle_updated"] is True
    assert len(db["payments"].docs) == 1
    assert db["payments"].docs[0]["status"] == "refunded"
    assert db["payments"].docs[0]["plan_id"] == "plan-test-01"
    plan = db["payment_plans"].docs[0]
    assert plan["balance_remaining"] == 800.0
    assert plan["total_paid"] == 200.0 and plan["payments_made"] == 1
    assert "TX-LIFE" not in plan["applied_transaction_ids"]


def test_completed_retry_writes_the_missing_initial_audit(payment_app, monkeypatch):
    db, client = _lifecycle_client(payment_app, monkeypatch)
    db["audit_events"].fail_insert = True
    first = _post_lifecycle(client, "payment.completed", "approved")
    assert first.status_code == 503
    assert first.json().get("success") is not True
    assert len(db["payments"].docs) == 1
    assert db["audit_events"].docs == []
    db["audit_events"].fail_insert = False
    second = _post_lifecycle(client, "payment.completed", "approved")
    assert second.status_code == 200 and second.json()["duplicate"] is True
    assert len(db["payments"].docs) == 1
    (ev,) = db["audit_events"].docs
    assert ev["event_type"] == "payment.approved" and ev["transaction_id"] == "TX-LIFE"


def _post_completed(client, custom_fields, amount=100, txn="TX-LIFE"):
    return client.post(
        "/api/webhooks/payment",
        json={
            "event_type": "payment.completed",
            "transaction_id": txn,
            "amount": amount,
            "status": "approved",
            "custom_fields": custom_fields,
        },
    )


def test_completed_webhook_enriches_an_unowned_poller_row(payment_app, monkeypatch):
    from dashboard.routers import payment_plans as pp
    from dashboard.routers.payment_plans import _booking_identity

    db, client = _lifecycle_client(payment_app, monkeypatch)
    monkeypatch.setattr(pp, "get_collection", lambda name: db[name])
    monkeypatch.setattr(pp, "_payment_claim_index_ready", False)
    db["payments"].docs.append({
        "_id": "pay-poll",
        "transaction_id": "TX-LIFE",
        "amount": 100.0,
        "status": "approved",
        "booking_number": "",
        "source": "swipesimple_gmail_poll",
    })
    db["payment_plans"].docs.append({
        "plan_id": "plan-test-01",
        "booking_number": "TEST-PP-1",
        "county": "Lee",
        "state": "FL",
        "total_premium": 1000.0,
        "down_payment": 200.0,
        "balance_remaining": 800.0,
        "installment_amount": 100.0,
        "frequency": "weekly",
        "payments_made": 1,
        "total_paid": 200.0,
        "status": "active",
    })
    payment_app.include_router(pp.payment_plans_bp)
    resp = _post_completed(client, {
        "booking_number": "TEST-PP-1", "county": "Lee (FL)", "state": "FL",
    })
    body = resp.json()
    assert resp.status_code == 200 and body["enriched"] is True and body["duplicate"] is True
    row = db["payments"].docs[0]
    assert row["booking_number"] == "TEST-PP-1"
    assert row["county"] == "Lee" and row["state"] == "FL"
    assert row["custom_fields"]["county"] == "Lee"
    assert "ownership_enrichment" not in row
    assert len(db["payments"].docs) == 1
    assert _booking_identity(row, nested=True) == ("TEST-PP-1", "lee", "fl")
    kinds = [e["event_type"] for e in db["audit_events"].docs]
    assert "payment.ownership_enriched" in kinds and "payment.approved" in kinds
    enriched = next(e for e in db["audit_events"].docs if e["event_type"] == "payment.ownership_enriched")
    assert enriched["booking_before"] == "" and enriched["booking_after"] == "TEST-PP-1"
    assert enriched["county_after"] == "Lee" and enriched["confidence"] == 1.0
    with patch("dashboard.routers.events.publish_event", new=AsyncMock()):
        pay = client.post(
            "/api/payments/plans/plan-test-01/pay",
            json={"amount": 100, "transaction_id": "TX-LIFE"},
        )
    assert pay.status_code == 200 and pay.json()["linked_logged_payment"] is True
    assert row["plan_id"] == "plan-test-01"
    assert db["payment_plans"].docs[0]["balance_remaining"] == 700.0


def test_completed_webhook_does_not_overwrite_a_different_booking(payment_app, monkeypatch):
    db, client = _lifecycle_client(payment_app, monkeypatch)
    db["payments"].docs.append({
        "_id": "pay-poll",
        "transaction_id": "TX-LIFE",
        "amount": 100.0,
        "status": "approved",
        "booking_number": "OTHER-1",
        "county": "Collier",
        "state": "FL",
    })
    resp = _post_completed(client, {
        "booking_number": "TEST-PP-1", "county": "Lee", "state": "FL",
    })
    assert resp.status_code == 409
    assert resp.json()["error"] == "logged_payment_jurisdiction_unverified"
    row = db["payments"].docs[0]
    assert row["booking_number"] == "OTHER-1" and row["county"] == "Collier"
    assert len(db["payments"].docs) == 1
    assert not any(e["event_type"] == "payment.ownership_enriched" for e in db["audit_events"].docs)


def test_completed_webhook_amount_mismatch_does_not_enrich(payment_app, monkeypatch):
    db, client = _lifecycle_client(payment_app, monkeypatch)
    db["payments"].docs.append({
        "_id": "pay-poll",
        "transaction_id": "TX-LIFE",
        "amount": 100.0,
        "status": "approved",
        "booking_number": "",
    })
    resp = _post_completed(client, {
        "booking_number": "TEST-PP-1", "county": "Lee", "state": "FL",
    }, amount=90)
    assert resp.status_code == 409
    assert resp.json()["error"] == "payment_lifecycle_amount_mismatch"
    row = db["payments"].docs[0]
    assert row["booking_number"] == "" and "county" not in row
    assert len(db["payments"].docs) == 1
