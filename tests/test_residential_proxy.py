"""Tests for APE-first residential proxy resolution."""

from unittest.mock import MagicMock, patch

from scrapers.socks_proxy import (
    resolve_residential_proxy,
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


def test_resolve_require_false_when_nothing_available():
    url, source = resolve_residential_proxy(None, require=False)
    assert source in ("none", "env_socks", "office_socks", "ape", "direct")
    if source == "none":
        assert url is None
    if source == "direct":
        assert url is None


def test_resolve_uses_ape_http_proxy_when_endpoint_ok():
    scraper = MagicMock()
    scraper.get_proxy.return_value = "http://user:pass@proxy.example:8080"
    scraper.get_sticky_proxy.return_value = None
    residential = {
        "ok": True,
        "ip": "73.1.2.3",
        "org": "Comcast",
        "country": "US",
        "residential_likely": True,
    }
    with patch("scrapers.socks_proxy.socks5_connect_ok", return_value=False), patch(
        "scrapers.socks_proxy.http_proxy_endpoint_ok", return_value=True
    ), patch("scrapers.cf_browser.check_exit_ip", return_value=residential):
        url, source = resolve_residential_proxy(scraper, require=False)
    assert source == "ape"
    assert url.startswith("http://")
    scraper.get_proxy.assert_called()


def test_resolve_sticky_session_preferred():
    scraper = MagicMock()
    scraper.get_sticky_proxy.return_value = "http://warren:pw@hub:8000"
    scraper.get_proxy.return_value = "http://other:pw@hub:8000"
    residential = {
        "ok": True,
        "ip": "73.1.2.3",
        "org": "Comcast",
        "country": "US",
        "residential_likely": True,
    }
    with patch("scrapers.socks_proxy.socks5_connect_ok", return_value=False), patch(
        "scrapers.socks_proxy.http_proxy_endpoint_ok", return_value=True
    ), patch("scrapers.cf_browser.check_exit_ip", return_value=residential):
        url, source = resolve_residential_proxy(
            scraper, require=False, sticky_session="fl-charlotte"
        )
    assert source == "ape"
    scraper.get_sticky_proxy.assert_called_once_with("fl-charlotte")
