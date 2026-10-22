"""Lee (FL) co-signer discovery. Fixtures are synthetic. No outreach."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.models import ArrestRecord
from dashboard.services.contact_discovery import (
    ANCHOR_WEIGHTS,
    confidence_for,
    make_candidate,
    public_request,
    purge_unreviewed_candidates,
    run_discovery,
    run_lee_discovery_batch,
)
from scrapers.scraper_resilience import EgressBlocked
from dashboard.services.contact_discovery import DiscoveryRateLimited as _RateLimited
from writers.indemnitor_discovery_hook import enqueue_lee_indemnitor_discovery as _enqueue


def _make_writer(bulk_result=None):
    with patch("writers.mongo_writer.MongoClient") as mock_client_cls, \
         patch("writers.mongo_writer.settings") as mock_settings:
        mock_settings.MONGODB_URI = "mongodb://mock"
        mock_settings.MONGODB_DB_NAME = "test"
        mock_db = MagicMock()
        mock_client_cls.return_value.__getitem__.return_value = mock_db
        from writers.mongo_writer import MongoWriter
        writer = MongoWriter(uri="mongodb://mock", db_name="test")
        if bulk_result is not None:
            writer.arrests.bulk_write = MagicMock(return_value=bulk_result)
        return writer


def _bulk_result(upserted_indexes, modified=0):
    res = MagicMock()
    res.upserted_count = len(upserted_indexes)
    res.modified_count = modified
    res.bulk_api_result = {"upserted": [{"index": i, "_id": f"id{i}"} for i in upserted_indexes]}
    return res


class _Result:
    def __init__(self, modified):
        self.modified_count = modified
        self.matched_count = modified


def _values(doc, key):
    curs = [doc]
    for part in str(key).split("."):
        nxt = []
        for cur in curs:
            if isinstance(cur, list):
                for item in cur:
                    if isinstance(item, dict) and part in item:
                        nxt.append(item[part])
            elif isinstance(cur, dict) and part in cur:
                nxt.append(cur[part])
        curs = nxt
    return curs


def _set_path(doc, key, value):
    parts = str(key).split(".")
    cur = doc
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _unset_path(doc, key):
    parts = str(key).split(".")
    cur = doc
    for part in parts[:-1]:
        cur = cur.get(part)
        if not isinstance(cur, dict):
            return
    cur.pop(parts[-1], None)


def _match(doc, query):
    if not query:
        return True
    if "$and" in query:
        return all(_match(doc, part) for part in query["$and"])
    if "$or" in query:
        return any(_match(doc, part) for part in query["$or"])
    for key, expected in query.items():
        if key.startswith("$"):
            continue
        values = _values(doc, key)
        present = bool(values)
        if isinstance(expected, dict):
            if "$in" in expected:
                allowed = expected["$in"]
                hit = False
                for value in values:
                    if isinstance(value, list) and any(item in allowed for item in value):
                        hit = True
                    elif value in allowed:
                        hit = True
                if not hit:
                    return False
            if "$ne" in expected and any(value == expected["$ne"] for value in values):
                return False
            if "$exists" in expected and bool(expected["$exists"]) != present:
                return False
            if "$gte" in expected and not any(value is not None and value >= expected["$gte"] for value in values):
                return False
            if "$lte" in expected and not any(value is not None and value <= expected["$lte"] for value in values):
                return False
            if "$regex" in expected:
                import re
                flags = re.I if "i" in str(expected.get("$options") or "") else 0
                if not any(re.search(expected["$regex"], str(value), flags) for value in values):
                    return False
            continue
        if expected not in values and not any(isinstance(value, list) and expected in value for value in values):
            return False
    return True


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def limit(self, count):
        self._rows = self._rows[:count]
        return self

    def sort(self, *args, **kwargs):
        return self

    async def to_list(self, length=None):
        if length is None:
            return list(self._rows)
        return list(self._rows[:length])


class AsyncCollection:
    def __init__(self):
        self.docs = []

    def find(self, query=None, projection=None):
        return _Cursor([doc for doc in self.docs if _match(doc, query or {})])

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if _match(doc, query or {}):
                return doc
        return None

    async def update_one(self, filt, update, upsert=False):
        for doc in self.docs:
            if _match(doc, filt or {}):
                self._apply(doc, update)
                return _Result(1)
        if not upsert:
            return _Result(0)
        doc = {"_id": f"id{len(self.docs)}"}
        for key, value in (filt or {}).items():
            if not str(key).startswith("$") and not isinstance(value, dict):
                _set_path(doc, key, value)
        self._apply(doc, update)
        self.docs.append(doc)
        return _Result(1)

    @staticmethod
    def _apply(doc, update):
        for key, value in (update.get("$set") or {}).items():
            _set_path(doc, key, value)
        for key in (update.get("$unset") or {}):
            _unset_path(doc, key)


class AsyncDB:
    def __init__(self):
        self._cols = {}

    def __getitem__(self, name):
        self._cols.setdefault(name, AsyncCollection())
        return self._cols[name]


class SyncCollection:
    def __init__(self):
        self.updates = []

    def update_one(self, filt, update, upsert=False):
        self.updates.append((filt, update))


class SyncDB:
    def __init__(self):
        self._cols = {}

    def __getitem__(self, name):
        self._cols.setdefault(name, SyncCollection())
        return self._cols[name]


NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def _booking():
    return {
        "_id": "arrest-1",
        "booking_number": "B-100",
        "county": "Lee",
        "state": "FL",
        "full_name": "Casey Sample",
        "dob": "1990-01-02",
        "address": "100 Test St",
        "zip": "00000",
        "first_seen_at": NOW,
        "indemnitor_discovery": {"status": "queued", "updated_at": NOW},
    }


def test_confidence_is_the_anchor_weight_sum():
    assert make_candidate(
        booking_number="B-100",
        name="Alex Sample",
        anchors=[],
        source="prior_shamrock",
        relationship="prior_indemnitor",
    ) is None
    phone = make_candidate(
        booking_number="B-100",
        name="Alex Sample",
        anchors=["phone", "phone"],
        source="prior_shamrock",
        relationship="prior_indemnitor",
        defendant_name="Casey Sample",
    )
    assert phone["matched_anchors"] == ["phone"]
    assert phone["confidence"] == ANCHOR_WEIGHTS["phone"]
    both = confidence_for(["dob", "address_zip"])
    assert both == round(ANCHOR_WEIGHTS["dob"] + ANCHOR_WEIGHTS["address_zip"], 2)
    assert confidence_for(["phone", "dob", "address_zip"]) == 0.99


@pytest.mark.asyncio
async def test_name_alone_is_dropped_and_anchors_are_stored():
    db = AsyncDB()
    db["arrests"].docs.append(_booking())
    db["indemnitors"].docs.extend([
        {
            "_id": "name-only",
            "defendant_name": "Casey Sample",
            "name": "Alex Sample",
        },
        {
            "_id": "same-case",
            "booking_number": "B-100",
            "indemnitor_name": "Jordan Sample",
            "indemnitor_phone": "5550100100",
            "relationship": "sibling",
        },
        {
            "_id": "dob-link",
            "defendant_name": "Sample, Casey",
            "defendant_dob": "01/02/1990",
            "indemnitor_name": "Riley Sample",
            "indemnitor_phone": "5550100101",
        },
    ])
    db["arrests"].docs.append({
        "_id": "arrest-2",
        "booking_number": "B-200",
        "full_name": "Jordan Household",
        "address": "100 Test St",
        "zip": "00000",
    })

    result = await run_discovery(db, booking_number="B-100", force=True, arrest=_booking())
    by_name = {row["name"]: row for row in result["contacts"]}
    assert "Alex Sample" not in by_name
    assert by_name["Jordan Sample"]["matched_anchors"] == ["phone"]
    assert by_name["Jordan Sample"]["confidence"] == ANCHOR_WEIGHTS["phone"]
    assert by_name["Riley Sample"]["matched_anchors"] == ["dob"]
    assert by_name["Riley Sample"]["confidence"] == ANCHOR_WEIGHTS["dob"]
    assert by_name["Jordan Household"]["matched_anchors"] == ["address_zip"]
    assert by_name["Jordan Household"]["confidence"] == ANCHOR_WEIGHTS["address_zip"]
    assert by_name["Jordan Household"]["source"] == "same_address"
    assert result["discovery_status"] == "complete"


@pytest.mark.asyncio
async def test_second_run_is_cached_and_review_survives_refresh():
    db = AsyncDB()
    db["indemnitors"].docs.append({
        "booking_number": "B-100",
        "indemnitor_name": "Jordan Sample",
        "indemnitor_phone": "5550100100",
    })
    first = await run_discovery(
        db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True,
    )
    assert first["cached"] is False
    assert len(first["contacts"]) == 1
    stored = db["contacts"].docs[0]
    stored["discovered_contacts"][0]["review_status"] = "reviewed"
    second = await run_discovery(
        db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=False,
    )
    assert second["cached"] is True
    refreshed = await run_discovery(
        db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True,
    )
    assert refreshed["contacts"][0]["review_status"] == "reviewed"
    assert refreshed["contacts"][0]["candidate_id"] == first["contacts"][0]["candidate_id"]
    assert "unreviewed_expire_at" not in db["contacts"].docs[0]


@pytest.mark.asyncio
async def test_provider_failure_is_status_not_an_exception():
    db = AsyncDB()

    async def _boom(db, booking):
        raise RuntimeError("backend")

    def _boom_sync(booking):
        raise RuntimeError("backend")

    with patch("dashboard.services.contact_discovery._provider_prior", _boom), \
         patch("dashboard.services.contact_discovery._provider_same_address", _boom), \
         patch("dashboard.services.contact_discovery._provider_booking_fields", _boom_sync):
        result = await run_discovery(
            db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True,
        )
    assert result["discovery_status"] == "failed"
    assert result["contacts"] == []
    assert db["contacts"].docs[0]["discovery_status"] == "failed"


@pytest.mark.asyncio
async def test_http_and_skiptrace_are_off_by_default(monkeypatch):
    monkeypatch.delenv("INDEMNITOR_DISCOVERY_HTTP", raising=False)
    monkeypatch.delenv("INDEMNITOR_SKIPTRACE_URL", raising=False)
    monkeypatch.delenv("INDEMNITOR_SKIPTRACE_API_KEY", raising=False)

    def _forbidden(*args, **kwargs):
        raise AssertionError("public http")

    monkeypatch.setattr("dashboard.services.contact_discovery.public_request", _forbidden)
    monkeypatch.setattr("scrapers.plain_http.request", _forbidden)
    db = AsyncDB()
    result = await run_discovery(
        db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True,
    )
    statuses = {row["provider"]: row["status"] for row in result["providers_run"]}
    assert statuses["lee_property_appraiser"] == "skipped_disabled"
    assert statuses["lee_clerk_parties"] == "skipped_disabled"
    assert statuses["skiptrace"] == "skipped_disabled"


@pytest.mark.asyncio
async def test_enabled_http_uses_plain_request_and_keeps_anchor(monkeypatch):
    monkeypatch.setenv("INDEMNITOR_DISCOVERY_HTTP", "1")
    monkeypatch.setenv("INDEMNITOR_DISCOVERY_HTTP_GAP_S", "0")
    monkeypatch.delenv("INDEMNITOR_SKIPTRACE_URL", raising=False)
    monkeypatch.delenv("INDEMNITOR_SKIPTRACE_API_KEY", raising=False)
    monkeypatch.setattr("dashboard.services.contact_discovery._last_http_monotonic", 0.0)

    class _Resp:
        text = "Owner SAMPLE, JORDAN 100 Test St 00000"
        content = b"ok"
        status_code = 200

    def _ok(method, url):
        assert method == "GET"
        assert url.startswith("https://")
        return _Resp()

    monkeypatch.setattr("dashboard.services.contact_discovery.public_request", _ok)
    db = AsyncDB()
    result = await run_discovery(
        db,
        booking_number="B-100",
        full_name="Casey Sample",
        county="Lee",
        state="FL",
        force=True,
    )
    names = {row["name"] for row in result["contacts"]}
    assert "SAMPLE, JORDAN" in names or any(row["source"] == "lee_property_appraiser" for row in result["contacts"])
    prop = next(row for row in result["contacts"] if row["source"] == "lee_property_appraiser")
    assert "address_zip" in prop["matched_anchors"]
    assert prop["confidence"] == ANCHOR_WEIGHTS["address_zip"]


def test_sheriff_cooldown_does_not_call_the_origin(monkeypatch):
    monkeypatch.setattr("scrapers.lee_rate_limit.is_cooled_down", lambda: True)

    def _forbidden(*args, **kwargs):
        raise AssertionError("origin")

    monkeypatch.setattr("scrapers.plain_http.request", _forbidden)
    with pytest.raises(_RateLimited) as caught:
        public_request("GET", "https://www.sheriffleefl.org/booking")
    assert caught.value.code == "lee_rate_limited"


def test_sheriff_429_records_cooldown_without_the_body(monkeypatch):
    monkeypatch.setattr("scrapers.lee_rate_limit.is_cooled_down", lambda: False)
    monkeypatch.setattr("dashboard.services.contact_discovery._last_http_monotonic", 0.0)
    monkeypatch.setenv("INDEMNITOR_DISCOVERY_HTTP_GAP_S", "0")
    noted = []
    monkeypatch.setattr("scrapers.lee_rate_limit.record_429", lambda detail="": noted.append(detail))

    def _blocked(*args, **kwargs):
        raise EgressBlocked("egress_block: Lee HTTP 429")

    monkeypatch.setattr("scrapers.plain_http.request", _blocked)
    with pytest.raises(_RateLimited):
        public_request("GET", "https://www.sheriffleefl.org/booking")
    assert noted == ["HTTP 429"]


def test_public_request_source_is_plain_https():
    import inspect
    src = inspect.getsource(public_request)
    for banned in ("impersonate", "curl_cffi", "verify=False", "Mozilla/"):
        assert banned not in src


@pytest.mark.asyncio
async def test_purge_drops_old_unreviewed_and_keeps_reviewed():
    db = AsyncDB()
    old = (NOW - timedelta(days=31)).isoformat()
    fresh = NOW.isoformat()
    db["contacts"].docs.append({
        "_id": "c1",
        "booking_number": "B-100",
        "discovered_contacts": [
            {"name": "Old Person", "source": "prior_shamrock", "review_status": "unreviewed", "discovered_at": old},
            {"name": "Kept Person", "source": "prior_shamrock", "review_status": "reviewed", "discovered_at": old},
            {"name": "New Person", "source": "same_address", "review_status": "unreviewed", "discovered_at": fresh},
        ],
    })
    result = await purge_unreviewed_candidates(db, now=NOW)
    assert result["purged_candidates"] == 1
    names = [row["name"] for row in db["contacts"].docs[0]["discovered_contacts"]]
    assert names == ["Kept Person", "New Person"]


@pytest.mark.asyncio
async def test_batch_claims_a_queued_lee_booking(monkeypatch):
    db = AsyncDB()
    db["arrests"].docs.append(_booking())
    db["indemnitors"].docs.append({
        "booking_number": "B-100",
        "indemnitor_name": "Jordan Sample",
        "indemnitor_phone": "5550100100",
    })
    monkeypatch.setattr(
        "dashboard.services.contact_discovery._lee_claim_query",
        lambda now: {"booking_number": "B-100"},
    )
    summary = await run_lee_discovery_batch(db, limit=5, now=NOW)
    assert summary["claimed"] == 1
    assert summary["completed"] == 1
    assert db["arrests"].docs[0]["indemnitor_discovery"]["candidate_count"] == 1
    assert len(db["contacts"].docs[0]["discovered_contacts"]) == 1


def test_enqueue_is_lee_florida_only():
    db = SyncDB()
    count = _enqueue(db, [
        ArrestRecord(County="Lee", State="GA", Booking_Number="B-1", Full_Name="Casey Sample"),
        ArrestRecord(County="Lee", State="FL", Booking_Number="B-2", Full_Name="Casey Sample"),
    ])
    assert count == 1
    updates = db["arrests"].updates
    assert len(updates) == 1
    assert updates[0][0] == {"state": "FL", "county": "Lee", "booking_number": "B-2"}
    assert updates[0][1]["$set"]["indemnitor_discovery.status"] == "queued"


def test_raising_hook_leaves_the_write_successful(monkeypatch):
    def _boom(db, records):
        raise RuntimeError("hook")

    monkeypatch.setattr(
        "writers.indemnitor_discovery_hook.enqueue_lee_indemnitor_discovery",
        _boom,
    )
    writer = _make_writer(_bulk_result([0]))
    stats = writer.write_records(
        [ArrestRecord(County="Lee", State="FL", Booking_Number="B-100", Full_Name="Casey Sample")],
        "Lee",
    )
    assert stats["new_records"] == 1
    assert stats["new_record_indexes"] == [0]


def test_slow_hook_leaves_the_write_on_time(monkeypatch):
    monkeypatch.setattr("writers.indemnitor_discovery_hook.HOOK_JOIN_SECONDS", 0.05)

    def _slow(db, records):
        time.sleep(2)

    monkeypatch.setattr(
        "writers.indemnitor_discovery_hook.enqueue_lee_indemnitor_discovery",
        _slow,
    )
    writer = _make_writer(_bulk_result([0]))
    started = time.perf_counter()
    stats = writer.write_records(
        [ArrestRecord(County="Lee", State="FL", Booking_Number="B-100", Full_Name="Casey Sample")],
        "Lee",
    )
    elapsed = time.perf_counter() - started
    assert stats["new_records"] == 1
    assert elapsed < 1.0
