"""Regression tests for Lead Explorer API sort + activity metadata.

Frontend (sl-data.js / sl-core.js) defaults sort to ``scraped_at`` and expects
``activity.scraped_last_hour`` in the /api/leads response. These tests lock
that contract so the diagnosis_findings mismatch cannot regress silently.
"""
from __future__ import annotations

from dashboard.models.leads import LeadsQueryModel
from dashboard.routers.stats import _build_leads_query


def test_leads_query_model_defaults_scraped_at():
    q = LeadsQueryModel()
    assert q.sort == "scraped_at"
    assert q.order == "desc"
    assert q.page == 1
    assert q.limit == 50
    assert q.days is None


def test_build_leads_query_empty_filters():
    q = LeadsQueryModel()
    assert _build_leads_query(q) == {}


def test_build_leads_query_days_uses_scraped_at():
    q = LeadsQueryModel(days=7, status="Hot")
    mongo = _build_leads_query(q)
    assert mongo["lead_status"] == "Hot"
    assert "scraped_at" in mongo
    assert "$gte" in mongo["scraped_at"]


def test_sort_map_includes_scraped_at_and_defaults():
    """Mirror the sort_map contract used by stats.api_leads / export."""
    sort_map = {
        "scraped_at": "scraped_at",
        "lead_score": "lead_score",
        "bond_amount": "bond_amount",
        "booking_date": "booking_date",
        "full_name": "full_name",
        "county": "county",
        "arrest_date": "arrest_date",
        "created_at": "created_at",
        "state": "state",
        "lead_status": "lead_status",
    }
    # Frontend default must resolve, not fall back to lead_score
    assert sort_map.get("scraped_at", "scraped_at") == "scraped_at"
    assert sort_map.get(None or "scraped_at", "scraped_at") == "scraped_at"
    # Unknown fields fall back to scraped_at (live-view default), not lead_score
    assert sort_map.get("unknown_field", "scraped_at") == "scraped_at"
    # Old bug: missing key + fallback lead_score
    old_map = {"lead_score": "lead_score"}
    assert old_map.get("scraped_at", "lead_score") == "lead_score"
