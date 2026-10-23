"""Duval: status only from a published release or status field; never invented In Custody."""
import inspect

from scrapers.counties import duval
from scrapers.counties.duval import DuvalCountyScraper, published_status


def _scraper():
    return DuvalCountyScraper.__new__(DuvalCountyScraper)


def _rec(entry):
    return _scraper()._extract_from_api({"data": [entry]})


BASE = {"name": "DOE, J", "bookingNumber": "2026000001"}


def test_no_in_custody_literal_in_source():
    src = inspect.getsource(duval)
    assert 'else "In Custody"' not in src
    assert 'Status="In Custody"' not in src


def test_published_status_helper():
    assert published_status({}) == ""
    assert published_status({"status": "  "}) == ""
    assert published_status({"custodyStatus": "IN JAIL"}) == "IN JAIL"
    assert published_status({"status": "IN JAIL"}, "10/01/2026") == "Released"


def test_api_no_fields_blank_status():
    recs = _rec(dict(BASE))
    assert len(recs) == 1 and recs[0].Status == ""


def test_api_release_date_released():
    rec = _rec(BASE | {"releaseDate": "10/01/2026"})[0]
    assert rec.Status == "Released" and rec.Release_Date == "10/01/2026"


def test_api_published_status_kept():
    assert _rec(BASE | {"inmateStatus": "In Custody"})[0].Status == "In Custody"


def test_dom_path_blank_status():
    html = "<table><tr><td>Doe, John</td><td>Booking Number: 2026000001</td><td>Bond: $1,500.00</td></tr></table>"
    recs = _scraper()._parse_dom(html)
    assert recs and all(r.Status == "" for r in recs)


def test_docstring_cites_recon():
    assert "FL_DUVAL_ROSTER_SCOPE_2026-10-15" in duval.__doc__
