"""Regression tests — InteropWeb & SmartCOP base scrapers.

Guards against the July 2026 bug where both bases constructed ArrestRecord
with lowercase kwargs (county=, booking_number=, ...) that the canonical
dataclass rejects with TypeError, silently zeroing out ~38 GA/SC counties.

Simulates raw HTML payloads per the project's TDD-for-scrapers rule.
"""
from unittest.mock import MagicMock, patch

import pytest

from scrapers.interopweb_base import InteropWebBaseScraper
from scrapers.smartcop_base import SmartCOPBaseScraper
from core.models import ArrestRecord


INTEROP_HTML = """
<html><body>
<table id="dgInmates">
  <tr><th>Photo</th><th>Name</th><th>Booking Date</th><th>Charges</th><th>Bond</th></tr>
  <tr>
    <td><a href="detail.aspx?id=1"><img src="x.jpg"/></a></td>
    <td>DOE, JOHN A</td>
    <td>07/18/2026</td>
    <td>THEFT BY TAKING - FELONY</td>
    <td>$5,000.00</td>
  </tr>
  <tr>
    <td></td>
    <td>SMITH, JANE</td>
    <td>07/19/2026 10:15:00 AM</td>
    <td>DUI LESS SAFE</td>
    <td></td>
  </tr>
</table>
</body></html>
"""

SMARTCOP_HTML = """
<html><body>
<input type="hidden" name="__VIEWSTATE" value="abc"/>
<input type="hidden" name="__VIEWSTATEGENERATOR" value="def"/>
<input type="hidden" name="__EVENTVALIDATION" value="ghi"/>
<table id="ctl00_ContentPlaceHolder1_GridView1">
  <tr><th>Photo</th><th>Name</th><th>Booking Date</th><th>Charge</th></tr>
  <tr>
    <td></td>
    <td>BROWN, ROBERT</td>
    <td>07/19/2026 03:52 AM</td>
    <td>POSSESSION OF CONTROLLED SUBSTANCE</td>
  </tr>
</table>
</body></html>
"""


class _InteropGA(InteropWebBaseScraper):
    @property
    def county(self):
        return "Bacon"

    @property
    def state(self):
        return "GA"

    @property
    def portal_url(self):
        return "https://www.interopweb.com/bacon/"


class _SmartCopSC(SmartCOPBaseScraper):
    @property
    def county(self):
        return "Sumter"

    @property
    def state(self):
        return "SC"

    @property
    def portal_url(self):
        return "https://portal.example.org/smartwebclient/jail.aspx"


def _mock_response(html: str):
    resp = MagicMock()
    resp.text = html
    resp.raise_for_status = MagicMock()
    return resp


def test_interopweb_parses_canonical_records():
    scraper = _InteropGA()
    with patch("scrapers.interopweb_base.requests.get", return_value=_mock_response(INTEROP_HTML)):
        records = scraper.scrape()

    assert len(records) == 2, "Both roster rows should parse into ArrestRecords"
    rec = records[0]
    assert isinstance(rec, ArrestRecord)
    assert rec.County == "Bacon"
    assert rec.State == "GA", "State must come from scraper.state, not default FL"
    assert rec.Last_Name == "DOE"
    assert rec.First_Name == "JOHN A"
    assert rec.Full_Name.startswith("DOE")
    assert rec.Booking_Date == "2026-07-18"
    assert rec.Bond_Amount == "5000.0"
    assert rec.Booking_Number, "Pseudo booking number must be generated"
    # Dedup key sanity (idempotency axiom)
    assert rec.get_dedup_key() == f"Bacon:{rec.Booking_Number}"

    rec2 = records[1]
    assert rec2.Bond_Amount == "0", "Missing bond should normalize to '0' string"


def test_smartcop_parses_canonical_records():
    scraper = _SmartCopSC()
    session = MagicMock()
    session.get.return_value = _mock_response(SMARTCOP_HTML)
    session.post.return_value = _mock_response(SMARTCOP_HTML)
    with patch("scrapers.smartcop_base.requests.Session", return_value=session):
        records = scraper.scrape()

    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, ArrestRecord)
    assert rec.County == "Sumter"
    assert rec.State == "SC"
    assert rec.Last_Name == "BROWN"
    assert rec.Booking_Date == "2026-07-19"
    assert "POSSESSION" in rec.Charges
    assert rec.Bond_Amount == "0"


def test_base_scraper_provides_self_logger():
    scraper = _InteropGA()
    assert hasattr(scraper, "logger"), "BaseScraper must expose self.logger for subclasses"
    scraper.logger.debug("logger smoke test")


def test_interopweb_missing_table_returns_empty_without_crash():
    scraper = _InteropGA()
    with patch(
        "scrapers.interopweb_base.requests.get",
        return_value=_mock_response("<html><body><p>maintenance</p></body></html>"),
    ):
        records = scraper.scrape()
    assert records == []
