#!/usr/bin/env python3
"""Apply the Palmetto field-placement spec to a DocuSeal template.

Dry-run unless ``--apply`` is passed. Reads the API URL from
``DOCUSEAL_URL`` or ``DOCUSEAL_SERVER`` and the token from
``DOCUSEAL_API_KEY``. Template id is ``DOCUSEAL_TEMPLATE_ID_PALMETTO``
or 5. Never creates a submission and never sends anything to a signer.

``--create`` posts a new template instead of updating template 5. The
script stops if a spec document cannot be matched to a template
attachment.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from dashboard.palmetto_field_placement import PACKET_INVENTORY, QUESTIONS, docuseal_fields

# Filename hints for the carrier PDFs. First match wins. Unmatched spec
# documents fail closed.
_DOC_HINTS = {
    "appearance-bond": ("appearance bond", "appearance-bond", "official appearance"),
    "defendant-application": ("defendant application", "defendant-application", "application for appearance"),
    "indemnity-agreement": ("indemnity",),
    "collateral-receipt": ("collateral", "promissory note"),
    "bail-bond-information-sheet-palmetto": (
        "surety-terms-palmetto",
        "surety term",
        "bail bond information",
        "information sheet",
        "form 704",
        "form#704",
    ),
}


def _api_base() -> str:
    return (os.environ.get("DOCUSEAL_URL") or os.environ.get("DOCUSEAL_SERVER") or "").rstrip("/")


def _token() -> str:
    return (os.environ.get("DOCUSEAL_API_KEY") or "").strip()


def _template_id() -> str:
    return (os.environ.get("DOCUSEAL_TEMPLATE_ID_PALMETTO") or "5").strip()


def _request(method: str, url: str, token: str, body: Optional[dict] = None) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "X-Auth-Token": token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _documents(template: dict) -> List[dict]:
    docs = template.get("documents") or template.get("schema") or []
    if isinstance(docs, dict):
        docs = list(docs.values())
    return [doc for doc in docs if isinstance(doc, dict)]


def _doc_name(doc: dict) -> str:
    return str(doc.get("name") or doc.get("filename") or doc.get("title") or "").strip()


def _doc_uuid(doc: dict) -> str:
    return str(doc.get("uuid") or doc.get("attachment_uuid") or "").strip()


def match_documents(template: dict) -> Dict[str, str]:
    """Map spec slug → attachment uuid. Raises if a slug cannot be matched."""
    docs = _documents(template)
    used = set()
    matched: Dict[str, str] = {}
    unmatched = []
    for slug, hints in _DOC_HINTS.items():
        hit = None
        for doc in docs:
            uuid = _doc_uuid(doc)
            name = _doc_name(doc).lower()
            if not uuid or uuid in used:
                continue
            if any(hint in name for hint in hints):
                hit = doc
                break
        if hit is None:
            unmatched.append(slug)
            continue
        used.add(_doc_uuid(hit))
        matched[slug] = _doc_uuid(hit)
    if unmatched:
        visible = [f"{_doc_name(doc) or '(unnamed)'} uuid={_doc_uuid(doc) or '(none)'}" for doc in docs]
        raise SystemExit(
            "Refusing to apply. Unmatched Palmetto documents: "
            + ", ".join(unmatched)
            + ". Template documents: "
            + ("; ".join(visible) or "(none)")
        )
    return matched


def build_payload_fields(attachment_by_slug: Dict[str, str]) -> List[dict]:
    fields = []
    seen = set()
    for field in docuseal_fields():
        slug = field["document"]
        if slug not in attachment_by_slug:
            continue
        # Two appearance-bond widgets share AgentField. DocuSeal wants one
        # field with one area per box.
        key = (slug, field["name"])
        area = {
            "x": field["x_norm"],
            "y": field["y_norm"],
            "w": field["w_norm"],
            "h": field["h_norm"],
            "page": field["page"],
            "attachment_uuid": attachment_by_slug[slug],
        }
        if key in seen:
            for existing in fields:
                if existing["name"] == field["name"] and existing.get("_slug") == slug:
                    existing["areas"].append(area)
                    break
            continue
        seen.add(key)
        kind = {"text": "text", "checkbox": "checkbox", "signature": "signature"}.get(
            field["type"], "text"
        )
        fields.append({
            "name": field["name"],
            "type": kind,
            "role": field["role"],
            "required": bool(field["required"]) if kind != "signature" else False,
            "areas": [area],
            "_slug": slug,
            "preferences": {"data_source": field["data_source"]},
        })
    for field in fields:
        field.pop("_slug", None)
    return fields


def spec_document() -> dict:
    return {
        "template_id_env": "DOCUSEAL_TEMPLATE_ID_PALMETTO",
        "default_template_id": "5",
        "inventory": PACKET_INVENTORY,
        "questions": QUESTIONS,
        "fields": [
            {
                "document": field["document"],
                "name": field["name"],
                "type": field["type"],
                "role": field["role"],
                "page": field["page"],
                "x": field["x_norm"],
                "y": field["y_norm"],
                "w": field["w_norm"],
                "h": field["h_norm"],
                "data_source": field["data_source"],
                "required": field["required"],
            }
            for field in docuseal_fields()
        ],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Apply Palmetto DocuSeal field placement")
    parser.add_argument("--apply", action="store_true", help="PUT the template. Default is dry-run.")
    parser.add_argument("--create", action="store_true", help="POST a new template instead of updating.")
    parser.add_argument("--write-spec", default="", help="Write the machine-readable spec JSON to this path.")
    args = parser.parse_args(argv)

    document = spec_document()
    if args.write_spec:
        with open(args.write_spec, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
        print(f"wrote {args.write_spec} ({len(document['fields'])} fields)")

    if not args.apply and not args.create:
        print(
            f"dry-run: {len(document['fields'])} DocuSeal fields ready. "
            "Set DOCUSEAL_URL and DOCUSEAL_API_KEY and pass --apply to update "
            f"template {_template_id()}. No request was sent."
        )
        return 0

    base = _api_base()
    token = _token()
    if not base or not token:
        print("DOCUSEAL_URL/DOCUSEAL_SERVER and DOCUSEAL_API_KEY are required for --apply/--create.", file=sys.stderr)
        return 2

    if args.create:
        body = {
            "name": "shamrock-palmetto-paperwork-complete",
            "external_id": "shamrock-palmetto-paperwork",
        }
        created = _request("POST", f"{base}/api/templates", token, body)
        template_id = str(created.get("id") or "")
        if not template_id:
            print("Create returned no template id. Stopping.", file=sys.stderr)
            return 2
        print(f"created template {template_id}. Upload the carrier PDFs, then rerun --apply.")
        return 0

    template_id = _template_id()
    try:
        current = _request("GET", f"{base}/api/templates/{template_id}", token)
    except urllib.error.HTTPError as exc:
        print(f"GET template {template_id} failed: {exc.code}. Stopping.", file=sys.stderr)
        return 2
    attachment_by_slug = match_documents(current)
    fields = build_payload_fields(attachment_by_slug)
    existing = current.get("fields") or []
    if _same_fields(existing, fields):
        print(f"template {template_id} already matches the spec ({len(fields)} fields).")
        return 0
    updated = _request(
        "PUT",
        f"{base}/api/templates/{template_id}",
        token,
        {"fields": fields},
    )
    print(f"updated template {updated.get('id') or template_id} with {len(fields)} fields.")
    return 0


def _same_fields(existing: list, wanted: list) -> bool:
    def key(field: dict) -> tuple:
        areas = tuple(
            (
                round(float(area.get("x") or 0), 4),
                round(float(area.get("y") or 0), 4),
                round(float(area.get("w") or 0), 4),
                round(float(area.get("h") or 0), 4),
                int(area.get("page") or 0),
                str(area.get("attachment_uuid") or ""),
            )
            for area in field.get("areas") or []
        )
        return (field.get("name"), field.get("type"), field.get("role"), areas)

    if len(existing) != len(wanted):
        return False
    return sorted(key(field) for field in existing if isinstance(field, dict)) == sorted(
        key(field) for field in wanted
    )


if __name__ == "__main__":
    raise SystemExit(main())
