"""
Tests for OSINT Worker v2 — Sherlock + SpiderFoot parsers and tool resolution.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "osint-worker"))

from runners import (
    parse_maigret_json,
    parse_sherlock_json,
    parse_spiderfoot_json,
    parse_blackbird_json,
    _categorize_platform,
)
from defaults import (
    resolve_tool_flags,
    build_username_candidates,
    dedupe_accounts,
    score_signals,
)


# ── Sherlock Parser Tests ─────────────────────────────────────────────────────

def test_parse_sherlock_dict_format():
    """Sherlock outputs {site_name: {status, url_user, ...}}."""
    raw = {
        "Twitter": {"status": "Claimed", "url_user": "https://twitter.com/jsmith"},
        "GitHub": {"status": "Claimed", "url_user": "https://github.com/jsmith"},
        "FakeSite": {"status": "Available", "url_user": ""},
    }
    accounts = parse_sherlock_json(raw)
    assert len(accounts) == 2
    assert accounts[0]["source"] == "sherlock"
    assert accounts[0]["platform"] == "Twitter"
    assert accounts[1]["platform"] == "GitHub"


def test_parse_sherlock_list_format():
    """Some versions output a list of dicts."""
    raw = [
        {"site": "Twitter", "status": "Claimed", "url_user": "https://twitter.com/jsmith", "username": "jsmith"},
        {"site": "Reddit", "status": "Available", "url_user": ""},
    ]
    accounts = parse_sherlock_json(raw)
    assert len(accounts) == 1
    assert accounts[0]["platform"] == "Twitter"


def test_parse_sherlock_empty():
    assert parse_sherlock_json(None) == []
    assert parse_sherlock_json({}) == []
    assert parse_sherlock_json([]) == []


# ── SpiderFoot Parser Tests ───────────────────────────────────────────────────

def test_parse_spiderfoot_social_accounts():
    raw = [
        {"type": "SOCIAL_MEDIA", "data": "https://twitter.com/jsmith", "module": "sfp_accounts", "confidence": "high"},
        {"type": "EMAILADDR", "data": "jsmith@gmail.com", "module": "sfp_emailformat", "confidence": "medium"},
        {"type": "PHONE_NUMBER", "data": "+1-239-555-0100", "module": "sfp_phone", "confidence": "high"},
    ]
    accounts, entities = parse_spiderfoot_json(raw)
    assert len(accounts) == 1
    assert accounts[0]["source"] == "spiderfoot"
    assert len(entities) == 2
    assert entities[0]["type"] == "email"
    assert entities[1]["type"] == "phone"


def test_parse_spiderfoot_dict_wrapper():
    raw = {"results": [
        {"type": "SOCIAL_MEDIA", "data": "https://facebook.com/jsmith", "module": "sfp_social_media"},
    ]}
    accounts, entities = parse_spiderfoot_json(raw)
    assert len(accounts) == 1


def test_parse_spiderfoot_empty():
    accounts, entities = parse_spiderfoot_json(None)
    assert accounts == []
    assert entities == []


# ── Blackbird Parser Tests ────────────────────────────────────────────────────

def test_parse_blackbird_found():
    raw = [
        {"name": "Twitter", "url": "https://twitter.com/jsmith", "status": "FOUND"},
        {"name": "Reddit", "url": "https://reddit.com/u/jsmith", "status": "NOT FOUND"},
    ]
    accounts = parse_blackbird_json(raw)
    assert len(accounts) == 1
    assert accounts[0]["platform"] == "Twitter"


# ── Maigret Parser Tests ─────────────────────────────────────────────────────

def test_parse_maigret_sites_format():
    raw = {"sites": {
        "Twitter": {"status": {"status": "found", "url": "https://twitter.com/jsmith"}},
        "FakeSite": {"status": {"status": "not found"}},
    }}
    accounts = parse_maigret_json(raw)
    assert len(accounts) == 1
    assert accounts[0]["source"] == "maigret"


# ── Platform Categorization ───────────────────────────────────────────────────

def test_categorize_platform():
    assert _categorize_platform("Twitter") == "social"
    assert _categorize_platform("GitHub") == "professional"
    assert _categorize_platform("Reddit") == "forum"
    assert _categorize_platform("Tinder") == "dating"
    assert _categorize_platform("SomeRandomSite") == "other"


# ── Policy Defaults ───────────────────────────────────────────────────────────

def test_resolve_tool_flags_defaults():
    mg, bb, notes = resolve_tool_flags(email=None, run_maigret=None, run_blackbird=None)
    assert mg is True
    assert bb is False


def test_resolve_tool_flags_email_enables_blackbird():
    mg, bb, notes = resolve_tool_flags(email="test@test.com", run_maigret=None, run_blackbird=None)
    assert bb is True


# ── Username Candidates ───────────────────────────────────────────────────────

def test_build_username_candidates():
    result = build_username_candidates(["jsmith"], "John Smith")
    assert "jsmith" in result
    assert len(result) >= 1


def test_build_username_candidates_name_only():
    result = build_username_candidates([], "John Smith")
    assert len(result) >= 1
    assert "john.smith" in result


# ── Deduplication ─────────────────────────────────────────────────────────────

def test_dedupe_accounts():
    accounts = [
        {"platform": "Twitter", "url": "https://twitter.com/jsmith", "source": "maigret"},
        {"platform": "Twitter", "url": "https://twitter.com/jsmith", "source": "maigret"},
        {"platform": "Twitter", "url": "https://twitter.com/jsmith", "source": "sherlock"},
    ]
    result = dedupe_accounts(accounts)
    assert len(result) == 2  # One per source


# ── Risk Scoring ──────────────────────────────────────────────────────────────

def test_score_signals_empty():
    score, signals = score_signals([])
    assert score > 0  # social_inactivity signal
    assert any(s["signal_type"] == "social_inactivity" for s in signals)


def test_score_signals_high_count():
    accounts = [{"platform": f"site{i}", "url": f"https://site{i}.com/u", "source": "maigret", "profile_data": {}} for i in range(35)]
    score, signals = score_signals(accounts)
    assert score >= 20
    assert any(s["signal_type"] == "high_account_count" for s in signals)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
