import unittest

from scrapers.counties_al.baldwin import BaldwinScraper


CARD_WITH_SOURCE_ID = """
<div class="booking-card">
  <h5>DOE, JANE Q</h5>
  <div>Booked: 08/14/2026 02:35 PM</div>
  <div>Arrest Date/Time: 08/14/2026 01:45 PM</div>
  <div>Arresting Agency: Example Agency</div>
  <div>Bond Total: $1,000.00</div>
  <div class="charge-item">Example Charge Bond: $1,000.00</div>
  <a href="viewbooking.php?BookingID=ABC-24590">View Full</a>
</div>
"""


class TestSouthernSoftwareSafety(unittest.TestCase):
    def setUp(self):
        self.scraper = BaldwinScraper()

    def test_maps_only_source_issued_booking_identifier(self):
        records = self.scraper._parse_booking_cards(CARD_WITH_SOURCE_ID)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.County, "Baldwin")
        self.assertEqual(record.State, "AL")
        self.assertEqual(record.Booking_Number, "ABC-24590")
        self.assertEqual(record.Booking_Date, "08/14/2026 02:35 PM")
        self.assertEqual(record.Status, "Unknown")
        self.assertEqual(
            record.extra_data["booking_key_origin"],
            "source-issued Citizen Connect booking/inmate ID",
        )

    def test_missing_source_identifier_fails_closed(self):
        without_id = CARD_WITH_SOURCE_ID.replace(' href="viewbooking.php?BookingID=ABC-24590"', "")
        self.assertEqual(self.scraper._parse_booking_cards(without_id), [])

    def test_missing_booking_date_fails_closed(self):
        without_booked = CARD_WITH_SOURCE_ID.replace("08/14/2026 02:35 PM", "")
        self.assertEqual(self.scraper._parse_booking_cards(without_booked), [])

    def test_labeled_source_identifier_is_supported(self):
        labeled = CARD_WITH_SOURCE_ID.replace(
            '<a href="viewbooking.php?BookingID=ABC-24590">View Full</a>',
            '<div>Booking #: CARD-24590</div>',
        )
        records = self.scraper._parse_booking_cards(labeled)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].Booking_Number, "CARD-24590")


if __name__ == "__main__":
    unittest.main()
