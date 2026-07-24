"""Unit tests for Monroe (Keys SO) v2 JSON API scraper — simulated payload."""
import unittest
from unittest.mock import patch

from scrapers.counties.monroe import MonroeCountyScraper

SAMPLE = [
    {
        "filename": "ArrestLog1.json",
        "data": {
            "currentArrests": {"asOf": "Thursday, July 23, 2026 at 20:00"},
            "arrests": [
                {
                    "mugShot": "https://www.keysso.net:8443/webData/ArrestLogs/MCSO78MNI183475.jpg",
                    "mugShotL": "https://www.keysso.net:8443/webData/ArrestLogs/MCSO78MNI183475L.jpg",
                    "ArrestDate": "07/23/2026",
                    "ArrestTime": "13:31",
                    "CADno": "",
                    "OffenseNo": "",
                    "Name": "AVILES, ADRIAN DYLAN",
                    "DoB": " ",
                    "Age": "NA",
                    "Sex": "M",
                    "Race": "W",
                    "Address": "Not Available",
                    "ArrestLocation": " VIVIAN - GEORGE ST, KEY WEST",
                    "Bond": "7500",
                    "Arraignment": "08/12/2026 at 09:00",
                    "Charges": [
                        {"Charge": "1 Misdemeanor Count(s) of 316.061.1 HIT AND RUN"},
                        {"Charge": "1 Misdemeanor Count(s) of 843.02 RESIST OFFICER"},
                    ],
                },
                {
                    # no mugshot → falls back to hash key; junk name filtered below
                    "Name": "DOE, JANE",
                    "ArrestDate": "07/23/2026",
                    "Bond": "NO BOND",
                    "Charges": [],
                },
                {"Name": "", "ArrestDate": "07/23/2026"},  # skipped: empty name
            ],
        },
    },
    {
        "filename": "ArrestLog2.json",
        "data": {
            "arrests": [
                {
                    # duplicate of first record (same MNI) → deduped
                    "mugShot": "https://www.keysso.net:8443/webData/ArrestLogs/MCSO78MNI183475.jpg",
                    "Name": "AVILES, ADRIAN DYLAN",
                    "ArrestDate": "07/23/2026",
                    "Bond": "7500",
                },
            ]
        },
    },
]


class FakeResp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return SAMPLE


def make_scraper():
    return MonroeCountyScraper.__new__(MonroeCountyScraper)


class TestMonroeScraper(unittest.TestCase):
    def _scrape(self):
        with patch("scrapers.counties.monroe.requests.get", return_value=FakeResp()):
            return MonroeCountyScraper.scrape(make_scraper())

    def test_parses_and_dedupes(self):
        recs = self._scrape()
        # 2 valid unique records (empty-name row skipped, dup MNI deduped)
        self.assertEqual(len(recs), 2)

    def test_natural_key_from_mugshot(self):
        recs = self._scrape()
        aviles = next(r for r in recs if r.Last_Name == "AVILES")
        self.assertEqual(aviles.Booking_Number, "MCSO78MNI183475")

    def test_fields(self):
        recs = self._scrape()
        aviles = next(r for r in recs if r.Last_Name == "AVILES")
        self.assertEqual(aviles.County, "Monroe")
        self.assertEqual(aviles.First_Name, "ADRIAN")
        self.assertEqual(aviles.Bond_Amount, "7500.0")
        self.assertEqual(aviles.Court_Date, "08/12/2026")
        self.assertEqual(aviles.Court_Time, "09:00")
        self.assertIn("HIT AND RUN", aviles.Charges)
        self.assertEqual(aviles.Address, "")  # "Not Available" scrubbed

    def test_no_bond_is_zero(self):
        recs = self._scrape()
        doe = next(r for r in recs if r.Last_Name == "DOE")
        self.assertEqual(float(doe.Bond_Amount), 0.0)
        self.assertTrue(doe.Booking_Number.startswith("MONROE-"))

    def test_arraignment_parser(self):
        p = MonroeCountyScraper._parse_arraignment
        self.assertEqual(p("08/12/2026 at 09:00"), ("08/12/2026", "09:00"))
        self.assertEqual(p("08/12/2026"), ("08/12/2026", ""))
        self.assertEqual(p(""), ("", ""))
        self.assertEqual(p("garbage"), ("", ""))


if __name__ == "__main__":
    unittest.main()
