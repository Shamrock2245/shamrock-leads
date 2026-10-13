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
