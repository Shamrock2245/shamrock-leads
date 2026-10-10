"""No DASHBOARD_PIN => protected routes fail closed (503) unless ENV=development.

CoS-approved hardening (2026-10-10). Before this, any non-production ENV
(including ENV unset) let every protected route through when the PIN was
missing. /health and the public allowlist keep their behaviour. Synthetic
values only; no Mongo (the /health DB ping is mocked).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dashboard.auth import pin_middleware
from dashboard.auth.pin_middleware import no_pin_passthrough_allowed
from dashboard.main import app

client = TestClient(app)

PROTECTED = ("/api/stats", "/openapi.json", "/docs", "/api/traccar/device-status/12345", "/")
PUBLIC = ("/login", "/robots.txt", "/kiosk", "/manifest.json")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("DASHBOARD_PIN", "ENV", "ENVIRONMENT", "REQUIRE_DASHBOARD_PIN",
                "GAS_API_KEY", "LEADS_INTERNAL_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(pin_middleware, "DASHBOARD_PIN", "")
    monkeypatch.setenv("SECRET_KEY", "synthetic-test-secret-key-not-real")


def _health_db():
    col, db = MagicMock(), MagicMock()
    db.command = AsyncMock(return_value={"ok": 1})
    col.database = db
    return patch("dashboard.main.get_collection", return_value=col)


def _get(path):
    return client.get(path, follow_redirects=False)


def _public_statuses():
    return {p: _get(p).status_code for p in PUBLIC}


@pytest.mark.parametrize("env", [None, "production", "prod", "staging", "test", "dev", "Development "])
def test_missing_pin_fails_closed_unless_env_is_development(monkeypatch, env):
    if env is not None:
        monkeypatch.setenv("ENV", env)
    expected_open = (env or "").strip().lower() == "development"
    assert no_pin_passthrough_allowed() is expected_open
    if expected_open:
        return  # covered by the development test below
    for path in PROTECTED:
        resp = _get(path)
        assert resp.status_code == 503, (env, path)
        assert resp.json() == {"error": "Dashboard PIN not configured"}
    with _health_db():
        assert _get("/health").status_code == 200


def test_missing_pin_env_unset_returns_503():
    assert _get("/api/stats").status_code == 503


def test_missing_pin_env_production_returns_503(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    assert _get("/api/stats").status_code == 503
    monkeypatch.delenv("ENV")
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert _get("/api/stats").status_code == 503


def test_missing_pin_env_development_passes_through(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    assert _get("/openapi.json").status_code == 200
    assert _get("/docs").status_code == 200
    monkeypatch.delenv("ENV")
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert _get("/openapi.json").status_code == 200


def test_development_still_closed_when_pin_required(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("REQUIRE_DASHBOARD_PIN", "true")
    assert _get("/api/stats").status_code == 503


@pytest.mark.parametrize("env", [None, "production", "development"])
def test_pin_set_keeps_401_and_302(monkeypatch, env):
    monkeypatch.setenv("DASHBOARD_PIN", "synthetic-test-pin-0000")
    if env:
        monkeypatch.setenv("ENV", env)
    for path in ("/api/stats", "/openapi.json", "/api/traccar/device-status/12345"):
        resp = _get(path)
        assert resp.status_code == 401, (env, path)
        assert resp.json() == {"error": "Authentication required"}
    resp = _get("/docs")
    assert resp.status_code == 302 and "/login" in resp.headers["location"]
    with _health_db():
        assert _get("/health").status_code == 200


def test_public_allowlist_unchanged_by_missing_pin(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PIN", "synthetic-test-pin-0000")
    with_pin = _public_statuses()
    monkeypatch.delenv("DASHBOARD_PIN")
    without_pin = _public_statuses()
    assert without_pin == with_pin
    assert all(code != 503 for code in without_pin.values())
