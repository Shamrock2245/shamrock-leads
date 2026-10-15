"""Suwannee (FL): unpublished bond is "", published 0 kept; Status only from the card; honest plain HTTPS."""
import inspect

import pytest
import requests

from scrapers.counties import suwannee
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import EgressBlocked


def _charge(bond):
    return f'<tr><td></td><td>810.08</td><td>C1</td><td>TRESPASS</td><td>M</td><td>2</td><td>{bond}</td><td></td></tr>'


def _card(status_line="<tr><td>Status: In Jail</td></tr>", charges=(_charge("$500.00"),)):
    return ("<table><tr><td><img src='ViewImage.aspx?bookno=SCSO26JBN000009'></td>"
            "<td>DOE, JOHN Q (W/ MALE )</td></tr>" + status_line +
            "<tr><td>Booking No: SCSO26JBN000009</td></tr><tr><td>Booking Date: 09/25/2026 06:36 AM</td></tr>"
            '<tr><td><table class="JailViewCharges"><tr class="SearchHeader"><td>CHARGES</td></tr>'
            + "".join(charges) + "</table></td></tr></table>")


def _one(html):
    recs = suwannee.SuwanneeCountyScraper()._parse_html(html, set())
    assert len(recs) == 1
    return recs[0]


@pytest.mark.parametrize("bonds,want", [
    (["$500.00", "$1,000.00"], "1500"),
    (["$0.00"], "0"),                         # published 0 kept
    (["$0.00", "$0.00"], "0"),
    ([""], ""),
    (["NO BOND"], ""),
    (["HOLD"], ""),
    (["$500.00", ""], ""),                    # one charge unpublished
    (["$500.00", "NO BOND"], ""),
    ([], ""),                                 # no charges
])
def test_bond_published_only(bonds, want):
    assert _one(_card(charges=[_charge(b) for b in bonds])).Bond_Amount == want


@pytest.mark.parametrize("line,want", [
    ("<tr><td>Status: In Jail</td></tr>", "In Custody"),
    ("<tr><td>Status: In Custody</td></tr>", "In Custody"),
    ("<tr><td>Status: Out of Jail</td></tr>", "Released"),   # used to read as In Custody
    ("<tr><td>Status: Released</td></tr>", "Released"),
    ("", ""),                                                  # absent -> unknown, not invented
    ("<tr><td>Status: Pending</td></tr>", ""),
])
def test_status_only_from_source(line, want):
    assert _one(_card(status_line=line)).Status == want


def test_no_chrome_disguise():
    src = inspect.getsource(suwannee)
    for banned in ("Mozilla/", "Chrome/", "impersonate", "curl_cffi", "verify=False", "requests.Session()"):
        assert banned not in src, banned


class _Resp:
    def __init__(self, code=200, text=""):
        self.status_code, self.text = code, text
        self.headers = {"content-type": "text/html"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.status_code)


def _install(monkeypatch, responses=None, exc=None):
    calls = []

    def fake(self, method, url, **kw):
        calls.append({"kw": kw, "headers": dict(self.headers)})
        if exc:
            raise exc
        return responses.pop(0)

    monkeypatch.setattr(requests.Session, "request", fake)
    return calls


def test_requests_honest_and_verified(monkeypatch):
    calls = _install(monkeypatch, [_Resp(text="<html>form gone</html>")])
    with pytest.raises(RuntimeError, match="search form changed"):
        suwannee.SuwanneeCountyScraper().scrape()
    assert calls[0]["headers"]["User-Agent"] == HONEST_USER_AGENT and calls[0]["kw"]["verify"] is True


@pytest.mark.parametrize("code", [401, 403, 429])
def test_block_fails_visibly(monkeypatch, code):
    _install(monkeypatch, [_Resp(code=code)])
    with pytest.raises(EgressBlocked, match=r"^egress_block: Suwannee"):
        suwannee.SuwanneeCountyScraper().scrape()


def test_bad_certificate_fails_visibly(monkeypatch):
    _install(monkeypatch, exc=requests.exceptions.SSLError("certificate verify failed"))
    with pytest.raises(SourceTLSInvalid, match=r"^tls_cert_invalid: Suwannee"):
        suwannee.SuwanneeCountyScraper().scrape()
