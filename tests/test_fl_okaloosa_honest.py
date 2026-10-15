"""Okaloosa FL: honest plain_http, typed blocks, no invented custody, CoS $0 rule kept."""
import inspect
import logging
from datetime import datetime, timedelta

import pytest
import requests

from scrapers.counties import okaloosa
from scrapers.counties.okaloosa import OkaloosaCountyScraper
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import EgressBlocked


def _row(booking, when, status="1"):
    return {"bookingNo": booking, "fullName": "DOE, JANE Q", "custodyDate": when.strftime("%Y-%m-%dT%H:%M:%S"),
            "totalBondAmt": 0, "status": status, "sex": "F", "race": "W", "dobDttm": "1990-01-15T00:00:00",
            "spnNo": 1, "nameID": 1, "height1": 505, "weight1": 120}


def _detail(booking, charges, release=None):
    rows = [{"chargeDesc": "", "charge": "", "bailAmt": None, "bailType": "", **c} for c in charges]
    return {"bookingNo": booking, "charges": rows, "releaseDate": release, "status": "1"}


def _resp(code=200, data=None):
    import json
    r = requests.Response()
    r.status_code = code
    r._content = json.dumps(data).encode() if data is not None else b"denied"
    r.headers["content-type"] = "application/json" if data is not None else "text/html"
    r.url = okaloosa.SEARCH_URL
    return r


@pytest.fixture()
def fake(monkeypatch):
    calls = []

    def install(rows, details, search_code=200, detail_exc=None):
        def _req(sess, method, url, **kw):
            calls.append({"url": url, "kw": kw, "ua": sess.headers.get("User-Agent")})
            if url == okaloosa.SEARCH_URL:
                if search_code != 200:
                    return _resp(search_code)
                return _resp(200, {"total": len(rows), "page": 1, "pageSize": 100, "data": rows})
            if detail_exc is not None:
                raise detail_exc
            return _resp(200, details[url.rsplit("/", 1)[-1]])

        monkeypatch.setattr(requests.Session, "request", _req)
        monkeypatch.setattr(okaloosa.time, "sleep", lambda *_: None)
        return calls

    return install


NOW = datetime.now()


def test_no_chrome_ua():
    assert "Chrome/" not in inspect.getsource(okaloosa)
    assert okaloosa.HEADERS["User-Agent"] == HONEST_USER_AGENT


def test_requests_honest_and_verified(fake):
    calls = fake([_row("2026000001", NOW)], {"2026000001": _detail("2026000001", [{"chargeDesc": "DUI", "bailAmt": 500}])})
    OkaloosaCountyScraper().scrape()
    assert calls and all(c["ua"] == HONEST_USER_AGENT and c["kw"].get("verify") is True for c in calls)
    assert all("impersonate" not in c["kw"] for c in calls)


def test_status_is_blank_never_invented(fake):
    fake([_row("2026000001", NOW)], {"2026000001": _detail("2026000001", [{"chargeDesc": "DUI", "bailAmt": 500}])})
    r = OkaloosaCountyScraper().scrape()[0]
    assert r.Status == "" and "status_source" not in (r.extra_data or {})
    assert 'Status="In Custody"' not in inspect.getsource(okaloosa)


def test_published_release_date_is_recorded(fake):
    fake([_row("2026000001", NOW)], {"2026000001": _detail("2026000001", [{"chargeDesc": "DUI", "bailAmt": 500}],
                                                           release="2026-10-15T08:00:00")})
    r = OkaloosaCountyScraper().scrape()[0]
    assert r.Release_Date == "2026-10-15T08:00:00" and r.Status == ""


def test_status_zero_rows_still_skipped(fake):
    fake([_row("2026000002", NOW, status="0"), _row("2026000001", NOW)],
         {"2026000001": _detail("2026000001", [{"chargeDesc": "DUI", "bailAmt": 500}])})
    assert [r.Booking_Number for r in OkaloosaCountyScraper().scrape()] == ["2026000001"]


def test_cos_zero_bond_is_unpublished():
    d = okaloosa.parse_detail(_detail("2026000001", [{"chargeDesc": "A", "bailAmt": 0}]), "2026000001")
    assert d["bond"] == ""
    d = okaloosa.parse_detail(_detail("2026000001", [{"chargeDesc": "A", "bailAmt": 1000}, {"chargeDesc": "B", "bailAmt": None}]), "2026000001")
    assert d["bond"] == ""
    d = okaloosa.parse_detail(_detail("2026000001", [{"chargeDesc": "A", "bailAmt": 1000}, {"chargeDesc": "B", "bailAmt": 500}]), "2026000001")
    assert d["bond"] == "1500.00"


def test_no_charges_means_empty_charges():
    d = okaloosa.parse_detail(_detail("2026000001", []), "2026000001")
    rec = okaloosa.build_record(_row("2026000001", NOW), d)
    assert rec.Charges == "" and rec.Bond_Amount == ""


def test_only_source_booking_keys():
    assert okaloosa.build_record(_row("ABC", NOW), None) is None
    assert okaloosa.build_record(_row("2026000001", NOW), None).Booking_Number == "2026000001"


@pytest.mark.parametrize("code", [401, 403, 429])
def test_search_block_raises_typed_not_contract_error(fake, code):
    fake([], {}, search_code=code)
    with pytest.raises(EgressBlocked):
        OkaloosaCountyScraper().scrape()


@pytest.mark.parametrize("exc", [EgressBlocked("egress_block: Okaloosa connection_reset: x"), SourceTLSInvalid("tls_cert_invalid: x")])
def test_detail_block_raises(fake, exc):
    fake([_row("2026000001", NOW)], {}, detail_exc=exc)
    with pytest.raises(type(exc)):
        OkaloosaCountyScraper().scrape()


def test_logs_have_no_names(fake, caplog):
    fake([_row("2026000002", NOW, status="0"), _row("2026000001", NOW)],
         {"2026000001": _detail("2026000001", [{"chargeDesc": "DUI", "bailAmt": 500}])})
    with caplog.at_level(logging.DEBUG, logger=okaloosa.logger.name):
        OkaloosaCountyScraper().scrape()
    assert not any("DOE" in r.getMessage() or "1990" in r.getMessage() for r in caplog.records)
