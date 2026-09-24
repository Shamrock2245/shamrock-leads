"""Fail-closed tests for POST /api/webhooks/payment."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from unittest.mock import patch

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
