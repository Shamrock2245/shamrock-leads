"""0178 Tailscale recovery must not probe or rewrite other BlueBubbles lines."""
from unittest.mock import MagicMock

from dashboard.services.bb_client import _bb_url_is_tailscale, get_bb_client


class _Client:
    def __init__(self, url, password):
        self.url = url
        self.password = password


def _reset(ext, bbc):
    ext.BB_SERVERS.clear()
    ext._BB_URL_OVERRIDES.clear()
    bbc._tailscale_retry_after = 0.0


def test_tailscale_check_uses_the_hostname_only():
    assert _bb_url_is_tailscale("http://100.102.10.86:1234") is True
    assert _bb_url_is_tailscale("http://shamrocksimac:1234") is True
    assert _bb_url_is_tailscale("http://178.156.179.237:12434/path/100.") is False
    assert _bb_url_is_tailscale("http://0178.example") is False


def test_non_primary_line_is_not_probed_or_rewritten(monkeypatch):
    from dashboard import extensions as ext
    from dashboard.services import bb_client as bbc

    saved_servers = dict(ext.BB_SERVERS)
    saved_overrides = dict(ext._BB_URL_OVERRIDES)
    probe = MagicMock(side_effect=AssertionError("0314 must not probe Tailscale"))
    reinit = MagicMock(side_effect=AssertionError("0314 must not re-init BlueBubbles"))
    try:
        _reset(ext, bbc)
        ext.BB_SERVERS["2399550314"] = {
            "url": "http://178.156.179.237:12434",
            "password": "pw",
            "suffix": "0314",
        }
        ext._BB_URL_OVERRIDES["0314"] = "http://178.156.179.237:12434"
        monkeypatch.setattr("config.tailscale.ts_config.is_imac_reachable", probe)
        monkeypatch.setattr(ext, "init_bluebubbles", reinit)
        monkeypatch.setattr("dashboard.routers.bb_private_api.BlueBubblesClient", _Client)
        client = get_bb_client("2399550314")
        assert client.url == "http://178.156.179.237:12434"
        assert ext._BB_URL_OVERRIDES["0314"] == "http://178.156.179.237:12434"
        probe.assert_not_called()
        reinit.assert_not_called()
    finally:
        ext.BB_SERVERS.clear()
        ext.BB_SERVERS.update(saved_servers)
        ext._BB_URL_OVERRIDES.clear()
        ext._BB_URL_OVERRIDES.update(saved_overrides)
        bbc._tailscale_retry_after = 0.0


def test_primary_switches_to_tailscale_without_dropping_overrides(monkeypatch):
    from dashboard import extensions as ext
    from dashboard.services import bb_client as bbc

    saved_servers = dict(ext.BB_SERVERS)
    saved_overrides = dict(ext._BB_URL_OVERRIDES)

    def reinit():
        ext.BB_SERVERS["2399550178"] = {
            "url": "http://100.102.10.86:1234",
            "password": "pw",
            "suffix": "0178",
        }

    try:
        _reset(ext, bbc)
        ext.BB_SERVERS["2399550178"] = {
            "url": "http://178.156.179.237:12434",
            "password": "pw",
            "suffix": "0178",
        }
        ext._BB_URL_OVERRIDES["0178"] = "http://178.156.179.237:12434"
        ext._BB_URL_OVERRIDES["0314"] = "http://example.test/0314"
        monkeypatch.setattr("config.tailscale.ts_config.is_imac_reachable", lambda timeout=1.5: True)
        monkeypatch.setattr(ext, "init_bluebubbles", reinit)
        monkeypatch.setattr("dashboard.routers.bb_private_api.BlueBubblesClient", _Client)
        client = get_bb_client("2399550178")
        assert client.url == "http://100.102.10.86:1234"
        assert ext._BB_URL_OVERRIDES["0178"] == "http://178.156.179.237:12434"
        assert ext._BB_URL_OVERRIDES["0314"] == "http://example.test/0314"
    finally:
        ext.BB_SERVERS.clear()
        ext.BB_SERVERS.update(saved_servers)
        ext._BB_URL_OVERRIDES.clear()
        ext._BB_URL_OVERRIDES.update(saved_overrides)
        bbc._tailscale_retry_after = 0.0


def test_failed_tailscale_probe_is_not_repeated_on_the_next_send(monkeypatch):
    from dashboard import extensions as ext
    from dashboard.services import bb_client as bbc

    saved_servers = dict(ext.BB_SERVERS)
    saved_overrides = dict(ext._BB_URL_OVERRIDES)
    calls = {"n": 0}

    def probe(timeout=1.5):
        calls["n"] += 1
        raise OSError("tailnet down")

    try:
        _reset(ext, bbc)
        ext.BB_SERVERS["2399550178"] = {
            "url": "http://178.156.179.237:12434",
            "password": "pw",
            "suffix": "0178",
        }
        monkeypatch.setattr("config.tailscale.ts_config.is_imac_reachable", probe)
        monkeypatch.setattr(ext, "init_bluebubbles", MagicMock(side_effect=AssertionError("no re-init")))
        monkeypatch.setattr("dashboard.routers.bb_private_api.BlueBubblesClient", _Client)
        first = get_bb_client("2399550178")
        second = get_bb_client("2399550178")
        assert first.url == "http://178.156.179.237:12434"
        assert second.url == "http://178.156.179.237:12434"
        assert calls["n"] == 1
    finally:
        ext.BB_SERVERS.clear()
        ext.BB_SERVERS.update(saved_servers)
        ext._BB_URL_OVERRIDES.clear()
        ext._BB_URL_OVERRIDES.update(saved_overrides)
        bbc._tailscale_retry_after = 0.0


def test_probe_that_does_not_switch_the_url_backs_off(monkeypatch):
    from dashboard import extensions as ext
    from dashboard.services import bb_client as bbc

    saved_servers = dict(ext.BB_SERVERS)
    saved_overrides = dict(ext._BB_URL_OVERRIDES)
    calls = {"n": 0}

    def probe(timeout=1.5):
        calls["n"] += 1
        return True

    def reinit():
        ext.BB_SERVERS["2399550178"] = {
            "url": "http://178.156.179.237:12434",
            "password": "pw",
            "suffix": "0178",
        }

    try:
        _reset(ext, bbc)
        ext.BB_SERVERS["2399550178"] = {
            "url": "http://178.156.179.237:12434",
            "password": "pw",
            "suffix": "0178",
        }
        monkeypatch.setattr("config.tailscale.ts_config.is_imac_reachable", probe)
        monkeypatch.setattr(ext, "init_bluebubbles", reinit)
        monkeypatch.setattr("dashboard.routers.bb_private_api.BlueBubblesClient", _Client)
        first = get_bb_client("2399550178")
        second = get_bb_client("2399550178")
        assert first.url == "http://178.156.179.237:12434"
        assert second.url == "http://178.156.179.237:12434"
        assert calls["n"] == 1
    finally:
        ext.BB_SERVERS.clear()
        ext.BB_SERVERS.update(saved_servers)
        ext._BB_URL_OVERRIDES.clear()
        ext._BB_URL_OVERRIDES.update(saved_overrides)
        bbc._tailscale_retry_after = 0.0
