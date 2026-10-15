"""Orange (FL): honest plain_http identity; status only from source (never invented In Custody)."""
import inspect

import pytest

from scrapers import plain_http
from scrapers.counties import orange
from scrapers.counties.orange import published_status
from scrapers.scraper_resilience import EgressBlocked
from tests.test_fl_orange_bestjail import _detail
from datetime import datetime


def test_no_chrome_ua_or_in_custody_default():
    body = inspect.getsource(orange).split('"""', 2)[-1]
    assert "Chrome/" not in body
    assert 'Status="In Custody"' not in body
    assert "requests.Session" not in body


def test_session_is_honest():
    s = plain_http.make_session(orange.HEADERS)
    assert s.headers["User-Agent"] == plain_http.HONEST_USER_AGENT and s.verify is True


def test_published_status_blank_without_field():
    detail = _detail("26000001", datetime(2026, 10, 7, 21, 5))[0]
    assert published_status(detail) == ""
    assert published_status(detail | {"STATUS": "Released"}) == "Released"


def test_build_record_status_blank():
    booked = datetime(2026, 10, 7, 21, 5)
    rec = orange.build_record(
        "26000001", "X", _detail("26000001", booked)[0],
        orange.parse_charges([{"Charge": "BATTERY", "BondAmount": "0.00"}]),
    )
    assert rec is not None
    assert rec.Status == ""
    assert rec.Bond_Amount == "0.00"  # published 0 kept


def test_roster_block_raises_egress(monkeypatch):
    monkeypatch.setattr(orange.plain_http, "make_session", lambda *a, **k: object())
    monkeypatch.setattr(
        orange.plain_http, "request",
        lambda *a, **k: (_ for _ in ()).throw(EgressBlocked("egress_block: Orange HTTP 403")),
    )
    monkeypatch.setattr(orange.time, "sleep", lambda *_: None)
    with pytest.raises(EgressBlocked, match="egress_block"):
        orange.OrangeCountyScraper().scrape(lookback_days=7)
