"""Miami-Dade (FL) ArcGIS jail-bookings layer contract, 2026-10-08.

The layer has no bond field, so the bond stays unknown ("") and is never $0.
There is no placeholder charge. It uses plain requests, and an HTTP/ArcGIS
error, field drift or short page walk raises instead of returning a silently
truncated batch. Synthetic fixtures only; no network.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from dashboard.services.packet_builder_service import arrest_bond_value
from scrapers.counties import miami_dade
from scrapers.counties.miami_dade import MiamiDadeContractError, MiamiDadeCountyScraper

BOOK_MS = int(datetime(2026, 10, 7, 4, tzinfo=timezone.utc).timestamp() * 1000)


def _attrs(oid, **over):
    a = {"ObjectId": oid, "GlobalID": f"gid-{oid}", "BookDate": BOOK_MS, "Defendant": f"DOE, JANE{oid}",
         "DOB": None, "Charge1": "BATTERY", "Code2": None, "Charge3": None}
    a.update(over)
    return a


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code != 200:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._data


def _install(monkeypatch, count, pages, calls=None):
    """pages: list of (features, exceeded) returned in order for non-count queries."""
    queue = list(pages)

    class _S:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None):
            assert url == miami_dade.QUERY_ENDPOINT
            if calls is not None:
                calls.append(dict(params))
            if params.get("returnCountOnly"):
                return count if isinstance(count, _Resp) else _Resp({"count": count})
            item = queue.pop(0)
            if isinstance(item, _Resp):
                return item
            feats, exceeded = item
            return _Resp({"features": [{"attributes": a} for a in feats], "exceededTransferLimit": exceeded})

    monkeypatch.setattr(miami_dade.requests, "Session", _S)
    monkeypatch.setattr(miami_dade.time, "sleep", lambda *_: None)


def test_bond_unknown_and_no_placeholder_charge():
    rec = MiamiDadeCountyScraper()._parse_record(_attrs(1, Charge1=None))
    assert rec is not None
    assert rec.Bond_Amount == ""
    assert rec.Charges == ""
    assert rec.extra_data["bond_published"] is False
    assert rec.Booking_Date == "2026-10-07"


def test_hydrate_treats_new_and_legacy_miami_dade_bond_as_unknown():
    rec = MiamiDadeCountyScraper()._parse_record(_attrs(2))
    doc = rec.to_mongo_doc()
    doc["extra"] = rec.extra_data
    assert arrest_bond_value(doc) == ""
    legacy = {"county": "Miami-Dade", "state": "FL", "bond_amount": 0.0, "bond_amount_raw": "0"}
    assert arrest_bond_value(legacy) == ""
    assert arrest_bond_value({**legacy, "county": "Miami-Dade County"}) == ""
    # a staff-entered amount still wins
    assert arrest_bond_value({**legacy, "bond_amount": 2500.0, "bond_override": True}) == 2500.0


def test_scrape_pages_to_the_server_count(monkeypatch):
    calls: list = []
    _install(monkeypatch, 3, [([_attrs(3), _attrs(2)], True), ([_attrs(1)], False)], calls)
    monkeypatch.setattr(miami_dade, "PAGE_SIZE", 2)
    recs = MiamiDadeCountyScraper().scrape()
    assert [r.Booking_Number for r in recs] == ["", "", ""]  # no source booking number
    assert len(recs) == 3 and all(r.extra_data["md_dedupe"].startswith("md_dedupe_v2:") for r in recs)
    assert calls[0]["returnCountOnly"] == "true"
    assert all("outFields" in c and "Address" not in c["outFields"] and "DOB" in c["outFields"] for c in calls[1:])


@pytest.mark.parametrize(
    "count,pages",
    [
        (_Resp(None, 503), []),                                      # count query fails
        (_Resp({"error": {"code": 400}}), []),                       # ArcGIS error on count
        (2, [_Resp(None, 500)]),                                     # page HTTP error
        (2, [_Resp({"error": {"code": 498}})]),                      # ArcGIS error on page
        (2, [_Resp({"rows": []})]),                                  # no features list
        (2, [([{"ObjectId": 1, "GlobalID": "g", "BookDate": BOOK_MS}], False)]),  # field drift
        (3, [([_attrs(3), _attrs(2)], False)]),                      # short walk
        (2, [([_attrs(2)], True), ([_attrs(2)], False)]),            # repeated ObjectId
    ],
)
def test_scrape_fails_loud_on_errors_and_drift(monkeypatch, count, pages):
    _install(monkeypatch, count, pages)
    monkeypatch.setattr(miami_dade, "PAGE_SIZE", 1)
    with pytest.raises(MiamiDadeContractError):
        MiamiDadeCountyScraper().scrape()


def test_count_over_page_cap_raises(monkeypatch):
    _install(monkeypatch, miami_dade.PAGE_SIZE * miami_dade.MAX_PAGES + 1, [])
    with pytest.raises(MiamiDadeContractError):
        MiamiDadeCountyScraper().scrape()


def test_empty_window_is_not_an_error(monkeypatch):
    _install(monkeypatch, 0, [])
    assert MiamiDadeCountyScraper().scrape() == []


def test_module_uses_plain_requests_only():
    src = open(miami_dade.__file__).read()
    for banned in ("curl_cffi", "impersonate", "verify=False", "DrissionPage", "proxies", "captcha"):
        assert banned not in src, banned
