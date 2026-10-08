"""Collier and Glades: only a bond the source publishes counts (2026-10-08).

Fixtures are synthetic (placeholder people and ids) and follow the live page
shapes checked from the box on 2026-10-08:

* Collier's daily report publishes no bond amount. Each person has
  ``lblBondSummary`` ("No information available." or "<date>  BONDED") and a
  ``gvCharge`` grid whose offense text can carry dollar figures
  ("GRAND THEFT PROPERTY VALUE $750-$5K"). Span ids share the person's
  ``gvReport_ctlNN_ReportUC_`` prefix.
* Glades SmartWEB cards print "Bond Amount:" (often "NO BOND" or "$0.00") and
  a ``JailViewCharges`` grid whose BOND column holds "NO BOND" or "$2500.00".

An unknown or unpublished bond stays ``""``: never ``"0"``, never a figure
from charge text, never a neighbour's bond.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from scrapers.counties.collier import CollierCountyScraper
from scrapers.counties.glades import GladesCountyScraper


# ── Collier ─────────────────────────────────────────────────────────────────
def _collier_person(ctl: str, booking: str, offense: str, summary: str, bond_amount: str | None = None) -> str:
    p = f"gvReport_{ctl}_ReportUC_"
    amount = f'<td><span id="{p}lblBondAmount">{bond_amount}</span></td>' if bond_amount else ""
    return f"""
<tr><td>
 <table id="{p}Table1"><tr><td>Name</td><td>Date of Birth</td><td>Residence</td></tr>
  <tr><td>DOE, JANE</td><td>01/01/1990</td><td>1 MAIN ST NAPLES FL 34102</td></tr></table>
 <table><tr><td>A#</td><td><span id="{p}lblANbr">A0000001</span></td>
  <td>PIN</td><td><span id="{p}lblPIN">P0000001</span></td><td>Race</td><td>W</td><td>Sex</td><td>F</td></tr></table>
 <table><tr><td><span id="{p}lblInCustody">IN CUSTODY</span></td></tr></table>
 <table><tr><td>Booking Date</td><td>10/07/2026</td><td>Booking Number</td><td>{booking}</td>
  <td>Agency</td><td>CCSO</td><td>Age at Arrest</td><td>36</td></tr></table>
 <table id="{p}gvCharge"><tr><td>Charged</td><td>Count</td><td>Offense</td><td>Hold For</td><td>Case Number</td><td>Court Date</td></tr>
  <tr><td>Charged</td><td>1</td><td>{offense}</td><td></td><td>26-CF-000001</td><td>11/02/2026</td></tr></table>
 <table><tr><td>Bond</td><td><span id="{p}lblBondSummary">{summary}</span></td>{amount}</tr></table>
</td></tr>"""


def _collier(*people: str):
    html = f'<div id="UpdatePanel1"><table id="gvReport">{"".join(people)}</table></div>'
    soup = BeautifulSoup(html, "html.parser")
    recs = CollierCountyScraper()._parse_arrest_tables(soup.find_all("table"), soup)
    return {r.Booking_Number: r for r in recs}


def test_collier_dollar_figures_in_offense_text_are_not_a_bond():
    recs = _collier(
        _collier_person("ctl02", "202600000101", "GRAND THEFT PROPERTY VALUE $750-$5K", "No information available."),
        _collier_person("ctl03", "202600000102", "DUI", "No information available."),
    )
    assert recs["202600000101"].Bond_Amount == ""
    assert recs["202600000102"].Bond_Amount == ""


def test_collier_unknown_bond_is_empty_not_zero():
    recs = _collier(_collier_person("ctl02", "202600000201", "DUI", "10/07/2026  BONDED"))
    rec = recs["202600000201"]
    assert rec.Bond_Amount == ""
    assert rec.Bond_Paid == "BONDED"
    doc = rec.to_mongo_doc()
    assert doc["bond_amount_raw"] == "" and doc["bond_amount"] == 0.0


def test_collier_bond_never_comes_from_the_next_person():
    recs = _collier(
        _collier_person("ctl02", "202600000301", "DUI", "No information available."),
        _collier_person("ctl03", "202600000302", "BATTERY", "10/07/2026  BONDED", bond_amount="$2,500.00"),
    )
    assert recs["202600000301"].Bond_Amount == ""
    assert recs["202600000301"].Bond_Paid == "NO"
    assert recs["202600000302"].Bond_Amount == "2500.0"
    assert recs["202600000302"].Bond_Paid == "BONDED"


def test_collier_without_an_id_prefix_reads_no_bond():
    # Same report without the gvReport_ctlNN_ReportUC_ ids: there is no safe
    # way to tell this person's bond span from the next person's.
    html = _collier_person("ctl02", "202600000501", "DUI", "No information available.") + _collier_person(
        "ctl03", "202600000502", "BATTERY", "10/07/2026  BONDED", bond_amount="$2,500.00")
    html = html.replace(' id="gvReport_ctl02_ReportUC_Table1"', "").replace(' id="gvReport_ctl03_ReportUC_Table1"', "")
    soup = BeautifulSoup(f"<table>{html}</table>", "html.parser")
    recs = {r.Booking_Number: r for r in CollierCountyScraper()._parse_arrest_tables(soup.find_all("table"), soup)}
    assert set(recs) == {"202600000501", "202600000502"}
    assert recs["202600000501"].Bond_Amount == ""
    assert recs["202600000502"].Bond_Amount == ""


def test_collier_published_bond_span_still_counts():
    recs = _collier(_collier_person("ctl02", "202600000401", "GRAND THEFT $750-$5K",
                                    "No information available.", bond_amount="$5,000.00"))
    assert recs["202600000401"].Bond_Amount == "5000.0"


# ── Glades ──────────────────────────────────────────────────────────────────
GLADES_HEADER = ("<tr class='SearchHeader'><td></td><td>STATUTE</td><td>COURT CASE NUMBER</td><td>CHARGE</td>"
                 "<td>DEGREE</td><td>LEVEL</td><td>BOND</td><td>BOND TYPE</td><td>FEES</td><td>Arrest Number</td></tr>")


def _glades_card(bookno: str, card_bond: str, charge_bonds: list[str] | None) -> str:
    card = f"""
<tr><td><img src="ViewImage.aspx?bookno={bookno}"/><br/><a href="ViewImageFull.aspx?bookno={bookno}">Enlarge Photo</a></td>
<td><table><thead><tr><td class="SearchHeader">DOE, JANE Q (W/ FEMALE / DOB: 01/01/1990 )</td></tr></thead><tbody>
 <tr><td>Status:</td><td>In Jail</td><td>Visitation Status:</td><td>Allowed</td></tr>
 <tr><td>Booking No:</td><td>{bookno}</td><td>MniNo:</td><td>GCSO03MNI000001</td></tr>
 <tr><td>Booking Date:</td><td>10/06/2026 08:21 AM</td></tr>
 <tr><td>Bond Amount:</td><td>{card_bond}</td><td>Cash Only:</td><td>$0.00</td></tr>
</tbody></table></td></tr>
<tr class="InmateRecordSeperater"></tr>"""
    if charge_bonds is None:
        return card
    rows = "".join(
        f"<tr><td>[+]</td><td>843.02</td><td>26MM00000{i}</td><td>RESIST OFFICER</td><td>M</td><td>1</td>"
        f"<td>{b}</td><td>{'NO BOND' if b == 'NO BOND' else 'SURETY'}</td><td>$0.00</td><td>A{i}</td><td>0</td></tr>"
        for i, b in enumerate(charge_bonds)
    )
    return card + f"""
<tr><td colspan="2"><table class="JailViewCharges"><tr><td>CHARGES</td></tr>{GLADES_HEADER}{rows}</table></td></tr>"""


def _glades(*cards: str):
    html = f"<table>{''.join(cards)}</table>"
    recs = GladesCountyScraper()._parse_html(html, set())
    # "" (unknown) stays "", a figure compares by value ("7500" == "7500.00").
    return {r.Booking_Number: (float(r.Bond_Amount) if r.Bond_Amount else "") for r in recs}


def test_glades_bond_is_formatted_like_the_shared_smartweb_helper():
    raw = GladesCountyScraper()._parse_html(_glades_card("GCSO26JBN000401", "$0.00", ["$2500.00", "$0.50"]), set())
    assert [r.Bond_Amount for r in raw] == ["2500.50"]
    raw = GladesCountyScraper()._parse_html(_glades_card("GCSO26JBN000402", "$0.00", ["$2500.00"]), set())
    assert [r.Bond_Amount for r in raw] == ["2500"]


def test_glades_sums_only_this_cards_published_charge_bonds():
    bonds = _glades(
        _glades_card("GCSO26JBN000101", "NO BOND", ["NO BOND", "NO BOND"]),
        _glades_card("GCSO26JBN000102", "NO BOND", ["$2500.00", "$1,000.00", "NO BOND"]),
        _glades_card("GCSO26JBN000103", "NO BOND", None),
        _glades_card("GCSO26JBN000104", "$0.00", ["$0.00"]),
        _glades_card("GCSO26JBN000105", "$0.00", ["$2500.00", "$1,000.00", "$0.00"]),
    )
    assert bonds == {
        "GCSO26JBN000101": "",      # every charge NO BOND: no published bond
        "GCSO26JBN000102": "",      # a NO BOND hold makes the total unknown, not 3500
        "GCSO26JBN000103": "",      # no charge grid, card NO BOND: unknown, not "0"
        "GCSO26JBN000104": 0.0,     # a printed charge $0.00 is a real 0
        "GCSO26JBN000105": 3500.0,  # card prints $0.00, charges publish 2500 + 1000 + 0
    }


def test_glades_printed_zero_is_the_string_0_and_hold_is_empty():
    raw = GladesCountyScraper()._parse_html(
        _glades_card("GCSO26JBN000501", "$0.00", ["$0.00", "$0.00"])
        + _glades_card("GCSO26JBN000502", "NO BOND", ["$0.00", "NO BOND"]), set())
    assert {r.Booking_Number: r.Bond_Amount for r in raw} == {
        "GCSO26JBN000501": "0", "GCSO26JBN000502": ""}


def test_glades_card_level_zero_without_charges_is_not_a_bond():
    # Live 2026-10-08: the card prints "Bond Amount: $0.00" even when its own
    # charges publish $15,000-$245,000, so the card-level $0.00 is not a total.
    assert _glades(_glades_card("GCSO26JBN000601", "$0.00", None)) == {"GCSO26JBN000601": ""}


def test_glades_unreadable_bond_cell_makes_the_total_unknown():
    assert _glades(_glades_card("GCSO26JBN000701", "$0.00", ["$2500.00", ""])) == {"GCSO26JBN000701": ""}


def test_glades_card_level_bond_counts_when_it_is_the_only_figure():
    bonds = _glades(_glades_card("GCSO26JBN000201", "$7,500.00", None))
    assert bonds == {"GCSO26JBN000201": 7500.0}


def test_glades_no_bond_card_never_takes_the_next_cards_bond():
    bonds = _glades(
        _glades_card("GCSO26JBN000301", "NO BOND", None),
        _glades_card("GCSO26JBN000302", "$15,000.00", ["$15000.00"]),
    )
    assert bonds == {"GCSO26JBN000301": "", "GCSO26JBN000302": 15000.0}
