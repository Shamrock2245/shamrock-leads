"""
ShamrockLeads — Paperwork PDF Engine (secondary / offline assist)

Stitches blank PDF templates from templates/blanks into a single packet and
places SignNow-style text tags where anchors exist.

PRIMARY production path remains SignNowPacketService (SignNow templates).
This module is for local stitch / fallback / offline preview — not a replacement
for surety-routed SignNow delivery.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "blanks"

# Canonical packet order (matches Telegram DOC_ORDER / SignNow phases, minus print-only bond)
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

# Docs that need one copy per indemnitor
PER_INDEMNITOR_DOCS = frozenset({"indemnity-agreement"})

# Docs that need one copy per person (defendant + each indemnitor)
PER_PERSON_DOCS = frozenset({"master-waiver", "ssa-release"})


def get_template_path(slug: str, surety: str = "osi") -> Path:
    """Resolve blank PDF path; prefer surety-specific file when present."""
    surety = (surety or "osi").lower().strip()
    if surety == "palmetto":
        palmetto = TEMPLATES_DIR / f"{slug}-palmetto.pdf"
        if palmetto.is_file():
            return palmetto
    return TEMPLATES_DIR / f"{slug}.pdf"


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
        logger.warning("[paperwork_pdf] missing blank template: %s", path.name)
        return None
    try:
        return fitz.open(path)
    except Exception as e:
        logger.error("[paperwork_pdf] failed to open %s: %s", path.name, e)
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


def hydrate_indemnity_agreement(data: Dict[str, Any], indemnitor_index: int = 0, surety: str = "osi") -> bytes:
    """Fills the Indemnity Agreement and places SignNow signature tags."""
    doc = _open_blank("indemnity-agreement", surety)
    if doc is None:
        raise FileNotFoundError("indemnity-agreement blank PDF missing under templates/blanks")

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
        # Reuse dedicated hydrator when we have indemnitor index in role
        try:
            # role like "Indemnitor 1"
            idx = 0
            if role.startswith("Indemnitor"):
                parts = role.split()
                if len(parts) >= 2 and parts[-1].isdigit():
                    idx = max(0, int(parts[-1]) - 1)
            return hydrate_indemnity_agreement(data, indemnitor_index=idx, surety=surety)
        except FileNotFoundError:
            return None

    doc = _open_blank(slug, surety)
    if doc is None:
        return None
    _hydrate_common_fields(doc, data, person=person, role=role, role_index=role_index)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    buf.seek(0)
    return buf.read()


def generate_full_packet(data: Dict[str, Any], surety: str = "osi") -> bytes:
    """
    Stitch the full blank packet (all available docs in canonical order).

    - Per-indemnitor: indemnity-agreement
    - Per-person: master-waiver, ssa-release
    - Static/shared: remaining docs once each
    Missing blank files are skipped with a warning (never silent empty crash).
    """
    surety = (surety or "osi").lower().strip()
    if surety not in ("osi", "palmetto"):
        surety = "osi"

    out_doc = fitz.open()
    included: List[str] = []
    missing: List[str] = []

    people = _person_list(data)
    indemnitors = [p for p in people if p[0] != "Defendant"]
    if not indemnitors:
        # Still emit one indemnitor slot so packet structure is complete
        indemnitors = [("Indemnitor 1", "Indemnitor", {})]

    for slug in PACKET_DOC_ORDER:
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
            "generate_full_packet produced zero pages — check templates/blanks PDFs"
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
    return {slug: get_template_path(slug, surety).is_file() for slug in PACKET_DOC_ORDER}
