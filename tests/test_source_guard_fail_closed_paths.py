"""Every refetch / recheck / ingest / trigger path honours fail_closed before
any request (Codex P1 on #174, charlotte.py:93).

The network is mocked to fail on any call (socket, requests, httpx, urllib).
Each path is run for Charlotte (FL), Manatee (FL), a generic Health
fail_closed county (Levy (FL)) and a generic scraper with
SOURCE_CONTRACT_VALIDATED=False, and must make zero requests. A positive
control shows the blocker does see a request from an open county.
Synthetic data only.
"""
from __future__ import annotations

import asyncio
import socket
import urllib.request
from types import SimpleNamespace

import pytest

from config import source_guard
from config.source_guard import (
    FAIL_CLOSED,
    HEALTH_UNREADABLE,
    SOURCE_CONTRACT_UNVALIDATED,
    SourceFailClosed,
    assert_source_allowed,
    county_label,
    fail_closed_reason,
    label_for_url,
)

CHARLOTTE_URL = "https://inmates.charlottecountyfl.revize.com/bookings/SYN-0001"
MANATEE_URL = "https://manatee-sheriff.revize.com/bookings/SYN-0002"
LEVY_URL = "https://jail.example.test/booking/SYN-0003"
SYN_URL = "https://jail.example.test/booking/SYN-0004"
OPEN_URL = "https://open-county.example.test/booking/SYN-0005"

# (county, state, detail_url, scraper flag)
CLOSED = [
    pytest.param("Charlotte", "FL", CHARLOTTE_URL, False, id="charlotte"),
    pytest.param("Manatee", "FL", MANATEE_URL, False, id="manatee"),
    pytest.param("Levy", "FL", LEVY_URL, True, id="generic_health_fail_closed"),
    pytest.param("Synthetic", "FL", SYN_URL, False, id="generic_flag_unvalidated"),
]


@pytest.fixture
def net(monkeypatch):
    """Record and refuse every network call."""
    calls: list = []

    def boom(*a, **k):
        calls.append("net")
        raise ConnectionRefusedError("network blocked in test")

    async def aboom(*a, **k):
        boom()

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket.socket, "connect_ex", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    import requests

    monkeypatch.setattr(requests.Session, "request", boom)
    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests, "post", boom)
    import httpx

    monkeypatch.setattr(httpx.Client, "send", boom)
    monkeypatch.setattr(httpx.AsyncClient, "send", aboom)
    return calls


class _Scraper:
    def __init__(self, county, state="FL", validated=True):
        self.county, self.state = county, state
        self.county_label = f"{county} ({state})"
        self.scraper_id = f"scraper_{county.lower()}"
        self.SOURCE_CONTRACT_VALIDATED = validated
        self.calls: list = []

    def _fetch_single_booking(self, booking_id, detail_url=""):
        self.calls.append(("single", booking_id))
        import requests

        requests.get(detail_url, timeout=1)

    def fetch_bond_recheck(self, booking_id, detail_url=""):
        self.calls.append(("recheck", booking_id))
        import requests

        requests.get(detail_url, timeout=1)

    def run(self, writers=None, force_canary=False):
        self.calls.append(("run", None))
        return {}

    def health_check(self):
        return {}


def _doc(county, state, url, bk="SYN-1"):
    return {
        "county": county, "state": state, "booking_number": bk, "detail_url": url,
        "full_name": "SYNTHETIC DEFENDANT", "bond_amount": 0.0, "status": "In Custody",
    }


# ── Shared guard ─────────────────────────────────────────────────────────────
def test_guard_labels_and_hosts():
    assert county_label("Charlotte") == "Charlotte (FL)"
    assert county_label("Charlotte County", "fl") == "Charlotte (FL)"
    assert county_label("Lee (AL)") == "Lee (AL)"
    assert county_label("") == ""
    assert label_for_url(CHARLOTTE_URL) == "Charlotte (FL)"
    assert label_for_url("https://www.ccso.org/x") == "Charlotte (FL)"
    assert label_for_url(MANATEE_URL) == "Manatee (FL)"
    assert label_for_url("www.manateesheriff.com/inmates") == "Manatee (FL)"
    assert label_for_url("https://notccso.org.example.test/") == ""
    assert label_for_url("") == ""


def test_guard_reasons():
    for county in ("Charlotte", "Manatee", "Levy"):
        assert fail_closed_reason(county, "FL") == FAIL_CLOSED
    assert fail_closed_reason(url=CHARLOTTE_URL) == FAIL_CLOSED
    assert fail_closed_reason(url=MANATEE_URL) == FAIL_CLOSED
    # Charlotte NC (Mecklenburg) is a different county.
    assert fail_closed_reason("Charlotte", "NC") is None
    assert fail_closed_reason(scraper=_Scraper("Synthetic", validated=False)) == SOURCE_CONTRACT_UNVALIDATED
    assert fail_closed_reason(scraper=_Scraper("Synthetic")) is None
    assert fail_closed_reason("Synthetic", "FL", url=OPEN_URL) is None
    # Any one closed county closes the path (open county, closed host).
    assert fail_closed_reason("Synthetic", "FL", url=MANATEE_URL) == FAIL_CLOSED
    with pytest.raises(SourceFailClosed):
        assert_source_allowed("Charlotte", "FL")
    assert_source_allowed("Synthetic", "FL")


def test_real_charlotte_and_manatee_classes_are_closed():
    from scrapers.counties.charlotte import CharlotteCountyScraper
    from scrapers.counties.manatee import ManateeCountyScraper

    for cls in (CharlotteCountyScraper, ManateeCountyScraper):
        assert cls.SOURCE_CONTRACT_VALIDATED is False
    stub = SimpleNamespace(county="Charlotte", state="FL", SOURCE_CONTRACT_VALIDATED=False)
    assert fail_closed_reason(scraper=stub) == FAIL_CLOSED


def test_guard_unreadable_health(monkeypatch):
    from dashboard import extensions

    def boom(label):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(extensions, "scraper_source_state", boom)
    assert fail_closed_reason("Synthetic", "FL") == HEALTH_UNREADABLE
    assert fail_closed_reason("Synthetic", "FL", unknown_closed=False) is None
    # Flag still closes when Health can't be read.
    assert fail_closed_reason(scraper=_Scraper("Synthetic", validated=False), unknown_closed=False) == SOURCE_CONTRACT_UNVALIDATED


# ── FirstAppearanceWatcher ───────────────────────────────────────────────────
def _watcher(scrapers):
    from core.first_appearance_watcher import FirstAppearanceWatcher

    w = FirstAppearanceWatcher.__new__(FirstAppearanceWatcher)
    w._scrapers = scrapers
    return w


@pytest.mark.parametrize("county,state,url,flag", CLOSED)
def test_watcher_refetch_makes_no_request(net, county, state, url, flag):
    scraper = _Scraper(county, state, validated=flag)
    w = _watcher({county: scraper})
    doc = _doc(county, state, url)
    assert w._refetch_record(doc) is None
    assert scraper.calls == [] and net == []
    # No registered scraper: the generic requests.get fallback is also closed
    # (for the flag-only county there is nothing else to close it, so only
    # the Health / host counties apply here).
    if flag:
        w2 = _watcher({})
        assert w2._refetch_record(doc) is None
        assert w2._generic_refetch(doc, url) is None
        assert net == []
    assert w._generic_refetch(doc, url) is None
    assert net == []


def test_watcher_generic_refetch_closed_by_host_alone(net):
    w = _watcher({})
    for url in (CHARLOTTE_URL, MANATEE_URL, "https://www.ccso.org/booking/SYN"):
        doc = _doc("", "", url)
        assert w._refetch_record(doc) is None
        assert w._generic_refetch(doc, url) is None
    assert net == []


def test_watcher_open_county_control_reaches_network(net):
    w = _watcher({})
    doc = _doc("Synthetic", "FL", OPEN_URL)
    assert w._generic_refetch(doc, OPEN_URL) is None
    assert net, "blocker must see a request from an open county"


# ── Pending bond recheck ─────────────────────────────────────────────────────
@pytest.mark.parametrize("county,state,url,flag", CLOSED)
def test_pending_bond_recheck_makes_no_request(net, county, state, url, flag):
    from core.pending_bond_recheck import PendingBondRecheck, county_exclusion

    scraper = _Scraper(county, state, validated=flag)
    for relay in (False, True):
        assert county_exclusion(scraper, include_relay_only=relay) in (FAIL_CLOSED, SOURCE_CONTRACT_UNVALIDATED)
    stats = {"checked": 0}
    PendingBondRecheck._recheck_one(SimpleNamespace(), scraper, f"{county} ({state})",
                                    _doc(county, state, url), None, stats, {"checked": 0})
    assert stats["checked"] == 0 and stats["skipped_fail_closed"] == 1
    assert scraper.calls == [] and net == []


def test_pending_bond_recheck_per_booking_host_backstop(net):
    from core.pending_bond_recheck import PendingBondRecheck

    scraper = _Scraper("Synthetic")
    stats = {"checked": 0}
    PendingBondRecheck._recheck_one(SimpleNamespace(), scraper, "Synthetic (FL)",
                                    _doc("Synthetic", "FL", MANATEE_URL), None, stats, {"checked": 0})
    assert stats["skipped_fail_closed"] == 1 and scraper.calls == [] and net == []


# ── Scheduler triggers (dashboard run-now / custody recheck) ─────────────────
class _Triggers:
    def __init__(self, docs):
        self.docs = docs
        self.sets = []

    def find(self, q):
        return [d for d in self.docs if d.get("status") == "pending"]

    def update_one(self, flt, upd):
        self.sets.append((flt, upd["$set"]))


def _poll(monkeypatch, scraper, docs):
    from core.scheduler import ScraperScheduler

    sched = ScraperScheduler(max_workers=1)
    sched.register_scraper(scraper)
    triggers = _Triggers(docs)
    db = {"scraper_triggers": triggers, "arrests": None, "custody_rechecks": None}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __getitem__(self, name):
            return db

        def close(self):
            pass

    import pymongo

    monkeypatch.setattr(pymongo, "MongoClient", _Client)
    sched._poll_triggers()
    return sched, triggers, db


@pytest.mark.parametrize("county,state,url,flag", CLOSED)
def test_scheduler_triggers_make_no_request(net, monkeypatch, county, state, url, flag):
    scraper = _Scraper(county, state, validated=flag)
    sched, triggers, db = _poll(monkeypatch, scraper, [
        {"_id": 1, "county": county, "status": "pending"},
        {"_id": 2, "county": county, "status": "pending", "type": "custody_recheck",
         "mode": "single", "booking_number": "SYN-1", "detail_url": url},
        {"_id": 3, "county": county, "status": "pending", "type": "health_check"},
    ])
    assert [s["status"] for _, s in triggers.sets] == ["fail_closed"] * 3
    assert scraper.calls == [] and net == []
    # Direct call to the custody handler is closed too.
    sched._handle_custody_recheck(db, {"_id": 9, "county": county, "mode": "single",
                                       "booking_number": "SYN-1", "detail_url": url}, scraper)
    assert triggers.sets[-1][1]["fail_closed"] is True
    assert scraper.calls == [] and net == []


def test_scheduler_open_county_still_runs(monkeypatch):
    scraper = _Scraper("Synthetic")
    _, triggers, _ = _poll(monkeypatch, scraper, [{"_id": 1, "county": "Synthetic", "status": "pending"}])
    assert scraper.calls == [("run", None)]
    assert triggers.sets[-1][1]["status"] == "done"


# ── URL ingest (detail enrich, legal NLP, Lee clerk watch, booking intake) ───
@pytest.mark.parametrize("county,state,url", [
    ("Charlotte", "FL", CHARLOTTE_URL),
    ("Manatee", "FL", MANATEE_URL),
    ("Levy", "FL", LEVY_URL),
])
def test_ingest_url_makes_no_request(net, county, state, url):
    from dashboard.services.url_ingest_service import ingest_url

    res = asyncio.run(ingest_url(url, county=county, state=state))
    assert res["success"] is False and res["fail_closed"] is True
    if county != "Levy":  # host alone is enough for Charlotte / Manatee
        res = asyncio.run(ingest_url(url))
        assert res["fail_closed"] is True
    for host_url in ("https://www.ccso.org/booking/SYN", "www.manateesheriff.com/booking/SYN"):
        assert asyncio.run(ingest_url(host_url))["fail_closed"] is True
    assert net == []


def test_ingest_url_open_control_reaches_network(net):
    from dashboard.services.url_ingest_service import _detect_county, ingest_url

    res = asyncio.run(ingest_url(OPEN_URL))
    assert res["success"] is False and not res.get("fail_closed")
    assert net
    assert _detect_county(CHARLOTTE_URL) == "Charlotte"
    assert _detect_county(MANATEE_URL) == "Manatee"


def test_lee_clerk_watch_refresh_passes_county(net):
    from dashboard.services import lee_clerk_watch

    out = asyncio.run(lee_clerk_watch._refresh_jail_source(
        {"booking_number": "SYN-1", "county": "Levy", "state": "FL", "detail_url": LEVY_URL}))
    assert out == {"attempted": True, "updated": False}
    assert net == []


# ── Dashboard manual triggers ────────────────────────────────────────────────
class _AsyncColl:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.writes: list = []

    async def find_one(self, q, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return d
        return None

    async def insert_one(self, doc):
        self.writes.append(("insert", doc))
        return SimpleNamespace(inserted_id="t1")

    async def update_one(self, flt, upd, upsert=False):
        self.writes.append(("update", flt))
        return SimpleNamespace(matched_count=1)


def _request(body):
    async def _json():
        return body

    return SimpleNamespace(json=_json)


@pytest.mark.parametrize("county,state,url", [
    ("Charlotte", "FL", CHARLOTTE_URL),
    ("Manatee", "FL", MANATEE_URL),
    ("Levy", "FL", LEVY_URL),
])
def test_refresh_from_source_makes_no_request_and_queues_nothing(net, monkeypatch, county, state, url):
    from dashboard.routers import legacy
    from dashboard.services import url_ingest_service

    cols = {"arrests": _AsyncColl([_doc(county, state, url, bk="SYN-9")]), "scraper_triggers": _AsyncColl()}
    monkeypatch.setattr(legacy, "get_collection", lambda n: cols.setdefault(n, _AsyncColl()))

    async def no_ingest(*a, **k):
        raise AssertionError("ingest_url must not be called")

    monkeypatch.setattr(url_ingest_service, "ingest_url", no_ingest)
    res = asyncio.run(legacy.refresh_from_source(_request({"booking_number": "SYN-9"})))
    assert res["fail_closed"] is True and res["trigger_id"] is None
    assert cols["scraper_triggers"].writes == [] and cols["arrests"].writes == []
    assert net == []


@pytest.mark.parametrize("county", ["Charlotte", "Manatee", "Levy"])
def test_dashboard_scraper_controls_queue_nothing(monkeypatch, county):
    from dashboard.routers import scraper_control as sc

    label = f"{county} (FL)"
    trig = _AsyncColl()
    monkeypatch.setattr(sc, "get_collection", lambda n: trig)
    monkeypatch.setattr(sc, "REGISTERED_COUNTIES", [label, "Synthetic (FL)"])

    for fn, body in (
        (sc.api_run_now, {"county": county, "state": "FL"}),
        (sc.api_custody_recheck, {"county": label}),
        (sc.api_custody_recheck, {"county": label, "booking_number": "SYN-1"}),
        (sc.api_scraper_health_check, {"county": label}),
    ):
        res = asyncio.run(fn(_request(body)))
        assert res.status_code == 409, fn.__name__
    assert trig.writes == []
    res = asyncio.run(sc.api_run_all())
    assert res["skipped_fail_closed"] == [label] and res["triggered"] == 1
    assert [w[1]["county"] for w in trig.writes] == [sc.registered_county_to_trigger_key("Synthetic (FL)")]


def test_watcher_candidate_query_excludes_fail_closed_counties(monkeypatch):
    from core.first_appearance_watcher import FirstAppearanceWatcher

    seen = {}

    class _Arrests:
        def find(self, query, *a, **k):
            seen["query"] = query
            return SimpleNamespace(sort=lambda *a, **k: SimpleNamespace(limit=lambda n: []))

    w = FirstAppearanceWatcher.__new__(FirstAppearanceWatcher)
    w._arrests = _Arrests()
    w._stored_fa_target_counties = lambda: ["Levy"]
    monkeypatch.delenv("WATCH_COUNTIES", raising=False)
    w._query_candidates()
    values = {str(v).casefold() for v in seen["query"]["county"]["$in"]}
    assert "lee" in values
    assert not any(v.startswith(("charlotte", "manatee", "levy", "sarasota")) for v in values)
