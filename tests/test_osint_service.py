"""
Unit tests for OSINT parsers kept for worker runners + dashboard smoke.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "osint-worker"))

from runners import parse_blackbird_json, parse_maigret_json  # noqa: E402
from defaults import score_signals  # noqa: E402


def test_parse_maigret_simple_claimed():
    raw = {
        "GitHub": {
            "status": {
                "status": "Claimed",
                "url": "https://github.com/johndoe",
                "username": "johndoe",
            },
        },
        "Twitter": {"status": {"status": "Claimed", "url": "https://x.com/johndoe"}},
        "Reddit": {"status": {"status": "Not Found"}},
    }
    accounts = parse_maigret_json(raw)
    platforms = {a["platform"] for a in accounts}
    assert platforms == {"GitHub", "Twitter"}


def test_parse_maigret_nested_sites_found_id():
    raw = {
        "sites": {
            "Instagram": {
                "status": {"id": "Found", "url": "https://instagram.com/x"},
                "username": "x",
            },
        },
        "username": "x",
    }
    accounts = parse_maigret_json(raw)
    assert len(accounts) == 1
    assert accounts[0]["platform"] == "Instagram"


def test_parse_maigret_empty():
    assert parse_maigret_json({}) == []
    assert parse_maigret_json(None) == []


def test_parse_blackbird_list_found():
    raw = [
        {"name": "GitHub", "url": "https://github.com/u", "status": "FOUND", "username": "u"},
        {"name": "Ghost", "url": "https://ghost.example/u", "status": "NOT FOUND"},
    ]
    accounts = parse_blackbird_json(raw)
    assert len(accounts) == 1
    assert accounts[0]["platform"] == "GitHub"


def test_parse_blackbird_results_wrapper():
    raw = {"results": [{"site": "TikTok", "url": "https://tiktok.com/@u", "Status": "FOUND"}]}
    accounts = parse_blackbird_json(raw)
    assert len(accounts) == 1


def test_score_signals_high_account_count():
    accounts = [{"platform": f"p{i}", "profile_data": {}, "source": "maigret"} for i in range(35)]
    score, signals = score_signals(accounts)
    assert score >= 20
    assert any(s["signal_type"] == "high_account_count" for s in signals)


def test_probe_tools_structure():
    from runners import probe_tools
    probe = probe_tools()
    assert "maigret" in probe
    assert "blackbird" in probe
    assert "ready_for_scans" in probe
    assert "defaults" in probe
