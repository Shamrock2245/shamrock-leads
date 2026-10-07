import unittest

from scrapers.counties_sc.newberry import NewberryScraper


class NewberryPdfScraperTests(unittest.TestCase):
    def setUp(self):
        self.scraper = NewberryScraper()

    def test_discovers_only_sheriff_pdf_uploads(self):
        html = """
        <a href="/sites/default/files/uploads/departments/sheriff-s-office/current-bookings.pdf">Bookings</a>
        <a href="/sites/default/files/uploads/departments/planning-zoning_fees.pdf">Fees</a>
        """

        self.assertEqual(
            self.scraper._discover_sheriff_pdf_urls(html),
            [
                "https://www.newberrycounty.gov/sites/default/files/uploads/"
                "departments/sheriff-s-office/current-bookings.pdf"
            ],
        )

    def test_parses_legacy_so_identifier_and_dollar_bond(self):
        text = """
        TESTER, PERSON
        Booked 08/12/2026
        SO# ABC-123
        Bond $1,500.00
        Petit Larceny

        SKIPPED, PERSON
        Booked 08/12/2026
        Bond $2,000.00
        """

        records = self.scraper._parse_pdf_text(
            text,
            "https://www.newberrycounty.gov/current-bookings.pdf",
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].Booking_Number, "SO-ABC-123")
        self.assertEqual(records[0].Bond_Amount, "1500.00")
        self.assertEqual(records[0].County, "Newberry")
        self.assertEqual(records[0].State, "SC")
        self.assertIn("Petit Larceny", records[0].Charges)

    def test_parses_modern_layout_charges_without_inventing_bond(self):
        text = """
Prisoners/Charges Booked In by Date Range10/06/2026 Page 1
09/28/2026
HUGHES, OMARIAN TREJOHNATHON - SO-0024345 - 25
 Released 09/29/2026 - BOND POSTED
TRAFFIC / UNINSURED MOTOR VEHICLE FEE VIOLATION, 1
DUS / DRIVING UNDER SUSPENSION, LICENSE NOT SUSPEN
TRAFFIC / RECKLESS DRIVING
FLORES, MYA LEANNA - NP-0019645 - 25
 Released 09/29/2026 - BOND POSTED
SHOPLIFTING / SHOPLIFTING, VALUE $2,000 OR LESS
SHOPLIFTING / SHOPLIFTING, VALUE $2,000 OR LESS
MILLS, KAMRYN RENEE - HP-0005878 - 25
 Released 09/28/2026 - SELF
"""
        records = self.scraper._parse_pdf_text(
            text,
            "https://www.newberrycounty.gov/current-bookings.pdf",
        )
        by_key = {r.Booking_Number: r for r in records}
        self.assertIn("SO-0024345", by_key)
        self.assertIn("NP-0019645", by_key)
        self.assertIn("HP-0005878", by_key)

        so = by_key["SO-0024345"]
        self.assertIn("TRAFFIC / UNINSURED MOTOR VEHICLE", so.Charges)
        self.assertIn("RECKLESS DRIVING", so.Charges)
        # "BOND POSTED" is a release label, not a dollar amount.
        self.assertEqual(so.Bond_Amount, "0")
        self.assertEqual(so.Booking_Date, "09/28/2026")
        self.assertNotEqual(so.Charges, "Unknown")

        np = by_key["NP-0019645"]
        # Dedup identical repeated charge lines (one unique charge string).
        self.assertEqual(np.Charges.count(" | "), 0)
        self.assertIn("SHOPLIFTING", np.Charges)
        # $2,000 inside statute text is not a bond dollar.
        self.assertEqual(np.Bond_Amount, "0")

        hp = by_key["HP-0005878"]
        self.assertEqual(hp.Charges, "")  # no charge lines published

    def test_source_contract_validated(self):
        self.assertTrue(NewberryScraper.SOURCE_CONTRACT_VALIDATED)


if __name__ == "__main__":
    unittest.main()
