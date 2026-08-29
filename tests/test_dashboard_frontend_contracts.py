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


def test_defendant_card_write_print_is_the_primary_desk():
    source = (ROOT / "dashboard" / "sl-features.js").read_text()

    assert "openDefendantWritePrint" in source
    assert "Write / Print" in source
    assert "appearanceBondReadiness" in source
    assert "openLeeBookingImport" in source
    assert "hydrateDefendantPacket('${bkSafe}')" not in source
    assert "onclick=\"openBondModal(window._leadMap[" not in source


def test_bond_intelligence_write_uses_write_print_desk():
    source = (ROOT / "dashboard" / "sl-bond-intelligence.js").read_text()
    assert "openDefendantWritePrint" in source


def test_lee_bookmarklet_opens_dashboard_extract_hash():
    source = (ROOT / "dashboard" / "sl-hydrate.js").read_text()

    assert "booking-extract=" in source
    assert "/api/leads/merge-booking-extract" in source
    assert "sl-booking-extract" in source
    assert "buildLeeBookmarklet" in source
    assert "ingestExtract" in source
