"""Charlotte FL has no proxy, SOCKS, APE/Warren or stealth path (2026-10-07).

Same cleanup as Manatee #121: Charlotte runs only from the Leads Ops home
relay's own residential exit with stock Playwright. These tests prove the
removed paths are unreachable, statically (no imports or calls in
charlotte.py or the shared revize_roster.py) and at runtime (every
proxy/stealth helper patched to explode while a full scrape runs with proxy
env vars set).
"""
from __future__ import annotations

import ast
import inspect

import pytest

from scrapers import revize_roster
from scrapers.counties import charlotte
from scrapers.counties.charlotte import CharlotteCountyScraper

FORBIDDEN_NAMES = {
    "resolve_residential_proxy", "validate_residential_proxy", "get_socks_proxy_url",
    "require_socks_or_raise", "to_playwright_proxy", "curl_cffi_proxies",
    "launch_cf_browser", "new_stealth_context", "wait_past_cloudflare", "_launch_sync_playwright",
    "record_proxy_success", "record_proxy_failure", "get_sticky_proxy", "get_proxy",
}
FORBIDDEN_MODULES = {"scrapers.socks_proxy", "scrapers.proxy_engine", "patchright", "patchright.sync_api", "curl_cffi"}
PROXY_ENV = {
    "HTTP_PROXY": "http://198.51.100.1:3128",
    "HTTPS_PROXY": "http://198.51.100.1:3128",
    "ALL_PROXY": "socks5://198.51.100.1:1080",
    "https_proxy": "http://198.51.100.1:3128",
    "SCRAPER_SOCKS_PROXY": "socks5://198.51.100.1:1080",
    "WARREN_PROXY_URL": "http://warren:x@198.51.100.2:8000",
}


@pytest.mark.parametrize("module", [charlotte, revize_roster])
def test_no_proxy_or_stealth_imports_or_calls(module):
    tree = ast.parse(inspect.getsource(module))
    imported, names, called = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Call):
            called.add(node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", ""))
    assert not imported & FORBIDDEN_MODULES
    assert not names & FORBIDDEN_NAMES
    assert not called & FORBIDDEN_NAMES
    cf_names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                and n.module == "scrapers.cf_browser" for a in n.names}
    assert cf_names <= {"check_exit_ip"}


def test_only_direct_egress_mode(monkeypatch):
    monkeypatch.delenv("CHARLOTTE_EGRESS_MODE", raising=False)
    assert charlotte.egress_mode() == "direct"
    assert revize_roster.EGRESS_MODES == ("direct",)


class _Resp:
    status = 200
    headers = {"server": "cloudflare", "content-type": "text/html"}


class _Page:
    def goto(self, url, **kw):
        return _Resp()

    def title(self):
        return "Bookings | Charlotte County Sheriff"

    def evaluate(self, js):
        return {
            "has_table": True,
            "headers": ["Booking #", "Last Name", "First Name", "Middle", "Charge", "Arrest Date", "Released"],
            "rows": [{"cells": ["2026054321", "DOE", "JANE", "", "DUI", "10/06/2026 14:35", ""],
                      "href": "/bookings/2026054321", "img": ""}],
            "text": "Showing 1 to 1 of 1 entries", "next_href": "", "max_page": 1,
            "title": "Bookings | Charlotte County Sheriff",
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
        raise AssertionError(f"{name} must not be reachable from Charlotte")
    return boom


def test_full_scrape_with_proxy_env_set_reaches_no_proxy_or_stealth_path(monkeypatch):
    for k, v in PROXY_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CHARLOTTE_EGRESS_MODE", raising=False)

    import scrapers.socks_proxy as sp
    for attr in ("resolve_residential_proxy", "validate_residential_proxy", "get_socks_proxy_url"):
        monkeypatch.setattr(sp, attr, _explode(attr))

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
                                             "country": "US", "city": "Port Charlotte"},
                                  request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "Client", _Client)

    pw = _PW()
    import playwright.sync_api as psa
    monkeypatch.setattr(psa, "sync_playwright", lambda: type("S", (), {"start": lambda self: pw})())
    monkeypatch.setattr(revize_roster.time, "sleep", lambda s: None)

    scraper = CharlotteCountyScraper()
    for attr in ("record_proxy_success", "record_proxy_failure", "get_sticky_proxy", "get_proxy"):
        monkeypatch.setattr(scraper, attr, _explode(attr), raising=False)

    records = scraper.scrape()

    assert [r.Booking_Number for r in records] == ["2026054321"]
    assert records[0].Bond_Amount == ""  # roster publishes no bond: unknown, never "0"
    assert scraper.last_walk_meta["egress_source"] == "direct"
    assert clients and all(c.get("trust_env") is False and "proxy" not in c for c in clients)
    launch = pw.chromium.launch_kwargs
    assert "proxy" not in launch and launch["headless"] is True
    assert "--no-proxy-server" in launch["args"]
    assert not {k.lower() for k in launch["env"]} & {k.lower() for k in PROXY_ENV}
    assert pw.chromium.browser.context_kwargs == {}  # stock context, no stealth options


def test_unverified_exit_stops_before_any_browser(monkeypatch):
    import scrapers.cf_browser as cfb
    monkeypatch.setattr(cfb, "check_exit_ip", lambda *a, **k: {
        "ok": True, "ip": "203.0.113.9", "org": "", "country": "", "exit_unverified": True,
        "residential_likely": False})
    monkeypatch.setattr(charlotte, "launch_plain_browser", _explode("launch_plain_browser"))
    from scrapers.scraper_resilience import EgressBlocked
    with pytest.raises(EgressBlocked, match="egress_block"):
        CharlotteCountyScraper().scrape()
