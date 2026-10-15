"""Marion (SC) has no source host or proxy path; Sarasota evidence matches Health.

2026-10-15 cleanup: Marion (SC) pointed at the Florida Marion jail host and had a
SOCKS proxy path. Sarasota's evidence row said recon_only while Health said
fail_closed.
"""
from __future__ import annotations

import inspect
import json
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def no_network(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)


def test_marion_sc_fail_closed_without_network(no_network, monkeypatch):
    for k in ("MARION_SOCKS_PROXY", "SOCKS_PROXY", "RESIDENTIAL_SOCKS", "HTTPS_PROXY"):
        monkeypatch.setenv(k, "socks5://198.51.100.1:1080")
    from dashboard.extensions import scraper_source_state
    from scrapers.counties_sc.marion import MarionScraper

    s = MarionScraper()
    assert s.SOURCE_CONTRACT_VALIDATED is False
    assert s.SOURCE_CONTRACT_REASON
    assert s.county_label == "Marion (SC)"
    assert scraper_source_state("Marion (SC)") == "fail_closed"
    assert s.scrape() == []


def test_marion_sc_has_no_host_proxy_or_invented_values():
    import scrapers.counties_sc.marion as mod

    code = inspect.getsource(mod)
    body = code.split('"""', 2)[2]  # skip the module docstring that explains the history
    for banned in ("marionso.com", "http://", "https://", "SOCKS", "socks", "proxies",
                   "requests", "verify=False", "hashlib", "md5", "PORTAL_URL"):
        assert banned not in body, banned
    assert not hasattr(mod, "PORTAL_URL")


def test_sarasota_evidence_matches_health_and_matrix():
    from dashboard.extensions import SCRAPER_SOURCE_STATES

    ev = json.loads((ROOT / "docs/recon/county_source_contract_evidence.json").read_text())
    rows = [r for r in ev["records"] if r["state"] == "FL" and r["county_fips"] == "115"]
    assert len(rows) == 1
    assert rows[0]["passive_recommendation"] == "fail_closed"
    assert SCRAPER_SOURCE_STATES["Sarasota (FL)"] == "fail_closed"
    matrix = (ROOT / "docs/recon/COUNTY_SOURCE_CONTRACT_MATRIX.md").read_text()
    assert "| FL | 115 | Sarasota County | OSI + Palmetto | registered | fail_closed |" in matrix
