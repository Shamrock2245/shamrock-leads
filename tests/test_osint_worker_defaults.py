"""
Unit tests for osint-worker policy defaults (no network / CLI).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "osint-worker"
sys.path.insert(0, str(WORKER))

from defaults import (  # noqa: E402
    build_username_candidates,
    dedupe_accounts,
    resolve_tool_flags,
    score_signals,
    assess_maigret_quality,
)


def test_maigret_default_on_blackbird_off():
    m, b, notes = resolve_tool_flags(email=None, run_maigret=None, run_blackbird=None)
    assert m is True
    assert b is False
    assert any("blackbird default OFF" in n for n in notes)


def test_blackbird_on_when_email():
    m, b, notes = resolve_tool_flags(
        email="a@example.com", run_maigret=None, run_blackbird=None
    )
    assert m is True
    assert b is True
    assert any("email-focused" in n for n in notes)


def test_second_opinion_enables_blackbird():
    m, b, notes = resolve_tool_flags(
        email=None, run_maigret=None, run_blackbird=None, second_opinion=True
    )
    assert m is True
    assert b is True
    assert any("second opinion" in n for n in notes)


def test_explicit_blackbird_off_wins_over_email():
    m, b, _ = resolve_tool_flags(
        email="a@example.com", run_maigret=True, run_blackbird=False
    )
    assert m is True
    assert b is False


def test_username_candidates_prefer_explicit():
    c = build_username_candidates(["knownhandle"], "John Smith")
    assert c[0] == "knownhandle"
    # name-derived capped
    assert len([x for x in c if x != "knownhandle"]) <= 2


def test_dedupe_by_host():
    acc = [
        {"platform": "GitHub", "url": "https://github.com/u", "source": "maigret"},
        {"platform": "GitHub", "url": "https://www.github.com/u", "source": "blackbird"},
    ]
    # different source → both kept (source in key); host-only would collapse within source
    out = dedupe_accounts(acc)
    assert len(out) == 2
    out2 = dedupe_accounts([
        {"platform": "GitHub", "url": "https://github.com/u/", "source": "maigret"},
        {"platform": "GitHub", "url": "https://github.com/u", "source": "maigret"},
    ])
    assert len(out2) == 1


def test_score_empty_social_inactivity():
    score, signals = score_signals([])
    assert score > 0
    assert any(s["signal_type"] == "social_inactivity" for s in signals)


def test_degraded_quality_heuristic():
    stderr = 'Too many errors of type "Access denied" (20.0%).\nToo many errors of type "Just a moment: bot redirect challenge" (12.5%)'
    q = assess_maigret_quality(stderr)
    assert q["degraded"] is True
    assert q["reasons"]
