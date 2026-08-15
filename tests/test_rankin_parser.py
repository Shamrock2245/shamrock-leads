import unittest

from scrapers.counties_ms.rankin import RankinScraper


HTML = '''<table><tr><th>#</th><th>Name</th><th>ID</th><th>Age</th><th>Intake</th><th>Agency</th><th>Charge 1</th></tr>
<tr><td>1</td><td>DOE, JANE</td><td>2026080001</td><td>30</td><td>08/01/2026 12:51 AM</td><td>Rankin County Sheriff's Office</td><td>Ignored</td></tr>
<tr><td>2</td><td>SMITH, JOHN</td><td></td><td>31</td><td>08/01/2026 1:00 AM</td><td>Agency</td><td>Ignored</td></tr></table>'''


class RankinParserTests(unittest.TestCase):
    def test_listing_contract_mapping(self):
        records = RankinScraper()._parse_listing(HTML)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.Booking_Number, '2026080001')
        self.assertEqual(record.Booking_Date, '2026-08-01T00:51:00')
        self.assertEqual(record.State, 'MS')
        self.assertEqual(record.County, 'Rankin')

    def test_required_identity_and_time(self):
        self.assertEqual(RankinScraper()._parse_listing('<table></table>'), [])


if __name__ == '__main__':
    unittest.main()
