"""Lancaster SC NewWorld detail/listing parser tests (synthetic fixtures, no network)."""
from __future__ import annotations

import unittest

from scrapers.counties_sc.lancaster import LancasterScraper


LISTING_HTML = """
<html><body>
<table>
<thead><tr>
  <th class="Name">Name</th><th class="InCustody">In Custody</th>
  <th class="Race">Race</th><th class="Gender">Gender</th>
</tr></thead>
<tbody>
<tr>
  <td class="Name"><a href="/NewWorld.InmateInquiry/SC0290000/Inmate/Detail/-1001">DOE, JANE Q</a></td>
  <td class="InCustody">Yes</td><td class="Race">White</td><td class="Gender">Female</td>
</tr>
<tr>
  <td class="Name"><a href="/NewWorld.InmateInquiry/SC0290000/Inmate/Detail/-1002">SMITH, JOHN A</a></td>
  <td class="InCustody">Yes</td><td class="Race">Black or African American</td><td class="Gender">Male</td>
</tr>
</tbody>
</table>
<a href="/NewWorld.InmateInquiry/SC0290000?InCustody=True&amp;Page=2">Next</a>
</body></html>
"""

DETAIL_HTML = """
<html><body>
<h1>Inmate Detail - DOE, JANE Q</h1>
<ul class="FieldList">
<li class="Name"><label>Name</label><span>DOE, JANE Q</span></li>
<li class="Age"><label>Age</label><span>41</span></li>
<li class="Gender"><label>Gender</label><span>Female</span></li>
<li class="Race"><label>Race</label><span>White</span></li>
</ul>
<div class="Booking">
  <h3><label>Booking</label><span>2026-00001234</span></h3>
  <div class="BookingData">
    <ul class="FieldList">
      <li class="BookingDate"><label>Booking Date</label><span>10/01/2026 9:15 AM</span></li>
      <li class="TotalBondAmount"><label>Total Bond Amount</label><span>$1,500.00</span></li>
      <li class="TotalBailAmount"><label>Total Bail Amount</label><span>$0.00</span></li>
      <li class="BookingOrigin"><label>Booking Origin</label><span>Lancaster County Sheriff&#39;s Office</span></li>
    </ul>
    <div class="BookingBonds">
      <table border="1">
        <thead><tr><th class="BondType">Bond Type</th>
        <th class="BondAmount">Bond Amount</th><th class="BondStatus">Bond Status</th></tr></thead>
        <tbody><tr><td>Surety</td><td>$1,500.00</td><td>Active</td></tr></tbody>
      </table>
    </div>
    <div class="BookingCharges">
      <table border="1">
        <thead><tr><th class="SeqNumber">Number</th>
        <th class="ChargeDescription">Charge Description</th>
        <th class="ChargeBond">Bond</th></tr></thead>
        <tbody>
          <tr><td class="SeqNumber">2</td>
              <td class="ChargeDescription">SIMPLE ASSAULT</td>
              <td class="ChargeBond"></td></tr>
          <tr><td class="SeqNumber">1</td>
              <td class="ChargeDescription">PUBLIC DISORDERLY CONDUCT</td>
              <td class="ChargeBond">2026-00009999</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
</body></html>
"""

ZERO_BOND_DETAIL_HTML = """
<html><body>
<ul class="FieldList">
<li class="Name"><label>Name</label><span>ROE, RICHARD</span></li>
</ul>
<div class="Booking">
  <h3><label>Booking</label><span>2025-00000001</span></h3>
  <ul class="FieldList">
    <li class="BookingDate"><label>Booking Date</label><span>1/2/2025 8:00 AM</span></li>
    <li class="TotalBondAmount"><label>Total Bond Amount</label><span>$0.00</span></li>
  </ul>
  <div class="BookingCharges">
    <table border="1">
      <thead><tr><th>Number</th><th>Charge Description</th><th>Bond</th></tr></thead>
      <tbody><tr><td>1</td><td>BENCH WARRANT</td><td></td></tr></tbody>
    </table>
  </div>
</div>
</body></html>
"""

NO_BOOKING_DETAIL_HTML = """
<html><body>
<ul class="FieldList">
<li class="Name"><label>Name</label><span>NOKEY, PERSON</span></li>
</ul>
<div class="Booking">
  <h3><label>Booking</label><span></span></h3>
</div>
</body></html>
"""


class LancasterScParserTests(unittest.TestCase):
    def setUp(self):
        self.scraper = LancasterScraper()

    def test_listing_links(self):
        links = LancasterScraper.parse_listing_links(LISTING_HTML)
        self.assertEqual(len(links), 2)
        self.assertEqual(links[0][0], "DOE, JANE Q")
        self.assertIn("/Inmate/Detail/-1001", links[0][1])

    def test_parse_detail_booking_charges_bond(self):
        info = LancasterScraper.parse_detail_html(DETAIL_HTML)
        self.assertEqual(info["booking"], "2026-00001234")
        self.assertEqual(info["bond"], "1500.00")
        self.assertIn("SIMPLE ASSAULT", info["charges"])
        self.assertIn("PUBLIC DISORDERLY CONDUCT", info["charges"])
        # Per-charge Bond cell is a reference id — must not become Bond_Amount.
        self.assertNotIn("9999", info["bond"])
        self.assertEqual(info["bond_type"], "Surety")

    def test_zero_bond_stays_published_zero(self):
        info = LancasterScraper.parse_detail_html(ZERO_BOND_DETAIL_HTML)
        self.assertEqual(info["booking"], "2025-00000001")
        self.assertEqual(info["bond"], "0.00")
        self.assertIn("BENCH WARRANT", info["charges"])

    def test_record_requires_source_booking(self):
        self.assertIsNone(
            self.scraper.parse_detail_to_record(
                NO_BOOKING_DETAIL_HTML,
                fallback_name="NOKEY, PERSON",
                detail_url="https://example.test/Inmate/Detail/-9",
            )
        )
        rec = self.scraper.parse_detail_to_record(
            DETAIL_HTML,
            fallback_name="DOE, JANE Q",
            detail_url="https://inmate.lancastercountysc.net/NewWorld.InmateInquiry/SC0290000/Inmate/Detail/-1001",
        )
        self.assertIsNotNone(rec)
        self.assertEqual(rec.Booking_Number, "2026-00001234")
        self.assertFalse(rec.Booking_Number.startswith("NW_"))
        self.assertEqual(rec.Bond_Amount, "1500.00")
        self.assertEqual(rec.County, "Lancaster")
        self.assertEqual(rec.State, "SC")
        self.assertEqual(rec.Booking_Date, "10/01/2026")
        self.assertEqual(rec.Booking_Time, "9:15 AM")
        self.assertEqual(rec.Sex, "F")
        self.assertIn("SIMPLE ASSAULT", rec.Charges)
        # Reference id in ChargeBond must not leak into Bond_Amount
        self.assertNotEqual(rec.Bond_Amount, "2026-00009999")

    def test_source_contract_validated_not_verified_public_yet(self):
        import ast
        from pathlib import Path

        self.assertTrue(LancasterScraper.SOURCE_CONTRACT_VALIDATED)
        tree = ast.parse((Path(__file__).resolve().parents[1] / "dashboard" / "extensions.py").read_text())
        states = None
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "SCRAPER_SOURCE_STATES":
                states = ast.literal_eval(node.value)
                break
        self.assertIsInstance(states, dict)
        # Default Health is unverified until write smoke — do not promote here.
        self.assertNotEqual(states.get("Lancaster (SC)"), "verified_public")
        self.assertNotEqual(states.get("Lancaster (SC)"), "fail_closed")


if __name__ == "__main__":
    unittest.main()
