"""Pending-bond re-check worker (core/pending_bond_recheck.py), 2026-10-08.

A blank / "0" / no-bond bond on an in-custody booking is usually still pending.
The worker re-reads it daily on days 1-3 after booking, then weekly, through the
county's existing detail path, and stops on a published bond, a release or a
sentencing status. Writes go only through MongoWriter. Synthetic data only; no
network.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import core.pending_bond_recheck as pbr
from core.models import ArrestRecord
from core.pending_bond_recheck import (
    PendingBondRecheck,
    booking_day,
    county_exclusion,
    is_due,
    is_pending_bond,
    is_target,
    merge_with_stored,
    stop_reason_for,
)
from scrapers.counties import hernando
from tests.test_fl_hernando_detail_status import _Resp, _case, _detail
from tests.test_staff_edits_survive_rescrape import FakeArrests, _matches, _writer

NAME = "ZZSYNTH, TESTPERSON"
FIRST, LAST = "TESTPERSON", "ZZSYNTH"
NOW = datetime(2026, 10, 8, 16, 0, tzinfo=timezone.utc)  # 12:00 ET
TODAY = NOW.date()


# ── Fakes ──────────────────────────────────────────────────────────────────
class Arrests(FakeArrests):
    def find_one(self, query, projection=None):
        hits = self.find(query)
        return hits[0] if hits else None


class StateColl:
    def __init__(self):
        self.docs = []

    def find(self, query, projection=None):
        return [deepcopy(d) for d in self.docs if _matches(d, query)]

    def update_one(self, flt, update, upsert=False):
        doc = next((d for d in self.docs if _matches(d, flt)), None)
        if doc is None:
            doc = dict(flt)
            doc.update(update.get("$setOnInsert", {}))
            self.docs.append(doc)
        doc.update(update.get("$set", {}))
        for k, v in update.get("$inc", {}).items():
            doc[k] = doc.get(k, 0) + v

    def one(self, booking):
        return next((d for d in self.docs if d["booking_number"] == booking), None)


def _mongo(docs):
    arrests = Arrests(docs)
    w = _writer(arrests)
    states = StateColl()
    w.db = {"bond_rechecks": states}
    return w, arrests, states


class FakeScraper:
    SOURCE_CONTRACT_VALIDATED = True

    def __init__(self, county="Hernando", state="FL", results=None):
        self.county, self.state = county, state
        self.county_label = f"{county} ({state})"
        self.results = results or {}
        self.calls = []

    def fetch_bond_recheck(self, booking_id, detail_url=""):
        self.calls.append(booking_id)
        r = self.results.get(booking_id)
        return r(booking_id) if callable(r) else deepcopy(r)


def _stored(bk, *, county="Hernando", booked="10/06/2026", bond_raw="", bond=0.0, status="In Custody", **kw):
    doc = {
        "state": "FL", "county": county, "booking_number": bk, "full_name": NAME,
        "first_name": FIRST, "last_name": LAST, "dob": "01/01/1990", "booking_date": booked,
        "status": status, "charges": "843.02 - RESIST OFFICER", "bond_amount": bond,
        "bond_amount_raw": bond_raw, "bond_type": "", "lead_score": 42, "lead_status": "Cold",
        "detail_url": f"https://example.invalid/{bk}",
    }
    doc.update(kw)
    return doc


def _fetched(bk, *, bond="", status="In Custody", county="Hernando", extra=None, release="", bond_type=""):
    r = ArrestRecord(County=county, State="FL", Booking_Number=bk, Status=status, Release_Date=release,
                     Charges="843.02 - RESIST OFFICER", Bond_Amount=bond, Bond_Type=bond_type)
    r.extra_data = dict(extra) if extra is not None else {"bond_published": bond != ""}
    return r


class _NoScore:
    def score_and_update(self, record):
        return record


class _QuietNotifier:
    def notify_bond_set(self, record):
        return True

    def notify_hot_lead(self, record):
        return True


def _worker(scrapers, w, **kw):
    kw.setdefault("notifier", _QuietNotifier())
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("clock", lambda: NOW)
    kw.setdefault("scorer", _NoScore())
    return PendingBondRecheck(scrapers, [w], **kw)


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.delenv("PENDING_BOND_RECHECK_ENABLED", raising=False)


# ── Cadence ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "day, hours_ago, due",
    [
        (None, None, False),     # no usable booking date: never guessed
        (0, None, False),        # booked today: regular scrape / first-appearance watcher
        (1, None, True),
        (1, 10, False),
        (2, 21, True),
        (3, 21, True),
        (3, 19, False),
        (4, 48, False),          # weekly after day 3
        (4, 24 * 7, True),
        (30, 24 * 6, False),
        (30, 24 * 8, True),
    ],
)
def test_cadence_daily_days_1_to_3_then_weekly(day, hours_ago, due):
    last = None if hours_ago is None else NOW - timedelta(hours=hours_ago)
    assert is_due(day, last, NOW) is due


def test_booking_day_formats_and_fallback():
    assert booking_day({"booking_date": "10/05/2026"}, TODAY) == 3
    assert booking_day({"booking_date": "2026-10-07T23:10:00"}, TODAY) == 1
    assert booking_day({"booking_date": "10/08/2026 01:15"}, TODAY) == 0
    created = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)
    assert booking_day({"booking_date": "garbled", "created_at": created}, TODAY) == 7
    assert booking_day({"booking_date": ""}, TODAY) is None


# ── Pending detection and stop conditions ───────────────────────────────────
@pytest.mark.parametrize(
    "raw, extra, pending",
    [
        ("", None, True),
        ("0", None, True),                       # a 0 the parser can't vouch for stays pending
        ("$0.00", None, True),
        ("NO BOND", None, True),
        ("NOT SET", None, True),
        ("0", {"bond_published": True}, False),  # parser-marked real published 0
        ("1500", None, False),
    ],
)
def test_pending_bond_detection(raw, extra, pending):
    doc = {"bond_amount_raw": raw}
    if extra:
        doc["extra"] = extra
    assert is_pending_bond(doc) is pending


@pytest.mark.parametrize(
    "kw, reason",
    [
        ({"bond": "2500"}, "bond_published"),
        ({"bond": "0", "extra": {"bond_published": True}}, "bond_final_zero"),
        ({"bond": "0", "extra": {}}, None),
        ({"bond": ""}, None),
        ({"status": "Released", "release": "10/08/2026"}, "released"),
        ({"status": "SENTENCED"}, "sentenced"),
        ({"bond_type": "Serving Sentence"}, "sentenced"),
    ],
)
def test_stop_conditions(kw, reason):
    assert stop_reason_for(_fetched("HCSO26JBN000001", **kw)) == reason


@pytest.mark.parametrize(
    "doc, reason",
    [
        (_stored("B1"), None),
        (_stored("B1", bond_raw="0"), None),
        (_stored("B1", bond_raw="1500", bond=1500.0), "bond_known"),
        (_stored("B1", status="Released"), "not_in_custody"),
        (_stored("B1", status="Out of Custody"), "not_in_custody"),
        (_stored("B1", status="Sentenced"), "sentenced"),
        (_stored("", status="In Custody"), "no_booking_key"),
        (_stored("B1", bond_raw="0", staff_edits={"bond": {"amount": 0.0}}), "staff_bond"),
    ],
)
def test_target_selection(doc, reason):
    assert is_target(doc) == reason


# ── County eligibility: fail_closed / relay-only / no path ─────────────────
def test_fail_closed_relay_only_and_no_path_are_excluded(monkeypatch):
    from dashboard import extensions

    monkeypatch.setitem(extensions.SCRAPER_SOURCE_STATES, "Hernando (FL)", "fail_closed")
    assert county_exclusion(FakeScraper(), include_relay_only=False) == "fail_closed"
    monkeypatch.setitem(extensions.SCRAPER_SOURCE_STATES, "Hernando (FL)", "unverified")
    assert county_exclusion(FakeScraper(), include_relay_only=False) is None

    relay = FakeScraper(county="Manatee")
    assert county_exclusion(relay, include_relay_only=False) == "relay_only"
    assert county_exclusion(relay, include_relay_only=True) is None  # only when run on the relay

    unvalidated = FakeScraper()
    unvalidated.SOURCE_CONTRACT_VALIDATED = False
    assert county_exclusion(unvalidated, include_relay_only=False) == "source_contract_unvalidated"
    assert county_exclusion(SimpleNamespace(county="Lee", state="FL"), include_relay_only=False) == "no_recheck_path"

    disabled = FakeScraper()
    disabled._load_resilience_state = lambda writer: SimpleNamespace(auto_disabled=True)
    assert county_exclusion(disabled, include_relay_only=False, writer=object()) == "auto_disabled"


def test_excluded_counties_are_never_fetched(monkeypatch):
    from dashboard import extensions

    monkeypatch.setitem(extensions.SCRAPER_SOURCE_STATES, "Hernando (FL)", "fail_closed")
    w, arrests, states = _mongo([_stored("B1"), _stored("M1", county="Manatee")])
    closed = FakeScraper(results={"B1": _fetched("B1", bond="2500")})
    relay = FakeScraper(county="Manatee", results={"M1": _fetched("M1", county="Manatee", bond="2500")})
    stats = _worker([closed, relay], w).run()
    assert closed.calls == [] and relay.calls == []
    assert stats["excluded"] == {"Hernando (FL)": "fail_closed", "Manatee (FL)": "relay_only"}
    assert arrests.one("B1")["bond_amount_raw"] == "" and states.docs == []


def test_only_opted_in_scrapers_have_a_recheck_path():
    from scrapers.counties.collier import CollierCountyScraper
    from scrapers.counties.hernando import HernandoCountyScraper
    from scrapers.counties.indian_river import IndianRiverCountyScraper
    from scrapers.counties.lee import LeeCountyScraper

    assert callable(getattr(HernandoCountyScraper, "fetch_bond_recheck", None))
    assert callable(getattr(IndianRiverCountyScraper, "fetch_bond_recheck", None))
    # Single-booking paths that use stealth / impersonation / a proxy are not opted in.
    assert getattr(LeeCountyScraper, "fetch_bond_recheck", None) is None
    assert getattr(CollierCountyScraper, "fetch_bond_recheck", None) is None


# ── Worker runs ─────────────────────────────────────────────────────────────
def test_published_bond_written_through_mongo_writer_and_stops():
    w, arrests, states = _mongo([_stored("B1")])
    scraper = FakeScraper(results={"B1": _fetched("B1", bond="2500")})
    stats = _worker([scraper], w).run()
    doc = arrests.one("B1")
    assert (doc["bond_amount_raw"], doc["bond_amount"]) == ("2500", 2500.0)
    # Detail-only refresh never blanks stored person/booking fields.
    assert (doc["full_name"], doc["dob"], doc["booking_date"], doc["lead_score"]) == (NAME, "01/01/1990", "10/06/2026", 42)
    assert doc["last_checked_mode"] == "BOND_RECHECK"
    st = states.one("B1")
    assert (st["check_count"], st["stop_reason"], st["last_outcome"]) == (1, "bond_published", "refreshed")
    assert stats["stopped"] == {"bond_published": 1}
    # Stopped: never fetched again, even when it is due.
    later = _worker([scraper], w, clock=lambda: NOW + timedelta(days=8))
    later.run()
    assert scraper.calls == ["B1"]


def test_still_pending_zero_is_rechecked_on_cadence():
    w, arrests, states = _mongo([_stored("B1", bond_raw="0")])
    scraper = FakeScraper(results={"B1": _fetched("B1", bond="", extra={"bond_published": False})})
    _worker([scraper], w).run()
    _worker([scraper], w, clock=lambda: NOW + timedelta(hours=5)).run()   # same day: not due
    _worker([scraper], w, clock=lambda: NOW + timedelta(hours=21)).run()  # day 3: daily
    assert scraper.calls == ["B1", "B1"]
    st = states.one("B1")
    assert st["check_count"] == 2 and "stop_reason" not in st
    assert arrests.one("B1")["bond_amount_raw"] == ""  # unknown stays "", never "0"


def test_released_and_sentenced_stop():
    w, arrests, states = _mongo([_stored("R1"), _stored("S1")])
    scraper = FakeScraper(results={
        "R1": _fetched("R1", status="Released", release="10/08/2026"),
        "S1": _fetched("S1", status="SENTENCED"),
    })
    _worker([scraper], w).run()
    assert states.one("R1")["stop_reason"] == "released"
    assert states.one("S1")["stop_reason"] == "sentenced"
    assert arrests.one("R1")["status"] == "Released"


def test_no_result_writes_nothing_never_stops_and_is_never_released():
    stored = _stored("B1", bond_raw="0", booked="10/07/2026")  # days 1, 2, 3: daily
    w, arrests, states = _mongo([stored])
    before = deepcopy(arrests.one("B1"))
    scraper = FakeScraper(results={})
    for i in range(3):
        _worker([scraper], w, clock=lambda i=i: NOW + timedelta(hours=21 * i)).run()
    assert arrests.one("B1") == before and arrests.sets == []
    st = states.one("B1")
    # A None can be an outage as well as a missing booking: never a stop.
    assert st["no_result_streak"] == 3 and "stop_reason" not in st
    assert st["last_outcome"] == "no_result" and len(scraper.calls) == 3


def test_fetch_exception_writes_nothing_and_is_classified():
    w, arrests, states = _mongo([_stored("B1")])

    def boom(bk):
        raise TimeoutError("synthetic")

    stats = _worker([FakeScraper(results={"B1": boom})], w).run()
    st = states.one("B1")
    assert arrests.sets == [] and st["no_result_streak"] == 1 and "stop_reason" not in st
    assert st["last_outcome"] == "fetch_error:TimeoutError" and stats["errors"] == 1


def test_county_wide_no_result_is_flagged(caplog):
    w, _, _ = _mongo([_stored("B1"), _stored("B2")])
    with caplog.at_level(logging.WARNING):
        _worker([FakeScraper(results={})], w).run()
    assert any("no result for all 2 re-checks" in r.getMessage() for r in caplog.records)


def test_stop_follows_what_was_persisted_not_what_was_fetched():
    # Bond published but no charges: the writer keeps the stored charges/bond
    # pair, so the bond is still pending in Mongo and the re-check continues.
    w, arrests, states = _mongo([_stored("B1")])
    fetched = _fetched("B1", bond="5000")
    fetched.Charges = ""
    scored = []

    class Scorer:
        def score_and_update(self, record):
            scored.append(record.Bond_Amount)
            return record

    _worker([FakeScraper(results={"B1": fetched})], w, scorer=Scorer()).run()
    doc = arrests.one("B1")
    assert (doc["charges"], doc["bond_amount_raw"]) == ("843.02 - RESIST OFFICER", "")
    assert scored == [""]  # scored on the pair that is actually stored
    assert "stop_reason" not in states.one("B1")


def test_published_bond_sends_the_bond_set_alert():
    class Notifier:
        def __init__(self):
            self.calls = []

        def notify_bond_set(self, record):
            self.calls.append(("bond_set", record.Booking_Number, record.Bond_Amount))

        def notify_hot_lead(self, record):
            self.calls.append(("hot", record.Booking_Number))

    class HotScorer:
        def score_and_update(self, record):
            record.Lead_Score, record.Lead_Status = 90, "Hot"
            return record

    w, _, _ = _mongo([_stored("B1"), _stored("B2")])
    n = Notifier()
    scraper = FakeScraper(results={"B1": _fetched("B1", bond="5000"), "B2": _fetched("B2", bond="")})
    _worker([scraper], w, notifier=n, scorer=HotScorer()).run()
    assert n.calls == [("bond_set", "B1", "5000"), ("hot", "B1")]


def test_global_cap_skips_and_later_run_catches_up():
    docs = [_stored(f"B{i}") for i in range(5)]
    w, arrests, states = _mongo(docs)
    scraper = FakeScraper(results={f"B{i}": _fetched(f"B{i}", bond="") for i in range(5)})
    stats = _worker([scraper], w, max_per_run=2).run()
    assert len(scraper.calls) == 2 and stats["skipped_over_cap"] == 3
    _worker([scraper], w, max_per_run=2).run()
    _worker([scraper], w, max_per_run=2).run()
    assert sorted(scraper.calls) == [f"B{i}" for i in range(5)]  # each once; checked ones not yet due


def test_global_cap_spans_counties():
    w, arrests, states = _mongo([_stored("H1"), _stored("H2"), _stored("I1", county="Indian River")])
    h = FakeScraper(results={"H1": _fetched("H1"), "H2": _fetched("H2")})
    ir = FakeScraper(county="Indian River", results={"I1": _fetched("I1", county="Indian River")})
    _worker([h, ir], w, max_per_run=2).run()
    assert len(h.calls) == 2 and ir.calls == []


def test_per_county_cap_and_pacing_follow_the_county_module():
    class Capped(FakeScraper):
        MAX_DETAILS_PER_RUN = 1
        REQUEST_PAUSE_S = 2.5

    class Paced(FakeScraper):
        REQUEST_PAUSE_S = 0.1

    w, _, _ = _mongo([_stored("B1"), _stored("B2"), _stored("I1", county="Indian River"),
                      _stored("I2", county="Indian River")])
    capped = Capped(results={"B1": _fetched("B1"), "B2": _fetched("B2")})
    paced = Paced(county="Indian River", results={"I1": _fetched("I1", county="Indian River"),
                                                  "I2": _fetched("I2", county="Indian River")})
    sleeps = []
    stats = _worker([capped, paced], w, sleep=sleeps.append, default_pause_s=1.0).run()
    assert len(capped.calls) == 1 and stats["counties"]["Hernando (FL)"]["over_cap"] == 1
    assert len(paced.calls) == 2
    assert sleeps == [1.0]  # slower of module pause and worker default, between fetches only


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("PENDING_BOND_RECHECK_ENABLED", "0")
    w, _, _ = _mongo([_stored("B1")])
    scraper = FakeScraper(results={"B1": _fetched("B1", bond="2500")})
    assert _worker([scraper], w).run().get("disabled") is True and scraper.calls == []


def test_staff_bond_is_never_rechecked():
    w, arrests, _ = _mongo([_stored("B1", bond_raw="0", staff_edits={"bond": {"amount": 0.0}})])
    scraper = FakeScraper(results={"B1": _fetched("B1", bond="2500")})
    _worker([scraper], w).run()
    assert scraper.calls == [] and arrests.one("B1")["bond_amount_raw"] == "0"


def test_logs_carry_keys_and_field_names_only(caplog):
    w, _, _ = _mongo([_stored("B1"), _stored("B2"), _stored("B3")])
    scraper = FakeScraper(results={"B1": _fetched("B1", bond="2500"), "B2": None,
                                   "B3": _fetched("B3", status="Released", release="10/08/2026")})
    with caplog.at_level(logging.DEBUG):
        _worker([scraper], w).run()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "B1" in text and "bond_amount_raw" in text
    for needle in (NAME, FIRST, LAST, "01/01/1990"):
        assert needle not in text


def test_merge_keeps_stored_fields_and_manual_mode():
    stored = _stored("B1", last_checked_mode="MANUAL_CHARGE_BONDS", extra={"source_note": "x"})
    rec = merge_with_stored(_fetched("B1", bond="2500"), stored, NOW)
    assert (rec.Full_Name, rec.DOB, rec.Booking_Date, rec.Lead_Score) == (NAME, "01/01/1990", "10/06/2026", 42)
    assert rec.LastCheckedMode == "MANUAL_CHARGE_BONDS"
    assert rec.extra_data["source_note"] == "x" and rec.extra_data["bond_published"] is True
    assert (rec.Bond_Amount, rec.Charges) == ("2500", "843.02 - RESIST OFFICER")


# ── County hooks ────────────────────────────────────────────────────────────
BK = "HCSO26JBN009001"


def test_hernando_recheck_reads_the_existing_detail_page(monkeypatch):
    seen = []

    def get(url, **kw):
        seen.append(url)
        return _Resp(_detail(cases=[_case("26 CF 1", [("$0.00", "")])]))

    monkeypatch.setattr(hernando.requests, "get", get)
    rec = hernando.HernandoCountyScraper().fetch_bond_recheck(BK)
    assert seen == [f"{hernando.DETAIL_URL}?BookNo={BK}"]
    assert (rec.Booking_Number, rec.Status, rec.Bond_Amount) == (BK, "In Custody", "")  # $0.00 = unknown
    assert rec.Full_Name == "" and stop_reason_for(rec) is None

    monkeypatch.setattr(hernando.requests, "get", lambda url, **kw: _Resp(_detail(cases=[_case("26 CF 1", [("$0.00", "ROR")])])))
    assert stop_reason_for(hernando.HernandoCountyScraper().fetch_bond_recheck(BK)) == "bond_final_zero"

    monkeypatch.setattr(hernando.requests, "get", lambda url, **kw: _Resp(_detail(release="10/07/2026 14:05")))
    assert stop_reason_for(hernando.HernandoCountyScraper().fetch_bond_recheck(BK)) == "released"


@pytest.mark.parametrize(
    "page",
    [
        _Resp("<html>maintenance</html>"),          # drift
        _Resp(_detail(release="PENDING")),          # unreadable custody
        _Resp(_detail(cases=[])),                   # no charge grid
        _Resp("", status=503),                      # fetch failure
    ],
)
def test_hernando_recheck_returns_none_instead_of_blanks(monkeypatch, page):
    monkeypatch.setattr(hernando.requests, "get", lambda url, **kw: page)
    assert hernando.HernandoCountyScraper().fetch_bond_recheck(BK) is None


def test_hernando_recheck_requires_a_source_booking_key(monkeypatch):
    monkeypatch.setattr(hernando.requests, "get", lambda *a, **k: pytest.fail("no request expected"))
    assert hernando.HernandoCountyScraper().fetch_bond_recheck("DOE-JANE-1990") is None


# ── Scheduler registration ──────────────────────────────────────────────────
def test_job_registered_in_existing_scheduler(monkeypatch):
    import main
    from core.scheduler import ScraperScheduler

    sched = ScraperScheduler()
    assert main.register_pending_bond_recheck(sched) is True
    job = sched.scheduler.get_job("pending_bond_recheck")
    assert job is not None
    monkeypatch.setenv("PENDING_BOND_RECHECK_ENABLED", "0")
    sched2 = ScraperScheduler()
    assert main.register_pending_bond_recheck(sched2) is False
    assert sched2.scheduler.get_job("pending_bond_recheck") is None


def test_state_collection_is_classified():
    from dashboard.tenancy.constants import GLOBAL_COLLECTIONS, KNOWN_APP_COLLECTIONS

    assert pbr.STATE_COLLECTION in GLOBAL_COLLECTIONS and pbr.STATE_COLLECTION in KNOWN_APP_COLLECTIONS


# ── Source state unavailable fails closed (CoS follow-up, 2026-10-09) ─────
def test_source_state_import_failure_fails_closed(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def broken(name, *a, **k):
        if name == "dashboard.extensions":
            raise ImportError("synthetic")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", broken)
    assert pbr._source_state("Hernando (FL)") == "fail_closed"
    assert county_exclusion(FakeScraper(), include_relay_only=False) == "fail_closed"


def test_source_state_lookup_error_fails_closed_and_nothing_is_fetched(monkeypatch):
    from dashboard import extensions

    def boom(label):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(extensions, "scraper_source_state", boom)
    w, arrests, states = _mongo([_stored("B1")])
    scraper = FakeScraper(results={"B1": _fetched("B1", bond="2500")})
    stats = _worker([scraper], w).run()
    assert scraper.calls == [] and stats["excluded"] == {"Hernando (FL)": "fail_closed"}
    assert arrests.sets == [] and states.docs == []
