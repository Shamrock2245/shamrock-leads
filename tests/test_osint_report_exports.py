"""OSINT recovery report: shared builder, PDF dossier, sectioned CSV, worker geo wiring."""
from __future__ import annotations

import asyncio
import csv
import io
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _scan_doc() -> dict:
    return {
        "_id": "65f0c0ffee0000000000abcd",
        "subject_type": "defendant",
        "subject_id": "case-123",
        "full_name": "José “Joe” Example — Fugitive",
        "status": "partial",
        "engines_requested": ["maigret", "holehe", "exiftool", "hibf", "ignorant"],
        "scan_requested_by": "admin",
        "scan_params": {
            "usernames": ["joeex"],
            "email": "joe@example.com",
            "phone": "+12395550100",
            "license_plate": "ABC1234",
            "image_url": "https://example.com/p.jpg",
            "deep_scan": False,
            "top_sites": 250,
        },
        "accounts": [
            {"platform": "Instagram", "url": "https://instagram.com/joeex", "username": "joeex",
             "source": "maigret", "confidence": "found", "category": "social"},
            {"platform": "Tinder", "url": "https://tinder.com/@joe.alt", "username": "joe.alt",
             "source": "maigret", "confidence": "likely", "category": "dating"},
            {"platform": "Reddit", "url": "https://reddit.com/u/joe.alt", "username": "joe.alt",
             "source": "maigret", "confidence": "found", "category": "forum"},
            {"platform": "Junk", "url": "https://junk.example", "username": "nope",
             "source": "maigret", "confidence": "uncertain", "category": "other", "relevance": "irrelevant"},
        ],
        "entities": [
            {"type": "phone", "value": "+12395559999", "source": "toutatis", "context": "IG obfuscated"},
            {"type": "email", "value": "joe@example.com", "source": "holehe"},
            {"type": "email", "value": "=HYPERLINK(\"x\")@evil.com", "source": "spiderfoot"},
            {"type": "breach", "value": "ExampleDump 2021", "source": "h8mail"},
            {"type": "location", "value": "123 Main St, Fort Myers, FL", "source": "ghunt"},
        ],
        "geo_points": [
            {"latitude": 26.6406, "longitude": -81.8723, "source": "exiftool",
             "label": "Fort Myers, FL 🌴", "context": "2026:10:01 · Apple iPhone 15"},
        ],
        "progress": {
            "maigret": {"status": "completed", "accounts_found": 4},
            "holehe": {"status": "completed", "entities_found": 1},
            "exiftool": {"status": "completed", "entities_found": 2},
            "hibf": {"status": "failed", "error": "upstream 503"},
            "ignorant": {"status": "skipped"},
        },
        "risk_signals": [{"signal_type": "dating_profile", "severity": "medium", "detail": "Active dating profile"}],
        "osint_risk_score": 12,
        "warnings": ["maigret: 6% access denied"],
        "created_at": "2026-11-16T15:00:00+00:00",
    }


def test_report_builds_recovery_leads_and_next_steps():
    from dashboard.services.osint_report import build_osint_report

    r = build_osint_report(_scan_doc())
    leads = r["leads"]

    assert [l["source"] for l in leads["locations"]] == ["exiftool", "ghunt"]
    assert leads["locations"][0]["map_url"] == "https://www.google.com/maps?q=26.6406,-81.8723"

    by_value = {c["value"]: c for c in leads["contacts"]}
    assert by_value["+12395559999"]["new"] == "NEW"
    assert by_value["joe@example.com"]["new"] == "searched"

    handles = {h["handle"]: h for h in leads["handles"]}
    assert handles["joe.alt"]["platform_count"] == 2 and handles["joe.alt"]["new"] == "NEW"
    assert handles["joeex"]["new"] == "searched"
    assert "nope" not in handles  # irrelevant rows are excluded
    assert r["hidden_irrelevant"] == 1
    assert [b["value"] for b in leads["breaches"]] == ["ExampleDump 2021"]

    cats = [c for c, _ in r["accounts_by_category"]]
    assert cats == ["social", "dating", "forum"]

    steps = " ".join(r["next_steps"])
    assert "Phone playbook on +12395559999" in steps
    assert "Alias playbook on @joe.alt" in steps
    assert "Re-run failed engines" in steps
    assert "Instaloader" in steps

    log = {e["engine_id"]: e for e in r["engine_log"]}
    assert log["hibf"]["status"] == "FAILED" and log["hibf"]["note"] == "upstream 503"
    assert ["Photo URL", "https://example.com/p.jpg"] in r["inputs"]


def test_pdf_renders_with_unicode_and_many_rows():
    from dashboard.services.osint_pdf_export import generate_osint_pdf

    doc = _scan_doc()
    doc["accounts"] += [
        {"platform": f"Site{i} — ✨", "url": f"https://s{i}.example/{'x' * 120}", "username": f"u{i}",
         "source": "sherlock", "confidence": "found", "category": "social"}
        for i in range(90)
    ]
    pdf = generate_osint_pdf(doc)
    assert isinstance(pdf, (bytes, bytearray))
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 4000


def test_pdf_renders_empty_scan():
    from dashboard.services.osint_pdf_export import generate_osint_pdf

    pdf = generate_osint_pdf({"_id": "x", "status": "failed", "engines_requested": ["maigret"]})
    assert pdf[:5] == b"%PDF-"


def test_csv_is_sectioned_and_formula_safe():
    from dashboard.services.osint_service import OSINTService

    svc = OSINTService()
    with patch.object(svc, "get_scan", AsyncMock(return_value=_scan_doc())):
        out = asyncio.run(svc.export_csv("65f0c0ffee0000000000abcd"))

    assert out.startswith("\ufeff")
    rows = list(csv.reader(io.StringIO(out.lstrip("\ufeff"))))
    assert rows[0][0] == "Section"
    sections = {r[0] for r in rows[1:] if r}
    for expected in ("Summary", "Search input", "Lead: location", "Lead: contact", "Lead: handle",
                     "Lead: breach", "Next step", "Account: social", "Risk signal (advisory)", "Engine log"):
        assert expected in sections, expected

    values = [cell for r in rows for cell in r]
    assert "'+12395559999" in values           # phone kept as text
    assert not any(v.startswith("=") for v in values)  # formula injection neutralised
    assert "'=HYPERLINK(\"x\")@evil.com" in values


def test_scan_request_accepts_plate_and_photo_only():
    from dashboard.models.osint import OSINTScanRequest

    req = OSINTScanRequest(license_plate="ABC1234", engines=["hibf"])
    assert req.license_plate == "ABC1234"
    req2 = OSINTScanRequest(image_url="https://example.com/p.jpg", engines=["exiftool"], top_sites=250)
    assert req2.image_url and req2.top_sites == 250 and req2.derive_usernames is True
