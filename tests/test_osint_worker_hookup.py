"""OSINT worker hookup: key minting, probe distinction, Trape open path."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from dashboard.auth.pin_middleware import OPEN_PREFIXES
from dashboard.services import osint_service
from scripts.ensure_osint_worker_key import ensure


def test_ensure_osint_worker_key_mints_once(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("MONGODB_URI=mongodb://x\nOSINT_WORKER_KEY=\n", encoding="utf-8")
    first = ensure(env)
    assert first["key_minted"] is True
    assert first["key_len"] == 64
    text = env.read_text(encoding="utf-8")
    assert "OSINT_WORKER_KEY=" in text
    assert "TRAPE_SERVER_URL=https://leads.shamrockbailbonds.biz" in text
    key_line = [ln for ln in text.splitlines() if ln.startswith("OSINT_WORKER_KEY=")][0]
    second = ensure(env)
    assert second["key_minted"] is False
    assert env.read_text(encoding="utf-8").count(key_line) == 1


def test_probe_tools_auth_fail_is_not_worker_down():
    trape = {"available": True, "server_url": "https://leads.shamrockbailbonds.biz"}

    class _Resp:
        def __init__(self, code, payload=None):
            self.status_code = code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            if url.endswith("/health"):
                return _Resp(200)
            return _Resp(503)

    with patch.object(osint_service, "OSINT_WORKER_KEY", ""), patch(
        "dashboard.services.osint_service.httpx.Client", _Client
    ), patch.object(osint_service.OSINTService, "_trape_status", return_value=trape):
        data = osint_service.OSINTService.probe_tools()

    assert data["worker_reachable"] is True
    assert data["worker_auth_ok"] is False
    assert data["ready_for_scans"] is False
    assert data["maigret"]["available"] is False
    assert "OSINT_WORKER_KEY" in data["error"]


def test_probe_tools_healthy_auth():
    trape = {"available": True, "server_url": "https://leads.shamrockbailbonds.biz"}
    payload = {
        "maigret": {"available": True, "path": "python -m maigret"},
        "tookie": {"available": True},
        "ready_for_scans": True,
        "version": "2.4.0",
    }

    class _Resp:
        def __init__(self, code, body=None):
            self.status_code = code
            self._body = body or {}

        def json(self):
            return dict(self._body)

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            if url.endswith("/health"):
                return _Resp(200)
            return _Resp(200, payload)

    with patch.object(osint_service, "OSINT_WORKER_KEY", "test-key"), patch(
        "dashboard.services.osint_service.httpx.Client", _Client
    ), patch.object(osint_service.OSINTService, "_trape_status", return_value=trape):
        data = osint_service.OSINTService.probe_tools()

    assert data["worker_reachable"] is True
    assert data["worker_auth_ok"] is True
    assert data["ready_for_scans"] is True
    assert data["maigret"]["available"] is True
    assert data["trape"]["available"] is True
    assert data["worker_key_configured"] is True


def test_track_prefix_is_open():
    assert any(p == "/track/" for p in OPEN_PREFIXES)
