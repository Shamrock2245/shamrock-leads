"""Pitt NC: honest plain_http identity; no invented charges/bond/status; real booking keys only."""
import inspect

import pytest
from bs4 import BeautifulSoup

from scrapers import plain_http
from scrapers.counties_nc import pitt
from scrapers.counties_nc.pitt import PittScraper, _HonestSession, parse_grid, status_fields
from scrapers.scraper_resilience import EgressBlocked

HEAD = "<tr><th></th><th>Last Name</th><th>Suffix</th><th>First Name</th><th>Middle Name</th><th>Date of Birth</th><th>Booking Number</th><th>Gender</th><th>Race</th>{extra}</tr>"
ROW = ("<tr><td><a href=\"javascript:__doPostBack('x','Select$0')\">Select</a></td>"
       "<td>DOE</td><td></td><td>JOHN</td><td></td><td>01/01/1990</td><td>{bk}</td><td>M</td><td>W</td>{extra}</tr>")


def _soup(rows, extra_head=""):
    return BeautifulSoup(f'<table id="{pitt.GRID_ID}">{HEAD.format(extra=extra_head)}{rows}</table>', "html.parser")


def test_no_chrome_or_invented_defaults_in_source():
    src = inspect.getsource(pitt).split('"""', 2)[2]
    for banned in ("Chrome/", "verify=False", 'Status="In Custody"', 'Charges="Unknown"', 'Bond_Amount="0"', "curl_cffi"):
        assert banned not in src, banned


def test_honest_session_routes_through_plain_http(monkeypatch):
    seen = {}

    class R:
        status_code, text = 200, ""

    monkeypatch.setattr(pitt, "plain_request", lambda sess, m, u, county=None, **kw: seen.update(m=m, county=county) or R())
    s = _HonestSession()
    assert s.verify is True and s.headers["User-Agent"] == plain_http.HONEST_USER_AGENT
    s.get(pitt.PORTAL_URL)
    assert seen == {"m": "GET", "county": "Pitt"}


def test_record_unknowns_are_blank():
    row = parse_grid(_soup(ROW.format(bk="123456", extra="")))[0]
    rec = PittScraper()._to_record(row)
    assert (rec.Charges, rec.Bond_Amount, rec.Status) == ("", "", "")
    assert rec.extra_data["status_source"] == ""
    assert rec.Booking_Number == "123456"


def test_published_status_wins_if_column_appears():
    row = parse_grid(_soup(ROW.format(bk="123456", extra="<td>IN JAIL</td>"), "<th>Status</th>"))[0]
    rec = PittScraper()._to_record(row)
    assert rec.Status == "IN JAIL" and rec.extra_data["status_source"] == "published_status"
    assert status_fields({}) == ("", "")


@pytest.mark.parametrize("bk", ["", "AB123", "12", "PITT_1"])
def test_only_real_source_booking_keys(bk):
    assert parse_grid(_soup(ROW.format(bk=bk, extra=""))) == []


def test_block_raises(monkeypatch):
    def blocked(self, url, **kw):
        raise EgressBlocked("egress_block: Pitt connection_reset")

    monkeypatch.setattr(_HonestSession, "get", blocked)
    with pytest.raises(EgressBlocked):
        PittScraper().scrape()


def test_docstring_cites_recon():
    assert "NC_PITT_ROSTER_SCOPE_2026-10-15" in pitt.__doc__
