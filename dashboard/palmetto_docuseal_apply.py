"""Merge a Palmetto field spec into a live DocuSeal template.

A DocuSeal field update replaces the template's whole field list. Documents
this spec does not cover (paperwork header, FAQ pages, master waiver,
SSA release) must keep every existing field object unchanged. Covered
carrier documents are replaced. The appearance bond is print/wet-ink and
is omitted when it is not an attachment on the template.
"""
from __future__ import annotations

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
}

_AREA_DECIMALS = 4


class PalmettoApplyError(Exception):
    """The template cannot be updated without dropping or mis-routing a field."""


def template_attachments(template: Mapping[str, Any]) -> List[Dict[str, str]]:
    """Attachment rows in template order. Area uuids come from ``schema``."""
    schema = template.get("schema") or []
    documents = template.get("documents") or []
    if isinstance(schema, dict):
        schema = list(schema.values())
    if isinstance(documents, dict):
        documents = list(documents.values())
    rows: List[Dict[str, str]] = []
    if isinstance(schema, list) and schema:
        for index, item in enumerate(schema):
            if not isinstance(item, dict):
                continue
            uuid = str(item.get("attachment_uuid") or item.get("uuid") or "").strip()
            name = str(item.get("name") or "").strip()
            filename = ""
            if isinstance(documents, list) and index < len(documents) and isinstance(documents[index], dict):
                filename = str(documents[index].get("filename") or documents[index].get("name") or "").strip()
            rows.append({"uuid": uuid, "name": name, "filename": filename or name})
        return [row for row in rows if row["uuid"]]
    if isinstance(documents, list):
        for item in documents:
            if not isinstance(item, dict):
                continue
            uuid = str(item.get("uuid") or item.get("attachment_uuid") or "").strip()
            filename = str(item.get("filename") or item.get("name") or "").strip()
            if uuid:
                rows.append({"uuid": uuid, "name": filename, "filename": filename})
    return rows


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
    slugs = list(COVERED_SLUGS) + list(PRINT_ONLY_SLUGS)
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


def _same_geometry(
    existing: Mapping[str, Any],
    spec_group: Sequence[Mapping[str, Any]],
    attachment_uuid: str,
    page_delta: int,
) -> bool:
    live = [
        _round_area(
            area.get("x"),
            area.get("y"),
            area.get("w"),
            area.get("h"),
            area.get("page") or 0,
            str(area.get("attachment_uuid") or ""),
        )
        for area in existing.get("areas") or []
        if isinstance(area, dict)
    ]
    wanted = [
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
    if len(live) != len(wanted):
        return False
    same_type = str(existing.get("type") or "text") == _docuseal_type(str(spec_group[0].get("type") or "text"))
    return same_type and sorted(live) == sorted(wanted)


def _docuseal_type(kind: str) -> str:
    return {"text": "text", "checkbox": "checkbox", "signature": "signature"}.get(kind, "text")


def _spec_groups(spec_fields: Iterable[Mapping[str, Any]]) -> Dict[Tuple[str, str], List[Mapping[str, Any]]]:
    groups: Dict[Tuple[str, str], List[Mapping[str, Any]]] = {}
    for field in spec_fields:
        groups.setdefault((str(field["document"]), str(field["name"])), []).append(field)
    return groups


def _build_field(
    spec_group: Sequence[Mapping[str, Any]],
    attachment_uuid: str,
    submitter_uuid: str,
    page_delta: int,
    existing_uuid: str,
) -> Dict[str, Any]:
    first = spec_group[0]
    kind = _docuseal_type(str(first.get("type") or "text"))
    payload: Dict[str, Any] = {}
    if existing_uuid:
        payload["uuid"] = existing_uuid
    payload["submitter_uuid"] = submitter_uuid
    payload["name"] = first["name"]
    payload["type"] = kind
    payload["required"] = bool(first.get("required")) if kind != "signature" else False
    source = str(first.get("data_source") or "")
    if source:
        payload["preferences"] = {"data_source": source}
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


def plan_merge(
    template: Mapping[str, Any],
    spec_fields: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return the PUT field list and a per-document added/moved/removed/kept plan.

    Uncovered field dicts in ``fields`` are the same objects as ``template['fields']``.
    """
    spec_fields = list(spec_fields if spec_fields is not None else docuseal_fields())
    matched = match_attachments(template)
    attachments = template_attachments(template)
    known = {row["uuid"] for row in attachments}
    covered_uuids = {
        row["uuid"]
        for slug, row in matched.items()
        if slug in COVERED_SLUGS
    }
    slug_by_uuid = {row["uuid"]: slug for slug, row in matched.items()}
    page_delta = live_page_delta(template)
    submitters = _submitters(template)
    groups = _spec_groups(spec_fields)

    original_fields = [field for field in (template.get("fields") or []) if isinstance(field, dict)]
    for field in original_fields:
        uuids = _area_uuids(field)
        if not uuids:
            raise PalmettoApplyError(
                f"Field {field.get('name') or field.get('uuid') or '(unnamed)'} has no attachment area."
            )
        unknown = [uuid for uuid in uuids if uuid not in known]
        if unknown:
            raise PalmettoApplyError(
                f"Field {field.get('name')} references unknown attachment(s): {', '.join(unknown)}."
            )
        unique = list(dict.fromkeys(uuids))
        on_covered = [uuid for uuid in unique if uuid in covered_uuids]
        on_open = [uuid for uuid in unique if uuid not in covered_uuids]
        if on_covered and on_open:
            raise PalmettoApplyError(
                f"Field {field.get('name')} spans a covered document and a document the spec does not cover."
            )
        if len(on_covered) > 1:
            raise PalmettoApplyError(
                f"Field {field.get('name')} spans more than one covered document."
            )

    by_attachment: Dict[str, List[dict]] = {row["uuid"]: [] for row in attachments}
    for field in original_fields:
        unique = list(dict.fromkeys(_area_uuids(field)))
        if len(unique) == 1:
            by_attachment.setdefault(unique[0], []).append(field)

    def _names(fields: Sequence[Mapping[str, Any]]) -> List[str]:
        return [str(field.get("name") or "") for field in fields]

    documents: List[Dict[str, Any]] = []
    replacements: Dict[str, List[dict]] = {}

    for row in attachments:
        uuid = row["uuid"]
        slug = slug_by_uuid.get(uuid, "")
        existing = by_attachment.get(uuid) or []
        if uuid not in covered_uuids:
            documents.append({
                "document": row["name"] or row["filename"],
                "filename": row["filename"],
                "attachment_uuid": uuid,
                "slug": slug,
                "coverage": "keep",
                "added": [],
                "moved": [],
                "removed": [],
                "kept": _names(existing),
            })
            continue

        spec_for_doc = [(name, group) for (doc_slug, name), group in groups.items() if doc_slug == slug]
        existing_by_name: Dict[str, List[dict]] = {}
        for field in existing:
            existing_by_name.setdefault(str(field.get("name") or ""), []).append(field)
        spec_names = {name for name, _group in spec_for_doc}
        added: List[str] = []
        moved: List[str] = []
        removed: List[str] = []
        kept: List[str] = []
        built: List[dict] = []
        for name, group in spec_for_doc:
            role = str(group[0].get("role") or "").strip().lower()
            submitter_uuid = submitters.get(role, "")
            if not submitter_uuid:
                known_roles = ", ".join(sorted(submitters)) or "(none)"
                raise PalmettoApplyError(
                    f"Spec field {name} role {role or '(blank)'} is not a template submitter ({known_roles})."
                )
            prior = existing_by_name.get(name) or []
            existing_uuid = str(prior[0].get("uuid") or "") if prior else ""
            if not prior:
                added.append(name)
            elif _same_geometry(prior[0], group, uuid, page_delta):
                kept.append(name)
            else:
                moved.append(name)
            if len(prior) > 1:
                removed.extend([name] * (len(prior) - 1))
            built.append(_build_field(group, uuid, submitter_uuid, page_delta, existing_uuid))
        for name, priors in existing_by_name.items():
            if name not in spec_names:
                removed.extend(_names(priors))
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

    for slug in PRINT_ONLY_SLUGS:
        if slug in matched:
            continue
        names = [name for (doc_slug, name) in groups if doc_slug == slug]
        documents.append({
            "document": slug,
            "filename": "",
            "attachment_uuid": "",
            "slug": slug,
            "coverage": "not_on_template",
            "added": names,
            "moved": [],
            "removed": [],
            "kept": [],
        })

    payload: List[dict] = []
    emitted = set()
    for field in original_fields:
        unique = list(dict.fromkeys(_area_uuids(field)))
        if len(unique) == 1 and unique[0] in covered_uuids:
            uuid = unique[0]
            if uuid not in emitted:
                payload.extend(replacements.get(uuid) or [])
                emitted.add(uuid)
            continue
        payload.append(field)

    for row in attachments:
        uuid = row["uuid"]
        if uuid in covered_uuids and uuid not in emitted:
            payload.extend(replacements.get(uuid) or [])
            emitted.add(uuid)

    return {
        "recommended": "clone",
        "clone_name": CLONE_NAME,
        "page_delta": page_delta,
        "documents": documents,
        "fields": payload,
    }
