"""Tests for APE-first residential proxy resolution (WAF/CF fail-closed)."""

from unittest.mock import MagicMock, patch

import pytest

from scrapers.socks_proxy import (
    curl_cffi_proxies,
    resolve_residential_proxy,
    validate_residential_proxy,
    _normalize_playwright_proxy,
    to_playwright_proxy,
)


def test_normalize_playwright_proxy():
    assert _normalize_playwright_proxy("socks5h://1.2.3.4:1080") == "socks5://1.2.3.4:1080"
    assert _normalize_playwright_proxy("1.2.3.4:1080") == "socks5://1.2.3.4:1080"
    assert _normalize_playwright_proxy("socks5://1.2.3.4:1080") == "socks5://1.2.3.4:1080"


def test_to_playwright_proxy_splits_credentials():
    d = to_playwright_proxy("http://warren:s3cret@178.156.179.237:8000")
    assert d["server"] == "http://178.156.179.237:8000"
    assert d["username"] == "warren"
    assert d["password"] == "s3cret"

    d2 = to_playwright_proxy("socks5://172.18.0.1:1080")
    assert d2["server"] == "socks5://172.18.0.1:1080"
    assert "username" not in d2


def test_curl_cffi_proxies():
    assert curl_cffi_proxies(None) is None
    d = curl_cffi_proxies("http://u:p@h:8000")
    assert d == {"http": "http://u:p@h:8000", "https": "http://u:p@h:8000"}


def test_resolve_require_false_when_nothing_available():
    # Force every path dead (no real network)
    dc = {
        "ok": True,
        "ip": "1.2.3.4",
        "org": "Hetzner Online GmbH",
        "country": "DE",
        "residential_likely": False,
    }
    with patch("scrapers.socks_proxy.socks5_connect_ok", return_value=False), patch(
        "scrapers.socks_proxy.http_proxy_endpoint_ok", return_value=False
    ), patch("scrapers.cf_browser.check_exit_ip", return_value=dc), patch(
        "scrapers.socks_proxy._office_socks_candidates", return_value=[]
    ):
        url, source = resolve_residential_proxy(None, require=False)
    assert source == "none"
    assert url is None


def test_resolve_require_true_raises_when_nothing():
    dc = {
        "ok": True,
        "ip": "1.2.3.4",
        "org": "Hetzner Online GmbH",
        "country": "DE",
        "residential_likely": False,
    }
    with patch("scrapers.socks_proxy.socks5_connect_ok", return_value=False), patch(
        "scrapers.socks_proxy.http_proxy_endpoint_ok", return_value=False
    ), patch("scrapers.cf_browser.check_exit_ip", return_value=dc), patch(
        "scrapers.socks_proxy._office_socks_candidates", return_value=[]
    ):
        with pytest.raises(RuntimeError, match="residential egress"):
            resolve_residential_proxy(None, require=True)


def test_resolve_uses_ape_http_proxy_when_endpoint_ok():
    scraper = MagicMock()
    scraper.get_proxy.return_value = "http://user:pass@proxy.example:8080"
    scraper.get_sticky_proxy.return_value = None
    scraper.ape = None
    residential = {
        "ok": True,
        "ip": "73.1.2.3",
        "org": "Comcast",
        "country": "US",
        "residential_likely": True,
    }
    with patch("scrapers.socks_proxy.socks5_connect_ok", return_value=False), patch(
        "scrapers.socks_proxy.http_proxy_endpoint_ok", return_value=True
    ), patch("scrapers.cf_browser.check_exit_ip", return_value=residential), patch(
        "scrapers.socks_proxy._office_socks_candidates", return_value=[]
    ):
        url, source = resolve_residential_proxy(scraper, require=False)
    assert source == "ape"
    assert url.startswith("http://")
    scraper.get_proxy.assert_called()


def test_resolve_sticky_session_preferred():
    scraper = MagicMock()
    scraper.get_sticky_proxy.return_value = "http://warren:pw@hub:8000"
    scraper.get_proxy.return_value = "http://other:pw@hub:8000"
    scraper.ape = None
    residential = {
        "ok": True,
        "ip": "73.1.2.3",
        "org": "Comcast",
        "country": "US",
        "residential_likely": True,
    }
    with patch("scrapers.socks_proxy.socks5_connect_ok", return_value=False), patch(
        "scrapers.socks_proxy.http_proxy_endpoint_ok", return_value=True
    ), patch("scrapers.cf_browser.check_exit_ip", return_value=residential), patch(
        "scrapers.socks_proxy._office_socks_candidates", return_value=[]
    ):
        url, source = resolve_residential_proxy(
            scraper, require=False, sticky_session="fl-charlotte"
        )
    assert source == "ape"
    scraper.get_sticky_proxy.assert_called()
    # First call uses base sticky id
    assert scraper.get_sticky_proxy.call_args_list[0][0][0] == "fl-charlotte"


def test_resolve_rotates_when_ape_exit_not_residential():
    scraper = MagicMock()
    scraper.get_sticky_proxy.return_value = None
    # First candidate datacenter, second residential
    scraper.get_proxy.side_effect = [
        "http://bad:pw@hub:8000",
        "http://good:pw@hub:8000",
    ]
    scraper.ape = None
    dc = {
        "ok": True,
        "ip": "5.6.7.8",
        "org": "DigitalOcean",
        "country": "US",
        "residential_likely": False,
    }
    res = {
        "ok": True,
        "ip": "73.1.2.3",
        "org": "Comcast",
        "country": "US",
        "residential_likely": True,
    }

    def _exit(url, **kwargs):
        if url and "bad" in url:
            return dc
        return res

    with patch("scrapers.socks_proxy.socks5_connect_ok", return_value=False), patch(
        "scrapers.socks_proxy.http_proxy_endpoint_ok", return_value=True
    ), patch("scrapers.cf_browser.check_exit_ip", side_effect=_exit), patch(
        "scrapers.socks_proxy._office_socks_candidates", return_value=[]
    ):
        url, source = resolve_residential_proxy(
            scraper, require=False, max_ape_attempts=3
        )
    assert source == "ape"
    assert "good" in url
    scraper.record_proxy_failure.assert_called()


def test_validate_rejects_datacenter_http():
    dc = {
        "ok": True,
        "ip": "1.2.3.4",
        "org": "Hetzner",
        "country": "DE",
        "residential_likely": False,
    }
    with patch("scrapers.socks_proxy.http_proxy_endpoint_ok", return_value=True), patch(
        "scrapers.cf_browser.check_exit_ip", return_value=dc
    ):
        ok, info = validate_residential_proxy("http://w:p@h:8000")
    assert ok is False
    assert info.get("org") == "Hetzner"


def test_ape_get_next_proxy_residential_only_skips_stormsia():
    from scrapers.proxy_engine import AutonomousProxyEngine

    ape = AutonomousProxyEngine(
        {
            "warren_enabled": False,
            "s5w2c_enabled": False,
            "stormsia_enabled": True,
        }
    )
    with patch.object(
        ape.stormsia_manager, "next_proxy", return_value="socks5://9.9.9.9:1080"
    ) as stormsia:
        got = ape.get_next_proxy(prefer_residential=True, residential_only=True)
        stormsia.assert_not_called()
    assert got is None
    # Without residential_only Stormsia is allowed
    with patch.object(
        ape.stormsia_manager, "next_proxy", return_value="socks5://9.9.9.9:1080"
    ):
        got2 = ape.get_next_proxy(prefer_residential=True, residential_only=False)
    assert got2 == "socks5://9.9.9.9:1080"
