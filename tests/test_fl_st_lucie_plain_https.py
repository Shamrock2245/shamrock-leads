"""St. Lucie (FL): plain requests, honest UA, TLS verified; no assumed custody,
no invented bond, no name-keyed rows. Synthetic HTML only; no network."""
from __future__ import annotations

import inspect

import pytest
import requests

import scrapers.counties.st_lucie as stl
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import EgressBlocked

HTML = """<html><body><table>
<tr><th>Name</th><th>DOB</th><th>Age</th><th>Inmate Id</th><th>Booking Date</th><th>Release Date</th></tr>
<tr><td><a href="inmateProfile.php?inmate_id=123456">AAA, BBB</a></td><td>01-01-1990</td><td>36</td><td>123456</td><td>10-14-2026 10:00</td><td>:</td></tr>
<tr><td>CCC, DDD</td><td>02-02-1991</td><td>35</td><td>223344</td><td>10-13-2026 09:00</td><td>10-14-2026 12:30</td></tr>
<tr><td>EEE, FFF</td><td>03-03-1992</td><td>34</td><td></td><td>10-12-2026 08:00</td><td>:</td></tr>
<tr><td>AAA, BBB</td><td>01-01-1990</td><td>36</td><td>123456</td><td>10-14-2026 10:00</td><td>:</td></tr>
</table></body></html>"""


def _resp(status=200, body="", ctype="text/html"):
    r = requests.Response()
    r.status_code = status
    r._content = body.encode()
    r.headers["content-type"] = ctype
    return r


@pytest.fixture()
def fake(monkeypatch):
    calls = []

    def install(handler):
        def _req(sess, method, url, **kw):
            calls.append({"method": method, "url": url, "kw": kw, "headers": dict(sess.headers)})
            return handler(method, url, kw)

        monkeypatch.setattr(requests.Session, "request", _req)
        return calls

    return install


def test_no_impersonation_ua_or_tls_off():
    src = inspect.getsource(stl).split('"""', 2)[2]
    for banned in ("curl_cffi", "impersonate", "IMPERSONATE", "Mozilla/", "Chrome/", "verify=False"):
        assert banned not in src, banned


def test_parse_honest_request_and_fields(fake):
    calls = fake(lambda m, u, kw: _resp(200, HTML))
    recs = stl.StLucieCountyScraper().scrape()
    assert len(calls) == 1
    c = calls[0]
    assert c["method"] == "POST" and c["headers"]["User-Agent"] == HONEST_USER_AGENT
    assert c["kw"].get("verify") is True and "impersonate" not in c["kw"]
    by = {r.Booking_Number: r for r in recs}
    assert set(by) == {"123456", "223344"}  # blank id skipped, duplicate collapsed
    assert by["123456"].Status == "" and by["123456"].Release_Date == ""
    assert by["223344"].Status == "Released" and by["223344"].Release_Date == "10-14-2026 12:30"
    assert all(r.Bond_Amount == "" for r in recs)
    assert by["123456"].Detail_URL.endswith("inmateProfile.php?inmate_id=123456")


@pytest.mark.parametrize("code", [401, 403, 429])
def test_block_raises(fake, code):
    fake(lambda m, u, kw: _resp(code, "denied"))
    with pytest.raises(EgressBlocked):
        stl.StLucieCountyScraper().scrape()


def test_bad_certificate_raises(fake):
    def h(*_a):
        raise requests.exceptions.SSLError("certificate verify failed")

    fake(h)
    with pytest.raises(SourceTLSInvalid):
        stl.StLucieCountyScraper().scrape()


def test_no_table_returns_empty(fake):
    fake(lambda m, u, kw: _resp(200, "<html><body>maintenance</body></html>"))
    assert stl.StLucieCountyScraper().scrape() == []
