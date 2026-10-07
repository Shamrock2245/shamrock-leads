"""Davidson NC DCInmates parser tests (synthetic fixtures, no network)."""
from __future__ import annotations

import unittest
from xml.etree import ElementTree as ET

from scrapers.counties_nc.davidson import DavidsonScraper


ROSTER_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<rows total_count="3">
  <row id="17942">
    <cell><![CDATA[STEWARD-APONTE]]></cell>
    <cell><![CDATA[ASHLEY]]></cell>
    <cell><![CDATA[MARIAH]]></cell>
    <cell><![CDATA[<a href="javascript:doVw('17942', '26-003404');">STEWARD-APONTE, ASHLEY</a>]]></cell>
    <cell><![CDATA[404 NORTHVIEW DR]]></cell>
    <cell><![CDATA[LEXINGTON]]></cell>
    <cell><![CDATA[NC]]></cell>
    <cell><![CDATA[F]]></cell>
    <cell><![CDATA[30]]></cell>
    <cell><![CDATA[5'04"]]></cell>
    <cell><![CDATA[158]]></cell>
    <cell><![CDATA[26-003404]]></cell>
    <cell><![CDATA[17942]]></cell>
  </row>
  <row id="1">
    <cell><![CDATA[NOKEY]]></cell>
    <cell><![CDATA[PERSON]]></cell>
    <cell><![CDATA[]]></cell>
    <cell><![CDATA[NOKEY, PERSON]]></cell>
    <cell><![CDATA[]]></cell>
    <cell><![CDATA[]]></cell>
    <cell><![CDATA[NC]]></cell>
    <cell><![CDATA[M]]></cell>
    <cell><![CDATA[40]]></cell>
    <cell><![CDATA[]]></cell>
    <cell><![CDATA[]]></cell>
    <cell><![CDATA[]]></cell>
    <cell><![CDATA[1]]></cell>
  </row>
  <row id="2">
    <cell><![CDATA[BAD]]></cell>
    <cell><![CDATA[KEY]]></cell>
    <cell><![CDATA[]]></cell>
    <cell><![CDATA[BAD, KEY]]></cell>
    <cell><![CDATA[]]></cell>
    <cell><![CDATA[]]></cell>
    <cell><![CDATA[NC]]></cell>
    <cell><![CDATA[M]]></cell>
    <cell><![CDATA[20]]></cell>
    <cell><![CDATA[]]></cell>
    <cell><![CDATA[]]></cell>
    <cell><![CDATA[DAV_FAKE]]></cell>
    <cell><![CDATA[2]]></cell>
  </row>
</rows>
"""

DETAILS_HTML = """
<div>Davidson County Inmate Details</div>
<span>ASHLEY MARIAH STEWARD-APONTE</span>
Race: W<br />
<h3>Bail Bonds</h3>
<table>
<tr><td>Bond Type</td><td>Initial Amt.</td><td>Remaining</td></tr>
<tr><td>SECURED</td><td>$4,000.00</td><td>$4,000.00</td></tr>
<tr><td>Totals:</td><td>$4,000.00</td><td>$4,000.00</td></tr>
</table>
Incarceration Date: 7/13/2026 10:26:44 AM
Arrest Date: 7/13/2026 11:56:26 AM
<table>
<tr>
  <td>Offense Date/Time</td><td>Counts</td><td>Offense Description</td>
  <td>Related Incident</td><td>Court Reference</td>
</tr>
<tr>
  <td>7/13/2026 10:26:44 AM</td><td>1</td><td>POSSESS STOLEN MOTOR VEHICLE</td>
  <td>26L003361</td><td>26CR355981-280</td>
</tr>
</table>
"""


class DavidsonNcParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scraper = DavidsonScraper()

    def test_roster_keeps_source_booking_skips_empty_and_invented(self) -> None:
        rows, total = self.scraper._parse_roster_xml(ROSTER_XML)
        self.assertEqual(total, 3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["booking"], "26-003404")
        self.assertEqual(rows[0]["in_num"], "17942")
        self.assertEqual(rows[0]["first"], "ASHLEY")
        self.assertEqual(rows[0]["last"], "STEWARD-APONTE")
        # Ensure no invented-key fallback assignment remains.
        import scrapers.counties_nc.davidson as mod
        src = open(mod.__file__).read()
        self.assertNotIn('f"DAV_', src)
        self.assertNotIn("f'DAV_", src)
        self.assertNotIn('DAV_{', src)

    def test_details_parse_incarceration_charges_bond(self) -> None:
        parsed = self.scraper._parse_details_html(DETAILS_HTML)
        self.assertIsNotNone(parsed)
        booking_date, charges, bond, race = parsed
        self.assertEqual(booking_date, "7/13/2026 10:26:44 AM")
        self.assertIn("POSSESS STOLEN MOTOR VEHICLE", charges)
        self.assertEqual(bond, "4000")
        self.assertEqual(race, "W")

    def test_source_contract_validated(self) -> None:
        self.assertTrue(self.scraper.SOURCE_CONTRACT_VALIDATED)
        self.assertIn("YY-######", self.scraper.SOURCE_CONTRACT_REASON)


if __name__ == "__main__":
    unittest.main()
