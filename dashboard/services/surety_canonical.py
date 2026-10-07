"""
Canonical BondCase fields for data-driven surety packets.

A published surety version maps PDF widgets (or staff-placed boxes) onto
these ids. Publish is blocked until every required rule is mapped. The
resolver used for NEW sureties does not invent amounts, POA numbers,
premiums, phones, or emails. OSI and Palmetto v1 keep their historical
appearance-bond recipes (see bond_pdf_service) so existing filled output
stays the same.

Required rules (a publish must satisfy every row):

  defendant.identity     defendant.full_name
                         OR defendant.first_name AND defendant.last_name
  defendant.county       defendant.county
  booking.number         booking.number
  case.number            case.number
  charge.description     charge.description
  charge.bond_amount     charge.bond_amount
  charge.poa_number      charge.poa_number
  bond.premium_amount    bond.premium_amount
  bond.execution_date    bond.execution_date
                         OR day + month + (year OR year_yy)

When repeat-per-charge is on, the repeating form itself must map
charge.description, charge.bond_amount, and charge.poa_number.

Recommended (warning only — Palmetto's appearance bond has no indemnitor
widget, and most legal PDFs only carry a signature widget):
  indemnitor.name or indemnitor.with_defendant
  collateral.description
  a signature or date position
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

# (rule id, label, any_of groups). A group is satisfied when every id in it is mapped.
REQUIRED_RULES: Tuple[Dict[str, Any], ...] = (
    {
        "id": "defendant.identity",
        "label": "Defendant name",
        "any_of": (
            ("defendant.full_name",),
            ("defendant.first_name", "defendant.last_name"),
        ),
    },
    {
        "id": "defendant.county",
        "label": "County",
        "any_of": (("defendant.county",),),
    },
    {
        "id": "booking.number",
        "label": "Booking / arrest number",
        "any_of": (("booking.number",),),
    },
    {
        "id": "case.number",
        "label": "Court case number",
        "any_of": (("case.number",),),
    },
    {
        "id": "charge.description",
        "label": "Charge description",
        "any_of": (("charge.description",),),
    },
    {
        "id": "charge.bond_amount",
        "label": "Bond amount",
        "any_of": (("charge.bond_amount",),),
    },
    {
        "id": "charge.poa_number",
        "label": "POA number for the charge",
        "any_of": (("charge.poa_number",),),
    },
    {
        "id": "bond.premium_amount",
        "label": "Premium amount",
        "any_of": (("bond.premium_amount",),),
    },
    {
        "id": "bond.execution_date",
        "label": "Execution date",
        "any_of": (
            ("bond.execution_date",),
            ("bond.execution_day", "bond.execution_month", "bond.execution_year"),
            ("bond.execution_day", "bond.execution_month", "bond.execution_year_yy"),
        ),
    },
)

REPEAT_CHARGE_FIELDS: Tuple[str, ...] = (
    "charge.description",
    "charge.bond_amount",
    "charge.poa_number",
)

# Values that must be present (non-empty) before a fail-closed surety PDF
# is written. Legacy OSI/Palmetto recipes are not held to this gate.
FAIL_CLOSED_VALUE_FIELDS: Tuple[str, ...] = (
    "charge.description",
    "charge.bond_amount",
    "charge.poa_number",
    "bond.premium_amount",
    "booking.number",
    "case.number",
    "defendant.county",
)

CANONICAL_FIELDS: Tuple[Dict[str, str], ...] = (
    {"id": "defendant.full_name", "label": "Defendant full name", "group": "defendant"},
    {"id": "defendant.first_name", "label": "Defendant first name", "group": "defendant"},
    {"id": "defendant.last_name", "label": "Defendant last name", "group": "defendant"},
    {"id": "defendant.county", "label": "County", "group": "defendant"},
    {"id": "defendant.address", "label": "Defendant address", "group": "defendant"},
    {"id": "defendant.dob", "label": "Defendant date of birth", "group": "defendant"},
    {"id": "defendant.phone", "label": "Defendant phone", "group": "defendant"},
    {"id": "defendant.email", "label": "Defendant email", "group": "defendant"},
    {"id": "indemnitor.name", "label": "Indemnitor name", "group": "indemnitor"},
    {"id": "indemnitor.with_defendant", "label": "Indemnitor and defendant names", "group": "indemnitor"},
    {"id": "indemnitor.address", "label": "Indemnitor address", "group": "indemnitor"},
    {"id": "indemnitor.phone", "label": "Indemnitor phone", "group": "indemnitor"},
    {"id": "indemnitor.email", "label": "Indemnitor email", "group": "indemnitor"},
    {"id": "booking.number", "label": "Booking / arrest number", "group": "booking"},
    {"id": "case.number", "label": "Court case number", "group": "case"},
    {"id": "charge.description", "label": "Charge (line 1)", "group": "charge"},
    {"id": "charge.description_line2", "label": "Charge (line 2)", "group": "charge"},
    {"id": "charge.bond_amount", "label": "Bond amount", "group": "charge"},
    {"id": "charge.poa_number", "label": "POA number", "group": "charge"},
    {"id": "bond.premium_amount", "label": "Premium (numeric)", "group": "bond"},
    {"id": "bond.premium_words", "label": "Premium (words)", "group": "bond"},
    {"id": "bond.execution_date", "label": "Execution date", "group": "bond"},
    {"id": "bond.execution_day", "label": "Execution day", "group": "bond"},
    {"id": "bond.execution_month", "label": "Execution month", "group": "bond"},
    {"id": "bond.execution_year", "label": "Execution year", "group": "bond"},
    {"id": "bond.execution_year_yy", "label": "Execution year (2-digit)", "group": "bond"},
    {"id": "court.date", "label": "Court date", "group": "court"},
    {"id": "court.time", "label": "Court time", "group": "court"},
    {"id": "court.datetime", "label": "Court date and time", "group": "court"},
    {"id": "court.type", "label": "Court type", "group": "court"},
    {"id": "collateral.description", "label": "Collateral", "group": "collateral"},
    {"id": "agent.name", "label": "Writing agent name", "group": "agent"},
    {"id": "agent.license", "label": "Writing agent license", "group": "agent"},
    {"id": "agency.name", "label": "Agency name", "group": "agency"},
    {"id": "agency.details", "label": "Agency details", "group": "agency"},
)

_CANONICAL_IDS = frozenset(row["id"] for row in CANONICAL_FIELDS)

# Normalized PDF field name → canonical id. Used only as a suggestion.
_SUGGESTIONS: Tuple[Tuple[str, str], ...] = (
    ("deflastname", "defendant.last_name"),
    ("lastname", "defendant.last_name"),
    ("defendantlastname", "defendant.last_name"),
    ("deffirstname", "defendant.first_name"),
    ("firstname", "defendant.first_name"),
    ("defendantfirstname", "defendant.first_name"),
    ("defendantnamefield", "defendant.full_name"),
    ("defendantname", "defendant.full_name"),
    ("defname", "defendant.full_name"),
    ("defendantfullname", "defendant.full_name"),
    ("defcounty", "defendant.county"),
    ("countyfield", "defendant.county"),
    ("county", "defendant.county"),
    ("defaddress", "defendant.address"),
    ("defendantaddress", "defendant.address"),
    ("address", "defendant.address"),
    ("arrestcaseno", "booking.number"),
    ("arrestnumberfield", "booking.number"),
    ("bookingnumber", "booking.number"),
    ("arrestnumber", "booking.number"),
    ("casenum", "case.number"),
    ("casenumberfield", "case.number"),
    ("casenumber", "case.number"),
    ("caseno", "case.number"),
    ("defcharge1", "charge.description"),
    ("chargesfield1", "charge.description"),
    ("chargestfield1", "charge.description"),
    ("charge", "charge.description"),
    ("offense", "charge.description"),
    ("defcharge1line2", "charge.description_line2"),
    ("chargesfield2", "charge.description_line2"),
    ("bondamountcharge1", "charge.bond_amount"),
    ("numericbondamount", "charge.bond_amount"),
    ("bondamount", "charge.bond_amount"),
    ("powernum", "charge.poa_number"),
    ("powernumfield", "charge.poa_number"),
    ("poanumber", "charge.poa_number"),
    ("power", "charge.poa_number"),
    ("numericpremiumamount", "bond.premium_amount"),
    ("calculatedpremiumfield", "bond.premium_amount"),
    ("premiumamount", "bond.premium_amount"),
    ("premium", "bond.premium_amount"),
    ("writtenpremiumamount", "bond.premium_words"),
    ("writtenpremiumamountfield", "bond.premium_words"),
    ("premiumwords", "bond.premium_words"),
    ("daydd", "bond.execution_day"),
    ("dayfield", "bond.execution_day"),
    ("month", "bond.execution_month"),
    ("monthwrittenfield", "bond.execution_month"),
    ("yearyy", "bond.execution_year_yy"),
    ("yearyyyyfield", "bond.execution_year"),
    ("courtdate", "court.date"),
    ("courttime", "court.time"),
    ("courtdateandtimefield", "court.datetime"),
    ("defcourttype", "court.type"),
    ("circofield", "court.type"),
    ("courttype", "court.type"),
    ("indnameanddefname", "indemnitor.with_defendant"),
    ("indemnitorname", "indemnitor.name"),
    ("indname", "indemnitor.name"),
    ("collateralfield", "collateral.description"),
    ("collateral", "collateral.description"),
    ("bondagentname", "agent.name"),
    ("agentfield", "agent.name"),
    ("agentname", "agent.name"),
    ("bondagentlicensenum", "agent.license"),
    ("agentbaillicnumfield", "agent.license"),
    ("agentlicense", "agent.license"),
    ("agencydetails", "agency.details"),
    ("agencyfield", "agency.name"),
    ("agencyname", "agency.name"),
)


def canonical_catalog() -> List[Dict[str, str]]:
    return [dict(row) for row in CANONICAL_FIELDS]


def required_rule_ids() -> List[str]:
    return [row["id"] for row in REQUIRED_RULES]


def is_canonical_id(raw: object) -> bool:
    return str(raw or "").strip() in _CANONICAL_IDS


def _norm_name(raw: object) -> str:
    return "".join(ch for ch in str(raw or "").lower() if ch.isalnum())


def suggest_canonical(field_name: object) -> Tuple[str, float]:
    """Best-effort canonical id for a PDF field name. Empty when unknown."""
    key = _norm_name(field_name)
    if not key:
        return "", 0.0
    for alias, canonical in _SUGGESTIONS:
        if key == alias:
            return canonical, 0.95
    for alias, canonical in _SUGGESTIONS:
        if len(alias) >= 6 and (alias in key or key in alias):
            return canonical, 0.6
    return "", 0.0


def mapped_ids_from_form(form: Mapping[str, Any]) -> Set[str]:
    found: Set[str] = set()
    for bucket in ("fields", "placed_fields"):
        for field in form.get(bucket) or []:
            if not isinstance(field, Mapping):
                continue
            canonical = str(field.get("canonical") or "").strip()
            if canonical in _CANONICAL_IDS:
                found.add(canonical)
    return found


def mapped_ids(version: Mapping[str, Any]) -> Set[str]:
    found: Set[str] = set()
    for form in version.get("forms") or []:
        if isinstance(form, Mapping):
            found |= mapped_ids_from_form(form)
    return found


def _repeat_form(version: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    settings = version.get("settings") if isinstance(version.get("settings"), Mapping) else {}
    repeat = settings.get("repeat_per_charge") if isinstance(settings.get("repeat_per_charge"), Mapping) else {}
    if not repeat.get("enabled"):
        return None
    forms = [f for f in (version.get("forms") or []) if isinstance(f, Mapping)]
    wanted = str(repeat.get("form_id") or "").strip()
    if wanted:
        for form in forms:
            if str(form.get("form_id") or "") == wanted:
                return form
    for form in forms:
        if str(form.get("role") or "") == "repeat_per_charge":
            return form
    return forms[0] if forms else None


def validate_publish(version: Mapping[str, Any]) -> Dict[str, Any]:
    """Block when a required canonical rule is unmapped."""
    mapped = mapped_ids(version)
    missing: List[str] = []
    for rule in REQUIRED_RULES:
        options: Sequence[Sequence[str]] = rule["any_of"]
        if not any(all(field in mapped for field in group) for group in options):
            missing.append(rule["id"])

    repeat_form = _repeat_form(version)
    if repeat_form is not None:
        on_form = mapped_ids_from_form(repeat_form)
        for field in REPEAT_CHARGE_FIELDS:
            if field not in on_form:
                missing.append(f"repeat_per_charge:{field}")

    warnings: List[str] = []
    if "indemnitor.name" not in mapped and "indemnitor.with_defendant" not in mapped:
        warnings.append("indemnitor.name")
    if "collateral.description" not in mapped:
        warnings.append("collateral.description")
    has_mark = False
    for form in version.get("forms") or []:
        if not isinstance(form, Mapping):
            continue
        if form.get("signatures") or form.get("dates"):
            has_mark = True
        for field in form.get("fields") or []:
            if isinstance(field, Mapping) and str(field.get("type") or "").lower() == "signature":
                has_mark = True
    if not has_mark:
        warnings.append("signature_or_date_position")

    settings = version.get("settings") if isinstance(version.get("settings"), Mapping) else {}
    prefixes = settings.get("poa_prefixes") or []
    if not prefixes:
        warnings.append("poa_prefixes")

    # Stable order, no duplicates.
    seen = set()
    missing_unique = []
    for item in missing:
        if item not in seen:
            seen.add(item)
            missing_unique.append(item)

    return {
        "ok": not missing_unique,
        "missing": missing_unique,
        "warnings": warnings,
        "mapped": sorted(mapped),
        "required": required_rule_ids(),
    }


def iter_required_ids() -> Iterable[str]:
    for rule in REQUIRED_RULES:
        yield rule["id"]
