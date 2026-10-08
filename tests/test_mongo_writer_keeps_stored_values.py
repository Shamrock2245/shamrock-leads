"""MongoWriter never blanks stored charges / bond with an empty scrape (2026-10-08).

The upsert ``$set``s the whole doc, so an empty ``charges`` or bond from one run
(a source ``charges: []``, a missed detail) used to overwrite the stored values.
Now charges and bond are kept as one pair: if either scraped side is empty while
the stored side has a value, the stored pair stays, so a doc never mixes old
charges with a new blank bond or the reverse. Otherwise every published value,
including a real "0", replaces the stored one. A stored zero bond is not protected,
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


def test_unknown_bond_replaces_a_stored_zero_when_charges_are_published():
    arrests = FakeArrests([_stored("B1", "Orange", bond_raw="0", bond=0.0)])
    _writer(arrests).write_records([_rec("B1", charges="DUI", bond="")], "Orange")
    doc = arrests.one("B1")
    # A stored zero is not protected (see module docstring): unknown replaces it.
    assert (doc["charges"], doc["bond_amount_raw"]) == ("DUI", "")


def test_new_rows_keep_their_empty_values():
    arrests = FakeArrests([])
    _writer(arrests).write_records([_rec("B2", charges="", bond="")], "Orange")
    assert (arrests.one("B2")["charges"], arrests.one("B2")["bond_amount_raw"]) == ("", "")


def test_failed_stored_read_raises_instead_of_writing_blind():
    class _Broken(FakeArrests):
        def find(self, query, projection=None):
            raise RuntimeError("mongo read failed")

    arrests = _Broken([_stored("B1", "Orange")])
    with pytest.raises(RuntimeError, match="mongo read failed"):
        _writer(arrests).write_records([_rec("B1", charges="", bond="")], "Orange")
    assert arrests.sets == []  # nothing was written


def test_empty_charges_keep_the_stored_bond_too_even_with_a_new_bond():
    arrests = FakeArrests([_stored("B1", "Orange")])
    _writer(arrests).write_records([_rec("B1", charges="", bond="2500")], "Orange")
    doc = arrests.one("B1")
    assert (doc["charges"], doc["bond_amount_raw"], doc["bond_amount"]) == ("BATTERY", "1500", 1500.0)


def test_empty_bond_keeps_the_stored_charges_too_even_with_new_charges():
    arrests = FakeArrests([_stored("B1", "Orange")])
    _writer(arrests).write_records([_rec("B1", charges="DUI", bond="")], "Orange")
    doc = arrests.one("B1")
    assert (doc["charges"], doc["bond_amount_raw"]) == ("BATTERY", "1500")


def test_charge_details_and_bond_type_travel_with_the_kept_pair():
    stored = _stored("B1", "Orange")
    stored.update(charge_details=[{"charge": "BATTERY", "bond_amount": 1500.0}], bond_type="SURETY")
    arrests = FakeArrests([stored])
    rec = _rec("B1", charges="", bond="")
    rec.extra_data = {"charge_details": [{"charge": "", "bond_amount": None}]}
    _writer(arrests).write_records([rec], "Orange")
    doc = arrests.one("B1")
    assert doc["charge_details"] == [{"charge": "BATTERY", "bond_amount": 1500.0}]
    assert doc["bond_type"] == "SURETY"


def test_published_zero_with_charges_replaces_the_stored_pair():
    arrests = FakeArrests([_stored("B1", "Orange")])
    _writer(arrests).write_records([_rec("B1", charges="TRESPASS", bond="0")], "Orange")
    doc = arrests.one("B1")
    assert (doc["charges"], doc["bond_amount_raw"], doc["bond_amount"]) == ("TRESPASS", "0", 0.0)


def test_skip_log_names_fields_and_booking_key_only(caplog):
    arrests = FakeArrests([_stored("B1", "Orange")])
    rec = _rec("B1", charges="", bond="")
    rec.Full_Name = "ZZTESTNAME, PERSON"
    with caplog.at_level("INFO", logger="writers.mongo_writer"):
        _writer(arrests).write_records([rec], "Orange")
    kept = [r.getMessage() for r in caplog.records if "kept stored charges/bond pair" in r.getMessage()]
    assert kept and "key=FL/Orange/B1" in kept[0] and "reason=empty_pair" in kept[0]
    assert "charges" in kept[0] and "bond_amount_raw" in kept[0]
    assert not any("ZZTESTNAME" in r.getMessage() for r in caplog.records)


def _kept_logs(caplog):
    return [r.getMessage() for r in caplog.records if "kept stored charges/bond pair" in r.getMessage()]


def test_partial_pair_reason_when_charges_empty_and_bond_published(caplog):
    arrests = FakeArrests([_stored("B1", "Orange")])
    with caplog.at_level("INFO", logger="writers.mongo_writer"):
        _writer(arrests).write_records([_rec("B1", charges="", bond="2500")], "Orange")
    doc = arrests.one("B1")
    assert (doc["charges"], doc["bond_amount_raw"]) == ("BATTERY", "1500")
    logs = _kept_logs(caplog)
    assert len(logs) == 1 and "reason=partial_pair" in logs[0] and "key=FL/Orange/B1" in logs[0]
    assert "protected=charges" in logs[0]


def test_partial_pair_reason_when_bond_empty_and_charges_published(caplog):
    arrests = FakeArrests([_stored("B1", "Orange")])
    with caplog.at_level("INFO", logger="writers.mongo_writer"):
        _writer(arrests).write_records([_rec("B1", charges="DUI", bond="")], "Orange")
    doc = arrests.one("B1")
    assert (doc["charges"], doc["bond_amount_raw"]) == ("BATTERY", "1500")
    logs = _kept_logs(caplog)
    assert len(logs) == 1 and "reason=partial_pair" in logs[0] and "protected=bond" in logs[0]


def test_nothing_stored_on_the_empty_side_writes_the_incoming_values(caplog):
    # Stored charges empty; incoming charges empty, bond published: nothing to protect.
    arrests = FakeArrests([_stored("B1", "Orange", charges="", bond_raw="1500", bond=1500.0),
                           _stored("B2", "Orange", charges="BATTERY", bond_raw="", bond=0.0)])
    with caplog.at_level("INFO", logger="writers.mongo_writer"):
        _writer(arrests).write_records(
            [_rec("B1", charges="", bond="2500"), _rec("B2", charges="DUI", bond="")], "Orange"
        )
    assert (arrests.one("B1")["charges"], arrests.one("B1")["bond_amount_raw"]) == ("", "2500")
    # Stored bond empty; incoming bond empty, charges published: write the new charges.
    assert (arrests.one("B2")["charges"], arrests.one("B2")["bond_amount_raw"]) == ("DUI", "")
    assert _kept_logs(caplog) == []
