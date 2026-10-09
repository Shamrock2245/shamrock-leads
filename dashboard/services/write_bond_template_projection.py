"""Project Write Bond prefill onto the secondary field maps.

The appearance-bond seed (OSI) and ``PALMETTO_FIELDS`` (Palmetto rebuild
spec, template 6) are not the live DocuSeal widgets. Live template 1 and
template 5 are matched by field name in the golden test, from the checked-in
inventories. This module only projects the two secondary maps.

A spec field takes a value only from its allow-listed prefill keys.
Empty values are omitted. Signature and checkbox widgets are not prefilled.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from dashboard.palmetto_docuseal_apply import resolved_docuseal_name
from dashboard.palmetto_field_placement import PALMETTO_FIELDS
from dashboard.services.docuseal_signing_ux import STAFF_READONLY_FIELD_NAMES
from dashboard.services.surety_template_store import seed_version

_SPEC_PATH = (
    Path(__file__).resolve().parents[2]
    / "templates"
    / "palmetto"
    / "palmetto_docuseal_field_spec.json"
)

# Extra prefill keys that may fill a resolved template name. The placement's
# own name and data_source stay first, so a miss key cannot override a value
# that already landed on the right box.
_PALMETTO_WIRED_SOURCES: Dict[str, Tuple[str, ...]] = {
    # charge_line_1 resolves to charges_summary. ``charges`` is that same text.
    "charges_summary": ("charges",),
    # chargesField2 is named charge_line_2. The prefill copies offense_2 there.
    "charge_line_2": ("offense_2", "charge_2"),
    # CourtDateAndTimeField's data_source is court_datetime, not court_date.
    "court_datetime": ("court_date", "CourtDate"),
    # The words boxes read bond_amount_words. These two keys are the same words.
    "bond_amount_words": ("bond_amount_written", "full_bond_amount_words"),
}

# Confirmed misses with no Palmetto placement box. Do not invent one.
UNPLACED_MISS_KEYS = ("ssa_release_reason",)

_SKIP_TYPES = frozenset({"signature", "initials", "checkbox"})

# OSI appearance map canonical -> prefill keys, in order. Charge lines use
# the per-charge strings, not the joined summary.
_OSI_SOURCES: Dict[str, Tuple[str, ...]] = {
    "defendant.last_name": ("DefLastName",),
    "defendant.first_name": ("DefFirstName",),
    "defendant.county": ("county", "County"),
    "court.type": ("court_type", "CourtType"),
    "charge.bond_amount": ("bond_amount",),
    "charge.description": ("offense_1", "charge_1"),
    "charge.description_line2": ("offense_2", "charge_2"),
    "court.date": ("court_date", "CourtDate"),
    "court.time": ("court_time",),
    "case.number": ("case_number", "CaseNum"),
    "booking.number": ("booking_number",),
    "defendant.address": ("defendant_address", "DefAddress"),
    "bond.execution_day": ("today_day", "bond_date_day"),
    "bond.execution_month": ("today_month", "bond_date_month"),
    "bond.execution_year_yy": ("today_year_2digit", "bond_date_year_2digit"),
    "charge.poa_number": ("poa_number", "PowerNum"),
    "bond.premium_words": ("premium_words", "written_premium"),
    "bond.premium_amount": ("premium_amount", "numeric_premium_dollar"),
    "agent.name": ("agent_name", "AgentName", "bondsman_name"),
    "agent.license": ("agent_license", "AgentLicense", "bondsman_license"),
    "agency.details": ("agency_address",),
    "indemnitor.with_defendant": ("indemnitor_name", "defendant_name"),
}

# OSI appearance boxes are the bondsman's. The seed map has no role column.
_OSI_ROLE = "bondsman"


def palmetto_spec_rows() -> List[Dict[str, Any]]:
    """Checked-in Palmetto spec fields. Same rows as ``PALMETTO_FIELDS``."""
    document = json.loads(_SPEC_PATH.read_text(encoding="utf-8"))
    rows = document.get("fields") or []
    if not isinstance(rows, list):
        raise ValueError("Palmetto spec fields must be a list.")
    return rows


def spec_matches_placement() -> bool:
    """True when every spec row has the same name, document, and data source."""
    spec = {
        (row.get("document"), row.get("name"), row.get("data_source") or "")
        for row in palmetto_spec_rows()
    }
    placed = {
        (row.get("document"), row.get("name"), row.get("data_source") or "")
        for row in PALMETTO_FIELDS
    }
    return spec == placed


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first(values: Mapping[str, Any], keys: Sequence[str]) -> Tuple[str, str]:
    for key in keys:
        text = _text(values.get(key))
        if text:
            return key, text
    return "", ""


def _readonly(field_name: str, source: str) -> bool:
    return (
        field_name in STAFF_READONLY_FIELD_NAMES
        or source in STAFF_READONLY_FIELD_NAMES
    )


def _store(
    out: Dict[str, Dict[str, Any]],
    name: str,
    *,
    value: str,
    source: str,
    role: str,
) -> None:
    entry = {
        "value": value,
        "readonly": _readonly(name, source),
        "submitter_role": [role],
        "source": source,
    }
    previous = out.get(name)
    if previous is None:
        out[name] = entry
        return
    if previous["value"] != value or previous["readonly"] != entry["readonly"]:
        raise ValueError(
            f"Template field {name} got two values: {previous['source']} and {source}."
        )
    if role not in previous["submitter_role"]:
        previous["submitter_role"].append(role)
        previous["submitter_role"] = sorted(previous["submitter_role"])


def _palmetto_keys(field: Mapping[str, Any]) -> Tuple[str, Tuple[str, ...]]:
    resolved = resolved_docuseal_name(field)
    source = str(field.get("data_source") or "").strip()
    keys: List[str] = []
    for key in (resolved, source, *_PALMETTO_WIRED_SOURCES.get(resolved, ())):
        if key and key not in keys:
            keys.append(key)
    return resolved, tuple(keys)


def _project_palmetto(values: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for field in PALMETTO_FIELDS:
        if str(field.get("type") or "") in _SKIP_TYPES:
            continue
        if not str(field.get("data_source") or "").strip():
            continue
        resolved, keys = _palmetto_keys(field)
        source, text = _first(values, keys)
        if not text:
            continue
        role = str(field.get("role") or "").strip().lower()
        if not role:
            raise ValueError(f"Palmetto field {resolved} has no submitter role.")
        _store(out, resolved, value=text, source=source, role=role)
    return out


def _project_osi(values: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    seed = seed_version("osi")
    if not seed:
        raise ValueError("OSI field map seed is missing.")
    forms = [form for form in (seed.get("forms") or []) if isinstance(form, Mapping)]
    if not forms:
        raise ValueError("OSI field map has no form.")
    out: Dict[str, Dict[str, Any]] = {}
    for field in forms[0].get("fields") or []:
        name = str(field.get("name") or "").strip()
        canonical = str(field.get("canonical") or "").strip()
        keys = _OSI_SOURCES.get(canonical)
        if not name or not keys:
            raise ValueError(f"OSI field map row {name!r} / {canonical!r} has no source.")
        if canonical == "indemnitor.with_defendant":
            indemnitor = _text(values.get("indemnitor_name"))
            defendant = _text(values.get("defendant_name"))
            if not indemnitor or not defendant:
                continue
            _store(
                out,
                name,
                value=f"{indemnitor} / {defendant}",
                source="indemnitor_name",
                role=_OSI_ROLE,
            )
            continue
        source, text = _first(values, keys)
        if not text:
            continue
        _store(out, name, value=text, source=source, role=_OSI_ROLE)
    return out


def allowed_sources(surety_id: str, field_name: str) -> Tuple[str, ...]:
    """Prefill keys allowed to fill one projected template field."""
    surety = str(surety_id or "").strip().lower()
    if surety == "palmetto":
        found: List[str] = []
        for field in PALMETTO_FIELDS:
            resolved, keys = _palmetto_keys(field)
            if resolved == field_name:
                for key in keys:
                    if key not in found:
                        found.append(key)
        return tuple(found)
    if surety == "osi":
        if field_name == "IndNameandDefName":
            return ("indemnitor_name", "defendant_name")
        seed = seed_version("osi") or {}
        forms = seed.get("forms") or []
        if not forms:
            return ()
        for field in forms[0].get("fields") or []:
            if str(field.get("name") or "") == field_name:
                return _OSI_SOURCES.get(str(field.get("canonical") or ""), ())
        return ()
    raise ValueError(f"No template field map for surety {surety_id!r}.")


def project_template_fields(
    surety_id: str,
    values: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Map one prefill dict onto that surety's template fields.

    Keys are the template field names. Each entry has ``value``, ``readonly``,
    ``submitter_role``, and ``source`` (the prefill key that supplied it).
    """
    surety = str(surety_id or "").strip().lower()
    if surety == "palmetto":
        return _project_palmetto(values)
    if surety == "osi":
        return _project_osi(values)
    raise ValueError(f"No template field map for surety {surety_id!r}.")


def golden_fields(projected: Mapping[str, Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Drop the source key. Goldens store value, readonly, and submitter role."""
    return {
        name: {
            "value": entry["value"],
            "readonly": entry["readonly"],
            "submitter_role": list(entry["submitter_role"]),
        }
        for name, entry in projected.items()
    }
