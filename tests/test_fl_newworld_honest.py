"""fl_newworld (Flagler, Walton): honest plain_http, published-only status, typed blocks."""
import inspect

import pytest
import requests

from scrapers import fl_newworld as nw
from scrapers.counties.flagler import FlaglerCountyScraper
from scrapers.counties.walton import WaltonCountyScraper
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import EgressBlocked

BASE = "https://x.example/NewWorld.InmateInquiry/P/"
FORM_CHECKED = '<input type="checkbox" name="InCustody" value="true" checked="checked"><input type="hidden" name="InCustody" value="false">'
FORM_UNCHECKED = '<input type="checkbox" name="InCustody" value="true"><input type="hidden" name="InCustody" value="false">'


def listing(rows, column=True, form=FORM_CHECKED):
    head = "<tr><th>Name</th>" + ("<th>In Custody</th>" if column else "") + "</tr>"
    body = "".join(
        f'<tr><td class="Name"><a href="/NewWorld.InmateInquiry/P/Inmate/Detail/-{i}">DOE, J{i}</a></td>'
        + (f'<td class="InCustody">{c}</td>' if column else "") + "</tr>"
        for i, c in rows
    )
    return f"<html>{form}<table>{head}{body}</table></html>"


def detail(bk="2026-00001234", release="", bond="$1,500.00", charges=("DUI",)):
    ch = "".join(f'<tr><td class="ChargeDescription">{c}</td></tr>' for c in charges)
    return f"""<html><li class="Name"><span>DOE, JANE</span></li><h2>Booking History</h2>
<div class="Booking"><h3><label>Booking</label><span>{bk}</span></h3>
<li class="BookingDate"><span>10/5/2026 9:41 PM</span></li><li class="ReleaseDate"><span>{release}</span></li>
<li class="TotalBondAmount"><span>{bond}</span></li><div class="BookingCharges"><table>{ch}</table></div></div></html>"""


def _resp(code=200, body=""):
    r = requests.Response()
    r.status_code = code
    r._content = body.encode()
    r.headers["content-type"] = "text/html"
    r.url = BASE
    return r


@pytest.fixture()
def fake(monkeypatch):
    calls = []

    def install(list_html, details, list_code=200, detail_exc=None):
        def _req(sess, method, url, **kw):
            calls.append({"url": url, "kw": kw, "ua": sess.headers.get("User-Agent")})
            if "/Inmate/Detail/" in url:
                if detail_exc is not None:
                    raise detail_exc
                i = int(url.rsplit("-", 1)[1])
                return _resp(200, details.get(i, ""))
            if (kw.get("params") or {}).get("Page"):
                return _resp(200, listing([], column=False))
            return _resp(list_code, list_html if list_code == 200 else "denied")

        monkeypatch.setattr(requests.Session, "request", _req)
        monkeypatch.setattr(nw.time, "sleep", lambda *_: None)
        return calls

    return install


def _scrape(county="Walton"):
    return nw.scrape_newworld_roster(county=county, facility="F", portal_url=BASE.rstrip("/"))


def test_no_chrome_ua_and_honest_session():
    assert "Chrome/" not in inspect.getsource(nw)
    s = nw._HonestSession("Walton")
    assert s.headers["User-Agent"] == HONEST_USER_AGENT and s.verify is True


def test_requests_are_honest_and_verified(fake):
    calls = fake(listing([(1, "Yes")]), {1: detail()})
    _scrape()
    assert all(c["ua"] == HONEST_USER_AGENT and c["kw"].get("verify") is True for c in calls)
    assert all("impersonate" not in c["kw"] for c in calls)


def test_walton_column_yes_sets_in_custody_with_source(fake):
    fake(listing([(1, "Yes")]), {1: detail()})
    r = _scrape()[0]
    assert r.Status == "In Custody" and r.extra_data["status_source"] == "published_in_custody_column"


def test_column_no_gives_blank_status(fake):
    fake(listing([(1, "No")]), {1: detail()})
    r = _scrape()[0]
    assert r.Status == "" and "status_source" not in (r.extra_data or {})


def test_flagler_filter_echo_sets_in_custody_with_source(fake):
    fake(listing([(1, None)], column=False), {1: detail()})
    r = _scrape("Flagler")[0]
    assert r.Status == "In Custody" and r.extra_data["status_source"] == "published_in_custody_filter"


def test_no_column_and_no_filter_echo_gives_blank(fake):
    fake(listing([(1, None)], column=False, form=FORM_UNCHECKED), {1: detail()})
    r = _scrape("Flagler")[0]
    assert r.Status == "" and "status_source" not in (r.extra_data or {})


def test_released_booking_never_emitted_even_if_column_yes(fake):
    fake(listing([(1, "Yes")]), {1: detail(release="10/6/2026 1:00 PM")})
    with pytest.raises(RuntimeError):  # every row dropped -> parse drift, never a guessed record
        _scrape()


def test_detail_to_record_default_status_is_blank():
    rec = nw.detail_to_record(detail(), county="Walton", facility="F", detail_url="u")
    assert rec.Status == "" and "status_source" not in (rec.extra_data or {})


def test_bond_and_charges_published_only(fake):
    fake(listing([(1, "Yes"), (2, "Yes"), (3, "Yes")]),
         {1: detail(bk="2026-00000001", bond="", charges=()),
          2: detail(bk="2026-00000002", bond="$0.00"),
          3: detail(bk="2026-00000003", bond="$2,500.00")})
    recs = {r.Booking_Number: r for r in _scrape()}
    assert recs["2026-00000001"].Bond_Amount == "" and recs["2026-00000001"].Charges == ""
    assert recs["2026-00000002"].Bond_Amount == "0.00"
    assert recs["2026-00000003"].Bond_Amount == "2500.00"


def test_only_source_booking_keys(fake):
    fake(listing([(1, "Yes"), (2, "Yes")]), {1: detail(bk="History"), 2: detail(bk="2026-00000777")})
    assert [r.Booking_Number for r in _scrape()] == ["2026-00000777"]


@pytest.mark.parametrize("code", [401, 403, 429])
def test_roster_block_raises(fake, code):
    fake("", {}, list_code=code)
    with pytest.raises(EgressBlocked):
        _scrape()


@pytest.mark.parametrize("exc", [EgressBlocked("egress_block: x HTTP 403"), SourceTLSInvalid("tls_cert_invalid: x")])
def test_detail_block_not_swallowed(fake, exc):
    fake(listing([(1, "Yes")]), {}, detail_exc=exc)
    with pytest.raises(type(exc)):
        _scrape()


def test_detail_transport_error_log_has_no_url_or_name(fake, caplog, monkeypatch):
    fake(listing([(1, "Yes"), (2, "Yes")]), {2: detail(bk="2026-00000002")}, detail_exc=None)
    import logging

    def boom(sess, method, url, **kw):
        if url.endswith("-1"):
            raise requests.ConnectionError("refused DOE")
        return _resp(200, detail(bk="2026-00000002") if "/Detail/" in url else listing([(1, "Yes"), (2, "Yes")]))

    import scrapers.fl_newworld as m
    monkeypatch.setattr(requests.Session, "request", boom)
    with caplog.at_level(logging.DEBUG, logger=m.logger.name):
        recs = _scrape()
    assert [r.Booking_Number for r in recs] == ["2026-00000002"]
    assert not any("DOE" in r.getMessage() or "/Detail/" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("cls", [FlaglerCountyScraper, WaltonCountyScraper])
def test_county_wrappers_use_honest_base(cls, fake):
    fake(listing([(1, "Yes")]), {1: detail()})
    recs = cls().scrape()
    assert recs and recs[0].Booking_Number == "2026-00001234"
