"""Polk / Volusia / Pasco (FL): an unpublished bond is "", never a counted 0.

A real published 0 ($0.00) is kept as "0". The booking total is the sum only
when every charge has a published amount. Synthetic data only; no network.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest
import requests

from scrapers.published_bond import charge_bond_amount, total_published_bond

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("text,want", [
    ("$1,500.00", 1500.0), ("0", 0.0), ("$0.00", 0.0), ("2500", 2500.0),
    ("", None), (None, None), ("NO BOND", None), ("HOLD", None), ("N/A", None),
    ("None", None), ("pending", None),
])
def test_charge_bond_amount(text, want):
    assert charge_bond_amount(text) == want


@pytest.mark.parametrize("vals,want", [
    ([], ""), ([None], ""), ([1000.0, None], ""), ([0.0], "0"),
    ([0.0, 0.0], "0"), ([1000.0, 250.5], "1250.50"), ([1000.0, 0.0], "1000"),
])
def test_total_published_bond(vals, want):
    assert total_published_bond(vals) == want


@pytest.mark.parametrize("name", ["polk", "volusia", "pasco"])
def test_no_counted_zero_left_in_module(name):
    src = (ROOT / "scrapers" / "counties" / f"{name}.py").read_text()
    assert "total_bond" not in src
    assert "total_published_bond(" in src
    assert not re.search(r'\.get\("(BondAmountDue|bondAmount)",\s*"0', src)
    assert not re.search(r"return 0\.0", src)


def _resp(status=200, body="", ctype="text/html"):
    r = requests.Response()
    r.status_code = status
    r._content = body.encode()
    r.headers["content-type"] = ctype
    return r


@pytest.fixture()
def fake_http(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_a: None)

    def _install(handler):
        monkeypatch.setattr(requests.Session, "request",
                            lambda sess, method, url, **kw: handler(method, url, kw))

    return _install


# ── Pasco ────────────────────────────────────────────────────────────────────
def _pasco_roster(n):
    today = date.today().strftime("%m/%d/%Y")
    return [{"firstName": "SYN", "middleName": "", "lastName": f"T{i}", "suffix": "",
             "bookingNumber": f"20260000{i}", "nameId": i, "dob": "",
             "bookingDate": today, "bookingTime": ""} for i in range(1, n + 1)]


def _run_pasco(fake_http, charges_by_id):
    def handler(method, url, kw):
        if "GetInCustody" in url:
            return _resp(200, json.dumps(_pasco_roster(len(charges_by_id))), "application/json")
        nid = int(url.rsplit("=", 1)[1])
        return _resp(200, json.dumps(charges_by_id[nid]), "application/json")

    fake_http(handler)
    from scrapers.counties.pasco import PascoCountyScraper

    return {r.Booking_Number: r.Bond_Amount for r in PascoCountyScraper().scrape()}


def test_pasco_bonds(fake_http):
    got = _run_pasco(fake_http, {
        1: [{"arrestCharge": "784.03", "chargeDescription": "BATTERY", "bondAmount": ""}],
        2: [{"arrestCharge": "784.03", "chargeDescription": "BATTERY", "bondAmount": "0.00"}],
        3: [{"arrestCharge": "a", "chargeDescription": "A", "bondAmount": "1000"},
            {"arrestCharge": "b", "chargeDescription": "B", "bondAmount": "500"}],
        4: [{"arrestCharge": "a", "chargeDescription": "A", "bondAmount": "1000"},
            {"arrestCharge": "b", "chargeDescription": "B"}],
        5: [],
    })
    assert got == {"202600001": "", "202600002": "0", "202600003": "1500",
                   "202600004": "", "202600005": ""}


def test_pasco_old_booking_without_charge_fetch_is_unknown(fake_http):
    roster = _pasco_roster(1)
    roster.append({**roster[0], "bookingNumber": "OLD1", "nameId": 99, "bookingDate": "01/01/2020"})

    def handler(method, url, kw):
        if "GetInCustody" in url:
            return _resp(200, json.dumps(roster), "application/json")
        return _resp(200, json.dumps([{"arrestCharge": "a", "chargeDescription": "A", "bondAmount": "100"}]),
                     "application/json")

    fake_http(handler)
    from scrapers.counties.pasco import PascoCountyScraper

    got = {r.Booking_Number: r.Bond_Amount for r in PascoCountyScraper().scrape()}
    assert got["OLD1"] == ""  # was "0" before: charges never fetched
    assert got["202600001"] == "100"


# ── Polk ─────────────────────────────────────────────────────────────────────
PROFILE = '<input name="__RequestVerificationToken" value="tok"/>'


def _run_polk(fake_http, charges):
    def handler(method, url, kw):
        if "inmate-profile" in url:
            return _resp(200, PROFILE)
        return _resp(200, json.dumps(charges), "application/json; charset=utf-8")

    fake_http(handler)
    from scrapers.counties.polk import PolkCountyScraper
    from scrapers.plain_http import make_session

    rec = PolkCountyScraper()._scrape_booking_details(
        make_session(), {"BookingNum": "PK1", "FullName": "THETIC, SYN", "EntryDate": "10/14/2026"})
    return rec.Bond_Amount


@pytest.mark.parametrize("charges,want", [
    ([{"Statute": "1", "ChargeDesc": "A"}], ""),
    ([{"Statute": "1", "ChargeDesc": "A", "BondAmountDue": ""}], ""),
    ([{"Statute": "1", "ChargeDesc": "A", "BondAmountDue": "0.00"}], "0"),
    ([{"Statute": "1", "ChargeDesc": "A", "BondAmountDue": "$2,000.00"},
      {"Statute": "2", "ChargeDesc": "B", "BondAmountDue": "500.00"}], "2500"),
    ([{"Statute": "1", "ChargeDesc": "A", "BondAmountDue": "2000.00"},
      {"Statute": "2", "ChargeDesc": "B", "BondAmountDue": "NO BOND"}], ""),
    ([], ""),
])
def test_polk_bonds(fake_http, charges, want):
    assert _run_polk(fake_http, charges) == want


def test_polk_no_token_means_unknown(fake_http):
    fake_http(lambda m, u, kw: _resp(200, "<html></html>"))
    from scrapers.counties.polk import PolkCountyScraper
    from scrapers.plain_http import make_session

    rec = PolkCountyScraper()._scrape_booking_details(make_session(), {"BookingNum": "PK2", "FullName": "X, Y"})
    assert rec.Bond_Amount == ""


# ── Volusia ──────────────────────────────────────────────────────────────────
def test_volusia_parse_bond_val_is_published_only():
    from scrapers.counties.volusia import VolusiaCountyScraper

    assert VolusiaCountyScraper._parse_bond_val("") is None
    assert VolusiaCountyScraper._parse_bond_val("NO BOND") is None
    assert VolusiaCountyScraper._parse_bond_val("$0.00") == 0.0
    assert VolusiaCountyScraper._parse_bond_val("$5,000.00") == 5000.0
