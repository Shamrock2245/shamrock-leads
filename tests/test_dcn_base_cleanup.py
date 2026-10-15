"""DCN base: honest identity, published-only bond, no invented status, bid-only keys."""
import inspect

import pytest
import requests

from scrapers import dcn_base, plain_http
from scrapers.counties.desoto import DeSotoCountyScraper
from scrapers.counties_nc.carteret import CarteretScraper
from scrapers.counties_nc.halifax import HalifaxScraper
from scrapers.counties_nc.lee import LeeScraper
from scrapers.counties_nc.moore import MooreScraper
from scrapers.counties_nc.richmond import RichmondScraper
from scrapers.counties_sc.darlington import DarlingtonScraper
from scrapers.scraper_resilience import EgressBlocked

AFFECTED = (
    CarteretScraper, LeeScraper, HalifaxScraper, RichmondScraper, MooreScraper,
    DarlingtonScraper, DeSotoCountyScraper,
)

ROSTER = (
    '<table>'
    '<tr id="gvInmates_DXDataRow0"><td><a href="/DCN/inmate-details?id=PERSON1&bid=BID001">DOE, JOHN</a></td>'
    '<td>30</td><td>W</td><td>M</td><td>10/01/2026</td></tr>'
    '<tr id="gvInmates_DXDataRow1"><td><a href="/DCN/inmate-details?id=PERSON2">ROE, JANE</a></td>'
    '<td>31</td><td>B</td><td>F</td><td>10/02/2026</td></tr>'
    '</table>'
)


def _detail_html(bond_cells):
    rows = "".join(
        f'<tr id="ChargeGrid_DXDataRow{i}"><td>CHARGE {i}</td><td></td><td>{b}</td><td>SURETY</td></tr>'
        for i, b in enumerate(bond_cells)
    )
    return f"<html><body><table>{rows}</table></body></html>"


@pytest.mark.parametrize("cls", AFFECTED)
def test_affected_county_uses_dcn_base(cls):
    assert issubclass(cls, dcn_base.DCNBaseScraper)


def test_honest_headers_and_verified_session():
    assert dcn_base.DEFAULT_HEADERS["User-Agent"] == plain_http.HONEST_USER_AGENT
    assert "Chrome/" not in dcn_base.DEFAULT_HEADERS["User-Agent"]
    s = dcn_base.DCNBaseScraper.__new__(CarteretScraper)
    sess = s._make_session()
    assert sess.verify is True
    assert sess.headers["User-Agent"] == plain_http.HONEST_USER_AGENT


@pytest.mark.parametrize("cls", AFFECTED)
def test_require_source_bid_and_no_synthetic(cls):
    assert cls.require_source_bid is True
    rows = cls()._parse_roster(ROSTER, "https://example.invalid")
    assert [r["booking"] for r in rows] == ["BID001"]
    assert "PERSON1" not in [r["booking"] for r in rows]
    assert not any(str(r["booking"]).startswith("DCN_") for r in rows)


@pytest.mark.parametrize("bonds,want", [
    (["$1,000.00", "$500.00"], "1500"),
    (["$0.00"], "0"),
    (["NO BOND"], ""),
    ([""], ""),
    (["$100.00", "HOLD"], ""),
])
def test_base_bond_published_only(bonds, want):
    s = CarteretScraper()
    s._http_get = lambda session, url, **kw: _Resp(_detail_html(bonds))
    info = dcn_base.DCNBaseScraper._fetch_detail(s, object(), "https://example.invalid/x")
    assert info.get("bond", "") == want


@pytest.mark.parametrize("cls", [CarteretScraper, DarlingtonScraper, MooreScraper])
def test_record_never_invents_bond_or_status(cls):
    row = {
        "name": "DOE, JOHN", "first": "JOHN", "middle": "", "last": "DOE",
        "age": "30", "race": "W", "sex": "M", "admit": "10/01/2026",
        "detail_url": "https://example.invalid/x?bid=BID001", "booking": "BID001",
    }
    rec = cls()._to_record(row, {})
    assert rec.Bond_Amount == ""
    assert rec.Status == ""
    assert rec.Booking_Number == "BID001"
    rec0 = cls()._to_record(row, {"bond": "0", "charges": "X"})
    assert rec0.Bond_Amount == "0"


def test_desoto_fail_closed_untouched():
    assert DeSotoCountyScraper.SOURCE_CONTRACT_VALIDATED is False
    assert DeSotoCountyScraper().scrape() == []


def test_http_403_is_egress_block(monkeypatch):
    s = CarteretScraper()
    sess = s._make_session()

    def boom(*a, **k):
        raise EgressBlocked("egress_block: Carteret HTTP 403")

    monkeypatch.setattr(plain_http, "request", boom)
    with pytest.raises(EgressBlocked, match="egress_block"):
        s._http_get(sess, s.inmates_url)


def test_source_has_no_chrome_or_synthetic():
    src = inspect.getsource(dcn_base)
    assert "Chrome/" not in src.split('"""', 2)[-1]
    assert "_synthetic_booking" not in src
    assert "hashlib" not in src
    assert 'Status="In Custody"' not in src
    assert 'or "0"' not in src.split("Bond_Amount", 1)[-1][:200]


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status
        self.headers = {"content-type": "text/html"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.status_code)
