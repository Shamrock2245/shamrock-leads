"""Mecklenburg NC Inmate Inquiry parser tests (synthetic fixtures, no network)."""
from __future__ import annotations

import unittest

from scrapers.counties_nc.mecklenburg import MecklenburgScraper


SEARCH_PAGE = [
    {
        "DetailsUrl": "/Inmate/Details?a=abc",
        "DobFormatted": "7/28/1983",
        "Page": 1,
        "RN": 1,
        "IsActive": "Y",
        "Name": "ABBITT, ARMENTRICE",
        "FirstName": "ARMENTRICE",
        "LastName": "ABBITT",
        "MiddleName": "MAE",
        "PID": "0000463981",
        "JID": "26-125813",
        "Race": "B",
        "Ethnicity": "B",
        "Sex": "F",
        "ArrestNumber": "1962271",
        "TotalRows": 2224,
    },
    {
        "DetailsUrl": "/Inmate/Details?a=def",
        "DobFormatted": "1/1/1990",
        "Page": 1,
        "Name": "DOE, JANE",
        "FirstName": "JANE",
        "LastName": "DOE",
        "MiddleName": "",
        "PID": "0000999999",
        "JID": "25-105498",
        "Race": "W",
        "Sex": "F",
        "ArrestNumber": "1111111",
        "TotalRows": 2224,
    },
    # Missing JID and ArrestNumber — must be skipped (no MECK_ invent).
    {
        "Name": "NOKEY, PERSON",
        "FirstName": "PERSON",
        "LastName": "NOKEY",
        "PID": "0000000001",
        "JID": "",
        "ArrestNumber": "",
        "TotalRows": 2224,
    },
    # Invalid JID shape — fall back to ArrestNumber.
    {
        "Name": "FALLBACK, BOB",
        "FirstName": "BOB",
        "LastName": "FALLBACK",
        "PID": "0000000002",
        "JID": "bad",
        "ArrestNumber": "1234567",
        "TotalRows": 2224,
    },
]

SUMMARY = {
    "DobFormatted": "7/28/1983",
    "CommitedFormatted": "8/10/2026",
    "OBID": "2684400",
    "Name": "ABBITT, ARMENTRICE MAE",
    "ImprisonmentStatus": "STATE INMATE",
    "PID": "0000463981",
    "JID": "26-125813",
    "Race": "B",
    "Sex": "F",
    "ArrestNumber": "1962271",
    "FirstName": "ARMENTRICE",
    "LastName": "ABBITT",
}

CHARGES = [
    {
        "Reference": "001",
        "OffenseCode": "340100",
        "DISP": "A",
        "ActualBailAmount": "1000",
        "Description": "POSSESS DRUG PARAPHERNALIA",
        "CaseNumber": "26CR379149",
    },
    {
        "Reference": "002",
        "OffenseCode": "291200",
        "DISP": "A",
        "ActualBailAmount": "1000",
        "Description": "INJURY TO PERSONAL PROPERTY",
        "CaseNumber": "26CR377000",
    },
]

CHARGES_NO_BAIL = [
    {
        "Reference": "001",
        "ActualBailAmount": "",
        "Description": "HOLD FOR USMS",
    }
]


class MecklenburgNcParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scraper = MecklenburgScraper()

    def test_source_contract_flag(self) -> None:
        self.assertTrue(self.scraper.SOURCE_CONTRACT_VALIDATED)
        self.assertIn("JID", self.scraper.SOURCE_CONTRACT_REASON)

    def test_listing_uses_jid_not_invented_keys(self) -> None:
        records = self.scraper.parse_search_payload(SEARCH_PAGE)
        keys = {r.Booking_Number for r in records}
        self.assertEqual(keys, {"26-125813", "25-105498", "1234567"})
        self.assertTrue(all(not k.startswith("MECK_") for k in keys))
        self.assertEqual(len(records), 3)

    def test_rejects_empty_booking_key(self) -> None:
        records = self.scraper.parse_search_payload(
            [{"Name": "X", "JID": "", "ArrestNumber": "", "PID": "1"}]
        )
        self.assertEqual(records, [])

    def test_detail_sums_published_bail_only(self) -> None:
        rec = self.scraper.record_from_detail(SEARCH_PAGE[0], SUMMARY, CHARGES)
        assert rec is not None
        self.assertEqual(rec.Booking_Number, "26-125813")
        self.assertEqual(rec.Booking_Date, "8/10/2026")
        self.assertIn("POSSESS DRUG PARAPHERNALIA", rec.Charges)
        self.assertIn("INJURY TO PERSONAL PROPERTY", rec.Charges)
        self.assertEqual(rec.Bond_Amount, "2000")

    def test_empty_bail_stays_zero_not_invented(self) -> None:
        rec = self.scraper.record_from_detail(
            SEARCH_PAGE[0], SUMMARY, CHARGES_NO_BAIL
        )
        assert rec is not None
        self.assertEqual(rec.Charges, "HOLD FOR USMS")
        self.assertEqual(rec.Bond_Amount, "0")

    def test_non_numeric_bail_ignored(self) -> None:
        rows = [
            {
                "Description": "FOO",
                "ActualBailAmount": "SECURED",
            },
            {
                "Description": "BAR",
                "ActualBailAmount": "500.50",
            },
        ]
        charges, bond = self.scraper._parse_charges(rows)
        self.assertEqual(charges, "FOO | BAR")
        self.assertEqual(bond, "500.50")


if __name__ == "__main__":
    unittest.main()
