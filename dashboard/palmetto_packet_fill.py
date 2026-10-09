"""
Fill Palmetto carrier forms from the same bond payload OSI appearance bonds use.

Text widgets are written by name. An empty value clears a baked-in /V
(see ``_clear_widget_value``). Signature widgets are left for wet-ink or
DocuSeal. Nothing here delivers a packet to a signer.
"""
from __future__ import annotations

import io
from typing import Any, Dict, List, Optional

import fitz

from dashboard.bond_pdf_service import (
    AGENCY_NAME,
    _amount_to_words,
    _apply_field_values,
    _clear_widget_value,
    _parse_date_parts,
    _parse_defendant_name,
    _safe_float,
    _split_court_datetime,
    fill_palmetto_bond,
)
from dashboard.palmetto_field_placement import PAGE_SIZE, fields_for
from dashboard.paperwork_pdf_service import get_template_path


def _person_text(person: dict, *keys: str) -> str:
    if not isinstance(person, dict):
        return ""
    for key in keys:
        value = person.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _split_three(full: str) -> tuple:
    full = str(full or "").strip()
    if not full:
        return "", "", ""
    if "," in full:
        last, rest = full.split(",", 1)
        parts = rest.split()
        first = parts[0] if parts else ""
        middle = " ".join(parts[1:]) if len(parts) > 1 else ""
        return first, middle, last.strip()
    parts = full.split()
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def build_palmetto_context(data: Optional[dict]) -> Dict[str, str]:
    """Flat string context keyed by ``data_source`` on the placement spec."""
    data = dict(data or {})
    defendant = data.get("defendant") if isinstance(data.get("defendant"), dict) else {}
    indemnitor = data.get("indemnitor") if isinstance(data.get("indemnitor"), dict) else {}

    full_name = str(
        data.get("name") or data.get("defendant_name") or _person_text(defendant, "name", "full_name") or ""
    ).strip()
    first, last = _parse_defendant_name(
        full_name,
        first_name=str(data.get("first_name") or _person_text(defendant, "first_name", "firstName") or ""),
        last_name=str(data.get("last_name") or _person_text(defendant, "last_name", "lastName") or ""),
    )
    def_first, def_middle, def_last = _split_three(full_name)
    if first:
        def_first = first.split()[0] if first else def_first
    if last:
        def_last = last

    indemnitor_name = str(
        data.get("indemnitor_name") or _person_text(indemnitor, "name", "full_name") or ""
    ).strip()
    ind_first, ind_middle, ind_last = _split_three(indemnitor_name)
    ind_first = _person_text(indemnitor, "first_name", "firstName") or ind_first
    ind_middle = _person_text(indemnitor, "middle_name", "middleName") or ind_middle
    ind_last = _person_text(indemnitor, "last_name", "lastName") or ind_last

    bond_amount = _safe_float(data.get("bond_amount", 0))
    premium = _safe_float(data.get("premium_amount") or data.get("premium") or 0)
    if premium <= 0 and bond_amount > 0:
        premium = max(100.0, bond_amount * 0.10)

    charge_raw = data.get("charge") or data.get("charges") or ""
    # Full charge text. The second line is not a shortened remainder.
    charge_line1, charge_line2 = str(charge_raw or "").strip(), ""
    if not charge_line1:
        charge_line1 = "No Charge Specified"

    court_date, court_time = _split_court_datetime(
        data.get("court_date") or data.get("defendant_court_date") or "",
        data.get("court_time") or data.get("defendant_court_time") or "",
    )
    if not court_date:
        court_date = "TBN"
    if court_time and str(court_date).upper() != "TBN":
        court_datetime = f"{court_date} {court_time}".strip()
    else:
        court_datetime = court_date

    date_parts = _parse_date_parts(data.get("bond_date") or data.get("date") or "")
    poa = str(data.get("poa_number") or "").strip()
    poa_list = data.get("poa_numbers") if isinstance(data.get("poa_numbers"), list) else []
    poa_slots = [str(x).strip() for x in poa_list if str(x).strip()]
    if poa and (not poa_slots or poa_slots[0] != poa):
        poa_slots = [poa] + [p for p in poa_slots if p != poa]
    while len(poa_slots) < 4:
        poa_slots.append("")

    digits = "".join(ch for ch in (poa_slots[0] or poa) if ch.isdigit())
    receipt_no = digits[-6:] if digits else ""

    from dashboard.services.docuseal_service import resolve_writing_agent

    agent, license_no = resolve_writing_agent(data)

    county = str(data.get("county") or data.get("defendant_county") or "").strip()
    address = str(
        data.get("address") or data.get("defendant_address") or _person_text(defendant, "address") or ""
    ).strip()

    ctx: Dict[str, str] = {
        "defendant_name": full_name,
        "defendant_first": def_first,
        "defendant_middle": def_middle,
        "defendant_last": def_last,
        "defendant_address": address,
        "defendant_city": str(data.get("defendant_city") or _person_text(defendant, "city") or ""),
        "defendant_state": str(data.get("defendant_state") or _person_text(defendant, "state") or ""),
        "defendant_zip": str(data.get("defendant_zip") or _person_text(defendant, "zip") or ""),
        "defendant_phone": str(data.get("defendant_phone") or _person_text(defendant, "phone") or ""),
        "defendant_email": str(data.get("defendant_email") or _person_text(defendant, "email") or ""),
        "defendant_dob": str(data.get("dob") or data.get("defendant_dob") or _person_text(defendant, "dob") or ""),
        "defendant_dl": str(data.get("defendant_dl") or _person_text(defendant, "dl") or ""),
        "defendant_dl_state": str(data.get("defendant_dl_state") or _person_text(defendant, "dl_state") or ""),
        "defendant_ssn": str(data.get("defendant_ssn") or _person_text(defendant, "ssn") or ""),
        "defendant_alias": str(data.get("defendant_alias") or _person_text(defendant, "alias") or ""),
        "defendant_employer": str(data.get("defendant_employer") or _person_text(defendant, "employer") or ""),
        "defendant_employer_phone": str(data.get("defendant_employer_phone") or ""),
        "defendant_employer_address": str(data.get("defendant_employer_address") or ""),
        "defendant_employer_how_long": str(data.get("defendant_employer_how_long") or ""),
        "defendant_boss": str(data.get("defendant_boss") or ""),
        "defendant_previous_employment": str(data.get("defendant_previous_employment") or ""),
        "defendant_previous_how_long": str(data.get("defendant_previous_how_long") or ""),
        "defendant_address_how_long": str(data.get("defendant_address_how_long") or ""),
        "defendant_former_address": str(data.get("defendant_former_address") or ""),
        "defendant_former_how_long": str(data.get("defendant_former_how_long") or ""),
        "defendant_height": str(data.get("defendant_height") or _person_text(defendant, "height") or ""),
        "defendant_weight": str(data.get("defendant_weight") or _person_text(defendant, "weight") or ""),
        "defendant_eyes": str(data.get("defendant_eyes") or ""),
        "defendant_hair": str(data.get("defendant_hair") or ""),
        "defendant_race": str(data.get("defendant_race") or ""),
        "defendant_tattoos": str(data.get("defendant_tattoos") or ""),
        "defendant_spouse_name": str(data.get("defendant_spouse_name") or ""),
        "defendant_spouse_phone": str(data.get("defendant_spouse_phone") or ""),
        "defendant_spouse_address": str(data.get("defendant_spouse_address") or ""),
        "defendant_spouse_employer": str(data.get("defendant_spouse_employer") or ""),
        "defendant_social_login": str(data.get("defendant_social_login") or ""),
        "defendant_social_password": str(data.get("defendant_social_password") or ""),
        "children_names_ages_1": str(data.get("children_names_ages_1") or data.get("children_names_ages") or ""),
        "children_school_1": str(data.get("children_school_1") or data.get("children_school") or ""),
        "children_school_2": str(data.get("children_school_2") or ""),
        "def_parent_name": str(data.get("def_parent_name") or ""),
        "def_parent_address": str(data.get("def_parent_address") or ""),
        "def_parent_phone": str(data.get("def_parent_phone") or ""),
        "def_best_friend_name": str(data.get("def_best_friend_name") or ""),
        "def_best_friend_address": str(data.get("def_best_friend_address") or ""),
        "def_best_friend_phone": str(data.get("def_best_friend_phone") or ""),
        "def_attorney_name": str(data.get("def_attorney_name") or ""),
        "def_attorney_address": str(data.get("def_attorney_address") or ""),
        "def_attorney_phone": str(data.get("def_attorney_phone") or ""),
        "def_spouse_parent_name": str(
            data.get("def_spouse_parent_name") or data.get("defendant_spouse_name") or ""
        ),
        "def_spouse_parent_address": str(
            data.get("def_spouse_parent_address") or data.get("defendant_spouse_address") or ""
        ),
        "def_spouse_parent_phone": str(
            data.get("def_spouse_parent_phone") or data.get("defendant_spouse_phone") or ""
        ),
        "def_sibling_1_name": str(data.get("def_sibling_1_name") or data.get("sibling_1_name") or ""),
        "def_sibling_1_address": str(data.get("def_sibling_1_address") or data.get("sibling_1_address") or ""),
        "def_sibling_1_phone": str(data.get("def_sibling_1_phone") or data.get("sibling_1_phone") or ""),
        "def_sibling_2_name": str(data.get("def_sibling_2_name") or data.get("sibling_2_name") or ""),
        "def_sibling_2_address": str(data.get("def_sibling_2_address") or data.get("sibling_2_address") or ""),
        "def_sibling_2_phone": str(data.get("def_sibling_2_phone") or data.get("sibling_2_phone") or ""),
        "def_sibling_3_name": str(data.get("def_sibling_3_name") or data.get("sibling_3_name") or ""),
        "def_sibling_3_address": str(data.get("def_sibling_3_address") or data.get("sibling_3_address") or ""),
        "def_sibling_3_phone": str(data.get("def_sibling_3_phone") or data.get("sibling_3_phone") or ""),
        "def_vehicle_year": str(data.get("def_vehicle_year") or ""),
        "def_vehicle_make": str(data.get("def_vehicle_make") or ""),
        "def_vehicle_model": str(data.get("def_vehicle_model") or ""),
        "def_vehicle_color": str(data.get("def_vehicle_color") or ""),
        "def_vehicle_plate": str(data.get("def_vehicle_plate") or ""),
        "def_vehicle_purchase_location": str(data.get("def_vehicle_purchase_location") or ""),
        "def_vehicle_amount_owed": str(data.get("def_vehicle_amount_owed") or ""),
        "def_vehicle_lender": str(data.get("def_vehicle_lender") or ""),
        "def_remarks": str(data.get("def_remarks") or data.get("remarks") or ""),
        "indemnitor_name": indemnitor_name,
        "indemnitor_first": ind_first,
        "indemnitor_middle": ind_middle,
        "indemnitor_last": ind_last,
        "indemnitor_address": str(data.get("indemnitor_address") or _person_text(indemnitor, "address") or ""),
        "indemnitor_city": str(data.get("indemnitor_city") or _person_text(indemnitor, "city") or ""),
        "indemnitor_state": str(data.get("indemnitor_state") or _person_text(indemnitor, "state") or ""),
        "indemnitor_zip": str(data.get("indemnitor_zip") or _person_text(indemnitor, "zip") or ""),
        "indemnitor_phone": str(data.get("indemnitor_phone") or _person_text(indemnitor, "phone") or ""),
        "indemnitor_phone2": str(data.get("indemnitor_phone2") or ""),
        "indemnitor_email": str(data.get("indemnitor_email") or _person_text(indemnitor, "email") or ""),
        "indemnitor_dob": str(data.get("indemnitor_dob") or _person_text(indemnitor, "dob") or ""),
        "indemnitor_ssn": str(data.get("indemnitor_ssn") or ""),
        "indemnitor_employer": str(data.get("indemnitor_employer") or ""),
        "indemnitor_employer_address": str(data.get("indemnitor_employer_address") or ""),
        "indemnitor_spouse_name": str(data.get("indemnitor_spouse_name") or ""),
        "indemnitor_spouse_dob": str(data.get("indemnitor_spouse_dob") or ""),
        "indemnitor_spouse_email": str(data.get("indemnitor_spouse_email") or ""),
        "indemnitor_spouse_employer": str(data.get("indemnitor_spouse_employer") or ""),
        "indemnitor_spouse_employer_address": str(data.get("indemnitor_spouse_employer_address") or ""),
        "reference_1_name": str(data.get("reference_1_name") or ""),
        "reference_1_address": str(data.get("reference_1_address") or ""),
        "reference_1_phone": str(data.get("reference_1_phone") or ""),
        "reference_2_name": str(data.get("reference_2_name") or ""),
        "reference_2_address": str(data.get("reference_2_address") or ""),
        "reference_2_phone": str(data.get("reference_2_phone") or ""),
        "reference_3_name": str(data.get("reference_3_name") or ""),
        "reference_3_address": str(data.get("reference_3_address") or ""),
        "reference_3_phone": str(data.get("reference_3_phone") or ""),
        "county": county,
        "state": "FL",
        "court_type": str(data.get("court_type") or data.get("defendant_court_type") or ""),
        "court_time": court_time if str(court_date).upper() != "TBN" else "",
        "court_datetime": court_datetime,
        "booking_number": str(data.get("booking_number") or data.get("defendant_booking_number") or "").strip(),
        "case_number": str(data.get("case_number") or data.get("defendant_case_number") or "").strip(),
        "poa_number": poa_slots[0],
        "poa_numbers": ", ".join(p for p in poa_slots if p),
        "poa_1": poa_slots[0],
        "poa_2": poa_slots[1],
        "poa_3": poa_slots[2],
        "poa_4": poa_slots[3],
        "charge_line_1": charge_line1,
        "charge_line_2": charge_line2,
        "charges_summary": charge_line1,
        "bond_amount_numeric": f"${bond_amount:,.2f}" if bond_amount else "",
        "bond_amount_words": _amount_to_words(bond_amount) if bond_amount else "",
        "premium_numeric": f"${premium:,.2f}" if premium else "",
        "premium_words": _amount_to_words(premium) if premium else "",
        "day": date_parts["day"],
        "month": date_parts["month"],
        "year": date_parts["year"],
        "today_date": date_parts["formatted"],
        "agent_name": agent,
        "agent_license": license_no,
        "agency_name": AGENCY_NAME,
        "collateral": str(data.get("collateral") or "Indemnity Agreement, Promissory Note"),
        "collateral_description": str(data.get("collateral_description") or ""),
        "collateral_receipt_number": str(data.get("collateral_receipt_number") or receipt_no),
        "who_signed": str(data.get("who_signed") or "defendant and family/friends"),
        "transfer_agent": str(data.get("transfer_agent") or ""),
        "relationship": str(data.get("relationship") or data.get("indemnitor_relationship") or ""),
        "contacted_by": str(data.get("contacted_by") or ""),
        "card_fee_percent": str(data.get("card_fee_percent") or ""),
        "collateral_promissory_note": "",
        "collateral_indemnity": "",
        "collateral_mortgage": "",
    }
    return ctx


def _widget_names(page) -> set:
    return {w.field_name for w in (page.widgets() or []) if w.field_name}


def stamp_document_widgets(doc: fitz.Document, slug: str) -> int:
    """Add spec widgets that are not already on the page. Returns how many were added.

    Existing appearance-bond widgets are left where the carrier placed them.
    Zero-area placeholder signatures are removed first.
    """
    added = 0
    if not doc.page_count:
        return 0
    page = doc[0]
    for widget in list(page.widgets() or []):
        rect = widget.rect
        if abs(rect.width) < 1 and abs(rect.height) < 1:
            page.delete_widget(widget)
    spec_fields = list(fields_for(slug))
    spec_by_name: Dict[str, Dict[str, Any]] = {}
    for field in spec_fields:
        spec_by_name.setdefault(field["name"], field)
    if slug != "appearance-bond":
        for widget in list(page.widgets() or []):
            name = widget.field_name or ""
            field = spec_by_name.get(name)
            if field is None:
                page.delete_widget(widget)
                continue
            target = fitz.Rect(field["x"], field["y"], field["x"] + field["w"], field["y"] + field["h"])
            moved = (
                abs(widget.rect.x0 - target.x0) > 1.5
                or abs(widget.rect.y0 - target.y0) > 1.5
                or abs(widget.rect.x1 - target.x1) > 1.5
                or abs(widget.rect.y1 - target.y1) > 1.5
            )
            if moved:
                page.delete_widget(widget)
    present = _widget_names(page)
    # Appearance bond already has one AgentField name covering both lines.
    seen_names = set(present)
    for field in spec_fields:
        if field["name"] in seen_names:
            continue
        if slug == "appearance-bond":
            continue
        widget = fitz.Widget()
        kind = field["type"]
        if kind == "checkbox":
            widget.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
        elif kind == "signature":
            widget.field_type = fitz.PDF_WIDGET_TYPE_SIGNATURE
        else:
            widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.field_name = field["name"]
        widget.rect = fitz.Rect(field["x"], field["y"], field["x"] + field["w"], field["y"] + field["h"])
        widget.border_width = 0
        widget.text_fontsize = 8
        if kind == "text":
            widget.field_value = ""
        page.add_widget(widget)
        if kind == "text":
            for placed in page.widgets() or []:
                if placed.field_name == field["name"] and abs(placed.rect.x0 - field["x"]) < 1:
                    _clear_widget_value(placed)
                    break
        seen_names.add(field["name"])
        added += 1
    return added


def values_for_document(slug: str, data: Optional[dict]) -> Dict[str, str]:
    ctx = build_palmetto_context(data)
    values: Dict[str, str] = {}
    for field in fields_for(slug):
        if field["type"] != "text":
            continue
        source = field["data_source"]
        values[field["name"]] = ctx.get(source, "") if source else ""
    return values


# Placement slug → paperwork file slug. The information sheet keeps the
# historical filename so the print stitch still resolves surety-terms.
_FILE_SLUG = {
    "bail-bond-information-sheet-palmetto": "surety-terms",
}


def fill_palmetto_document(slug: str, data: Optional[dict]) -> bytes:
    """Fill one Palmetto carrier PDF. Appearance bonds use ``fill_palmetto_bond``."""
    if slug not in PAGE_SIZE:
        raise KeyError(f"No Palmetto placement spec for {slug}")
    if slug == "appearance-bond":
        return fill_palmetto_bond(dict(data or {}))
    path = get_template_path(_FILE_SLUG.get(slug, slug), "palmetto")
    if not path.is_file():
        raise FileNotFoundError(f"Palmetto blank missing: {path}")
    doc = fitz.open(str(path))
    try:
        stamp_document_widgets(doc, slug)
        values = values_for_document(slug, data)
        for page in doc:
            _apply_field_values(page, values)
        buf = io.BytesIO()
        doc.save(buf)
    finally:
        doc.close()
    buf.seek(0)
    return buf.read()


def fill_palmetto_packet_forms(data: Optional[dict]) -> Dict[str, bytes]:
    """One filled PDF per Palmetto carrier form. Does not stitch OSI shared legal."""
    return {slug: fill_palmetto_document(slug, data) for slug in PAGE_SIZE}
