"""Revenue automation defaults + review-mode safety."""
from __future__ import annotations

from pathlib import Path

from dashboard.services.automation_config import DEFAULT_CONFIG, _deep_merge_defaults
from dashboard.services.automation_schedule import NODE_RED_SCHEDULE


def test_revenue_defaults_enabled_in_review():
    for key in ("speed_to_contact", "paperwork_chase", "intake_recovery"):
        sec = DEFAULT_CONFIG[key]
        assert sec["enabled"] is True
        assert sec.get("mode") == "review"


def test_speed_to_contact_never_defaults_full_auto():
    assert DEFAULT_CONFIG["speed_to_contact"]["mode"] != "full_auto"


def test_deep_merge_preserves_operator_disable():
    stored = {
        "type": "automation_master",
        "speed_to_contact": {"enabled": False, "mode": "review"},
    }
    merged = _deep_merge_defaults(stored, DEFAULT_CONFIG)
    assert merged["speed_to_contact"]["enabled"] is False
    # Fills missing nested keys from defaults
    assert "min_lead_score" in merged["speed_to_contact"]


def test_node_red_schedule_has_core_jobs():
    paths = {j["path"] for j in NODE_RED_SCHEDULE}
    assert "/api/automation/lead-qualification" in paths
    assert "/api/automation/ops-digest" in paths
    assert "/api/automation/bond-report" in paths
    assert "/api/automation/discharge-report" in paths
    assert "/api/automation/osint-hot-leads" in paths
    assert "/api/automation/osint-status" in paths


def test_cron_registry_enables_revenue_jobs():
    from dashboard.cron import CRON_REGISTRY

    by_name = {c.name: c for c in CRON_REGISTRY}
    for name in (
        "speed_to_contact",
        "paperwork_chase",
        "intake_recovery",
        "poa_low_stock",
        "surety_weekly_reports",
    ):
        assert name in by_name, f"missing cron {name}"
        assert by_name[name].default_enabled is True


def test_node_red_doc_exists():
    root = Path(__file__).resolve().parents[1]
    assert (root / "docs/automation/NODE_RED_SCHEDULE.md").is_file()
