"""Polk (FL): no catch-all handlers; blocks, TLS, HTTP status and payload drift raise typed errors.
A per-booking detail miss leaves charges/bond "" and is counted in one warning (no names or ids).
Synthetic responses only; no network."""
from __future__ import annotations

import inspect
import json
import logging

import pytest
import requests

import scrapers.counties.polk as polk
from scrapers.counties.polk import PolkCountyScraper, PolkHTTPError
from scrapers.plain_http import SourceTLSInvalid
from scrapers.scraper_resilience import (
    ERROR_NETWORK, ERROR_PARSE_DRIFT, ERROR_URL_CHANGED, EgressBlocked, ParseDriftError, classify_exception,
)

BOOKING = {"BookingNum": "2610001", "FullName": "THETIC, SYN", "RS": "WM", "EntryDate": "10/21/2026",
           "ReleaseDate": "", "Location": "CCJ"}
PROFILE = '<input name="__RequestVerificationToken" value="tok"/>'
CHARGES = [{"Statute": "316.193", "ChargeDesc": "DUI", "BondAmountDue": "500.00"}]


def _resp(status=200, body="", ctype="text/html"):
    r = requests.Response()
    r.status_code, r._content = status, body.encode() if isinstance(body, str) else body
    r.headers["content-type"] = ctype
    return r


def _json(obj, status=200):
    return _resp(status, json.dumps(obj), "application/json; charset=utf-8")


@pytest.fixture()
def http(monkeypatch):
    monkeypatch.setattr(polk, "DAYS_BACK", 1)
    monkeypatch.setattr(polk.time, "sleep", lambda *_a: None)

    def _install(search=lambda: _json([BOOKING]), profile=lambda: _resp(200, PROFILE), charges=lambda: _json(CHARGES)):
        def fake(sess, method, url, **kw):
            if "SearchJail" in url:
                out = search()
            elif "inmate-profile" in url:
                out = profile()
            else:
                out = charges()
            if isinstance(out, BaseException):
                raise out
            return out
        monkeypatch.setattr(requests.Session, "request", fake)
    return _install


def test_no_catch_all_handlers_or_id_logging_in_source():
    src = inspect.getsource(polk)
    assert "except Exception" not in src
    assert "for {booking_num}" not in src and "booking {booking_num}" not in src


def test_happy_path_records_status_source_and_bond(http):
    http(search=lambda: _json([BOOKING, BOOKING | {"BookingNum": "2610002", "ReleaseDate": "10/21/2026"}]))
    a, b = PolkCountyScraper().scrape()
    assert (a.Status, a.extra_data) == ("", {})
    assert (b.Status, b.extra_data["status_source"]) == ("Released", "published_release_date")
    assert (a.Charges, a.Bond_Amount) == ("316.193 - DUI", "500")


@pytest.mark.parametrize("step", ["search", "profile", "charges"])
def test_block_propagates_from_every_step(http, step):
    http(**{step: lambda: _resp(403, "denied")})
    with pytest.raises(EgressBlocked):
        PolkCountyScraper().scrape()


@pytest.mark.parametrize("step", ["search", "profile", "charges"])
def test_tls_failure_propagates_from_every_step(http, step):
    http(**{step: lambda: requests.exceptions.SSLError("certificate verify failed")})
    with pytest.raises(SourceTLSInvalid):
        PolkCountyScraper().scrape()


@pytest.mark.parametrize("status,expected", [(404, ERROR_URL_CHANGED), (500, ERROR_NETWORK)])
def test_search_http_failure_is_polk_http_error(http, status, expected):
    http(search=lambda: _resp(status, "err"))
    with pytest.raises(PolkHTTPError) as ei:
        PolkCountyScraper().scrape()
    assert ei.value.status_code == status
    assert classify_exception(ei.value).error_class == expected


@pytest.mark.parametrize("body", [_resp(200, "<html>moved</html>"), _json({"Data": []}), _json(["x"])])
def test_search_payload_drift_raises(http, body):
    http(search=lambda: body)
    with pytest.raises(ParseDriftError) as ei:
        PolkCountyScraper().scrape()
    assert classify_exception(ei.value).error_class == ERROR_PARSE_DRIFT


def test_charges_payload_drift_raises(http):
    http(charges=lambda: _json({"charges": CHARGES}))
    with pytest.raises(ParseDriftError, match="GetCharges"):
        PolkCountyScraper().scrape()


def test_network_error_is_not_swallowed(http):
    http(charges=lambda: requests.exceptions.ReadTimeout("read timed out"))
    with pytest.raises(polk.PolkNetworkError) as ei:
        PolkCountyScraper().scrape()
    assert isinstance(ei.value.__cause__, requests.exceptions.ReadTimeout)


# ── Codex P2 on f6f6acd: no booking ids in propagated network errors ───────────
_LEAKY = ("HTTPSConnectionPool(host='www.polksheriff.org', port=443): Max retries exceeded with url: "
          "/inmate-profile/2610001 (Caused by NewConnectionError('Failed to establish a new connection: "
          "[Errno 111] Connection refused'))")


@pytest.mark.parametrize("step,name", [("profile", "inmate-profile"), ("charges", "GetCharges"), ("search", "SearchJail")])
def test_network_error_message_is_scrubbed_and_cause_kept(http, step, name):
    cause = requests.exceptions.ConnectionError(_LEAKY)
    http(**{step: lambda: cause})
    with pytest.raises(polk.PolkNetworkError) as ei:
        PolkCountyScraper().scrape()
    msg = str(ei.value)
    assert msg == f"Polk: network error on {name} (ConnectionError)"
    for leak in ("2610001", "inmate-profile/", "THETIC", "polksheriff.org", "url:"):
        assert leak not in msg
    assert ei.value.__cause__ is cause  # cause chain kept for debugging
    v = classify_exception(ei.value)
    assert v.error_class == ERROR_NETWORK and v.retryable


def test_blocks_and_tls_are_not_rewrapped(http):
    http(profile=lambda: _resp(403, "denied"))
    with pytest.raises(EgressBlocked) as ei:
        PolkCountyScraper().scrape()
    assert not isinstance(ei.value, polk.PolkNetworkError)


@pytest.mark.parametrize("kw", [
    {"profile": lambda: _resp(404, "gone")},
    {"profile": lambda: _resp(200, "<html>no token</html>")},
    {"charges": lambda: _resp(500, "err")},
    {"charges": lambda: _resp(200, "<html>no charges</html>")},
])
def test_detail_miss_blanks_fields_and_is_counted_once(http, caplog, kw):
    http(search=lambda: _json([BOOKING, BOOKING | {"BookingNum": "2610002"}]), **kw)
    with caplog.at_level(logging.WARNING, logger=polk.__name__):
        recs = PolkCountyScraper().scrape()
    assert [(r.Charges, r.Bond_Amount) for r in recs] == [("", ""), ("", "")]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["Polk: 2 booking detail(s) unavailable; charges/bond left blank"]
    assert all("2610001" not in w and "THETIC" not in w for w in warnings)


def test_empty_search_is_an_empty_run(http):
    http(search=lambda: _json([]))
    assert PolkCountyScraper().scrape() == []
