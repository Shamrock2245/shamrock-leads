"""Volusia (FL): status only from the published InCustody / Release Date cells, typed errors,
nothing swallowed (docs/recon/FL_VOLUSIA_HONEST_2026-10-22.md). Synthetic data, no network."""
from __future__ import annotations

import inspect

import pytest
import requests

import scrapers.counties.volusia as v
from scrapers.counties.volusia import VolusiaCountyScraper, VolusiaHTTPError, parse_recent_bookings, status_fields
from scrapers.plain_http import SourceTLSInvalid
from scrapers.scraper_resilience import (
    ERROR_NETWORK, ERROR_PARSE_DRIFT, ERROR_URL_CHANGED, EgressBlocked, ParseDriftError, classify_exception,
)

HDR = "".join(f"<th>{h}</th>" for h in ("Booking #", "Photo", "Inmate ID", "Last Name", "First Name", "Middle",
                                       "Suffix", "Sex", "Race", "Booking Date", "Release Date", "InCustody"))


def _row(booking, rid, release="", custody="Y"):
    cells = [booking, f'<a href="Details.aspx?InmateRID={rid}">x</a>', "P1", "DOE", "JANE", "", "", "F", "W",
             "10/21/2026 09:15", release, custody]
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def _list(*rows):
    return f"<html><table><tr>{HDR}</tr>{''.join(rows)}<tr><td>1 2</td></tr></table></html>"


FORM = '<html><input name="__VIEWSTATE" value="vs"><input name="__EVENTVALIDATION" value="ev"></html>'
DETAIL = """<html><input id="txtCity" value="Deland"><table>
<tr><th>Charge #</th><th>Statute</th><th>Statute Description</th><th>Bond Type</th><th>Bond Amount</th></tr>
<tr><td>1</td><td>316.193</td><td>DUI</td><td>Surety</td><td>$1,000.00</td></tr>
<tr><td>2</td><td>843.02</td><td>RESIST</td><td>Surety</td><td>$0.00</td></tr></table></html>"""


def _resp(status=200, body=""):
    r = requests.Response()
    r.status_code, r._content = status, body.encode()
    r.headers["content-type"] = "text/html"
    return r


@pytest.fixture()
def http(monkeypatch):
    def _install(handler):
        calls = []

        def fake(sess, method, url, **kw):
            calls.append((method, url))
            out = handler(method, url, len(calls))
            if isinstance(out, BaseException):
                raise out
            return out

        monkeypatch.setattr(requests.Session, "request", fake)
        return calls
    return _install


def _flow(list_html, detail=lambda url: _resp(200, DETAIL)):
    def handler(method, url, n):
        if "Details.aspx" in url:
            return detail(url)
        if method == "POST" and url.endswith("Default.aspx"):
            return _resp(200, list_html)
        return _resp(200, FORM)
    return handler


# ── status ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("custody,release,want", [
    ("Y", "", ("In Custody", "published_in_custody_column")),
    ("N", "10/21/2026", ("Released", "published_in_custody_column")),
    ("", "10/21/2026", ("Released", "published_release_date")),
    ("", "", ("", "")), ("?", "", ("", "")),
])
def test_status_fields(custody, release, want):
    assert status_fields(custody, release) == want


def test_blank_custody_cell_is_never_in_custody():
    assert 'else "In Custody"' not in inspect.getsource(v)


def test_scrape_records_status_source_and_published_bond(http):
    http(_flow(_list(_row("2026001", "11"), _row("2026002", "12", "10/21/2026 18:00", "N"), _row("2026003", "13", custody=""))))
    recs = VolusiaCountyScraper().scrape()
    assert [r.Booking_Number for r in recs] == ["2026001", "2026002", "2026003"]
    assert [(r.Status, r.extra_data.get("status_source", "")) for r in recs] == [
        ("In Custody", "published_in_custody_column"), ("Released", "published_in_custody_column"), ("", "")]
    assert recs[1].Release_Date == "10/21/2026"
    assert recs[0].Charges == "316.193 - DUI | 843.02 - RESIST" and recs[0].Bond_Amount == "1000"
    assert recs[0].Person_ID == "P1"


def test_unpublished_charge_bond_makes_bond_blank_and_zero_kept(http):
    detail0 = DETAIL.replace("$1,000.00", "$0.00")
    http(_flow(_list(_row("1", "1")), detail=lambda u: _resp(200, detail0)))
    assert VolusiaCountyScraper().scrape()[0].Bond_Amount == "0"
    partial = DETAIL.replace("$1,000.00", "")
    http(_flow(_list(_row("1", "1")), detail=lambda u: _resp(200, partial)))
    assert VolusiaCountyScraper().scrape()[0].Bond_Amount == ""


def test_detail_non_200_leaves_charges_and_bond_blank(http):
    http(_flow(_list(_row("1", "1")), detail=lambda u: _resp(404, "gone")))
    rec = VolusiaCountyScraper().scrape()[0]
    assert (rec.Charges, rec.Bond_Amount) == ("", "")


def test_rows_without_booking_number_or_detail_link_are_skipped():
    html = _list(_row("", "1"), _row("2026009", "2").replace('<a href="Details.aspx?InmateRID=2">x</a>', "x"))
    assert parse_recent_bookings(html) == []


# ── typed errors, nothing swallowed ─────────────────────────────────────────
def test_detail_block_and_tls_failures_propagate(http):
    http(_flow(_list(_row("1", "1")), detail=lambda u: _resp(403, "denied")))
    with pytest.raises(EgressBlocked):
        VolusiaCountyScraper().scrape()
    http(_flow(_list(_row("1", "1")), detail=lambda u: requests.exceptions.SSLError("certificate verify failed")))
    with pytest.raises(SourceTLSInvalid):
        VolusiaCountyScraper().scrape()


def test_detail_parse_error_is_not_swallowed(http, monkeypatch):
    http(_flow(_list(_row("1", "1"))))
    monkeypatch.setattr(v, "parse_detail", lambda html: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(ValueError):
        VolusiaCountyScraper().scrape()


@pytest.mark.parametrize("html,msg", [
    ("<html>moved</html>", "Recent Bookings table not found"),
    ("<html><table><tr><th>Name</th></tr></table></html>", "columns changed"),
])
def test_changed_list_is_parse_drift(http, html, msg):
    http(_flow(html))
    with pytest.raises(ParseDriftError, match=msg) as ei:
        VolusiaCountyScraper().scrape()
    assert classify_exception(ei.value).error_class == ERROR_PARSE_DRIFT


def test_missing_viewstate_is_parse_drift(http):
    http(lambda m, u, n: _resp(200, "<html>no form</html>"))
    with pytest.raises(ParseDriftError, match="ViewState"):
        VolusiaCountyScraper().scrape()


@pytest.mark.parametrize("status,expected", [(404, ERROR_URL_CHANGED), (503, ERROR_NETWORK)])
def test_list_http_failures_classified_by_status(http, status, expected):
    http(lambda m, u, n: _resp(status, "err"))
    with pytest.raises(VolusiaHTTPError) as ei:
        VolusiaCountyScraper().scrape()
    assert classify_exception(ei.value).error_class == expected


def test_no_swallow_and_no_name_logging_in_source():
    src = inspect.getsource(v)
    assert "except Exception" not in src
    assert "full_name}" not in src and "{full_name" not in src
