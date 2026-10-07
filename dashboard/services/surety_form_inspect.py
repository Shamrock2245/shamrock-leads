"""Detect AcroForm widgets on an uploaded surety PDF and suggest canonical ids."""
from __future__ import annotations

from typing import Any, Dict, List

import fitz

from dashboard.services.surety_canonical import suggest_canonical


def inspect_pdf(pdf_bytes: bytes) -> Dict[str, Any]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        fields: List[Dict[str, Any]] = []
        signatures: List[Dict[str, Any]] = []
        seen: Dict[str, int] = {}
        for page_index, page in enumerate(doc):
            for widget in page.widgets() or []:
                name = str(widget.field_name or "").strip()
                rect = widget.rect
                box = [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]
                kind = str(widget.field_type_string or "Text")
                if kind.lower() == "signature":
                    signatures.append({
                        "role": "indemnitor",
                        "kind": "signature",
                        "page": page_index,
                        "rect": box,
                        "name": name,
                        "canonical": "",
                    })
                    continue
                if not name:
                    continue
                seen[name] = seen.get(name, 0) + 1
                canonical, confidence = suggest_canonical(name)
                fields.append({
                    "name": name,
                    "page": page_index,
                    "rect": box,
                    "type": kind,
                    "canonical": "",
                    "suggestion": canonical,
                    "suggestion_confidence": confidence,
                })
        text_fields = [f for f in fields if f["type"].lower() != "signature"]
        kind = "acroform" if text_fields else "flat"
        return {
            "kind": kind,
            "page_count": doc.page_count,
            "fields": fields,
            "signatures": signatures,
            "placement": "widgets" if text_fields else "manual",
        }
    finally:
        doc.close()
