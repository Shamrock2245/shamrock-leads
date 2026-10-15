"""DeSoto (FL): plain honest HTTPS (no DrissionPage / stealth / cert bypass); bond published-only;
Status only from the source; bid key unchanged, never synthesized; Health state unchanged."""
import inspect

import pytest
import requests

from scrapers.counties import desoto
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import EgressBlocked


def _detail(charges, release=""):
    rows = "".join(
        f'<tr id="ChargeGrid_DXDataRow{i}"><td>{c}</td><td></td><td></td><td></td><td></td><td>{b}</td><td>SURETY</td></tr>'
        for i, (c, b) in enumerate(charges))
    rel = f"<tr><td>Release Date:</td><td>{release}</td></tr>" if release is not None else ""
    return (f'<h3 class="header-text"><span>DOE, JOHN</span></h3><table id="tblDetails">'
            f"<tr><td>Admit Date:</td><td>10/01/2026</td></tr>{rel}</table><table>{rows}</table>")


@pytest.mark.parametrize("charges,want", [
    ([("A", "$1,000.00"), ("B", "$500.00")], "1500"),
    ([("A", "$0.00")], "0"),                       # published 0 kept
    ([("A", "")], ""),
    ([("A", "NO BOND")], ""),
    ([("A", "$1,000.00"), ("B", "")], ""),         # partly unpublished
    ([("A", "$1,000.00"), ("B", "HOLD")], ""),
    ([], ""),
])
def test_bond_published_only(charges, want):
    assert desoto.parse_detail(_detail(charges))["bond"] == want


@pytest.mark.parametrize("release,want", [("", ""), ("N/A", ""), (None, ""), ("10/05/2026", "Released")])
def test_status_only_from_source(release, want):
    d = desoto.parse_detail(_detail([("A", "$100")], release))
    assert d["status"] == want


def test_record_defaults_never_invented():
    s = desoto.DeSotoCountyScraper()
    row = {"name": "DOE, JOHN", "first": "JOHN", "middle": "", "last": "DOE", "age": "30", "race": "W",
           "sex": "M", "admit": "10/01/2026", "detail_url": "https://jail.desotosheriff.org/DCN/inmate-details?id=1&bid=ABC",
           "booking": "ABC"}
    rec = s._to_record(row, {})               # no detail fetched
    assert rec.Bond_Amount == "" and rec.Status == "" and rec.Booking_Number == "ABC"
    rec2 = s._to_record(row, desoto.parse_detail(_detail([("A", "NO BOND")])))
    assert rec2.Bond_Amount == "" and rec2.Status == ""


def test_roster_requires_source_bid_no_synthetic_key():
    s = desoto.DeSotoCountyScraper()
    html = ('<table><tr id="gvInmates_DXDataRow0"><td><a href="/DCN/inmate-details?id=1&bid=XYZ1">DOE, JOHN</a></td>'
            '<td>30</td><td>W</td><td>M</td><td>10/01/2026</td></tr>'
            '<tr id="gvInmates_DXDataRow1"><td><a href="/DCN/inmate-details?id=2">ROE, JANE</a></td>'
            '<td>31</td><td>B</td><td>F</td><td>10/02/2026</td></tr></table>')
    rows = s._parse_roster(html, "https://jail.desotosheriff.org")
    assert [r["booking"] for r in rows] == ["XYZ1"]
    assert not any(r["booking"].startswith("DCN_") for r in rows)


def test_no_browser_stealth_or_cert_bypass():
    src = inspect.getsource(desoto).split('"""', 2)[2]  # code only, not the docstring
    for banned in ("DrissionPage", "ChromiumPage", "_get_browser_options", "ignore-certificate", "verify=False",
                   "Mozilla/", "Chrome/", "impersonate"):
        assert banned not in src, banned


class _Resp:
    def __init__(self, code=200, text=""):
        self.status_code, self.text = code, text
        self.headers = {"content-type": "text/html"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.status_code)


def _install(monkeypatch, resp=None, exc=None):
    calls = []

    def fake(self, method, url, **kw):
        calls.append({"kw": kw, "headers": dict(self.headers)})
        if exc:
            raise exc
        return resp

    monkeypatch.setattr(requests.Session, "request", fake)
    return calls


def test_honest_ua_verify_and_empty_roster(monkeypatch):
    calls = _install(monkeypatch, _Resp(text="<html></html>"))
    assert desoto.DeSotoCountyScraper().scrape() == []
    assert calls[0]["headers"]["User-Agent"] == HONEST_USER_AGENT and calls[0]["kw"]["verify"] is True


@pytest.mark.parametrize("code", [401, 403, 429])
def test_block_fails_visibly(monkeypatch, code):
    _install(monkeypatch, _Resp(code=code))
    with pytest.raises(EgressBlocked, match=r"^egress_block: DeSoto"):
        desoto.DeSotoCountyScraper().scrape()


def test_bad_certificate_fails_visibly(monkeypatch):
    _install(monkeypatch, exc=requests.exceptions.SSLError("certificate verify failed"))
    with pytest.raises(SourceTLSInvalid, match=r"^tls_cert_invalid: DeSoto"):
        desoto.DeSotoCountyScraper().scrape()


def test_health_state_unchanged():
    from dashboard.extensions import scraper_source_state
    assert scraper_source_state("DeSoto (FL)") == "unverified"
