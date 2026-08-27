"""Watchdog includes Shannon path checks. No live HTTP, no client texts."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from dashboard.services.watchdog_service import WatchdogService


def test_watchdog_passes_when_shannon_paths_healthy():
    svc = WatchdogService(db=None)
    svc.slack = MagicMock()
    svc.slack._post = MagicMock()

    async def fake_get(url, *a, **k):
        resp = MagicMock()
        resp.status_code = 200
        return resp

    shannon = {
        "success": True,
        "checks": {
            "bluebubbles": {"ok": True, "path_in_use": "tailscale"},
            "mem0": {"ok": True, "enabled": True},
            "netlify_voice": {"ok": True, "status": 403},
            "netlify_voice_fallback": {"ok": True, "status": 200},
            "gas_health": {"ok": True, "status": 200},
        },
    }

    with patch("httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.get = AsyncMock(side_effect=fake_get)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client_cls.return_value = client
        with patch(
            "dashboard.routers.shannon_health.collect_shannon_path_checks",
            AsyncMock(return_value=shannon),
        ):
            result = asyncio.run(svc.run_health_checks())

    assert result["api_health"] is True
    assert result["gas_bridge"] is True
    assert result["shannon_paths"] is True
    assert result["errors"] == []
    svc.slack._post.assert_not_called()


def test_watchdog_alerts_when_bluebubbles_down():
    svc = WatchdogService(db=None)
    svc.slack = MagicMock()
    svc.slack.webhook_errors = "https://example.invalid/hooks"
    svc.slack._post = MagicMock()

    async def fake_get(url, *a, **k):
        resp = MagicMock()
        resp.status_code = 200
        return resp

    shannon = {
        "success": False,
        "checks": {
            "bluebubbles": {"ok": False, "error": "timeout"},
            "mem0": {"ok": True},
            "netlify_voice": {"ok": True, "status": 403},
            "netlify_voice_fallback": {"ok": True, "status": 200},
            "gas_health": {"ok": True, "status": 200},
        },
    }

    with patch("httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.get = AsyncMock(side_effect=fake_get)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client_cls.return_value = client
        with patch(
            "dashboard.routers.shannon_health.collect_shannon_path_checks",
            AsyncMock(return_value=shannon),
        ):
            result = asyncio.run(svc.run_health_checks())

    assert result["shannon_paths"] is False
    assert any("bluebubbles" in err for err in result["errors"])
    svc.slack._post.assert_called()


def test_shannon_health_includes_fallback_dial():
    from dashboard.routers import shannon_health

    assert shannon_health.VOICE_FALLBACK_URL.endswith("/api/twilio-voice-fallback")
    assert shannon_health.DESK_E164 == "+12399550301"
    assert shannon_health.OFFICE_E164 == "+12393322245"
    assert shannon_health.SHANNON_E164 == "+17272952245"
    assert shannon_health.MEMORY_STATUS_PATH == "/api/agent-brain/memory/status"
