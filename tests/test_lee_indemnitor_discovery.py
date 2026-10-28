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


def _ops_for(writer):
    call = writer.arrests.bulk_write.call_args_list[-1]
    return call.args[0]


def test_queue_marker_rides_in_the_arrest_upsert_for_lee_fl_only():
    writer = _make_writer(_bulk_result([0]))
    writer.write_records(
        [ArrestRecord(County="Lee", State="FL", Booking_Number="B-100", Full_Name="Casey Sample")],
        "Lee",
    )
    op = _ops_for(writer)[0]
    marker = op._doc["$setOnInsert"]["indemnitor_discovery"]
    assert marker["status"] == "queued"
    assert "created_at" in op._doc["$setOnInsert"]
    # Durable with the write itself: no second arrests round trip, no thread.
    writer.arrests.update_one.assert_not_called()

    other = _make_writer(_bulk_result([0]))
    other.write_records(
        [ArrestRecord(County="Lee", State="GA", Booking_Number="B-100", Full_Name="Casey Sample")],
        "Lee",
    )
    assert "indemnitor_discovery" not in _ops_for(other)[0]._doc["$setOnInsert"]


def test_queue_exception_still_writes_the_arrests(monkeypatch, caplog):
    def _boom(*args, **kwargs):
        raise RuntimeError("queue down")

    monkeypatch.setattr("writers.indemnitor_discovery_hook._is_lee_florida", _boom)
    writer = _make_writer(_bulk_result([0, 1]))
    with caplog.at_level("WARNING"):
        stats = writer.write_records(
            [
                ArrestRecord(County="Lee", State="FL", Booking_Number="B-100", Full_Name="Casey Sample"),
                ArrestRecord(County="Lee", State="FL", Booking_Number="B-101", Full_Name="Casey Sample"),
            ],
            "Lee",
        )
    assert stats["new_records"] == 2
    assert stats["new_record_indexes"] == [0, 1]
    ops = _ops_for(writer)
    assert len(ops) == 2
    for op in ops:
        assert "indemnitor_discovery" not in op._doc["$setOnInsert"]
        assert "created_at" in op._doc["$setOnInsert"]
    text = caplog.text
    assert "error_type=RuntimeError" in text
    assert "B-100" not in text and "Casey" not in text


def test_marker_import_failure_still_writes_the_arrests(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _no_hook(name, *args, **kwargs):
        if name == "writers.indemnitor_discovery_hook":
            raise ImportError("hook missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_hook)
    writer = _make_writer(_bulk_result([0]))
    stats = writer.write_records(
        [ArrestRecord(County="Lee", State="FL", Booking_Number="B-100", Full_Name="Casey Sample")],
        "Lee",
    )
    assert stats["new_records"] == 1


def test_write_path_starts_no_thread_and_waits_on_nothing(monkeypatch):
    import threading

    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self.name))
    writer = _make_writer(_bulk_result([0]))
    t0 = time.perf_counter()
    writer.write_records(
        [ArrestRecord(County="Lee", State="FL", Booking_Number="B-100", Full_Name="Casey Sample")],
        "Lee",
    )
    assert time.perf_counter() - t0 < 1.0
    assert not [name for name in started if "indemnitor" in name]


@pytest.mark.asyncio
async def test_unreviewed_candidate_is_never_an_outreach_target():
    from dashboard.services.outreach_sequencer import OutreachSequencer

    db = AsyncDB()
    db["contacts"].docs.append({
        "state": "FL", "county": "Lee", "booking_number": "B-100",
        "discovered_contacts": [
            {"name": "Jordan Sample", "phone": "5550100100", "confidence": 0.9, "review_status": "unreviewed"},
        ],
    })
    seq = OutreachSequencer(db)
    arrest = {"state": "FL", "county": "Lee", "booking_number": "B-100", "full_name": ""}
    assert await seq._resolve_phone(arrest) == ("", "none")

    db["contacts"].docs[0]["discovered_contacts"][0]["review_status"] = "approved"
    assert await seq._resolve_phone(arrest) == ("5550100100", "contact_discovery")
    # A legacy candidate with no review state is not usable either.
    db["contacts"].docs[0]["discovered_contacts"][0].pop("review_status")
    assert await seq._resolve_phone(arrest) == ("", "none")


@pytest.mark.asyncio
async def test_outreach_reads_contacts_on_the_full_arrest_identity():
    from dashboard.services.outreach_sequencer import OutreachSequencer

    db = AsyncDB()
    db["contacts"].docs.append({
        "state": "FL", "county": "Collier", "booking_number": "B-100",
        "discovered_contacts": [
            {"name": "Other Person", "phone": "5550100999", "confidence": 0.99, "review_status": "reviewed"},
        ],
    })
    seq = OutreachSequencer(db)
    lee = {"state": "FL", "county": "Lee", "booking_number": "B-100", "full_name": ""}
    assert await seq._resolve_phone(lee) == ("", "none")


@pytest.mark.asyncio
async def test_same_booking_in_two_counties_keeps_separate_contacts():
    db = AsyncDB()
    db["indemnitors"].docs.extend([
        {"booking_number": "B-100", "county": "Lee", "indemnitor_name": "Jordan Sample", "indemnitor_phone": "5550100100"},
        {"booking_number": "B-100", "county": "Collier", "indemnitor_name": "Pat Other", "indemnitor_phone": "5550100300"},
    ])
    lee = await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    col = await run_discovery(db, booking_number="B-100", full_name="Drew Other", county="Collier", state="FL", force=True)
    assert [row["name"] for row in lee["contacts"]] == ["Jordan Sample"]
    assert [row["name"] for row in col["contacts"]] == ["Pat Other"]
    keys = sorted((d["state"], d["county"], d["booking_number"]) for d in db["contacts"].docs)
    assert keys == [("FL", "Collier", "B-100"), ("FL", "Lee", "B-100")]
    # A cached read for Lee does not return Collier's doc.
    again = await run_discovery(db, booking_number="B-100", county="Lee", state="FL", force=False)
    assert again["cached"] is True
    assert [row["name"] for row in again["contacts"]] == ["Jordan Sample"]


@pytest.mark.asyncio
async def test_ambiguous_booking_without_county_writes_nothing():
    db = AsyncDB()
    db["arrests"].docs.extend([
        {"_id": "a1", "booking_number": "B-100", "county": "Lee", "state": "FL"},
        {"_id": "a2", "booking_number": "B-100", "county": "Collier", "state": "FL"},
    ])
    result = await run_discovery(db, booking_number="B-100", full_name="Casey Sample", force=True)
    assert result["discovery_status"] == "identity_ambiguous"
    assert db["contacts"].docs == []


@pytest.mark.asyncio
async def test_refresh_keeps_reviewed_rows_the_run_did_not_return():
    db = AsyncDB()
    db["contacts"].docs.append({
        "state": "FL", "county": "Lee", "booking_number": "B-100",
        "discovered_contacts": [
            {"name": "Kept Reviewed", "source": "skiptrace", "review_status": "reviewed",
             "candidate_id": "c-1", "discovered_at": NOW.isoformat()},
            {"name": "Dropped Unreviewed", "source": "same_address", "review_status": "unreviewed",
             "discovered_at": NOW.isoformat()},
        ],
    })
    result = await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    names = {row["name"]: row for row in result["contacts"]}
    assert names["Kept Reviewed"]["review_status"] == "reviewed"
    assert names["Kept Reviewed"]["candidate_id"] == "c-1"
    assert "Dropped Unreviewed" not in names
    assert "unreviewed_expire_at" not in db["contacts"].docs[0]


@pytest.mark.asyncio
async def test_refresh_keeps_prior_rows_of_a_provider_that_failed():
    db = AsyncDB()
    db["contacts"].docs.append({
        "state": "FL", "county": "Lee", "booking_number": "B-100",
        "discovered_contacts": [
            {"name": "Prior Row", "source": "same_address", "review_status": "unreviewed",
             "discovered_at": NOW.isoformat()},
        ],
    })

    async def _boom(db, booking):
        raise RuntimeError("backend")

    with patch("dashboard.services.contact_discovery._provider_same_address", _boom):
        result = await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    assert [row["name"] for row in result["contacts"]] == ["Prior Row"]


@pytest.mark.asyncio
async def test_intake_queue_rows_are_read_on_their_booking_fields():
    db = AsyncDB()
    db["intake_queue"].docs.extend([
        {
            "defendant_booking_number": "B-100", "defendant_county": "Lee",
            "indemnitor_name": "Jordan Sample", "indemnitor_phone": "5550100100",
            "indemnitor": {"firstName": "Jordan", "lastName": "Sample", "dob": "1980-05-06", "relationship": "sibling"},
            "defendant": {"name": ""},
        },
        {
            "matched_booking_number": "B-100", "matched_county": "Collier",
            "indemnitor_name": "Pat Other", "indemnitor_phone": "5550100300",
            "indemnitor": {}, "defendant": {},
        },
    ])
    result = await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    by_name = {row["name"]: row for row in result["contacts"]}
    assert set(by_name) == {"Jordan Sample"}
    assert by_name["Jordan Sample"]["matched_anchors"] == ["phone", "dob"]
    assert by_name["Jordan Sample"]["relationship"] == "sibling"


@pytest.mark.asyncio
async def test_family_records_are_parsed_with_their_schemas():
    db = AsyncDB()
    db["family_graph"].docs.append(
        {"defendant_name": "Casey Sample", "relative_name": "Morgan Sample", "relative_phone": "5550100200", "relationship": "parent"},
    )
    db["family_relationships"].docs.extend([
        {"person_id": "casey_sample", "person_name": "casey_sample", "relative_name": "Taylor Sample",
         "relation_type": "sibling", "phone": "5550100201", "status": "active"},
        {"person_id": "casey_sample", "person_name": "Casey Sample", "relative_name": "Gone Sample",
         "relation_type": "sibling", "phone": "5550100202", "status": "soft_deleted"},
        {"person_id": "casey_sample", "person_name": "Casey Sample", "relative_name": "No Anchor",
         "relation_type": "sibling", "status": "active"},
    ])
    result = await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    by_name = {row["name"]: row for row in result["contacts"]}
    assert set(by_name) == {"Morgan Sample", "Taylor Sample"}
    assert by_name["Morgan Sample"]["matched_anchors"] == ["phone"]
    assert by_name["Morgan Sample"]["relationship"] == "parent"
    assert by_name["Taylor Sample"]["relationship"] == "sibling"


@pytest.mark.asyncio
async def test_public_requests_run_off_the_event_loop_and_sleep_out_the_gap(monkeypatch):
    import threading
    from dashboard.services import contact_discovery as cd

    monkeypatch.setenv("INDEMNITOR_DISCOVERY_HTTP_GAP_S", "0.2")
    monkeypatch.setattr(cd, "_last_http_monotonic", 0.0)
    monkeypatch.setattr(cd, "_http_gap_lock", None)
    threads = []

    def _fake(method, url):
        threads.append(threading.current_thread() is threading.main_thread())
        return "ok"

    monkeypatch.setattr(cd, "public_request", _fake)
    t0 = time.perf_counter()
    assert await cd.public_request_async("GET", "https://example.test/a") == "ok"
    assert await cd.public_request_async("GET", "https://example.test/b") == "ok"
    assert time.perf_counter() - t0 >= 0.15  # waited, did not skip
    assert threads == [False, False]


@pytest.mark.asyncio
async def test_rate_limited_provider_defers_the_booking(monkeypatch):
    from dashboard.services import contact_discovery as cd

    monkeypatch.setenv("INDEMNITOR_DISCOVERY_HTTP", "1")
    monkeypatch.setenv("INDEMNITOR_DISCOVERY_HTTP_GAP_S", "0")
    monkeypatch.delenv("INDEMNITOR_SKIPTRACE_URL", raising=False)

    def _limited(method, url):
        raise _RateLimited("lee_rate_limited")

    monkeypatch.setattr(cd, "public_request", _limited)
    db = AsyncDB()
    db["arrests"].docs.append(_booking())
    monkeypatch.setattr(cd, "_lee_claim_query", lambda now: {"booking_number": "B-100"})
    summary = await run_lee_discovery_batch(db, limit=5, now=NOW)
    assert summary["deferred"] == 1 and summary["completed"] == 0
    marker = db["arrests"].docs[0]["indemnitor_discovery"]
    assert marker["status"] == "deferred"
    assert marker["retry_after"] > NOW
    assert marker.get("deferrals", 0) == 0

    # The next claim after retry_after re-picks it and counts the deferral.
    db["arrests"].docs[0]["indemnitor_discovery"]["retry_after"] = NOW - timedelta(minutes=1)
    summary = await run_lee_discovery_batch(db, limit=5, now=NOW)
    assert summary["deferred"] == 1
    assert db["arrests"].docs[0]["indemnitor_discovery"]["deferrals"] == 1


def test_claim_query_picks_deferred_rows_after_retry_after():
    from dashboard.services.contact_discovery import _lee_claim_query

    clauses = _lee_claim_query(NOW)["$and"][1]["$or"]
    assert {"indemnitor_discovery.status": "deferred", "indemnitor_discovery.retry_after": {"$lte": NOW}} in clauses


def test_skiptrace_anchors_are_recomputed_from_returned_fields():
    from dashboard.services.contact_discovery import skiptrace_anchors

    assert skiptrace_anchors({"phone": "5550100100", "matched_anchors": ["phone", "dob"]}) == ["phone"]
    assert skiptrace_anchors({"phone": "12", "matched_anchors": ["phone"]}) == []
    assert skiptrace_anchors({"dob": "1980-05-06", "matched_anchors": []}) == []
    assert skiptrace_anchors({
        "address": "100 Test St", "zip": "00000", "dob": "05/06/1980",
        "matched_anchors": ["address_zip", "dob"],
    }) == ["dob", "address_zip"]


@pytest.mark.asyncio
async def test_skiptrace_vendor_claims_cannot_inflate_confidence(monkeypatch):
    monkeypatch.setenv("INDEMNITOR_SKIPTRACE_URL", "https://skiptrace.example.test/v1")
    monkeypatch.setenv("INDEMNITOR_SKIPTRACE_API_KEY", "test-key")
    monkeypatch.setenv("INDEMNITOR_DISCOVERY_HTTP_GAP_S", "0")
    monkeypatch.delenv("INDEMNITOR_DISCOVERY_HTTP", raising=False)

    class _Resp:
        content = b"{}"

        @staticmethod
        def json():
            return {"candidates": [
                {"name": "Jordan Sample", "phone": "5550100100", "matched_anchors": ["phone", "dob"]},
                {"name": "Claim Only", "matched_anchors": ["phone", "dob", "address_zip"]},
            ]}

    monkeypatch.setattr("scrapers.plain_http.make_session", lambda *a, **k: object())
    monkeypatch.setattr("scrapers.plain_http.request", lambda *a, **k: _Resp())
    db = AsyncDB()
    result = await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    by_name = {row["name"]: row for row in result["contacts"]}
    assert set(by_name) == {"Jordan Sample"}
    assert by_name["Jordan Sample"]["confidence"] == ANCHOR_WEIGHTS["phone"]


def test_discovery_cron_is_controllable_from_service_control():
    from dashboard.routers.automation_control import ALL_SERVICE_KEYS, SERVICE_META

    assert "lee_indemnitor_discovery" in ALL_SERVICE_KEYS
    assert "lee_indemnitor_discovery" in SERVICE_META
