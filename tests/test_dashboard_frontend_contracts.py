"""Static source contracts for staff-dashboard behaviors that are not API-testable."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_client_portal_checkin_kpi_uses_the_rendered_id_and_seven_day_metric():
    source = (ROOT / "dashboard" / "sl-portal.js").read_text()

    assert "getElementById('kpiCheckins')" in source
    assert "data.checkins_7d" in source
    assert "kpiPortalCheckins" not in source


def test_fta_ui_does_not_promise_a_retired_signature_provider():
    source = (ROOT / "dashboard" / "sl-fta.js").read_text()

    assert "SignNow" not in source
    assert "No e-sign packet is created" in source
    assert "data.staff_document_required" in source
