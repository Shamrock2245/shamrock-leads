"""Lee (FL) single-point-of-failure hardening.

Synthetic rows only: 7-digit booking numbers, no names or DOBs.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from core import lee_freshness as lf
from scrapers import lee_rate_limit as rl
from scrapers.counties import lee
from scrapers.counties.lee import LeeCountyScraper, LeeFetchIncomplete, LeeParseDriftError
from scrapers.scraper_resilience import ERROR_PARSE_DRIFT, ParseDriftError, classify_exception


def _row(bn, **extra):
    row = {"bookingNumber": str(bn), "id": "2026102300000" + str(bn)[-1], "permId": "999",
           "bookingDate": "2026-10-23T10:00:00", "inCustody": True}
    row.update(extra)
    return row


class _Resp:
    def __init__(self, payload=None, status=200, bad_json=False):
        self.status_code = status
        self.text = "" if payload is None else str(payload)
        self._payload = payload
        self._bad = bad_json
        self.headers = {"content-type": "application/json"}

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._payload


_REAL_OPS_ALERT = LeeCountyScraper._ops_alert


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    rl.reset_for_tests(state_path="")
    monkeypatch.setattr(lee.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(LeeCountyScraper, "_ops_alert", lambda self, *a, **k: self.__dict__.setdefault("_alerts", []).append(a[0]))
    monkeypatch.setattr(LeeCountyScraper, "_alert_parse_drift", lambda self, d, s: self.__dict__.setdefault("_drift", []).append(s))
    yield
    rl.reset_for_tests(state_path="")


def _scraper_with(responses, monkeypatch, enrich=False):
    s = LeeCountyScraper()
    queue = list(responses)
    calls = []

    def fake_fetch(url, params=None):
        calls.append(dict(params or {}))
        item = queue.pop(0) if queue else _Resp([])
        return item

    monkeypatch.setattr(s, "_http_fetch", fake_fetch)
    monkeypatch.setattr(s, "_cleanup", lambda: None)
    if not enrich:
        monkeypatch.setattr(lee, "MAX_ENRICH", 0)
    s._calls = calls
    return s


# ── typed errors instead of silent 0 rows ──

def test_unknown_json_shape_raises_parse_drift():
    with pytest.raises(LeeParseDriftError):
        LeeCountyScraper._extract_records({"message": "maintenance"})
    with pytest.raises(LeeParseDriftError):
        LeeCountyScraper._extract_records("[]")
    with pytest.raises(LeeParseDriftError):
        LeeCountyScraper._extract_records([1, 2])
    assert LeeCountyScraper._extract_records([]) == []
    assert LeeCountyScraper._extract_records({"data": [_row(1034500)]})[0]["bookingNumber"] == "1034500"


def test_parse_drift_classified_and_not_retried(monkeypatch):
    s = _scraper_with([_Resp({"unexpected": True})] * 6, monkeypatch)
    with pytest.raises(ParseDriftError):
        s.scrape()
    # One request: drift is not transient, so no outer retry burns quota.
    assert len(s._calls) == 1
    assert classify_exception(LeeParseDriftError("x")).error_class == ERROR_PARSE_DRIFT


def test_rows_without_published_booking_number_are_drift(monkeypatch):
    rows = [{"id": "20261023000001", "permId": "5"}, {"id": "20261023000002", "permId": "6"}]
    s = _scraper_with([_Resp(rows), _Resp(rows)], monkeypatch)
    with pytest.raises(LeeParseDriftError):
        s.scrape()


def test_partial_missing_keys_alert_but_keep_keyed_rows(monkeypatch):
    rows = [_row(1034500), {"id": "20261023000002"}]
    s = _scraper_with([_Resp(rows), _Resp(rows)], monkeypatch)
    out = s.scrape()
    assert [r.Booking_Number for r in out] == ["1034500"]
    assert s._drift == ["partial"]


# ── booking key: published bookingNumber only ──

def test_internal_id_is_never_a_booking_key():
    s = LeeCountyScraper()
    assert s._normalize_record({"id": "20261023000001", "permId": "77"}) is None
    assert s._normalize_record({"booking_id": "1034500"}) is None
    norm = s._normalize_record(_row(1034500))
    assert norm["booking_number"] == "1034500"
    assert norm["person_id"] == "999"


def test_dedupe_keys_on_booking_number_and_keeps_distinct_bookings(monkeypatch):
    # Same internal id on two different bookings must not merge them.
    a = _row(1034500, id="X")
    b = _row(1034501, id="X")
    s = _scraper_with([_Resp([a, b]), _Resp([a, b])], monkeypatch)
    out = s.scrape()
    assert sorted(r.Booking_Number for r in out) == ["1034500", "1034501"]


# ── bounded retries with backoff ──

def test_page_retry_recovers_transient_5xx(monkeypatch):
    rows = [_row(1034500)]
    s = _scraper_with([_Resp(status=503), _Resp(rows), _Resp(rows)], monkeypatch)
    out = s.scrape()
    assert [r.Booking_Number for r in out] == ["1034500"]
    assert s._variant_complete == [True, True]


def test_page_retry_never_retries_429(monkeypatch):
    s = _scraper_with([_Resp("Throttled 1 over (INTERVAL 12 HOUR | 480000)", status=429)], monkeypatch)
    with pytest.raises(RuntimeError):
        s.scrape()
    assert len(s._calls) == 1


def test_incomplete_listing_retries_then_keeps_rows_and_alerts(monkeypatch):
    full = [_row(1034000 + i) for i in range(lee.PAGE_SIZE)]
    # Each attempt: page 1 full, page 2 fails twice (with retry) for both variants.
    attempt = [_Resp(full), _Resp(status=502), _Resp(status=502),
               _Resp(full), _Resp(status=502), _Resp(status=502)]
    s = _scraper_with(attempt * (lee.RETRY_LIMIT + 1), monkeypatch)
    out = s.scrape()
    assert len(out) == lee.PAGE_SIZE
    assert "fetch_incomplete" in s._alerts
    # Outer retries ran: (RETRY_LIMIT + 1) attempts x 6 requests.
    assert len(s._calls) == 6 * (lee.RETRY_LIMIT + 1)


def test_incomplete_listing_raises_typed_error_before_final_attempt(monkeypatch):
    full = [_row(1034000 + i) for i in range(lee.PAGE_SIZE)]
    s = _scraper_with([_Resp(full), _Resp(status=502), _Resp(status=502),
                       _Resp(full), _Resp(status=502), _Resp(status=502)], monkeypatch)
    s._final_attempt = False
    with pytest.raises(LeeFetchIncomplete):
        s._scrape_once()


# ── pagination guards ──

def test_offset_ignored_stops_without_burning_pages(monkeypatch):
    full = [_row(1034000 + i) for i in range(lee.PAGE_SIZE)]
    s = _scraper_with([_Resp(full)] * 20, monkeypatch)
    rows = s._fetch_with_pagination({"inCustody": "true"})
    assert len(rows) == lee.PAGE_SIZE
    assert len(s._calls) == 2
    assert s._last_variant_complete is True
    assert s._fetch_truncated is False


def test_page_cap_with_full_last_page_flags_truncation(monkeypatch):
    monkeypatch.setattr(lee, "MAX_PAGES", 2)
    pages = [_Resp([_row(1030000 + p * 100 + i) for i in range(lee.PAGE_SIZE)]) for p in range(4)]
    s = _scraper_with(pages, monkeypatch)
    s.scrape()
    assert s._fetch_truncated is True
    assert "page_cap" in s._alerts


# ── window overlap guard ──

def test_window_gap_detects_non_overlap():
    prev = {"min_bn": 1034400, "max_bn": 1034450}
    assert lf.window_gap(prev, {"min_bn": 1034440, "max_bn": 1034514}) is None
    assert lf.window_gap(prev, {"min_bn": 1034451, "max_bn": 1034514}) is None
    gap = lf.window_gap(prev, {"min_bn": 1034490, "max_bn": 1034514})
    assert gap == {"prev_max_bn": 1034450, "cur_min_bn": 1034490, "unseen_numbers": 39}


def test_scraper_alerts_and_persists_window_gap(monkeypatch):
    s = _scraper_with([_Resp([_row(1034490), _row(1034514)])] * 2, monkeypatch)
    s._prev_window = {"min_bn": 1034400, "max_bn": 1034450}
    s.scrape()
    assert "window_gap" in s._alerts
    extra = s.status_extra_fields()
    assert extra["source_window_gap"]["unseen_numbers"] == 39
    assert extra["source_window"]["max_bn"] == 1034514
    # The window is committed only after BaseScraper persisted the run.
    assert s._prev_window["max_bn"] == 1034450
    s.on_status_persisted()
    assert s._prev_window["max_bn"] == 1034514
    # Next run overlaps: gap clears.
    s2_rows = [_Resp([_row(1034510), _row(1034520)])] * 2
    queue = list(s2_rows)
    monkeypatch.setattr(s, "_http_fetch", lambda url, params=None: queue.pop(0))
    s.scrape()
    assert s.status_extra_fields()["source_window_gap"] is None


def test_prev_window_loaded_from_status_writer():
    s = LeeCountyScraper()
    writer = MagicMock()
    writer.get_scraper_status_fields.return_value = {"source_window": {"min_bn": 1, "max_bn": 1034450}}
    s._run_status_writer = writer
    assert s._load_prev_window()["max_bn"] == 1034450


# ── freshness / heartbeat ──

NOW = datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc)


def test_n_default_is_75_minutes(monkeypatch):
    monkeypatch.delenv("LEE_FRESHNESS_MAX_AGE_MIN", raising=False)
    assert lf.max_age_minutes() == 75
    monkeypatch.setenv("LEE_FRESHNESS_MAX_AGE_MIN", "40")
    assert lf.max_age_minutes() == 40


def test_healthy_doc_has_no_findings():
    doc = {"status": "ok", "records": 20, "last_success_at": NOW - timedelta(minutes=31),
           "consecutive_failures": 0}
    assert lf.evaluate_freshness(doc, NOW, max_age_min=75) == []


def test_stale_when_no_success_in_n_minutes():
    doc = {"status": "error", "records": 0, "last_run": NOW - timedelta(minutes=5),
           "last_success_at": NOW - timedelta(minutes=80), "consecutive_failures": 1}
    codes = [f.code for f in lf.evaluate_freshness(doc, NOW, max_age_min=75)]
    assert codes == ["stale"]


def test_legacy_doc_without_last_success_uses_ok_last_run():
    doc = {"status": "ok", "records": 5, "last_run": NOW - timedelta(minutes=10)}
    assert lf.evaluate_freshness(doc, NOW, max_age_min=75) == []
    doc = {"status": "error", "records": 0, "last_run": NOW - timedelta(minutes=10)}
    assert [f.code for f in lf.evaluate_freshness(doc, NOW, max_age_min=75)] == ["stale"]


def test_zero_rows_while_site_lists_bookings():
    doc = {"status": "empty", "records": 0, "last_success_at": NOW - timedelta(minutes=10)}
    assert lf.evaluate_freshness(doc, NOW, max_age_min=75, site_rows=None) == []
    codes = [f.code for f in lf.evaluate_freshness(doc, NOW, max_age_min=75, site_rows=20)]
    assert codes == ["zero_rows_site_has_bookings"]


def test_repeated_errors_and_window_gap_findings():
    doc = {"status": "error", "records": 0, "last_success_at": NOW - timedelta(minutes=20),
           "consecutive_failures": 3, "error_class": "network",
           "source_window_gap": {"prev_max_bn": 10, "cur_min_bn": 20, "unseen_numbers": 9}}
    codes = sorted(f.code for f in lf.evaluate_freshness(doc, NOW, max_age_min=75, max_failures=3))
    assert codes == ["repeated_errors", "window_gap"]


def test_missing_doc_is_critical():
    assert [f.code for f in lf.evaluate_freshness(None, NOW)] == ["no_status"]


def test_alerter_dedupes_and_sends_recovery():
    notifier = MagicMock()
    alerter = lf.LeeFreshnessAlerter(notifier=notifier, realert_min=60)
    stale = [lf.Finding("stale", "critical", "Lee (FL): no successful run")]
    assert alerter.process(stale, NOW)["sent"] == ["stale"]
    assert alerter.process(stale, NOW + timedelta(minutes=5))["sent"] == []
    assert alerter.process(stale, NOW + timedelta(minutes=61))["sent"] == ["stale"]
    out = alerter.process([], NOW + timedelta(minutes=70))
    assert out["recovered"] == ["stale"]
    titles = [c.kwargs["title"] for c in notifier.notify_alert.call_args_list]
    assert titles[-1] == "Lee (FL) freshness recovered"
    for call in notifier.notify_alert.call_args_list:
        assert call.kwargs["source"] == "lee_freshness"


def test_probe_counts_keyed_rows_and_skips_during_cooldown():
    get = MagicMock(return_value=_Resp([_row(1034500), {"id": "20261023000001"}]))
    assert lf.probe_listing_count(get=get) == 1
    rl.record_429("unit-test", cooldown_s=60)
    get.reset_mock()
    assert lf.probe_listing_count(get=get) is None
    get.assert_not_called()


def test_probe_uses_honest_ua_and_ignores_env(monkeypatch):
    import requests
    captured = {}

    def fake_get(self, url, timeout=None):
        captured.update(ua=self.headers["User-Agent"], trust=self.trust_env, url=url)
        return _Resp([])

    monkeypatch.setattr(requests.Session, "get", fake_get)
    assert lf.probe_listing_count() == 0
    assert captured["ua"].startswith("ShamrockLeads/1.0")
    assert captured["trust"] is False
    assert captured["url"] == lf.LEE_LISTING_URL


class _Coll:
    def __init__(self, doc):
        self.doc = doc
        self.filters = []

    async def find_one(self, flt, proj=None):
        self.filters.append(flt)
        return self.doc


def test_run_check_probes_only_on_zero_rows():
    notifier = MagicMock()
    coll = _Coll({"status": "empty", "records": 0, "last_success_at": NOW - timedelta(minutes=5)})
    db = {"scraper_status": coll}
    probe = MagicMock(return_value=12)
    out = asyncio.run(lf.run_lee_freshness_check(
        db, now=NOW, alerter=lf.LeeFreshnessAlerter(notifier=notifier), probe=probe))
    assert out["findings"] == ["zero_rows_site_has_bookings"]
    assert out["site_rows"] == 12
    probe.assert_called_once()

    coll.doc = {"status": "ok", "records": 9, "last_success_at": NOW - timedelta(minutes=5)}
    probe.reset_mock()
    out = asyncio.run(lf.run_lee_freshness_check(
        db, now=NOW, alerter=lf.LeeFreshnessAlerter(notifier=notifier), probe=probe))
    assert out["findings"] == []
    probe.assert_not_called()


def test_lee_freshness_cron_registered():
    from dashboard.cron import CRON_REGISTRY
    from dashboard.routers.automation_control import ALL_SERVICE_KEYS
    from dashboard.services.automation_config import DEFAULT_CONFIG
    cron = next(c for c in CRON_REGISTRY if c.name == "lee_freshness")
    assert cron.interval == 300 and cron.default_enabled is True
    assert "lee_freshness" in ALL_SERVICE_KEYS
    assert DEFAULT_CONFIG["lee_freshness"]["enabled"] is True


# ── base scraper heartbeat fields ──

def test_base_scraper_persists_last_success_and_extra_fields(monkeypatch):
    s = _scraper_with([_Resp([_row(1034500)])] * 2, monkeypatch)
    writer = MagicMock()
    writer.write_records.return_value = {"new_records": 1, "duplicates_skipped": 0}
    writer.get_scraper_resilience.return_value = {}
    writer.get_scraper_status_fields.return_value = {}
    import scrapers.base_scraper as bs
    monkeypatch.setattr(bs, "_slack", MagicMock())
    s.run(writers=[writer])
    kwargs = writer.upsert_scraper_status.call_args.kwargs
    extra = kwargs["extra_fields"]
    assert kwargs["status"] == "ok"
    assert isinstance(extra["last_success_at"], datetime)
    assert isinstance(extra["last_records_at"], datetime)
    assert extra["source_window"]["max_bn"] == 1034500
    assert extra["source_window_gap"] is None


def test_base_scraper_failure_does_not_stamp_last_success(monkeypatch):
    s = _scraper_with([_Resp({"unexpected": 1})], monkeypatch)
    writer = MagicMock()
    writer.get_scraper_resilience.return_value = {}
    import scrapers.base_scraper as bs
    monkeypatch.setattr(bs, "_slack", MagicMock())
    s.run(writers=[writer])
    extra = writer.upsert_scraper_status.call_args.kwargs["extra_fields"]
    assert "last_success_at" not in extra
    assert extra["error_class"] == ERROR_PARSE_DRIFT


# ── scheduler: misfire grace + missed-run alert ──

def test_scheduler_misfire_grace_and_missed_alert(monkeypatch):
    from core.scheduler import ScraperScheduler
    sched = ScraperScheduler(max_workers=1)
    added = {}
    monkeypatch.setattr(sched.scheduler, "add_job", lambda *a, **k: added.update(k))
    sched.register_scraper(LeeCountyScraper(), interval_minutes=30,
                           misfire_grace_time=1500, alert_on_miss=True)
    assert added["misfire_grace_time"] == 1500
    job_id = added["id"]

    notifier = MagicMock()
    import writers.slack_notifier as sn
    monkeypatch.setattr(sn, "SlackNotifier", lambda: notifier)
    event = MagicMock(job_id=job_id)
    sched._on_job_missed(event)
    sched._on_job_missed(event)  # throttled
    assert notifier.notify_alert.call_count == 1
    assert "Lee (FL)" in notifier.notify_alert.call_args.kwargs["title"]

    other = MagicMock(job_id="scraper_other")
    sched._on_job_missed(other)
    assert notifier.notify_alert.call_count == 1


def test_main_registers_lee_with_grace_and_alert():
    import inspect
    import main
    src = inspect.getsource(main)
    assert "misfire_grace_time=25 * 60, alert_on_miss=True" in src


def test_failed_run_does_not_advance_window(monkeypatch):
    s = _scraper_with([_Resp([{"id": "1"}])] * 2, monkeypatch)
    s._prev_window = {"min_bn": 1034400, "max_bn": 1034450}
    with pytest.raises(LeeParseDriftError):
        s.scrape()
    assert s._prev_window["max_bn"] == 1034450


# ── Codex review follow-ups (PR #292) ──

def _status_run(monkeypatch, *, write_raises=False, upsert_raises=False):
    s = _scraper_with([_Resp([_row(1034500)])] * 2, monkeypatch)
    s._prev_window = {"min_bn": 1034400, "max_bn": 1034450}
    writer = MagicMock()
    if write_raises:
        writer.write_records.side_effect = RuntimeError("bulk write failed")
    else:
        writer.write_records.return_value = {"new_records": 1, "duplicates_skipped": 0}
    if upsert_raises:
        writer.upsert_scraper_status.side_effect = RuntimeError("upsert failed")
    writer.get_scraper_resilience.return_value = {}
    writer.get_scraper_status_fields.return_value = {}
    import scrapers.base_scraper as bs
    monkeypatch.setattr(bs, "_slack", MagicMock())
    s.run(writers=[writer])
    return s, writer


def test_failed_status_write_does_not_advance_heartbeat(monkeypatch):
    s, writer = _status_run(monkeypatch, write_raises=True)
    extra = writer.upsert_scraper_status.call_args.kwargs["extra_fields"]
    assert "last_success_at" not in extra
    assert "last_records_at" not in extra
    assert isinstance(extra["records_write_failed_at"], datetime)
    assert "source_window" not in extra
    assert s._prev_window["max_bn"] == 1034450


def test_failed_status_upsert_does_not_commit_window(monkeypatch):
    s, writer = _status_run(monkeypatch, upsert_raises=True)
    assert writer.upsert_scraper_status.called
    assert s._prev_window["max_bn"] == 1034450


def test_persisted_run_commits_window(monkeypatch):
    s, writer = _status_run(monkeypatch)
    extra = writer.upsert_scraper_status.call_args.kwargs["extra_fields"]
    assert isinstance(extra["last_success_at"], datetime)
    assert "records_write_failed_at" not in extra
    assert s._prev_window["max_bn"] == 1034500


def test_freshness_flags_records_write_failure():
    doc = {"status": "ok", "records": 5, "last_run": NOW - timedelta(minutes=2),
           "last_success_at": NOW - timedelta(minutes=32),
           "records_write_failed_at": NOW - timedelta(minutes=2)}
    assert [f.code for f in lf.evaluate_freshness(doc, NOW)] == ["records_write_failed"]
    # A later success clears it.
    doc["last_success_at"] = NOW - timedelta(minutes=1)
    assert lf.evaluate_freshness(doc, NOW) == []


def test_write_failure_disables_legacy_last_run_fallback():
    doc = {"status": "ok", "records": 5, "last_run": NOW - timedelta(minutes=2),
           "records_write_failed_at": NOW - timedelta(minutes=2)}
    codes = [f.code for f in lf.evaluate_freshness(doc, NOW)]
    assert "stale" in codes and "records_write_failed" in codes


def _patch_notifier(monkeypatch, notifier):
    import writers.slack_notifier as sn
    monkeypatch.setattr(sn, "SlackNotifier", lambda: notifier)


@pytest.mark.parametrize("failure", [False, RuntimeError("slack down")])
def test_ops_alert_not_throttled_when_notify_fails(monkeypatch, failure):
    notifier = MagicMock()
    if isinstance(failure, Exception):
        notifier.notify_alert.side_effect = [failure, True]
    else:
        notifier.notify_alert.side_effect = [False, True]
    _patch_notifier(monkeypatch, notifier)
    s = LeeCountyScraper()
    _REAL_OPS_ALERT(s, "page_cap", "Lee (FL) listing hit LEE_MAX_PAGES", "6 x 50 full")
    assert "page_cap" not in s._ops_alerted
    _REAL_OPS_ALERT(s, "page_cap", "Lee (FL) listing hit LEE_MAX_PAGES", "6 x 50 full")
    assert "page_cap" in s._ops_alerted
    _REAL_OPS_ALERT(s, "page_cap", "Lee (FL) listing hit LEE_MAX_PAGES", "6 x 50 full")
    assert notifier.notify_alert.call_count == 2  # third call throttled


@pytest.mark.parametrize("failure", [False, RuntimeError("slack down")])
def test_scheduler_miss_alert_retries_when_notify_fails(monkeypatch, failure):
    from core.scheduler import ScraperScheduler
    sched = ScraperScheduler(max_workers=1)
    added = {}
    monkeypatch.setattr(sched.scheduler, "add_job", lambda *a, **k: added.update(k))
    sched.register_scraper(LeeCountyScraper(), interval_minutes=30,
                           misfire_grace_time=1500, alert_on_miss=True)
    job_id = added["id"]
    notifier = MagicMock()
    notifier.notify_alert.side_effect = [failure if isinstance(failure, Exception) else False, True]
    _patch_notifier(monkeypatch, notifier)
    event = MagicMock(job_id=job_id)
    sched._on_job_missed(event)
    assert (job_id, "misfire") not in sched._miss_alerted_at
    sched._on_job_missed(event)  # retried, delivered
    assert (job_id, "misfire") in sched._miss_alerted_at
    sched._on_job_missed(event)  # now throttled
    assert notifier.notify_alert.call_count == 2


@pytest.mark.parametrize("failure", [False, RuntimeError("slack down")])
def test_freshness_alerter_retries_when_notify_fails(failure):
    notifier = MagicMock()
    notifier.notify_alert.side_effect = [failure if isinstance(failure, Exception) else False, True]
    alerter = lf.LeeFreshnessAlerter(notifier=notifier, realert_min=60)
    stale = [lf.Finding("stale", "critical", "Lee (FL): no successful run")]
    out = alerter.process(stale, NOW)
    assert out["sent"] == [] and out["failed"] == ["stale"]
    out = alerter.process(stale, NOW + timedelta(minutes=5))  # next tick retries
    assert out["sent"] == ["stale"]
    assert alerter.process(stale, NOW + timedelta(minutes=10))["sent"] == []
    assert notifier.notify_alert.call_count == 2


def test_freshness_recovery_retried_when_notify_fails():
    notifier = MagicMock()
    notifier.notify_alert.side_effect = [True, False, True]
    alerter = lf.LeeFreshnessAlerter(notifier=notifier, realert_min=60)
    alerter.process([lf.Finding("stale", "critical", "x")], NOW)
    assert alerter.process([], NOW + timedelta(minutes=5))["recovered"] == []
    assert alerter.process([], NOW + timedelta(minutes=10))["recovered"] == ["stale"]
    assert notifier.notify_alert.call_count == 3


def test_freshness_cron_does_not_block_event_loop():
    """Slack delivery blocks until the event loop has ticked: proves it ran off-loop.

    (The autouse fixture no-ops time.sleep, so this waits on an Event instead.)
    """
    import threading

    threads = []
    loop_ticked = threading.Event()

    def blocking_notify(**_kwargs):
        threads.append(threading.get_ident())
        # On the loop thread this would deadlock until the timeout.
        return loop_ticked.wait(timeout=3)

    notifier = MagicMock()
    notifier.notify_alert.side_effect = blocking_notify
    coll = _Coll({"status": "error", "records": 0, "last_success_at": NOW - timedelta(minutes=120)})

    async def main():
        ticks = 0
        done = asyncio.Event()

        async def ticker():
            nonlocal ticks
            while not done.is_set():
                ticks += 1
                if ticks >= 5:
                    loop_ticked.set()
                await asyncio.sleep(0.005)

        t = asyncio.create_task(ticker())
        out = await lf.run_lee_freshness_check(
            {"scraper_status": coll}, now=NOW,
            alerter=lf.LeeFreshnessAlerter(notifier=notifier))
        done.set()
        await t
        return out, ticks, threading.get_ident()

    out, ticks, loop_thread = asyncio.run(main())
    assert out["sent"] == ["stale"]  # delivered only because the loop kept ticking
    assert threads and all(tid != loop_thread for tid in threads)
    assert ticks >= 5


# ── scheduler: max-instances skip (previous Lee run still going) ──

def _lee_sched(monkeypatch):
    from core.scheduler import ScraperScheduler
    sched = ScraperScheduler(max_workers=1)
    added = {}
    monkeypatch.setattr(sched.scheduler, "add_job", lambda *a, **k: added.update(k))
    sched.register_scraper(LeeCountyScraper(), interval_minutes=30,
                           misfire_grace_time=1500, alert_on_miss=True)
    return sched, added["id"]


def _max_instances_event(job_id):
    from apscheduler.events import EVENT_JOB_MAX_INSTANCES
    return MagicMock(job_id=job_id, code=EVENT_JOB_MAX_INSTANCES)


def test_missed_listener_subscribed_to_max_instances():
    from apscheduler.events import EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED
    from core.scheduler import ScraperScheduler
    sched = ScraperScheduler(max_workers=1)
    masks = [mask for cb, mask in sched.scheduler._listeners if cb == sched._on_job_missed]
    assert masks and masks[0] & EVENT_JOB_MAX_INSTANCES and masks[0] & EVENT_JOB_MISSED


def test_max_instances_skip_fires_lee_alert(monkeypatch):
    sched, job_id = _lee_sched(monkeypatch)
    notifier = MagicMock()
    notifier.notify_alert.return_value = True
    _patch_notifier(monkeypatch, notifier)
    sched._on_job_missed(_max_instances_event(job_id))
    assert notifier.notify_alert.call_count == 1
    kw = notifier.notify_alert.call_args.kwargs
    assert "Lee (FL)" in kw["title"] and "previous run still running" in kw["title"]
    assert kw["details"] == {"job_id": job_id, "reason": "max_instances"}
    sched._on_job_missed(_max_instances_event(job_id))  # throttled
    assert notifier.notify_alert.call_count == 1
    # A misfire is a different reason: not hidden by the max-instances throttle.
    sched._on_job_missed(MagicMock(job_id=job_id))
    assert notifier.notify_alert.call_count == 2
    assert notifier.notify_alert.call_args.kwargs["details"]["reason"] == "misfire"


def test_max_instances_skip_fires_through_real_scheduler(monkeypatch):
    """APScheduler itself emits EVENT_JOB_MAX_INSTANCES and our listener alerts."""
    from apscheduler.events import JobSubmissionEvent, EVENT_JOB_MAX_INSTANCES
    sched, job_id = _lee_sched(monkeypatch)
    notifier = MagicMock()
    notifier.notify_alert.return_value = True
    _patch_notifier(monkeypatch, notifier)
    sched.scheduler._dispatch_event(
        JobSubmissionEvent(EVENT_JOB_MAX_INSTANCES, job_id, None, [NOW]))
    assert notifier.notify_alert.call_args.kwargs["details"]["reason"] == "max_instances"


@pytest.mark.parametrize("failure", [False, RuntimeError("slack down")])
def test_max_instances_alert_not_throttled_when_notify_fails(monkeypatch, failure):
    sched, job_id = _lee_sched(monkeypatch)
    notifier = MagicMock()
    notifier.notify_alert.side_effect = [failure if isinstance(failure, Exception) else False, True]
    _patch_notifier(monkeypatch, notifier)
    sched._on_job_missed(_max_instances_event(job_id))
    assert (job_id, "max_instances") not in sched._miss_alerted_at
    sched._on_job_missed(_max_instances_event(job_id))  # retried, delivered
    assert (job_id, "max_instances") in sched._miss_alerted_at
    sched._on_job_missed(_max_instances_event(job_id))  # now throttled
    assert notifier.notify_alert.call_count == 2


def test_max_instances_skip_ignored_for_non_lee_jobs(monkeypatch):
    sched, _job_id = _lee_sched(monkeypatch)
    notifier = MagicMock()
    _patch_notifier(monkeypatch, notifier)
    sched._on_job_missed(_max_instances_event("scraper_other"))
    assert notifier.notify_alert.call_count == 0
    assert sched._miss_alerted_at == {}
