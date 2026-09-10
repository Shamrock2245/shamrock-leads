"""
Unit tests for Traccar device auto-provisioning script and setup router.
"""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.traccar_auto_provision import generate_traccar_config
from dashboard.services.traccar_client import booking_to_unique_id


def test_booking_to_unique_id():
    uid = booking_to_unique_id("LEE-2026-00123")
    assert uid == "shamrock-LEE-2026-00123"


def test_generate_traccar_config():
    config = generate_traccar_config("shamrock-LEE-2026-00123", public_host="leads.shamrockbailbonds.biz")
    assert config["unique_id"] == "shamrock-LEE-2026-00123"
    assert config["server_url"] == "http://leads.shamrockbailbonds.biz:5055"
    assert "org.traccar.client://" in config["deeplink"]
    assert "url=http%3A%2F%2Fleads.shamrockbailbonds.biz%3A5055" in config["deeplink"]
    assert "id=shamrock-LEE-2026-00123" in config["deeplink"]
    assert config["setup_url"].endswith("/traccar/setup/shamrock-LEE-2026-00123")


def test_compose_forward_header_is_quoted_yaml():
    """Unquoted 'Header: value' is parsed as a YAML map and docker compose config fails."""
    from pathlib import Path
    main = Path("docker-compose.yml").read_text()
    overlay = Path("traccar/docker-compose.traccar.yml").read_text()
    assert '"CONFIG_FORWARD_HEADER=X-Traccar-Webhook-Secret:' in main
    assert '"CONFIG_FORWARD_HEADER=X-Traccar-Webhook-Secret:' in overlay
    assert "CONFIG_USE_ENVIRONMENT_VARIABLES=true" in main


def test_device_status_token_fail_closed_without_secret(monkeypatch):
    from dashboard.routers.traccar_setup_api import make_device_status_token, verify_device_status_token
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("TRACCAR_STATUS_TOKEN_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        make_device_status_token("12345")
    assert verify_device_status_token("12345", "deadbeef") is False


def test_device_status_token_rejects_length_mismatch(monkeypatch):
    from dashboard.routers.traccar_setup_api import make_device_status_token, verify_device_status_token
    monkeypatch.setenv("SECRET_KEY", "unit-test-secret")
    token = make_device_status_token("dev-1")
    assert verify_device_status_token("dev-1", token) is True
    assert verify_device_status_token("dev-1", "short") is False
    assert verify_device_status_token("dev-1", "") is False


@pytest.mark.asyncio
async def test_traccar_health_check_fallback():
    from dashboard.services.traccar_client import TraccarClient
    client = TraccarClient(base_url="http://unreachable-host:9999")
    h = await client.health_check()
    assert h.get("status") == "standby"
    assert "admin@shamrockbailbonds.biz" in h.get("user", "")
