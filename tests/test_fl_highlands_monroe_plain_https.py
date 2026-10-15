"""Highlands / Monroe (FL): plain requests, honest UA, TLS verified; blocks fail
loud (egress_block / tls_cert_invalid); no invented $0 bond or custody.
Synthetic JSON only; no network."""
from __future__ import annotations

import inspect
import json

import pytest
import requests

import scrapers.counties.highlands as hl
import scrapers.counties.monroe as mo
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import EgressBlocked

HL = {"Information": [
    {"BookingNumber": "HCSO26JBN000001", "BookingDateTime": "2026-10-14T21:10:39-04:00", "LName": "B", "FName": "A",
     "MName": "", "Name": "B, A", "DOB": "", "Gender": "M", "Race": "W", "Age": 30, "Address": "SEBRING FL",
     "Charges": "BATTERY", "BondAmount": "$1500.00", "image": "", "PhotoURL": "", "MNINumber": "1"},
    {"BookingNumber": "HCSO26JBN000002", "BookingDateTime": "2026-10-14T20:00:00-04:00", "LName": "D", "FName": "C",
     "MName": "", "Name": "D, C", "DOB": "", "Gender": "F", "Race": "W", "Age": 31, "Address": "",
     "Charges": "", "BondAmount": "NO BOND", "image": "", "PhotoURL": "", "MNINumber": "2"},
    {"BookingNumber": "HCSO26JBN000003", "BookingDateTime": "", "LName": "F", "FName": "E", "MName": "",
     "Name": "F, E", "DOB": "", "Gender": "", "Race": "", "Age": "", "Address": "", "Charges": "",
     "BondAmount": "$0.00", "image": "", "PhotoURL": "", "MNINumber": "3"},
    {"BookingNumber": "HCSO26JBN000004", "BookingDateTime": "", "LName": "H", "FName": "G", "MName": "",
     "Name": "H, G", "DOB": "", "Gender": "", "Race": "", "Age": "", "Address": "", "Charges": "",
     "BondAmount": "", "image": "", "PhotoURL": "", "MNINumber": "4"},
]}
MO = [{"data": {"arrests": [
    {"mugShot": "https://www.keysso.net:8443/webData/ArrestLogs/MCSO78MNI183475.jpg", "Name": "AAA, BBB",
     "ArrestDate": "10/14/2026", "Bond": "0", "Charges": []},
    {"CADno": "CAD-9", "Name": "CCC, DDD", "ArrestDate": "10/14/2026", "Bond": "", "Charges": []},
    {"OffenseNo": "OFF-1", "Name": "EEE, FFF", "ArrestDate": "10/14/2026", "Bond": 2500, "Charges": []},
]}}]


def _resp(status=200, payload=None):
    r = requests.Response()
    r.status_code = status
    r._content = (json.dumps(payload) if payload is not None else "denied").encode()
    r.headers["content-type"] = "application/json" if status == 200 else "text/html"
    return r


@pytest.fixture()
def fake(monkeypatch):
    calls = []

    def install(payload=None, status=200, exc=None):
        def _req(sess, method, url, **kw):
            calls.append({"kw": kw, "headers": dict(sess.headers)})
            if exc:
                raise exc
            return _resp(status, payload if status == 200 else None)

        monkeypatch.setattr(requests.Session, "request", _req)
        return calls

    return install


@pytest.mark.parametrize("mod", [hl, mo])
def test_no_disguise_stealth_or_tls_off(mod):
    src = inspect.getsource(mod).split('"""', 2)[2]
    for banned in ("curl_cffi", "impersonate", "IMPERSONATE", "Mozilla/", "Chrome/", "STEALTH",
                   "Sec-Fetch", "verify=False", "urllib3"):
        assert banned not in src, banned


def test_highlands_fields(fake):
    calls = fake(HL)
    recs = {r.Booking_Number: r for r in hl.HighlandsCountyScraper().scrape()}
    assert calls[0]["headers"]["User-Agent"] == HONEST_USER_AGENT and calls[0]["kw"].get("verify") is True
    assert recs["HCSO26JBN000001"].Bond_Amount == "1500"
    assert (recs["HCSO26JBN000002"].Bond_Amount, recs["HCSO26JBN000002"].Bond_Type) == ("", "NO BOND")
    assert recs["HCSO26JBN000003"].Bond_Amount == "0"  # published $0 kept
    assert recs["HCSO26JBN000004"].Bond_Amount == ""
    assert all(r.Status == "" for r in recs.values())


def test_monroe_fields(fake):
    calls = fake(MO)
    recs = {r.Booking_Number: r for r in mo.MonroeCountyScraper().scrape()}
    assert calls[0]["headers"]["User-Agent"] == HONEST_USER_AGENT and calls[0]["kw"].get("verify") is True
    assert recs["MCSO78MNI183475"].Bond_Amount == "0"   # published 0 kept
    assert recs["MONROE-CAD-9"].Bond_Amount == ""
    assert recs["MONROE-OFF-1"].Bond_Amount == "2500"
    assert all(r.Status == "" for r in recs.values())


@pytest.mark.parametrize("cls", [hl.HighlandsCountyScraper, mo.MonroeCountyScraper])
@pytest.mark.parametrize("code", [401, 403, 429])
def test_block_fails_visibly(fake, cls, code):
    fake(status=code)
    with pytest.raises(EgressBlocked) as ei:
        cls().scrape()
    assert str(ei.value).startswith("egress_block:")


@pytest.mark.parametrize("cls", [hl.HighlandsCountyScraper, mo.MonroeCountyScraper])
def test_bad_certificate_fails_visibly(fake, cls):
    fake(exc=requests.exceptions.SSLError("certificate verify failed"))
    with pytest.raises(SourceTLSInvalid) as ei:
        cls().scrape()
    assert str(ei.value).startswith("tls_cert_invalid:")
