"""Florence SC detail-page charges/bond + Name ID key tests."""
from __future__ import annotations

import unittest
from scrapers.counties_sc.florence import FlorenceScraper


DETAIL_HTML = """
<html><body>
<table>
<tr><td>Name ID</td><td>49073</td></tr>
<tr><td>Age</td><td>54</td></tr>
<tr><td>Race</td><td>B</td></tr>
<tr><td>Sex</td><td>F</td></tr>
<tr><td>Admit Date</td><td>10-05-2026</td></tr>
</table>
<table>
<tr class="dxgvDataRow_MaterialCompact">
  <td>VEHICLE / USE OF VEHICLE WITHOUT PERMISSION</td>
  <td>10-05-2026</td><td></td>
  <td>$1,000.00</td><td>Surety Bond</td>
  <td>FLORENCE COUNTY - SC0210000</td>
</tr>
<tr class="dxgvDataRow_MaterialCompact">
  <td>TRAFFIC / RECKLESS DRIVING</td>
  <td>10-05-2026</td><td></td>
  <td>$250.00</td><td>Cash Bond</td>
  <td>FLORENCE COUNTY - SC0210000</td>
</tr>
</table>
</body></html>
"""

NO_BOND_DETAIL_HTML = """
<html><body>
<table>
<tr><td>Name ID</td><td>44094</td></tr>
<tr><td>Admit Date</td><td>02-05-2025</td></tr>
</table>
<table>
<tr class="dxgvDataRow_MaterialCompact">
  <td>Drugs / Trafficking in Fentanyl, 28 grams or more</td>
  <td>02-05-2025</td><td></td>
  <td></td><td>No Bond</td>
  <td>FLORENCE COUNTY - SC0210000</td>
</tr>
</table>
</body></html>
"""

LIST_HTML = """
<html><body>
<table>
<tr class="dxgvDataRow_MaterialCompact">
  <td><a href="/inmate-details?id=abc&amp;bid=xyz">ADDERLEY, TAMMY TERESA</a></td>
  <td>54</td><td>B</td><td>F</td><td>10/5/2026</td><td></td>
</tr>
</table>
</body></html>
"""


class FlorenceScParserTests(unittest.TestCase):
    def setUp(self):
        self.scraper = FlorenceScraper()

    def test_parse_detail_sums_bonds_and_joins_charges(self):
        info = FlorenceScraper.parse_detail_html(DETAIL_HTML)
        self.assertEqual(info["name_id"], "49073")
        self.assertIn("VEHICLE / USE OF VEHICLE WITHOUT PERMISSION", info["charges"])
        self.assertIn("TRAFFIC / RECKLESS DRIVING", info["charges"])
        self.assertEqual(info["bond"], "1250.00")
        self.assertIn("Surety Bond", info["bond_type"])
        self.assertIn("Cash Bond", info["bond_type"])

    def test_parse_detail_no_bond_stays_zero(self):
        info = FlorenceScraper.parse_detail_html(NO_BOND_DETAIL_HTML)
        self.assertEqual(info["name_id"], "44094")
        self.assertIn("Trafficking in Fentanyl", info["charges"])
        self.assertNotIn("bond", info)  # no $ amount published
        self.assertEqual(info["bond_type"], "No Bond")

    def test_to_record_requires_source_name_id(self):
        row = {
            "name": "ADDERLEY, TAMMY TERESA",
            "age": "54",
            "race": "B",
            "sex": "F",
            "booked": "10/5/2026",
            "detail_url": "https://booking.fcso.org/inmate-details?id=abc&bid=xyz",
        }
        self.assertIsNone(self.scraper._to_record(row, {}))
        rec = self.scraper._to_record(row, FlorenceScraper.parse_detail_html(DETAIL_HTML))
        self.assertIsNotNone(rec)
        self.assertEqual(rec.Booking_Number, "49073")
        self.assertFalse(rec.Booking_Number.startswith("FLO_"))
        self.assertEqual(rec.Bond_Amount, "1250.00")
        self.assertNotEqual(rec.Charges, "Unknown")
        self.assertNotEqual(rec.Charges, "")

    def test_parse_grid_captures_detail_url(self):
        from bs4 import BeautifulSoup

        rows = self.scraper._parse_grid(BeautifulSoup(LIST_HTML, "html.parser"))
        self.assertEqual(len(rows), 1)
        self.assertIn("inmate-details", rows[0]["detail_url"])
        self.assertEqual(rows[0]["name"], "ADDERLEY, TAMMY TERESA")

    def test_source_contract_validated(self):
        self.assertTrue(FlorenceScraper.SOURCE_CONTRACT_VALIDATED)


if __name__ == "__main__":
    unittest.main()
