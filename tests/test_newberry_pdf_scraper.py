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

    def test_parses_only_rows_with_source_so_identifier(self):
        text = """
        TESTER, PERSON
        Booked 08/12/2026
        SO# ABC-123
        Bond $1,500.00

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


if __name__ == "__main__":
    unittest.main()
