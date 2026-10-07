"""FL idle eight 2026-10-07: Hamilton/Madison/Gilchrist/Citrus/Okaloosa parsers + holds.

Fixtures are synthetic (placeholder names, fabricated-but-well-formed IDs) and
exercise only the parsing contract; no network access.
"""
from __future__ import annotations

import io

import pytest

from scrapers.counties import citrus, okaloosa
from scrapers.counties.alachua import AlachuaCountyScraper
from scrapers.counties.clay import ClayCountyScraper
from scrapers.counties.columbia import ColumbiaCountyScraper
from scrapers.counties.gilchrist import GilchristCountyScraper
from scrapers.counties.hamilton import HamiltonCountyScraper
from scrapers.counties.madison import MadisonCountyScraper
from scrapers.counties.okeechobee import OkeechobeeCountyScraper
from scrapers import fl_smartweb


def _smartweb_card(bookno: str, text_bookno: str) -> str:
    return f"""
<tr><td><img src='ViewImage.aspx?bookno={bookno}'></td>
<td>DOE, JOHN Q (W/ MALE )</td></tr>
<tr><td>Status: In Jail</td></tr>
<tr><td>Booking No: {text_bookno} / MNI No: XXSO00MNI000000</td></tr>
<tr><td>Booking Date: 10/05/2026 06:36 AM</td></tr>
<tr><td><table class="JailViewCharges"><tr><td>HOLDS</td></tr><tr><td>OTHER AGENCY</td></tr></table></td></tr>
<tr><td><table class="JailViewCharges">
<tr class="SearchHeader"><td>CHARGES</td></tr>
<tr><td></td><td>810.08.2a</td><td>C1</td><td>TRESPASSING</td><td>M</td><td>2</td><td>$500.00</td><td></td></tr>
</table></td></tr>
"""


def test_fl_smartweb_parses_matching_booking_no_charges_and_bond():
    html = "<table>" + _smartweb_card("HCSO26JBN000001", "HCSO26JBN000001") + "</table>"
    recs = fl_smartweb._parse_html(
        html, set(), county="Hamilton", facility="Hamilton County Jail", detail_url="https://example.test/jail.aspx"
    )
    assert len(recs) == 1
    r = recs[0]
    assert r.Booking_Number == "HCSO26JBN000001"
    assert (r.Booking_Date, r.Booking_Time) == ("10/05/2026", "06:36 AM")
    assert r.Charges == "810.08.2a - TRESPASSING"
    assert r.Bond_Amount == "500"
    assert r.State == "FL"


def test_fl_smartweb_drops_when_image_and_text_booking_disagree():
    html = "<table>" + _smartweb_card("HCSO26JBN000001", "HCSO26JBN000002") + "</table>"
    assert (
        fl_smartweb._parse_html(
            html, set(), county="Hamilton", facility="X", detail_url="https://example.test/"
        )
        == []
    )


@pytest.mark.parametrize(
    "cls,url_fragment",
    [
        (HamiltonCountyScraper, "inmate.hamiltonsheriff.com"),
        (MadisonCountyScraper, "smartweb.mcso-fl.org"),
        (GilchristCountyScraper, "inmate.gcso.us"),
    ],
)
def test_smartweb_fl_counties_are_source_validated_with_live_hosts(cls, url_fragment):
    assert cls.SOURCE_CONTRACT_VALIDATED is True
    from scrapers.counties import hamilton, madison, gilchrist

    mod = {HamiltonCountyScraper: hamilton, MadisonCountyScraper: madison, GilchristCountyScraper: gilchrist}[cls]
    assert url_fragment in mod.BASE_URL


def test_citrus_table_row_requires_ar_number():
    s = citrus.CitrusCountyScraper()
    col = {"photo": 0, "name": 1, "ar #": 2, "date": 3, "arrest type": 4, "offense": 5, "dob": 6, "bond": 7}
    good = ["", "DOE, JANE Q", "AB26-000123", "10/01/2026", "Felony Arrest", "810.08 - TRESPASS", "01/01/1990", "1,000.00"]
    bad = ["", "DOE, JANE Q", "", "10/01/2026", "Felony Arrest", "TRESPASS", "01/01/1990", "0"]
    rec = s._row_to_record(good, col, set(), "https://example.test/x.pdf")
    assert rec is not None
    assert rec.Booking_Number == "AB26-000123"
    assert rec.Bond_Amount == "1000.0" or rec.Bond_Amount == "1000"
    assert s._row_to_record(bad, col, set(), "https://example.test/x.pdf") is None


def test_citrus_extracts_pdf_url_from_iframe():
    html = (
        '<iframe src="public%20info/recent%20arrests/Citrus%20County%20Sheriff\'s%20'
        'Office%20Arrests%20and%20Charges%2020260927%20to%2020261006.pdf?t=1"></iframe>'
    )
    url = citrus.CitrusCountyScraper._extract_pdf_url(html)
    assert url.startswith("https://www.sheriffcitrus.org/")
    assert url.endswith(".pdf?t=1") or ".pdf?" in url
    assert "Arrests%20and%20Charges" in url or "Arrests and Charges" in url


def test_okaloosa_parser_requires_source_booking_number():
    html = """
    <table>
      <tr><th>NameTypeID</th><th>NameType</th><th>NameTitle</th><th>LastName</th>
          <th>FirstName</th><th>MiddleName</th><th>NameSuffix</th><th>RTC</th><th></th>
          <th>Eye</th><th>Hair</th><th>Skin</th><th></th><th></th><th></th><th></th>
          <th></th><th></th><th></th><th>Booking#</th><th>SPN#</th><th>Name</th>
          <th>DOB</th><th>Age</th><th>Sex</th><th>Race</th><th>Height</th><th>Weight</th>
          <th>EligReleaseDate</th></tr>
      <tr><td>1</td><td>0</td><td></td><td>DOE</td><td>JANE</td><td>Q</td><td></td><td></td><td></td>
          <td></td><td></td><td></td><td></td><td></td><td></td><td></td>
          <td></td><td></td><td></td><td>2026005082</td><td>123</td><td>DOE,JANE</td>
          <td>01/1990</td><td>36</td><td>F</td><td>W</td><td>505</td><td>120</td><td></td></tr>
      <tr><td>2</td><td>0</td><td></td><td>ROE</td><td>JOHN</td><td></td><td></td><td></td><td></td>
          <td></td><td></td><td></td><td></td><td></td><td></td><td></td>
          <td></td><td></td><td></td><td></td><td>456</td><td>ROE,JOHN</td>
          <td>02/1991</td><td>35</td><td>M</td><td>W</td><td>510</td><td>180</td><td></td></tr>
    </table>
    """
    from bs4 import BeautifulSoup

    recs = okaloosa.OkaloosaCountyScraper()._parse_soup(BeautifulSoup(html, "html.parser"))
    assert len(recs) == 1  # row without Booking# must be dropped
    assert recs[0].Booking_Number == "2026005082"
    assert recs[0].Last_Name.upper() == "DOE"
    assert recs[0].First_Name.upper() == "JANE"


@pytest.mark.parametrize(
    "cls",
    [ClayCountyScraper, ColumbiaCountyScraper, OkeechobeeCountyScraper, AlachuaCountyScraper],
)
def test_incomplete_idle_counties_are_fail_closed(cls):
    assert cls.SOURCE_CONTRACT_VALIDATED is False
    assert cls().scrape() == []
