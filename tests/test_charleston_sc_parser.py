"""Charleston SC ListView parser — source Inmate # contract."""
from pathlib import Path

from scrapers.base_scraper import BaseScraper
from scrapers.counties_sc.charleston import CharlestonScraper

FIXTURE = Path(__file__).parent / "fixtures" / "charleston_results_sample.html"


def test_charleston_listview_parses_source_inmate_numbers():
    html = FIXTURE.read_text(encoding="utf-8")
    records = CharlestonScraper._parse_listview(html)
    assert len(records) >= 2
    for rec in records:
        assert BaseScraper._has_source_booking_identifier(rec.Booking_Number)
        assert not str(rec.Booking_Number).startswith("CHS_")
        assert rec.Full_Name
        assert rec.Booking_Date
    assert any("BOYLES" in r.Full_Name.upper() for r in records)


def test_charleston_rejects_legacy_synthetic_prefix():
    assert not BaseScraper._has_source_booking_identifier("CHS_abcdef1234")
