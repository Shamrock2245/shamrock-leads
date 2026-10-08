"""Hendry (FL) fail closed: the only id on the public feed is a person id (MNI).

docs/recon/FL_HENDRY_FAIL_CLOSED_2026-10-08.md. The MyOCV inmates.json keys
every row on ``inmateID`` = ``HCSO<YY>MNI<NNNNNN>``, a Master Name Index
(person) id, not a booking number. CoS approved fail_closed on 2026-10-08.
"""
from __future__ import annotations

import json
import re
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


def test_hendry_is_fail_closed_in_health_and_code(no_network):
    from dashboard.extensions import KEY_FL_COUNTY_LABELS, SCRAPER_SOURCE_STATES, scraper_source_state
    from scrapers.counties.hendry import HendryCountyScraper

    assert SCRAPER_SOURCE_STATES["Hendry (FL)"] == "fail_closed"
    assert scraper_source_state("Hendry (FL)") == "fail_closed"
    assert "Hendry (FL)" in KEY_FL_COUNTY_LABELS  # still a home county, just not emitting
    assert HendryCountyScraper.SOURCE_CONTRACT_VALIDATED is False
    assert "MNI" in HendryCountyScraper.SOURCE_CONTRACT_REASON
    scraper = HendryCountyScraper()
    assert scraper.county_label == "Hendry (FL)"
    assert scraper.scrape() == []


def test_hendry_module_has_no_fetch_or_impersonation():
    src = (ROOT / "scrapers" / "counties" / "hendry.py").read_text()
    for banned in (
        r"^\s*(from|import)\s+(curl_cffi|requests|httpx|urllib3)\b",
        r"impersonate\s*=",
        r"verify\s*=\s*False",
        r"\.(get|post)\(",
        r"Booking_Number\s*=",
    ):
        assert not re.search(banned, src, re.M), banned


def test_hendry_hold_and_evidence_rows_agree():
    live = json.loads((ROOT / "docs" / "recon" / "live_emitter_evidence.json").read_text())
    rows = [r for r in live["records"] if r["label"] == "Hendry (FL)"]
    assert len(rows) == 1 and rows[0]["emitter"] == "hold"
    ev = json.loads((ROOT / "docs" / "recon" / "county_source_contract_evidence.json").read_text())
    hendry = [r for r in ev["records"] if r["state"] == "FL" and r["county_fips"] == "051"]
    assert len(hendry) == 1 and hendry[0]["passive_recommendation"] == "fail_closed"
    assert "MNI" in hendry[0]["evidence_note"]
    matrix = (ROOT / "docs" / "recon" / "COUNTY_SOURCE_CONTRACT_MATRIX.md").read_text()
    assert "| Hendry (FL) | fail_closed | hold |" in matrix
    assert (ROOT / "docs" / "recon" / "FL_HENDRY_FAIL_CLOSED_2026-10-08.md").exists()


def test_hendry_drops_out_of_the_sellable_seed():
    from dashboard.services.lead_subscriptions import sellable_counties, shamrock_seed

    assert "Hendry (FL)" not in {r["label"] for r in sellable_counties()}
    assert "Hendry" not in {r["county"] for r in shamrock_seed()}
    assert "Lee" in {r["county"] for r in shamrock_seed()}
