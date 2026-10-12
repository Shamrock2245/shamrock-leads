"""Merge a Palmetto field spec into a live DocuSeal template.

A DocuSeal field update replaces the template's whole field list. Documents
this spec does not cover (FAQ pages, master waiver, SSA release) keep every
existing area. The paperwork header keeps every existing area and gains the
offense rows from this spec. A text, checkbox, or execution-date field whose
areas sit on more than one document is split: areas on uncovered documents
stay on the original field object (same uuid, name, and submitter). Areas on
covered documents are replaced by the spec.

Signature, initials, and date-signed fields keep the live area and role,
including areas on a covered document. ``today_date`` is the prefilled
execution date and still follows the spec. A name in
``SIGNATURE_GEOMETRY_EXCEPTIONS`` is the only one allowed to move or drop.

Covered text, date, and number boxes keep a live template 5 name when that
name is one ``prefill_values_from_bond`` already sends. The placement spec's
``app_*`` / ``ind_*`` / ``cr_*`` / ``bbis_*`` names are not what gets written.
The appearance bond is print/wet-ink and is omitted when it is not an
attachment on the template.
"""
from __future__ import annotations

import copy
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from dashboard.palmetto_field_placement import docuseal_fields

# Carrier documents the placement spec is allowed to replace.
COVERED_SLUGS = (
    "defendant-application",
    "indemnity-agreement",
    "collateral-receipt",
    "bail-bond-information-sheet-palmetto",
)

# Printed, not e-signed. Reported, never written onto template 5.
PRINT_ONLY_SLUGS = ("appearance-bond",)

# Live fields stay. Spec rows on these documents are added beside them.
APPEND_SLUGS = ("paperwork-header",)

CLONE_NAME = "shamrock-palmetto-paperwork-complete (field review)"

# Longer hints win. "promissory" is intentionally absent so a separate
# promissory attachment is kept rather than treated as the collateral receipt.
_DOC_HINTS = {
    "appearance-bond": (
        "official appearance bond",
        "appearance-bond",
        "appearance bond",
    ),
    "defendant-application": (
        "defendant-application-palmetto",
        "defendant-application",
        "defendant application",
    ),
    "indemnity-agreement": (
        "indemnity-agreement-palmetto",
        "indemnity-agreement",
        "indemnity agreement",
        "indemnity",
    ),
    "collateral-receipt": (
        "collateral-receipt-palmetto",
        "collateral-receipt",
        "collateral receipt",
        "collateral",
    ),
    "bail-bond-information-sheet-palmetto": (
        "surety-terms-palmetto",
        "bail-bond-information-sheet",
        "bail bond information",
        "form#704",
        "form 704",
        "information sheet",
        "surety-terms",
        "surety term",
    ),
    "paperwork-header": (
        "shamrock-paperwork-header",
        "paperwork-header",
        "paperwork header",
    ),
}

_AREA_DECIMALS = 4

# Placement ``data_source`` values that are not themselves a prefill key.
# The right-hand side is the key ``prefill_values_from_bond`` already emits,
# which is also the live template 5 spelling where that spelling exists.
_SOURCE_NAMES = {
    "bond_amount_numeric": "numeric_full_bond_amount",
    "premium_numeric": "numeric_premium",
    "premium_words": "written_premium",
    "charge_line_1": "charges_summary",
    "day": "today_day",
    "month": "today_month",
    "year": "today_year_2digit",
    "poa_1": "poa_number_1",
    "poa_2": "poa_number_2",
    "poa_3": "poa_number_3",
    "poa_4": "poa_number_4",
    "indemnitor_first": "indemnitor_first_name",
    "indemnitor_middle": "indemnitor_middle_name",
    "indemnitor_last": "indemnitor_last_name",
    "defendant_former_how_long": "defendant_former_address_how_long",
    "defendant_previous_how_long": "defendant_previous_employment_how_long",
    "defendant_social_login": "defendant_social_media_login",
    "defendant_social_password": "defendant_social_media_password",
}

# Signature widgets, and checkboxes whose live template 5 name is not the
# data_source. Payment boxes keep these names so prefill hits template 5.
# Template 5 has no Other checkbox. Template 6 names that box cr_other.
# The submission builder sends cr_other only when the target template has it.
_WIDGET_NAMES = {
    "app_agent_signature": "agent_signature_5",
    "app_defendant_signature": "defendant_signature_3",
    "ind_defendant_signature": "defendant_signature_2",
    "ind_indemnitor_signature": "indemnitor_signature_2",
    "ind_coindemnitor_signature": "coindemnitor_signature_2",
    "bbis_sign_left": "defendant_signature_3",
    "bbis_sign_right": "indemnitor_signature_3",
    "note_defendant_signature": "defendant_signature_2",
    "note_indemnitor_signature": "indemnitor_signature",
    "note_coindemnitor_signature": "coidemnitor_signature",
    "cr_agent_signature": "agent_signature_6",
    "cr_indemnitor_signature": "indemnitor_signature_1",
    "cr_promissory_note": "promissory_note_checkbox",
    "cr_indemnity": "indemnity_agreement_checkbox",
    "cr_mortgage": "mortgage_agreement_checkbox",
    "cr_cash": "collateral_cash_checkbox",
    "cr_check": "collateral_check_checkbox",
    "cr_money_order": "collateral_money_order_checkbox",
    "cr_credit_card": "collateral_credit_card_checkbox",
}

# Live signature, initials, and date-signed boxes stay on the printed line.
# Map a field name to the reason it was moved or dropped. Empty means every
# live box of those types is copied through (same uuid, role, and area).
# today_date is a prefill execution date, not a signature date.
SIGNATURE_GEOMETRY_EXCEPTIONS: Dict[str, str] = {}

# Resolved field name -> reason, when a rebuilt text or date box does not
# use the live template 5 submitter for that name on that document.
# Empty means every prefilled text and date stays on the live role.
# On the rebuilt documents that live role is bondsman. The unnamed
# defendant date-signed box is not in this map; it is copied unchanged.
ROLE_CHANGES: Dict[str, str] = {}

# plan_merge fields that touch each covered form on the template 5 export.
# Application and indemnity each include the agent_license box: one more
# field than the plan before that box existed.
# The paperwork header is not replaced. It keeps 4 live fields and gains
# the 4 offense rows. The PUT total is +4.
TEMPLATE_5_FORM_FIELD_COUNTS = {
    "defendant-application": 85,
    "indemnity-agreement": 51,
    "collateral-receipt": 30,
    "bail-bond-information-sheet-palmetto": 6,
}
TEMPLATE_5_HEADER_FIELD_COUNT = 8
TEMPLATE_5_PUT_FIELD_COUNT = 199

_TEXT_ALIGNS = frozenset({"left", "center", "right"})
_TEXT_VALIGNS = frozenset({"top", "center", "bottom"})

# Text, number, and date boxes the prefill does not populate. Left blank.
# Do not invent a value for these.
BLANK_BY_DESIGN = frozenset({
    "contacted_by",
    "court_time",
    "defendant_social_media_login",
    "defendant_social_media_password",
    "card_fee_percent",
    "collateral_description",
    "indemnitor_spouse_dob",
    "indemnitor_spouse_email",
    "reference_3_name",
    "reference_3_address",
    "reference_3_phone",
})


def keeps_live_geometry(field: Mapping[str, Any]) -> bool:
    """Signature, initials, and date-signed boxes keep the live template 5 area.

    ``today_date`` is the prefilled execution date and still follows the spec.
    A name in ``SIGNATURE_GEOMETRY_EXCEPTIONS`` is allowed to move or drop.
    """
    name = str(field.get("name") or "")
    if name in SIGNATURE_GEOMETRY_EXCEPTIONS:
        reason = str(SIGNATURE_GEOMETRY_EXCEPTIONS.get(name) or "").strip()
        if not reason:
            raise PalmettoApplyError(
                f"Signature geometry exception {name!r} needs a written reason."
            )
        return False
    kind = str(field.get("type") or "")
    if kind in ("signature", "initials"):
        return True
    if kind == "date" and name != "today_date":
        return True
    return False


def resolved_docuseal_name(field: Mapping[str, Any]) -> str:
    """DocuSeal field name for one placement row.

    Live names that prefill already sends win. A data source that is itself
    a prefill key is used as the name. Signature and checkbox rows use the
    live widget name when one exists.
    """
    spec_name = str(field.get("name") or "")
    if spec_name in _WIDGET_NAMES:
        return _WIDGET_NAMES[spec_name]
    source = str(field.get("data_source") or "").strip()
    if source:
        return _SOURCE_NAMES.get(source, source)
    if not spec_name:
        raise PalmettoApplyError("Spec field is missing a name.")
    return spec_name


class PalmettoApplyError(Exception):
    """The template cannot be updated without dropping or mis-routing a field."""


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, list):
        return value
    return []


def _attachment_catalog(template: Mapping[str, Any]) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
    """Attachment rows whose uuid is the target document uuid.

    A clone keeps the schema order and gives every document a new uuid.
    Areas are written with that document uuid. A schema attachment uuid is
    only a lookup key onto the document in the same position.
    """
    schema = [item for item in _as_list(template.get("schema")) if isinstance(item, dict)]
    documents = [item for item in _as_list(template.get("documents")) if isinstance(item, dict)]
    rows: List[Dict[str, str]] = []
    remap: Dict[str, str] = {}
    if schema:
        for index, item in enumerate(schema):
            schema_uuid = str(item.get("attachment_uuid") or item.get("uuid") or "").strip()
            document = documents[index] if index < len(documents) else {}
            document_uuid = str(document.get("uuid") or document.get("attachment_uuid") or "").strip()
            filename = str(document.get("filename") or document.get("name") or "").strip()
            name = str(item.get("name") or "").strip()
            canonical = document_uuid or schema_uuid
            if not canonical:
                continue
            if schema_uuid:
                remap[schema_uuid] = canonical
            remap[canonical] = canonical
            rows.append({"uuid": canonical, "name": name, "filename": filename or name})
        return rows, remap
    for item in documents:
        document_uuid = str(item.get("uuid") or item.get("attachment_uuid") or "").strip()
        filename = str(item.get("filename") or item.get("name") or "").strip()
        if not document_uuid:
            continue
        remap[document_uuid] = document_uuid
        rows.append({"uuid": document_uuid, "name": filename, "filename": filename})
    return rows, remap


def template_attachments(template: Mapping[str, Any]) -> List[Dict[str, str]]:
    """Attachment rows in template order. The uuid is the document uuid."""
    rows, _remap = _attachment_catalog(template)
    return rows


def document_uuids(template: Mapping[str, Any]) -> set:
    """Uuids of the documents on this template, which a clone regenerates."""
    found = set()
    for item in _as_list(template.get("documents")):
        if not isinstance(item, dict):
            continue
        value = str(item.get("uuid") or item.get("attachment_uuid") or "").strip()
        if value:
            found.add(value)
    return found


def assert_put_identity(template: Mapping[str, Any], fields: Sequence[Mapping[str, Any]]) -> None:
    """Refuse a PUT whose fields DocuSeal cannot key, or whose areas miss the target documents."""
    allowed = document_uuids(template)
    if not allowed:
        raise PalmettoApplyError(
            "Target template has no document uuids. Refusing to write fields."
        )
    seen = set()
    for field in fields:
        name = str(field.get("name") or "").strip() or "(unnamed)"
        field_uuid = str(field.get("uuid") or "").strip()
        if not field_uuid:
            raise PalmettoApplyError(f"Field {name} has no uuid.")
        if field_uuid in seen:
            raise PalmettoApplyError(f"Field uuid {field_uuid} is duplicated ({name}).")
        seen.add(field_uuid)
        areas = [area for area in (field.get("areas") or []) if isinstance(area, dict)]
        if not areas:
            raise PalmettoApplyError(f"Field {name} has no area.")
        for area in areas:
            attachment = str(area.get("attachment_uuid") or "").strip()
            if attachment not in allowed:
                raise PalmettoApplyError(
                    f"Field {name} attachment {attachment or '(blank)'} "
                    "is not a document on the target template."
                )


def _score(slug: str, blob: str) -> int:
    best = 0
    for hint in _DOC_HINTS.get(slug, ()):
        if hint in blob:
            best = max(best, len(hint))
    return best


def match_attachments(template: Mapping[str, Any]) -> Dict[str, Dict[str, str]]:
    """Map a spec slug to one attachment. Covered slugs must match."""
    rows = template_attachments(template)
    claimed: Dict[str, Dict[str, str]] = {}
    used_uuids = set()
    slugs = list(COVERED_SLUGS) + list(PRINT_ONLY_SLUGS) + list(APPEND_SLUGS)
    ranking: List[Tuple[int, str, Dict[str, str]]] = []
    for slug in slugs:
        for row in rows:
            blob = f"{row['name']} {row['filename']}".lower()
            score = _score(slug, blob)
            if score:
                ranking.append((score, slug, row))
    ranking.sort(key=lambda item: item[0], reverse=True)
    for _score_value, slug, row in ranking:
        if slug in claimed or row["uuid"] in used_uuids:
            continue
        claimed[slug] = row
        used_uuids.add(row["uuid"])
    missing = [slug for slug in COVERED_SLUGS if slug not in claimed]
    if missing:
        visible = [f"{row['name'] or row['filename'] or '(unnamed)'} uuid={row['uuid']}" for row in rows]
        raise PalmettoApplyError(
            "Refusing to apply. Unmatched Palmetto documents: "
            + ", ".join(missing)
            + ". Template documents: "
            + ("; ".join(visible) or "(none)")
        )
    return claimed


def _area_uuids(field: Mapping[str, Any]) -> List[str]:
    found = []
    for area in field.get("areas") or []:
        if isinstance(area, dict):
            uuid = str(area.get("attachment_uuid") or "").strip()
            if uuid:
                found.append(uuid)
    return found


def live_page_delta(template: Mapping[str, Any]) -> int:
    """Spec pages are 1-based. Live DocuSeal areas are often 0-based.

    If any stored area uses page 0, subtract 1 when writing. Otherwise keep
    the spec page. This does not guess when the export has no page 0.
    """
    for field in template.get("fields") or []:
        if not isinstance(field, dict):
            continue
        for area in field.get("areas") or []:
            if isinstance(area, dict) and area.get("page") is not None and int(area["page"]) == 0:
                return -1
    return 0


def _submitters(template: Mapping[str, Any]) -> Dict[str, str]:
    found: Dict[str, str] = {}
    for row in template.get("submitters") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip().lower()
        uuid = str(row.get("uuid") or "").strip()
        if name and uuid:
            found[name] = uuid
    return found


def _round_area(x: Any, y: Any, w: Any, h: Any, page: Any, uuid: str) -> Tuple:
    return (
        round(float(x), _AREA_DECIMALS),
        round(float(y), _AREA_DECIMALS),
        round(float(w), _AREA_DECIMALS),
        round(float(h), _AREA_DECIMALS),
        int(page),
        uuid,
    )


def _docuseal_type(kind: str) -> str:
    return {"text": "text", "checkbox": "checkbox", "signature": "signature"}.get(kind, "text")


def _emitted_type(spec_group: Sequence[Mapping[str, Any]], name: str) -> str:
    if name == "today_date":
        return "date"
    return _docuseal_type(str(spec_group[0].get("type") or "text"))


def _spec_areas(spec_group: Sequence[Mapping[str, Any]], attachment_uuid: str, page_delta: int) -> List[Tuple]:
    return [
        _round_area(
            field["x_norm"],
            field["y_norm"],
            field["w_norm"],
            field["h_norm"],
            int(field["page"]) + page_delta,
            attachment_uuid,
        )
        for field in spec_group
    ]


def _live_areas(areas: Iterable[Mapping[str, Any]], attachment_uuid: str) -> List[Tuple]:
    found = []
    for area in areas:
        if not isinstance(area, dict):
            continue
        if str(area.get("attachment_uuid") or "") != attachment_uuid:
            continue
        found.append(
            _round_area(
                area.get("x"),
                area.get("y"),
                area.get("w"),
                area.get("h"),
                area.get("page") or 0,
                attachment_uuid,
            )
        )
    return found


def _groups_for(
    slug: str,
    spec_fields: Iterable[Mapping[str, Any]],
) -> List[Tuple[str, str, List[Mapping[str, Any]]]]:
    """Placement rows grouped by DocuSeal name and role.

    One DocuSeal field has one submitter. Two boxes that share a name but
    not a role stay two fields (live template 5 already does this).
    """
    grouped: Dict[Tuple[str, str], List[Mapping[str, Any]]] = {}
    order: List[Tuple[str, str]] = []
    resolve = slug in COVERED_SLUGS or slug in APPEND_SLUGS
    for field in spec_fields:
        if str(field.get("document") or "") != slug:
            continue
        name = resolved_docuseal_name(field) if resolve else str(field.get("name") or "")
        role = str(field.get("role") or "").strip().lower()
        if not name:
            raise PalmettoApplyError(f"Spec field on {slug} is missing a name.")
        key = (name, role)
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append(field)
    return [(name, role, grouped[(name, role)]) for name, role in order]


def _validated_preferences(name: str, raw: Mapping[str, Any]) -> Dict[str, Any]:
    """DocuSeal 3.3.1 text preferences. font_size is an integer."""
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        if key == "align":
            text = str(value or "").strip().lower()
            if text not in _TEXT_ALIGNS:
                raise PalmettoApplyError(f"Spec field {name} has unknown alignment {text!r}.")
            out["align"] = text
        elif key == "valign":
            text = str(value or "").strip().lower()
            if text not in _TEXT_VALIGNS:
                raise PalmettoApplyError(
                    f"Spec field {name} has unknown vertical alignment {text!r}."
                )
            out["valign"] = text
        elif key == "font_size":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PalmettoApplyError(
                    f"Spec field {name} font_size must be an integer, not {value!r}."
                )
            number = float(value)
            if number <= 0 or not number.is_integer():
                raise PalmettoApplyError(
                    f"Spec field {name} font_size must be a positive integer, not {value!r}."
                )
            out["font_size"] = int(number)
        else:
            raise PalmettoApplyError(f"Spec field {name} has unknown preference {key!r}.")
    return out


def _text_preferences(
    name: str,
    spec_group: Sequence[Mapping[str, Any]],
    kind: str,
) -> Dict[str, Any]:
    """DocuSeal preferences. An omitted preferences dict leaves the previous output."""
    preferences: Dict[str, Any] = {}
    if kind == "date":
        preferences["format"] = "MM/DD/YYYY"
    blobs: List[Dict[str, Any]] = []
    for field in spec_group:
        raw = field.get("preferences")
        if not raw:
            continue
        if not isinstance(raw, dict):
            raise PalmettoApplyError(f"Spec field {name} preferences must be an object.")
        blob = _validated_preferences(name, raw)
        if blob not in blobs:
            blobs.append(blob)
    if len(blobs) > 1:
        raise PalmettoApplyError(f"Spec field {name} has conflicting preferences.")
    if blobs:
        preferences.update(blobs[0])
        if kind == "date":
            preferences["format"] = "MM/DD/YYYY"
    return preferences


def _build_field(
    name: str,
    spec_group: Sequence[Mapping[str, Any]],
    attachment_uuid: str,
    submitter_uuid: str,
    page_delta: int,
    existing_uuid: str,
) -> Dict[str, Any]:
    roles = {str(field.get("role") or "").strip().lower() for field in spec_group}
    if len(roles) != 1 or not next(iter(roles)):
        raise PalmettoApplyError(f"Spec field {name} has conflicting roles.")
    kinds = {_docuseal_type(str(field.get("type") or "text")) for field in spec_group}
    if len(kinds) != 1:
        raise PalmettoApplyError(f"Spec field {name} has conflicting types.")
    kind = _emitted_type(spec_group, name)
    kept_uuid = str(existing_uuid or "").strip()
    payload: Dict[str, Any] = {
        "uuid": kept_uuid or str(uuid.uuid4()),
        "submitter_uuid": submitter_uuid,
    }
    payload["name"] = name
    payload["type"] = kind
    if kind == "signature":
        payload["required"] = False
    else:
        payload["required"] = any(bool(field.get("required")) for field in spec_group)
    payload["preferences"] = _text_preferences(name, spec_group, kind)
    payload["areas"] = [
        {
            "x": field["x_norm"],
            "y": field["y_norm"],
            "w": field["w_norm"],
            "h": field["h_norm"],
            "page": int(field["page"]) + page_delta,
            "attachment_uuid": attachment_uuid,
        }
        for field in spec_group
    ]
    return payload


def _names_on(fields: Sequence[Mapping[str, Any]], attachment_uuid: str) -> List[str]:
    names: List[str] = []
    for field in fields:
        if any(
            isinstance(area, dict) and str(area.get("attachment_uuid") or "") == attachment_uuid
            for area in field.get("areas") or []
        ):
            names.append(str(field.get("name") or ""))
    return names


def plan_merge(
    template: Mapping[str, Any],
    spec_fields: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return the PUT field list and a per-document added/moved/removed/kept plan.

    Areas on documents the spec does not cover are copied onto the original
    field object. Signature, initials, and date-signed fields are copied
    whole, covered areas included. The caller's template is not mutated.
    Wholly uncovered fields compare equal to the input field.
    """
    spec_fields = list(spec_fields if spec_fields is not None else docuseal_fields())
    attachments, remap = _attachment_catalog(template)
    matched = match_attachments(template)
    known = {row["uuid"] for row in attachments}
    covered_uuids = {
        row["uuid"]
        for slug, row in matched.items()
        if slug in COVERED_SLUGS
    }
    slug_by_uuid = {row["uuid"]: slug for slug, row in matched.items()}
    page_delta = live_page_delta(template)
    submitters = _submitters(template)

    original_fields = []
    for source in template.get("fields") or []:
        if not isinstance(source, dict):
            continue
        field = copy.deepcopy(source)
        for area in field.get("areas") or []:
            if not isinstance(area, dict):
                continue
            current = str(area.get("attachment_uuid") or "").strip()
            if current in remap:
                area["attachment_uuid"] = remap[current]
        original_fields.append(field)
    for field in original_fields:
        areas = [area for area in (field.get("areas") or []) if isinstance(area, dict)]
        uuids = _area_uuids(field)
        if not uuids or len(uuids) != len(areas):
            raise PalmettoApplyError(
                f"Field {field.get('name') or field.get('uuid') or '(unnamed)'} has no attachment area."
            )
        unknown = [uuid for uuid in uuids if uuid not in known]
        if unknown:
            raise PalmettoApplyError(
                f"Field {field.get('name')} references unknown attachment(s): {', '.join(unknown)}."
            )

    def _wholly_here(field: Mapping[str, Any], attachment_uuid: str) -> bool:
        uuids = list(dict.fromkeys(_area_uuids(field)))
        return uuids == [attachment_uuid]

    documents: List[Dict[str, Any]] = []
    replacements: Dict[str, List[dict]] = {}

    for row in attachments:
        uuid = row["uuid"]
        slug = slug_by_uuid.get(uuid, "")
        if uuid not in covered_uuids:
            added_names: List[str] = []
            if slug in APPEND_SLUGS:
                existing_names = set(_names_on(original_fields, uuid))
                built_rows: List[dict] = []
                for name, role, group in _groups_for(slug, spec_fields):
                    if name in existing_names:
                        continue
                    submitter_uuid = submitters.get(role, "")
                    if not submitter_uuid:
                        known_roles = ", ".join(sorted(submitters)) or "(none)"
                        raise PalmettoApplyError(
                            f"Spec field {name} role {role or '(blank)'} is not a template submitter ({known_roles})."
                        )
                    added_names.append(name)
                    built_rows.append(
                        _build_field(name, group, uuid, submitter_uuid, page_delta, "")
                    )
                if built_rows:
                    replacements[uuid] = built_rows
            documents.append({
                "document": row["name"] or row["filename"],
                "filename": row["filename"],
                "attachment_uuid": uuid,
                "slug": slug,
                "coverage": "keep",
                "added": added_names,
                "moved": [],
                "removed": [],
                "kept": _names_on(original_fields, uuid),
            })
            continue

        spec_for_doc = _groups_for(slug, spec_fields)
        live_by_name: Dict[str, List[dict]] = {}
        live_fields: List[dict] = []
        for field in original_fields:
            if not any(
                isinstance(area, dict) and str(area.get("attachment_uuid") or "") == uuid
                for area in field.get("areas") or []
            ):
                continue
            live_fields.append(field)
            live_by_name.setdefault(str(field.get("name") or ""), []).append(field)

        added: List[str] = []
        moved: List[str] = []
        removed: List[str] = []
        kept: List[str] = []
        built: List[dict] = []
        spec_pairs = set()
        for name, role, group in spec_for_doc:
            submitter_uuid = submitters.get(role, "")
            if not submitter_uuid:
                known_roles = ", ".join(sorted(submitters)) or "(none)"
                raise PalmettoApplyError(
                    f"Spec field {name} role {role or '(blank)'} is not a template submitter ({known_roles})."
                )
            spec_pairs.add((name, submitter_uuid))
            priors = [
                field for field in live_by_name.get(name) or []
                if str(field.get("submitter_uuid") or "") == submitter_uuid
            ]
            if any(keeps_live_geometry(field) for field in priors):
                kept.append(name)
                continue
            donors = [field for field in priors if _wholly_here(field, uuid)]
            existing_uuid = str(donors[0].get("uuid") or "") if donors else ""
            emitted = _emitted_type(group, name)
            live_sig: List[Tuple] = []
            live_types = set()
            for field in priors:
                live_sig.extend(_live_areas(field.get("areas") or [], uuid))
                live_types.add(str(field.get("type") or "text"))
            wanted = _spec_areas(group, uuid, page_delta)
            if not priors:
                added.append(name)
            elif live_types == {emitted} and sorted(live_sig) == sorted(wanted):
                kept.append(name)
            else:
                moved.append(name)
            built.append(_build_field(name, group, uuid, submitter_uuid, page_delta, existing_uuid))
        seen_pairs = set()
        for field in live_fields:
            pair = (str(field.get("name") or ""), str(field.get("submitter_uuid") or ""))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            if pair not in spec_pairs:
                locked = [
                    field for field in live_by_name.get(pair[0]) or []
                    if str(field.get("submitter_uuid") or "") == pair[1]
                    and keeps_live_geometry(field)
                ]
                if locked:
                    kept.append(pair[0])
                else:
                    removed.append(pair[0])
        replacements[uuid] = built
        documents.append({
            "document": row["name"] or row["filename"],
            "filename": row["filename"],
            "attachment_uuid": uuid,
            "slug": slug,
            "coverage": "replace",
            "added": added,
            "moved": moved,
            "removed": removed,
            "kept": kept,
        })

    appearance_groups = _groups_for("appearance-bond", spec_fields)
    if "appearance-bond" not in matched:
        documents.append({
            "document": "appearance-bond",
            "filename": "",
            "attachment_uuid": "",
            "slug": "appearance-bond",
            "coverage": "not_on_template",
            "added": [name for name, _role, _group in appearance_groups],
            "moved": [],
            "removed": [],
            "kept": [],
        })

    payload: List[dict] = []
    emitted_docs: set = set()

    def _emit(attachment_uuid: str) -> None:
        if attachment_uuid not in emitted_docs:
            payload.extend(replacements.get(attachment_uuid) or [])
            emitted_docs.add(attachment_uuid)

    for field in original_fields:
        if keeps_live_geometry(field):
            payload.append(copy.deepcopy(field))
            for attachment_uuid in dict.fromkeys(_area_uuids(field)):
                if attachment_uuid in covered_uuids:
                    _emit(attachment_uuid)
            continue
        unique = list(dict.fromkeys(_area_uuids(field)))
        on_covered = [item for item in unique if item in covered_uuids]
        on_open = [item for item in unique if item not in covered_uuids]
        if on_open:
            kept_field = copy.deepcopy(field)
            if on_covered:
                kept_field["areas"] = [
                    area
                    for area in kept_field.get("areas") or []
                    if str(area.get("attachment_uuid") or "") not in covered_uuids
                ]
            payload.append(kept_field)
        for attachment_uuid in on_covered:
            _emit(attachment_uuid)

    for row in attachments:
        if row["uuid"] in covered_uuids:
            _emit(row["uuid"])
        elif row["uuid"] in replacements:
            payload.extend(replacements[row["uuid"]])

    assert_put_identity(template, payload)
    return {
        "recommended": "clone",
        "clone_name": CLONE_NAME,
        "page_delta": page_delta,
        "documents": documents,
        "fields": payload,
    }
