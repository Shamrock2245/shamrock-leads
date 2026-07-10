"""
Unit tests for OSINT parsers + fail-loud tool meta (no live network / CLI).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.services.osint_service import OSINTService, _score_signals


@pytest.fixture
def svc() -> OSINTService:
    return OSINTService()


# ── Maigret parsers ────────────────────────────────────────────────────────────

def test_parse_maigret_simple_claimed(svc: OSINTService):
    raw = {
        "GitHub": {
            "status": {
                "status": "Claimed",
                "url": "https://github.com/johndoe",
                "username": "johndoe",
            },
            "url_user": "https://github.com/johndoe",
        },
        "Twitter": {
            "status": {"status": "Claimed", "url": "https://x.com/johndoe"},
        },
        "Reddit": {
            "status": {"status": "Not Found"},
        },
    }
    accounts = svc._parse_maigret_json(raw)
    platforms = {a["platform"] for a in accounts}
    assert platforms == {"GitHub", "Twitter"}
    assert all(a["source"] == "maigret" for a in accounts)
    assert all(a["url"] for a in accounts)


def test_parse_maigret_nested_sites_found_id(svc: OSINTService):
    raw = {
        "sites": {
            "Instagram": {
                "status": {"id": "Found", "url": "https://instagram.com/x"},
                "username": "x",
            },
            "MetaKey": "skip-me",
        },
        "username": "x",
    }
    accounts = svc._parse_maigret_json(raw)
    assert len(accounts) == 1
    assert accounts[0]["platform"] == "Instagram"


def test_parse_maigret_empty_dict_is_zero(svc: OSINTService):
    assert svc._parse_maigret_json({}) == []
    assert svc._parse_maigret_json(None) == []


# ── Blackbird parsers ──────────────────────────────────────────────────────────

def test_parse_blackbird_list_found(svc: OSINTService):
    raw = [
        {"name": "GitHub", "url": "https://github.com/u", "status": "FOUND", "username": "u"},
        {"name": "Ghost", "url": "https://ghost.example/u", "status": "NOT FOUND"},
    ]
    accounts = svc._parse_blackbird_json(raw)
    assert len(accounts) == 1
    assert accounts[0]["platform"] == "GitHub"
    assert accounts[0]["source"] == "blackbird"


def test_parse_blackbird_results_wrapper(svc: OSINTService):
    raw = {
        "results": [
            {"site": "TikTok", "url": "https://tiktok.com/@u", "Status": "FOUND"},
        ]
    }
    accounts = svc._parse_blackbird_json(raw)
    assert len(accounts) == 1
    assert accounts[0]["platform"] == "TikTok"


def test_parse_blackbird_found_only_export_no_status(svc: OSINTService):
    # Some exports omit status and only include found accounts
    raw = [{"name": "Pinterest", "url": "https://pinterest.com/u"}]
    accounts = svc._parse_blackbird_json(raw)
    assert len(accounts) == 1


# ── Risk signals ───────────────────────────────────────────────────────────────

def test_score_signals_zero_accounts_social_inactivity():
    score, signals = _score_signals([], "defendant")
    assert score > 0
    assert any(s["signal_type"] == "social_inactivity" for s in signals)


def test_score_signals_high_account_count():
    accounts = [{"platform": f"p{i}", "profile_data": {}, "source": "maigret"} for i in range(35)]
    score, signals = _score_signals(accounts, "defendant")
    assert score >= 20
    assert any(s["signal_type"] == "high_account_count" for s in signals)


# ── Fail-loud status decision (pure helper logic) ──────────────────────────────

def _decide_status(
    run_maigret: bool,
    run_blackbird: bool,
    tool_results: dict,
    account_count: int,
    fatal_exc: str | None = None,
) -> str:
    """
    Mirror of fail-loud status rules in OSINTService._execute_scan.
    Kept here so status policy is unit-testable without Mongo/async.
    """
    attempted = [k for k, v in tool_results.items() if isinstance(v, dict) and v.get("attempted")]
    succeeded = [k for k, v in tool_results.items() if isinstance(v, dict) and v.get("ok")]
    failed_tools = [k for k, v in tool_results.items() if isinstance(v, dict) and not v.get("ok")]
    any_tool_requested = bool(run_maigret or run_blackbird)

    if fatal_exc:
        return "failed"
    if not any_tool_requested:
        return "failed"
    if not attempted and failed_tools:
        return "failed"
    if attempted and not succeeded:
        return "failed"
    if failed_tools and succeeded:
        return "partial" if account_count else "failed"
    if succeeded and not account_count:
        return "complete"
    return "complete"


def test_fail_loud_tools_missing_is_failed():
    status = _decide_status(
        True, True,
        {
            "maigret": {"ok": False, "error": "not installed", "attempted": False},
            "blackbird": {"ok": False, "error": "not installed", "attempted": False},
        },
        account_count=0,
    )
    assert status == "failed"


def test_fail_loud_all_tools_errored_is_failed():
    status = _decide_status(
        True, True,
        {
            "maigret": {"ok": False, "error": "timeout", "attempted": True},
            "blackbird": {"ok": False, "error": "no json", "attempted": True},
        },
        account_count=0,
    )
    assert status == "failed"


def test_legitimate_empty_scan_is_complete():
    status = _decide_status(
        True, True,
        {
            "maigret": {"ok": True, "attempted": True, "accounts": 0},
            "blackbird": {"ok": True, "attempted": True, "accounts": 0},
        },
        account_count=0,
    )
    assert status == "complete"


def test_partial_when_one_tool_works_with_hits():
    status = _decide_status(
        True, True,
        {
            "maigret": {"ok": True, "attempted": True, "accounts": 3},
            "blackbird": {"ok": False, "error": "timeout", "attempted": True},
        },
        account_count=3,
    )
    assert status == "partial"


def test_partial_with_zero_accounts_is_failed():
    # One tool "ok" empty + other failed → no usable success path for empty
    # When succeeded exists but account_count=0 and failed_tools also exist:
    status = _decide_status(
        True, True,
        {
            "maigret": {"ok": True, "attempted": True, "accounts": 0},
            "blackbird": {"ok": False, "error": "crash", "attempted": True},
        },
        account_count=0,
    )
    assert status == "failed"


def test_probe_tools_returns_structure():
    probe = OSINTService.probe_tools()
    assert "maigret" in probe
    assert "blackbird" in probe
    assert "trape" in probe
    assert "ready_for_scans" in probe
    assert "available" in probe["maigret"]
    assert "available" in probe["blackbird"]
