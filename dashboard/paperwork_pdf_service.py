"""
ShamrockLeads — Paperwork PDF Engine (local stitch / fill / flatten assist)

Packet composition rule
-----------------------
  OSI packet      = templates/surety-agnostic-shamrock/*  +  templates/osi/*
  Palmetto packet = templates/surety-agnostic-shamrock/*  +  templates/palmetto/*

Defendant fields come from arrest-lead scrape data; indemnitor fields come from
intake / match / dashboard. This module hydrates blanks, stitches the packet,
and leaves e-sign to DocuSeal (primary; SignNow retired for new packets).

PRIMARY production path is DocuSeal (self-hosted templates + prefill).
This module is for local stitch / Adobe PDF Services fill / offline preview.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# ── Roots (Docker /app/templates, else repo templates/) ──────────────────────
_DOCKER_ROOT = Path("/app/templates")
_LOCAL_ROOT = Path(__file__).resolve().parent.parent / "templates"
TEMPLATES_ROOT = _DOCKER_ROOT if _DOCKER_ROOT.exists() else _LOCAL_ROOT

AGNOSTIC_DIR = TEMPLATES_ROOT / "surety-agnostic-shamrock"
OSI_DIR = TEMPLATES_ROOT / "osi"
PALMETTO_DIR = TEMPLATES_ROOT / "palmetto"
# Legacy fallback if someone still has the old flat layout
LEGACY_BLANKS_DIR = TEMPLATES_ROOT / "blanks"

# Canonical packet order (matches SignNow phases; appearance bond is print-only)
PACKET_DOC_ORDER: List[str] = [
    "paperwork-header",
    "faq-cosigners",
    "faq-defendants",
    "indemnity-agreement",
    "defendant-application",
    "promissory-note",
    "disclosure-form",
    "surety-terms",
    "master-waiver",
    "ssa-release",
    "collateral-receipt",
    "payment-plan",
]

# Optional print-only docs (not e-signed) — resolved via bond_pdf_service paths
PRINT_ONLY_DOC_ORDER: List[str] = [
    "appearance-bond",
]

# Docs that need one copy per indemnitor
PER_INDEMNITOR_DOCS = frozenset({"indemnity-agreement"})

# Docs that need one copy per person (defendant + each indemnitor)
PER_PERSON_DOCS = frozenset({"master-waiver", "ssa-release"})

# ── Slug → on-disk filename by tier ──────────────────────────────────────────
# Surety-agnostic Shamrock forms (always included for both sureties)
AGNOSTIC_FILES: Dict[str, str] = {
    "paperwork-header": "paperwork-header.pdf",
    "faq-cosigners": "faq-cosigners.pdf",
    "faq-defendants": "faq-defendants.pdf",
    "master-waiver": "master-waiver.pdf",
    "ssa-release": "ssa-release.pdf",
    "payment-plan": "payment-plan.pdf",
}

# OSI surety-specific (and shared legal forms currently stored under osi/)
OSI_FILES: Dict[str, str] = {
    "appearance-bond": "Appearance Bond blank.pdf",
    "collateral-receipt": "collateral-receipt.pdf",
    "defendant-application": "defendant-application.pdf",
    "disclosure-form": "disclosure-form.pdf",
    "indemnity-agreement": "indemnity-agreement.pdf",
    "promissory-note": "promissory-note.pdf",
    "surety-terms": "surety-terms.pdf",
}

# Palmetto surety-specific
PALMETTO_FILES: Dict[str, str] = {
    "appearance-bond": "Shamrock Palmetto Official Appearance Bond.pdf",
    "collateral-receipt": "collateral-receipt-palmetto.pdf",
    "defendant-application": "defendant-application-palmetto.pdf",
    "indemnity-agreement": "indemnity-agreement-palmetto.pdf",
    "surety-terms": "surety-terms-palmetto.pdf",
}

# Shared legal forms that have no Palmetto-branded PDF; live under templates/osi/
# and are used for BOTH sureties (matches SignNow shared templates).
SHARED_LEGAL_SLUGS = frozenset({"promissory-note", "disclosure-form"})


def _normalize_surety(surety: Optional[str]) -> str:
    s = (surety or "osi").lower().strip()
    if s not in ("osi", "palmetto"):
        return "osi"
    return s


def get_template_path(slug: str, surety: str = "osi") -> Path:
    """
    Resolve blank PDF path for a document slug + surety.

    Order:
      1. surety-agnostic-shamrock/{file} when slug is agnostic
      2. templates/{osi|palmetto}/{file} for surety-specific slugs
      3. shared legal (promissory/disclosure) from templates/osi/ for any surety
      4. legacy templates/blanks/{slug}[-palmetto].pdf (migration safety net)
    """
    surety = _normalize_surety(surety)
    slug = (slug or "").strip().lower()

    # 1) Agnostic
    if slug in AGNOSTIC_FILES:
        path = AGNOSTIC_DIR / AGNOSTIC_FILES[slug]
        if path.is_file():
            return path

    # 2) Surety-specific
    if surety == "palmetto" and slug in PALMETTO_FILES:
        path = PALMETTO_DIR / PALMETTO_FILES[slug]
        if path.is_file():
            return path

    if surety == "osi" and slug in OSI_FILES:
        path = OSI_DIR / OSI_FILES[slug]
        if path.is_file():
            return path

    # 3) Shared legal stored under osi/ (available to Palmetto packets too)
    if slug in SHARED_LEGAL_SLUGS and slug in OSI_FILES:
        path = OSI_DIR / OSI_FILES[slug]
        if path.is_file():
            return path

    # 3b) Palmetto missing a surety form → do NOT borrow OSI branding forms
    #     except shared legal (already handled). Fall through to legacy.

    # 4) Legacy flat blanks/ layout
    if LEGACY_BLANKS_DIR.is_dir():
        if surety == "palmetto":
            pal = LEGACY_BLANKS_DIR / f"{slug}-palmetto.pdf"
            if pal.is_file():
                return pal
        leg = LEGACY_BLANKS_DIR / f"{slug}.pdf"
        if leg.is_file():
            return leg

    # Expected path (may not exist — caller checks is_file())
    if slug in AGNOSTIC_FILES:
        return AGNOSTIC_DIR / AGNOSTIC_FILES[slug]
    if surety == "palmetto" and slug in PALMETTO_FILES:
        return PALMETTO_DIR / PALMETTO_FILES[slug]
    if slug in OSI_FILES:
        return OSI_DIR / OSI_FILES[slug]
    return AGNOSTIC_DIR / f"{slug}.pdf"


def packet_composition(surety: str = "osi") -> Dict[str, Any]:
    """
    Describe which folders + files compose a packet for a surety.
    Used by paperwork config / diagnostics UI.
    """
    surety = _normalize_surety(surety)
    agnostic = {
        slug: str(AGNOSTIC_DIR / name)
        for slug, name in AGNOSTIC_FILES.items()
    }
    if surety == "palmetto":
        surety_files = {
            slug: str(PALMETTO_DIR / name)
            for slug, name in PALMETTO_FILES.items()
        }
        shared_legal = {
            slug: str(OSI_DIR / OSI_FILES[slug])
            for slug in SHARED_LEGAL_SLUGS
            if slug in OSI_FILES
        }
        surety_dir = str(PALMETTO_DIR)
    else:
        surety_files = {
            slug: str(OSI_DIR / name)
            for slug, name in OSI_FILES.items()
            if slug not in SHARED_LEGAL_SLUGS
        }
        shared_legal = {
            slug: str(OSI_DIR / OSI_FILES[slug])
            for slug in SHARED_LEGAL_SLUGS
            if slug in OSI_FILES
        }
        surety_dir = str(OSI_DIR)

    return {
        "surety_id": surety,
        "rule": (
            "surety-agnostic-shamrock + palmetto"
            if surety == "palmetto"
            else "surety-agnostic-shamrock + osi"
        ),
        "folders": {
            "agnostic": str(AGNOSTIC_DIR),
            "surety": surety_dir,
            "templates_root": str(TEMPLATES_ROOT),
        },
        "agnostic_docs": agnostic,
        "surety_docs": surety_files,
        "shared_legal_docs": shared_legal,
        "packet_order": list(PACKET_DOC_ORDER),
        "print_only": list(PRINT_ONLY_DOC_ORDER),
        "esign_providers": ["docuseal", "none"],
        "esign_default": "docuseal",
        "flatten_engines": ["adobe_pdf_services", "local_pymupdf"],
    }


def place_text_by_anchor(
    page: fitz.Page,
    anchor: str,
    text: str,
    dx: float = 0,
    dy: float = 0,
    font_size: float = 10,
    index: int = 0,
) -> None:
    """Search for an anchor string on the page and place `text` at an offset."""
    if not text:
        return

    rects = page.search_for(anchor)
    if rects and len(rects) > index:
        r = rects[index]
        point = fitz.Point(r.x1 + dx, r.y0 + dy)
        page.insert_text(point, str(text), fontsize=font_size, color=(0, 0, 0))


def _open_blank(slug: str, surety: str) -> Optional[fitz.Document]:
    path = get_template_path(slug, surety)
    if not path.is_file():
        logger.warning(
            "[paperwork_pdf] missing blank template slug=%s surety=%s path=%s",
            slug,
            surety,
            path,
        )
        return None
    try:
        return fitz.open(path)
    except Exception as e:
        logger.error("[paperwork_pdf] failed to open %s: %s", path, e)
        return None


def _person_list(data: Dict[str, Any]) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Return [(role, display_name, fields_dict), ...] defendant first."""
    people: List[Tuple[str, str, Dict[str, Any]]] = []
    def_name = (data.get("defendant_name") or data.get("Defendant_Name") or "").strip()
    people.append(
        (
            "Defendant",
            def_name or "Defendant",
            {
                "name": def_name,
                "address": data.get("defendant_address") or data.get("address") or "",
                "phone": data.get("defendant_phone") or "",
            },
        )
    )
    inds = data.get("indemnitors") or []
    if not inds and data.get("indemnitor_name"):
        inds = [
            {
                "name": data.get("indemnitor_name"),
                "address": data.get("indemnitor_address") or "",
                "phone": data.get("indemnitor_phone") or "",
            }
        ]
    for i, ind in enumerate(inds):
        if not isinstance(ind, dict):
            continue
        name = (ind.get("name") or "").strip()
        role = "Indemnitor" if i == 0 else f"Co-Indemnitor {i}"
        people.append(
            (
                role,
                name or role,
                {
                    "name": name,
                    "address": ind.get("address") or "",
                    "phone": ind.get("phone") or "",
                },
            )
        )
    return people


def _hydrate_common_fields(
    doc: fitz.Document,
    data: Dict[str, Any],
    *,
    person: Optional[Dict[str, Any]] = None,
    role: str = "",
    role_index: int = 0,
) -> None:
    """Best-effort text placement on first page using common anchors."""
    if not doc.page_count:
        return
    page = doc[0]
    def_name = data.get("defendant_name") or data.get("Defendant_Name") or ""
    case_no = data.get("case_number") or data.get("Case_Number") or ""
    county = data.get("county") or data.get("County") or ""
    bond_amt = data.get("bond_amount") or data.get("Bond_Amount") or ""
    poa = data.get("poa_number") or data.get("POA_Number") or ""
    person = person or {}

    place_text_by_anchor(page, "(Defendant/Principal)", def_name, dx=5, dy=-15)
    place_text_by_anchor(page, "Defendant", def_name, dx=10, dy=10, index=0)
    place_text_by_anchor(page, "Name", person.get("name") or def_name, dx=10, dy=10)
    place_text_by_anchor(page, "Address", person.get("address") or "", dx=10, dy=10)
    place_text_by_anchor(page, "Case", str(case_no), dx=10, dy=10)
    place_text_by_anchor(page, "County", str(county), dx=10, dy=10)
    place_text_by_anchor(page, "Bond Amount", str(bond_amt), dx=10, dy=10)
    place_text_by_anchor(page, "POA", str(poa), dx=10, dy=10)

    if role:
        # SignNow text tags for field extraction on secondary PDF path
        sig_tag = f"{{{{s1_{role}}}}}"
        date_tag = f"{{{{d1_{role}}}}}"
        place_text_by_anchor(page, "INDEMNITOR:", sig_tag, dx=50, dy=10, font_size=8)
        place_text_by_anchor(page, "Signature", sig_tag, dx=20, dy=10, font_size=8)
        place_text_by_anchor(page, "this", date_tag, dx=20, dy=0, font_size=8, index=role_index)


def hydrate_indemnity_agreement(
    data: Dict[str, Any], indemnitor_index: int = 0, surety: str = "osi"
) -> bytes:
    """Fills the Indemnity Agreement and places SignNow signature tags."""
    doc = _open_blank("indemnity-agreement", surety)
    if doc is None:
        raise FileNotFoundError(
            "indemnity-agreement blank PDF missing under "
            f"templates/{_normalize_surety(surety)}/ (or surety-agnostic path)"
        )

    inds = data.get("indemnitors") or [{}]
    if indemnitor_index >= len(inds):
        ind: Dict[str, Any] = {}
    else:
        ind = inds[indemnitor_index] if isinstance(inds[indemnitor_index], dict) else {}

    role = f"Indemnitor {indemnitor_index + 1}"
    _hydrate_common_fields(doc, data, person=ind, role=role, role_index=0)

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    buf.seek(0)
    return buf.read()


def _doc_bytes_for_slug(
    slug: str,
    data: Dict[str, Any],
    surety: str,
    *,
    person: Optional[Dict[str, Any]] = None,
    role: str = "",
    role_index: int = 0,
) -> Optional[bytes]:
    if slug == "indemnity-agreement" and person is not None:
        try:
            idx = 0
            if role.startswith("Indemnitor"):
                parts = role.split()
                if len(parts) >= 2 and parts[-1].isdigit():
                    idx = max(0, int(parts[-1]) - 1)
            return hydrate_indemnity_agreement(data, indemnitor_index=idx, surety=surety)
        except FileNotFoundError:
            return None

    # Appearance bonds: one form per charge (case # + exclusive POA per charge).
    # Multi-charge defendants produce a merged print PDF via bond_pdf_service.
    if slug == "appearance-bond":
        try:
            from dashboard.bond_pdf_service import generate_appearance_bond

            bond_data = dict(data or {})
            bond_data["surety"] = _normalize_surety(surety)
            return generate_appearance_bond(bond_data)
        except Exception as exc:
            logger.warning("[paperwork_pdf] appearance-bond fill failed: %s", exc)
            doc = _open_blank(slug, surety)
            if doc is None:
                return None
            buf = io.BytesIO()
            doc.save(buf)
            doc.close()
            buf.seek(0)
            return buf.read()

    doc = _open_blank(slug, surety)
    if doc is None:
        return None
    _hydrate_common_fields(doc, data, person=person, role=role, role_index=role_index)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    buf.seek(0)
    return buf.read()


def generate_full_packet(
    data: Dict[str, Any],
    surety: str = "osi",
    *,
    include_appearance_bond: bool = False,
) -> bytes:
    """
    Stitch the full blank packet for a surety:

      agnostic Shamrock forms + that surety's forms

    - Per-indemnitor: indemnity-agreement
    - Per-person: master-waiver, ssa-release
    - Static/shared: remaining docs once each
    Missing blank files are skipped with a warning (never silent empty crash).
    """
    surety = _normalize_surety(surety)

    out_doc = fitz.open()
    included: List[str] = []
    missing: List[str] = []

    people = _person_list(data)
    indemnitors = [p for p in people if p[0] != "Defendant"]
    if not indemnitors:
        indemnitors = [("Indemnitor 1", "Indemnitor", {})]

    order = list(PACKET_DOC_ORDER)
    if include_appearance_bond:
        order = order + list(PRINT_ONLY_DOC_ORDER)

    for slug in order:
        if slug in PER_INDEMNITOR_DOCS:
            for i, (role, _name, fields) in enumerate(indemnitors):
                label = f"Indemnitor {i + 1}"
                raw = _doc_bytes_for_slug(
                    slug, data, surety, person=fields, role=label, role_index=i
                )
                if raw is None:
                    missing.append(f"{slug}[{label}]")
                    continue
                part = fitz.open("pdf", raw)
                out_doc.insert_pdf(part)
                part.close()
                included.append(f"{slug}[{label}]")
            continue

        if slug in PER_PERSON_DOCS:
            for i, (role, _name, fields) in enumerate(people):
                raw = _doc_bytes_for_slug(
                    slug, data, surety, person=fields, role=role, role_index=i
                )
                if raw is None:
                    missing.append(f"{slug}[{role}]")
                    continue
                part = fitz.open("pdf", raw)
                out_doc.insert_pdf(part)
                part.close()
                included.append(f"{slug}[{role}]")
            continue

        # static / shared
        raw = _doc_bytes_for_slug(slug, data, surety, person=None, role="", role_index=0)
        if raw is None:
            missing.append(slug)
            continue
        part = fitz.open("pdf", raw)
        out_doc.insert_pdf(part)
        part.close()
        included.append(slug)

    if out_doc.page_count == 0:
        out_doc.close()
        raise RuntimeError(
            "generate_full_packet produced zero pages — check "
            f"templates/surety-agnostic-shamrock + templates/{surety}/"
        )

    if missing:
        logger.warning(
            "[paperwork_pdf] packet incomplete surety=%s missing=%s included=%s",
            surety,
            missing,
            included,
        )
    else:
        logger.info(
            "[paperwork_pdf] full packet surety=%s docs=%s pages=%s",
            surety,
            len(included),
            out_doc.page_count,
        )

    buf = io.BytesIO()
    out_doc.save(buf)
    out_doc.close()
    buf.seek(0)
    return buf.read()


def list_available_blanks(surety: str = "osi") -> Dict[str, bool]:
    """Diagnostics: which packet slugs resolve to an on-disk blank."""
    surety = _normalize_surety(surety)
    out = {slug: get_template_path(slug, surety).is_file() for slug in PACKET_DOC_ORDER}
    for slug in PRINT_ONLY_DOC_ORDER:
        out[slug] = get_template_path(slug, surety).is_file()
    return out


def list_template_inventory() -> Dict[str, Any]:
    """Full inventory for paperwork config / health checks."""
    return {
        "osi": {
            **packet_composition("osi"),
            "available": list_available_blanks("osi"),
        },
        "palmetto": {
            **packet_composition("palmetto"),
            "available": list_available_blanks("palmetto"),
        },
    }
