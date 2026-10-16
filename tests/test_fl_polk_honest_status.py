"""Polk: status only from a published release/status field; never default In Custody."""
import inspect

from scrapers.counties import polk
from scrapers.counties.polk import PolkCountyScraper, published_status


class _Resp:
    status_code = 404
    text = ""


def _record(monkeypatch, row):
    monkeypatch.setattr(polk, "request", lambda *a, **k: _Resp())
    return PolkCountyScraper()._scrape_booking_details(object(), row)


BASE = {"BookingNum": "2026000001", "FullName": "DOE, JOHN", "RS": "WM", "EntryDate": "10/12/2026"}


def test_no_in_custody_default_in_source():
    assert 'status = "In Custody"' not in inspect.getsource(polk)


def test_helper():
    assert published_status({}) == ""
    assert published_status({"Status": " "}) == ""
    assert published_status({"CustodyStatus": "IN JAIL"}) == "IN JAIL"
    assert published_status({"Status": "IN JAIL"}, "10/13/2026") == "Released"


def test_no_release_no_status_is_blank(monkeypatch):
    rec = _record(monkeypatch, dict(BASE))
    assert rec.Status == ""
    assert rec.Bond_Amount == ""  # unknown bond stays unknown (published-only)


def test_release_date_gives_released(monkeypatch):
    rec = _record(monkeypatch, BASE | {"ReleaseDate": "10/13/2026"})
    assert rec.Status == "Released" and rec.Release_Date == "2026-10-13"


def test_published_status_kept(monkeypatch):
    assert _record(monkeypatch, BASE | {"Status": "In Custody"}).Status == "In Custody"
