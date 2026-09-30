"""Tennessee county scrapers test suite (2026-09-29).

Synthetic fixtures for Davidson, Knox, Sumner, and Shelby county scrapers.
Exercises source-key contracts, charges parsing, bond extraction, and
data normalization without live network access.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from bs4 import BeautifulSoup

from scrapers.counties_tn.davidson import DavidsonScraper
from scrapers.counties_tn.hamblen import HamblenScraper
from scrapers.counties_tn.hamilton import HamiltonScraper
from scrapers.counties_tn.knox import KnoxScraper
from scrapers.counties_tn.sevier import SevierScraper
from scrapers.counties_tn.shelby import ShelbyScraper
from scrapers.counties_tn.sumner import SumnerScraper
from scrapers.counties_tn.washington import WashingtonScraper


# ── Davidson County Fixtures ─────────────────────────────────────────────────

DAVIDSON_LISTING_HTML = """
<html><body>
<table class="table">
  <tr><th>View</th><th>Name</th><th>Control #</th><th>Facility</th><th>Admitted</th></tr>
  <tr>
    <td><a href="/Search/Details/1077001">View Details</a></td>
    <td>DOE, JOHN QUINCY</td>
    <td>987654</td>
    <td>DCSO Downtown Detention Center</td>
    <td>09/28/2026 14:30</td>
  </tr>
</table>
</body></html>
"""

DAVIDSON_DETAIL_HTML = """
<html><body>
<div id="processing-inmate-information">
  <ul>
    <li><label>Facility:</label> DCSO Downtown Detention Center</li>
    <li><label>Date of Birth:</label> 05/12/1988</li>
    <li><label>Admitted:</label> 09/28/2026 14:30</li>
  </ul>
</div>
<ul class="details-list">
  <li><label>Arrested Charge:</label> AGGRAVATED ASSAULT - DEADLY WEAPON</li>
  <li><label>Warrant:</label> GS123456</li>
  <li><label>Bond:</label> $15,000.00</li>
</ul>
<ul class="details-list">
  <li><label>Arrested Charge:</label> RESISTING ARREST</li>
  <li><label>Warrant:</label> GS123457</li>
  <li><label>Bond:</label> $2,500.00</li>
</ul>
</body></html>
"""


def test_davidson_scraper_contract():
    scraper = DavidsonScraper()
    assert scraper.SOURCE_CONTRACT_VALIDATED is True
    assert scraper.county == "Davidson"
    assert scraper.state == "TN"

    # Test listing parse
    records = scraper._parse_results_table(DAVIDSON_LISTING_HTML, source="recent")
    assert len(records) == 1
    rec = records[0]
    assert rec.Booking_Number == "1077001"
    assert rec.Full_Name == "DOE, JOHN QUINCY"
    assert rec.Facility == "DCSO Downtown Detention Center"

    # Test detail parse
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = DAVIDSON_DETAIL_HTML
    mock_session.get.return_value = mock_resp

    detail = scraper._fetch_detail(mock_session, "1077001")
    assert detail is not None
    assert detail["dob"] == "05/12/1988"
    assert detail["bond"] == "17500"
    assert "AGGRAVATED ASSAULT" in detail["charges"]
    assert "RESISTING ARREST" in detail["charges"]


# ── Knox County Fixtures ─────────────────────────────────────────────────────

KNOX_HTML = """
<html><body>
<table>
  <tr><th>PUBLIC, JANE MARIE</th><th>D.O.B. 04/15/1990</th></tr>
  <tr><td>IDN#: 1720300</td><td>Age: 36</td></tr>
</table>
<table>
  <tr><th>Seq</th><th>Booked Date</th><th>Charge</th><th>Bond</th></tr>
  <tr><td>1</td><td>09/29/2026</td><td>DRIVING UNDER THE INFLUENCE</td><td>Bond Amount: $1,500.00</td></tr>
  <tr><td>2</td><td>09/29/2026</td><td>IMPROPER LANE USAGE</td><td>Bond Amount: $0.00</td></tr>
</table>
<table>
  <tr><th>Court Date</th><th>Hearing</th></tr>
  <tr><td>10/15/2026</td><td>Arraignment</td></tr>
</table>
</body></html>
"""


def test_knox_scraper_contract():
    scraper = KnoxScraper()
    assert scraper.SOURCE_CONTRACT_VALIDATED is True
    assert scraper.county == "Knox"
    assert scraper.state == "TN"

    records = scraper._parse_knox_html(KNOX_HTML, source_url="https://sheriff.knoxcountytn.gov/index.php")
    assert len(records) == 1
    rec = records[0]
    assert rec.Booking_Number == "1720300"
    assert rec.Full_Name == "PUBLIC, JANE MARIE"
    assert rec.First_Name == "JANE MARIE"
    assert rec.Last_Name == "PUBLIC"
    assert rec.Bond_Amount == "1500"
    assert "DRIVING UNDER THE INFLUENCE" in rec.Charges
    assert rec.Court_Date == "10/15/2026"


# ── Sumner County Fixtures ───────────────────────────────────────────────────

SUMNER_RTJB_ITEM = {
    "title": "SMITH, ROBERT TYLER",
    "content": """
      <div>
        <p>Inmate ID: 263108</p>
        <p>Race: W</p>
        <p>Sex: M</p>
        <p>Age: 31</p>
        <p>Booking Date: 09/29/2026 01:22 AM</p>
        <p>Charges:</p>
        <p>Description: THEFT OF PROPERTY $2,500 - $10,000</p>
        <p>Bond: $7,500.00</p>
        <p>Description: EVADING ARREST</p>
        <p>Bond: $2,500.00</p>
      </div>
    """,
    "_id": "67890abcd",
}


def test_sumner_scraper_contract():
    scraper = SumnerScraper()
    assert scraper.SOURCE_CONTRACT_VALIDATED is True
    assert scraper.county == "Sumner"
    assert scraper.state == "TN"

    rec = scraper._parse_rtjb_item(SUMNER_RTJB_ITEM)
    assert rec is not None
    assert rec.Booking_Number == "263108"
    assert rec.Full_Name == "SMITH, ROBERT TYLER"
    assert rec.First_Name == "ROBERT"
    assert rec.Last_Name == "SMITH"
    assert rec.Bond_Amount == "10000"
    assert "THEFT OF PROPERTY" in rec.Charges
    assert "Bond:" not in rec.Charges


# ── Shelby County Fixtures ───────────────────────────────────────────────────

SHELBY_LISTING_HTML = """
<html><body>
<table class="grid">
  <tr><th>Name</th><th>Booking #</th><th>Permanent ID</th><th>DOB</th><th>Action</th></tr>
  <tr>
    <td>WILLIAMS, MARCUS DEON</td>
    <td>26115215</td>
    <td>1045920</td>
    <td>11/04/1994</td>
    <td><a href="javascript:submitInmate('492015')">View</a></td>
  </tr>
</table>
</body></html>
"""

SHELBY_DETAIL_HTML = """
<html><body>
<div>
  <h2>Inmate Information</h2>
  <p>Commitment Date: 09/28/2026</p>
  <p>Next Court Date: 10/12/2026 09:00 AM</p>
  <p>Grand Total: 25,000.00</p>
  <table>
    <tr><th>Seq</th><th>Date</th><th>Code</th><th>Description</th><th>Grade</th></tr>
    <tr><td>1</td><td>09/28/2026</td><td>21027</td><td>BURGLARY - MOTOR VEHICLE</td><td>FA</td></tr>
    <tr><td>2</td><td>09/28/2026</td><td>21001</td><td>THEFT OF PROPERTY $1,000 OR LESS</td><td>MB</td></tr>
  </table>
</div>
</body></html>
"""


def test_shelby_scraper_contract():
    scraper = ShelbyScraper()
    assert scraper.SOURCE_CONTRACT_VALIDATED is True
    assert scraper.county == "Shelby"
    assert scraper.state == "TN"

    # Test listing parse
    inmates = scraper._parse_listing_page(SHELBY_LISTING_HTML)
    assert len(inmates) == 1
    assert inmates[0]["sys_id"] == "492015"
    assert inmates[0]["booking_number"] == "26115215"
    assert inmates[0]["perm_id"] == "1045920"
    assert inmates[0]["dob"] == "11/04/1994"

    # Test detail parse
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = SHELBY_DETAIL_HTML
    mock_session.post.return_value = mock_resp

    detail = scraper._fetch_inmate_detail(mock_session, "492015")
    assert detail is not None
    assert detail["commitment_date"] == "09/28/2026"
    assert "10/12/2026" in detail["court_date"]
    assert detail["bond"] == "25000.00"
    assert "BURGLARY" in detail["charges"]


# ── Hamilton County Fixtures ─────────────────────────────────────────────────

HAMILTON_BOOKING_ITEM = {
    "R_ID": "11514DF8-D7A0-4C2A-A831-693E278B15AE",
    "FullName": "CONNER, CHRISTOPHER DEWAYNE",
    "LastName": "CONNER",
    "FirstName": "CHRISTOPHER",
    "MiddleName": "DEWAYNE",
    "SPN": "00318029",
    "BookingDate": "09/30/2026 04:31:00",
    "ArrestDate": "09/30/2026 03:00:00",
    "Age": "41",
    "Race": "White",
    "Sex": "M",
    "City": "CHATTANOOGA",
    "ArrestingAgency": "Chattanooga Police Department",
    "Charges": "CRIMINAL TRESPASS",
}


def test_hamilton_scraper_contract():
    scraper = HamiltonScraper()
    assert scraper.SOURCE_CONTRACT_VALIDATED is True
    assert scraper.county == "Hamilton"
    assert scraper.state == "TN"

    rec = scraper._booking_to_record(
        HAMILTON_BOOKING_ITEM,
        roster_bond="5000.00",
        detail_info={"dob": "01/15/1985", "facility": "Hamilton County Jail"},
    )
    assert rec is not None
    assert rec.Booking_Number == "11514DF8-D7A0-4C2A-A831-693E278B15AE"
    assert rec.Person_ID == "00318029"
    assert rec.Full_Name == "Conner, Christopher Dewayne"
    assert rec.First_Name == "Christopher"
    assert rec.Last_Name == "Conner"
    assert rec.Charges == "CRIMINAL TRESPASS"
    assert rec.Bond_Amount == "5000.00"
    assert rec.DOB == "01/15/1985"
    assert rec.Facility == "Hamilton County Jail"


# ── Sevier County Fixtures ───────────────────────────────────────────────────

SEVIER_HTML = (
    '<script>self.__next_f.push([1, "1:{\\"entries\\":['
    '{\\"inmateID\\":974489,\\"title\\":\\"Johnson, Micah Dean\\",'
    '\\"content\\":\\"<div><p>Booked Date: 09/29/2026 08:30 PM</p><p>Age: 28</p><p>Gender: M</p><p>Race: W</p></div>\\",'
    '\\"custody_status_cd\\":\\"IN\\"}'
    ']}\\n"])</script>'
)


def test_sevier_scraper_contract():
    scraper = SevierScraper()
    assert scraper.SOURCE_CONTRACT_VALIDATED is True
    assert scraper.county == "Sevier"
    assert scraper.state == "TN"

    entries = scraper._extract_flight_entries(SEVIER_HTML)
    assert len(entries) == 1
    assert entries[0]["inmateID"] == 974489

    rec = scraper._parse_entry(entries[0])
    assert rec is not None
    assert rec.Booking_Number == "974489"
    assert rec.Full_Name == "Johnson, Micah Dean"
    assert rec.First_Name == "Micah"
    assert rec.Last_Name == "Johnson"
    assert rec.Status == "In Custody"
    assert rec.Sex == "M"
    assert rec.Race == "W"


# ── Washington County Fixtures ───────────────────────────────────────────────

def test_washington_scraper_contract():
    scraper = WashingtonScraper()
    assert scraper.SOURCE_CONTRACT_VALIDATED is True
    assert scraper.county == "Washington"
    assert scraper.state == "TN"

    entry = {
        "booking_number": "202606801",
        "name": "RABY, BOBBY EARL JR",
        "sex": "Male",
        "race": "White",
        "age": "38",
        "charges": "55-50-504 - Driving While Suspended 1st offense; 39-14-103 - Theft of Property (Up to $1000)",
        "booking_date": "09/30/26 02:16",
    }
    rec = scraper._entry_to_record(entry)
    assert rec is not None
    assert rec.Booking_Number == "202606801"
    assert rec.Full_Name == "Raby, Bobby Earl Jr"
    assert rec.First_Name == "Bobby"
    assert rec.Middle_Name == "Earl Jr"
    assert rec.Last_Name == "Raby"
    assert rec.Booking_Date == "09/30/2026 02:16"
    assert "Driving While Suspended" in rec.Charges
    assert "Theft of Property" in rec.Charges
    assert rec.Status == "In Custody"


# ── Hamblen County Fixtures ──────────────────────────────────────────────────

HAMBLEN_CARD_HTML = """
<article class="inmate">
  <section style="width: 500px;">
    <h1>AILOR, SHASHA W</h1>
    <p><h2>Age:</h2><data>38</data></p>
    <p><h2>Race/Sex:</h2><data>W/F</data></p>
    <p><h2>Intake Date:</h2><data>09/16/2026 06:36 PM</data></p>
    <p><h2>City:</h2><data>NASHVILLE</data></p>
    <p><h2>Arrested By Department:</h2><data>HAMBLEN COUNTY SHERIFF'S OFFICE</data></p>
    <p><h2>Release Date:</h2><data></data></p>
  </section>
  <section>
    <table class="charges">
      <tr><th>Charge</th><th>Bond</th></tr>
      <tr><td>FAILURE TO APPEAR</td><td>25000</td></tr>
      <tr><td>DRIVING ON SUSPENDED</td><td>1500</td></tr>
    </table>
  </section>
</article>
"""


def test_hamblen_scraper_contract():
    scraper = HamblenScraper()
    assert scraper.SOURCE_CONTRACT_VALIDATED is True
    assert scraper.county == "Hamblen"
    assert scraper.state == "TN"

    soup = BeautifulSoup(HAMBLEN_CARD_HTML, "html.parser")
    card = soup.select_one("article.inmate")
    assert card is not None

    rec = scraper._card_to_record(card)
    assert rec is not None
    assert rec.Full_Name == "Ailor, Shasha W"
    assert rec.First_Name == "Shasha"
    assert rec.Last_Name == "Ailor"
    assert rec.Bond_Amount == "26500.00"
    assert "FAILURE TO APPEAR" in rec.Charges
    assert "DRIVING ON SUSPENDED" in rec.Charges
    assert rec.Booking_Number.startswith("HAMBLEN-")
    assert rec.Status == "In Custody"

