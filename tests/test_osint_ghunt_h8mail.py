"""Parsers and scoring for GHunt + h8mail (no live Google/breach calls)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "osint-worker"))

from defaults import score_signals  # noqa: E402
from runners import (  # noqa: E402
    ghunt_creds_present,
    parse_ghunt_email_json,
    parse_h8mail_json,
)


def test_parse_ghunt_extracts_maps_address_and_name():
    raw = {
        "PROFILE_CONTAINER": {
            "profile": {
                "personId": "gaia-123",
                "names": {"PROFILE": {"value": "Jane Skip"}},
                "emails": {"PROFILE": {"value": "jane@gmail.com"}},
            },
            "maps": {
                "reviews": [
                    {"address": "1528 Broadway, Fort Myers, FL 33901"},
                    {"formattedAddress": "100 Main St, Naples, FL"},
                ]
            },
        }
    }
    accounts, entities = parse_ghunt_email_json(raw)
    platforms = {a["platform"] for a in accounts}
    assert "Google" in platforms
    assert "Google Maps" in platforms
    maps = next(a for a in accounts if a["platform"] == "Google Maps")
    assert maps["profile_data"]["has_location"] is True
    assert maps["source"] == "ghunt"
    addr_ents = [e for e in entities if e["type"] == "address"]
    assert any("Fort Myers" in e["value"] for e in addr_ents)
    assert any(e["type"] == "email" for e in entities)


def test_parse_ghunt_empty():
    assert parse_ghunt_email_json(None) == ([], [])
    assert parse_ghunt_email_json({}) == ([], [])


def test_parse_h8mail_flags_pwned_without_storing_password():
    raw = {
        "targets": {
            "jane@gmail.com": {
                "data": [
                    ["HIBP", "jane@gmail.com"],
                    ["hunter", "jane@gmail.com:SuperSecret99"],
                    ["HIBP", "duplicate"],
                ]
            }
        }
    }
    accounts, entities = parse_h8mail_json(raw, email="jane@gmail.com")
    sources = {(a.get("profile_data") or {}).get("source") for a in accounts}
    assert "HIBP" in sources
    assert "hunter" in sources
    hunter = next(a for a in accounts if (a.get("profile_data") or {}).get("source") == "hunter")
    assert hunter["profile_data"]["pwned"] is True
    assert hunter["profile_data"]["has_password"] is True
    blob = str(accounts)
    assert "SuperSecret99" not in blob
    assert any(e["type"] == "breach" for e in entities)
    email_ent = next(e for e in entities if e["type"] == "email")
    assert "***" in email_ent["value"]


def test_parse_h8mail_empty():
    assert parse_h8mail_json(None) == ([], [])
    assert parse_h8mail_json({}) == ([], [])


def test_score_signals_ghunt_and_h8mail():
    accounts = [
        {
            "platform": "Google Maps",
            "source": "ghunt",
            "profile_data": {"has_location": True, "location_count": 2},
        },
        {
            "platform": "Breach:HIBP",
            "source": "h8mail",
            "profile_data": {"pwned": True, "source": "HIBP"},
        },
    ]
    score, signals = score_signals(accounts)
    kinds = {s["signal_type"] for s in signals}
    assert "google_maps_footprint" in kinds
    assert "email_breach_exposure" in kinds
    assert score > 0


def test_ghunt_creds_absent_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("GHUNT_CREDS_PATH", raising=False)
    assert ghunt_creds_present() is False
    creds = tmp_path / ".malfrats" / "ghunt"
    creds.mkdir(parents=True)
    (creds / "creds.m").write_text("x" * 40)
    assert ghunt_creds_present() is True


def test_engine_enum_includes_new_tools():
    from dashboard.models.osint import EngineType
    assert EngineType.ghunt.value == "ghunt"
    assert EngineType.h8mail.value == "h8mail"


def test_dashboard_ui_lists_ghunt_and_h8mail():
    js = (ROOT / "dashboard" / "sl-osint.js").read_text()
    html = (ROOT / "dashboard" / "index.html").read_text()
    assert "data-engine=\"ghunt\"" in html
    assert "data-engine=\"h8mail\"" in html
    assert "'ghunt'" in js and "'h8mail'" in js
