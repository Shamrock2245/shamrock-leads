"""
Regression tests for Codex PR review findings (automation sweeps + court dates).

Covers:
  P1 — Court email scan must use sync PyMongo (not Motor) — tested via source contract
  P2 — Bond lifecycle sweep queries active_bonds
  P2 — Court email date formats parse for reminder scheduling
  P2 — Discharge report applies days_back to queries
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dashboard.services.court_reminder_service import parse_court_date_string
from dashboard.services.court_email_scheduler import CourtEmailScheduler


ROOT = Path(__file__).resolve().parents[1]
SWEEPS = ROOT / "dashboard" / "routers" / "automation_sweeps.py"


class TestCourtDateParsing:
    def test_iso_with_offset(self):
        dt = parse_court_date_string("2026-05-15T09:00:00+00:00")
        assert dt is not None
        assert dt.year == 2026 and dt.month == 5 and dt.day == 15
        assert dt.hour == 9

    def test_court_email_us_format(self):
        """Codex P2: CourtEmailProcessor yields 05/15/2026 + 09:00 AM."""
        dt = parse_court_date_string("05/15/2026 09:00 AM")
        assert dt is not None
        assert dt == datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc)

    def test_court_email_compact_am(self):
        dt = parse_court_date_string("05/15/2026 9:00AM")
        assert dt is not None
        assert dt.hour == 9 and dt.minute == 0

    def test_date_only_defaults_morning(self):
        dt = parse_court_date_string("05/15/2026")
        assert dt is not None
        assert dt.hour == 9

    def test_long_form_month(self):
        dt = parse_court_date_string("May 15, 2026 09:00 AM")
        assert dt is not None
        assert dt.month == 5 and dt.day == 15

    def test_invalid_returns_none(self):
        assert parse_court_date_string("") is None
        assert parse_court_date_string("not-a-date") is None

    def test_scheduler_parse_matches_email_processor_output(self):
        dt = CourtEmailScheduler._parse_court_datetime("05/15/2026", "09:00 AM")
        assert dt is not None
        assert dt.isoformat().startswith("2026-05-15T09:00:00")
        # Must be ISO-parseable by fromisoformat (original failure mode)
        roundtrip = datetime.fromisoformat(dt.isoformat())
        assert roundtrip.year == 2026


class TestAutomationSweepsSourceContracts:
    """Static contracts so we don't reintroduce Motor / wrong collection bugs."""

    def _source(self) -> str:
        return SWEEPS.read_text(encoding="utf-8")

    def test_court_email_scan_uses_sync_pymongo(self):
        src = self._source()
        # Must construct pymongo.MongoClient for CourtEmailScheduler
        assert "from pymongo import MongoClient" in src
        assert "CourtEmailScheduler(db=db)" in src
        # Must NOT pass Motor client into the scheduler for this route
        court_fn = self._extract_function_source("court_email_scan")
        assert "get_mongo_client" not in court_fn
        assert "MongoClient" in court_fn

    def test_bond_lifecycle_queries_active_bonds(self):
        fn = self._extract_function_source("bond_lifecycle_sweep")
        assert 'get_collection("active_bonds")' in fn
        assert 'get_collection("bonds")' not in fn

    def test_discharge_report_applies_days_back(self):
        fn = self._extract_function_source("generate_discharge_report")
        assert "days_back" in fn
        assert "since" in fn
        # Query must constrain by the computed window
        assert '"$gte": since' in fn or "'$gte': since" in fn
        assert "date_window" in fn or "updated_at" in fn

    def _extract_function_source(self, name: str) -> str:
        src = self._source()
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return ast.get_source_segment(src, node) or ""
        # Decorated async routes still FunctionDef under decorator
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
                return ast.get_source_segment(src, node) or ""
        pytest.fail(f"function {name} not found in automation_sweeps.py")
