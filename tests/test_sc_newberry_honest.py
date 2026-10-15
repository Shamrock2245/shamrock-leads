"""Newberry SC: honest plain_http identity, published-only bond/status, visible failures."""
import inspect

import pytest

from scrapers import plain_http
from scrapers.counties_sc import newberry
from scrapers.counties_sc.newberry import NewberryContractError, NewberryScraper, _HonestSession
from scrapers.scraper_resilience import EgressBlocked

PDF = "https://www.newberrycounty.gov/departments/sheriff-s-office/x.pdf"
TEXT = """
Prisoners/Charges Booked In by Date Range10/06/2026 Page 1
09/28/2026
DOE, JOHN - SO-0000001 - 30
 Released 09/29/2026 - BOND POSTED
TRAFFIC / RECKLESS DRIVING
ROE, JANE - NP-0000002 - 40
SHOPLIFTING / SHOPLIFTING, VALUE $2,000 OR LESS
Bond $0.00
POE, AL - HP-0000003 - 22
BURGLARY / BURGLARY 2ND
Bond $5,000.00
"""


def _recs(text=TEXT):
    return {r.Booking_Number: r for r in NewberryScraper()._parse_pdf_text(text, PDF)}


def test_no_chrome_verify_false_or_in_custody_default():
    src = inspect.getsource(newberry).split('"""', 2)[2]
    for banned in ("Chrome/", "verify=False", "disable_warnings", 'Status="In Custody"', 'bond_amount = "0"', "curl_cffi"):
        assert banned not in src, banned


def test_honest_session_routes_through_plain_http(monkeypatch):
    seen = {}

    class R:
        status_code, text = 200, ""

    monkeypatch.setattr(newberry, "plain_request", lambda sess, m, u, county=None, **kw: seen.update(m=m, county=county) or R())
    s = _HonestSession()
    assert s.verify is True and s.headers["User-Agent"] == plain_http.HONEST_USER_AGENT
    s.get(newberry.PORTAL_URL)
    assert seen == {"m": "GET", "county": "Newberry"}


def test_published_release_line_gives_released_not_booking_date():
    r = _recs()["SO-0000001"]
    assert r.Status == "Released" and r.Release_Date == "09/29/2026"
    assert r.extra_data["status_source"] == "published_release_line"
    assert r.Booking_Date == "09/28/2026"  # section date, never the release date


def test_no_release_line_is_blank_never_in_custody():
    for bk in ("NP-0000002", "HP-0000003"):
        r = _recs()[bk]
        assert r.Status == "" and r.extra_data["status_source"] == ""


def test_release_date_never_backfills_booking_date():
    text = "DOE, JOHN - SO-0000009 - 30\n Released 09/29/2026 - SELF\n"
    r = _recs(text)["SO-0000009"]
    assert r.Booking_Date == "" and r.Status == "Released"


def test_bond_published_only():
    recs = _recs()
    assert recs["SO-0000001"].Bond_Amount == ""  # "BOND POSTED" is a label; no $ line
    assert recs["NP-0000002"].Bond_Amount == "0.00"  # printed $0.00 kept
    assert recs["HP-0000003"].Bond_Amount == "5000.00"


def test_legacy_layout_unknown_bond_and_status():
    text = "TESTER, PERSON\nBooked 08/12/2026\nSO# ABC-123\nPetit Larceny\n"
    r = _recs(text)["SO-ABC-123"]
    assert r.Bond_Amount == "" and r.Status == ""


def test_page_block_raises(monkeypatch):
    def blocked(self, url, **kw):
        raise EgressBlocked("egress_block: Newberry http_403")

    monkeypatch.setattr(_HonestSession, "get", blocked)
    with pytest.raises(EgressBlocked):
        NewberryScraper().scrape()


def test_pdf_block_raises(monkeypatch):
    class Page:
        status_code, text = 200, f'<a href="{PDF}">x</a>'

        def raise_for_status(self):
            pass

    def get(self, url, **kw):
        if url.endswith(".pdf"):
            raise EgressBlocked("egress_block: Newberry connection_reset")
        return Page()

    monkeypatch.setattr(_HonestSession, "get", get)
    with pytest.raises(EgressBlocked):
        NewberryScraper().scrape()


def test_missing_pdf_is_not_an_empty_success(monkeypatch):
    class Page:
        status_code, text = 200, "<html>no pdf</html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(_HonestSession, "get", lambda self, url, **kw: Page())
    with pytest.raises(NewberryContractError):
        NewberryScraper().scrape()


def test_docstring_cites_recon():
    assert "SC_NEWBERRY_ROSTER_SCOPE_2026-10-15" in newberry.__doc__
