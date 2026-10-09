"""DRAFT record_key PR: identity decoupled from the printed booking number.

Synthetic fixtures only. Covers core/record_key.py, the MongoWriter upsert in
both RECORD_KEY_MODE settings, routing by record_key with legacy booking
URLs, serialize_doc, and the migration script (dry-run default, duplicate
report, backup guard, apply on in-memory fakes). Nothing touches prod.
"""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.record_key import (
    BOOKING_PARTIAL_INDEX,
    LEGACY_BOOKING_INDEX,
    RECORD_KEY_INDEX,
    arrest_ref_query,
    record_key_for,
    record_key_mode,
    stored_booking_number,
)
from tests.test_fl_miami_dade_natural_key import _row, _snapshot
from tests.test_staff_edits_survive_rescrape import FakeArrests, _matches, _rec, _writer

KEY = "md_dedupe_v2:" + "cd" * 32
KEY2 = "md_dedupe_v2:" + "ef" * 32


# ── core ───────────────────────────────────────────────────────────────────

def test_record_key_for_prefers_stored_then_md_then_booking():
    assert record_key_for({"record_key": "R1", "booking_number": "B1"}) == "R1"
    assert record_key_for({"md_dedupe": KEY, "booking_number": ""}) == KEY
    assert record_key_for({"md_dedupe": "junk", "booking_number": "B2"}) == "B2"
    assert record_key_for({"booking_number": KEY}) == KEY
    assert record_key_for({}) == "" and record_key_for(None) == ""


def test_mode_defaults_off_and_stored_booking_number(monkeypatch):
    monkeypatch.delenv("RECORD_KEY_MODE", raising=False)
    assert record_key_mode() is False
    assert stored_booking_number(KEY, True) == KEY          # off: unchanged behaviour
    monkeypatch.setenv("RECORD_KEY_MODE", "on")
    assert record_key_mode() is True
    assert stored_booking_number(KEY, True) == ""            # on: MD stores blank
    assert stored_booking_number("2026-1", False) == "2026-1"


def test_arrest_ref_query_routes_record_key_and_legacy_booking_and_never_blank():
    q = arrest_ref_query(KEY)
    assert {"record_key": KEY} in q["$or"] and {"booking_number": KEY} in q["$or"]
    docs = [{"_id": 1, "record_key": KEY, "booking_number": ""},
            {"_id": 2, "record_key": "2026-1", "booking_number": "2026-1"},
            {"_id": 3, "booking_number": "LEGACY-9"}, {"_id": 4, "record_key": KEY2, "booking_number": ""}]
    hit = lambda ref: [d for d in docs if _matches(d, arrest_ref_query(ref))]  # noqa: E731
    assert hit(KEY) == [docs[0]]
    assert hit("2026-1") == [docs[1]]
    assert hit("LEGACY-9") == [docs[2]]          # legacy URL (no record_key yet)
    assert hit("") == [] and hit("   ") == []    # a blank ref never matches blank-booking records
    assert arrest_ref_query("X", county="Lee", state=None) == {"$or": [{"record_key": "X"}, {"booking_number": "X"}],
                                                                "county": "Lee"}


# ── writer ─────────────────────────────────────────────────────────────────

def _md_record(monkeypatch):
    return _snapshot(monkeypatch, [_row(1, "g1")])[0]


def test_writer_mode_off_is_unchanged_but_adds_record_key(monkeypatch):
    monkeypatch.delenv("RECORD_KEY_MODE", raising=False)
    rec = _md_record(monkeypatch)
    key = rec.extra_data["md_dedupe"]
    arrests = FakeArrests([])
    _writer(arrests).write_records([rec], "Miami-Dade")
    doc = arrests.docs[0]
    assert doc["booking_number"] == key and doc["record_key"] == key
    arrests = FakeArrests([])
    _writer(arrests).write_records([_rec("2026-77")], "Lee")
    assert arrests.docs[0]["booking_number"] == "2026-77" == arrests.docs[0]["record_key"]


def test_writer_mode_on_upserts_on_record_key_and_stores_blank_md_booking(monkeypatch):
    monkeypatch.setenv("RECORD_KEY_MODE", "on")
    rec = _md_record(monkeypatch)
    key = rec.extra_data["md_dedupe"]
    arrests = FakeArrests([])
    w = _writer(arrests)
    w.write_records([rec], "Miami-Dade")
    w.write_records([rec], "Miami-Dade")                     # rescrape: same doc, no duplicate
    assert len(arrests.docs) == 1
    doc = arrests.docs[0]
    assert doc["booking_number"] == "" and doc["record_key"] == key and doc["booking_key_internal"] is True
    # Another MD person gets their own doc even though both bookings are blank.
    rec2 = _snapshot(monkeypatch, [_row(2, "g2", name="ZZSYNTH, BETA")])[0]
    w.write_records([rec2], "Miami-Dade")
    assert len(arrests.docs) == 2 and len({d["record_key"] for d in arrests.docs}) == 2
    # Normal counties: booking_number and record_key both carry the source number.
    w.write_records([_rec("2026-88")], "Lee")
    lee = next(d for d in arrests.docs if d["county"] == "Lee")
    assert lee["booking_number"] == "2026-88" == lee["record_key"]


def test_writer_index_selection(monkeypatch):
    calls = []

    class Coll:
        name = "arrests"

        def create_index(self, keys, **kw):
            calls.append(kw.get("name"))

        def drop_index(self, *a, **k):
            pass

        def update_many(self, *a, **k):
            pass

    from writers.mongo_writer import MongoWriter

    w = MongoWriter.__new__(MongoWriter)
    w.arrests, w.leads, w.scraper_status = Coll(), Coll(), Coll()
    monkeypatch.delenv("RECORD_KEY_MODE", raising=False)
    w._ensure_indexes()
    assert LEGACY_BOOKING_INDEX in calls and RECORD_KEY_INDEX not in calls
    calls.clear()
    monkeypatch.setenv("RECORD_KEY_MODE", "on")
    w._ensure_indexes()
    assert RECORD_KEY_INDEX in calls and BOOKING_PARTIAL_INDEX in calls and LEGACY_BOOKING_INDEX not in calls


# ── routing ────────────────────────────────────────────────────────────────

def test_routes_resolve_md_by_record_key_and_legacy_url():
    from dashboard.routers import legacy
    from tests.test_staff_edits_survive_rescrape import AsyncFake, _request

    md = {"_id": "oid-md", "state": "FL", "county": "Miami-Dade", "booking_number": "", "record_key": KEY,
          "booking_key_internal": True, "md_dedupe": KEY, "full_name": "ZZSYNTH, ALPHA", "charges": "A",
          "charge_details": [{"charge": "A"}], "bond_amount": ""}
    other = {"_id": "oid-2", "state": "FL", "county": "Miami-Dade", "booking_number": "", "record_key": KEY2,
             "booking_key_internal": True, "full_name": "ZZSYNTH, BETA", "charges": "B"}
    cols = {"arrests": AsyncFake([md, other])}
    with patch.object(legacy, "get_collection", side_effect=lambda n: cols.setdefault(n, AsyncFake())):
        asyncio.run(legacy.update_lead_details(_request({"booking_number": KEY, "case_number": "26CF1"})))
    docs = {d["_id"]: d for d in cols["arrests"].docs}
    assert docs["oid-md"]["case_number"] == "26CF1"
    assert "case_number" not in docs["oid-2"]                # the other blank-booking MD record untouched
    assert docs["oid-md"]["booking_number"] == "" and docs["oid-md"]["record_key"] == KEY


def test_serialize_doc_exposes_record_key_and_internal_flag_post_migration():
    from dashboard.routers.helpers import serialize_doc

    out = serialize_doc({"booking_number": "", "record_key": KEY, "booking_key_internal": True})
    assert out["record_key"] == KEY and out["booking_key_internal"] is True and out["booking_number_display"] == ""
    out = serialize_doc({"booking_number": "2026-5"})
    assert out["record_key"] == "2026-5" and out["booking_key_internal"] is False


def test_sl_core_route_ref_helper():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "dashboard/sl-core.js").read_text(encoding="utf-8")
    assert "window.slRecordRef" in js and "rec.record_key || rec.booking_number" in js


# ── migration script ───────────────────────────────────────────────────────

from scripts.migrations import record_key_migration as mig  # noqa: E402


class FakeColl:
    def __init__(self, docs=()):
        self.docs = [deepcopy(d) for d in docs]
        self.indexes = {"_id_": {}, LEGACY_BOOKING_INDEX: {}}
        self.writes = 0

    def find(self, q=None, proj=None):
        return [deepcopy(d) for d in self.docs if _matches(d, q or {})]

    def count_documents(self, q):
        return len(self.find(q))

    def bulk_write(self, ops, ordered=False):
        for op in ops:
            for d in self.docs:
                if _matches(d, op._filter):
                    d.update(op._doc["$set"])
                    self.writes += 1
        return SimpleNamespace()

    def create_index(self, keys, **kw):
        self.indexes[kw["name"]] = kw

    def index_information(self):
        return dict(self.indexes)

    def drop_index(self, name):
        self.indexes.pop(name)


class FakeDB(dict):
    def __missing__(self, k):
        self[k] = FakeColl()
        return self[k]


def _db():
    db = FakeDB()
    db["arrests"] = FakeColl([
        {"_id": 1, "state": "FL", "county": "Miami-Dade", "booking_number": KEY, "md_dedupe": KEY,
         "booking_key_internal": True},
        {"_id": 2, "state": "FL", "county": "Miami-Dade", "booking_number": KEY2, "md_dedupe": KEY2,
         "booking_key_internal": True},
        {"_id": 3, "state": "FL", "county": "Lee", "booking_number": "2026-1"},
        {"_id": 4, "state": "GA", "county": "Lee", "booking_number": "2026-1"},   # other state: not a dup
    ])
    db["active_bonds"] = FakeColl([{"_id": 10, "booking_number": KEY}, {"_id": 11, "booking_number": "2026-1"}])
    db["tasks"] = FakeColl([{"_id": 20, "booking_number": KEY2}])
    return db


def test_plan_reports_backfill_internal_and_no_dupes():
    db = _db()
    rep = mig.plan(db["arrests"].find(), mig.linked_internal_counts(db))
    assert rep["record_key_backfill"] == 4 and rep["internal_key_records_to_blank"] == 2
    assert rep["duplicate_record_keys"] == [] and rep["duplicate_nonempty_booking_numbers"] == []
    assert rep["safe_to_apply"] is True
    assert rep["linked_internal_refs"] == {"active_bonds.booking_number": 1, "tasks.booking_number": 1}
    assert "md_dedupe" not in json.dumps(rep)                # report never prints keys or names


def test_plan_flags_duplicates_and_blocks_apply():
    docs = _db()["arrests"].find() + [{"_id": 5, "state": "FL", "county": "Lee", "booking_number": "2026-1"},
                                      {"_id": 6, "state": "FL", "county": "Miami-Dade", "record_key": KEY,
                                       "booking_number": ""}]
    rep = mig.plan(docs)
    assert rep["safe_to_apply"] is False
    assert {d["count"] for d in rep["duplicate_record_keys"]} == {2}
    assert rep["duplicate_nonempty_booking_numbers"][0]["ids"] == ["3", "5"]


def test_apply_on_fakes_backfills_swaps_indexes_blanks_md_and_links():
    from pymongo import UpdateOne

    db = _db()
    done = mig.apply(db, update_one_cls=UpdateOne)
    a = {d["_id"]: d for d in db["arrests"].docs}
    assert a[1]["booking_number"] == "" and a[1]["record_key"] == KEY
    assert a[3]["booking_number"] == "2026-1" == a[3]["record_key"]
    assert RECORD_KEY_INDEX in db["arrests"].indexes and BOOKING_PARTIAL_INDEX in db["arrests"].indexes
    assert LEGACY_BOOKING_INDEX not in db["arrests"].indexes
    assert db["arrests"].indexes[BOOKING_PARTIAL_INDEX]["partialFilterExpression"] == {"booking_number": {"$gt": ""}}
    ab = {d["_id"]: d for d in db["active_bonds"].docs}
    assert ab[10]["record_key"] == KEY and ab[10]["booking_number"] == KEY   # not blanked without --blank-linked
    assert "record_key" not in ab[11]
    assert done["internal_blanked"] == 2 and done["linked_record_key_set"] == 2 and done["linked_blanked"] == 0
    again = mig.apply(db, update_one_cls=UpdateOne)          # idempotent
    assert again["record_key_backfilled"] == 0 and again["internal_blanked"] == 0


def test_main_defaults_to_dry_run_and_guards_apply(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MONGODB_URI", raising=False)
    assert mig.main(["--apply"]) == 2                                   # no verified-backup flag
    assert mig.main(["--apply", "--i-have-a-verified-backup"]) == 2     # no backup dir
    assert mig.main(["--apply", "--i-have-a-verified-backup", "--backup-dir", str(tmp_path)]) == 2  # empty dir
    dump = tmp_path / "ShamrockBailDB"
    dump.mkdir()
    (dump / "arrests.bson").write_bytes(b"x")
    assert mig.check_backup(str(tmp_path), "ShamrockBailDB") is None
    assert mig.main([]) == 2                                            # dry run still needs the app env
    err = capsys.readouterr().err
    assert "refusing --apply" in err and "MONGODB_URI is not set" in err
    src = (mig.ROOT / "scripts/migrations/record_key_migration.py").read_text()
    assert "mongodump" in src and "mongorestore" in src                 # backup + restore documented
