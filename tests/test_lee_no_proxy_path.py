"""Lee FL has no proxy, SOCKS, APE/Warren, Scrapfly or stealth path (2026-10-08).

Owner decision via CoS: Lee's public-api path is plain direct HTTPS only
(``requests``, honest User-Agent, normal DNS, ``trust_env=False``). These
tests prove the removed paths are unreachable, both statically (no imports
or calls) and at runtime (proxy env set, every proxy/stealth helper patched
to explode, while a full scrape still returns rows).
"""
from __future__ import annotations

import ast
import inspect
import json
from types import SimpleNamespace

import pytest

from scrapers.counties import lee
from scrapers.counties.lee import LeeCountyScraper

FORBIDDEN_NAMES = {
    "create_stealth_session", "resolve_residential_proxy", "validate_residential_proxy",
    "get_socks_proxy_url", "require_socks_or_raise", "to_playwright_proxy", "curl_cffi_proxies",
    "launch_cf_browser", "new_stealth_context", "wait_past_cloudflare",
    "record_proxy_success", "record_proxy_failure", "get_sticky_proxy", "get_proxy",
    "lee_api_get", "lee_curl_options", "invalidate_lee_origin_cache",
}
FORBIDDEN_MODULES = {
    "scrapers.socks_proxy", "scrapers.proxy_engine", "scrapers.lee_origin",
    "patchright", "patchright.sync_api", "curl_cffi",
}
FORBIDDEN_STRINGS = {
    "SCRAPFLY_API_KEY", "scrapfly.io", "StealthSession", "prefer_residential",
    "SOCKS_PROXY", "LEE_PREFER_RESIDENTIAL", "LEE_ALLOW_DIRECT", "impersonate",
}
PROXY_ENV = {
    "HTTP_PROXY": "http://198.51.100.1:3128",
    "HTTPS_PROXY": "http://198.51.100.1:3128",
    "ALL_PROXY": "socks5://198.51.100.1:1080",
    "http_proxy": "http://198.51.100.1:3128",
    "SCRAPER_SOCKS_PROXY": "socks5://198.51.100.1:1080",
    "WARREN_PROXY_URL": "http://warren:x@198.51.100.2:8000",
    "SCRAPFLY_API_KEY": "sf-test-key",
    "SOCKS_PROXY": "socks5://198.51.100.1:1080",
}


def _tree():
    return ast.parse(inspect.getsource(lee))


def test_lee_imports_no_proxy_or_stealth_module():
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
    assert "requests" in imported


def test_lee_never_calls_proxy_or_stealth_helpers():
    called = {
        (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", ""))
        for n in ast.walk(_tree()) if isinstance(n, ast.Call)
    }
    assert not called & FORBIDDEN_NAMES
    for attr in FORBIDDEN_NAMES:
        assert not hasattr(lee, attr)


def _code_only() -> str:
    """Module source without docstrings or comments (the docstring names the
    removed paths on purpose)."""
    tree = _tree()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_lee_code_has_no_stealth_or_proxy_strings():
    code = _code_only()
    for token in FORBIDDEN_STRINGS:
        assert token not in code, token
    assert "trust_env = False" in code
    assert lee.LEE_USER_AGENT.startswith("ShamrockLeads/1.0")


def _explode(name):
    def boom(*a, **k):
        raise AssertionError(f"{name} must not be reachable from Lee")
    return boom


class _FakeResp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.headers = {"content-type": "application/json"}
        self.text = json.dumps(payload)
        self._payload = payload

    def json(self):
        return self._payload


def test_full_scrape_with_proxy_env_set_uses_plain_requests(monkeypatch):
    for k, v in PROXY_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("LEE_MAX_PAGES", "1")
    monkeypatch.setenv("LEE_MAX_ENRICH", "0")
    monkeypatch.setenv("LEE_VARIANT_DELAY_S", "0")
    monkeypatch.setenv("LEE_PAGE_DELAY_S", "0")
    monkeypatch.setenv("LEE_DAYS_BACK", "1")

    # Reload lean defaults after env changes.
    import importlib
    import scrapers.lee_rate_limit as rl
    rl.reset_for_tests(state_path="")
    importlib.reload(lee)

    import scrapers.proxy_engine as pe
    import scrapers.socks_proxy as sp
    for mod, attrs in (
        (pe, ("create_stealth_session",)),
        (sp, ("resolve_residential_proxy", "validate_residential_proxy", "get_socks_proxy_url")),
    ):
        for attr in attrs:
            if hasattr(mod, attr):
                monkeypatch.setattr(mod, attr, _explode(attr))

    gets = []
    sessions = []

    class _Sess:
        def __init__(self):
            sessions.append(self)
            self.trust_env = True
            self.proxies = {"http": "leak"}
            self.headers = {}

        def close(self):
            pass

        def get(self, url, params=None, timeout=30, proxies=None):
            gets.append({"url": url, "params": params, "proxies": proxies, "trust_env": self.trust_env})
            assert proxies == {}
            assert self.trust_env is False
            bn = f"{1000000 + len(gets)}"
            return _FakeResp([{
                "id": "12345678901234",
                "bookingNumber": bn,
                "bookingDate": "2026-10-08 12:00:00.000",
                "givenName": "TEST",
                "surName": "PERSON",
                "middleName": "",
                "suffixName": "",
                "inCustody": True,
                "address": "",
                "image": "",
            }])

    monkeypatch.setattr(lee.requests, "Session", _Sess)
    monkeypatch.setattr(lee.time, "sleep", lambda s: None)

    scraper = lee.LeeCountyScraper()
    for attr in ("record_proxy_success", "record_proxy_failure", "get_sticky_proxy", "get_proxy"):
        monkeypatch.setattr(scraper, attr, _explode(attr), raising=False)

    records = scraper.scrape()
    assert records
    assert all(r.Booking_Number.isdigit() for r in records)
    assert gets and all(g["proxies"] == {} and g["trust_env"] is False for g in gets)
    assert all("scrapfly" not in g["url"] for g in gets)
    assert sessions and all(x.headers.get("User-Agent") == lee.LEE_USER_AGENT for x in sessions)
    assert all(x.proxies == {} for x in sessions)


def test_session_ignores_proxy_env_even_when_requests_would(monkeypatch):
    for k, v in PROXY_ENV.items():
        monkeypatch.setenv(k, v)
    scraper = LeeCountyScraper()
    sess = scraper._get_session()
    assert sess.trust_env is False
    assert sess.proxies == {}
    assert sess.headers["User-Agent"] == lee.LEE_USER_AGENT


def _smoke():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "lee_direct_smoke.py"
    spec = importlib.util.spec_from_file_location("lee_direct_smoke", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_direct_smoke_exit_codes_and_shapes():
    s = _smoke()
    assert s.shape("1234567") == "NNNNNNN" and s.shape("AB12") == "AANN"
    assert s.classify_page(200, "application/json", "[]") == "ok"
    assert s.classify_page(403, "text/html", "") == "blocked"
    assert s.classify_page(200, "text/html", "<title>Just a moment...</title>") == "blocked"
    assert s.classify_page(200, "text/html", "<html></html>") == "drift"
    ok = [{"state": "ok"}]
    assert s.evaluate(ok, {"inCustody": ["1000001"], "date_range": ["1000001"]}) == ("ok", 0)
    assert s.evaluate([{"state": "blocked"}], {"inCustody": []}) == ("blocked", 2)
    assert s.evaluate([{"state": "no_response"}], {}) == ("blocked", 2)
    assert s.evaluate(ok, {"inCustody": []}) == ("empty", 3)
    assert s.evaluate(ok, {"inCustody": ["1000001", "1000001"]}) == ("drift", 3)
    assert s.evaluate(ok, {"inCustody": ["A100"]}) == ("drift", 3)
    assert s.evaluate(ok, {"inCustody": [""]}) == ("drift", 3)



def test_direct_smoke_isolates_cooldown_unless_overridden():
    s = _smoke()
    env = {"PATH": "/usr/bin"}
    assert s.isolate_cooldown_state(env) == "memory_only"
    assert env["LEE_RATE_LIMIT_PERSIST"] == "false"
    assert env["LEE_RATE_LIMIT_STATE_PATH"] == ""
    env2 = {"LEE_RATE_LIMIT_PERSIST": "true", "LEE_RATE_LIMIT_STATE_PATH": "/tmp/keep.json"}
    assert s.isolate_cooldown_state(env2) == "caller_override"
    assert env2["LEE_RATE_LIMIT_PERSIST"] == "true"
