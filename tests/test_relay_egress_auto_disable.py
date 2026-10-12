"""Relay-only counties (config/relay_only.py) are never auto-disabled by
egress-blocked failures (403 / Cloudflare challenge / relay exit gate).

Context 2026-10-09: Charlotte (7) and Manatee (10) consecutive-failure counts
rose from Cloudflare 403s on the Comcast relay exit. Those failures now go to
``egress_blocked_failures``; parser failures still count and alert.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scrapers import base_scraper as bs
from scrapers import scraper_resilience as sr
from scrapers.scraper_resilience import EgressBlocked, ParseDriftError
from tests.test_base_scraper_self_heal import (  # noqa: F401 - fixture re-export
    FakeStatusWriter,
    ScriptedScraper,
    _http_error,
    _isolate,
    _record,
)

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def _scraper_cls(county, state="FL"):
    return type(f"S_{county}", (ScriptedScraper,), {"label_county": county, "label_state": state})


# ── State machine ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("exc", [
    EgressBlocked("egress_block: Charlotte page 2 stuck on a Cloudflare challenge/block (HTTP 403)"),
    _http_error(403),
    RuntimeError("Cloudflare challenge: Just a moment..."),
])
def test_relay_egress_block_is_counted_separately(exc):
    verdict = sr.classify_exception(exc)
    assert sr.is_egress_blocked(verdict)
    state = sr.ResilienceState(consecutive_failures=4)
    for _ in range(10):
        state, tripped = sr.state_after_failure(state, verdict, NOW, threshold=5, relay_only=True)
        assert tripped is False
    assert state.consecutive_failures == 4  # unchanged
    assert state.egress_blocked_failures == 10
    assert state.auto_disabled is False
    assert state.last_error_class == sr.ERROR_EGRESS_BLOCKED


def test_non_relay_scope_still_counts_403():
    verdict = sr.classify_exception(_http_error(403))
    state = sr.ResilienceState(consecutive_failures=4)
    state, tripped = sr.state_after_failure(state, verdict, NOW, threshold=5, relay_only=False)
    assert tripped and state.auto_disabled and state.consecutive_failures == 5


@pytest.mark.parametrize("exc", [
    ParseDriftError("Charlotte: no roster table on page 1"),
    RuntimeError("Pinellas: every Subject Charge Report modal failed to render (reasons: selector_not_found=10)"),
])
def test_relay_parser_failures_still_count(exc):
    verdict = sr.classify_exception(exc)
    assert not sr.is_egress_blocked(verdict)
    state = sr.ResilienceState(consecutive_failures=4)
    state, tripped = sr.state_after_failure(state, verdict, NOW, threshold=5, relay_only=True)
    assert tripped and state.consecutive_failures == 5 and state.egress_blocked_failures == 0


def test_cooldown_is_not_egress_blocked():
    verdict = sr.classify_exception(sr.SourceCooldownActive("rate-limit cooldown"))
    assert not sr.is_egress_blocked(verdict)


def test_success_resets_egress_counter_and_state_round_trips():
    state = sr.ResilienceState(consecutive_failures=2, egress_blocked_failures=7)
    assert sr.ResilienceState.from_doc(state.to_fields()) == state
    assert sr.ResilienceState.from_doc({"egress_blocked_failures": "x"}).egress_blocked_failures == 0
    after, _ = sr.state_after_success(state, 3, NOW)
    assert after.consecutive_failures == 0 and after.egress_blocked_failures == 0


def test_threshold_is_five_and_lives_in_scraper_resilience(monkeypatch):
    monkeypatch.delenv("SCRAPER_AUTO_DISABLE_THRESHOLD", raising=False)
    assert sr.auto_disable_threshold() == 5


# ── BaseScraper.run() integration ──────────────────────────────────────────
def test_pinellas_egress_gate_failures_never_auto_disable(_isolate):
    Pin = _scraper_cls("Pinellas")
    writer = FakeStatusWriter()
    gate = EgressBlocked("egress_block: PINELLAS_EGRESS_MODE=direct but this host's exit is not verified US residential")
    for _ in range(8):
        result = Pin([gate]).run(writers=[writer])
    assert result["status"] == "error" and result["egress_blocked"] is True
    assert writer.doc["auto_disabled"] is False
    assert writer.doc["consecutive_failures"] == 0
    assert writer.doc["egress_blocked_failures"] == 8
    assert writer.doc["last_error_class"] == "egress_blocked"
    assert "notify_scraper_auto_disabled" not in _isolate.names()


def test_pinellas_real_failures_still_auto_disable(_isolate):
    Pin = _scraper_cls("Pinellas")
    writer = FakeStatusWriter()
    err = RuntimeError("Pinellas: every Subject Charge Report modal failed to render (reasons: selector_not_found=10)")
    results = [Pin([err]).run(writers=[writer]) for _ in range(5)]
    assert results[-1]["status"] == "auto_disabled"
    assert writer.doc["consecutive_failures"] == 5
    assert "notify_scraper_auto_disabled" in _isolate.names()


@pytest.mark.parametrize("county", ["Charlotte", "Manatee"])
def test_charlotte_manatee_cloudflare_403s_do_not_grow_the_streak(_isolate, county):
    S = _scraper_cls(county)
    writer = FakeStatusWriter()
    writer.doc["consecutive_failures"] = 4
    for _ in range(6):
        S([_http_error(403)]).run(writers=[writer])
    assert writer.doc["consecutive_failures"] == 4
    assert writer.doc["egress_blocked_failures"] == 6
    # No exempt "would have auto-disabled" alert from egress failures.
    assert "notify_scraper_auto_disabled" not in _isolate.names()
    # A real parser failure still counts and hits the exempt alert at 5.
    S([ParseDriftError(f"{county}: no roster table on page 1")]).run(writers=[writer])
    assert writer.doc["consecutive_failures"] == 5
    alerts = [c for c in _isolate.calls if c[0] == "notify_scraper_auto_disabled"]
    assert len(alerts) == 1 and alerts[0][2].get("exempt") is True
    assert writer.doc["auto_disabled"] is False  # still exempt from the skip


def test_relay_success_clears_both_counters(_isolate):
    S = _scraper_cls("Charlotte")
    writer = FakeStatusWriter()
    writer.doc.update({"consecutive_failures": 3, "egress_blocked_failures": 9})
    S([[_record()]]).run(writers=[writer])
    assert writer.doc["consecutive_failures"] == 0 and writer.doc["egress_blocked_failures"] == 0


def test_mongo_writer_reads_and_resets_the_egress_counter():
    from pathlib import Path

    src = (Path(bs.__file__).resolve().parents[1] / "writers" / "mongo_writer.py").read_text()
    fields = src.split("_RESILIENCE_FIELDS = (", 1)[1].split(")", 1)[0]
    assert '"egress_blocked_failures"' in fields
    reenable = src.split("def reenable_scraper", 1)[1].split("def close", 1)[0]
    assert '"egress_blocked_failures": 0' in reenable
