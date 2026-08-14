"""
Unit tests for OSINT parsers kept for worker runners + dashboard smoke.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "osint-worker"))

from runners import (  # noqa: E402
    parse_blackbird_json,
    parse_holehe_results,
    parse_ignorant_results,
    parse_maigret_json,
    parse_phone_for_ignorant,
    parse_toutatis_user,
    run_holehe,
)
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


def test_parse_phone_for_ignorant_nanp():
    assert parse_phone_for_ignorant("239-555-0100") == ("1", "2395550100")
    assert parse_phone_for_ignorant("+1 (239) 555-0100") == ("1", "2395550100")
    assert parse_phone_for_ignorant("12395550100") == ("1", "2395550100")
    assert parse_phone_for_ignorant("") is None
    assert parse_phone_for_ignorant("123") is None


def test_parse_ignorant_results_exists_only():
    raw = [
        {"name": "instagram", "domain": "instagram.com", "rateLimit": False, "exists": True},
        {"name": "snapchat", "domain": "snapchat.com", "rateLimit": False, "exists": False},
        {"name": "amazon", "domain": "amazon.com", "rateLimit": True, "exists": False},
    ]
    accounts, entities = parse_ignorant_results(
        raw, country_code="1", national="2395550100"
    )
    assert len(accounts) == 1
    assert accounts[0]["platform"] == "Instagram"
    assert accounts[0]["source"] == "ignorant"
    assert accounts[0]["profile_data"]["phone_registered"] is True
    assert any(e["type"] == "phone" for e in entities)


def test_score_signals_phone_linked_social():
    accounts = [
        {
            "platform": "Instagram",
            "source": "ignorant",
            "profile_data": {"phone_registered": True},
        },
        {
            "platform": "Snapchat",
            "source": "ignorant",
            "profile_data": {"phone_registered": True},
        },
    ]
    score, signals = score_signals(accounts)
    assert score >= 8
    assert any(s["signal_type"] == "phone_linked_social" for s in signals)


def test_parse_toutatis_user_extracts_pii():
    user = {
        "username": "jdoe",
        "userID": "123",
        "full_name": "John Doe",
        "biography": "hello",
        "is_private": False,
        "is_verified": False,
        "is_business": False,
        "follower_count": 10,
        "following_count": 5,
        "media_count": 2,
        "external_url": "",
        "public_email": "john@example.com",
        "public_phone_number": "5550100",
        "public_phone_country_code": "1",
        "hd_profile_pic_url_info": {"url": "https://cdn.example/p.jpg"},
    }
    lookup = {"obfuscated_email": "jo***@example.com", "obfuscated_phone": "+1 ***0100"}
    accounts, entities = parse_toutatis_user(user, lookup=lookup)
    assert len(accounts) == 1
    assert accounts[0]["source"] == "toutatis"
    assert accounts[0]["username"] == "jdoe"
    assert accounts[0]["profile_data"]["public_email"] == "john@example.com"
    types = {e["type"] for e in entities}
    assert "email" in types
    assert "phone" in types
    assert "name" in types


def test_score_signals_toutatis_contact_pii():
    accounts = [
        {
            "platform": "Instagram",
            "source": "toutatis",
            "profile_data": {
                "public_email": "a@b.com",
                "public_phone": "+1 555",
            },
        }
    ]
    score, signals = score_signals(accounts)
    assert score >= 5
    assert any(s["signal_type"] == "instagram_contact_pii" for s in signals)


def test_probe_tools_structure():
    from runners import probe_tools
    probe = probe_tools()
    assert "maigret" in probe
    assert "blackbird" in probe
    assert "ignorant" in probe
    assert "holehe" in probe
    assert "toutatis" in probe
    assert "ready_for_scans" in probe
    assert "defaults" in probe
    assert probe["defaults"].get("ignorant_on_phone") is True
    assert probe["defaults"].get("holehe_on_email") is True
    assert probe["defaults"].get("toutatis_on_username") is True
    # Without session cookie, package may install but not be runnable
    assert "session_configured" in probe["toutatis"]


def test_parse_holehe_results_only_exists():
    raw = [
        {"name": "instagram", "domain": "instagram.com", "exists": True, "rateLimit": False},
        {"name": "twitter", "domain": "twitter.com", "exists": False, "rateLimit": False},
        {"name": "snapchat", "domain": "snapchat.com", "exists": False, "rateLimit": True},
    ]
    accounts, entities = parse_holehe_results(raw, email="jdoe@example.com")
    assert len(accounts) == 1
    assert accounts[0]["platform"] == "Instagram"
    assert accounts[0]["source"] == "holehe"
    assert accounts[0]["profile_data"]["email_registered"] is True
    assert entities and entities[0]["type"] == "email"
    assert "jdoe@" not in (entities[0].get("value") or "")


def test_score_signals_holehe_email():
    accounts = [
        {"platform": "Instagram", "source": "holehe", "profile_data": {"email_registered": True}},
        {"platform": "Twitter", "source": "holehe", "profile_data": {"email_registered": True}},
    ]
    score, signals = score_signals(accounts)
    assert score >= 3
    assert any(s["signal_type"] == "email_linked_accounts" for s in signals)


@pytest.mark.asyncio
async def test_run_holehe_invalid_email():
    result = await run_holehe("not-an-email")
    assert result["ok"] is False
    assert "email" in (result.get("error") or "").lower()


def test_instagram_session_id_url_decodes(monkeypatch):
    from runners import get_instagram_session_id

    monkeypatch.setenv(
        "INSTAGRAM_SESSION_ID",
        "12345%3Aabc%3A9%3Axyz",
    )
    monkeypatch.delenv("TOUTATIS_SESSION_ID", raising=False)
    assert get_instagram_session_id() == "12345:abc:9:xyz"


def test_instagram_session_id_strips_quotes(monkeypatch):
    from runners import get_instagram_session_id

    monkeypatch.setenv("INSTAGRAM_SESSION_ID", "'12345:abc:9:xyz'")
    assert get_instagram_session_id() == "12345:abc:9:xyz"


@pytest.mark.asyncio
async def test_run_toutatis_fails_closed_without_session(monkeypatch):
    from runners import run_toutatis

    monkeypatch.delenv("INSTAGRAM_SESSION_ID", raising=False)
    monkeypatch.delenv("TOUTATIS_SESSION_ID", raising=False)
    result = await run_toutatis(["someuser"])
    assert result["ok"] is False
    err = (result.get("error") or "").lower()
    assert "session" in err or "not installed" in err


@pytest.mark.asyncio
async def test_run_ignorant_invalid_phone():
    from runners import run_ignorant

    result = await run_ignorant("12")
    assert result["ok"] is False
    assert result.get("error")


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
async def test_delete_scan_and_clear_all():
    from unittest.mock import MagicMock, AsyncMock, patch
    from dashboard.services.osint_service import OSINTService

    svc = OSINTService()
    mock_col = MagicMock()

    # Mock delete_one
    mock_delete_one_res = MagicMock()
    mock_delete_one_res.deleted_count = 1
    mock_col.delete_one = AsyncMock(return_value=mock_delete_one_res)

    # Mock delete_many
    mock_delete_many_res = MagicMock()
    mock_delete_many_res.deleted_count = 5
    mock_col.delete_many = AsyncMock(return_value=mock_delete_many_res)

    mock_db = {"osint_scans": mock_col}
    svc._db = mock_db
    with patch("dashboard.services.audit_service.AuditService.log_event", AsyncMock()):
        # Test single delete
        deleted = await svc.delete_scan("507f1f77bcf86cd799439011")
        assert deleted is True
        mock_col.delete_one.assert_called_once()

        # Test clear all
        cleared = await svc.delete_all_scans()
        assert cleared == 5
        mock_col.delete_many.assert_called_once()


