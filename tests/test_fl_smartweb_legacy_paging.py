"""FL SmartWEB legacy AddMoreResults paging + Putnam/Sumter contracts (2026-10-07).

Fixtures are synthetic (placeholder names, fabricated-but-well-formed IDs). The
HTTP layer is faked; no network access.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scrapers import fl_smartweb
from scrapers.counties.putnam import PutnamCountyScraper
from scrapers.counties.sumter import SumterCountyScraper

LEGACY_JS = """
<script>
var SearchVals = { FirstName: '', MiddleName: '', LastName: '', BeginBookDate: '', EndBookDate: '', BeginReleaseDate: '', EndReleaseDate: '', TypeJailSearch: 0, RecordsLoaded: 0, SortOption: 0, SortOrder: 0, IsDefault : true};
function LoadMoreResults() {
  $.ajax({ type: "POST", url: "Jail.aspx/AddMoreResults", data: JSON.stringify(SearchVals),
    success: function (msg) { var data = msg.d.Data; } });
}
</script>
"""

MODERN_JS = """
<script>
var SearchVals = { FirstName: '', MiddleName: '', LastName: '', BeginBookDate: '', EndBookDate: '', BeginReleaseDate: '', EndReleaseDate: '', TypeJailSearch: 0, RecordsLoaded: 0, SortOption: 0, SortOrder: 0, IsDefault: true, DateOfBirth: '', BookingNumber: ''};
function LoadMoreResults() {
  $.ajax({ type: "POST", url: "Jail.aspx/AddMoreResults", data: JSON.stringify({ searchVals: SearchVals }),
    success: function (msg) { var data = msg.d; } });
}
</script>
"""

LANDING = """
<html><body><form>
<input type="hidden" name="__VIEWSTATE" value="vs" />
<input type="hidden" name="__EVENTVALIDATION" value="ev" />
<input name="tbBeginDate" /><select name="TypeSearch"><option value="0">Current</option></select>
</form></body></html>
"""


def _card(bookno: str, name: str = "DOE, JANE Q") -> str:
    return f"""
<tr class="InmateRecordRow">
  <td><img src='ViewImage.aspx?bookno={bookno}'></td>
  <td><table><thead><tr><td class="SearchHeader">{name} (W/ FEMALE )</td></tr></thead>
  <tbody>
    <tr><td>Status: In Jail</td></tr>
    <tr><td>Booking No: {bookno}</td></tr>
    <tr><td>Booking Date: 10/06/2026 08:21 AM</td></tr>
  </tbody></table></td>
</tr>
<tr><td><table class="JailViewCharges">
<tr class="SearchHeader"><td>CHARGES</td></tr>
<tr><td></td><td>843.02</td><td></td><td>RESIST OFFICER</td><td>M</td><td>1</td><td>$500.00</td></tr>
</table></td></tr>
"""


def _results_page(js: str, booknos: list[str]) -> str:
    cards = "".join(_card(b) for b in booknos)
    return (
        f"<html><body><span id=\"ResultsReturned\">{len(booknos)}</span>"
        f"<div id=\"JailInfo\"><table>{cards}</table></div>{js}</body></html>"
    )


class _Resp:
    def __init__(self, text: str = "", payload=None, status: int = 200):
        self.text = text
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeSession:
    """Serves a landing page, a results page, then AddMoreResults pages.

    ``legacy`` hosts reject the modern ``{"searchVals": ...}`` wrapper with 500,
    mirroring Putnam/Bradford/Dixie/Taylor/Santa Rosa behaviour observed live.
    """

    def __init__(self, *, legacy: bool, first: list[str], more: list[list[str]]):
        self.legacy = legacy
        self.first = first
        self.more = list(more)
        self.headers: dict = {}
        self.ajax_bodies: list[dict] = []

    def get(self, url, timeout=None):
        return _Resp(LANDING)

    def post(self, url, data=None, json=None, headers=None, timeout=None):
        if url.endswith("/AddMoreResults"):
            self.ajax_bodies.append(json)
            wrapped = isinstance(json, dict) and "searchVals" in json
            if self.legacy and wrapped:
                return _Resp(status=500)
            if not self.legacy and not wrapped:
                return _Resp(status=500)
            batch = self.more.pop(0) if self.more else []
            body = {
                "data": "".join(_card(b) for b in batch),
                "resultsReturned": len(batch),
                "resultsAttempted": 10,
            }
            return _Resp(payload={"d": {"Data": body}} if self.legacy else {"d": body})
        return _Resp(_results_page(LEGACY_JS if self.legacy else MODERN_JS, self.first))


def _run(monkeypatch, session: _FakeSession, county: str):
    monkeypatch.setattr(fl_smartweb.requests, "Session", lambda: session)
    monkeypatch.setattr(fl_smartweb, "REQUEST_DELAY_S", 0)
    return fl_smartweb.scrape_smartweb_jail_view(
        county=county, facility=f"{county} County Jail", base_url="https://example.test/smartwebclient"
    )


def test_detects_legacy_and_modern_page_methods():
    assert fl_smartweb._is_legacy_page_method(LEGACY_JS) is True
    assert fl_smartweb._is_legacy_page_method(MODERN_JS) is False
    # No page-method script at all: default to the modern wrapper (prior behaviour).
    assert fl_smartweb._is_legacy_page_method("<html></html>") is False


def test_legacy_payload_is_bare_searchvals_without_modern_keys():
    body = fl_smartweb._more_results_payload("09/07/2026", "10/07/2026", 20, legacy=True)
    assert "searchVals" not in body
    assert body["RecordsLoaded"] == 20
    assert body["BeginBookDate"] == "09/07/2026"
    assert "DateOfBirth" not in body and "BookingNumber" not in body
    modern = fl_smartweb._more_results_payload("09/07/2026", "10/07/2026", 20, legacy=False)
    assert set(modern) == {"searchVals"}
    assert modern["searchVals"]["BookingNumber"] == ""


def test_more_results_body_unwraps_both_shapes():
    inner = {"data": "<tr></tr>", "resultsReturned": 3}
    assert fl_smartweb._more_results_body({"d": {"Data": inner}}) == inner
    assert fl_smartweb._more_results_body({"d": inner}) == inner
    assert fl_smartweb._more_results_body({}) == {}


def test_legacy_host_pages_past_first_results(monkeypatch):
    session = _FakeSession(
        legacy=True,
        first=[f"PCSO26JBN{n:06d}" for n in range(1, 11)],
        more=[[f"PCSO26JBN{n:06d}" for n in range(11, 21)], [f"PCSO26JBN{n:06d}" for n in range(21, 24)]],
    )
    recs = _run(monkeypatch, session, "Putnam")
    assert [r.Booking_Number for r in recs] == [f"PCSO26JBN{n:06d}" for n in range(1, 24)]
    assert all("searchVals" not in b for b in session.ajax_bodies)
    assert all(r.Charges == "843.02 - RESIST OFFICER" and r.Bond_Amount == "500" for r in recs)


def test_modern_host_still_uses_wrapper(monkeypatch):
    session = _FakeSession(
        legacy=False,
        first=[f"SCSO26JBN{n:06d}" for n in range(1, 11)],
        more=[[f"SCSO26JBN{n:06d}" for n in range(11, 14)]],
    )
    recs = _run(monkeypatch, session, "Sumter")
    assert len(recs) == 13
    assert all("searchVals" in b for b in session.ajax_bodies)


@pytest.mark.parametrize(
    "cls,url_fragment,county",
    [
        (PutnamCountyScraper, "smartweb.pcso.us", "Putnam"),
        (SumterCountyScraper, "portal.sumtercountysheriff.org", "Sumter"),
    ],
)
def test_putnam_sumter_are_source_validated_plain_requests(cls, url_fragment, county):
    from scrapers.counties import putnam, sumter

    mod = {PutnamCountyScraper: putnam, SumterCountyScraper: sumter}[cls]
    assert cls.SOURCE_CONTRACT_VALIDATED is True
    assert url_fragment in mod.BASE_URL
    s = cls.__new__(cls)
    assert s.county == county
    assert s.state == "FL"
    src = Path(mod.__file__).read_text()
    assert "curl_cffi" not in src
    assert "verify=False" not in src
    assert '"%"' not in src  # wildcard name search retired


def test_putnam_scrape_delegates_to_shared_helper(monkeypatch):
    from scrapers.counties import putnam

    seen = {}

    def fake(**kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(putnam, "scrape_smartweb_jail_view", fake)
    PutnamCountyScraper.__new__(PutnamCountyScraper).scrape(lookback_days=7)
    assert seen["base_url"] == putnam.BASE_URL
    assert seen["lookback_days"] == 7
    assert seen["county"] == "Putnam"
