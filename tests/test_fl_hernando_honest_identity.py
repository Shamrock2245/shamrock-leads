"""Hernando: honest plain_http identity, no roster In Custody default, blocks fail visibly."""
import inspect

import pytest

from scrapers import plain_http
from scrapers.counties import hernando
from scrapers.counties.hernando import HernandoCountyScraper, _HonestSession
from scrapers.scraper_resilience import EgressBlocked
from tests.test_fl_hernando_detail_status import _Resp, _detail, _row

BK = "HCSO26JBN000001"


def test_no_chrome_ua_or_impersonation():
    src = inspect.getsource(hernando).split('"""', 2)[2]
    for banned in ("Chrome/", "curl_cffi", "impersonate", "verify=False", "requests.get(", "requests.post("):
        assert banned not in src, banned
    assert "User-Agent" not in hernando.HEADERS


def test_honest_session_routes_through_plain_http(monkeypatch):
    seen = {}

    def fake(sess, method, url, county=None, **kw):
        seen.update(method=method, url=url, county=county)
        return _Resp("ok")

    monkeypatch.setattr(hernando, "plain_request", fake)
    s = _HonestSession()
    assert s.verify is True and s.headers["User-Agent"] == plain_http.HONEST_USER_AGENT
    s.get("https://www.hernandosheriff.org/x")
    assert seen == {"method": "GET", "url": "https://www.hernandosheriff.org/x", "county": "Hernando"}


def test_roster_rows_have_no_custody_default():
    src = inspect.getsource(HernandoCountyScraper._parse)
    assert 'Status="In Custody"' not in src
    assert 'Status=""' in src


class _BlockSession:
    def get(self, url, **kw):
        raise EgressBlocked("egress_block: Hernando http_403")


def test_detail_block_fails_run_visibly(monkeypatch):
    monkeypatch.setattr(hernando, "REQUEST_PAUSE_S", 0)
    with pytest.raises(EgressBlocked):
        HernandoCountyScraper()._enrich_from_details(_BlockSession(), [_row(BK)])


def test_recheck_block_raises_for_worker(monkeypatch):
    monkeypatch.setattr(_HonestSession, "get", lambda self, url, **kw: _BlockSession().get(url))
    with pytest.raises(EgressBlocked):
        HernandoCountyScraper().fetch_bond_recheck(BK)


def test_recheck_still_works_on_published_detail(monkeypatch):
    monkeypatch.setattr(_HonestSession, "get", lambda self, url, **kw: _Resp(_detail(booking=BK)))
    rec = HernandoCountyScraper().fetch_bond_recheck(BK)
    assert rec is not None and rec.Booking_Number == BK
    assert rec.Status in ("In Custody", "Released")  # from the published Release Date/Time only


def test_docstring_cites_recon():
    assert "FL_HERNANDO_ROSTER_SCOPE_2026-10-15" in hernando.__doc__
