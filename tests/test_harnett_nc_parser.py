"""Harnett (NC) Citizen Connect — source BookingID only."""
from __future__ import annotations

import unittest

from scrapers.counties_nc.harnett import HarnettScraper

# Synthetic card shaped like Citizen Connect Harnett cards (no live PII).
HARNETT_CARD = """
<div class="card booking-card">
  <div class="card-body">
    <div class="booking-header"><h5 class="mb-0">DOE, JANE Q</h5></div>
    <div>Demographics: 40 years / W / F</div>
    <div>Booked: 10/01/2026</div>
    <div>Arresting Agency: HARNETT - NC0430000</div>
    <div>Arrest Date/Time: 10/01/2026 09:15</div>
    <div>Bond Total: $5,000.00</div>
    <div class="charge-item">1 MISDEMEANOR LARCENY Bond: $5,000.00</div>
    <!-- Debug: JMSAgencyID=NC0430000 NameID=9999 BookingID=4242 -->
    <img class="booking-mugshot" data-bookingid="4242" data-json-nameid="9999" alt="Booking photo" />
    <a href="../../bookingdetails/index.php?BookingID=4242&AgencyID=HarnettCoNC">View Full Details</a>
  </div>
</div>
"""


class TestHarnettNcParser(unittest.TestCase):
    def setUp(self):
        self.scraper = HarnettScraper()

    def test_contract_flags(self):
        self.assertTrue(self.scraper.SOURCE_CONTRACT_VALIDATED)
        self.assertEqual(self.scraper.county, "Harnett")
        self.assertEqual(self.scraper.state, "NC")
        self.assertEqual(self.scraper.agency_id, "HarnettCoNC")
        self.assertIn("BookingID", self.scraper.SOURCE_CONTRACT_REASON)

    def test_maps_source_booking_id_not_nameid(self):
        records = self.scraper._parse_booking_cards(HARNETT_CARD)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.County, "Harnett")
        self.assertEqual(rec.State, "NC")
        self.assertEqual(rec.Booking_Number, "4242")
        self.assertNotEqual(rec.Booking_Number, "9999")
        self.assertEqual(rec.Booking_Date, "10/01/2026")
        self.assertEqual(rec.Bond_Amount, "5000.00")
        self.assertIn("MISDEMEANOR LARCENY", rec.Charges)
        self.assertEqual(
            rec.extra_data["booking_key_origin"],
            "source-issued Citizen Connect booking/inmate ID",
        )

    def test_missing_booking_id_emits_nothing(self):
        stripped = (
            HARNETT_CARD.replace("BookingID=4242", "x=1")
            .replace('data-bookingid="4242"', "")
            .replace("BookingID=4242", "x=1")
        )
        # Also strip debug comment remnant
        stripped = stripped.replace("BookingID=4242", "")
        self.assertEqual(self.scraper._parse_booking_cards(stripped), [])


if __name__ == "__main__":
    unittest.main()
