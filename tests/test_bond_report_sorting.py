"""
Bond report chronological sort + date-window handling (2012 → present).

Covers:
  - sort_bonds_chronologically: mixed ISO / datetime / missing dates, never raises
  - parse_report_date_window / mongo_bond_date_filter: clamp, swap, invalid
  - POST /api/automation/bond-report: date warnings, sort_order, no crash on bad dates
  - GET /api/reports/surety-liability: start/end pass-through + 2012 clamp warnings
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.services.bond_report_xlsx import (
    REPORT_EPOCH,
    REPORT_EPOCH_ISO,
    mongo_bond_date_filter,
    parse_report_date_window,
    sort_bonds_chronologically,
)


# ── sort_bonds_chronologically ──────────────────────────────────────────────


def test_sort_bonds_oldest_first_mixed_date_types():
    records = [
        {"power_number": "C", "bond_date": "2024-06-15", "defendant_name": "Charlie"},
        {"power_number": "A", "bond_date": datetime(2015, 3, 1), "defendant_name": "Alice"},
        {"power_number": "B", "bond_date": "2020-01-10T14:30:00", "defendant_name": "Bob"},
        {"power_number": "Z", "bond_date": "2012-01-01", "defendant_name": "Zed"},
    ]
    sorted_rows = sort_bonds_chronologically(records)
    powers = [r["power_number"] for r in sorted_rows]
    assert powers == ["Z", "A", "B", "C"]


def test_sort_bonds_undated_trail_dated_rows():
    records = [
        {"power_number": "2", "bond_date": "2019-05-01"},
        {"power_number": "undated-a", "bond_date": None},
        {"power_number": "1", "bond_date": "2018-01-01"},
        {"power_number": "undated-b", "bond_date": "not-a-date"},
        {"power_number": "3", "date_executed": "2021-12-31"},
    ]
    sorted_rows = sort_bonds_chronologically(records)
    powers = [r["power_number"] for r in sorted_rows]
    assert powers[:3] == ["1", "2", "3"]
    assert set(powers[3:]) == {"undated-a", "undated-b"}


def test_sort_bonds_tz_aware_and_naive_do_not_crash():
    aware = datetime(2022, 4, 1, tzinfo=timezone.utc)
    naive = datetime(2021, 4, 1)
    records = [
        {"power_number": "aware", "bond_date": aware},
        {"power_number": "naive", "bond_date": naive},
        {"power_number": "iso", "posted_date": "2020-01-15"},
    ]
    sorted_rows = sort_bonds_chronologically(records)
    assert [r["power_number"] for r in sorted_rows] == ["iso", "naive", "aware"]


def test_sort_bonds_never_raises_on_garbage():
    garbage = [
        None,
        "not-a-dict",
        42,
        {"power_number": "ok", "bond_date": "2016-07-04"},
        {"power_number": "bad", "bond_date": object()},
    ]
    # Non-dicts filtered; dicts retained; never raises
    result = sort_bonds_chronologically(garbage)  # type: ignore[arg-type]
    assert isinstance(result, list)
    assert any(r.get("power_number") == "ok" for r in result)
    assert any(r.get("power_number") == "bad" for r in result)


def test_sort_bonds_empty_and_none():
    assert sort_bonds_chronologically([]) == []
    assert sort_bonds_chronologically(None) == []  # type: ignore[arg-type]


def test_sort_bonds_stable_tiebreak_by_power():
    records = [
        {"power_number": "B-2", "bond_date": "2020-01-01", "defendant_name": "Z"},
        {"power_number": "A-1", "bond_date": "2020-01-01", "defendant_name": "A"},
        {"power_number": "A-2", "bond_date": "2020-01-01", "defendant_name": "M"},
    ]
    sorted_rows = sort_bonds_chronologically(records)
    assert [r["power_number"] for r in sorted_rows] == ["A-1", "A-2", "B-2"]


# ── parse_report_date_window / mongo filter ──────────────────────────────────


def test_parse_window_invalid_date_warning_not_crash():
    start, end, warnings = parse_report_date_window("not-a-date", "2020-12-31")
    assert start is None
    assert end == datetime(2020, 12, 31)
    assert any("not a valid" in w for w in warnings)


def test_parse_window_pre_2012_clamped():
    start, end, warnings = parse_report_date_window("2008-05-01", "2015-06-01")
    assert start == REPORT_EPOCH
    assert end == datetime(2015, 6, 1)
    assert any("clamped" in w for w in warnings)
    assert REPORT_EPOCH_ISO in warnings[0]


def test_parse_window_swapped_range():
    start, end, warnings = parse_report_date_window("2020-12-31", "2018-01-01")
    assert start == datetime(2018, 1, 1)
    assert end == datetime(2020, 12, 31)
    assert any("swapped" in w for w in warnings)


def test_parse_window_empty_ok():
    start, end, warnings = parse_report_date_window(None, None)
    assert start is None and end is None and warnings == []


def test_mongo_bond_date_filter_uses_yyyy_mm_dd():
    """End bound is exclusive next-day so ISO timestamps on end day still match."""
    filt, warnings = mongo_bond_date_filter("2015-01-01", "2016-12-31")
    assert filt == {"bond_date": {"$gte": "2015-01-01", "$lt": "2017-01-01"}}
    assert warnings == []
    # Pure $lte "2016-12-31" would exclude "2016-12-31T15:00:00" (intake ISO form)
    assert "2016-12-31T15:00:00" < filt["bond_date"]["$lt"]
    assert "2016-12-31" < filt["bond_date"]["$lt"]


def test_mongo_bond_date_filter_clamp_pre_2012():
    filt, warnings = mongo_bond_date_filter("2010-01-01", None)
    assert filt["bond_date"]["$gte"] == REPORT_EPOCH_ISO
    assert any("clamped" in w for w in warnings)


# ── API: automation bond-report ─────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *args, **kwargs):
        return self

    async def to_list(self, n):
        return list(self._docs)[:n]


@pytest.fixture
def automation_app(monkeypatch):
    monkeypatch.setenv("GAS_API_KEY", "test-bond-report-key")
    from dashboard.routers.automation_sweeps import automation_bp

    app = FastAPI()
    app.include_router(automation_bp)
    return app


def _mock_bonds_col(active_docs=None, void_docs=None, discharge_docs=None):
    active_docs = active_docs or []
    void_docs = void_docs or []
    discharge_docs = discharge_docs or []
    col = MagicMock()

    def find(query, *args, **kwargs):
        status = query.get("status")
        if isinstance(status, dict) and "$in" in status:
            vals = status["$in"]
            if "void" in vals or "voided" in vals:
                return _FakeCursor(void_docs)
            if "exonerated" in vals:
                return _FakeCursor(discharge_docs)
        return _FakeCursor(active_docs)

    col.find.side_effect = find
    return col


@patch("dashboard.routers.automation_sweeps.get_collection")
def test_bond_report_api_invalid_date_returns_warnings(mock_get_col, automation_app):
    bonds = _mock_bonds_col(
        active_docs=[
            {
                "power_number": "P1",
                "bond_date": "2015-06-01",
                "bond_amount": 5000,
                "surety": "OSI",
                "status": "active",
                "defendant_name": "Test Defendant",
            }
        ]
    )
    reports_col = AsyncMock()
    reports_col.insert_one = AsyncMock()

    def side_effect(name):
        if name == "active_bonds":
            return bonds
        if name == "generated_reports":
            return reports_col
        return AsyncMock()

    mock_get_col.side_effect = side_effect

    client = TestClient(automation_app)
    resp = client.post(
        "/api/automation/bond-report",
        headers={"X-API-Key": "test-bond-report-key"},
        json={
            "surety": "OSI",
            "store": False,
            "start_date": "bogus",
            "end_date": "2016-12-31",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["sort_order"].startswith("bond_date ascending")
    assert data["end_date"] == "2016-12-31"
    assert data["start_date"] is None
    assert data["warnings"]
    assert any("not a valid" in w for w in data["warnings"])
    # PII-free diagnostics: no defendant names in warnings / error payloads
    blob = str(data["warnings"])
    assert "Defendant" not in blob
    assert "Test" not in blob


@patch("dashboard.routers.automation_sweeps.get_collection")
def test_bond_report_api_pre_2012_clamped(mock_get_col, automation_app):
    bonds = _mock_bonds_col(
        active_docs=[
            {
                "power_number": "P2",
                "bond_date": "2013-01-01",
                "bond_amount": 1000,
                "surety": "OSI",
                "status": "active",
            }
        ]
    )
    mock_get_col.side_effect = lambda name: bonds if name == "active_bonds" else AsyncMock()

    client = TestClient(automation_app)
    resp = client.post(
        "/api/automation/bond-report",
        headers={"X-API-Key": "test-bond-report-key"},
        json={
            "surety": "OSI",
            "store": False,
            "start_date": "2005-01-01",
            "end_date": "2014-12-31",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["start_date"] == REPORT_EPOCH_ISO
    assert data["end_date"] == "2014-12-31"
    assert any("clamped" in w for w in (data["warnings"] or []))


@patch("dashboard.routers.automation_sweeps.get_collection")
def test_bond_report_api_swapped_range_corrected(mock_get_col, automation_app):
    bonds = _mock_bonds_col()
    mock_get_col.side_effect = lambda name: bonds if name == "active_bonds" else AsyncMock()

    client = TestClient(automation_app)
    resp = client.post(
        "/api/automation/bond-report",
        headers={"X-API-Key": "test-bond-report-key"},
        json={
            "surety": "PALMETTO",
            "store": False,
            "include_discharges": False,
            "start_date": "2020-12-31",
            "end_date": "2019-01-01",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["start_date"] == "2019-01-01"
    assert data["end_date"] == "2020-12-31"
    assert any("swapped" in w for w in (data["warnings"] or []))


# ── API: reports surety-liability date pass-through ─────────────────────────


@pytest.fixture
def reports_app():
    from dashboard.routers.reports import reports_bp

    app = FastAPI()
    app.include_router(reports_bp)
    return app


@patch("dashboard.routers.reports.get_db")
def test_surety_liability_date_clamp_and_sort_order(mock_get_db, reports_app):
    docs = [
        {
            "poa_number": "NEW",
            "bond_date": "2024-01-15",
            "bond_amount": 10000,
            "surety": "OSI",
            "status": "active",
            "defendant_name": "Newer Bond",
        },
        {
            "poa_number": "OLD",
            "bond_date": "2015-03-01",
            "bond_amount": 2500,
            "surety": "OSI",
            "status": "active",
            "defendant_name": "Older Bond",
        },
    ]
    col = MagicMock()
    col.find.return_value = _FakeCursor(docs)
    mock_get_db.return_value = {"active_bonds": col}

    client = TestClient(reports_app)
    resp = client.get(
        "/api/reports/surety-liability",
        params={"start_date": "2009-01-01", "end_date": "2025-12-31"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["start_date"] == REPORT_EPOCH_ISO
    assert data["end_date"] == "2025-12-31"
    assert data["sort_order"].startswith("bond_date ascending")
    assert any("clamped" in w for w in (data["warnings"] or []))
    # Mongo query used date-only strings (not full ISO timestamps)
    call_args = col.find.call_args
    query = call_args[0][0]
    assert query["bond_date"]["$gte"] == REPORT_EPOCH_ISO
    # Inclusive end day → exclusive $lt next calendar day
    assert query["bond_date"]["$lt"] == "2026-01-01"
    # No year-cap: range includes multi-year span through 2025
    assert query["bond_date"]["$lt"] > "2025-12-31"


@patch("dashboard.routers.reports.get_db")
def test_surety_liability_invalid_start_ignored(mock_get_db, reports_app):
    col = MagicMock()
    col.find.return_value = _FakeCursor([])
    mock_get_db.return_value = {"active_bonds": col}

    client = TestClient(reports_app)
    resp = client.get(
        "/api/reports/surety-liability",
        params={"start_date": "xx", "end_date": "2020-06-01"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["start_date"] is None
    assert data["end_date"] == "2020-06-01"
    assert any("not a valid" in w for w in (data["warnings"] or []))
