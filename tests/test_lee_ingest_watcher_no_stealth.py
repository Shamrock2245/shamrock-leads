"""Lee URL ingest + FirstAppearanceWatcher use plain direct HTTPS (2026-10-08).

Follows #147: no StealthSession, no curl_cffi impersonation, no lee_origin DNS
pin. Proxy env vars are ignored (trust_env=False). Synthetic fixtures; no network.
"""
from __future__ import annotations

import ast
import asyncio
import inspect

import pytest

import dashboard.services.url_ingest_service as ingest
import core.first_appearance_watcher as watcher


FORBIDDEN_MODULES = {"scrapers.lee_origin", "curl_cffi", "scrapers.proxy_engine"}
FORBIDDEN_NAMES = {"lee_api_get", "lee_curl_options", "create_stealth_session", "impersonate"}
PROXY_ENV = {
    "HTTP_PROXY": "http://198.51.100.1:3128",
    "HTTPS_PROXY": "http://198.51.100.1:3128",
    "ALL_PROXY": "socks5://198.51.100.1:1080",
}


def _assert_no_stealth(module):
    tree = ast.parse(inspect.getsource(module))
    imported, names, called = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Call):
            called.add(node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", ""))
    assert not imported & FORBIDDEN_MODULES
    assert not names & FORBIDDEN_NAMES
    assert not called & FORBIDDEN_NAMES


def test_url_ingest_and_watcher_import_no_lee_origin_or_curl():
    _assert_no_stealth(ingest)
    _assert_no_stealth(watcher)
    import scrapers
    assert not hasattr(scrapers, "lee_origin")


def test_lee_origin_module_removed():
    with pytest.raises(ModuleNotFoundError):
        __import__("scrapers.lee_origin")


class _FakeResp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def test_url_ingest_lee_uses_plain_requests_ignoring_proxy_env(monkeypatch):
    for k, v in PROXY_ENV.items():
        monkeypatch.setenv(k, v)

    gets = []

    class _Sess:
        def __init__(self):
            self.trust_env = True
            self.proxies = {"http": "leak"}
            self.headers = {}

        def close(self):
            pass

        def get(self, url, timeout=20, proxies=None, headers=None):
            gets.append({"url": url, "proxies": proxies, "trust_env": self.trust_env, "headers": dict(self.headers)})
            assert proxies == {}
            assert self.trust_env is False
            if "/charges" in url:
                return _FakeResp([])
            return _FakeResp({
                "givenName": "TEST", "surName": "PERSON", "middleName": "",
                "bookingNumber": "1234567", "bookingDate": "2026-10-08T12:00:00",
                "inCustody": True,
            })

    # ingest imports requests inside the function; patch the module it will bind
    import requests as req
    monkeypatch.setattr(req, "Session", _Sess)

    # Cooldown clear
    from scrapers import lee_rate_limit
    lee_rate_limit.reset_for_tests(state_path="")

    out = asyncio.run(ingest._ingest_lee_county_api("1234567", "https://www.sheriffleefl.org/booking/?id=1234567"))
    assert out is not None
    assert gets and all(g["proxies"] == {} and g["trust_env"] is False for g in gets)
    assert all("scrapfly" not in g["url"] and "api.scrapfly" not in g["url"] for g in gets)
    assert any("/bookings/1234567" in g["url"] for g in gets)
    ua = gets[0]["headers"].get("User-Agent", "")
    assert ua.startswith("ShamrockLeads/1.0")


def test_first_appearance_watcher_lee_charges_plain_requests(monkeypatch):
    for k, v in PROXY_ENV.items():
        monkeypatch.setenv(k, v)
    gets = []

    class _Sess:
        def __init__(self):
            self.trust_env = True
            self.proxies = {"http": "leak"}

        def close(self):
            pass

        def get(self, url, headers=None, timeout=15, proxies=None):
            gets.append({"url": url, "proxies": proxies, "trust_env": self.trust_env, "headers": headers or {}})
            assert proxies == {}
            assert self.trust_env is False
            return _FakeResp([{
                "offenseDescription": "BATTERY ON PERSON",
                "bondAmount": "1500",
                "bondTypeName": "Surety",
                "caseNumber": "26CF1",
            }])

    import requests as req
    monkeypatch.setattr(req, "Session", _Sess)
    from scrapers import lee_rate_limit
    lee_rate_limit.reset_for_tests(state_path="")

    w = watcher.FirstAppearanceWatcher.__new__(watcher.FirstAppearanceWatcher)
    doc = {
        "county": "Lee",
        "booking_number": "1234567",
        "bond_amount": "",
        "bond_amount_raw": "",
        "full_name": "TEST PERSON",
        "charges": "",
        "extra": {},
    }
    # _generic_refetch expects ArrestRecord.from_mongo_doc; seed a minimal doc
    from core.models import ArrestRecord
    monkeypatch.setattr(
        ArrestRecord, "from_mongo_doc",
        classmethod(lambda cls, d: ArrestRecord(
            County="Lee", State="FL", Booking_Number=d["booking_number"],
            Full_Name=d.get("full_name", ""), Bond_Amount="", extra_data={},
        )),
    )
    rec = w._generic_refetch(doc, "https://www.sheriffleefl.org/booking/?id=1234567")
    assert gets and all(g["proxies"] == {} for g in gets)
    assert any("/charges" in g["url"] for g in gets)
    assert gets[0]["headers"].get("User-Agent", "").startswith("ShamrockLeads/1.0")
    assert rec is not None
    assert rec.Bond_Amount == "1500.00"
