"""FL 67-county status doc + Nassau hold (docs/recon/FL_67_STATUS_2026-10-08.md).

* The dated status table lists every registered FL county exactly once, with
  exactly one of the four allowed statuses, and never claims more than the
  Health registry (``SCRAPER_SOURCE_STATES``) says.
* Nassau is an owner hold (broken TLS chain; the old ``verify=False`` parser
  keyed every row on ``History``): fail closed in code and Health, no fetch.
* "no source" rows are stub modules that return nothing without a fetch.
"""
from __future__ import annotations

import importlib
import re
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "recon" / "FL_67_STATUS_2026-10-08.md"
STATUSES = {
    "verified_public",
    "unverified, awaiting Leads Ops smoke",
    "fail_closed",
    "no source",
}
ROW = re.compile(r"^\| (\d+) \| ([^|]+ \(FL\)) \| [\d,]+ \| \*\*([^*]+)\*\* \|", re.M)


def _rows():
    return [(int(r), label.strip(), status) for r, label, status in ROW.findall(DOC.read_text())]


@pytest.fixture()
def no_network(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)


def test_doc_lists_every_fl_county_once_with_one_status():
    from dashboard.extensions import REGISTERED_COUNTIES

    rows = _rows()
    labels = [label for _rank, label, _status in rows]
    from dashboard.extensions import NON_COUNTY_SOURCE_SCOPES

    fl = sorted(label for label in REGISTERED_COUNTIES if label.endswith("(FL)") and label not in NON_COUNTY_SOURCE_SCOPES)
    assert len(fl) == 67
    assert sorted(labels) == fl
    assert [rank for rank, _l, _s in rows] == list(range(1, 68))
    assert {status for _r, _l, status in rows} <= STATUSES


def test_doc_never_claims_more_than_health():
    from dashboard.extensions import scraper_source_state

    for _rank, label, status in _rows():
        if status in {"verified_public", "fail_closed"}:
            assert scraper_source_state(label) == status, label
        else:
            assert scraper_source_state(label) == "unverified", label


def test_doc_counts_match_table():
    text = DOC.read_text()
    rows = _rows()
    for status in STATUSES:
        n = sum(1 for _r, _l, s in rows if s == status)
        assert f"| {status} | {n} |" in text, status


def test_nassau_is_fail_closed_everywhere(no_network):
    from dashboard.extensions import scraper_source_state
    from scrapers.counties.nassau import NassauCountyScraper

    assert NassauCountyScraper.SOURCE_CONTRACT_VALIDATED is False
    assert "TLS chain" in NassauCountyScraper.SOURCE_CONTRACT_REASON
    assert scraper_source_state("Nassau (FL)") == "fail_closed"
    scraper = NassauCountyScraper()
    assert scraper.county_label == "Nassau (FL)"
    assert scraper.scrape() == []


def test_nassau_module_has_no_insecure_or_impersonating_fetch():
    src = (ROOT / "scrapers" / "counties" / "nassau.py").read_text()
    for banned in (
        r"verify\s*=\s*False\s*[,)]",
        r"^\s*(from|import)\s+(curl_cffi|requests|httpx|urllib3)\b",
        r"impersonate\s*=",
        r"\.(get|post)\(",
    ):
        assert not re.search(banned, src, re.M), banned


def test_nassau_hold_evidence_is_recorded():
    import json

    live = json.loads((ROOT / "docs" / "recon" / "live_emitter_evidence.json").read_text())
    rows = [r for r in live["records"] if r["label"] == "Nassau (FL)"]
    assert rows and rows[0]["emitter"] == "hold"
    ev = json.loads((ROOT / "docs" / "recon" / "county_source_contract_evidence.json").read_text())
    nassau = [r for r in ev["records"] if r["state"] == "FL" and r["county_fips"] == "089"]
    assert nassau and nassau[0]["passive_recommendation"] == "fail_closed"


@pytest.mark.parametrize("label", [label for _r, label, s in _rows() if s == "no source"])
def test_no_source_rows_are_stubs_without_fetch(label, no_network):
    name = label.removesuffix(" (FL)").lower().replace(" ", "_").replace(".", "")
    module = importlib.import_module(f"scrapers.counties.{name}")
    src = Path(module.__file__).read_text()
    assert "NO PUBLIC ONLINE ROSTER" in src
    cls = next(
        obj for obj in vars(module).values()
        if isinstance(obj, type) and obj.__module__ == module.__name__ and obj.__name__.endswith("Scraper")
    )
    assert cls().scrape() == []
