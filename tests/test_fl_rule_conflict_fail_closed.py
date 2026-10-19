"""Hillsborough (FL) and Marion (FL) fail closed: their only paths break the
no-CAPTCHA (except Broward/Lake) / no-proxy / no-impersonation / no-login rules.

docs/recon/FL_RULE_CONFLICT_FAIL_CLOSED_2026-10-14.md
"""
from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("Hillsborough (FL)", "057", "scrapers.counties.hillsborough", "HillsboroughCountyScraper",
     "https://webapps.hcso.tampa.fl.us/arrestinquiry/"),
    ("Marion (FL)", "083", "scrapers.counties.marion", "MarionCountyScraper",
     "https://jail.marionso.com/"),
]


@pytest.fixture()
def no_network(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)


@pytest.mark.parametrize("label,fips,mod,cls,url", CASES)
def test_fail_closed_in_health_and_code(no_network, monkeypatch, label, fips, mod, cls, url):
    import importlib

    from dashboard.extensions import SCRAPER_SOURCE_STATES, scraper_source_state

    # Credentials present must not reopen the path.
    monkeypatch.setenv("HCSO_EMAIL", "synthetic@example.invalid")
    monkeypatch.setenv("HCSO_PASSWORD", "synthetic")
    assert SCRAPER_SOURCE_STATES[label] == "fail_closed"
    assert scraper_source_state(label) == "fail_closed"
    klass = getattr(importlib.import_module(mod), cls)
    assert klass.SOURCE_CONTRACT_VALIDATED is False
    assert klass.SOURCE_CONTRACT_REASON
    scraper = klass()
    assert scraper.county_label == label
    assert scraper.scrape() == []


@pytest.mark.parametrize("label,fips,mod,cls,url", CASES)
def test_source_guard_closes_every_path(label, fips, mod, cls, url):
    import importlib

    from config.source_guard import fail_closed_reason, label_for_url

    assert label_for_url(url) == label
    assert fail_closed_reason(url=url) == "fail_closed"
    assert fail_closed_reason(label.split(" (")[0], "FL") == "fail_closed"
    klass = getattr(importlib.import_module(mod), cls)
    assert fail_closed_reason(scraper=klass()) == "fail_closed"


@pytest.mark.parametrize("label,fips,mod,cls,url", CASES)
def test_evidence_live_rows_and_matrix_agree(label, fips, mod, cls, url):
    ev = json.loads((ROOT / "docs/recon/county_source_contract_evidence.json").read_text())
    rows = [r for r in ev["records"] if r["state"] == "FL" and r["county_fips"] == fips]
    assert len(rows) == 1 and rows[0]["passive_recommendation"] == "fail_closed"
    assert "FL_RULE_CONFLICT_FAIL_CLOSED_2026-10-14" in rows[0]["evidence_note"]
    live = json.loads((ROOT / "docs/recon/live_emitter_evidence.json").read_text())
    lr = [r for r in live["records"] if r["label"] == label]
    assert len(lr) == 1 and lr[0]["emitter"] == "hold"
    matrix = (ROOT / "docs/recon/COUNTY_SOURCE_CONTRACT_MATRIX.md").read_text()
    assert f"| {label} | fail_closed | hold |" in matrix
    assert (ROOT / "docs/recon/FL_RULE_CONFLICT_FAIL_CLOSED_2026-10-14.md").exists()

