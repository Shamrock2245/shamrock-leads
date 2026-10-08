"""Pinellas (FL) fail closed: plain requests get only the Blazor Server shell.

docs/recon/FL_PINELLAS_FAIL_CLOSED_2026-10-08.md. A plain-requests read
returned no rows, and the patchright browser path is not allowed (no stealth).
"""
from __future__ import annotations

import builtins
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


@pytest.fixture()
def no_browser(monkeypatch):
    real_import = builtins.__import__

    def _guard(name, *a, **k):
        if name.split(".")[0] in {"patchright", "playwright", "DrissionPage", "undetected_chromedriver", "selenium"}:
            raise AssertionError(f"browser import attempted: {name}")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _guard)


def test_pinellas_is_fail_closed_in_health_and_code():
    from dashboard.extensions import SCRAPER_SOURCE_STATES, scraper_source_state
    from scrapers.counties.pinellas import PinellasCountyScraper

    assert SCRAPER_SOURCE_STATES["Pinellas (FL)"] == "fail_closed"
    assert scraper_source_state("Pinellas (FL)") == "fail_closed"
    assert PinellasCountyScraper.SOURCE_CONTRACT_VALIDATED is False
    assert "Blazor" in PinellasCountyScraper.SOURCE_CONTRACT_REASON


def test_pinellas_scrape_refuses_without_browser_or_network(no_network, no_browser):
    from scrapers.counties.pinellas import PinellasCountyScraper

    assert PinellasCountyScraper().scrape() == []


def test_pinellas_parser_contract_is_kept():
    from scrapers.counties.pinellas import PinellasCountyScraper as P

    assert P._format_bond_amount(None) == ""
    assert P._format_bond_amount("") == ""


def test_pinellas_hold_and_evidence_rows_agree():
    live = json.loads((ROOT / "docs" / "recon" / "live_emitter_evidence.json").read_text())
    rows = [r for r in live["records"] if r["label"] == "Pinellas (FL)"]
    assert len(rows) == 1 and rows[0]["emitter"] == "hold"
    ev = json.loads((ROOT / "docs" / "recon" / "county_source_contract_evidence.json").read_text())
    rec = [r for r in ev["records"] if r["state"] == "FL" and r["county_fips"] == "103"]
    assert len(rec) == 1 and rec[0]["passive_recommendation"] == "fail_closed"
    assert "Blazor" in rec[0]["evidence_note"]
    matrix = (ROOT / "docs" / "recon" / "COUNTY_SOURCE_CONTRACT_MATRIX.md").read_text()
    assert "| Pinellas (FL) | fail_closed | hold |" in matrix
    assert (ROOT / "docs" / "recon" / "FL_PINELLAS_FAIL_CLOSED_2026-10-08.md").exists()


def test_pinellas_drops_out_of_the_sellable_seed():
    from dashboard.services.lead_subscriptions import sellable_counties, shamrock_seed

    assert "Pinellas (FL)" not in {r["label"] for r in sellable_counties()}
    assert "Pinellas" not in {r["county"] for r in shamrock_seed()}
