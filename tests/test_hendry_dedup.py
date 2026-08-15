from __future__ import annotations

from scrapers.counties.hendry import HendryCountyScraper


def test_hendry_skips_rows_without_source_issued_inmate_identifier():
    """A name or document ID must never become a synthetic booking key."""
    scraper = HendryCountyScraper()

    record = scraper._parse_inmate(
        {
            "firstName": "JANE",
            "lastName": "DOE",
            "_id": {"$id": "document-only-id"},
        }
    )

    assert record is None


def test_hendry_preserves_source_issued_inmate_identifier_for_deduplication():
    scraper = HendryCountyScraper()

    record = scraper._parse_inmate(
        {
            "inmateID": "SOURCE-12345",
            "firstName": "JANE",
            "lastName": "DOE",
        }
    )

    assert record is not None
    assert record.County == "Hendry"
    assert record.Booking_Number == "SOURCE-12345"
    assert record.get_dedup_key() == "Hendry:SOURCE-12345"
