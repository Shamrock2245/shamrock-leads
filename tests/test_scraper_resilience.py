"""Unit tests for scrapers/scraper_resilience.py (backoff, classification,
auto-disable state machine, drift detection, Obscura routing policy)."""
from __future__ import annotations

import ast
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from scrapers import scraper_resilience as sr

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _http_error(code: int, body: str = "") -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = code
    resp._content = body.encode()
    return requests.HTTPError(f"{code} Error for url: https://example.invalid/roster", response=resp)


# ── Classification ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "exc, expected, retryable",
    [
        (requests.ConnectionError("Max retries exceeded"), sr.ERROR_NETWORK, True),
        (requests.Timeout("read timed out"), sr.ERROR_NETWORK, True),
        (TimeoutError("timed out"), sr.ERROR_NETWORK, True),
        (ConnectionResetError("reset by peer"), sr.ERROR_NETWORK, True),
        (socket.gaierror("Name or service not known"), sr.ERROR_NETWORK, True),
        (_http_error(502), sr.ERROR_NETWORK, True),
        (_http_error(503), sr.ERROR_NETWORK, True),
        (RuntimeError("curl: (28) Operation timed out after 30000 ms"), sr.ERROR_NETWORK, True),
        (_http_error(403), sr.ERROR_ANTI_BOT, False),
        (_http_error(429), sr.ERROR_ANTI_BOT, False),
        (RuntimeError("HTTP 429 Too Many Requests"), sr.ERROR_ANTI_BOT, False),
        (RuntimeError("Cloudflare challenge: Just a moment..."), sr.ERROR_ANTI_BOT, False),
        (RuntimeError("503 Server Error: Cloudflare Just a moment"), sr.ERROR_ANTI_BOT, False),
        (RuntimeError("reCAPTCHA failed to solve"), sr.ERROR_ANTI_BOT, False),
        (_http_error(404), sr.ERROR_URL_CHANGED, False),
        (_http_error(410), sr.ERROR_URL_CHANGED, False),
        (RuntimeError("Page not found on roster host"), sr.ERROR_URL_CHANGED, False),
        (sr.ParseDriftError("roster table missing"), sr.ERROR_PARSE_DRIFT, False),
        (KeyError("BookingNumber"), sr.ERROR_PARSE_DRIFT, False),
        (IndexError("list index out of range"), sr.ERROR_PARSE_DRIFT, False),
        (AttributeError("'NoneType' object has no attribute 'find_all'"), sr.ERROR_PARSE_DRIFT, False),
        (ValueError("something odd"), sr.ERROR_UNKNOWN, False),
    ],
)
def test_classification_uses_fixed_vocabulary(exc, expected, retryable):
    verdict = sr.classify_exception(exc)
    assert verdict.error_class == expected
    assert verdict.retryable is retryable
    assert verdict.error_class in sr.ERROR_CLASSES


def test_cooldown_is_anti_bot_not_retryable_and_not_counted():
    for exc in (
        sr.SourceCooldownActive("Lee public-api rate-limit cooldown (100s / 0.0h remaining)"),
        RuntimeError("Lee public-api rate-limit cooldown (10800s / 3.0h remaining)"),
        RuntimeError("Lee public-api rate-limit tripped mid-fetch (3s remaining)"),
    ):
        verdict = sr.classify_exception(exc)
        assert verdict.error_class == sr.ERROR_ANTI_BOT
        assert verdict.cooldown is True
        assert verdict.retryable is False
        assert verdict.counts_toward_disable is False


def test_error_class_vocabulary_is_fixed():
    assert sr.ERROR_CLASSES == ("network", "anti_bot", "url_changed", "parse_drift", "unknown")


# ── Backoff ────────────────────────────────────────────────────────────────
def test_retry_backoff_is_2_4_8_then_raises():
    sleeps: list[float] = []
    calls = {"n": 0}

    def always_timeout():
        calls["n"] += 1
        raise requests.Timeout("read timed out")

    with pytest.raises(requests.Timeout):
        sr.retry_transient(always_timeout, sleep=sleeps.append)
    assert sleeps == [2.0, 4.0, 8.0]
    assert calls["n"] == 4  # initial + 3 retries


def test_retry_recovers_after_transient_failure():
    sleeps: list[float] = []
    outcomes = iter([requests.ConnectionError("reset"), _http_error(502), ["ok"]])

    def flaky():
        item = next(outcomes)
        if isinstance(item, Exception):
            raise item
        return item

    assert sr.retry_transient(flaky, sleep=sleeps.append) == ["ok"]
    assert sleeps == [2.0, 4.0]


@pytest.mark.parametrize(
    "exc",
    [
        _http_error(429),
        _http_error(403),
        _http_error(404),
        sr.ParseDriftError("drift"),
        sr.SourceCooldownActive("cooldown"),
        ValueError("unknown"),
    ],
)
def test_non_transient_failures_are_never_retried(exc):
    sleeps: list[float] = []
    calls = {"n": 0}

    def fail():
        calls["n"] += 1
        raise exc

    with pytest.raises(type(exc)):
        sr.retry_transient(fail, sleep=sleeps.append)
    assert sleeps == []
    assert calls["n"] == 1


def test_retry_never_runs_into_an_active_cooldown():
    sleeps: list[float] = []
    calls = {"n": 0}

    def fail():
        calls["n"] += 1
        raise requests.Timeout("timed out")

    with pytest.raises(requests.Timeout):
        sr.retry_transient(fail, sleep=sleeps.append, cooldown_active=lambda: True)
    assert calls["n"] == 1 and sleeps == []


def test_retry_stops_if_cooldown_trips_during_backoff():
    sleeps: list[float] = []
    state = {"cool": False, "calls": 0}

    def fail():
        state["calls"] += 1
        raise requests.Timeout("timed out")

    def sleep(delay):
        sleeps.append(delay)
        state["cool"] = True  # e.g. another consumer recorded a Lee 429

    with pytest.raises(requests.Timeout):
        sr.retry_transient(fail, sleep=sleep, cooldown_active=lambda: state["cool"])
    assert sleeps == [2.0]
    assert state["calls"] == 1


# ── Auto-disable state machine ─────────────────────────────────────────────
NET = sr.ErrorClassification(sr.ERROR_NETWORK, retryable=True)
COOL = sr.ErrorClassification(sr.ERROR_ANTI_BOT, cooldown=True)


def test_five_consecutive_failures_trip_auto_disable_once():
    state = sr.ResilienceState()
    tripped_at = []
    for i in range(1, 7):
        state, tripped = sr.state_after_failure(state, NET, NOW, threshold=5)
        assert state.consecutive_failures == i
        if tripped:
            tripped_at.append(i)
    assert tripped_at == [5]
    assert state.auto_disabled is True
    assert state.auto_disabled_at == NOW
    assert state.last_error_class == "network"


def test_success_resets_streak_before_threshold():
    state = sr.ResilienceState()
    for _ in range(4):
        state, _ = sr.state_after_failure(state, NET, NOW, threshold=5)
    state, reenabled = sr.state_after_success(state, 0, NOW)  # honest empty is not a failure
    assert state.consecutive_failures == 0 and not reenabled
    state, tripped = sr.state_after_failure(state, NET, NOW, threshold=5)
    assert state.consecutive_failures == 1 and not tripped


def test_cooldown_failures_do_not_count():
    state = sr.ResilienceState(consecutive_failures=4)
    state, tripped = sr.state_after_failure(state, COOL, NOW, threshold=5)
    assert state.consecutive_failures == 4 and not tripped and not state.auto_disabled


def test_exempt_scope_counts_but_never_disables():
    state = sr.ResilienceState()
    for _ in range(8):
        state, tripped = sr.state_after_failure(state, NET, NOW, threshold=5, exempt=True)
        assert not tripped
    assert state.consecutive_failures == 8 and state.auto_disabled is False


def test_gate_skips_until_canary_window_then_canary():
    disabled = sr.ResilienceState(consecutive_failures=5, auto_disabled=True, auto_disabled_at=NOW)
    interval = timedelta(hours=6)
    assert sr.gate_decision(sr.ResilienceState(), NOW) == sr.GATE_RUN
    assert sr.gate_decision(disabled, NOW + timedelta(hours=1), interval=interval) == sr.GATE_SKIP
    assert sr.gate_decision(disabled, NOW + timedelta(hours=6), interval=interval) == sr.GATE_CANARY
    assert sr.gate_decision(disabled, NOW, interval=interval, force=True) == sr.GATE_CANARY
    after_canary = sr.ResilienceState(
        consecutive_failures=6, auto_disabled=True, auto_disabled_at=NOW,
        last_canary_at=NOW + timedelta(hours=6),
    )
    assert sr.gate_decision(after_canary, NOW + timedelta(hours=7), interval=interval) == sr.GATE_SKIP


def test_canary_reenables_only_with_records():
    disabled = sr.ResilienceState(consecutive_failures=5, auto_disabled=True, auto_disabled_at=NOW)
    still, reenabled = sr.state_after_success(disabled, 0, NOW, was_canary=True)
    assert still.auto_disabled and not reenabled and still.last_canary_at == NOW
    cleared, reenabled = sr.state_after_success(disabled, 12, NOW, was_canary=True)
    assert reenabled and cleared.auto_disabled is False and cleared.consecutive_failures == 0


def test_failed_canary_stays_disabled_and_records_canary_time():
    disabled = sr.ResilienceState(consecutive_failures=5, auto_disabled=True, auto_disabled_at=NOW)
    later = NOW + timedelta(hours=6)
    state, tripped = sr.state_after_failure(disabled, NET, later, threshold=5, was_canary=True)
    assert not tripped and state.auto_disabled and state.last_canary_at == later
    assert state.consecutive_failures == 6


def test_state_round_trips_and_tolerates_junk_docs():
    state = sr.ResilienceState(consecutive_failures=3, auto_disabled=True, auto_disabled_at=NOW,
                               last_error_class="anti_bot")
    assert sr.ResilienceState.from_doc(state.to_fields()) == state
    assert sr.ResilienceState.from_doc(None) == sr.ResilienceState()
    assert sr.ResilienceState.from_doc({"auto_disabled": "yes", "consecutive_failures": "7"}) == sr.ResilienceState()

    class NotADict:
        def get(self, *_):
            return object()

    assert sr.ResilienceState.from_doc(NotADict()) == sr.ResilienceState()


def test_threshold_default_is_five(monkeypatch):
    monkeypatch.delenv("SCRAPER_AUTO_DISABLE_THRESHOLD", raising=False)
    assert sr.auto_disable_threshold() == 5


# ── Schema drift ───────────────────────────────────────────────────────────
class _Row:
    def __init__(self, name="A B"):
        self.Full_Name = name


def test_drift_total_when_every_row_lost_its_key():
    finding = sr.assess_schema_drift(40, [])
    assert finding and finding.severity == "total"


def test_drift_partial_when_most_rows_lost_key_or_name():
    assert sr.assess_schema_drift(20, [_Row()] * 5).severity == "partial"
    assert sr.assess_schema_drift(12, [_Row("")] * 12).severity == "partial"


def test_no_drift_for_empty_or_healthy_results():
    assert sr.assess_schema_drift(0, []) is None
    assert sr.assess_schema_drift(30, [_Row()] * 29) is None
    assert sr.assess_schema_drift(3, [_Row()]) is None  # too small to judge partial


def test_alert_throttle_suppresses_repeats_within_window():
    clock = {"t": 0.0}
    throttle = sr.AlertThrottle(window_s=60, clock=lambda: clock["t"])
    assert throttle.allow(("X (FL)", "partial"))
    assert not throttle.allow(("X (FL)", "partial"))
    assert throttle.allow(("X (FL)", "total"))
    clock["t"] = 61
    assert throttle.allow(("X (FL)", "partial"))


# ── Obscura routing policy ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    "label",
    ["Hampton (SC)", "Marlboro (SC)", "Richland (SC)", "Sumter (SC)", "Sarasota (FL)", "Baker (FL)", "St. Clair (AL)"],
)
def test_obscura_never_routes_holds_even_when_opted_in(label):
    allowed, _ = sr.obscura_route_decision(
        label, source_contract_validated=True, source_state="verified_public", opt_in=frozenset({label})
    )
    assert allowed is False
    assert sr.obscura_hard_refusal(label, source_contract_validated=True, source_state="verified_public")


def test_obscura_refuses_fail_closed_and_unverified():
    assert sr.obscura_route_decision(
        "X (TN)", source_contract_validated=False, source_state="unverified", opt_in=frozenset({"X (TN)"})
    )[0] is False
    assert sr.obscura_route_decision(
        "X (TN)", source_contract_validated=True, source_state="fail_closed", opt_in=frozenset({"X (TN)"})
    )[0] is False
    assert sr.obscura_route_decision(
        "X (TN)", source_contract_validated=True, source_state="unverified", opt_in=frozenset({"X (TN)"})
    )[0] is False


def test_obscura_requires_verified_public_and_explicit_opt_in():
    assert sr.obscura_route_decision(
        "Charleston (SC)", source_contract_validated=True, source_state="verified_public", opt_in=frozenset()
    )[0] is False
    assert sr.obscura_route_decision(
        "Charleston (SC)", source_contract_validated=True, source_state="verified_public",
        opt_in=frozenset({"Charleston (SC)"}),
    )[0] is True


def test_obscura_opt_in_env_default_is_empty(monkeypatch):
    monkeypatch.delenv(sr.OBSCURA_ROUTE_ENV, raising=False)
    assert sr.obscura_opt_in_labels() == frozenset()
    assert sr.obscura_opt_in_labels(" Aiken (SC) , Charleston (SC),, ") == frozenset({"Aiken (SC)", "Charleston (SC)"})


def test_auto_disable_exempt_labels_mirror_key_fl_counties():
    tree = ast.parse((ROOT / "dashboard" / "extensions.py").read_text())
    key = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "KEY_FL_COUNTIES" for t in node.targets):
            key = ast.literal_eval(node.value)
    assert key is not None
    assert sr.AUTO_DISABLE_EXEMPT_LABELS == frozenset(f"{c} (FL)" for c in key)
