"""Staff bond / charge edits survive every rescrape, in every county.

Covers the central protection (core/staff_edits.py) through each write path
that puts source data onto an existing ``arrests`` doc: the shared
MongoWriter (every county via BaseScraper.run, including an SSW and a
SmartWEB county parser), the First Appearance watcher, the Lee jail refresh,
the bookmarklet merge, and the staff edit endpoints that record provenance.
All data is synthetic.
"""
from __future__ import annotations

import asyncio
import re
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.models import ArrestRecord
from core.staff_edits import (
    merge_charge_rows,
    protect_scraped_update,
    staff_bond_marker,
    staff_bond_value,
    staff_charges_marker,
    staff_provenance,
    staff_rows_from_text,
)


# ── In-memory arrests collection (sync for writers, async for the dashboard) ──
def _get(doc, dotted):
    cur = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def _matches(doc, query):
    for key, expected in (query or {}).items():
        if key == "$or":
            if not any(_matches(doc, q) for q in expected):
                return False
            continue
        if key == "$and":
            if not all(_matches(doc, q) for q in expected):
                return False
            continue
        actual, present = _get(doc, key)
        if isinstance(expected, dict):
            if "$exists" in expected and present != bool(expected["$exists"]):
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$regex" in expected and not re.search(expected["$regex"], str(actual or ""),
                                                      re.I if "i" in expected.get("$options", "") else 0):
                return False
            if "$nin" in expected and actual in expected["$nin"]:
                return False
            continue
        if actual != expected:
            return False
    return True


def _apply_set(doc, set_doc):
    for key, val in set_doc.items():
        parts = key.split(".")
        cur = doc
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = deepcopy(val)


class FakeArrests:
    def __init__(self, docs=()):
        self.docs = [deepcopy(d) for d in docs]
        self.sets = []

    def find(self, query, projection=None):
        return [deepcopy(d) for d in self.docs if _matches(d, query)]

    def bulk_write(self, ops, ordered=False):
        upserted = []
        for i, op in enumerate(ops):
            self._update(op._filter, op._doc, upsert=op._upsert, index=i, upserted=upserted)
        return SimpleNamespace(
            upserted_count=len(upserted), modified_count=len(ops) - len(upserted),
            bulk_api_result={"upserted": [{"index": i} for i in upserted]},
        )

    def _update(self, flt, update, *, upsert=False, index=0, upserted=None):
        self.sets.append(update.get("$set", {}))
        for doc in self.docs:
            if _matches(doc, flt):
                _apply_set(doc, update.get("$set", {}))
                return 1
        if upsert:
            doc = {k: v for k, v in flt.items() if not isinstance(v, dict)}
            _apply_set(doc, update.get("$setOnInsert", {}))
            _apply_set(doc, update.get("$set", {}))
            self.docs.append(doc)
            if upserted is not None:
                upserted.append(index)
        return 0

    def one(self, booking):
        return next(d for d in self.docs if d.get("booking_number") == booking)


class AsyncFake(FakeArrests):
    async def find_one(self, query, projection=None):
        hits = super().find(query)
        return hits[0] if hits else None

    async def update_one(self, flt, update, upsert=False):
        self._update(flt, update, upsert=upsert)
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def update_many(self, flt, update):
        for doc in self.docs:
            if _matches(doc, flt):
                _apply_set(doc, update.get("$set", {}))
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def insert_one(self, doc):
        self.docs.append(deepcopy(doc))
        return SimpleNamespace(inserted_id="x")

    def find(self, query, projection=None):
        items = super().find(query)

        class _Cursor:
            async def to_list(self, length=None):
                return items[: length or len(items)]

            def __iter__(self):
                return iter(items)

        return _Cursor()


def _writer(arrests):
    with patch("writers.mongo_writer.MongoClient"), patch("writers.mongo_writer.settings") as s:
        s.MONGODB_URI, s.MONGODB_DB_NAME = "mongodb://mock", "test"
        from writers.mongo_writer import MongoWriter

        w = MongoWriter(uri="mongodb://mock", db_name="test")
    w.arrests = arrests
    return w


def _rec(booking, *, county="Lee", state="FL", bond="", charges="", details=None, bond_type=""):
    r = ArrestRecord(
        Booking_Number=booking, County=county, State=state, Full_Name="DOE, JANE",
        Bond_Amount=bond, Bond_Type=bond_type, Charges=charges, Status="In Custody",
    )
    if details is not None:
        r.extra_data = {"charge_details": details}
    return r


def _base(booking, *, county="Lee", state="FL", **extra):
    doc = {"booking_number": booking, "county": county, "state": state, "full_name": "DOE, JANE"}
    doc.update(extra)
    return doc


# ── Unit: provenance + merge ──────────────────────────────────────────────────
def test_staff_zero_is_a_known_zero():
    doc = _base("B0", bond_amount=0.0, **{"staff_edits": {"bond": {"amount": 0.0}}})
    prov = staff_provenance(doc)
    assert prov.bond and prov.bond_amount == 0.0
    assert staff_bond_value(doc) == 0.0


def test_legacy_markers_inferred_without_backfill():
    assert staff_provenance(_base("B1", bond_override=True, bond_amount=2500.0)).bond
    assert staff_provenance(_base("B1", last_checked_mode="MANUAL_CHARGE_BONDS", bond_amount=10.0)).bond
    # Legacy flag with a zero amount is ambiguous (old rescrapes zeroed staff values).
    assert not staff_provenance(_base("B1", bond_override=True, bond_amount=0.0)).bond
    manual = _base("B1", last_checked_mode="MANUAL_CHARGE_BONDS",
                   charge_details=[{"charge": "A", "bond_amount": 5.0}], bond_amount=5.0)
    assert staff_provenance(manual).charges
    assert staff_provenance(_base("B1", bond_amount=500.0)).any is False


def test_no_staff_edits_passes_through_unchanged():
    set_doc = {"bond_amount": 1000.0, "charges": "X", "charge_details": [{"charge": "X"}]}
    out, prov = protect_scraped_update(set_doc, _base("B2", bond_amount=500.0))
    assert out == set_doc and not prov.any


def test_merge_rows_adds_new_scraped_skips_removed_and_baseline():
    existing = [{"charge": "BATTERY RENAMED BY STAFF", "source": "staff"}]
    prov = staff_provenance(_base("B3", staff_edits={"charges": {
        "removed": ["TRESPASS"], "baseline": ["BATTERY", "TRESPASS"]}}))
    scraped = [{"charge": "BATTERY"}, {"charge": "TRESPASS"}, {"charge": "RESISTING"}]
    merged = merge_charge_rows(existing, scraped, prov)
    assert [r["charge"] for r in merged] == ["BATTERY RENAMED BY STAFF", "RESISTING"]
    assert merged[-1]["source"] == "scraped"


def test_staff_charges_marker_tracks_removed_and_baseline():
    existing = _base("B4", charges="A | B", charge_details=[{"charge": "A"}, {"charge": "B"}])
    marker = staff_charges_marker(existing, [{"charge": "A"}, {"charge": "C"}], by="t")["staff_edits.charges"]
    assert marker["removed"] == ["B"] and marker["baseline"] == ["A", "B"]


def test_staff_rows_from_text_keep_case_poa_bond():
    rows = staff_rows_from_text(
        "A | NEW", [{"charge": "A", "bond_amount": 500.0, "case_number": "26CF1", "poa_number": "P1"}]
    )
    assert rows[0]["poa_number"] == "P1" and rows[0]["bond_amount"] == 500.0
    assert rows[1]["bond_amount"] is None and all(r["source"] == "staff" for r in rows)


# ── MongoWriter (every county via BaseScraper.run) ────────────────────────────
def test_writer_staff_bond_override_survives_rescrape():
    marker = staff_bond_marker(7500.0, bond_type="Surety", by="agent")["staff_edits.bond"]
    staff = _base("W1", bond_amount=7500.0, bond_amount_raw="", bond_type="Surety",
                  bond_override=True, staff_edits={"bond": marker})
    arrests = FakeArrests([staff])
    _writer(arrests).write_records([_rec("W1", bond="1000", bond_type="Cash")], "Lee")
    doc = arrests.one("W1")
    assert doc["bond_amount"] == 7500.0 and doc["bond_type"] != "Cash"
    assert doc["scraped_bond_amount"] == 1000.0 and doc["scraped_bond_type"] == "Cash"
    assert staff_bond_value(doc) == 7500.0


def test_writer_staff_zero_survives_rescrape_with_scraped_bond():
    arrests = FakeArrests([_base("W0", bond_amount=0.0, staff_edits={"bond": {"amount": 0.0, "type": "ROR"}})])
    _writer(arrests).write_records([_rec("W0", bond="25000")], "Lee")
    doc = arrests.one("W0")
    assert doc["bond_amount"] == 0.0 and doc["scraped_bond_amount"] == 25000.0

    from dashboard.services.packet_builder_service import arrest_bond_value

    assert arrest_bond_value(doc) == 0.0  # known $0, not unknown


def test_writer_legacy_bond_override_survives_and_gets_durable_marker():
    arrests = FakeArrests([_base("W2", bond_amount=3000.0, bond_override=True)])
    _writer(arrests).write_records([_rec("W2", bond="")], "Lee")  # source shows no bond
    doc = arrests.one("W2")
    assert doc["bond_amount"] == 3000.0
    assert doc["staff_edits"]["bond"]["amount"] == 3000.0
    assert doc["staff_edits"]["bond"]["inferred_from"] == "bond_override"


def test_writer_per_charge_manual_bonds_survive_rescrape():
    rows = [{"charge": "BATTERY", "bond_amount": 2000.0, "case_number": "26MM1", "poa_number": "OSI 1"},
            {"charge": "TRESPASS", "bond_amount": 500.0, "case_number": "26MM2"}]
    arrests = FakeArrests([_base("W3", bond_amount=2500.0, charges="BATTERY | TRESPASS",
                                 charge_details=rows, last_checked_mode="MANUAL_CHARGE_BONDS")])
    scraped = [{"charge": "BATTERY", "bond_amount": 0.0}, {"charge": "TRESPASS", "bond_amount": 0.0}]
    _writer(arrests).write_records([_rec("W3", bond="0", charges="BATTERY | TRESPASS", details=scraped)], "Lee")
    doc = arrests.one("W3")
    assert doc["bond_amount"] == 2500.0
    assert [(r["charge"], r["bond_amount"]) for r in doc["charge_details"]] == [("BATTERY", 2000.0), ("TRESPASS", 500.0)]
    assert doc["charge_details"][0]["poa_number"] == "OSI 1"
    assert doc["scraped_charge_details"] == [{"charge": "BATTERY", "bond_amount": 0.0}, {"charge": "TRESPASS", "bond_amount": 0.0}]
    assert doc["staff_edits"]["charges"]["inferred_from"] == "MANUAL_CHARGE_BONDS"


def test_writer_staff_edited_row_kept_when_scraped_charges_change():
    staff_rows = [{"charge": "BATTERY (STAFF WORDING)", "bond_amount": 1500.0, "case_number": "26MM9",
                   "poa_number": "P9", "source": "staff"}]
    arrests = FakeArrests([_base("W4", charges="BATTERY (STAFF WORDING)", charge_details=staff_rows,
                                 staff_edits={"charges": {"removed": [], "baseline": ["BATTERY"]}})])
    scraped = [{"charge": "BATTERY - AMENDED"}, {"charge": "BATTERY"}]
    _writer(arrests).write_records([_rec("W4", charges="BATTERY - AMENDED | BATTERY", details=scraped)], "Lee")
    doc = arrests.one("W4")
    assert doc["charge_details"][0] == staff_rows[0]
    # The amended charge is new on the source -> added; the baseline one is not re-added.
    assert [r["charge"] for r in doc["charge_details"]] == ["BATTERY (STAFF WORDING)", "BATTERY - AMENDED"]
    assert doc["charge_details"][1]["source"] == "scraped"
    assert doc["charges"] == "BATTERY (STAFF WORDING) | BATTERY - AMENDED"
    assert doc["scraped_charges"] == "BATTERY - AMENDED | BATTERY"


def test_writer_staff_added_charge_survives_and_removed_charge_stays_removed():
    rows = [{"charge": "DUI", "source": "staff"}, {"charge": "STAFF ADDED VOP", "bond_amount": 0.0, "source": "staff"}]
    arrests = FakeArrests([_base("W5", charge_details=rows, charges="DUI | STAFF ADDED VOP",
                                 staff_edits={"charges": {"removed": ["NO DL"], "baseline": ["DUI", "NO DL"]}})])
    _writer(arrests).write_records([_rec("W5", charges="DUI | NO DL")], "Lee")
    doc = arrests.one("W5")
    assert [r["charge"] for r in doc["charge_details"]] == ["DUI", "STAFF ADDED VOP"]
    assert doc["charge_details"][1]["bond_amount"] == 0.0  # staff $0 row stays $0


def test_writer_new_scraped_charge_added_when_staff_untouched_that_row():
    rows = [{"charge": "DUI", "bond_amount": 1000.0, "source": "staff"}]
    arrests = FakeArrests([_base("W6", charge_details=rows, charges="DUI",
                                 staff_edits={"charges": {"removed": [], "baseline": ["DUI"]}})])
    _writer(arrests).write_records([_rec("W6", charges="DUI | FLEEING", bond="3000")], "Lee")
    doc = arrests.one("W6")
    assert [(r["charge"], r.get("source")) for r in doc["charge_details"]] == [("DUI", "staff"), ("FLEEING", "scraped")]
    assert doc["bond_amount"] == 3000.0  # no staff bond -> the bond still refreshes


def test_writer_no_staff_edits_updates_normally():
    arrests = FakeArrests([_base("W7", bond_amount=500.0, charges="OLD", charge_details=[{"charge": "OLD"}])])
    _writer(arrests).write_records([_rec("W7", bond="900", charges="NEW", details=[{"charge": "NEW"}])], "Lee")
    doc = arrests.one("W7")
    assert doc["bond_amount"] == 900.0 and doc["charges"] == "NEW"
    assert doc["charge_details"] == [{"charge": "NEW"}]
    assert "scraped_bond_amount" not in doc and "staff_edits" not in doc


def test_writer_scope_is_state_and_county():
    # Same booking in another county / state never borrows staff provenance.
    arrests = FakeArrests([
        _base("W8", county="Lee", state="GA", bond_amount=4000.0, staff_edits={"bond": {"amount": 4000.0}}),
        _base("W8", county="Lee", state="FL", bond_amount=100.0),
    ])
    _writer(arrests).write_records([_rec("W8", bond="200")], "Lee")
    fl = next(d for d in arrests.docs if d["state"] == "FL")
    assert fl["bond_amount"] == 200.0


def test_ssw_county_writer_path_keeps_staff_edits():
    from scrapers.counties_nc.henderson import HendersonScraper

    card = """
<div class="card booking-card"><div class="card-body">
  <div class="booking-header"><h5 class="mb-0">DOE, JANE Q</h5></div>
  <div>Booked: 10/01/2026</div>
  <div>Bond Total: $5,000.00</div>
  <div class="charge-item">1 MISDEMEANOR LARCENY Bond: $5,000.00</div>
  <img class="booking-mugshot" data-bookingid="4242" data-json-nameid="9999" alt="Booking photo" />
  <a href="../../bookingdetails/index.php?BookingID=4242&AgencyID=HendersonCoNC">View Full Details</a>
</div></div>"""
    records = HendersonScraper()._parse_booking_cards(card)
    assert len(records) == 1
    booking = records[0].Booking_Number
    rows = [{"charge": "LARCENY (STAFF)", "bond_amount": 2500.0, "poa_number": "P1", "source": "staff"}]
    arrests = FakeArrests([_base(booking, county="Henderson", state="NC", bond_amount=2500.0,
                                 charge_details=rows, charges="LARCENY (STAFF)",
                                 staff_edits={"bond": {"amount": 2500.0},
                                              "charges": {"removed": [], "baseline": [records[0].Charges]}})])
    _writer(arrests).write_records(records, "Henderson")
    doc = arrests.one(booking)
    assert doc["bond_amount"] == 2500.0 and doc["scraped_bond_amount"] == 5000.0
    assert doc["charge_details"] == rows


def test_smartweb_county_writer_path_keeps_staff_edits():
    from scrapers import fl_smartweb

    html = """<table>
<tr class="InmateRecordRow"><td><img src='ViewImage.aspx?bookno=BCSO26JBN000777'></td>
  <td><table><thead><tr><td class="SearchHeader">DOE, JANE Q &nbsp; (W/ FEMALE )</td></tr></thead>
  <tbody><tr><td>Status: In Jail</td></tr>
    <tr><td>Booking No: BCSO26JBN000777</td></tr>
    <tr><td>Booking Date: 10/07/2026 11:12 AM</td></tr></tbody></table></td></tr>
<tr><td><table class="JailViewCharges"><tr class="SearchHeader"><td>CHARGES</td></tr>
<tr><td></td><td>810.08.2a</td><td>C1</td><td>TRESPASSING</td><td>M</td><td>2</td><td>$250.00</td><td></td></tr>
<tr><td></td><td>843.02</td><td>C2</td><td>RESIST OFFICER</td><td>M</td><td>1</td><td>$500.00</td><td></td></tr>
</table></td></tr></table>"""
    records = fl_smartweb._parse_html(html, set(), county="Bradford", facility="Bradford County Jail",
                                      detail_url="http://example.test/jail.aspx")
    assert len(records) == 1 and records[0].Bond_Amount
    first, second = [c.strip() for c in records[0].Charges.split("|")]
    staff_rows = [{"charge": first, "bond_amount": 0.0, "case_number": "26MM5", "source": "staff"}]
    arrests = FakeArrests([_base("BCSO26JBN000777", county="Bradford", bond_amount=0.0,
                                 charges=first, charge_details=staff_rows,
                                 staff_edits={"bond": {"amount": 0.0},
                                              "charges": {"removed": [], "baseline": [first]}})])
    _writer(arrests).write_records(records, "Bradford")
    doc = arrests.one("BCSO26JBN000777")
    assert doc["bond_amount"] == 0.0 and doc["scraped_bond_amount"] > 0  # staff $0 kept
    assert doc["charge_details"][0] == staff_rows[0]
    assert [r["charge"] for r in doc["charge_details"]] == [first, second]  # new source charge added


# ── First Appearance watcher ─────────────────────────────────────────────────
def _fa_watcher(arrests, candidates, refetched):
    from core.first_appearance_watcher import FirstAppearanceWatcher

    w = FirstAppearanceWatcher.__new__(FirstAppearanceWatcher)
    from scoring.lead_scorer import LeadScorer

    w._writers, w._scrapers, w._scorer, w._slack = [], {}, LeadScorer(), None
    w._mongo_client, w._db, w._arrests = None, None, arrests
    w._query_candidates = lambda: candidates
    w._refetch_record = lambda doc: refetched
    w._get_slack = lambda: None
    return w


def test_first_appearance_watcher_keeps_staff_zero_bond():
    staff = _base("F1", bond_amount=0.0, staff_edits={"bond": {"amount": 0.0, "type": "ROR"}})
    arrests = FakeArrests([staff])
    stats = _fa_watcher(arrests, [deepcopy(staff)], _rec("F1", bond="15000")).run()
    doc = arrests.one("F1")
    assert doc["bond_amount"] == 0.0 and doc["scraped_bond_amount"] == 15000.0
    assert stats["bond_set"] == 0


def test_first_appearance_watcher_still_sets_bond_without_staff_edits():
    plain = _base("F2", bond_amount=0.0)
    arrests = FakeArrests([plain])
    stats = _fa_watcher(arrests, [deepcopy(plain)], _rec("F2", bond="15000")).run()
    assert arrests.one("F2")["bond_amount"] == 15000.0 and stats["bond_set"] == 1


# ── Lee jail refresh (lee_clerk_watch) ───────────────────────────────────────
def test_lee_jail_refresh_keeps_staff_bond_and_rows():
    from dashboard.services import lee_clerk_watch

    rows = [{"charge": "DUI", "bond_amount": 1000.0, "poa_number": "P1", "source": "staff"}]
    doc = _base("L1", bond_amount=1000.0, charges="DUI", charge_details=rows,
                staff_edits={"bond": {"amount": 1000.0}, "charges": {"removed": [], "baseline": ["DUI"]}},
                detail_url="https://example.test/booking/L1")
    arrests = AsyncFake([doc])

    async def fake_ingest(url):
        return {"success": True, "data": {"bond_amount": 0.0, "charges": "DUI | NO DL", "status": "In Custody"}}

    with patch.object(lee_clerk_watch, "get_collection", return_value=arrests), \
         patch("dashboard.services.url_ingest_service.ingest_url", fake_ingest):
        res = asyncio.run(lee_clerk_watch._refresh_jail_source(deepcopy(doc)))
    out = arrests.one("L1")
    assert res["staff_bond_kept"] is True
    assert out["bond_amount"] == 1000.0 and out["scraped_bond_amount"] == 0.0
    assert [r["charge"] for r in out["charge_details"]] == ["DUI", "NO DL"]
    assert out["charge_details"][0]["poa_number"] == "P1"


# ── Bookmarklet merge (source page) ──────────────────────────────────────────
def test_booking_extract_merge_keeps_staff_bond_and_rows():
    from dashboard.services import booking_extract_merge as bem

    rows = [{"charge": "POSSESSION (STAFF)", "bond_amount": 2000.0, "poa_number": "P2", "source": "staff"}]
    doc = _base("1029767", _id="a1", full_name="PERKINS, MICHAEL JAMES", bond_amount=2000.0,
                charges="POSSESSION (STAFF)", charge_details=rows,
                staff_edits={"bond": {"amount": 2000.0}, "charges": {"removed": [], "baseline": []}})
    cols = {"arrests": AsyncFake([doc]), "active_bonds": AsyncFake(), "audit_events": AsyncFake()}
    payload = {
        "county": "Lee", "defendantFullName": "PERKINS, MICHAEL JAMES", "bookingNumber": "1029767",
        "charges": [{"description": "POSSESS CONTROLLED SUBSTANCE", "bondAmount": "5000"}],
    }
    with patch.object(bem, "get_collection", side_effect=lambda n: cols[n]):
        res = asyncio.run(bem.merge_booking_extract(payload, actor="t"))
    out = cols["arrests"].one("1029767")
    assert res["total_bond"] == 2000.0
    assert out["bond_amount"] == 2000.0 and out["scraped_bond_amount"] == 5000.0
    assert out["charge_details"][0] == rows[0]
    assert out["charge_details"][1]["charge"] == "POSSESS CONTROLLED SUBSTANCE"


# ── Staff edit endpoints record provenance ───────────────────────────────────
def _request(body):
    async def _json():
        return body

    return SimpleNamespace(json=_json)


def _legacy(cols):
    from dashboard.routers import legacy

    return patch.object(legacy, "get_collection", side_effect=lambda n: cols.setdefault(n, AsyncFake()))


def test_update_bond_amount_records_staff_zero_then_survives_rescrape():
    from dashboard.routers import legacy

    cols = {"arrests": AsyncFake([_base("E1", bond_amount=5000.0)])}
    with _legacy(cols):
        asyncio.run(legacy.update_bond_amount(_request({"booking_number": "E1", "bond_amount": 0})))
    doc = cols["arrests"].one("E1")
    assert doc["staff_edits"]["bond"]["amount"] == 0.0 and doc["bond_override"] is True
    arrests = FakeArrests([doc])
    _writer(arrests).write_records([_rec("E1", bond="5000")], "Lee")
    assert arrests.one("E1")["bond_amount"] == 0.0


def test_update_charge_bonds_marks_rows_and_blank_stays_unknown():
    from dashboard.routers import legacy

    start = _base("E2", bond_amount=0.0, charges="A | B",
                  charge_details=[{"charge": "A", "poa_number": "P1"}, {"charge": "B"}])
    cols = {"arrests": AsyncFake([start])}
    with _legacy(cols):
        asyncio.run(legacy.update_charge_bonds(_request({
            "booking_number": "E2",
            "charge_details": [{"charge": "A", "bond_amount": 1500, "case_number": "26CF1"},
                               {"charge": "C", "bond_amount": None}],
        })))
    doc = cols["arrests"].one("E2")
    assert all(r["source"] == "staff" for r in doc["charge_details"])
    assert doc["charge_details"][0]["poa_number"] == "P1"  # modal omits POA -> kept
    assert doc["charge_details"][1]["bond_amount"] is None  # blank stays unknown
    assert doc["staff_edits"]["bond"]["amount"] == 1500.0
    assert doc["staff_edits"]["charges"]["removed"] == ["B"]

    blank = _base("E3", bond_amount=800.0, charges="A", charge_details=[{"charge": "A"}])
    cols = {"arrests": AsyncFake([blank])}
    with _legacy(cols):
        asyncio.run(legacy.update_charge_bonds(_request({
            "booking_number": "E3", "charge_details": [{"charge": "A", "bond_amount": ""}]})))
    doc = cols["arrests"].one("E3")
    assert doc["bond_amount"] == 800.0  # all-blank rows never write a $0 bond
    assert "bond" not in doc["staff_edits"]


def test_update_lead_details_marks_bond_and_charges():
    from dashboard.routers import legacy

    cols = {"arrests": AsyncFake([_base("E4", bond_amount=100.0, charges="A",
                                        charge_details=[{"charge": "A", "case_number": "26MM1"}])])}
    with _legacy(cols):
        asyncio.run(legacy.update_lead_details(_request({
            "booking_number": "E4", "bond_amount": "2,500", "charges": "A | STAFF ADDED"})))
    doc = cols["arrests"].one("E4")
    assert doc["staff_edits"]["bond"]["amount"] == 2500.0
    assert [r["charge"] for r in doc["charge_details"]] == ["A", "STAFF ADDED"]
    assert doc["charge_details"][0]["case_number"] == "26MM1"
    arrests = FakeArrests([doc])
    _writer(arrests).write_records([_rec("E4", bond="100", charges="A")], "Lee")
    after = arrests.one("E4")
    assert after["bond_amount"] == 2500.0
    assert [r["charge"] for r in after["charge_details"]] == ["A", "STAFF ADDED"]


# ── Hydrate reads staff values first ─────────────────────────────────────────
def test_hydrate_reads_staff_rows_after_rescrape():
    from dashboard.services.packet_builder_service import arrest_bond_value, charge_details_from_sources

    rows = [{"charge": "DUI", "bond_amount": 1000.0, "case_number": "26MM1", "poa_number": "P1", "source": "staff"}]
    arrests = FakeArrests([_base("H1", bond_amount=1000.0, charges="DUI", charge_details=rows,
                                 staff_edits={"bond": {"amount": 1000.0}, "charges": {"removed": [], "baseline": ["DUI"]}})])
    _writer(arrests).write_records([_rec("H1", bond="0", charges="DUI", details=[{"charge": "DUI", "bond_amount": 0.0}])], "Lee")
    doc = arrests.one("H1")
    assert arrest_bond_value(doc) == 1000.0
    hydrated = charge_details_from_sources(arrest=doc)
    assert hydrated[0]["bond_amount"] == 1000.0


# ── Scheduler custody recheck (live roster) ──────────────────────────────────
class _SyncColl(FakeArrests):
    def update_one(self, flt, update, upsert=False):
        self._update(flt, update, upsert=upsert)

    def insert_one(self, doc):
        self.docs.append(deepcopy(doc))

    def delete_many(self, flt):
        return None


def _recheck(arrests, record):
    from core.scheduler import ScraperScheduler

    sched = ScraperScheduler.__new__(ScraperScheduler)
    sched._writers = []
    db = {"arrests": arrests, "custody_rechecks": _SyncColl(), "scraper_triggers": _SyncColl()}
    scraper = SimpleNamespace(_fetch_single_booking=lambda bk, url: record)
    sched._handle_custody_recheck(db, {"_id": "t1", "county": "Lee", "mode": "single",
                                       "booking_number": record.Booking_Number}, scraper)
    return db


def test_custody_recheck_keeps_staff_bond_and_rows():
    rows = [{"charge": "DUI", "bond_amount": 1000.0, "poa_number": "P1", "source": "staff"}]
    arrests = _SyncColl([_base("S1", bond_amount=1000.0, bond_type="Surety", charges="DUI", status="In Custody",
                               charge_details=rows,
                               staff_edits={"bond": {"amount": 1000.0, "type": "Surety"},
                                            "charges": {"removed": [], "baseline": ["DUI"]}})])
    _recheck(arrests, _rec("S1", bond="5000", bond_type="Cash", charges="DUI | NO DL"))
    doc = arrests.one("S1")
    assert doc["bond_amount"] == 1000.0 and doc["scraped_bond_amount"] == 5000.0
    assert [r["charge"] for r in doc["charge_details"]] == ["DUI", "NO DL"]
    assert doc["charge_details"][0]["poa_number"] == "P1"


def test_custody_recheck_updates_normally_and_never_blanks_unknowns():
    arrests = _SyncColl([_base("S2", bond_amount=700.0, bond_type="Surety", charges="DUI", status="In Custody")])
    _recheck(arrests, _rec("S2", bond="", charges="DUI | FLEEING"))
    doc = arrests.one("S2")
    assert doc["charges"] == "DUI | FLEEING"
    assert doc["bond_amount"] == 700.0 and doc["bond_type"] == "Surety"  # source blank -> unchanged
    assert doc["status"] == "In Custody"
