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
    module_short_name,
    resolve_tool_flags,
    score_signals,
    assess_maigret_quality,
    select_holehe_modules,
)


def test_holehe_first_pass_skips_rate_limit_bait():
    class _Mod:
        def __init__(self, name):
            self.__name__ = name

    mods = [
        _Mod("instagram"),
        _Mod("amazon"),
        _Mod("voxmedia"),
        _Mod("atlassian"),
        _Mod("codecademy"),
        _Mod("paypal"),
    ]
    quick = select_holehe_modules(mods, deep=False)
    names = {module_short_name(m) for m in quick}
    assert "instagram" in names
    assert "paypal" in names
    assert "voxmedia" not in names
    assert "atlassian" not in names
    deep = select_holehe_modules(mods, deep=True)
    deep_names = {module_short_name(m) for m in deep}
    assert "voxmedia" not in deep_names
    assert "instagram" in deep_names
    assert len(deep) >= len(quick)


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
