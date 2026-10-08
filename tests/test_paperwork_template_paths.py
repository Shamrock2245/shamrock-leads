"""
Template path resolution after templates/blanks → osi / palmetto / surety-agnostic-shamrock.
"""
from pathlib import Path

import fitz
import pytest

from dashboard.paperwork_pdf_service import (
    _doc_bytes_for_slug,
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
    assert osi["esign_providers"] == ["docuseal", "none"]


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


def test_osi_stitch_does_not_stamp_the_agent_block():
    """Indemnitor name and address must not land on the printed agent header.

    The five OSI forms that share the 'Agent name, Address, Phone & License #'
    line used to take the first case-insensitive 'Name' / 'Address' hit, which
    is that header. Raw {{...}} tags must not be printed either.
    """
    name = "ZZTESTINDEMNITOR"
    address = "ZZTESTADDRESSLANE"
    defendant = "ZZTESTDEFENDANT"
    data = {
        "defendant_name": defendant,
        "case_number": "00-SAMPLE-000",
        "county": "Sample",
        "bond_amount": "1000",
        "poa_number": "SAMPLE-POA",
    }
    person = {"name": name, "address": address}
    slugs = (
        "defendant-application",
        "promissory-note",
        "disclosure-form",
        "surety-terms",
        "collateral-receipt",
    )
    for slug in slugs:
        raw = _doc_bytes_for_slug(
            slug,
            data,
            "osi",
            person=person,
            role="Indemnitor 1",
            role_index=0,
        )
        assert raw, slug
        doc = fitz.open(stream=raw, filetype="pdf")
        try:
            for page in doc:
                assert "{{" not in page.get_text(), slug
                for hit in page.search_for("Agent name"):
                    clip = fitz.Rect(
                        hit.x0 - 40,
                        hit.y0 - 24,
                        page.rect.x1,
                        hit.y1 + 40,
                    ) & page.rect
                    stamped = page.get_text(clip=clip)
                    assert name not in stamped, slug
                    assert address not in stamped, slug
                    assert defendant not in stamped, slug
                for token in (name, address, defendant):
                    for rect in page.search_for(token):
                        assert page.rect.x0 - 1 <= rect.x0 <= page.rect.x1 + 1, slug
                        assert page.rect.y0 - 1 <= rect.y0 <= page.rect.y1 + 1, slug
                        assert rect.x1 <= page.rect.x1 + 1, slug
                        assert rect.y1 <= page.rect.y1 + 1, slug
        finally:
            doc.close()


def test_no_legacy_blanks_required():
    """New layout must work without templates/blanks/."""
    legacy = Path(__file__).resolve().parent.parent / "templates" / "blanks"
    # Even if legacy dir is gone, resolution still works
    assert get_template_path("payment-plan", "osi").is_file()
    assert get_template_path("indemnity-agreement", "palmetto").is_file()
    if legacy.exists():
        pytest.skip("legacy blanks still present (migration)")
