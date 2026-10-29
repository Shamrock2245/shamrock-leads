"""Contract tests for fail-closed BB_CONFIG_API_KEY on BlueBubbles URL hot-swap routes."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.bb_health_monitor import bb_health_bp
from dashboard.routers.legacy import legacy_bp

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(legacy_bp)
    app.include_router(bb_health_bp)
    return TestClient(app)


def test_bb_config_url_fails_closed_when_unconfigured(monkeypatch):
    monkeypatch.delenv("BB_CONFIG_API_KEY", raising=False)
    monkeypatch.delenv("GAS_API_KEY", raising=False)
    monkeypatch.delenv("LEADS_INTERNAL_TOKEN", raising=False)
    monkeypatch.setattr("dashboard.extensions.BB_CONFIG_API_KEY", "")
    monkeypatch.setattr("dashboard.routers.legacy.BB_CONFIG_API_KEY", "")

    client = _make_client()
    resp = client.post(
        "/api/config/bluebubbles-url",
        headers={"X-API-Key": "any-attempt"},
        json={"suffix": "0178", "url": "https://example.invalid"},
    )
    assert resp.status_code == 503
    assert resp.json()["error"] == "BB_CONFIG_API_KEY not configured"


def test_bb_config_url_requires_matching_configured_key(monkeypatch):
    monkeypatch.setenv("BB_CONFIG_API_KEY", "test-bb-sync-secret-9999")
    monkeypatch.setattr("dashboard.extensions.BB_CONFIG_API_KEY", "test-bb-sync-secret-9999")
    monkeypatch.setattr("dashboard.routers.legacy.BB_CONFIG_API_KEY", "test-bb-sync-secret-9999")

    client = _make_client()
    bad = client.post(
        "/api/config/bluebubbles-url",
        headers={"X-API-Key": "wrong-key"},
        json={"suffix": "0178", "url": "https://example.invalid"},
    )
    assert bad.status_code == 401

    with patch("dashboard.routers.legacy.update_bb_url", return_value={"2399550178": {"url": "https://bb.example.com", "label": "Primary"}}):
        ok = client.post(
            "/api/config/bluebubbles-url",
            headers={"X-API-Key": "test-bb-sync-secret-9999"},
            json={"suffix": "0178", "url": "https://bb.example.com"},
        )
    assert ok.status_code == 200
    assert ok.json()["success"] is True


def test_bb_health_update_url_fails_closed_without_key_or_admin_session(monkeypatch):
    monkeypatch.delenv("BB_CONFIG_API_KEY", raising=False)
    monkeypatch.delenv("GAS_API_KEY", raising=False)
    monkeypatch.delenv("LEADS_INTERNAL_TOKEN", raising=False)
    monkeypatch.setattr("dashboard.extensions.BB_CONFIG_API_KEY", "")

    client = _make_client()
    unconf = client.patch(
        "/api/bb-health/update-url",
        json={"suffix": "0178", "url": "https://bb.example.com", "api_key": "guess"},
    )
    assert unconf.status_code == 503

    no_auth = client.patch(
        "/api/bb-health/update-url",
        json={"suffix": "0178", "url": "https://bb.example.com"},
    )
    assert no_auth.status_code == 401


def test_bb_health_update_url_allows_admin_session_without_frontend_api_key(monkeypatch):
    monkeypatch.delenv("BB_CONFIG_API_KEY", raising=False)
    monkeypatch.setattr("dashboard.extensions.BB_CONFIG_API_KEY", "")

    fake_bb = MagicMock()
    fake_bb.ping = AsyncMock(return_value={"success": True})

    client = _make_client()
    with patch("dashboard.auth.pin_middleware.session_is_admin", return_value=True), \
         patch("dashboard.extensions.update_bb_url", return_value={"2399550178": {"url": "https://bb.example.com", "password": "pw", "label": "Primary"}}), \
         patch("dashboard.routers.bb_private_api.BlueBubblesClient", return_value=fake_bb):
        resp = client.patch(
            "/api/bb-health/update-url",
            json={"suffix": "0178", "url": "https://bb.example.com"},
        )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_frontend_js_does_not_embed_bb_config_key():
    src = (REPO_ROOT / "dashboard" / "sl-imessage.js").read_text(encoding="utf-8")
    assert "shamrock-bb-sync" not in src


def test_bb_url_sync_script_sources_launchagent_env_file():
    script = (REPO_ROOT / "scripts" / "bb-url-sync.sh").read_text(encoding="utf-8")
    assert ".shamrock-bb-sync.env" in script
    assert "BB_CONFIG_API_KEY" in script
    assert "shamrock-bb-sync-" not in script

