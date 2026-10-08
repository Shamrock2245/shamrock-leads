"""Pinellas (FL) bond honesty, 2026-10-08.

Live check (box, plain headless Chrome, 2026-10-06/07 bookings, 50 modals):
Bond Assessed is published per charge, including real $0.00 values (33 of
82 cells). Those stay "0". An unread modal, a booking with no Bond Assessed,
or any charge whose Bond Assessed is blank/non-numeric leaves the total ""
(unknown), never $0. Synthetic fixtures only; no network.
"""
from __future__ import annotations

from dashboard.services.packet_builder_service import arrest_bond_value
from scrapers.counties.pinellas import PinellasCountyScraper as P


def _charge(desc, bond):
    return f"Agency Report Number:\n1\nOffense Description:\n{desc}\nCourt Case Number:\n26-1-MM\nBond Assessed:\n{bond}\nBond Amount Due:\n{bond}\nCharge Status:\nAWAITING TRIAL\n"


def _modal(*charges):
    return "Subject Charge Report\nCharges\n" + "".join(_charge(d, b) for d, b in charges) + "Close Window\n"


def test_total_only_when_every_charge_publishes():
    assert P.parse_charge_report_text(_modal(("BATTERY", "$1,500.00"), ("DUI", "")))["bond_amount"] == ""
    assert P.parse_charge_report_text(_modal(("BATTERY", "$1,500.00"), ("DUI", "$250.00")))["bond_amount"] == "1750"
    assert P.parse_charge_report_text(_modal(("BATTERY", ""), ("DUI", "")))["bond_amount"] == ""
    assert P.parse_charge_report_text(_modal(("BATTERY", "$0.00"), ("DUI", "$1,500.00")))["bond_amount"] == "1500"


def test_published_zero_kept_and_text_values_unknown():
    assert P.parse_charge_report_text(_modal(("DOMESTIC BATTERY", "$0.00")))["bond_amount"] == "0"
    assert P.parse_charge_report_text(_modal(("VOP", "NO BOND")))["bond_amount"] == ""
    assert P.parse_charge_report_text(_modal(("VOP", "HOLD"), ("DUI", "$500.00")))["bond_amount"] == ""


def test_unread_modal_or_no_bond_line_is_unknown():
    assert P.parse_charge_report_text("")["bond_amount"] == ""
    assert P.parse_charge_report_text("Subject Charge Report\nOffense Description:\nDUI\n")["bond_amount"] == ""
    assert P._format_bond_amount(None) == "" and P._format_bond_amount("") == ""
    assert P._format_bond_amount("abc") == ""
    assert P._format_bond_amount("$0.00") == "0" and P._format_bond_amount(0) == "0"
    assert P._format_bond_amount("$2,500.50") == "2500.50"


def test_roster_only_record_is_unknown_and_published_zero_record_is_zero():
    rec = P()._row_to_record({"name": "DOE, JOHN", "booking_num": "2600000001", "charge": "BATTERY"})
    assert rec.Bond_Amount == ""
    rec0 = P()._row_to_record({"name": "DOE, JOHN", "booking_num": "2600000002", "charge": "BATTERY", "bond_amount": "0"})
    assert rec0.Bond_Amount == "0"


def test_pinellas_zero_stays_a_known_zero_on_hydrate():
    # Pinellas publishes real $0.00, so a stored "0" is not reclassified as unknown.
    assert arrest_bond_value({"county": "Pinellas", "state": "FL", "bond_amount": 0.0, "bond_amount_raw": "0"}) == "0"
    assert arrest_bond_value({"county": "Pinellas", "state": "FL", "bond_amount": 0.0, "bond_amount_raw": ""}) == ""


class _Btn:
    def count(self):
        return 1

    def is_disabled(self):
        return True

    def click(self, *a, **k):
        pass


class _Page:
    def get_by_role(self, *a, **k):
        return _Btn()

    def fill(self, *a, **k):
        pass

    def locator(self, *a, **k):
        class _L:
            first = None
        return _L()

    def wait_for_selector(self, *a, **k):
        return True


def _scrape_date_with(monkeypatch, rows, modals):
    import scrapers.counties.pinellas as mod

    s = P()
    s._modal_attempts = s._modal_failures = 0
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(P, "_extract_rows", lambda self, page: [dict(r) for r in rows])
    monkeypatch.setattr(P, "_read_detail_modal", lambda self, page, bn: modals.get(bn))
    page = _Page()
    page.get_by_role = lambda *a, **k: _Btn()
    page.locator = lambda *a, **k: type("L", (), {"first": type("F", (), {"check": lambda self, **k: None})()})()
    recs = s._scrape_date(page, "2026-10-07", set())
    return s, recs


def test_unread_modal_skips_the_booking_instead_of_writing_blanks(monkeypatch):
    rows = [{"name": "DOE, JANE", "booking_num": "2600000001", "charge": "BATT"},
            {"name": "DOE, JOHN", "booking_num": "2600000002", "charge": "DUI"}]
    modals = {"2600000002": P.parse_charge_report_text(_modal(("DUI", "$500.00")))}
    s, recs = _scrape_date_with(monkeypatch, rows, modals)
    assert [r.Booking_Number for r in recs] == ["2600000002"]  # 0001 not emitted at all
    assert recs[0].Bond_Amount == "500" and recs[0].Charges == "DUI"
    assert (s._modal_attempts, s._modal_failures) == (2, 1)
    s._check_modal_failures()  # one failure of two: warning only


def test_every_modal_failing_fails_the_run(monkeypatch):
    import pytest

    rows = [{"name": "DOE, JANE", "booking_num": "2600000001", "charge": "BATT"}]
    s, recs = _scrape_date_with(monkeypatch, rows, {})
    assert recs == []
    with pytest.raises(RuntimeError):
        s._check_modal_failures()
