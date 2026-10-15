"""Martin (FL): plain requests, honest UA, TLS verified; status only from the
published IsReleased flag; bond only when every bond publishes an amount;
State always FL; no fallback booking key. Synthetic JSON only; no network."""
from __future__ import annotations

import inspect
import json

import pytest
import requests

import scrapers.counties.martin as mtn
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import EgressBlocked


def _inmate(so="123456", released=False, bonds=None, state="FL", booking="2026-10-14T08:00:00", **extra):
    d = {
        "id": "internal-guid", "soNumber": so, "IsReleased": released,
        "name": {"firstName": "A", "middleName": "", "lastName": "B", "fullName": "B, A"},
        "dateOfBirth": "", "height": "", "weight": "", "race": "", "gender": "",
        "arrests": [{"arrestDate": "2026-10-14T07:47:00", "arrestingAgency": "MCSO", "ageAtArrest": 30,
                     "charges": [{"statute": {"description": "BATTERY", "code": "784.03"}}]}],
        "bonds": bonds if bonds is not None else [{"amount": 1500, "type": "SURETY"}],
        "address": {"city": "Stuart", "state": state},
        "bookingDate": booking,
    }
    if released is None:
        d.pop("IsReleased")
    d.update(extra)
    return d


def _resp(status=200, payload=None, ctype="application/json"):
    r = requests.Response()
    r.status_code = status
    r._content = (json.dumps(payload) if payload is not None else "denied").encode()
    r.headers["content-type"] = ctype
    return r


@pytest.fixture()
def fake(monkeypatch):
    calls = []

    def install(inmates=None, status=200, exc=None):
        def _req(sess, method, url, **kw):
            calls.append({"kw": kw, "headers": dict(sess.headers)})
            if exc:
                raise exc
            return _resp(status, {"inmates": inmates or []} if status == 200 else None,
                         "application/json" if status == 200 else "text/html")

        monkeypatch.setattr(requests.Session, "request", _req)
        return calls

    return install


def _one(fake, **kw):
    fake([_inmate(**kw)])
    recs = mtn.MartinCountyScraper().scrape()
    assert len(recs) == 1
    return recs[0]


def test_no_impersonation_ua_or_tls_off():
    src = inspect.getsource(mtn).split('"""', 2)[2]
    for banned in ("curl_cffi", "impersonate", "IMPERSONATE", "Mozilla/", "Chrome/", "verify=False", "urllib3"):
        assert banned not in src, banned


def test_honest_verified_request(fake):
    calls = fake([_inmate()])
    mtn.MartinCountyScraper().scrape()
    assert len(calls) == 1
    assert calls[0]["headers"]["User-Agent"] == HONEST_USER_AGENT
    assert calls[0]["kw"].get("verify") is True and "impersonate" not in calls[0]["kw"]


@pytest.mark.parametrize("released,expected", [(False, "In Custody"), (True, "Released"), (None, "")])
def test_status_from_published_flag_only(fake, released, expected):
    assert _one(fake, released=released).Status == expected


@pytest.mark.parametrize("bonds,expected", [
    ([{"amount": 1500, "type": "SURETY"}, {"amount": "2,500.50", "type": "CASH"}], "4000.50"),
    ([{"amount": 0, "type": "ROR"}], "0"),                                  # published 0 kept
    ([{"amount": 1000, "type": "SURETY"}, {"type": "NO BOND"}], ""),        # one unpublished -> unknown
    ([{"amount": None, "type": "SURETY"}], ""),
    ([], ""),                                                               # no bonds -> unknown, not 0
])
def test_bond_published_or_blank(fake, bonds, expected):
    r = _one(fake, bonds=bonds)
    assert r.Bond_Amount == expected


def test_state_is_always_fl(fake):
    assert _one(fake, state="NE").State == "FL"


def test_booking_date_from_source_field(fake):
    r = _one(fake, booking="2026-10-13T22:15:00")
    assert (r.Booking_Date, r.Booking_Time) == ("2026-10-13", "22:15:00")
    assert r.Arrest_Date == "2026-10-14"


def test_row_without_so_number_is_skipped(fake):
    fake([_inmate(so=""), _inmate(so="654321")])
    recs = mtn.MartinCountyScraper().scrape()
    assert [r.Booking_Number for r in recs] == ["654321"]


@pytest.mark.parametrize("code", [401, 403, 429])
def test_block_raises(fake, code):
    fake(status=code)
    with pytest.raises(EgressBlocked):
        mtn.MartinCountyScraper().scrape()


def test_bad_certificate_raises(fake):
    fake(exc=requests.exceptions.SSLError("certificate verify failed"))
    with pytest.raises(SourceTLSInvalid):
        mtn.MartinCountyScraper().scrape()
