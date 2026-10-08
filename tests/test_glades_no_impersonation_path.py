"""Glades FL has no curl_cffi impersonation, proxy or stealth path (2026-10-08).

CoS decision: Glades reads the SmartWEB JAIL View over plain ``requests`` via
the shared ``scrapers/fl_smartweb.py`` helper, or goes fail_closed; it never
keeps TLS impersonation. Static (imports / names) and runtime (a scrape with
proxy env vars set uses only ``requests.Session``) checks. Synthetic data only.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from scrapers import fl_smartweb
from scrapers.counties import glades
from scrapers.counties.glades import GladesCountyScraper

FORBIDDEN_MODULES = {"curl_cffi", "curl_cffi.requests", "patchright", "patchright.sync_api",
                     "scrapers.socks_proxy", "scrapers.proxy_engine", "playwright", "playwright.sync_api"}
FORBIDDEN_TEXT = ("impersonate", "chrome131", "create_stealth_session", "get_proxy", "residential")


def _imports(module):
    found = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("module", [glades, fl_smartweb])
def test_no_impersonation_proxy_or_stealth_import(module):
    assert not (_imports(module) & FORBIDDEN_MODULES)


def test_glades_code_names_no_impersonation_or_proxy():
    src = inspect.getsource(glades)
    code = src.split('"""', 2)[2]  # after the module docstring
    for text in FORBIDDEN_TEXT:
        assert text not in code, text


def test_glades_scrape_uses_plain_requests_even_with_proxy_env(monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(k, "http://198.51.100.1:3128")
    calls = {}

    def fake(**kw):
        calls.update(kw)
        return []

    monkeypatch.setattr(glades, "scrape_smartweb_jail_view", fake)
    assert GladesCountyScraper().scrape() == []
    assert calls["base_url"] == glades.BASE_URL and calls["lookback_days"] == glades.LOOKBACK_DAYS
    assert fl_smartweb.requests.__name__ == "requests"
