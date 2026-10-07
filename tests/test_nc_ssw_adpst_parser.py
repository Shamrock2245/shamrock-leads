"""NC SSW ADPST (Anson/Duplin/Polk/Scotland/Transylvania) — source BookingID only."""
from __future__ import annotations

import unittest

from scrapers.counties_nc.anson import AnsonScraper
from scrapers.counties_nc.duplin import DuplinScraper
from scrapers.counties_nc.polk import PolkScraper
from scrapers.counties_nc.scotland import ScotlandScraper
from scrapers.counties_nc.transylvania import TransylvaniaScraper


def _card(agency: str, booking_id: str = "4242", name_id: str = "9999") -> str:
    return f"""
<div class="card booking-card">
  <div class="card-body">
    <div class="booking-header"><h5 class="mb-0">DOE, JANE Q</h5></div>
    <div>Demographics: 40 years / W / F</div>
    <div>Booked: 10/01/2026</div>
    <div>Arresting Agency: SAMPLE - NC0000000</div>
    <div>Arrest Date/Time: 10/01/2026 09:15</div>
    <div>Bond Total: $5,000.00</div>
    <div class="charge-item">1 MISDEMEANOR LARCENY Bond: $5,000.00</div>
    <!-- Debug: JMSAgencyID=NC0000000 NameID={name_id} BookingID={booking_id} -->
    <img class="booking-mugshot" data-bookingid="{booking_id}" data-json-nameid="{name_id}" alt="Booking photo" />
    <a href="../../bookingdetails/index.php?BookingID={booking_id}&AgencyID={agency}">View Full Details</a>
  </div>
</div>
"""


CASES = [
    (AnsonScraper, "Anson", "AnsonCoNC"),
    (DuplinScraper, "Duplin", "DuplinCoNC"),
    (PolkScraper, "Polk", "PolkCoNC"),
    (ScotlandScraper, "Scotland", "ScotlandCoNC"),
    (TransylvaniaScraper, "Transylvania", "TransylvaniaCoNC"),
]


class TestNcSswAdpstParser(unittest.TestCase):
    def test_contract_flags_and_agency(self):
        for cls, county, agency in CASES:
            with self.subTest(county=county):
                scraper = cls()
                self.assertTrue(scraper.SOURCE_CONTRACT_VALIDATED)
                self.assertEqual(scraper.county, county)
                self.assertEqual(scraper.state, "NC")
                self.assertEqual(scraper.agency_id, agency)
                self.assertIn("BookingID", scraper.SOURCE_CONTRACT_REASON)

    def test_maps_source_booking_id_not_nameid(self):
        for cls, county, agency in CASES:
            with self.subTest(county=county):
                scraper = cls()
                records = scraper._parse_booking_cards(_card(agency))
                self.assertEqual(len(records), 1)
                rec = records[0]
                self.assertEqual(rec.County, county)
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
        for cls, county, agency in CASES:
            with self.subTest(county=county):
                scraper = cls()
                stripped = (
                    _card(agency)
                    .replace("BookingID=4242", "x=1")
                    .replace('data-bookingid="4242"', "")
                )
                self.assertEqual(scraper._parse_booking_cards(stripped), [])


if __name__ == "__main__":
    unittest.main()
