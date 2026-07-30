"""
Template path resolution after templates/blanks → osi / palmetto / surety-agnostic-shamrock.
"""
from pathlib import Path

import pytest

from dashboard.paperwork_pdf_service import (
    AGNOSTIC_DIR,
    AGNOSTIC_FILES,
    OSI_DIR,
    OSI_FILES,
    PACKET_DOC_ORDER,
    PALMETTO_DIR,
    PALMETTO_FILES,
    get_template_path,
    list_available_blanks,
    packet_composition,
)


def test_agnostic_slugs_resolve_to_agnostic_folder():
    for slug in AGNOSTIC_FILES:
        path = get_template_path(slug, "osi")
        assert path.parent == AGNOSTIC_DIR or path.name == AGNOSTIC_FILES[slug]
        path_p = get_template_path(slug, "palmetto")
        assert path_p.name == AGNOSTIC_FILES[slug]
        assert "surety-agnostic" in str(path_p) or path_p.parent == AGNOSTIC_DIR


def test_osi_surety_forms_under_osi():
    for slug in ("indemnity-agreement", "defendant-application", "surety-terms", "collateral-receipt"):
        path = get_template_path(slug, "osi")
        assert path.is_file(), f"missing OSI blank: {path}"
        assert path.parent == OSI_DIR
        assert "palmetto" not in path.name.lower()


def test_palmetto_surety_forms_under_palmetto():
    for slug in ("indemnity-agreement", "defendant-application", "surety-terms", "collateral-receipt"):
        path = get_template_path(slug, "palmetto")
        assert path.is_file(), f"missing Palmetto blank: {path}"
        assert path.parent == PALMETTO_DIR
        assert "palmetto" in path.name.lower() or "Palmetto" in path.name


def test_appearance_bond_filenames():
    osi = get_template_path("appearance-bond", "osi")
    pal = get_template_path("appearance-bond", "palmetto")
    assert osi.is_file()
    assert pal.is_file()
    assert osi.name == OSI_FILES["appearance-bond"]
    assert pal.name == PALMETTO_FILES["appearance-bond"]


def test_shared_legal_available_for_both_sureties():
    for slug in ("promissory-note", "disclosure-form"):
        for surety in ("osi", "palmetto"):
            path = get_template_path(slug, surety)
            assert path.is_file(), f"{slug} missing for {surety}: {path}"
            # Stored under osi/ (shared legal)
            assert path.parent == OSI_DIR


def test_packet_composition_rule():
    osi = packet_composition("osi")
    pal = packet_composition("palmetto")
    assert "surety-agnostic-shamrock + osi" in osi["rule"]
    assert "surety-agnostic-shamrock + palmetto" in pal["rule"]
    assert "signnow" in osi["esign_providers"]
    assert "adobe" in osi["esign_providers"]


def test_list_available_blanks_complete_for_osi():
    avail = list_available_blanks("osi")
    for slug in PACKET_DOC_ORDER:
        assert slug in avail
        assert avail[slug] is True, f"OSI packet missing blank for {slug}"
    assert avail.get("appearance-bond") is True


def test_list_available_blanks_palmetto_core():
    avail = list_available_blanks("palmetto")
    # Agnostic + palmetto-branded + shared legal
    for slug in (
        "paperwork-header",
        "faq-cosigners",
        "indemnity-agreement",
        "defendant-application",
        "promissory-note",
        "disclosure-form",
        "surety-terms",
        "master-waiver",
        "ssa-release",
        "collateral-receipt",
        "payment-plan",
        "appearance-bond",
    ):
        assert avail.get(slug) is True, f"Palmetto missing {slug}"


def test_no_legacy_blanks_required():
    """New layout must work without templates/blanks/."""
    legacy = Path(__file__).resolve().parent.parent / "templates" / "blanks"
    # Even if legacy dir is gone, resolution still works
    assert get_template_path("payment-plan", "osi").is_file()
    assert get_template_path("indemnity-agreement", "palmetto").is_file()
    if legacy.exists():
        pytest.skip("legacy blanks still present (migration)")
