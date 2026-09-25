"""CI drift gate: COUNTY_SOURCE_CONTRACT_MATRIX.md ↔ SCRAPER_SOURCE_STATES ↔ code.

Fails when:
* the committed matrix differs from a fresh ``scripts/build_recon_matrix.py`` build;
* a registered scraper has code-level ``SOURCE_CONTRACT_VALIDATED = False`` but
  Health (``SCRAPER_SOURCE_STATES``) does not say ``fail_closed``;
* a ``verified_public`` label's scraper is guarded in code;
* a hold county (Hampton / Marlboro / Richland / Sumter / Sarasota / FL JailTracker /
  Lake / Leon / Gadsden)
  is not fail_closed in both places.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

HOLD_LABELS = {
    "Hampton (SC)", "Marlboro (SC)", "Richland (SC)", "Sumter (SC)", "Sarasota (FL)",
    "Baker (FL)", "Calhoun (FL)", "Gulf (FL)", "Holmes (FL)", "Levy (FL)", "Wakulla (FL)", "Washington (FL)",
    "TnCIS (TN)",
    "Lake (FL)", "Leon (FL)", "Gadsden (FL)",
}
LIVE_SC = {"Charleston (SC)", "Dorchester (SC)", "Chesterfield (SC)", "Aiken (SC)", "Darlington (SC)"}


def _builder():
    spec = importlib.util.spec_from_file_location("build_recon_matrix", ROOT / "scripts" / "build_recon_matrix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def registered_scrapers():
    """Instantiate every scheduled scraper exactly as main.register_scrapers does."""
    logging.disable(logging.CRITICAL)
    try:
        import main

        class _Collect:
            def __init__(self):
                self.items = []

            def register_scraper(self, scraper, interval_minutes=None):
                self.items.append(scraper)

        sched = _Collect()
        main.register_scrapers(sched)
        return sched.items
    finally:
        logging.disable(logging.NOTSET)


def test_matrix_is_regenerated_from_registry_and_evidence():
    builder = _builder()
    text, _summary = builder.build_matrix(builder.DEFAULT_INVENTORY, builder.DEFAULT_EVIDENCE, builder.DEFAULT_LIVE_EVIDENCE)
    committed = builder.DEFAULT_OUTPUT.read_text()
    assert committed == text, (
        "docs/recon/COUNTY_SOURCE_CONTRACT_MATRIX.md drifted from SCRAPER_SOURCE_STATES / evidence. "
        "Run: python scripts/build_recon_matrix.py"
    )


def test_code_guards_are_fail_closed_on_health(registered_scrapers):
    from dashboard.extensions import REGISTERED_COUNTIES, SCRAPER_SOURCE_STATES

    labels = [f"{s.county} ({s.state})" for s in registered_scrapers]
    assert sorted(labels) == sorted(REGISTERED_COUNTIES)
    mismatched = sorted(
        label for scraper, label in zip(registered_scrapers, labels)
        if not getattr(scraper, "SOURCE_CONTRACT_VALIDATED", True)
        and SCRAPER_SOURCE_STATES.get(label) != "fail_closed"
    )
    assert mismatched == [], f"code-level fail_closed but Health not fail_closed: {mismatched}"


def test_verified_public_labels_are_not_guarded_in_code(registered_scrapers):
    from dashboard.extensions import SCRAPER_SOURCE_STATES

    guarded = sorted(
        f"{s.county} ({s.state})" for s in registered_scrapers
        if SCRAPER_SOURCE_STATES.get(f"{s.county} ({s.state})") == "verified_public"
        and not getattr(s, "SOURCE_CONTRACT_VALIDATED", True)
    )
    assert guarded == []


def test_hold_counties_stay_fail_closed_everywhere(registered_scrapers):
    from dashboard.extensions import SCRAPER_SOURCE_STATES

    by_label = {f"{s.county} ({s.state})": s for s in registered_scrapers}
    for label in sorted(HOLD_LABELS):
        assert SCRAPER_SOURCE_STATES.get(label) == "fail_closed", label
        assert getattr(by_label[label], "SOURCE_CONTRACT_VALIDATED", True) is False, label


def test_brief_live_scopes_match_registry():
    from dashboard.extensions import SCRAPER_SOURCE_STATES

    for label in sorted(LIVE_SC | {"Broward (FL)"}):
        assert SCRAPER_SOURCE_STATES.get(label) == "verified_public", label
    matrix = (ROOT / "docs" / "recon" / "COUNTY_SOURCE_CONTRACT_MATRIX.md").read_text()
    for label in ("Pinellas (FL)", "Seminole (FL)", "Lee (FL)"):
        assert f"| {label} | unverified | live_write |" in matrix
    for label in ("Richland (SC)", "Sumter (SC)", "Hampton (SC)", "Marlboro (SC)"):
        assert f"| {label} | fail_closed | hold |" in matrix


def test_builder_refuses_live_write_on_fail_closed_scope(tmp_path):
    builder = _builder()
    bad = tmp_path / "live.json"
    bad.write_text('{"records": [{"label": "Hampton (SC)", "emitter": "live_write", "evidence": "x", "source": "y"}]}')
    with pytest.raises(RuntimeError, match="fail_closed"):
        builder.build_matrix(builder.DEFAULT_INVENTORY, builder.DEFAULT_EVIDENCE, bad)
