"""Miami-Dade (FL) reopen on the approved internal natural key (2026-10-09).

Owner exception (Brendan 2026-10-09 9:32 AM ET): the ArcGIS jail table has no
booking number and reissues ObjectId/GlobalID on republish, so rows are keyed
on md_dedupe_v2 = sha256(defendant + DOB + BookDate); without a DOB the key
falls back to defendant + BookDate + verbatim charges (flagged). MongoWriter
upserts on it through a narrow allow-listed path; every other county keeps
the blank-booking guard. Synthetic data only; no network.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import pytest

from core.booking_identity import (
    INTERNAL_NATURAL_KEY_SCOPES,
    internal_natural_key,
    is_internal_booking_key,
    public_booking_number,
    scrub_internal_keys,
)
from scrapers.base_scraper import BaseScraper
from scrapers.counties import miami_dade
from scrapers.counties.miami_dade import MiamiDadeCountyScraper
from tests.test_fl_miami_dade_arcgis_contract import _install
from tests.test_staff_edits_survive_rescrape import FakeArrests, _writer

BOOK_MS = int(datetime(2026, 10, 8, 4, tzinfo=timezone.utc).timestamp() * 1000)
DOB_A = int(datetime(1990, 1, 2, 5, tzinfo=timezone.utc).timestamp() * 1000)
DOB_B = int(datetime(1985, 6, 7, 4, tzinfo=timezone.utc).timestamp() * 1000)


def _row(oid, gid, name="ZZSYNTH, ALPHA", dob=DOB_A, c1="BATTERY", c2=None, c3=None, book=BOOK_MS):
    return {"ObjectId": oid, "GlobalID": gid, "BookDate": book, "Defendant": name, "DOB": dob,
            "Charge1": c1, "Code2": c2, "Charge3": c3}


def _snapshot(monkeypatch, rows):
    _install(monkeypatch, len(rows), [(rows, False)])
    return MiamiDadeCountyScraper().scrape()


def _write(arrests, records):
    return _writer(arrests).write_records(records, "Miami-Dade")


SNAP_1 = [
    _row(101, "{aaaaaaaa-0000-0000-0000-000000000001}"),
    _row(102, "{aaaaaaaa-0000-0000-0000-000000000002}", name="ZZSYNTH, BRAVO", dob=DOB_B, c1="DUI"),
    _row(103, "{aaaaaaaa-0000-0000-0000-000000000003}", name="ZZSYNTH, CHARLIE", dob=None, c1="TRESPASS"),
]
# Same bookings after a republish: every ObjectId/GlobalID regenerated.
SNAP_2 = [
    _row(90101, "{bbbbbbbb-0000-0000-0000-000000000001}"),
    _row(90102, "{bbbbbbbb-0000-0000-0000-000000000002}", name="ZZSYNTH, BRAVO", dob=DOB_B, c1="DUI"),
    _row(90103, "{bbbbbbbb-0000-0000-0000-000000000003}", name="ZZSYNTH, CHARLIE", dob=None, c1="TRESPASS"),
]


def test_two_snapshots_with_regenerated_row_ids_add_zero_rows(monkeypatch):
    arrests = FakeArrests([])
    first = _write(arrests, _snapshot(monkeypatch, SNAP_1))
    assert first["new_records"] == 3 and first["skipped_invalid"] == 0
    second = _write(arrests, _snapshot(monkeypatch, SNAP_2))
    assert second["new_records"] == 0 and second["skipped_invalid"] == 0
    assert len(arrests.docs) == 3
    for d in arrests.docs:
        assert is_internal_booking_key(d["booking_number"]) and d["booking_key_internal"] is True
        assert d["md_dedupe"] == d["booking_number"]
        assert d["bond_amount_raw"] == ""  # unknown bond, never "0"


def test_charge_change_updates_the_same_record(monkeypatch):
    arrests = FakeArrests([])
    _write(arrests, _snapshot(monkeypatch, [_row(1, "g1")]))
    amended = _write(arrests, _snapshot(monkeypatch, [_row(77, "g77", c1="BATTERY ON LEO", c2="RESIST")]))
    assert amended["new_records"] == 0 and len(arrests.docs) == 1
    assert arrests.docs[0]["charges"] == "BATTERY ON LEO | RESIST"


def test_staff_charge_and_bond_edits_survive_a_rescrape(monkeypatch):
    arrests = FakeArrests([])
    _write(arrests, _snapshot(monkeypatch, [_row(1, "g1")]))
    doc = arrests.docs[0]
    doc.update({"charges": "STAFF CHARGE", "charge_details": [{"charge": "STAFF CHARGE", "source": "staff"}],
                "bond_amount": 2500.0, "bond_amount_raw": "2500",
                "staff_edits": {"bond": {"amount": 2500.0}, "charges": {"removed": ["BATTERY"], "baseline": ["BATTERY"]}}})
    _write(arrests, _snapshot(monkeypatch, [_row(5, "g5")]))
    assert len(arrests.docs) == 1
    d = arrests.docs[0]
    assert d["bond_amount"] == 2500.0 and d["bond_amount_raw"] == "2500"
    assert "STAFF CHARGE" in d["charges"] and "BATTERY" not in d["charges"].replace("STAFF CHARGE", "")


def test_dob_missing_fallback_is_flagged_and_logged_as_counts_only(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    recs = _snapshot(monkeypatch, SNAP_1)
    flags = {r.Full_Name: r.extra_data["md_key_fallback"] for r in recs}
    assert flags == {"ZZSYNTH, ALPHA": False, "ZZSYNTH, BRAVO": False, "ZZSYNTH, CHARLIE": True}
    arrests = FakeArrests([])
    _write(arrests, recs)
    assert sorted(d["md_key_fallback"] for d in arrests.docs) == [False, False, True]
    assert "md_key_fallback=1" in caplog.text
    assert "ZZSYNTH" not in caplog.text and "md_dedupe_v2:" not in caplog.text


def test_two_people_on_the_same_bookdate_do_not_collide(monkeypatch):
    recs = _snapshot(monkeypatch, [
        _row(1, "g1", name="ZZSYNTH, ALPHA", dob=DOB_A),
        _row(2, "g2", name="ZZSYNTH, ALPHA", dob=DOB_B),   # same name, different DOB
        _row(3, "g3", name="ZZSYNTH, DELTA", dob=DOB_A),   # same DOB, different name
    ])
    arrests = FakeArrests([])
    assert _write(arrests, recs)["new_records"] == 3
    assert len({d["booking_number"] for d in arrests.docs}) == 3


def test_same_person_same_bookdate_twice_merges_conservatively(monkeypatch):
    recs = _snapshot(monkeypatch, [_row(1, "g1", c1="BATTERY"), _row(2, "g2", c1="DUI", c2="BATTERY")])
    assert len(recs) == 1
    assert recs[0].Charges == "BATTERY | DUI"
    arrests = FakeArrests([])
    assert _write(arrests, recs)["new_records"] == 1


def test_key_never_reaches_booking_number_fields(monkeypatch):
    from dashboard.bond_pdf_service import build_osi_field_values, build_palmetto_field_values
    from dashboard.routers.helpers import serialize_doc
    from dashboard.services.docuseal_service import DocuSealService
    from dashboard.services.packet_builder_service import build_adaptive_field_map

    recs = _snapshot(monkeypatch, [_row(1, "g1")])
    arrests = FakeArrests([])
    _write(arrests, recs)
    key = arrests.docs[0]["booking_number"]
    assert recs[0].Booking_Number == ""  # the record never carries the key

    api = serialize_doc(dict(arrests.docs[0]))
    assert api["booking_number_display"] == "" and api["booking_key_internal"] is True

    fields = build_adaptive_field_map({"defendant": {"name": "ZZSYNTH, ALPHA"}, "booking_number": key, "county": "Miami-Dade"})
    assert key not in str(fields) and "md_dedupe" not in str(fields)

    for build in (build_osi_field_values, build_palmetto_field_values):
        values, _sizes = build({"name": "ZZSYNTH, ALPHA", "booking_number": key, "county": "Miami-Dade", "bond_amount": 0})
        assert key not in str(values) and "md_dedupe" not in str(values)

    sent = {}

    class _Svc:
        async def _request(self, method, path, json=None, **_):
            sent.update(json)
            return {}

    asyncio.run(DocuSealService.create_submission(
        _Svc(), template_id=1,
        submitters=[{"role": "Defendant", "email": "x@example.test",
                     "values": {"booking_number": key, "ArrestNumberField": key, "name": "ZZSYNTH"},
                     "fields": [{"name": "booking_number", "default_value": key}]}],
    ))
    assert key not in str(sent) and sent["submitters"][0]["values"]["name"] == "ZZSYNTH"
    assert public_booking_number(key) == "" and public_booking_number("2026-001234") == "2026-001234"
    assert scrub_internal_keys({"a": [key, "keep"]}) == {"a": ["", "keep"]}


def test_other_counties_blank_booking_guard_is_unchanged(monkeypatch):
    assert set(INTERNAL_NATURAL_KEY_SCOPES) == {("FL", "Miami-Dade")}
    rec = _snapshot(monkeypatch, [_row(1, "g1")])[0]
    key = rec.extra_data["md_dedupe"]

    # Same record shape in another county (or state) is still skipped by the writer.
    for county, state in (("Broward", "FL"), ("Lee", "FL"), ("Miami-Dade", "GA")):
        rec.County, rec.State = county, state
        arrests = FakeArrests([])
        stats = _writer(arrests).write_records([rec], county)
        assert stats["skipped_invalid"] == 1 and arrests.docs == [], (county, state)
        assert internal_natural_key(rec) == ""
    rec.County, rec.State = "Miami-Dade", "FL"

    # Miami-Dade with a blank or malformed key is skipped too.
    for bad in ("", "md_dedupe_v1:" + "a" * 64, key[:-1], key.upper(), "{guid}"):
        rec.extra_data["md_dedupe"] = bad
        arrests = FakeArrests([])
        assert _writer(arrests).write_records([rec], "Miami-Dade")["skipped_invalid"] == 1, bad
    rec.extra_data["md_dedupe"] = key

    # A source booking number is still written as-is (the key is not applied).
    rec.Booking_Number = "SRC-1"
    arrests = FakeArrests([])
    _writer(arrests).write_records([rec], "Miami-Dade")
    assert arrests.docs[0]["booking_number"] == "SRC-1" and "booking_key_internal" not in arrests.docs[0]
    rec.Booking_Number = ""

    # BaseScraper's pre-write filter: off by default, so other scrapers still drop blanks.
    assert BaseScraper.ALLOWS_INTERNAL_NATURAL_KEY is False

    class _Other(BaseScraper):
        pass

    assert _Other._filter_records_without_source_booking([rec]) == []
    assert MiamiDadeCountyScraper._filter_records_without_source_booking([rec]) == [rec]
    rec.extra_data["md_dedupe"] = ""
    assert MiamiDadeCountyScraper._filter_records_without_source_booking([rec]) == []


def test_unknown_bond_never_becomes_zero_and_stored_bond_is_kept(monkeypatch):
    arrests = FakeArrests([])
    _write(arrests, _snapshot(monkeypatch, [_row(1, "g1")]))
    assert arrests.docs[0]["bond_amount_raw"] == ""
    # A positive bond already stored (e.g. from a later source) is not blanked
    # by this source's unknown bond: the charges/bond pair guard still applies.
    arrests.docs[0].update({"bond_amount": 1000.0, "bond_amount_raw": "1000"})
    _write(arrests, _snapshot(monkeypatch, [_row(9, "g9")]))
    assert arrests.docs[0]["bond_amount_raw"] == "1000"


def test_report_groups_by_the_runtime_key_so_cleanup_and_runtime_agree(monkeypatch):
    import sys

    sys.path.insert(0, "scripts")
    import miami_dade_dedupe_report as rep

    # Runtime-written doc (internal key) for ALPHA ...
    arrests = FakeArrests([])
    _write(arrests, _snapshot(monkeypatch, [_row(1, "g1")]))
    runtime_doc = arrests.docs[0]
    base = {"county": "Miami-Dade", "state": "FL", "full_name": "ZZSYNTH, ALPHA", "booking_date": "2026-10-08",
            "status": "Unknown", "bond_amount_raw": ""}
    docs = [
        runtime_doc,
        # ... a legacy GlobalID copy WITH a stored DOB and amended charges: same runtime key.
        {**base, "booking_number": "{aaaaaaaa-0000-0000-0000-000000000009}", "dob": "1990-01-02", "charges": "DUI"},
        # legacy copies without a DOB: fallback key (name + date + source charges)
        {**base, "booking_number": "{bbbbbbbb-0000-0000-0000-000000000001}", "charges": "TRESPASS"},
        {**base, "booking_number": "{bbbbbbbb-0000-0000-0000-000000000002}", "charges": "TRESPASS"},
    ]
    today = datetime(2026, 10, 9, 14, tzinfo=timezone.utc)
    out = rep.report(docs, today=today)
    assert out["booking_number_shapes"] == {"internal_natural_key": 1, "arcgis_globalid": 3}
    assert out["rows_keyed_dob_or_internal"] == 2 and out["rows_keyed_fallback"] == 2
    assert out["duplicate_groups"] == 2 and out["rows_that_would_merge"] == 2
    assert out["rows_in_runtime_window_without_dob"] == 2

    index = {("ZZSYNTH, ALPHA", "2026-10-08", "TRESPASS"): {"md_dedupe_v2:" + "1" * 64}}
    out = rep.report(docs, today=today, source_index=index)
    assert out["window_rows_source_dob_lookup"] == {"one_runtime_key": 2}
    assert "ZZSYNTH" not in str(out) and "md_dedupe_v2:" not in str(out)
