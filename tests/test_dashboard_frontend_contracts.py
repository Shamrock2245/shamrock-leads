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
    assert "/api/ops/defendants" in source
    assert "hours: '48'" not in source


def test_state_counts_open_the_same_defendants_with_write_print():
    multi = (ROOT / "dashboard" / "sl-multi-state.js").read_text()
    command = (ROOT / "dashboard" / "sl-data.js").read_text()
    page = (ROOT / "dashboard" / "index.html").read_text()
    ops = (ROOT / "dashboard" / "routers" / "stats.py").read_text()

    assert "openDefendantWritePrint" in multi
    assert "/api/ops/defendants" in multi
    assert "SLIntel.open" in multi
    assert "OH" in multi
    assert 'data-write="' in command
    assert "state_order" in command
    assert "['FL','GA','SC','NC'].forEach" not in command
    assert "sl-multi-state.css" in page
    assert "preset:'all'" in page
    assert "ACTIVE_STATE_CODES" in ops
    assert "bond_ready_count\": len(bond_ready)" not in ops
    assert "booking_number" in ops


def test_osint_ui_has_ghunt_companion_login():
    source = (ROOT / "dashboard" / "sl-osint.js").read_text()
    assert "saveGhuntLogin" in source
    assert "ghuntCompanionBlob" in source
    assert "Load unpacked" in source
    assert "tools/ghunt-companion" in source
    assert "/api/osint/ghunt/login" in source


def test_lee_bookmarklet_opens_dashboard_extract_hash():
    source = (ROOT / "dashboard" / "sl-hydrate.js").read_text()

    assert "booking-extract=" in source
    assert "/api/leads/merge-booking-extract" in source
    assert "sl-booking-extract" in source
    assert "buildLeeBookmarklet" in source
    assert "ingestExtract" in source
