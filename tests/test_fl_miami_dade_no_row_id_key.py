"""Miami-Dade (FL): no source booking number, so no row-id keys (2026-10-09).

The ArcGIS jail table publishes no booking, jail or case number, and its
ObjectId/GlobalID were reissued by the 2026-10-09 08:03 ET republish (840 of
841 snapshot rows changed GlobalID). #166 failed closed; the owner then
approved an internal natural key (Brendan 2026-10-09 9:32 AM ET), so the
county reopens as unverified. Booking_Number stays blank and the key is never
displayed as a booking number. Synthetic data; no network.
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from dashboard.services.packet_builder_service import arrest_bond_value, build_adaptive_field_map
from scrapers.counties import miami_dade
from scrapers.counties.miami_dade import MiamiDadeCountyScraper, md_dedupe_key

BOOK_MS = int(datetime(2026, 10, 8, 4, tzinfo=timezone.utc).timestamp() * 1000)
DOB_MS = int(datetime(1990, 1, 2, 5, tzinfo=timezone.utc).timestamp() * 1000)
NAME = "ZZSYNTH, TESTPERSON Q"


def _attrs(oid, gid, **over):
    a = {"ObjectId": oid, "GlobalID": gid, "BookDate": BOOK_MS, "Defendant": NAME, "DOB": DOB_MS,
         "Charge1": "BATTERY", "Code2": "RESIST OFFICER W/O VIOLENCE", "Charge3": None}
    a.update(over)
    return a


def _rec(**over):
    return MiamiDadeCountyScraper()._parse_record(_attrs(1, "{11111111-2222-3333-4444-555555555555}", **over))


# ── Reopened under the owner exception ─────────────────────────────────────
def test_miami_dade_reopened_unverified_under_owner_exception():
    from dashboard.extensions import scraper_source_state

    assert MiamiDadeCountyScraper.SOURCE_CONTRACT_VALIDATED is True
    assert MiamiDadeCountyScraper.ALLOWS_INTERNAL_NATURAL_KEY is True
    assert scraper_source_state("Miami-Dade (FL)") == "unverified"
    assert "Owner exception" in (miami_dade.__doc__ or "")
    assert "DOB" in miami_dade.OUT_FIELDS and "GlobalID" not in miami_dade.OUT_FIELDS


# ── Booking number stays blank; internal key only ──────────────────────────
def test_booking_number_blank_and_dedupe_key_labelled():
    r = _rec()
    assert r.Booking_Number == ""
    assert r.extra_data["md_dedupe"].startswith("md_dedupe_v2:")
    assert r.extra_data["md_key_fallback"] is False
    assert "NOT a booking number" in r.extra_data["md_dedupe_label"]
    assert r.Bond_Amount == "" and r.extra_data["bond_published"] is False  # unknown, never "0"
    assert r.Charges == "BATTERY | RESIST OFFICER W/O VIOLENCE"
    assert r.DOB == "1990-01-02"


def test_key_survives_a_republish_that_reissues_row_ids():
    before = MiamiDadeCountyScraper()._parse_record(_attrs(10, "{aaaaaaaa-0000-0000-0000-000000000001}"))
    after = MiamiDadeCountyScraper()._parse_record(_attrs(99010, "{bbbbbbbb-0000-0000-0000-000000000002}"))
    assert before.extra_data["md_dedupe"] == after.extra_data["md_dedupe"]


def test_key_is_defendant_dob_bookdate_and_normalises_spacing_only():
    base = _rec().extra_data["md_dedupe"]
    assert _rec(Charge3="DUI").extra_data["md_dedupe"] == base            # charges not in the key
    assert _rec(Defendant="ZZSYNTH, TESTPERSON  q ").extra_data["md_dedupe"] == base  # spacing/case
    assert _rec(BookDate=BOOK_MS + 86_400_000).extra_data["md_dedupe"] != base
    assert _rec(DOB=DOB_MS + 86_400_000).extra_data["md_dedupe"] != base
    assert md_dedupe_key("", "1990-01-02", "2026-10-08") == ("", False)
    assert md_dedupe_key(NAME, "1990-01-02", "") == ("", False)


def test_key_is_recomputable_from_the_stored_doc():
    r = _rec()
    doc = r.to_mongo_doc()
    assert md_dedupe_key(doc["full_name"], doc["dob"], doc["booking_date"])[0] == doc["extra"]["md_dedupe"]
    assert md_dedupe_key(doc["full_name"], "01/02/1990", "10/08/2026")[0] == doc["extra"]["md_dedupe"]


def test_hash_never_reaches_booking_number_displays():
    from dashboard.routers.helpers import serialize_doc

    r = _rec()
    key = r.extra_data["md_dedupe"]
    doc = serialize_doc(r.to_mongo_doc())
    assert doc["booking_number"] == ""
    # The stored doc carries the key as its routing id; displays print it blank.
    stored = serialize_doc({**r.to_mongo_doc(), "booking_number": key, "md_dedupe": key, "booking_key_internal": True})
    assert stored["booking_number_display"] == "" and stored["booking_key_internal"] is True
    for d in (doc, stored):
        fields = build_adaptive_field_map({
            "defendant": {"name": d["full_name"]}, "booking_number": d["booking_number"],
            "county": d["county"], "charges": d["charges"], "bond_amount": d["bond_amount"],
        })
        assert fields.get("booking_number", "") == ""  # blank fields are omitted
        assert not any("md_dedupe" in str(v) or key.split(":")[1][:16] in str(v) for v in fields.values())
        assert arrest_bond_value(d) == ""


def test_other_county_blank_booking_still_skipped():
    from tests.test_staff_edits_survive_rescrape import FakeArrests, _writer

    r = _rec()
    r.County = "Broward"  # same record shape, not an allow-listed scope
    arrests = FakeArrests([])
    stats = _writer(arrests).write_records([r], "Broward")
    assert stats["skipped_invalid"] == 1 and arrests.docs == []


# ── Read-only dedupe report ────────────────────────────────────────────────
def _stored(gid, *, name=NAME, date="2026-10-08", charges="BATTERY | RESIST OFFICER W/O VIOLENCE", **kw):
    d = {"county": "Miami-Dade", "state": "FL", "booking_number": gid, "full_name": name,
         "booking_date": date, "charges": charges, "status": "Unknown", "bond_amount_raw": ""}
    d.update(kw)
    return d


def _docs():
    return [
        _stored("{aaaaaaaa-0000-0000-0000-000000000001}"),
        _stored("{bbbbbbbb-0000-0000-0000-000000000002}"),           # republish copy
        _stored("517001"),                                            # ObjectId-keyed copy
        _stored("{cccccccc-0000-0000-0000-000000000003}", name="ZZSYNTH, OTHER"),
        _stored("{dddddddd-0000-0000-0000-000000000004}", name="ZZSYNTH, STAFF", staff_edits={"bond": {"amount": 500.0}}),
        _stored("{eeeeeeee-0000-0000-0000-000000000005}", name="ZZSYNTH, STAFF"),
        _stored("{ffffffff-0000-0000-0000-000000000006}", name=""),   # cannot key
    ]


def test_report_counts_duplicate_groups_and_merges():
    sys.path.insert(0, "scripts")
    import miami_dade_dedupe_report as rep

    out = rep.report(_docs())
    assert out["total_rows"] == 7
    assert out["booking_number_shapes"] == {"arcgis_globalid": 6, "numeric (ArcGIS ObjectId)": 1}
    assert out["rows_keyed_fallback"] == 6  # stored GlobalID rows carry no DOB
    assert out["rows_without_name_or_date"] == 1
    assert out["duplicate_groups"] == 2
    assert out["rows_in_duplicate_groups"] == 5
    assert out["rows_that_would_merge"] == 3
    assert out["duplicate_group_sizes"] == {2: 1, 3: 1}
    assert out["groups_with_staff_edits"] == 1


def test_report_groups_staff_edited_charges_by_source_charges():
    """A staff charge edit must not split a row from its untouched duplicates."""
    sys.path.insert(0, "scripts")
    import miami_dade_dedupe_report as rep

    src = "BATTERY | RESIST OFFICER W/O VIOLENCE"
    docs = [
        _stored("{aaaaaaaa-0000-0000-0000-000000000011}"),
        # staff edited charges; provenance keeps the scraped baseline list
        _stored("{bbbbbbbb-0000-0000-0000-000000000012}", charges="BATTERY",
                staff_edits={"charges": {"removed": ["RESIST OFFICER W/O VIOLENCE"],
                                         "baseline": ["BATTERY", "RESIST OFFICER W/O VIOLENCE"]}}),
        # staff charges won a later rescrape; scraped value kept aside
        _stored("{cccccccc-0000-0000-0000-000000000013}", charges="BATTERY (STAFF NOTE)",
                scraped_charges=src, staff_edits={"charges": {"removed": [], "baseline": []}}),
        # legacy staff edit with no saved source list: unresolvable, not silently split
        _stored("{dddddddd-0000-0000-0000-000000000014}", charges="SOMETHING ELSE",
                last_checked_mode="MANUAL_CHARGE_BONDS"),
    ]
    out = rep.report(docs)
    assert out["duplicate_groups"] == 1
    assert out["rows_in_duplicate_groups"] == 3
    assert out["rows_that_would_merge"] == 2
    assert out["groups_with_staff_edits"] == 1
    assert out["rows_staff_charges_unresolvable"] == 1
    assert rep.source_charges({"charges": src}) == src


def test_report_main_is_read_only_and_names_free(monkeypatch):
    sys.path.insert(0, "scripts")
    import miami_dade_dedupe_report as rep

    docs = _docs()

    class Coll:
        def find(self, query, projection=None):
            assert set(query) == {"county"}
            return list(docs)

        def count_documents(self, query):
            return sum(1 for d in docs if d["county"] == query["county"])

        def __getattr__(self, name):
            raise AssertionError(f"write/other call {name} not allowed")

    class Client:
        def __init__(self, *a, **k):
            pass

        def __getitem__(self, name):
            return SimpleNamespace(arrests=Coll())

    import pymongo

    monkeypatch.setattr(pymongo, "MongoClient", Client)
    monkeypatch.setenv("MONGODB_URI", "mongodb://synthetic")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rep.main([])
    text = buf.getvalue()
    assert "rows_that_would_merge: 3" in text
    for needle in ("ZZSYNTH", "TESTPERSON", "md_dedupe_v1:", "aaaaaaaa", "517001"):
        assert needle not in text
