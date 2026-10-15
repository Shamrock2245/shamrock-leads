"""Florence SC: honest plain_http identity, published-only bond, no invented custody, visible blocks."""
import inspect

import pytest

from scrapers import plain_http
from scrapers.counties_sc import florence
from scrapers.counties_sc.florence import FlorenceContractError, FlorenceScraper, _HonestSession
from scrapers.scraper_resilience import EgressBlocked
from tests.test_florence_sc_parser import DETAIL_HTML, LIST_HTML, NO_BOND_DETAIL_HTML


def _detail(*rows):
    cells = "".join(
        f'<tr class="dxgvDataRow_X"><td>{c}</td><td>10-05-2026</td><td></td><td>{b}</td><td>T</td><td>A</td></tr>'
        for c, b in rows
    )
    return f"<html><body><table><tr><td>Name ID</td><td>49073</td></tr></table><table>{cells}</table></body></html>"


def test_no_chrome_ua_verify_false_or_in_custody():
    src = inspect.getsource(florence).split('"""', 2)[2]
    for banned in ("Chrome/", "verify=False", "disable_warnings", 'Status="In Custody"', 'or "0"', "curl_cffi"):
        assert banned not in src, banned


def test_honest_session_routes_through_plain_http(monkeypatch):
    seen = {}

    class R:
        status_code, text = 200, ""

    def fake(sess, method, url, county=None, **kw):
        seen.update(method=method, county=county)
        return R()

    monkeypatch.setattr(florence, "plain_request", fake)
    s = _HonestSession()
    assert s.verify is True and s.headers["User-Agent"] == plain_http.HONEST_USER_AGENT
    s.get(florence.PORTAL_URL)
    assert seen == {"method": "GET", "county": "Florence"}


def test_bond_published_only():
    assert FlorenceScraper.parse_detail_html(_detail(("A", "$0.00")))["bond"] == "0"  # published 0 kept
    assert "bond" not in FlorenceScraper.parse_detail_html(_detail(("A", "$500.00"), ("B", "")))  # partial -> unknown
    assert "bond" not in FlorenceScraper.parse_detail_html(NO_BOND_DETAIL_HTML)
    assert FlorenceScraper.parse_detail_html(DETAIL_HTML)["bond"] == "1250"


def test_record_unknown_bond_and_status_blank():
    s = FlorenceScraper()
    row = s._parse_grid(florence.BeautifulSoup(LIST_HTML, "html.parser"))[0]
    rec = s._to_record(row, FlorenceScraper.parse_detail_html(NO_BOND_DETAIL_HTML))
    assert rec.Bond_Amount == "" and rec.Status == ""
    rec0 = s._to_record(row, FlorenceScraper.parse_detail_html(_detail(("A", "$0.00"))))
    assert rec0.Bond_Amount == "0"


def test_letter_block_fails_visibly(monkeypatch):
    def blocked(self, session, letter):
        raise EgressBlocked("egress_block: Florence http_403")

    monkeypatch.setattr(FlorenceScraper, "_search_letter", blocked)
    with pytest.raises(EgressBlocked):
        FlorenceScraper().scrape()


def test_all_letters_failing_is_not_an_empty_success(monkeypatch):
    monkeypatch.setattr(florence, "REQUEST_PAUSE_S", 0)

    def boom(self, session, letter):
        raise RuntimeError("missing ViewState")

    monkeypatch.setattr(FlorenceScraper, "_search_letter", boom)
    with pytest.raises(FlorenceContractError):
        FlorenceScraper().scrape()


def test_detail_block_fails_visibly(monkeypatch):
    monkeypatch.setattr(florence, "REQUEST_PAUSE_S", 0)
    row = {"name": "X, Y", "detail_url": "https://booking.fcso.org/inmate-details?id=1&bid=2"}
    monkeypatch.setattr(FlorenceScraper, "_search_letter", lambda self, s, l: [row] if l == "A" else [])

    def blocked(self, session, url):
        raise EgressBlocked("egress_block: Florence connection_reset")

    monkeypatch.setattr(FlorenceScraper, "_parse_detail", blocked)
    with pytest.raises(EgressBlocked):
        FlorenceScraper().scrape()


def test_docstring_cites_recon():
    assert "SC_FLORENCE_ROSTER_SCOPE_2026-10-15" in florence.__doc__
