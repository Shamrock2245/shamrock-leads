"""
Fill a surety PDF from the active published version.

OSI and Palmetto v1 use the historical appearance-bond recipes so widget
values stay identical to fill_osi_bond / fill_palmetto_bond. Every other
published version uses the fail-closed canonical resolver: missing amounts,
POA numbers, premiums, phones, and emails stay blank. Blank writes go through
bond_pdf_service._apply_field_values, which clears a template /V.
"""
from __future__ import annotations

import io
from typing import Any, Dict, List, Mapping, Optional

import fitz

from dashboard.services.surety_canonical import FAIL_CLOSED_VALUE_FIELDS
from dashboard.services.surety_template_store import (
    SuretyTemplateError,
    active_published,
    load_form_bytes,
)

class SuretyDataMissing(SuretyTemplateError):
    def __init__(self, missing: List[str]):
        self.missing = list(missing)
        super().__init__(
            "Surety PDF blocked. Missing canonical values: " + ", ".join(self.missing),
            code="surety_data_missing",
        )


# Obviously fake. Previews and tests use this. Never a production record.
FAKE_PREVIEW_BOND: Dict[str, Any] = {
    "name": "SAMPLE, NOT A PERSON",
    "defendant_name": "SAMPLE, NOT A PERSON",
    "first_name": "NOT A PERSON",
    "last_name": "SAMPLE",
    "booking_number": "SAMPLE-BOOKING-000",
    "county": "Sample",
    "bond_amount": 1000,
    "charge": "SAMPLE CHARGE ONLY",
    "case_number": "00-SAMPLE-000",
    "court_date": "01/01/2000",
    "court_time": "9:00 AM",
    "court_type": "Sample Court",
    "address": "1 Sample Street, Sample City",
    "poa_number": "SAMPLE-POA-000",
    "bond_date": "01/01/2000",
    "indemnitor_name": "SAMPLE INDEMNITOR",
    "premium_amount": 100,
    "collateral": "SAMPLE COLLATERAL",
    "surety": "sample",
    "charge_details": [
        {
            "charge": "SAMPLE CHARGE ONLY",
            "bond_amount": 1000,
            "case_number": "00-SAMPLE-000",
            "poa_number": "SAMPLE-POA-000",
            "court_date": "01/01/2000",
            "court_time": "9:00 AM",
        },
        {
            "charge": "SAMPLE CHARGE TWO",
            "bond_amount": 2000,
            "case_number": "00-SAMPLE-001",
            "poa_number": "SAMPLE-POA-001",
            "court_date": "01/02/2000",
            "court_time": "10:00 AM",
        },
    ],
}


def _money(amount: float) -> str:
    return f"${amount:,.2f}"


def _paired_writing_agent(data: Mapping[str, Any]) -> tuple:
    """Name and license from one BOND_AGENTS lookup. Empty when the record has neither."""
    from dashboard.services.docuseal_service import _pair_from_agent_source

    pair = _pair_from_agent_source(data)
    if not pair:
        return "", ""
    return str(pair[0] or ""), str(pair[1] or "")


def resolve_fail_closed_canonical(data: Mapping[str, Any]) -> Dict[str, str]:
    """Format only values that are actually present. Never invent them."""
    from dashboard.bond_pdf_service import (
        _amount_to_words,
        _is_booking_as_case,
        _is_placeholder_charge,
        _parse_date_parts,
        _parse_defendant_name,
        _safe_float,
        _split_court_datetime,
    )

    data = data or {}
    full_name = str(data.get("name") or data.get("defendant_name") or "").strip()
    first, last = _parse_defendant_name(
        full_name,
        first_name=str(data.get("first_name") or ""),
        last_name=str(data.get("last_name") or ""),
    )
    booking = str(data.get("booking_number") or data.get("defendant_booking_number") or "").strip()
    county = str(data.get("county") or data.get("defendant_county") or "").strip()
    address = str(data.get("address") or data.get("defendant_address") or "").strip()
    case_number = str(data.get("case_number") or data.get("defendant_case_number") or "").strip()
    if _is_booking_as_case(case_number, booking):
        case_number = ""

    charge_raw = str(data.get("charge") or data.get("charges") or "").strip()
    if _is_placeholder_charge(charge_raw):
        charge_raw = ""
    # Full charge text on the primary line. Do not shorten it onto line 2.
    charge_line1, charge_line2 = charge_raw, ""

    court_date, court_time = _split_court_datetime(
        data.get("court_date") or data.get("defendant_court_date") or "",
        data.get("court_time") or data.get("defendant_court_time") or "",
    )
    if str(court_date).upper() in ("TBN", "TBD"):
        # An explicit TBN is data. A blank court date is not rewritten to TBN.
        if not str(data.get("court_date") or data.get("defendant_court_date") or "").strip():
            court_date = ""
    if str(court_date).upper() == "TBN":
        court_time = ""
    court_datetime = ""
    if court_date and court_time:
        court_datetime = f"{court_date} {court_time}".strip()
    elif court_date:
        court_datetime = court_date

    bond_present = data.get("bond_amount") not in (None, "")
    bond_amount = _safe_float(data.get("bond_amount")) if bond_present else 0.0
    bond_text = _money(bond_amount) if bond_present else ""

    premium_raw = data.get("premium_amount")
    if premium_raw in (None, "") and data.get("premium") not in (None, ""):
        premium_raw = data.get("premium")
    premium_present = premium_raw not in (None, "")
    premium_amount = _safe_float(premium_raw) if premium_present else 0.0
    premium_text = _money(premium_amount) if premium_present else ""
    premium_words = _amount_to_words(premium_amount) if premium_present and premium_amount > 0 else ""

    bond_date_raw = data.get("bond_date")
    if bond_date_raw:
        parts = _parse_date_parts(bond_date_raw)
    else:
        parts = {"day": "", "month": "", "year": "", "year_yy": "", "formatted": ""}

    indemnitor = str(data.get("indemnitor_name") or "").strip()
    ind_def = f"{indemnitor} / {full_name}" if indemnitor and full_name else (indemnitor or full_name)
    poa = str(data.get("poa_number") or "").strip()
    collateral = str(data.get("collateral") or "").strip()
    agent_name, agent_license = _paired_writing_agent(data)

    return {
        "defendant.full_name": full_name,
        "defendant.first_name": first,
        "defendant.last_name": last,
        "defendant.county": county,
        "defendant.address": address,
        "defendant.dob": str(data.get("defendant_dob") or "").strip(),
        "defendant.phone": str(data.get("defendant_phone") or "").strip(),
        "defendant.email": str(data.get("defendant_email") or "").strip(),
        "indemnitor.name": indemnitor,
        "indemnitor.with_defendant": ind_def,
        "indemnitor.address": str(data.get("indemnitor_address") or "").strip(),
        "indemnitor.phone": str(data.get("indemnitor_phone") or "").strip(),
        "indemnitor.email": str(data.get("indemnitor_email") or "").strip(),
        "booking.number": booking,
        "case.number": case_number,
        "charge.description": charge_line1,
        "charge.description_line2": charge_line2,
        "charge.bond_amount": bond_text,
        "charge.poa_number": poa,
        "bond.premium_amount": premium_text,
        "bond.premium_words": premium_words,
        "bond.execution_date": parts.get("formatted") or "",
        "bond.execution_day": parts.get("day") or "",
        "bond.execution_month": parts.get("month") or "",
        "bond.execution_year": parts.get("year") or "",
        "bond.execution_year_yy": parts.get("year_yy") or "",
        "court.date": court_date if court_date != "TBN" or str(data.get("court_date") or "").strip() else court_date,
        "court.time": court_time,
        "court.datetime": court_datetime,
        "court.type": str(data.get("court_type") or data.get("defendant_court_type") or "").strip(),
        "collateral.description": collateral,
        "agent.name": agent_name,
        "agent.license": agent_license,
        "agency.name": str(data.get("agency_name") or "").strip(),
        "agency.details": str(data.get("agency_details") or "").strip(),
    }


def project_form_values(form: Mapping[str, Any], canonical: Mapping[str, str]) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for bucket in ("fields", "placed_fields"):
        for field in form.get(bucket) or []:
            if not isinstance(field, Mapping):
                continue
            canonical_id = str(field.get("canonical") or "").strip()
            name = str(field.get("name") or "").strip()
            if not name or not canonical_id:
                continue
            values[name] = str(canonical.get(canonical_id) or "")
    return values


def _missing_fail_closed(form: Mapping[str, Any], canonical: Mapping[str, str]) -> List[str]:
    mapped = set()
    for bucket in ("fields", "placed_fields"):
        for field in form.get(bucket) or []:
            if isinstance(field, Mapping) and field.get("canonical"):
                mapped.add(str(field["canonical"]))
    missing = []
    identity_ok = bool(canonical.get("defendant.full_name") or (
        canonical.get("defendant.first_name") and canonical.get("defendant.last_name")
    ))
    if ("defendant.full_name" in mapped or "defendant.first_name" in mapped or "defendant.last_name" in mapped) and not identity_ok:
        missing.append("defendant.identity")
    for field in FAIL_CLOSED_VALUE_FIELDS:
        if field in mapped and not str(canonical.get(field) or "").strip():
            missing.append(field)
    date_mapped = any(
        key in mapped
        for key in (
            "bond.execution_date",
            "bond.execution_day",
            "bond.execution_month",
            "bond.execution_year",
            "bond.execution_year_yy",
        )
    )
    date_ok = bool(canonical.get("bond.execution_date") or (
        canonical.get("bond.execution_day") and canonical.get("bond.execution_month") and (
            canonical.get("bond.execution_year") or canonical.get("bond.execution_year_yy")
        )
    ))
    if date_mapped and not date_ok:
        missing.append("bond.execution_date")
    return missing


def _place_widgets(doc: fitz.Document, form: Mapping[str, Any], values: Mapping[str, str]) -> None:
    for field in form.get("placed_fields") or []:
        if not isinstance(field, Mapping):
            continue
        rect = field.get("rect")
        if not rect or len(rect) != 4:
            continue
        page_index = int(field.get("page") or 0)
        if page_index < 0 or page_index >= doc.page_count:
            continue
        page = doc[page_index]
        widget = fitz.Widget()
        widget.field_name = str(field.get("name") or "placed")
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.rect = fitz.Rect(*rect)
        widget.field_value = str(values.get(widget.field_name) or "")
        page.add_widget(widget)


def _stamp_sample_marks(doc: fitz.Document, form: Mapping[str, Any]) -> None:
    """Preview only. Draws the word SAMPLE inside staff-placed signature/date boxes."""
    for mark in list(form.get("signatures") or []) + list(form.get("dates") or []):
        if not isinstance(mark, Mapping):
            continue
        rect = mark.get("rect")
        if not rect or len(rect) != 4:
            continue
        page_index = int(mark.get("page") or 0)
        if page_index < 0 or page_index >= doc.page_count:
            continue
        page = doc[page_index]
        box = fitz.Rect(*rect)
        page.draw_rect(box, color=(0.6, 0.15, 0.15), width=0.6)
        label = "SAMPLE SIGNATURE" if mark.get("kind") != "date" else "SAMPLE DATE"
        page.insert_textbox(box, label, fontsize=8, color=(0.6, 0.15, 0.15), align=1)


def fill_pdf_bytes(
    pdf_bytes: bytes,
    field_values: Mapping[str, str],
    form: Optional[Mapping[str, Any]] = None,
    *,
    stamp_sample: bool = False,
) -> bytes:
    from dashboard.bond_pdf_service import _apply_field_values

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if form:
            _place_widgets(doc, form, field_values)
        for page in doc:
            _apply_field_values(page, dict(field_values))
        if stamp_sample and form:
            _stamp_sample_marks(doc, form)
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()
    finally:
        doc.close()


def _legacy_values(profile: str, data: Mapping[str, Any]) -> Dict[str, str]:
    from dashboard.bond_pdf_service import build_osi_field_values, build_palmetto_field_values

    if profile == "legacy_osi_appearance":
        values, _fonts = build_osi_field_values(dict(data))
        return values
    if profile == "legacy_palmetto_appearance":
        values, _fonts = build_palmetto_field_values(dict(data))
        return values
    raise SuretyTemplateError(f"Unknown legacy profile '{profile}'.", code="unknown_profile")


def _appearance_form(version: Mapping[str, Any]) -> Mapping[str, Any]:
    settings = version.get("settings") if isinstance(version.get("settings"), Mapping) else {}
    repeat = settings.get("repeat_per_charge") if isinstance(settings.get("repeat_per_charge"), Mapping) else {}
    forms = [f for f in (version.get("forms") or []) if isinstance(f, Mapping)]
    if not forms:
        raise SuretyTemplateError("Published version has no PDF.", code="pdf_missing")
    wanted = str(repeat.get("form_id") or "")
    for form in forms:
        if wanted and form.get("form_id") == wanted:
            return form
    for form in forms:
        if form.get("role") == "repeat_per_charge":
            return form
    return forms[0]


def field_values_for_version(version: Mapping[str, Any], data: Mapping[str, Any], *, strict: bool) -> Dict[str, str]:
    profile = str(version.get("value_profile") or "")
    if profile in ("legacy_osi_appearance", "legacy_palmetto_appearance"):
        return _legacy_values(profile, data)
    form = _appearance_form(version)
    canonical = resolve_fail_closed_canonical(data)
    if strict:
        missing = _missing_fail_closed(form, canonical)
        if missing:
            raise SuretyDataMissing(missing)
    return project_form_values(form, canonical)


def fill_published_appearance(surety_id: str, data: Mapping[str, Any], *, strict: Optional[bool] = None) -> bytes:
    """One appearance-bond PDF for one charge, from the active published version."""
    version = active_published(surety_id)
    if not version:
        raise SuretyTemplateError(
            f"No published surety template for '{surety_id}'.",
            code="no_published_template",
        )
    profile = str(version.get("value_profile") or "")
    if profile == "legacy_osi_appearance":
        from dashboard.bond_pdf_service import fill_osi_bond
        return fill_osi_bond(dict(data))
    if profile == "legacy_palmetto_appearance":
        from dashboard.bond_pdf_service import fill_palmetto_bond
        return fill_palmetto_bond(dict(data))
    form = _appearance_form(version)
    use_strict = True if strict is None else strict
    values = field_values_for_version(version, data, strict=use_strict)
    return fill_pdf_bytes(load_form_bytes(form), values, form, stamp_sample=False)


def _charge_slice(sample: Mapping[str, Any], index: int) -> Dict[str, Any]:
    data = dict(sample)
    rows = sample.get("charge_details") or []
    if isinstance(rows, list) and index < len(rows) and isinstance(rows[index], Mapping):
        row = rows[index]
        data["charge"] = row.get("charge") or ""
        data["bond_amount"] = row.get("bond_amount")
        data["case_number"] = row.get("case_number") or ""
        data["poa_number"] = row.get("poa_number") or ""
        data["court_date"] = row.get("court_date") or sample.get("court_date") or ""
        data["court_time"] = row.get("court_time") or ""
        if row.get("premium_amount") not in (None, ""):
            data["premium_amount"] = row.get("premium_amount")
        elif sample.get("premium_amount") not in (None, ""):
            data["premium_amount"] = sample.get("premium_amount")
    return data


def render_preview(version: Mapping[str, Any]) -> bytes:
    """Local PDF only. Fake sample data. No DocuSeal, email, or SMS."""
    from dashboard.services.surety_template_store import get_version

    stored = get_version(str(version.get("version_id") or ""), include_storage=True)
    if stored:
        version = stored
    sample = dict(FAKE_PREVIEW_BOND)
    sample["surety"] = version.get("surety_id") or "sample"
    forms = [f for f in (version.get("forms") or []) if isinstance(f, Mapping)]
    if not forms:
        raise SuretyTemplateError("Upload a PDF before preview.", code="pdf_required")
    settings = version.get("settings") if isinstance(version.get("settings"), Mapping) else {}
    repeat_on = bool((settings.get("repeat_per_charge") or {}).get("enabled"))
    charges = sample.get("charge_details") or []
    charge_count = len(charges) if isinstance(charges, list) and charges else 1
    out = fitz.open()
    try:
        for form in forms:
            repeats = charge_count if repeat_on and form.get("role") == "repeat_per_charge" else 1
            for index in range(repeats):
                data = _charge_slice(sample, index)
                profile = str(version.get("value_profile") or "")
                if profile in ("legacy_osi_appearance", "legacy_palmetto_appearance"):
                    values = _legacy_values(profile, data)
                else:
                    canonical = resolve_fail_closed_canonical(data)
                    values = project_form_values(form, canonical)
                blob = fill_pdf_bytes(load_form_bytes(form), values, form, stamp_sample=True)
                piece = fitz.open(stream=blob, filetype="pdf")
                out.insert_pdf(piece)
                piece.close()
        buf = io.BytesIO()
        out.save(buf)
        return buf.getvalue()
    finally:
        out.close()


def _with_computed_premium(bond_data: Mapping[str, Any]) -> Dict[str, Any]:
    """Copy bond data, filling premium from DocuSeal's statutory prefill when absent.

    The fail-closed resolver does not invent a premium. DocuSeal already
    computes one from the bond amount. The mapped-field gate uses that
    computed value, and still fails when the computation is empty.
    """
    data = dict(bond_data or {})
    if str(data.get("premium_amount") or data.get("premium") or "").strip():
        return data
    from dashboard.services.docuseal_service import DocuSealService

    prefill = DocuSealService.prefill_values_from_bond(data)
    premium = str(prefill.get("premium_amount") or "").strip()
    if premium:
        data["premium_amount"] = premium
    return data


def assert_mapped_submission_ready(surety_id: str, bond_data: Mapping[str, Any]) -> None:
    """Fail closed before DocuSeal when a mapped required value is empty."""
    version = active_published(surety_id)
    if not version or version.get("docuseal_field_mode") != "mapped":
        return
    data = _with_computed_premium(bond_data)
    canonical = resolve_fail_closed_canonical(data)
    missing: List[str] = []
    for form in version.get("forms") or []:
        if isinstance(form, Mapping):
            missing.extend(_missing_fail_closed(form, canonical))
    if missing:
        raise SuretyDataMissing(sorted(set(missing)))


def docuseal_mapped_aliases(surety_id: str, bond_data: Mapping[str, Any]) -> Dict[str, str]:
    """Extra DocuSeal value keys for a published mapped template.

    OSI/Palmetto seeds use canonical_prefill and return nothing, so the
    existing prefill dict is unchanged. Mapped sureties fail closed when a
    required value is empty, including a premium DocuSeal would have computed.
    """
    version = active_published(surety_id)
    if not version or version.get("docuseal_field_mode") != "mapped":
        return {}
    data = _with_computed_premium(bond_data)
    assert_mapped_submission_ready(surety_id, data)
    values = field_values_for_version(version, data, strict=True)
    return {key: val for key, val in values.items() if str(val or "").strip()}


def production_packet_parts(surety_id: str, bond_data: Mapping[str, Any]) -> Optional[List[bytes]]:
    """Every uploaded form for a mapped surety.

    Legacy OSI/Palmetto recipes return None so the caller keeps the
    one-PDF-per-charge appearance path. Repeat forms are emitted once per
    charge. Other forms are emitted once.
    """
    version = active_published(surety_id)
    if not version:
        return None
    profile = str(version.get("value_profile") or "")
    if profile in ("legacy_osi_appearance", "legacy_palmetto_appearance"):
        return None
    from dashboard.bond_pdf_service import normalize_charge_rows

    forms = [f for f in (version.get("forms") or []) if isinstance(f, Mapping)]
    if not forms:
        raise SuretyTemplateError("Published version has no PDF.", code="pdf_missing")
    settings = version.get("settings") if isinstance(version.get("settings"), Mapping) else {}
    repeat = settings.get("repeat_per_charge") if isinstance(settings.get("repeat_per_charge"), Mapping) else {}
    repeat_on = bool(repeat.get("enabled"))
    repeat_id = str(repeat.get("form_id") or "")
    rows = normalize_charge_rows(dict(bond_data))
    charge_count = max(1, len(rows))
    data = _with_computed_premium(bond_data)
    parts: List[bytes] = []
    for form in forms:
        is_repeat = form.get("role") == "repeat_per_charge" or (
            repeat_id and form.get("form_id") == repeat_id
        )
        repeats = charge_count if repeat_on and is_repeat else 1
        for index in range(repeats):
            sliced = _charge_slice(data, index) if is_repeat else dict(data)
            if is_repeat and index < len(rows):
                row = rows[index]
                sliced["charge"] = row.get("charge") or sliced.get("charge") or ""
                sliced["bond_amount"] = row.get("amount", sliced.get("bond_amount"))
                sliced["case_number"] = row.get("case_number") or sliced.get("case_number") or ""
                sliced["poa_number"] = row.get("poa_number") or sliced.get("poa_number") or ""
            canonical = resolve_fail_closed_canonical(sliced)
            missing = _missing_fail_closed(form, canonical)
            if missing:
                raise SuretyDataMissing(sorted(set(missing)))
            values = project_form_values(form, canonical)
            parts.append(fill_pdf_bytes(load_form_bytes(form), values, form, stamp_sample=False))
    return parts
