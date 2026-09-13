"""PBSO blotter parse + URL ingest. No live PBSO requests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scrapers.pbso_parse import (
    extract_pbso_booking_id,
    is_pbso_host,
    is_pbso_index_only,
    parse_pbso_card_text,
    parse_pbso_html,
    row_to_ingest_dict,
)

PBSO_CARD_HTML = """
<html><body>
<div id="allresults_1">
Name: SENGELMANN, MICHAEL
Race: White
Gender: Male
Arresting Agency: 01-PBSO
Booking Date/Time: 05/15/2026 10:34
Release Date: N/A
Jacket Number: 0428603
Booking Number: 2026012709
0003 BOOKED - COMMIT
Current Bond: $1,500.00
</div>
</body></html>
"""

TWO_CARD_HTML = """
<html><body>
<div id="allresults_1">
Name: SENGELMANN, MICHAEL
Booking Number: 2026012709
Current Bond: $100.00
</div>
<div id="allresults_2">
Name: DOE, JANE
Booking Number: 2026012710
Current Bond: $200.00
</div>
</body></html>
"""

JS_SHELL_HTML = """
<html><body>
<form>
<input id="start_date" />
<input id="end_date" />
<iframe src="https://hcaptcha.com/captcha"></iframe>
</form>
</body></html>
"""


def test_is_pbso_host():
    assert is_pbso_host("https://www3.pbso.org/blotter/index.cfm") is True
    assert is_pbso_host("https://pbso.org/blotter") is True
    assert is_pbso_host("https://sheriffleefl.org/booking/?id=1") is False


def test_extract_pbso_booking_id():
    assert extract_pbso_booking_id(
        "https://www3.pbso.org/blotter/index.cfm?booking=2026012709"
    ) == "2026012709"
    assert extract_pbso_booking_id(
        "https://www3.pbso.org/blotter/index.cfm?booking_number=2026012709"
    ) == "2026012709"
    assert extract_pbso_booking_id(
        "https://www3.pbso.org/blotter/index.cfm"
    ) is None
    assert is_pbso_index_only("https://www3.pbso.org/blotter/index.cfm") is True
    assert is_pbso_index_only(
        "https://www3.pbso.org/blotter/index.cfm?booking=2026012709"
    ) is False


def test_parse_pbso_card_requires_name_and_booking():
    row = parse_pbso_card_text(
        "Name: SENGELMANN, MICHAEL\nBooking Number: 2026012709\nCurrent Bond: $1,500.00\n"
    )
    assert row is not None
    assert row["booking_num"] == "2026012709"
    assert row["last_name"] == "SENGELMANN"
    assert row["first_name"] == "MICHAEL"
    assert row["bond_amount"] == "1500.00"
    assert parse_pbso_card_text("Name: NO BOOKING HERE") is None
    assert parse_pbso_card_text("Booking Number: 2026012709") is None


def test_parse_pbso_html_single_and_multi():
    rows = parse_pbso_html(PBSO_CARD_HTML)
    assert len(rows) == 1
    assert rows[0]["booking_num"] == "2026012709"
    two = parse_pbso_html(TWO_CARD_HTML)
    assert {r["booking_num"] for r in two} == {"2026012709", "2026012710"}
    assert parse_pbso_html(JS_SHELL_HTML) == []


def test_row_to_ingest_dict_is_palm_beach_fl():
    row = parse_pbso_html(PBSO_CARD_HTML)[0]
    data = row_to_ingest_dict(row, "https://www3.pbso.org/blotter/index.cfm?booking=2026012709")
    assert data["county"] == "Palm Beach"
    assert data["state"] == "FL"
    assert data["booking_number"] == "2026012709"
    assert "booking=2026012709" in data["detail_url"]


@pytest.mark.asyncio
async def test_ingest_pbso_html_card(monkeypatch):
    from dashboard.services import url_ingest_service as uis

    class _Resp:
        status_code = 200
        text = PBSO_CARD_HTML

        def raise_for_status(self):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _Resp()

    monkeypatch.setattr(uis.httpx, "AsyncClient", lambda **k: _Client())
    monkeypatch.setattr(uis, "_ingest_pbso_from_mongo", AsyncMock(return_value=None))

    result = await uis.ingest_url(
        "https://www3.pbso.org/blotter/index.cfm?booking=2026012709"
    )
    assert result["success"] is True
    assert result["parse_method"] == "pbso_blotter"
    assert result["data"]["county"] == "Palm Beach"
    assert result["data"]["state"] == "FL"
    assert result["data"]["booking_number"] == "2026012709"


@pytest.mark.asyncio
async def test_ingest_pbso_index_fail_closed(monkeypatch):
    from dashboard.services import url_ingest_service as uis

    class _Resp:
        status_code = 200
        text = JS_SHELL_HTML

        def raise_for_status(self):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _Resp()

    monkeypatch.setattr(uis.httpx, "AsyncClient", lambda **k: _Client())
    result = await uis.ingest_url("https://www3.pbso.org/blotter/index.cfm")
    assert result["success"] is False
    assert "booking" in result["error"].lower() or "javascript" in result["error"].lower()


@pytest.mark.asyncio
async def test_ingest_pbso_multiple_cards_fail_closed(monkeypatch):
    from dashboard.services import url_ingest_service as uis

    class _Resp:
        status_code = 200
        text = TWO_CARD_HTML

        def raise_for_status(self):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _Resp()

    monkeypatch.setattr(uis.httpx, "AsyncClient", lambda **k: _Client())
    result = await uis.ingest_url("https://www3.pbso.org/blotter/index.cfm")
    assert result["success"] is False
    assert "Multiple" in result["error"]


@pytest.mark.asyncio
async def test_ingest_pbso_mongo_palm_beach_only(monkeypatch):
    from dashboard.services import url_ingest_service as uis

    coll = MagicMock()
    coll.find_one = AsyncMock(return_value={
        "booking_number": "2026012709",
        "full_name": "Michael Sengelmann",
        "county": "Palm Beach",
        "state": "FL",
        "charges": "BATTERY",
        "bond_amount": 1500,
    })

    monkeypatch.setattr(uis, "_ingest_pbso_from_mongo", AsyncMock(return_value={
        "booking_number": "2026012709",
        "full_name": "Michael Sengelmann",
        "county": "Palm Beach",
        "state": "FL",
        "charges": "BATTERY",
        "bond_amount": 1500.0,
        "ingestion_method": "pbso_mongo_arrest",
    }))

    result = await uis.ingest_url(
        "https://www3.pbso.org/blotter/index.cfm?booking=2026012709"
    )
    assert result["success"] is True
    assert result["parse_method"] == "pbso_mongo_arrest"
    assert result["data"]["county"] == "Palm Beach"


@pytest.mark.asyncio
async def test_ingest_pbso_from_mongo_rejects_non_fl(monkeypatch):
    from dashboard.services import url_ingest_service as uis

    coll = MagicMock()
    coll.find_one = AsyncMock(return_value={
        "booking_number": "2026012709",
        "full_name": "Someone Else",
        "county": "Palm Beach",
        "state": "GA",
    })
    monkeypatch.setattr("dashboard.extensions.get_db", lambda: {"arrests": coll})
    got = await uis._ingest_pbso_from_mongo(
        "2026012709", "https://www3.pbso.org/blotter/index.cfm?booking=2026012709"
    )
    assert got is None


def test_fetch_single_booking_skips_index_without_http():
    from scrapers.counties.palm_beach import PalmBeachCountyScraper

    scraper = PalmBeachCountyScraper()
    with patch("requests.get") as get:
        rec = scraper._fetch_single_booking(
            "2026012709", "https://www3.pbso.org/blotter/index.cfm"
        )
        assert rec is None
        get.assert_not_called()


def test_fetch_single_booking_parses_html_match():
    from scrapers.counties.palm_beach import PalmBeachCountyScraper

    scraper = PalmBeachCountyScraper()
    resp = MagicMock()
    resp.status_code = 200
    resp.text = PBSO_CARD_HTML
    with patch("requests.get", return_value=resp) as get:
        rec = scraper._fetch_single_booking(
            "2026012709",
            "https://www3.pbso.org/blotter/index.cfm?booking=2026012709",
        )
        get.assert_called_once()
        assert rec is not None
        assert rec.Booking_Number == "2026012709"
        assert rec.County == "Palm Beach"
        assert rec.State == "FL"
        assert rec.Bond_Amount == "1500.00"


def test_fetch_single_booking_mismatch_fail_closed():
    from scrapers.counties.palm_beach import PalmBeachCountyScraper

    scraper = PalmBeachCountyScraper()
    rec = scraper._fetch_single_booking(
        "9999999999",
        "https://www3.pbso.org/blotter/index.cfm?booking=2026012709",
    )
    assert rec is None


def test_watcher_does_not_generic_refetch_pbso_index():
    from core.first_appearance_watcher import FirstAppearanceWatcher
    from scrapers.counties.palm_beach import PalmBeachCountyScraper

    watcher = FirstAppearanceWatcher.__new__(FirstAppearanceWatcher)
    watcher._scrapers = {"Palm Beach": PalmBeachCountyScraper()}
    watcher._generic_refetch = MagicMock(side_effect=AssertionError("generic refetch must not run"))
    result = watcher._refetch_record({
        "county": "Palm Beach",
        "detail_url": "https://www3.pbso.org/blotter/index.cfm",
        "booking_number": "2026012709",
    })
    assert result is None
