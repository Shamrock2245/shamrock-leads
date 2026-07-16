"""Regression tests for Putnam SmartWeb card-bounded parsing.

Guards against:
  - ENLARGE PHOTO UI chrome leaking into Address/Charges
  - Greedy Status capture
  - Missing bond type / amount when charge rows exist
"""

from scrapers.counties.putnam import PutnamCountyScraper


SAMPLE_HTML = """
<table>
  <tr>
    <td>
      <table>
        <tr>
          <td class="SearchHeader">DOE, JOHN ADAM (W/ MALE / DOB: 01/15/1990 )</td>
        </tr>
        <tr>
          <td>
            Status: In Jail
            Booking No: PU26JBN000111
            Booking Date: 07/10/2026 03:52 AM
            Address Given: 123 MAIN ST PALATKA FL
            <a href="#">ENLARGE PHOTO</a>
          </td>
        </tr>
      </table>
    </td>
  </tr>
  <tr>
    <td>
      <table class="JailViewCharges">
        <tr class="SearchHeader">
          <td></td><td>Statute</td><td>Case</td><td>Charge</td><td>Deg</td><td>Lvl</td><td>Bond</td>
        </tr>
        <tr>
          <td>[+]</td>
          <td>893.13</td>
          <td>2026-CF-001</td>
          <td>POSS CONT SUBST</td>
          <td>3</td>
          <td>F</td>
          <td>$2,500.00</td>
        </tr>
        <tr>
          <td></td>
          <td>ENLARGE PHOTO</td>
          <td></td>
          <td></td>
          <td></td>
          <td></td>
          <td></td>
        </tr>
      </table>
    </td>
  </tr>
  <tr>
    <td>
      <table>
        <tr>
          <td class="SearchHeader">SMITH, JANE (B/ FEMALE / DOB: 05/20/1985 )</td>
        </tr>
        <tr>
          <td>
            Status: Released
            Booking No: PU26JBN000222
            Booking Date: 07/11/2026 01:00 PM
            Address Given: 9 OAK AVE ENLARGE PHOTO
          </td>
        </tr>
      </table>
    </td>
  </tr>
  <tr>
    <td>
      <table class="JailViewCharges">
        <tr>
          <td></td>
          <td>316.193</td>
          <td></td>
          <td>DUI</td>
          <td>1</td>
          <td>M</td>
          <td>$1,000.00 SURETY</td>
        </tr>
      </table>
    </td>
  </tr>
</table>
"""


def _parse():
    s = PutnamCountyScraper.__new__(PutnamCountyScraper)
    return s._parse_html(SAMPLE_HTML, set())


def test_parses_two_inmates():
    recs = _parse()
    assert len(recs) == 2
    assert recs[0].Booking_Number == "PU26JBN000111"
    assert recs[1].Booking_Number == "PU26JBN000222"


def test_no_enlarge_photo_in_fields():
    for r in _parse():
        blob = " ".join([
            r.Address or "",
            r.Charges or "",
            r.Full_Name or "",
            r.Status or "",
            r.Bond_Type or "",
        ]).upper()
        assert "ENLARGE" not in blob
        assert "PHOTO" not in blob or "PHOTO" not in (r.Address or "").upper()


def test_status_and_bonds():
    recs = _parse()
    assert recs[0].Status == "In Custody"
    assert recs[1].Status == "Released"
    assert float(recs[0].Bond_Amount) == 2500.0
    assert float(recs[1].Bond_Amount) == 1000.0
    assert recs[0].Bond_Type in ("SURETY", "CASH/SURETY")
    assert "POSS" in recs[0].Charges
    assert "DUI" in recs[1].Charges


def test_ui_noise_helpers():
    assert "ENLARGE" not in PutnamCountyScraper._strip_ui_noise("123 MAIN ENLARGE PHOTO ST").upper()
    assert PutnamCountyScraper._is_ui_label("ENLARGE PHOTO")
    amt, btype = PutnamCountyScraper._parse_bond_cell("$1,500.00")
    assert amt == 1500.0
    assert btype == "SURETY"
    amt, btype = PutnamCountyScraper._parse_bond_cell("NO BOND")
    assert amt == 0.0
    assert btype == "NO BOND"


def test_shared_smartweb_module():
    from scrapers.smartweb_card_parser import parse_smartweb_cards, strip_ui_noise, parse_bond_cell

    assert "ENLARGE" not in strip_ui_noise("ENLARGE PHOTO 9 OAK").upper()
    amt, btype = parse_bond_cell("$2,500.00 SURETY")
    assert amt == 2500.0
    recs = parse_smartweb_cards(
        SAMPLE_HTML,
        county="Putnam",
        facility="Putnam County Jail",
        detail_url="https://example.test",
        seen=set(),
    )
    assert len(recs) == 2
    assert all(r.State == "FL" for r in recs)
