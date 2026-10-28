"""Lee (FL) co-signer discovery. Fixtures are synthetic. No outreach."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from pymongo.errors import DuplicateKeyError

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


def _cmp(value, bound, sign):
    """Mongo-style bracketed compare: different BSON types never match."""
    if value is None or type(value) is not type(bound):
        return False
    return value >= bound if sign > 0 else value <= bound


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
            if "$elemMatch" in expected:
                items = [item for value in values if isinstance(value, list) for item in value]
                if not any(isinstance(item, dict) and _match(item, expected["$elemMatch"]) for item in items):
                    return False
                continue
            if "$nin" in expected and any(value in expected["$nin"] for value in values):
                return False
            if "$in" in expected:
                allowed = expected["$in"]
                # Mongo: a null in $in also matches a missing field.
                hit = None in allowed and not present
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
            if "$gte" in expected and not any(_cmp(value, expected["$gte"], 1) for value in values):
                return False
            if "$lte" in expected and not any(_cmp(value, expected["$lte"], -1) for value in values):
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
        for key, value in (update.get("$setOnInsert") or {}).items():
            _set_path(doc, key, value)
        if any(other.get("_id") == doc["_id"] for other in self.docs):
            raise DuplicateKeyError("E11000 duplicate key error collection: contacts index: _id_")
        self._apply(doc, update)
        self.docs.append(doc)
        return _Result(1)

    @staticmethod
    def _apply(doc, update):
        for key, value in (update.get("$set") or {}).items():
            _set_path(doc, key, value)
        for key in (update.get("$unset") or {}):
            _unset_path(doc, key)
        for key, amount in (update.get("$inc") or {}).items():
            doc[key] = (doc.get(key) or 0) + amount
        for key, cond in (update.get("$pull") or {}).items():
            if isinstance(doc.get(key), list):
                doc[key] = [item for item in doc[key] if not (isinstance(item, dict) and _match(item, cond))]


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
            "defendant_county": "Lee",
            "defendant_state": "FL",
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
        "defendant_county": "Lee",
        "defendant_state": "FL",
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
        "defendant_county": "Lee",
        "defendant_state": "FL",
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
        {"booking_number": "B-100", "defendant_county": "Lee", "defendant_state": "FL", "indemnitor_name": "Jordan Sample", "indemnitor_phone": "5550100100"},
        {"booking_number": "B-100", "defendant_county": "Collier", "defendant_state": "FL", "indemnitor_name": "Pat Other", "indemnitor_phone": "5550100300"},
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
            "defendant_booking_number": "B-100", "defendant_county": "Lee", "defendant_state": "FL",
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
        {"booking_number": "B-100", "defendant_county": "Lee", "defendant_state": "FL",
         "defendant_name": "Casey Sample", "relative_name": "Morgan Sample", "relative_phone": "5550100200", "relationship": "parent"},
    )
    db["family_relationships"].docs.extend([
        {"booking_number": "B-100", "defendant_county": "Lee County", "defendant_state": "Florida",
         "person_id": "casey_sample", "person_name": "casey_sample", "relative_name": "Taylor Sample",
         "relation_type": "sibling", "phone": "5550100201", "status": "active"},
        {"booking_number": "B-100", "defendant_county": "Lee", "defendant_state": "FL",
         "person_id": "casey_sample", "person_name": "Casey Sample", "relative_name": "Gone Sample",
         "relation_type": "sibling", "phone": "5550100202", "status": "soft_deleted"},
        {"booking_number": "B-100", "defendant_county": "Lee", "defendant_state": "FL",
         "person_id": "casey_sample", "person_name": "Casey Sample", "relative_name": "No Anchor",
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


# --- Codex P2 follow-ups on #280: intake state, idempotent upsert, newest-row TTL ---

from bson import ObjectId  # noqa: E402

from dashboard.services.contact_discovery import (  # noqa: E402
    UNREVIEWED_TTL_DAYS,
    contact_doc_id,
    contact_identity,
    unreviewed_expire_at,
    upsert_contact_doc,
)


class RacyContacts(AsyncCollection):
    """Every upsert checks for a match, then waits until all writers have
    checked before inserting. This is the window two real Mongo upserts race in."""

    def __init__(self, writers):
        super().__init__()
        self.writers = writers
        self.arrived = 0

    async def update_one(self, filt, update, upsert=False):
        import asyncio

        hit = any(_match(doc, filt or {}) for doc in self.docs)
        if upsert and not hit:
            self.arrived += 1
            for _ in range(1000):
                if self.arrived >= self.writers:
                    break
                await asyncio.sleep(0)
            # Insert without re-checking, like two concurrent upserts.
            doc = {"_id": f"id{len(self.docs)}"}
            for key, value in (filt or {}).items():
                if not isinstance(value, dict):
                    _set_path(doc, key, value)
            for key, value in (update.get("$setOnInsert") or {}).items():
                _set_path(doc, key, value)
            if any(other.get("_id") == doc["_id"] for other in self.docs):
                raise DuplicateKeyError("E11000 duplicate key error collection: contacts index: _id_")
            self._apply(doc, update)
            self.docs.append(doc)
            return _Result(1)
        return await super().update_one(filt, update, upsert=upsert)


class RacyDB(AsyncDB):
    def __init__(self, writers=2):
        super().__init__()
        self._cols["contacts"] = RacyContacts(writers)


def _lee_identity():
    return contact_identity("FL", "Lee", "B-100")


@pytest.mark.asyncio
async def test_intake_from_same_name_county_in_another_state_is_excluded():
    db = AsyncDB()
    db["intake_queue"].docs.extend([
        {   # Lee, GA via the defendant fields: excluded.
            "defendant_booking_number": "B-100", "defendant_county": "Lee", "defendant_state": "GA",
            "indemnitor_name": "Georgia Defendant Branch", "indemnitor_phone": "5550100201",
            "indemnitor": {}, "defendant": {},
        },
        {   # Lee, Georgia via the nested defendant block: excluded.
            "defendant_booking_number": "B-100", "defendant_county": "Lee",
            "indemnitor_name": "Georgia Nested Branch", "indemnitor_phone": "5550100202",
            "indemnitor": {}, "defendant": {"state": "Georgia"},
        },
        {   # Lee, GA via the matched fields: excluded.
            "matched_booking_number": "B-100", "matched_county": "Lee", "matched_state": "GA",
            "indemnitor_name": "Georgia Matched Branch", "indemnitor_phone": "5550100203",
            "indemnitor": {}, "defendant": {},
        },
        {   # Lee, FL via the matched fields: kept.
            "matched_booking_number": "B-100", "matched_county": "Lee", "matched_state": "FL",
            "indemnitor_name": "Florida Matched Branch", "indemnitor_phone": "5550100204",
            "indemnitor": {}, "defendant": {},
        },
    ])
    result = await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    assert [row["name"] for row in result["contacts"]] == ["Florida Matched Branch"]


@pytest.mark.asyncio
async def test_concurrent_upserts_of_one_arrest_create_one_doc():
    import asyncio

    db = RacyDB(writers=2)
    await asyncio.gather(
        run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True),
        run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True),
    )
    docs = db["contacts"].docs
    assert len(docs) == 1
    assert docs[0]["_id"] == contact_doc_id(_lee_identity())
    assert (docs[0]["state"], docs[0]["county"], docs[0]["booking_number"]) == ("FL", "Lee", "B-100")


@pytest.mark.asyncio
async def test_repeated_upserts_of_one_arrest_create_one_doc():
    db = AsyncDB()
    for _ in range(3):
        await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    assert len(db["contacts"].docs) == 1
    assert db["contacts"].docs[0]["_id"] == contact_doc_id(_lee_identity())


@pytest.mark.asyncio
async def test_legacy_objectid_doc_is_updated_not_duplicated():
    import asyncio

    legacy_id = ObjectId()
    db = RacyDB(writers=2)
    db["contacts"].docs.append({
        "_id": legacy_id, "state": "FL", "county": "Lee", "booking_number": "B-100",
        "discovery_status": "complete", "discovered_contacts": [],
    })
    await asyncio.gather(
        run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True),
        run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True),
    )
    await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    docs = db["contacts"].docs
    assert len(docs) == 1
    assert docs[0]["_id"] == legacy_id  # kept, not migrated
    cached = await run_discovery(db, booking_number="B-100", county="Lee", state="FL", force=False)
    assert cached["cached"] is True


@pytest.mark.asyncio
async def test_duplicate_key_on_insert_is_applied_as_an_update():
    db = AsyncDB()
    identity = _lee_identity()
    db["contacts"].docs.append({"_id": contact_doc_id(identity), "state": "FL", "county": "Lee", "booking_number": "B-100"})

    class LateInsert(AsyncCollection):
        """The upsert misses the doc (it lands just after the check), then
        the insert hits the unique _id."""

        def __init__(self, docs):
            super().__init__()
            self.docs = docs
            self.calls = []

        async def find_one(self, query, projection=None):
            return None  # the winner's insert lands just after this check

        async def update_one(self, filt, update, upsert=False):
            self.calls.append(upsert)
            if upsert:
                raise DuplicateKeyError("E11000 duplicate key error collection: contacts index: _id_")
            return await super().update_one(filt, update, upsert=False)

    col = LateInsert(db["contacts"].docs)
    await upsert_contact_doc(col, identity, {"$set": {**identity, "discovery_status": "complete"}})
    assert col.calls == [True, False]
    assert len(col.docs) == 1
    assert col.docs[0]["discovery_status"] == "complete"


@pytest.mark.asyncio
async def test_contacts_doc_id_is_scoped_to_the_full_identity():
    lee_fl = contact_doc_id(contact_identity("FL", "Lee", "B-100"))
    assert lee_fl == contact_doc_id(contact_identity("fl", "Lee", "B-100"))
    assert lee_fl != contact_doc_id(contact_identity("GA", "Lee", "B-100"))
    assert lee_fl != contact_doc_id(contact_identity("FL", "Collier", "B-100"))
    assert lee_fl != contact_doc_id(contact_identity("FL", "Lee", "B-101"))


def test_unreviewed_ttl_comes_from_the_newest_candidate():
    old = NOW - timedelta(days=29)
    newest = NOW - timedelta(days=1)
    rows = [
        {"review_status": "unreviewed", "discovered_at": old.isoformat()},
        {"review_status": "unreviewed", "discovered_at": newest.isoformat()},
        {"review_status": "unreviewed", "discovered_at": (NOW - timedelta(days=10)).isoformat()},
    ]
    assert unreviewed_expire_at(rows, NOW) == newest + timedelta(days=UNREVIEWED_TTL_DAYS)


@pytest.mark.asyncio
async def test_failed_provider_old_row_does_not_expire_a_fresh_row():
    db = AsyncDB()
    old = NOW - timedelta(days=29)
    db["contacts"].docs.append({
        "state": "FL", "county": "Lee", "booking_number": "B-100",
        "discovered_contacts": [
            {"name": "Prior Row", "source": "same_address", "review_status": "unreviewed",
             "discovered_at": old.isoformat()},
        ],
    })
    db["indemnitors"].docs.append(
        {"booking_number": "B-100", "defendant_county": "Lee", "defendant_state": "FL",
         "indemnitor_name": "Fresh Row", "indemnitor_phone": "5550100100"},
    )

    async def _boom(db, booking):
        raise RuntimeError("backend")

    with patch("dashboard.services.contact_discovery._provider_same_address", _boom):
        result = await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    assert {row["name"] for row in result["contacts"]} == {"Prior Row", "Fresh Row"}
    expire = db["contacts"].docs[0]["unreviewed_expire_at"]
    # Fresh Row was discovered during this run, so expiry is ~30 days from now,
    # not 1 day after the 29-day-old row.
    assert expire >= datetime.now(timezone.utc) + timedelta(days=UNREVIEWED_TTL_DAYS - 1)
    assert expire > old + timedelta(days=UNREVIEWED_TTL_DAYS)


# ── Codex round 2 on #280 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_out_of_state_resident_indemnitor_on_lee_fl_booking_is_kept():
    """The bare ``state`` on an indemnitor row is the co-signer's home state."""
    db = AsyncDB()
    db["indemnitors"].docs.extend([
        {   # Lives in GA, co-signed the Lee FL booking: kept.
            "booking_number": "B-100", "state": "GA", "city": "Test City",
            "defendant_county": "Lee", "defendant_state": "FL",
            "indemnitor_name": "Georgia Resident", "indemnitor_phone": "5550100401",
        },
        {   # Same booking number, but the arrest itself is in Lee GA: excluded.
            "booking_number": "B-100", "defendant_county": "Lee", "defendant_state": "GA",
            "indemnitor_name": "Lee Georgia Case", "indemnitor_phone": "5550100402",
        },
    ])
    result = await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    rows = [(row["name"], row["source"]) for row in result["contacts"]]
    assert rows == [("Georgia Resident", "prior_shamrock")]


def _raising_discovery(calls):
    async def _run(db, **kwargs):
        calls.append(kwargs.get("booking_number"))
        raise RuntimeError("transient")
    return _run


@pytest.mark.asyncio
async def test_transient_error_leaves_the_booking_retryable_and_it_is_reclaimed(monkeypatch):
    from dashboard.services import contact_discovery as cd

    db = AsyncDB()
    db["arrests"].docs.append(_booking())
    calls = []
    monkeypatch.setattr(cd, "run_discovery", _raising_discovery(calls))
    first = await run_lee_discovery_batch(db, limit=5, now=NOW)
    assert first["claimed"] == 1 and first["retry"] == 1 and first["failed"] == 0
    marker = db["arrests"].docs[0]["indemnitor_discovery"]
    assert marker["status"] == "retry"
    assert marker["attempts"] == 1
    assert marker["retry_after"] == NOW + timedelta(minutes=cd.RETRY_BASE_MINUTES)

    # Backoff not over: the real claim query does not pick it up.
    early = await run_lee_discovery_batch(db, limit=5, now=NOW + timedelta(minutes=5))
    assert early["claimed"] == 0 and calls == ["B-100"]

    # After the backoff the same booking is claimed again and completes.
    monkeypatch.setattr(cd, "run_discovery", run_discovery)
    later = await run_lee_discovery_batch(db, limit=5, now=NOW + timedelta(minutes=16))
    assert later["claimed"] == 1 and later["completed"] == 1
    assert db["arrests"].docs[0]["indemnitor_discovery"]["status"] == "complete"


@pytest.mark.asyncio
async def test_retry_cap_makes_the_booking_terminal_failed_and_logs(monkeypatch, caplog):
    from dashboard.services import contact_discovery as cd

    db = AsyncDB()
    db["arrests"].docs.append(_booking())
    calls = []
    monkeypatch.setattr(cd, "run_discovery", _raising_discovery(calls))
    when = NOW
    with caplog.at_level("WARNING", logger="shamrock.contact_discovery"):
        for _ in range(cd.MAX_DISCOVERY_ATTEMPTS + 2):
            await run_lee_discovery_batch(db, limit=5, now=when)
            when = when + timedelta(days=1)
    assert len(calls) == cd.MAX_DISCOVERY_ATTEMPTS
    marker = db["arrests"].docs[0]["indemnitor_discovery"]
    assert marker["status"] == "failed"
    assert marker["attempts"] == cd.MAX_DISCOVERY_ATTEMPTS
    assert marker["error"] == "RuntimeError"
    assert "retry limit reached" in caplog.text
    assert "B-100" not in caplog.text


def test_retry_backoff_doubles():
    from dashboard.services.contact_discovery import RETRY_BASE_MINUTES, retry_backoff

    assert [retry_backoff(n) for n in (1, 2, 3)] == [
        timedelta(minutes=RETRY_BASE_MINUTES * m) for m in (1, 2, 4)
    ]


def _contact_doc(idx, stamp):
    return {
        "_id": f"c{idx:04d}",
        "discovered_contacts": [
            {"name": f"Person {idx}", "source": "prior_shamrock", "review_status": "unreviewed", "discovered_at": stamp},
        ],
    }


@pytest.mark.asyncio
async def test_purge_reaches_expired_docs_past_the_first_200():
    db = AsyncDB()
    old = (NOW - timedelta(days=31)).isoformat()
    fresh = NOW.isoformat()
    docs = db["contacts"].docs
    docs.extend(_contact_doc(i, fresh) for i in range(250))      # positions 0-249: nothing expired
    docs.extend(_contact_doc(i, old) for i in range(250, 290))   # positions 250-289: expired
    result = await purge_unreviewed_candidates(db, now=NOW)
    assert result == {"purged_candidates": 40, "docs": 40}
    assert all(not doc["discovered_contacts"] for doc in docs[250:])
    assert all(len(doc["discovered_contacts"]) == 1 for doc in docs[:250])


@pytest.mark.asyncio
async def test_purge_pages_through_more_than_one_limit_of_expired_docs():
    db = AsyncDB()
    old = (NOW - timedelta(days=40)).isoformat()
    legacy = NOW - timedelta(days=40)   # a BSON-date stamp from an older writer
    docs = db["contacts"].docs
    docs.extend(_contact_doc(i, old) for i in range(430))
    docs.extend(_contact_doc(i, legacy) for i in range(430, 450))
    result = await purge_unreviewed_candidates(db, now=NOW, limit=200)
    assert result == {"purged_candidates": 450, "docs": 450}
    assert all(not doc["discovered_contacts"] for doc in docs)


@pytest.mark.asyncio
async def test_purge_does_not_loop_on_a_doc_it_cannot_parse():
    db = AsyncDB()
    db["contacts"].docs.append(_contact_doc(1, ""))   # "" sorts before the cutoff string
    result = await purge_unreviewed_candidates(db, now=NOW, limit=1, max_pages=5)
    assert result == {"purged_candidates": 0, "docs": 0}
    assert len(db["contacts"].docs[0]["discovered_contacts"]) == 1


def _contacts_client(monkeypatch, db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from dashboard.routers import contacts as contacts_router

    monkeypatch.setattr(contacts_router, "get_db", lambda: db)
    app = FastAPI()
    app.include_router(contacts_router.contacts_bp)
    return TestClient(app)


def test_discover_route_with_county_and_state_resolves_a_shared_booking(monkeypatch):
    db = AsyncDB()
    lee = _booking()
    collier = {**_booking(), "_id": "arrest-2", "county": "Collier", "full_name": "Drew Other", "address": "200 Other St"}
    db["arrests"].docs.extend([lee, collier])
    db["indemnitors"].docs.extend([
        {"booking_number": "B-100", "defendant_county": "Lee", "defendant_state": "FL", "indemnitor_name": "Jordan Sample", "indemnitor_phone": "5550100100"},
        {"booking_number": "B-100", "defendant_county": "Collier", "defendant_state": "FL", "indemnitor_name": "Pat Other", "indemnitor_phone": "5550100300"},
    ])
    client = _contacts_client(monkeypatch, db)

    # What the tracking drawer used to send: booking + name only -> 409.
    bare = client.post("/api/contacts/discover", json={"booking_number": "B-100", "full_name": "Casey Sample"})
    assert bare.status_code == 409
    assert bare.json()["error"] == "county_and_state_required"
    assert db["contacts"].docs == []

    # With the record's county and state the shared booking resolves.
    resp = client.post("/api/contacts/discover", json={
        "booking_number": "B-100", "full_name": "Casey Sample", "county": "Lee", "state": "FL",
    })
    assert resp.status_code == 200
    assert [row["name"] for row in resp.json()["contacts"]] == ["Jordan Sample"]
    keys = [(d["state"], d["county"], d["booking_number"]) for d in db["contacts"].docs]
    assert keys == [("FL", "Lee", "B-100")]


def test_tracking_discover_button_sends_county_and_state():
    import json as _json
    import shutil
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    js = root / "dashboard" / "sl-tracking.js"
    src = js.read_text(encoding="utf-8")
    # The drawer button passes the record's county and state to the action.
    assert "SLTracking.discoverContacts(\\'' + bkSafe + '\\',\\'' + nameSafe + '\\',\\'' + countySafe + '\\',\\'' + stateSafe + '\\')" in src
    assert "body: JSON.stringify(_discoverBody(bookingNumber, defName, county, state))" in src
    # The history endpoint the drawer reads returns the state.
    tracking_py = (root / "dashboard" / "routers" / "tracking.py").read_text(encoding="utf-8")
    assert '"state": bond.get("state")' in tracking_py

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, str(root / "tests/js/tracking_discover_sim.js"), str(js)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[:300]
    out = _json.loads(r.stdout.strip().splitlines()[-1])
    assert out["explicit"] == {"booking_number": "B-100", "full_name": "Casey Sample", "county": "Lee", "state": "FL"}
    assert out["blank"]["county"] == "" and out["blank"]["state"] == ""


# ── Codex round 3 on #280 ────────────────────────────────────────────────────

import copy  # noqa: E402
import hashlib  # noqa: E402
import logging  # noqa: E402

from dashboard.services.contact_discovery import (  # noqa: E402
    ContactsLegacyAmbiguous,
    canonical_place,
)


def _lee_claim(monkeypatch):
    monkeypatch.setattr(
        "dashboard.services.contact_discovery._lee_claim_query",
        lambda now: {"booking_number": "B-100"},
    )


@pytest.mark.asyncio
async def test_booking_only_prior_rows_without_arrest_county_or_state_do_not_match():
    """P1 :553 — a booking number alone never links a prior row to the arrest."""
    db = AsyncDB()
    db["indemnitors"].docs.extend([
        {"booking_number": "B-100", "indemnitor_name": "No Place", "indemnitor_phone": "5550100501"},
        {"booking_number": "B-100", "defendant_county": "Lee",
         "indemnitor_name": "No State", "indemnitor_phone": "5550100502"},
        {"booking_number": "B-100", "defendant_state": "FL",
         "indemnitor_name": "No County", "indemnitor_phone": "5550100503"},
        # Bare county/state on an indemnitor row is the co-signer's address.
        {"booking_number": "B-100", "county": "Lee", "state": "FL",
         "indemnitor_name": "Home Address Only", "indemnitor_phone": "5550100504"},
        {"booking_number": "B-100", "defendant_county": "Lee", "defendant_state": "FL",
         "indemnitor_name": "Scoped Row", "indemnitor_phone": "5550100505"},
    ])
    db["intake_queue"].docs.extend([
        {"defendant_booking_number": "B-100", "indemnitor_name": "Intake No Place",
         "indemnitor_phone": "5550100506", "indemnitor": {}, "defendant": {}},
        {"defendant_booking_number": "B-100", "defendant_state": "FL",
         "indemnitor_name": "Intake No County", "indemnitor_phone": "5550100507",
         "indemnitor": {}, "defendant": {}},
    ])
    result = await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    assert [row["name"] for row in result["contacts"]] == ["Scoped Row"]


@pytest.mark.asyncio
async def test_name_only_family_rows_are_rejected():
    """P1 :663 — family rows count only on booking + county + state."""
    db = AsyncDB()
    db["family_graph"].docs.extend([
        {"defendant_name": "Casey Sample", "relative_name": "Name Only",
         "relative_phone": "5550100601", "relationship": "parent"},
        {"booking_number": "B-100", "defendant_name": "Casey Sample", "relative_name": "Booking Only",
         "relative_phone": "5550100602", "relationship": "parent"},
        {"booking_number": "B-100", "defendant_county": "Collier", "defendant_state": "FL",
         "relative_name": "Other County", "relative_phone": "5550100603", "relationship": "parent"},
    ])
    db["family_relationships"].docs.extend([
        {"person_id": "casey_sample", "person_name": "Casey Sample", "relative_name": "Slug Only",
         "relation_type": "sibling", "phone": "5550100604", "status": "active"},
        {"booking_number": "B-100", "defendant_county": "Lee", "person_name": "Casey Sample",
         "relative_name": "No State", "relation_type": "sibling", "phone": "5550100605", "status": "active"},
    ])
    result = await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    assert result["contacts"] == []


@pytest.mark.asyncio
async def test_one_booking_only_legacy_contacts_doc_is_updated_in_place():
    """P2 :157 — the single legacy doc gets the identity fields; no second doc."""
    db = AsyncDB()
    db["arrests"].docs.append(_booking())
    legacy_id = ObjectId()
    db["contacts"].docs.append({
        "_id": legacy_id, "booking_number": "B-100",
        "discovered_contacts": [
            {"name": "Kept Reviewed", "phone": "5550100701", "review_status": "reviewed", "source": "manual"},
        ],
    })
    db["indemnitors"].docs.append(
        {"booking_number": "B-100", "defendant_county": "Lee", "defendant_state": "FL",
         "indemnitor_name": "Jordan Sample", "indemnitor_phone": "5550100100"},
    )
    result = await run_discovery(db, booking_number="B-100", county="Lee", state="FL", force=True)
    assert result["discovery_status"] == "complete"
    docs = db["contacts"].docs
    assert len(docs) == 1
    assert docs[0]["_id"] == legacy_id
    assert (docs[0]["state"], docs[0]["county"], docs[0]["booking_number"]) == ("FL", "Lee", "B-100")
    assert {row["name"] for row in docs[0]["discovered_contacts"]} == {"Kept Reviewed", "Jordan Sample"}
    # The next read finds it on the full identity.
    cached = await run_discovery(db, booking_number="B-100", county="Lee", state="FL", force=False)
    assert cached["cached"] is True


@pytest.mark.asyncio
async def test_legacy_doc_scoped_to_another_county_is_not_reused():
    db = AsyncDB()
    db["arrests"].docs.append(_booking())
    db["contacts"].docs.append({"_id": "collier-legacy", "booking_number": "B-100", "county": "Collier"})
    await run_discovery(db, booking_number="B-100", county="Lee", state="FL", force=True)
    assert len(db["contacts"].docs) == 2
    assert db["contacts"].docs[0] == {"_id": "collier-legacy", "booking_number": "B-100", "county": "Collier"}
    assert db["contacts"].docs[1]["_id"] == contact_doc_id(_lee_identity())


@pytest.mark.asyncio
async def test_two_legacy_docs_write_nothing_and_log_only_the_code(caplog):
    """P2 :157 — ambiguous legacy docs: no write, no guess, code only in logs."""
    db = AsyncDB()
    db["arrests"].docs.append(_booking())
    db["contacts"].docs.extend([
        {"_id": "legacy-a", "booking_number": "B-100", "discovered_contacts": []},
        {"_id": "legacy-b", "booking_number": "B-100", "state": "FL", "discovered_contacts": []},
    ])
    db["indemnitors"].docs.append(
        {"booking_number": "B-100", "defendant_county": "Lee", "defendant_state": "FL",
         "indemnitor_name": "Jordan Sample", "indemnitor_phone": "5550100100"},
    )
    before = copy.deepcopy(db["contacts"].docs)
    arrest_before = copy.deepcopy(db["arrests"].docs)
    with caplog.at_level(logging.INFO, logger="shamrock.contact_discovery"):
        result = await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    assert result["discovery_status"] == ContactsLegacyAmbiguous.code
    assert db["contacts"].docs == before
    assert db["arrests"].docs == arrest_before
    assert "code=contacts_legacy_ambiguous" in caplog.text
    for secret in ("B-100", "Casey", "Jordan", "Sample", "5550100100"):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_legacy_doc_whose_booking_is_on_two_arrests_writes_nothing():
    db = AsyncDB()
    db["arrests"].docs.extend([
        _booking(),
        {**_booking(), "_id": "arrest-2", "county": "Collier", "address": "200 Other St"},
    ])
    db["contacts"].docs.append({"_id": "legacy-a", "booking_number": "B-100", "discovered_contacts": []})
    before = copy.deepcopy(db["contacts"].docs)
    with pytest.raises(ContactsLegacyAmbiguous):
        await upsert_contact_doc(
            db["contacts"], _lee_identity(), {"$set": {"discovery_status": "complete"}},
            arrests=db["arrests"],
        )
    assert db["contacts"].docs == before
    # Without the arrests collection uniqueness is unknown: also fail closed.
    with pytest.raises(ContactsLegacyAmbiguous):
        await upsert_contact_doc(db["contacts"], _lee_identity(), {"$set": {"discovery_status": "complete"}})
    assert db["contacts"].docs == before


@pytest.mark.asyncio
async def test_batch_marks_an_ambiguous_legacy_booking_terminal(monkeypatch):
    db = AsyncDB()
    db["arrests"].docs.append(_booking())
    db["contacts"].docs.extend([
        {"_id": "legacy-a", "booking_number": "B-100"},
        {"_id": "legacy-b", "booking_number": "B-100"},
    ])
    _lee_claim(monkeypatch)
    summary = await run_lee_discovery_batch(db, limit=5, now=NOW)
    assert summary["failed"] == 1 and summary["completed"] == 0
    marker = db["arrests"].docs[0]["indemnitor_discovery"]
    assert marker["status"] == "failed"
    assert marker["error"] == "contacts_legacy_ambiguous"
    assert len(db["contacts"].docs) == 2


@pytest.mark.parametrize("county,state", [
    ("Lee", "FL"), ("lee", "fl"), ("LEE", "Florida"), ("Lee County", "FL"),
    ("lee county", "florida"), (" Lee ", " FL "), ("Lee County, FL", ""), ("Lee (FL)", ""),
    ("Lee", "Fla"),
])
def test_place_spellings_give_the_same_identity_and_id(county, state):
    """P2 :124 — county/state are canonical before the identity and the _id."""
    identity = contact_identity(state, county, "B-100")
    assert identity == {"state": "FL", "county": "Lee", "booking_number": "B-100"}
    # Same id as the 098b666 formula on the canonical "FL"/"Lee" spelling.
    raw = "\x1f".join(("FL", "Lee", "B-100"))
    v1 = "contacts:v1:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]
    assert contact_doc_id(identity) == v1


def test_canonical_place_handles_multiword_counties_and_rejects_unknown_states():
    assert canonical_place("st. lucie county", "florida") == ("St. Lucie", "FL")
    assert canonical_place("MIAMI-DADE", "Fl") == ("Miami-Dade", "FL")
    assert canonical_place("Lee", "Narnia")[1] == ""
    assert contact_identity("Narnia", "Lee", "B-100") is None


@pytest.mark.asyncio
async def test_variant_spellings_reuse_one_contacts_doc():
    db = AsyncDB()
    await run_discovery(db, booking_number="B-100", county="lee", state="Florida", force=True)
    await run_discovery(db, booking_number="B-100", county="Lee County", state="FL", force=True)
    assert len(db["contacts"].docs) == 1
    assert db["contacts"].docs[0]["_id"] == contact_doc_id(_lee_identity())
    assert (db["contacts"].docs[0]["county"], db["contacts"].docs[0]["state"]) == ("Lee", "FL")


class _BrokenCollection(AsyncCollection):
    def find(self, query=None, projection=None):
        raise RuntimeError("partial read")

    async def find_one(self, query, projection=None):
        raise RuntimeError("partial read")


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["family_graph", "intake_queue", "indemnitors"])
async def test_partial_prior_read_failure_marks_the_run_failed(broken):
    """P2 :726 — one prior collection failing is not a silent pass."""
    db = AsyncDB()
    db._cols[broken] = _BrokenCollection()
    for name in ("indemnitors", "family_graph"):
        if name != broken:
            db[name].docs.append(
                {"booking_number": "B-100", "defendant_county": "Lee", "defendant_state": "FL",
                 "indemnitor_name": "Still Read", "relative_name": "Still Read",
                 "indemnitor_phone": "5550100801", "relative_phone": "5550100801"},
            )
    result = await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    assert result["discovery_status"] == "failed"
    assert result["discovery_error"] == "prior_read_failed"
    # The whole prior provider is dropped for this run (no partial result is
    # treated as complete); rows from earlier runs are kept, see the batch test.
    assert result["contacts"] == []
    prior = [row for row in result["providers_run"] if row["provider"] == "prior_shamrock"]
    assert prior[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_partial_prior_read_failure_sends_the_booking_to_retry(monkeypatch):
    db = AsyncDB()
    db["arrests"].docs.append(_booking())
    db._cols["family_relationships"] = _BrokenCollection()
    db["contacts"].docs.append({
        "state": "FL", "county": "Lee", "booking_number": "B-100",
        "discovered_contacts": [
            {"name": "Prior Row", "phone": "5550100901", "source": "prior_shamrock",
             "review_status": "unreviewed", "discovered_at": NOW.isoformat()},
        ],
    })
    _lee_claim(monkeypatch)
    summary = await run_lee_discovery_batch(db, limit=5, now=NOW)
    assert summary["retry"] == 1 and summary["completed"] == 0
    marker = db["arrests"].docs[0]["indemnitor_discovery"]
    assert marker["status"] == "retry"
    assert marker["error"] == "prior_read_failed"
    assert marker["retry_after"] > NOW
    names = [row["name"] for row in db["contacts"].docs[0]["discovered_contacts"]]
    assert "Prior Row" in names


@pytest.mark.asyncio
async def test_batch_marks_an_unresolvable_identity_terminal(monkeypatch):
    db = AsyncDB()
    db["arrests"].docs.append({**_booking(), "state": "", "county": ""})
    _lee_claim(monkeypatch)
    summary = await run_lee_discovery_batch(db, limit=5, now=NOW)
    assert summary["failed"] == 1
    marker = db["arrests"].docs[0]["indemnitor_discovery"]
    assert (marker["status"], marker["error"]) == ("failed", "identity_ambiguous")
    assert db["contacts"].docs == []


@pytest.mark.asyncio
async def test_resolve_arrest_accepts_a_county_suffix_and_state_name():
    db = AsyncDB()
    db["arrests"].docs.append(_booking())
    db["indemnitors"].docs.append(
        {"booking_number": "B-100", "defendant_county": "Lee", "defendant_state": "FL",
         "indemnitor_name": "Jordan Sample", "indemnitor_phone": "5550100100"},
    )
    result = await run_discovery(db, booking_number="B-100", county="Lee County", state="Florida", force=True)
    assert result["discovery_status"] == "complete"
    assert db["arrests"].docs[0]["indemnitor_discovery"]["status"] == "complete"


def test_discover_route_returns_409_for_ambiguous_legacy_contacts(monkeypatch):
    db = AsyncDB()
    db["arrests"].docs.append(_booking())
    db["contacts"].docs.extend([
        {"_id": "legacy-a", "booking_number": "B-100"},
        {"_id": "legacy-b", "booking_number": "B-100"},
    ])
    before = copy.deepcopy(db["contacts"].docs)
    client = _contacts_client(monkeypatch, db)
    resp = client.post("/api/contacts/discover", json={
        "booking_number": "B-100", "full_name": "Casey Sample", "county": "Lee County", "state": "Florida",
    })
    assert resp.status_code == 409
    assert resp.json()["error"] == "legacy_contacts_ambiguous"
    assert db["contacts"].docs == before


def test_get_route_canonicalizes_the_place(monkeypatch):
    db = AsyncDB()
    db["contacts"].docs.append({
        "_id": "x", "state": "FL", "county": "Lee", "booking_number": "B-100",
        "discovered_contacts": [{"name": "Jordan Sample"}], "discovery_status": "complete",
    })
    client = _contacts_client(monkeypatch, db)
    from dashboard.routers import contacts as contacts_router

    monkeypatch.setattr(contacts_router, "get_collection", lambda name: db[name])
    for county, state in (("lee", "fl"), ("Lee County", "Florida")):
        resp = client.get(f"/api/contacts/B-100?county={county}&state={state}")
        assert resp.status_code == 200
        assert [row["name"] for row in resp.json()["contacts"]] == ["Jordan Sample"]


@pytest.mark.asyncio
async def test_legacy_scan_that_hits_its_cap_fails_closed():
    from dashboard.services import contact_discovery as cd

    db = AsyncDB()
    db["arrests"].docs.append(_booking())
    db["contacts"].docs.extend(
        {"_id": f"other-{i}", "booking_number": "B-100", "county": "Collier"} for i in range(cd._LEGACY_SCAN_LIMIT)
    )
    db["contacts"].docs.append({"_id": "legacy-lee", "booking_number": "B-100"})
    before = copy.deepcopy(db["contacts"].docs)
    result = await run_discovery(db, booking_number="B-100", county="Lee", state="FL", force=True)
    assert result["discovery_status"] == ContactsLegacyAmbiguous.code
    assert db["contacts"].docs == before


# --- Codex P2 on #280 at da199a7: atomic purge, revision-guarded refresh ---

def _row(name, status, stamp, source="same_address"):
    return {"name": name, "source": source, "review_status": status, "discovered_at": stamp}


class _RefreshBeforePurgeWrite(AsyncCollection):
    """A refresh lands after the purge read its page, before the purge writes."""

    def __init__(self, added):
        super().__init__()
        self.added = added
        self.fired = False

    async def update_one(self, filt, update, upsert=False):
        if "$pull" in update and not self.fired:
            self.fired = True
            doc = self.docs[0]
            doc["discovered_contacts"] = list(doc["discovered_contacts"]) + list(self.added)
            doc["discovery_rev"] = (doc.get("discovery_rev") or 0) + 1
        return await super().update_one(filt, update, upsert=upsert)


@pytest.mark.asyncio
async def test_purge_keeps_candidates_a_refresh_adds_between_read_and_write():
    db = AsyncDB()
    old = (NOW - timedelta(days=31)).isoformat()
    added = [_row("Refresh New", "unreviewed", NOW.isoformat()), _row("Refresh Reviewed", "reviewed", old)]
    col = _RefreshBeforePurgeWrite(added)
    db._cols["contacts"] = col
    col.docs.append({"_id": "c1", "state": "FL", "county": "Lee", "booking_number": "B-100",
                     "discovered_contacts": [_row("Expired", "unreviewed", old)]})
    result = await purge_unreviewed_candidates(db, now=NOW)
    assert col.fired
    assert result == {"purged_candidates": 1, "docs": 1}
    names = [row["name"] for row in col.docs[0]["discovered_contacts"]]
    assert names == ["Refresh New", "Refresh Reviewed"]
    assert col.docs[0]["discovery_rev"] == 2   # the refresh's bump plus the purge's
    # The TTL follows the array as it is now: a reviewed row keeps the doc.
    assert "unreviewed_expire_at" not in col.docs[0]


@pytest.mark.asyncio
async def test_purge_removes_only_expired_unreviewed_rows():
    db = AsyncDB()
    old = NOW - timedelta(days=31)
    cutoff = NOW - timedelta(days=30)
    db["contacts"].docs.append({"_id": "c1", "discovered_contacts": [
        _row("Expired String", "unreviewed", old.isoformat()),
        _row("Expired Date", "unreviewed", old),
        _row("At Cutoff", "unreviewed", cutoff.isoformat()),
        _row("Fresh String", "unreviewed", NOW.isoformat()),
        _row("Fresh Date", "unreviewed", NOW - timedelta(days=29)),
        _row("Blank Stamp", "unreviewed", ""),
        _row("Garbled Stamp", "unreviewed", "unknown"),
        _row("No Status", None, old.isoformat()),
    ]})
    result = await purge_unreviewed_candidates(db, now=NOW)
    assert result == {"purged_candidates": 3, "docs": 1}
    names = [row["name"] for row in db["contacts"].docs[0]["discovered_contacts"]]
    assert names == ["Fresh String", "Fresh Date", "Blank Stamp", "Garbled Stamp", "No Status"]
    # The update is a $pull on the row condition, never a rewrite of the array.
    pull = contact_discovery_module()._expired_row_conditions(cutoff)
    assert all(cond["review_status"] == "unreviewed" for cond in pull)


def contact_discovery_module():
    import dashboard.services.contact_discovery as module

    return module


@pytest.mark.asyncio
async def test_purge_never_removes_reviewed_rows():
    db = AsyncDB()
    ancient = NOW - timedelta(days=400)
    rows = []
    for status in sorted(contact_discovery_module().REVIEWED_STATUSES):
        rows.append(_row(f"{status} string", status, ancient.isoformat()))
        rows.append(_row(f"{status} date", status, ancient))
    db["contacts"].docs.append({"_id": "c1", "discovered_contacts": rows})
    db["contacts"].docs.append({"_id": "c2", "discovered_contacts": rows + [_row("Expired", "unreviewed", ancient)]})
    result = await purge_unreviewed_candidates(db, now=NOW)
    assert result == {"purged_candidates": 1, "docs": 1}
    assert db["contacts"].docs[0]["discovered_contacts"] == rows
    assert db["contacts"].docs[1]["discovered_contacts"] == rows


class _UpdateUseRecorder(AsyncCollection):
    def __init__(self):
        super().__init__()
        self.updates = []

    async def update_one(self, filt, update, upsert=False):
        self.updates.append(update)
        return await super().update_one(filt, update, upsert=upsert)


@pytest.mark.asyncio
async def test_purge_never_sets_the_candidate_array():
    db = AsyncDB()
    col = _UpdateUseRecorder()
    db._cols["contacts"] = col
    col.docs.append({"_id": "c1", "discovered_contacts": [
        _row("Expired", "unreviewed", (NOW - timedelta(days=31)).isoformat()),
        _row("Fresh", "unreviewed", NOW.isoformat()),
    ]})
    await purge_unreviewed_candidates(db, now=NOW)
    assert col.updates, "the purge wrote nothing"
    assert all("discovered_contacts" not in (update.get("$set") or {}) for update in col.updates)
    assert any("discovered_contacts" in (update.get("$pull") or {}) for update in col.updates)
    # One unreviewed row left: the TTL follows the newest remaining stamp.
    assert col.docs[0]["unreviewed_expire_at"] == NOW + timedelta(days=30)


class _RefreshAfterPurgeReread(AsyncCollection):
    """A refresh writes its own TTL after the purge re-read the doc."""

    async def find_one(self, query, projection=None):
        doc = await super().find_one(query, projection)
        if doc is not None and query == {"_id": "c1"}:
            snapshot = dict(doc)
            doc["unreviewed_expire_at"] = "refresh-ttl"
            doc["discovery_rev"] = doc.get("discovery_rev", 0) + 1
            return snapshot
        return doc


@pytest.mark.asyncio
async def test_purge_ttl_write_does_not_overwrite_a_newer_refresh():
    db = AsyncDB()
    col = _RefreshAfterPurgeReread()
    db._cols["contacts"] = col
    col.docs.append({"_id": "c1", "discovered_contacts": [
        _row("Expired", "unreviewed", (NOW - timedelta(days=31)).isoformat()),
        _row("Fresh", "unreviewed", NOW.isoformat()),
    ]})
    await purge_unreviewed_candidates(db, now=NOW)
    assert [row["name"] for row in col.docs[0]["discovered_contacts"]] == ["Fresh"]
    assert col.docs[0]["unreviewed_expire_at"] == "refresh-ttl"


class _WriterBetweenReadAndWrite(AsyncCollection):
    """Before each of the first ``times`` guarded refresh writes, another
    writer changes the doc (adds a reviewed row, bumps the revision)."""

    def __init__(self, times):
        super().__init__()
        self.times = times
        self.fired = 0

    async def update_one(self, filt, update, upsert=False):
        guarded = "discovery_rev" in (filt or {}) and "$pull" not in update
        if guarded and self.fired < self.times and self.docs:
            self.fired += 1
            doc = self.docs[0]
            doc["discovered_contacts"] = list(doc["discovered_contacts"]) + [
                {"name": f"Concurrent {self.fired}", "source": "skiptrace", "review_status": "reviewed",
                 "candidate_id": f"cc-{self.fired}", "discovered_at": NOW.isoformat()},
            ]
            doc["discovery_rev"] = (doc.get("discovery_rev") or 0) + 1
        return await super().update_one(filt, update, upsert=upsert)


def _lee_contacts_doc():
    return {"_id": "c1", "state": "FL", "county": "Lee", "booking_number": "B-100",
            "discovered_contacts": [
                {"name": "Prior Reviewed", "source": "skiptrace", "review_status": "reviewed",
                 "candidate_id": "c-1", "discovered_at": NOW.isoformat()},
            ]}


@pytest.mark.asyncio
async def test_refresh_remerges_when_the_doc_changed_after_its_read():
    db = AsyncDB()
    col = _WriterBetweenReadAndWrite(times=1)
    db._cols["contacts"] = col
    col.docs.append(_lee_contacts_doc())
    result = await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    assert col.fired == 1
    assert result["discovery_status"] != "failed"
    names = {row["name"] for row in col.docs[0]["discovered_contacts"]}
    assert {"Prior Reviewed", "Concurrent 1"} <= names
    assert col.docs[0]["discovery_rev"] == 2   # the other writer's bump plus ours


@pytest.mark.asyncio
async def test_refresh_does_not_put_back_rows_a_purge_removed_after_its_read():
    db = AsyncDB()
    old = (NOW - timedelta(days=31)).isoformat()

    class PurgeBetween(AsyncCollection):
        fired = False

        async def update_one(self, filt, update, upsert=False):
            if "discovery_rev" in (filt or {}) and "$pull" not in update and not self.fired:
                self.fired = True
                await purge_unreviewed_candidates(holder, now=NOW)
            return await super().update_one(filt, update, upsert=upsert)

    holder = db
    col = PurgeBetween()
    db._cols["contacts"] = col
    doc = _lee_contacts_doc()
    doc["discovered_contacts"].append(
        {"name": "Expired Row", "source": "prior_shamrock", "review_status": "unreviewed",
         "discovered_at": old, "candidate_id": "c-old"})
    col.docs.append(doc)
    # The prior provider fails this run, so a merge from the stale read would
    # keep its prior_shamrock row and write it back over the purge.
    db._cols["family_graph"] = _BrokenCollection()
    await run_discovery(db, booking_number="B-100", full_name="Casey Sample", county="Lee", state="FL", force=True)
    assert col.fired
    names = [row["name"] for row in col.docs[0]["discovered_contacts"]]
    assert "Expired Row" not in names
    assert "Prior Reviewed" in names


@pytest.mark.asyncio
async def test_refresh_that_keeps_losing_the_race_writes_nothing_and_retries(monkeypatch):
    db = AsyncDB()
    db["arrests"].docs.append(_booking())
    col = _WriterBetweenReadAndWrite(times=99)
    db._cols["contacts"] = col
    col.docs.append(_lee_contacts_doc())
    _lee_claim(monkeypatch)
    summary = await run_lee_discovery_batch(db, limit=5, now=NOW)
    attempts = contact_discovery_module().CONTACTS_WRITE_ATTEMPTS
    assert col.fired == attempts
    assert summary["retry"] == 1 and summary["completed"] == 0
    marker = db["arrests"].docs[0]["indemnitor_discovery"]
    assert (marker["status"], marker["error"]) == ("retry", "contacts_write_conflict")
    # Only the other writer's rows are there: the run wrote nothing.
    assert col.docs[0]["discovery_rev"] == attempts
    assert "discovery_status" not in col.docs[0]


@pytest.mark.asyncio
async def test_guarded_upsert_refuses_a_doc_that_appeared_after_the_read():
    from dashboard.services.contact_discovery import ContactsWriteConflict

    db = AsyncDB()
    identity = _lee_identity()
    db["contacts"].docs.append({"_id": contact_doc_id(identity), **identity, "discovery_rev": 1,
                                "discovered_contacts": [{"name": "Other Writer"}]})
    with pytest.raises(ContactsWriteConflict):
        await upsert_contact_doc(db["contacts"], identity, {"$set": {"discovered_contacts": []}},
                                 arrests=db["arrests"], expected=None)
    assert db["contacts"].docs[0]["discovered_contacts"] == [{"name": "Other Writer"}]
