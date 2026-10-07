"""FL SmartWEB five 2026-10-07: Bradford/Dixie/Taylor/Escambia/Santa Rosa.

Fixtures are synthetic (placeholder names, fabricated-but-well-formed IDs) and
exercise only the parsing contract; no network access.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scrapers import fl_smartweb
from scrapers.counties.bradford import BradfordCountyScraper
from scrapers.counties.dixie import DixieCountyScraper
from scrapers.counties.escambia import EscambiaCountyScraper
from scrapers.counties.santa_rosa import SantaRosaCountyScraper
from scrapers.counties.taylor import TaylorCountyScraper


def _card_with_dob(bookno: str) -> str:
    return f"""
<tr style="InmateRecordRow">
  <td><img src='ViewImage.aspx?bookno={bookno}'><BR>
      <a href="ViewImageFull.aspx?bookno={bookno}" target='blank'>Enlarge Photo</a></td>
  <td><table><thead><tr>
    <td class="SearchHeader">DOE, JANE Q &nbsp; (W/ FEMALE / DOB: 01/15/1990 )</td>
  </tr></thead>
  <tbody>
    <tr><td>Status: In Jail</td></tr>
    <tr><td>Booking No: {bookno} / MniNo: XXSO00MNI000001</td></tr>
    <tr><td>Booking Date: 10/07/2026 11:12 AM</td></tr>
    <tr><td>Bond Amount: $0.00</td></tr>
    <tr><td>Address Given: 123 MAIN ST ANYTOWN, FL 32000</td></tr>
  </tbody></table></td>
</tr>
<tr><td><table class="JailViewCharges">
<tr class="SearchHeader"><td>CHARGES</td></tr>
<tr><td></td><td>810.08.2a</td><td>C1</td><td>TRESPASSING</td><td>M</td><td>2</td><td>$250.00</td><td></td></tr>
</table></td></tr>
"""


def _card_no_dob(bookno: str) -> str:
    return f"""
<tr class="InmateRecordRow">
  <td><img src='ViewImage.aspx?bookno={bookno}'><BR>
      <a href="ViewImageFull.aspx?bookno={bookno}">Enlarge Photo</a></td>
  <td><table><thead><tr>
    <td class="SearchHeader">ROE, JOHN A &nbsp; (B/ MALE )</td>
  </tr></thead>
  <tbody>
    <tr><td>Status: In Jail</td></tr>
    <tr><td>Booking No: {bookno}</td></tr>
    <tr><td>Booking Date: 10/06/2026 08:21 AM</td></tr>
  </tbody></table></td>
</tr>
"""


def test_fl_smartweb_parses_searchheader_with_dob_inside_parens():
    html = "<table>" + _card_with_dob("BCSO26JBN000001") + "</table>"
    recs = fl_smartweb._parse_html(
        html, set(), county="Bradford", facility="Bradford County Jail", detail_url="http://example.test/jail.aspx"
    )
    assert len(recs) == 1
    r = recs[0]
    assert r.Booking_Number == "BCSO26JBN000001"
    assert r.Full_Name == "DOE, JANE Q"
    assert "ENLARGE" not in r.Full_Name and "PHOTO" not in r.Full_Name
    assert r.Sex == "F"
    assert r.DOB == "01/15/1990"
    assert r.Charges == "810.08.2a - TRESPASSING"
    assert r.Bond_Amount == "250"
    assert (r.Booking_Date, r.Booking_Time) == ("10/07/2026", "11:12 AM")


def test_fl_smartweb_parses_searchheader_without_dob():
    html = "<table>" + _card_no_dob("ECC26JBN012426") + "</table>"
    recs = fl_smartweb._parse_html(
        html, set(), county="Escambia", facility="Escambia County Jail", detail_url="https://example.test/jail.aspx"
    )
    assert len(recs) == 1
    r = recs[0]
    assert r.Booking_Number == "ECC26JBN012426"
    assert r.Full_Name == "ROE, JOHN A"
    assert r.Sex == "M"
    assert r.Race == "B"


def test_fl_smartweb_status_does_not_swallow_the_next_label():
    released = _card_no_dob("ECC26JBN000010").replace("Status: In Jail", "Status: Released")
    out = _card_no_dob("ECC26JBN000011").replace("Status: In Jail", "Status: Out of Jail")
    in_jail = _card_no_dob("ECC26JBN000012")
    html = "<table>" + released + out + in_jail + "</table>"
    recs = fl_smartweb._parse_html(
        html, set(), county="Escambia", facility="X", detail_url="https://example.test/"
    )
    by_book = {r.Booking_Number: r.Status for r in recs}
    assert by_book == {
        "ECC26JBN000010": "Released",
        "ECC26JBN000011": "Released",
        "ECC26JBN000012": "In Custody",
    }


def test_fl_smartweb_bond_cell_keeps_dollars_when_type_word_follows():
    html = _card_no_dob("TCSO26JBN000007")
    html = html.replace(
        "</tbody></table></td>\n</tr>",
        "</tbody></table></td>\n</tr>\n"
        "<tr><td><table class=\"JailViewCharges\">"
        "<tr class=\"SearchHeader\"><td>CHARGES</td></tr>"
        "<tr><td></td><td>843.02</td><td></td><td>RESIST OFFICER</td>"
        "<td>M</td><td>1</td><td>$2,500.00 SURETY</td></tr>"
        "</table></td></tr>",
        1,
    )
    recs = fl_smartweb._parse_html(
        "<table>" + html + "</table>",
        set(),
        county="Taylor",
        facility="X",
        detail_url="http://example.test/",
    )
    assert len(recs) == 1
    assert recs[0].Bond_Amount == "2500"
    assert recs[0].Charges == "843.02 - RESIST OFFICER"


def test_fl_smartweb_rejected_photo_does_not_hide_a_later_match():
    bad = _card_with_dob("BCSO26JBN000001").replace(
        "Booking No: BCSO26JBN000001", "Booking No: BCSO26JBN999999", 1
    )
    html = "<table>" + bad + _card_with_dob("BCSO26JBN000001") + "</table>"
    recs = fl_smartweb._parse_html(
        html, set(), county="Bradford", facility="X", detail_url="http://example.test/"
    )
    assert [r.Booking_Number for r in recs] == ["BCSO26JBN000001"]


def test_fl_smartweb_drops_mismatched_booking_no():
    html = _card_with_dob("BCSO26JBN000001").replace(
        "Booking No: BCSO26JBN000001", "Booking No: BCSO26JBN000002", 1
    )
    html = "<table>" + html + "</table>"
    assert (
        fl_smartweb._parse_html(
            html, set(), county="Bradford", facility="X", detail_url="http://example.test/"
        )
        == []
    )


@pytest.mark.parametrize(
    "cls,url_fragment",
    [
        (BradfordCountyScraper, "smartweb.bradfordsheriff.org"),
        (DixieCountyScraper, "smartcop.dixiecountysheriff.com"),
        (TaylorCountyScraper, "smartcop.taylorsheriff.org:8989"),
        (EscambiaCountyScraper, "inmatelookup.myescambia.com"),
        (SantaRosaCountyScraper, "jailview.srso.net"),
    ],
)
def test_smartweb_five_are_source_validated_with_live_hosts(cls, url_fragment):
    assert cls.SOURCE_CONTRACT_VALIDATED is True
    from scrapers.counties import bradford, dixie, taylor, escambia, santa_rosa

    mod = {
        BradfordCountyScraper: bradford,
        DixieCountyScraper: dixie,
        TaylorCountyScraper: taylor,
        EscambiaCountyScraper: escambia,
        SantaRosaCountyScraper: santa_rosa,
    }[cls]
    assert url_fragment in mod.BASE_URL
    src = Path(mod.__file__).read_text()
    assert "from curl_cffi" not in src
    assert "import curl_cffi" not in src
