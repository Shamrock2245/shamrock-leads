"""BaseScraper.run() self-healing integration: backoff, classification,
persisted consecutive-failure auto-disable, canary/manual re-enable, drift
alerts, and honest Health status. No network, no Mongo, no Slack."""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone

import pytest
import requests

from core.models import ArrestRecord
from scrapers import base_scraper as bs
from scrapers.scraper_resilience import ParseDriftError, SourceCooldownActive


class FakeSlack:
    def __init__(self):
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        if not name.startswith("notify_"):
            raise AttributeError(name)

        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return True

        return _record

    def names(self):
        return [c[0] for c in self.calls]


class FakeStatusWriter:
    """In-memory stand-in for MongoWriter's scraper_status surface."""

    def __init__(self):
        self.doc: dict = {}
        self.upserts: list[dict] = []

    def write_records(self, records, county):
        return {"new_records": len(records), "duplicates_skipped": 0,
                "qualified_records": 0, "total_records": len(records)}

    def upsert_scraper_status(self, county, **kwargs):
        extra = kwargs.pop("extra_fields", None) or {}
        self.upserts.append({"county": county, **kwargs, **extra})
        self.doc.update({k: v for k, v in kwargs.items()})
        self.doc.update(extra)

    def get_scraper_resilience(self, county, state=None):
        return dict(self.doc)

    def reenable_scraper(self, county, state=None, by="manual"):
        self.doc.update({"auto_disabled": False, "consecutive_failures": 0})
        return True


def _record(booking="B-1", name="Doe, Jane"):
    return ArrestRecord(County="Fixture", State="TN", Booking_Number=booking, Full_Name=name)


class ScriptedScraper(bs.BaseScraper):
    """scrape() pops the next outcome (exception → raise, list → return)."""

    label_county = "Fixture"
    label_state = "TN"

    def __init__(self, outcomes):
        super().__init__()
        self._outcomes = list(outcomes)
        self.scrape_calls = 0
        self.sleeps: list[float] = []
        self._retry_sleep = self.sleeps.append

    @property
    def county(self):
        return self.label_county

    @property
    def state(self):
        return self.label_state

    def scrape(self):
        self.scrape_calls += 1
        outcome = self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return list(outcome)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    slack = FakeSlack()
    monkeypatch.setattr(bs, "_slack", slack)
    monkeypatch.setattr(bs, "_error_tracker", None)
    monkeypatch.setattr(bs, "_dashboard_available", False)
    monkeypatch.setattr(bs, "_drift_alert_throttle", bs.AlertThrottle(window_s=1800))
    monkeypatch.setattr(bs.BaseScraper, "_check_disk_space", classmethod(lambda cls: {"ok": True}))
    monkeypatch.setattr(bs.BaseScraper, "_post_dashboard_event", lambda self, *a, **k: None)
    fake_rearrest = types.ModuleType("writers.rearrest_checker")

    class _NoRearrest:
        def __init__(self):
            raise RuntimeError("disabled in tests")

    fake_rearrest.RearrestChecker = _NoRearrest
    monkeypatch.setitem(sys.modules, "writers.rearrest_checker", fake_rearrest)
    for var in ("SCRAPER_AUTO_DISABLE_THRESHOLD", "SCRAPER_AUTO_DISABLE_CANARY_MINUTES",
                "SCRAPER_AUTO_DISABLE_ENABLED", "SCRAPER_BASE_RETRY_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    return slack


def test_transient_network_failure_retries_2_4_8_then_succeeds(_isolate):
    scraper = ScriptedScraper([requests.Timeout("timed out"), requests.ConnectionError("reset"),
                               _http_error(503), [_record()]])
    writer = FakeStatusWriter()
    result = scraper.run(writers=[writer])
    assert scraper.sleeps == [2.0, 4.0, 8.0]
    assert scraper.scrape_calls == 4
    assert result["status"] == "ok" and result["records_scraped"] == 1
    assert writer.doc["consecutive_failures"] == 0


def test_exhausted_retries_record_network_failure(_isolate):
    scraper = ScriptedScraper([requests.Timeout("timed out")])
    writer = FakeStatusWriter()
    result = scraper.run(writers=[writer])
    assert scraper.sleeps == [2.0, 4.0, 8.0] and scraper.scrape_calls == 4
    assert result["status"] == "error" and result["error_class"] == "network"
    assert writer.doc["consecutive_failures"] == 1
    assert writer.doc["last_error_class"] == "network"
    assert "notify_scraper_error" in _isolate.names()


def test_429_and_anti_bot_are_not_retried(_isolate):
    for exc, cls in ((_http_error(429), "anti_bot"), (_http_error(403), "anti_bot"), (_http_error(404), "url_changed")):
        scraper = ScriptedScraper([exc])
        result = scraper.run(writers=[FakeStatusWriter()])
        assert scraper.scrape_calls == 1 and scraper.sleeps == []
        assert result["error_class"] == cls


def test_active_cooldown_is_never_retried_or_counted(_isolate):
    class CoolingScraper(ScriptedScraper):
        def in_source_cooldown(self):
            return True

    writer = FakeStatusWriter()
    writer.doc["consecutive_failures"] = 4
    scraper = CoolingScraper([SourceCooldownActive("rate-limit cooldown (3600s remaining)")])
    result = scraper.run(writers=[writer])
    assert scraper.scrape_calls == 1 and scraper.sleeps == []
    assert result["status"] == "error" and result["error_class"] == "anti_bot"
    assert writer.doc["consecutive_failures"] == 4  # unchanged
    assert writer.doc["auto_disabled"] is False
    assert writer.doc["cooldown_active"] is True

    # A network error while a cooldown is active is not retried either.
    scraper = CoolingScraper([requests.Timeout("timed out")])
    scraper.run(writers=[FakeStatusWriter()])
    assert scraper.scrape_calls == 1 and scraper.sleeps == []


def test_lee_opts_out_of_base_retry_and_exposes_cooldown():
    from scrapers.counties.lee import LeeCountyScraper
    from scrapers import lee_rate_limit

    assert LeeCountyScraper.BASE_RETRY_ENABLED is False
    lee_rate_limit.reset_for_tests(state_path="")
    try:
        scraper = LeeCountyScraper()
        assert scraper.in_source_cooldown() is False
        lee_rate_limit.record_429("HTTP 429", cooldown_s=60)
        assert scraper.in_source_cooldown() is True
    finally:
        lee_rate_limit.reset_for_tests(state_path="")


def test_five_consecutive_failures_auto_disable_and_show_honestly(_isolate):
    writer = FakeStatusWriter()
    results = []
    for _ in range(5):
        scraper = ScriptedScraper([_http_error(403)])
        results.append(scraper.run(writers=[writer]))
    assert [r["status"] for r in results] == ["error"] * 4 + ["auto_disabled"]
    assert writer.doc["auto_disabled"] is True
    assert writer.doc["consecutive_failures"] == 5
    assert writer.doc["status"] == "auto_disabled"
    assert writer.doc["last_error_class"] == "anti_bot"
    assert _isolate.names().count("notify_scraper_auto_disabled") == 1

    # Next scheduled run is skipped before any source request.
    skipped = ScriptedScraper([AssertionError("must not scrape while auto-disabled")])
    out = skipped.run(writers=[writer])
    assert out["status"] == "auto_disabled" and skipped.scrape_calls == 0


def test_canary_after_window_reenables_on_records(_isolate):
    writer = FakeStatusWriter()
    writer.doc.update({
        "consecutive_failures": 5, "auto_disabled": True, "last_error_class": "network",
        "auto_disabled_at": datetime.now(timezone.utc) - timedelta(hours=7),
    })
    scraper = ScriptedScraper([[_record()]])
    result = scraper.run(writers=[writer])
    assert scraper.scrape_calls == 1
    assert result["status"] == "ok" and result.get("reenabled") is True
    assert writer.doc["auto_disabled"] is False and writer.doc["consecutive_failures"] == 0
    assert writer.doc["reenabled_by"] == "canary"
    assert "notify_scraper_reenabled" in _isolate.names()


def test_failed_canary_stays_disabled_quietly(_isolate):
    writer = FakeStatusWriter()
    writer.doc.update({
        "consecutive_failures": 5, "auto_disabled": True,
        "auto_disabled_at": datetime.now(timezone.utc) - timedelta(hours=7),
    })
    scraper = ScriptedScraper([_http_error(403)])
    result = scraper.run(writers=[writer])
    assert result["status"] == "auto_disabled"
    assert writer.doc["auto_disabled"] is True and writer.doc["last_canary_at"] is not None
    assert "notify_scraper_error" not in _isolate.names()
    # The canary window restarts: the immediate next run is skipped.
    again = ScriptedScraper([AssertionError("no scrape")])
    assert again.run(writers=[writer])["status"] == "auto_disabled" and again.scrape_calls == 0


def test_operator_run_now_forces_a_canary(_isolate):
    writer = FakeStatusWriter()
    writer.doc.update({"consecutive_failures": 5, "auto_disabled": True,
                       "auto_disabled_at": datetime.now(timezone.utc)})
    scraper = ScriptedScraper([[_record()]])
    result = scraper.run(writers=[writer], force_canary=True)
    assert scraper.scrape_calls == 1 and result.get("reenabled") is True


def test_manual_reenable_resumes_normal_runs(_isolate):
    writer = FakeStatusWriter()
    writer.doc.update({"consecutive_failures": 5, "auto_disabled": True,
                       "auto_disabled_at": datetime.now(timezone.utc)})
    assert writer.reenable_scraper("Fixture", "TN")
    scraper = ScriptedScraper([[_record()]])
    assert scraper.run(writers=[writer])["status"] == "ok" and scraper.scrape_calls == 1


def test_key_fl_county_is_never_auto_disabled_but_alerts(_isolate):
    class KeyFl(ScriptedScraper):
        label_county = "Sarasota"
        label_state = "FL"

    writer = FakeStatusWriter()
    for _ in range(6):
        result = KeyFl([_http_error(403)]).run(writers=[writer])
    assert result["status"] == "error" and writer.doc["auto_disabled"] is False
    assert writer.doc["consecutive_failures"] == 6
    exempt_alerts = [c for c in _isolate.calls if c[0] == "notify_scraper_auto_disabled"]
    assert len(exempt_alerts) == 1 and exempt_alerts[0][2].get("exempt") is True


def test_total_schema_drift_alerts_immediately_and_counts(_isolate):
    writer = FakeStatusWriter()
    scraper = ScriptedScraper([[_record(booking=""), _record(booking="")]])
    result = scraper.run(writers=[writer])
    assert result["status"] == "error" and result["error_class"] == "parse_drift"
    assert writer.doc["consecutive_failures"] == 1
    drift = [c for c in _isolate.calls if c[0] == "notify_parse_drift"]
    assert len(drift) == 1 and drift[0][1][0] == "Fixture (TN)"
    assert "notify_scraper_error" not in _isolate.names()


def test_partial_schema_drift_alerts_without_failing(_isolate):
    rows = [_record(booking=f"B{i}") for i in range(4)] + [_record(booking="") for _ in range(8)]
    writer = FakeStatusWriter()
    result = ScriptedScraper([rows]).run(writers=[writer])
    assert result["status"] == "ok" and result["records_scraped"] == 4
    assert [c[2].get("severity") for c in _isolate.calls if c[0] == "notify_parse_drift"] == ["partial"]


def test_parse_drift_exception_is_classified_and_alerted(_isolate):
    result = ScriptedScraper([ParseDriftError("roster table not found")]).run(writers=[FakeStatusWriter()])
    assert result["error_class"] == "parse_drift"
    assert "notify_parse_drift" in _isolate.names()


def test_kill_switches(monkeypatch, _isolate):
    monkeypatch.setenv("SCRAPER_BASE_RETRY_ENABLED", "false")
    scraper = ScriptedScraper([requests.Timeout("timed out")])
    scraper.run(writers=[FakeStatusWriter()])
    assert scraper.scrape_calls == 1 and scraper.sleeps == []

    monkeypatch.setenv("SCRAPER_AUTO_DISABLE_ENABLED", "false")
    writer = FakeStatusWriter()
    writer.doc.update({"consecutive_failures": 9, "auto_disabled": True,
                       "auto_disabled_at": datetime.now(timezone.utc)})
    runner = ScriptedScraper([[_record()]])
    assert runner.run(writers=[writer])["status"] == "ok" and runner.scrape_calls == 1
    assert writer.doc["auto_disabled"] is False


def test_in_memory_fallback_without_status_writer(_isolate):
    scraper = ScriptedScraper([_http_error(403)])
    for _ in range(5):
        result = scraper.run(writers=[])
    assert result["status"] == "auto_disabled"
    assert scraper.health_check()["auto_disabled"] is True


def test_health_status_mapping_is_honest_for_auto_disabled():
    from dashboard.routers.stats import _health_status_for_row

    now = datetime.now(timezone.utc)
    live = {"status": "auto_disabled", "records": 0, "auto_disabled": True}
    assert _health_status_for_row(enabled=True, live=live, last_run=now, latest=None, now=now)[0] == "auto_disabled"
    live_cleared = {"status": "auto_disabled", "records": 0, "auto_disabled": False}
    assert _health_status_for_row(enabled=True, live=live_cleared, last_run=now, latest=None, now=now)[0] == "error"


def test_obscura_guard_refuses_fail_closed_and_hold_scopes():
    from scrapers.counties_sc.hampton import HamptonScraper
    from scrapers.scraper_resilience import ObscuraRoutingRefused

    with pytest.raises(ObscuraRoutingRefused):
        HamptonScraper()._obscura_guard()

    class Guarded(ScriptedScraper):
        SOURCE_CONTRACT_VALIDATED = False

    with pytest.raises(ObscuraRoutingRefused):
        Guarded([[]])._obscura_guard()
    assert ScriptedScraper([[]]).obscura_route_enabled() is False


def test_slack_post_failure_never_logs_webhook_url(caplog, monkeypatch):
    from writers.slack_notifier import SlackNotifier

    secret = "https://hooks.slack.com/services/TEST/NOT/REAL-path-token"

    def boom(url, **kwargs):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr("writers.slack_notifier.requests.post", boom)
    notifier = SlackNotifier(webhook_errors=secret)
    with caplog.at_level("DEBUG"):
        assert notifier.notify_parse_drift("X (FL)", "drift") is False
    assert "hooks.slack.com" not in caplog.text and "REAL-path-token" not in caplog.text


def _http_error(code: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = code
    return requests.HTTPError(f"{code} Error", response=resp)
