"""Aiken (SC): plain honest HTTPS; no curl_cffi/verify=False; published-only bond/status."""
import inspect
from unittest.mock import MagicMock

import pytest
import requests

from scrapers import plain_http
from scrapers.counties_sc import aiken
from scrapers.counties_sc.aiken import AikenScraper, published_bond, published_status
from scrapers.scraper_resilience import EgressBlocked


def test_no_disguise_or_cert_bypass():
    src = inspect.getsource(aiken)
    body = src.split('"""', 2)[-1]
    for bad in ("curl_cffi", "impersonate", "verify=False", "Chrome/", "AIK_"):
        assert bad not in body, bad
    assert 'Status="In Custody"' not in body
    assert 'or "0"' not in body


def test_session_is_honest():
    s = plain_http.make_session()
    assert s.headers["User-Agent"] == plain_http.HONEST_USER_AGENT and s.verify is True


@pytest.mark.parametrize("detail,want", [
    ({}, ""),
    ({"bond": "1500"}, "1500"),
    ({"bond": "0"}, "0"),
    ({"bond": "$0.00"}, "0"),
    ({"bond": "NO BOND"}, ""),
    ({"bond": ""}, ""),
])
def test_published_bond(detail, want):
    assert published_bond(detail) == want


@pytest.mark.parametrize("detail,row,want", [
    ({}, {}, ""),
    ({"status": "Released"}, {}, "Released"),
    ({}, {"Status": "IN CUSTODY"}, "IN CUSTODY"),
])
def test_published_status(detail, row, want):
    assert published_status(detail, row) == want


def test_to_record_never_invents():
    row = {"inmate_id": "1008603", "last": "DOE", "first": "JOHN", "full": "DOE, JOHN",
           "arrest": "01/01/2026", "age": "30", "sex": "M", "race": "W", "detail_url": "https://x"}
    rec = AikenScraper()._to_record(row, {})
    assert rec is not None
    assert rec.Bond_Amount == "" and rec.Status == "" and rec.Charges == ""
    rec0 = AikenScraper()._to_record(row, {"bond": "0", "status": "Released"})
    assert rec0.Bond_Amount == "0" and rec0.Status == "Released"


def test_search_get_block_raises(monkeypatch):
    monkeypatch.setattr(plain_http, "make_session", lambda *a, **k: MagicMock())
    monkeypatch.setattr(
        plain_http, "request",
        lambda *a, **k: (_ for _ in ()).throw(
            EgressBlocked("egress_block: Aiken connection_reset: TLS handshake dropped")),
    )
    with pytest.raises(EgressBlocked, match="egress_block"):
        AikenScraper().scrape()


def test_ssl_error_paths(monkeypatch):
    sess = plain_http.make_session()

    def boom(*a, **k):
        raise requests.exceptions.SSLError("certificate verify failed")

    monkeypatch.setattr(sess, "request", boom)
    with pytest.raises(plain_http.SourceTLSInvalid, match="tls_cert_invalid"):
        plain_http.request(sess, "GET", aiken.SEARCH_URL, county="Aiken")
