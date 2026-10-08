"""FL bond/charges hydrate completeness — Pinellas / Marion / Charlotte /
Manatee / Miami-Dade (PR fix/fl-bond-charges-hydrate-2026-10-07).

Parsers must only emit bond amounts the jail actually publishes. Sources that
do not publish bond stay at Bond_Amount="0" (no invention).
"""
from __future__ import annotations

import unittest

from scrapers.counties.charlotte import CharlotteCountyScraper
from scrapers.counties.manatee import ManateeCountyScraper
from scrapers.counties.marion import MarionCountyScraper
from scrapers.counties.miami_dade import MiamiDadeCountyScraper, OUT_FIELDS
from scrapers.counties.pinellas import PinellasCountyScraper
from core.models import ArrestRecord


PINELLAS_MODAL_FIXTURE = """
Pinellas County Sheriff's Office
Subject Charge Report
Name:
DOE, JANE
Booking #:
2600009999 Inmate #: 123456
Charges
Agency Report Number:
26318000
Offense Description:
PETIT THEFT (FELONY)
Statute:
812.014(3)(c)
Court Case Number:
26-08535-CF
Sequence Number:
1
Bond Assessed:
$5,000.00
Bond Amount Due:
$5,000.00
Charge Status:
AWAITING TRIAL
Agency Report Number:
26318001
Offense Description:
OBSTRUCTING OR RESISTING OFFICER WITHOUT VIOLENCE
Statute:
843.02
Court Case Number:
26-08535-CF
Sequence Number:
2
Bond Assessed:
$1,000.00
Bond Amount Due:
$1,000.00
Charge Status:
AWAITING TRIAL
Close Window
"""

PINELLAS_ZERO_BOND_FIXTURE = """
Subject Charge Report
Offense Description:
DOMESTIC BATTERY
Court Case Number:
26-13184-MM
Bond Assessed:
$0.00
Bond Amount Due:
$0.00
Charge Status:
AWAITING TRIAL
"""

MARION_EMPTY_DETAIL = """
<html><body>
<span>Charge Information</span>
<div>No data was returned.</div>
</body></html>
"""

MARION_CHARGED_DETAIL = """
<html><body>
<span>Charge Information</span>
<table>
  <tr><th>Offense</th><th>Bond Amount</th></tr>
  <tr><td>BURGLARY OF DWELLING</td><td>$10,000.00</td></tr>
  <tr><td>GRAND THEFT</td><td>$2,500.00</td></tr>
</table>
</body></html>
"""


class TestPinellasChargeReportParse(unittest.TestCase):
    def test_sums_bond_assessed_and_joins_offenses(self):
        parsed = PinellasCountyScraper.parse_charge_report_text(PINELLAS_MODAL_FIXTURE)
        self.assertEqual(parsed["bond_amount"], "6000")
        self.assertIn("PETIT THEFT (FELONY)", parsed["charges"])
        self.assertIn("OBSTRUCTING OR RESISTING OFFICER WITHOUT VIOLENCE", parsed["charges"])
        self.assertIn("26-08535-CF", parsed["case_numbers"])

    def test_published_zero_bond_stays_zero(self):
        parsed = PinellasCountyScraper.parse_charge_report_text(PINELLAS_ZERO_BOND_FIXTURE)
        self.assertEqual(parsed["bond_amount"], "0")
        self.assertEqual(parsed["charges"], "DOMESTIC BATTERY")

    def test_row_to_record_uses_parsed_bond(self):
        scraper = PinellasCountyScraper()
        rec = scraper._row_to_record(
            {
                "name": "DOE, JANE",
                "booking_num": "2600009999",
                "inmate_num": "123",
                "booking_dt": "10/7/2026 6:29:22 AM",
                "custody": "In Custody",
                "race": "WHITE",
                "sex": "FEMALE",
                "dob": "01/04/1999",
                "charge": "PETIT THEFT",
                "bond_amount": "5000",
                "case_number": "26-08535-CF",
            }
        )
        self.assertIsNotNone(rec)
        self.assertEqual(rec.Bond_Amount, "5000")
        self.assertEqual(rec.Case_Number, "26-08535-CF")
        self.assertEqual(rec.Charges, "PETIT THEFT")

    def test_missing_bond_stays_unknown_not_zero(self):
        scraper = PinellasCountyScraper()
        rec = scraper._row_to_record(
            {
                "name": "DOE, JOHN",
                "booking_num": "2600000001",
                "charge": "BATTERY",
            }
        )
        self.assertIsNotNone(rec)
        self.assertEqual(rec.Bond_Amount, "")  # modal not read: unknown, never $0


class TestMarionDetailChargeParse(unittest.TestCase):
    def test_empty_charge_section_stays_blank_and_zero(self):
        parsed = MarionCountyScraper.parse_detail_charges(MARION_EMPTY_DETAIL)
        self.assertEqual(parsed["charges"], "")
        self.assertEqual(parsed["bond_amount"], "0")

    def test_table_charges_and_bond_sum(self):
        parsed = MarionCountyScraper.parse_detail_charges(MARION_CHARGED_DETAIL)
        self.assertIn("BURGLARY OF DWELLING", parsed["charges"])
        self.assertIn("GRAND THEFT", parsed["charges"])
        self.assertEqual(parsed["bond_amount"], "12500")


class TestCharlotteManateeBondHonest(unittest.TestCase):
    def test_charlotte_record_keeps_bond_unknown_with_charge(self):
        # Charlotte builds records through the shared Revize contract
        # (2026-10-07): the roster publishes no bond, so Bond_Amount is "" (unknown), never "0".
        from scrapers.counties.charlotte import ROSTER

        rec = ROSTER.build_records([{
            "booking": "12345", "last": "DOE", "first": "JANE", "middle": "",
            "charge": "BATTERY", "arrest_date": "2026-10-07", "arrest_time": "",
            "status": "In Custody", "release_date": "", "detail_url": "", "mugshot_url": "",
        }])[0]
        self.assertEqual(rec.Bond_Amount, "")
        self.assertEqual(rec.Charges, "BATTERY")
        self.assertEqual(rec.Facility, "Charlotte County Jail")
        self.assertEqual(CharlotteCountyScraper().county, "Charlotte")

    def test_manatee_record_keeps_bond_unknown_with_charge(self):
        # Manatee now builds records via build_records (2026-10-07 Sarasota/Manatee audit): the roster
        # publishes no bond, so Bond_Amount is "" (unknown), never "0".
        from scrapers.counties.manatee import build_records

        rec = build_records([{
            "booking": "67890", "last": "DOE", "first": "JOHN", "middle": "",
            "charge": "THEFT", "arrest_date": "2026-10-07", "arrest_time": "",
            "status": "In Custody", "release_date": "", "detail_url": "", "mugshot_url": "",
        }])[0]
        self.assertEqual(rec.Bond_Amount, "")
        self.assertEqual(rec.Charges, "THEFT")
        self.assertEqual(ManateeCountyScraper().county, "Manatee")


class TestMiamiDadeNoBondLayer(unittest.TestCase):
    def test_out_fields_include_code2_exclude_pii(self):
        self.assertIn("Code2", OUT_FIELDS)
        self.assertIn("Charge1", OUT_FIELDS)
        self.assertIn("Charge3", OUT_FIELDS)
        self.assertNotIn("Address", OUT_FIELDS)
        self.assertNotIn("DOB", OUT_FIELDS)

    def test_parse_leaves_bond_unknown_and_reads_code2(self):
        from datetime import datetime, timezone

        scraper = MiamiDadeCountyScraper()
        book_ms = int(datetime(2026, 10, 7, tzinfo=timezone.utc).timestamp() * 1000)
        rec = scraper._parse_record(
            {
                "GlobalID": "gid-1",
                "ObjectId": 1,
                "BookDate": book_ms,
                "Defendant": "DOE, JANE",
                "Charge1": "BENCH WARRANT",
                "Code2": "FTA",
                "Charge3": "",
            }
        )
        self.assertIsNotNone(rec)
        self.assertEqual(rec.Bond_Amount, "")  # layer has no bond field: unknown, never $0
        self.assertEqual(rec.Charges, "BENCH WARRANT | FTA")


if __name__ == "__main__":
    unittest.main()
