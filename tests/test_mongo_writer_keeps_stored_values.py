"""MongoWriter never blanks stored charges / bond with an empty scrape (2026-10-08).

The upsert ``$set``s the whole doc, so an empty ``charges`` or bond from one run
(a source ``charges: []``, a missed detail) used to overwrite the stored values.
Now an empty value never replaces a non-empty stored one, while any published
value (including a real "0") still does. A stored zero bond is not protected,
because the 2026-10 sweep showed most historic scraped "0" values were invented.
Synthetic data only.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.models import ArrestRecord
from scrapers.counties import okaloosa, orange
from tests.test_fl_okaloosa_api import _detail as _oka_detail
from tests.test_fl_okaloosa_api import _row as _oka_row
from tests.test_fl_orange_bestjail import _detail as _orange_detail
from tests.test_staff_edits_survive_rescrape import FakeArrests, _writer


def _stored(booking, county, *, charges="BATTERY", bond_raw="1500", bond=1500.0):
    return {
        "state": "FL", "county": county, "booking_number": booking, "full_name": "DOE, JANE",
        "charges": charges, "bond_amount": bond, "bond_amount_raw": bond_raw, "status": "In Custody",
    }


def test_orange_source_empty_charges_list_keeps_stored_charges_and_bond():
    rec = orange.build_record("26000001", "DOE, JANE Q", _orange_detail("26000001", datetime(2026, 10, 7, 9))[0],
                              orange.parse_charges([]))
    assert (rec.Charges, rec.Bond_Amount) == ("", "")  # what the scraper emits for charges: []
    arrests = FakeArrests([_stored("26000001", "Orange")])
    _writer(arrests).write_records([rec], "Orange")
    doc = arrests.one("26000001")
    assert (doc["charges"], doc["bond_amount_raw"], doc["bond_amount"]) == ("BATTERY", "1500", 1500.0)
    assert doc["status"] == "In Custody"  # the rest of the scrape still lands


def test_okaloosa_detail_with_no_charges_keeps_stored_charges_and_bond():
    detail = okaloosa.parse_detail(_oka_detail("2026000001", []), "2026000001")
    rec = okaloosa.build_record(_oka_row("2026000001", datetime(2026, 10, 7, 21, 5), bond=2500.0), detail)
    assert (rec.Charges, rec.Bond_Amount) == ("", "")
    arrests = FakeArrests([_stored("2026000001", "Okaloosa", charges="DUI", bond_raw="2500", bond=2500.0)])
    _writer(arrests).write_records([rec], "Okaloosa")
    doc = arrests.one("2026000001")
    assert (doc["charges"], doc["bond_amount_raw"]) == ("DUI", "2500")


def _rec(booking, *, charges, bond):
    return ArrestRecord(Booking_Number=booking, County="Orange", State="FL", Full_Name="DOE, JANE",
                        Charges=charges, Bond_Amount=bond, Status="In Custody")


def test_published_value_replaces_stored_value():
    arrests = FakeArrests([_stored("B1", "Orange")])
    _writer(arrests).write_records([_rec("B1", charges="DUI | RESIST", bond="2500")], "Orange")
    doc = arrests.one("B1")
    assert (doc["charges"], doc["bond_amount_raw"], doc["bond_amount"]) == ("DUI | RESIST", "2500", 2500.0)


def test_published_zero_replaces_stored_value():
    arrests = FakeArrests([_stored("B1", "Orange")])
    _writer(arrests).write_records([_rec("B1", charges="DUI", bond="0")], "Orange")
    doc = arrests.one("B1")
    assert (doc["bond_amount_raw"], doc["bond_amount"]) == ("0", 0.0)


def test_unknown_replaces_a_stored_zero_but_new_rows_keep_their_empty_values():
    arrests = FakeArrests([_stored("B1", "Orange", bond_raw="0", bond=0.0)])
    _writer(arrests).write_records([_rec("B1", charges="", bond=""), _rec("B2", charges="", bond="")], "Orange")
    assert arrests.one("B1")["bond_amount_raw"] == ""  # stored zero is not protected (see module docstring)
    assert arrests.one("B1")["charges"] == "BATTERY"
    assert (arrests.one("B2")["charges"], arrests.one("B2")["bond_amount_raw"]) == ("", "")


def test_failed_stored_read_raises_instead_of_writing_blind():
    class _Broken(FakeArrests):
        def find(self, query, projection=None):
            raise RuntimeError("mongo read failed")

    arrests = _Broken([_stored("B1", "Orange")])
    with pytest.raises(RuntimeError, match="mongo read failed"):
        _writer(arrests).write_records([_rec("B1", charges="", bond="")], "Orange")
    assert arrests.sets == []  # nothing was written
