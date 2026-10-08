#!/usr/bin/env python3
"""Plan or apply Palmetto DocuSeal fields without dropping uncovered documents.

Dry-run is the default and sends no request. Pass ``--live PATH`` to diff a
saved template export (the recommended check). ``--clone`` is the recommended
write: POST /templates/{id}/clone, merge into the clone, PUT the clone, and
print the new id. ``--apply`` updates the source template in place.

A field PUT replaces the template's whole field list. Fields on documents
the spec does not cover are copied through unchanged. Appearance-bond fields
are omitted when that PDF is not an attachment (it is print/wet-ink).

Reads DOCUSEAL_URL or DOCUSEAL_SERVER, DOCUSEAL_API_KEY, and
DOCUSEAL_TEMPLATE_ID_PALMETTO (default 5). Never creates a submission.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, List, Optional

from dashboard.palmetto_docuseal_apply import CLONE_NAME, PalmettoApplyError, plan_merge
from dashboard.palmetto_field_placement import PACKET_INVENTORY, QUESTIONS, docuseal_fields


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


def spec_document() -> dict:
    return {
        "template_id_env": "DOCUSEAL_TEMPLATE_ID_PALMETTO",
        "default_template_id": "5",
        "recommended": "clone",
        "clone_name": CLONE_NAME,
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


def _plan_view(template_id: str, plan: dict) -> dict:
    documents = []
    for row in plan["documents"]:
        documents.append({
            "document": row["document"],
            "filename": row["filename"],
            "slug": row["slug"],
            "coverage": row["coverage"],
            "added": row["added"],
            "moved": row["moved"],
            "removed": row["removed"],
            "kept": row["kept"],
        })
    return {
        "mode": "dry-run",
        "recommended": "clone",
        "clone_name": CLONE_NAME,
        "template_id": template_id,
        "page_delta": plan["page_delta"],
        "put_field_count": len(plan["fields"]),
        "documents": documents,
    }


def _load_live(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise PalmettoApplyError(f"{path} is not a template object.")
    return payload


def _credentials() -> tuple:
    base = _api_base()
    token = _token()
    if not base or not token:
        print(
            "DOCUSEAL_URL/DOCUSEAL_SERVER and DOCUSEAL_API_KEY are required.",
            file=sys.stderr,
        )
        return "", ""
    return base, token


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Apply Palmetto DocuSeal field placement")
    parser.add_argument("--apply", action="store_true", help="PUT the source template. Prefer --clone.")
    parser.add_argument(
        "--clone",
        action="store_true",
        help="POST /templates/{id}/clone, apply to the clone, and print the new id.",
    )
    parser.add_argument("--live", default="", help="Dry-run against a saved template JSON. Sends nothing.")
    parser.add_argument("--write-spec", default="", help="Write the machine-readable spec JSON to this path.")
    args = parser.parse_args(argv)

    if args.apply and args.clone:
        print("Pass only one of --apply or --clone.", file=sys.stderr)
        return 2
    if args.live and (args.apply or args.clone):
        print("--live is a dry-run. It does not write to DocuSeal.", file=sys.stderr)
        return 2

    document = spec_document()
    if args.write_spec:
        with open(args.write_spec, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
        print(f"wrote {args.write_spec} ({len(document['fields'])} fields)", file=sys.stderr)

    template_id = _template_id()
    if args.live:
        try:
            plan = plan_merge(_load_live(args.live))
        except (OSError, json.JSONDecodeError, PalmettoApplyError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(_plan_view(template_id, plan), indent=2))
        return 0

    if not args.apply and not args.clone:
        print(
            "Dry-run needs a template export. Pass --live PATH to list, per document, "
            "the fields added, moved, removed, and kept. No request was sent. "
            f"The recommended write is --clone (POST /templates/{template_id}/clone, "
            f"then apply to the clone named {CLONE_NAME!r}).",
            file=sys.stderr,
        )
        return 2

    base, token = _credentials()
    if not base:
        return 2

    source_id = template_id
    try:
        if args.clone:
            created = _request(
                "POST",
                f"{base}/api/templates/{source_id}/clone",
                token,
                {"name": CLONE_NAME},
            )
            clone_id = str(created.get("id") or "").strip()
            if not clone_id:
                print("Clone returned no template id. Source template was not modified.", file=sys.stderr)
                return 2
            current = _request("GET", f"{base}/api/templates/{clone_id}", token)
            target_id = clone_id
        else:
            current = _request("GET", f"{base}/api/templates/{source_id}", token)
            target_id = source_id
        plan = plan_merge(current)
    except urllib.error.HTTPError as exc:
        print(f"DocuSeal request failed: {exc.code}. Stopping.", file=sys.stderr)
        return 2
    except PalmettoApplyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    updated = _request(
        "PUT",
        f"{base}/api/templates/{target_id}",
        token,
        {"fields": plan["fields"]},
    )
    written_id = str(updated.get("id") or target_id)
    if args.clone:
        print(
            f"clone_template_id={written_id} source_template_id={source_id} "
            f"fields={len(plan['fields'])} name={CLONE_NAME}"
        )
    else:
        print(
            f"updated template {written_id} with {len(plan['fields'])} fields. "
            "--clone is the recommended path and was not used.",
            file=sys.stderr,
        )
        print(f"template_id={written_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
