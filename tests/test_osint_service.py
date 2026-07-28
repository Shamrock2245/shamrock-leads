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


def test_extract_importable_fields():
    from dashboard.services.osint_service import OSINTService
    svc = OSINTService()
    doc = {
        "_id": "scan123",
        "subject_type": "defendant",
        "subject_id": "def456",
        "full_name": "John Doe",
        "scan_params": {"email": "john@example.com", "phone": "2395550100", "usernames": ["johndoe"]},
        "accounts": [
            {"platform": "Twitter", "url": "https://twitter.com/johndoe", "username": "johndoe"},
            {"platform": "GitHub", "url": "https://github.com/johndoe", "username": "johndoe"},
        ],
        "entities": [
            {"type": "email", "value": "john.alt@example.com"},
            {"type": "alias", "value": "Johnny Doe"},
        ],
        "platforms_found": ["Twitter", "GitHub"],
        "osint_risk_score": 10,
        "total_accounts": 2,
    }
    fields = svc.extract_importable_fields(doc)
    assert fields["email"] in ("john@example.com", "john.alt@example.com")
    assert "john.alt@example.com" in fields["emails"]
    assert fields["social_profiles"]["twitter"] == "https://twitter.com/johndoe"
    assert "johndoe" in fields["usernames"]
    assert "Johnny Doe" in fields["aliases"]


@pytest.mark.asyncio
async def test_delete_scan_and_clear_all(mocker):
    from dashboard.services.osint_service import OSINTService

    svc = OSINTService()
    mock_col = mocker.MagicMock()

    # Mock delete_one
    mock_delete_one_res = mocker.MagicMock()
    mock_delete_one_res.deleted_count = 1
    mock_col.delete_one = mocker.AsyncMock(return_value=mock_delete_one_res)

    # Mock delete_many
    mock_delete_many_res = mocker.MagicMock()
    mock_delete_many_res.deleted_count = 5
    mock_col.delete_many = mocker.AsyncMock(return_value=mock_delete_many_res)

    mock_db = {"osint_scans": mock_col}
    svc._db = mock_db
    mocker.patch("dashboard.services.audit_service.AuditService.log_event", mocker.AsyncMock())

    # Test single delete
    deleted = await svc.delete_scan("507f1f77bcf86cd799439011")
    assert deleted is True
    mock_col.delete_one.assert_called_once()

    # Test clear all
    cleared = await svc.delete_all_scans()
    assert cleared == 5
    mock_col.delete_many.assert_called_once()


