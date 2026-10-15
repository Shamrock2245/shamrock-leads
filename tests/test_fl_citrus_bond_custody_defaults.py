"""Citrus (FL): unpublished bond is "", published 0 kept; Status ""; honest plain HTTPS, blocks fail visibly."""
import inspect

import pytest
import requests

from scrapers.counties import citrus
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import EgressBlocked

COL = {"photo": 0, "name": 1, "ar #": 2, "date": 3, "arrest type": 4, "offense": 5, "dob": 6, "bond": 7}


def _rec(bond):
    row = ["", "DOE, JANE Q", "AB26-000123", "10/01/2026", "Felony Arrest", "810.08 - TRESPASS", "01/01/1990", bond]
    return citrus.CitrusCountyScraper()._row_to_record(row, COL, set(), "https://example.test/x.pdf")


@pytest.mark.parametrize("bond,want", [
    ("1,000.00", "1000"), ("$2,500", "2500"), ("250.50", "250.50"),
    ("0", "0"), ("$0.00", "0"),                       # published 0 kept
    ("", ""), ("NO BOND", ""), ("HOLD", ""), ("N/A", ""), ("NONE", ""), ("SEE JUDGE", ""),
])
def test_bond_published_only(bond, want):
    assert _rec(bond).Bond_Amount == want


def test_status_never_invented():
    assert _rec("100").Status == ""


def test_no_chrome_disguise():
    src = inspect.getsource(citrus)
    for banned in ("Mozilla/", "Chrome/", "impersonate", "curl_cffi", "verify=False", "requests.get"):
        assert banned not in src, banned


class _Resp:
    def __init__(self, code=200, text="", content=b"", ctype="text/html"):
        self.status_code, self.text, self.content = code, text, content
        self.headers = {"content-type": ctype}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.status_code)


def _install(monkeypatch, responses=None, exc=None):
    calls = []

    def fake(self, method, url, **kw):
        calls.append({"url": url, "kw": kw, "headers": dict(self.headers), "verify": self.verify})
        if exc:
            raise exc
        return responses.pop(0)

    monkeypatch.setattr(requests.Session, "request", fake)
    return calls


def test_requests_honest_and_verified(monkeypatch):
    calls = _install(monkeypatch, [_Resp(text="<html>no pdf here</html>")])
    assert citrus.CitrusCountyScraper().scrape() == []
    assert calls[0]["headers"]["User-Agent"] == HONEST_USER_AGENT
    assert calls[0]["kw"]["verify"] is True


@pytest.mark.parametrize("code", [401, 403, 429])
def test_block_fails_visibly(monkeypatch, code):
    _install(monkeypatch, [_Resp(code=code)])
    with pytest.raises(EgressBlocked, match=r"^egress_block: Citrus"):
        citrus.CitrusCountyScraper().scrape()


def test_bad_certificate_fails_visibly(monkeypatch):
    _install(monkeypatch, exc=requests.exceptions.SSLError("certificate verify failed"))
    with pytest.raises(SourceTLSInvalid, match=r"^tls_cert_invalid: Citrus"):
        citrus.CitrusCountyScraper().scrape()
