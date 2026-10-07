"""Manatee FL has no proxy, SOCKS, APE/Warren or stealth path (2026-10-07).

Owner decision via CoS: Manatee runs only from the Leads Ops home relay's own
residential exit. These tests prove the removed paths are unreachable, both
statically (no imports or calls) and at runtime (every proxy/stealth helper
patched to explode while a full scrape runs with proxy env vars set).
"""
from __future__ import annotations

import ast
import inspect

import pytest

from scrapers.counties import manatee
from scrapers.counties.manatee import ManateeCountyScraper

FORBIDDEN_NAMES = {
    "resolve_residential_proxy", "validate_residential_proxy", "get_socks_proxy_url",
    "require_socks_or_raise", "to_playwright_proxy", "curl_cffi_proxies",
    "launch_cf_browser", "new_stealth_context", "wait_past_cloudflare", "_launch_sync_playwright",
    "record_proxy_success", "record_proxy_failure", "get_sticky_proxy",
}
FORBIDDEN_MODULES = {"scrapers.socks_proxy", "patchright", "patchright.sync_api", "curl_cffi"}
PROXY_ENV = {
    "HTTP_PROXY": "http://198.51.100.1:3128",
    "HTTPS_PROXY": "http://198.51.100.1:3128",
    "ALL_PROXY": "socks5://198.51.100.1:1080",
    "http_proxy": "http://198.51.100.1:3128",
    "SCRAPER_SOCKS_PROXY": "socks5://198.51.100.1:1080",
    "WARREN_PROXY_URL": "http://warren:x@198.51.100.2:8000",
}


def _tree():
    return ast.parse(inspect.getsource(manatee))


def test_manatee_imports_no_proxy_or_stealth_module():
    imported = set()
    names = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not imported & FORBIDDEN_MODULES
    assert not names & FORBIDDEN_NAMES
    # The only cf_browser symbol used is the exit-IP lookup.
    cf_names = {a.name for n in ast.walk(_tree()) if isinstance(n, ast.ImportFrom)
                and n.module == "scrapers.cf_browser" for a in n.names}
    assert cf_names == {"check_exit_ip"}


def test_manatee_never_calls_proxy_or_stealth_helpers():
    called = {
        (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", ""))
        for n in ast.walk(_tree()) if isinstance(n, ast.Call)
    }
    assert not called & FORBIDDEN_NAMES
    for attr in FORBIDDEN_NAMES:
        assert not hasattr(manatee, attr)


def test_browser_env_drops_every_proxy_variable():
    env = manatee.browser_env({**PROXY_ENV, "PATH": "/usr/bin", "no_proxy": "localhost"})
    assert env == {"PATH": "/usr/bin"}


class _Resp:
    status = 200
    headers = {"server": "cloudflare", "content-type": "text/html"}


class _Page:
    def goto(self, url, **kw):
        return _Resp()

    def title(self):
        return "Bookings | Manatee County Sheriff"

    def evaluate(self, js):
        return {
            "has_table": True,
            "headers": ["Booking #", "Last Name", "First Name", "Middle", "Charge", "Arrest Date", "Released"],
            "rows": [{"cells": ["2026012345", "DOE", "JANE", "", "DUI", "10/06/2026 14:35", ""],
                      "href": "/bookings/2026012345", "img": ""}],
            "text": "Showing 1 to 1 of 1 entries", "next_href": "", "max_page": 1,
            "title": "Bookings | Manatee County Sheriff",
        }

    def content(self):
        return ""


class _Browser:
    def __init__(self):
        self.context_kwargs = None

    def new_context(self, **kw):
        self.context_kwargs = kw
        return type("C", (), {"new_page": lambda self: _Page()})()

    def close(self):
        pass


class _Chromium:
    def __init__(self):
        self.launch_kwargs = None
        self.browser = _Browser()

    def launch(self, **kw):
        self.launch_kwargs = kw
        return self.browser


class _PW:
    def __init__(self):
        self.chromium = _Chromium()

    def stop(self):
        pass


def _explode(name):
    def boom(*a, **k):
        raise AssertionError(f"{name} must not be reachable from Manatee")
    return boom


def test_full_scrape_with_proxy_env_set_reaches_no_proxy_or_stealth_path(monkeypatch):
    for k, v in PROXY_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("MANATEE_EGRESS_MODE", raising=False)

    import scrapers.cf_browser as cfb
    import scrapers.socks_proxy as sp
    for mod, attr in [(sp, "resolve_residential_proxy"), (sp, "validate_residential_proxy"),
                      (sp, "get_socks_proxy_url"), (cfb, "launch_cf_browser"),
                      (cfb, "new_stealth_context"), (cfb, "wait_past_cloudflare"),
                      (cfb, "_launch_sync_playwright")]:
        monkeypatch.setattr(mod, attr, _explode(attr))

    # Exit check: must ignore proxy env vars (httpx trust_env=False, no proxy kwarg).
    import httpx
    clients = []

    class _Client:
        def __init__(self, **kw):
            clients.append(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return httpx.Response(200, json={"ip": "198.51.100.7", "org": "AS7922 Comcast Cable",
                                             "country": "US", "city": "Fort Myers"},
                                  request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "Client", _Client)

    pw = _PW()
    import playwright.sync_api as psa
    monkeypatch.setattr(psa, "sync_playwright", lambda: type("S", (), {"start": lambda self: pw})())
    monkeypatch.setattr(manatee.time, "sleep", lambda s: None)

    scraper = ManateeCountyScraper()
    for attr in ("record_proxy_success", "record_proxy_failure", "get_sticky_proxy"):
        monkeypatch.setattr(scraper, attr, _explode(attr), raising=False)

    records = scraper.scrape()

    assert [r.Booking_Number for r in records] == ["2026012345"]
    assert scraper.last_walk_meta["egress_source"] == "direct"
    assert clients and all(c.get("trust_env") is False and "proxy" not in c for c in clients)
    launch = pw.chromium.launch_kwargs
    assert "proxy" not in launch
    assert "--no-proxy-server" in launch["args"]
    assert not {k.lower() for k in launch["env"]} & {k.lower() for k in PROXY_ENV}
    assert pw.chromium.browser.context_kwargs == {}  # stock context, no stealth options


def test_unverified_exit_stops_before_any_browser(monkeypatch):
    import scrapers.cf_browser as cfb
    monkeypatch.setattr(cfb, "check_exit_ip", lambda *a, **k: {
        "ok": True, "ip": "203.0.113.9", "org": "", "country": "", "exit_unverified": True,
        "residential_likely": False})
    monkeypatch.setattr(manatee, "launch_plain_browser", _explode("launch_plain_browser"))
    from scrapers.scraper_resilience import EgressBlocked
    with pytest.raises(EgressBlocked, match="egress_block"):
        ManateeCountyScraper().scrape()
