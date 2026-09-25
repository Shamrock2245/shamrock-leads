"""FL gap queue 2026-09-25: Bay / Suwannee parsers + Lake / Leon / Gadsden holds.

Fixtures are synthetic (placeholder names, fabricated-but-well-formed IDs) and
exercise only the parsing contract; no network access.
"""
from __future__ import annotations

from unittest import mock

import pytest

from scrapers.counties import bay, suwannee
from scrapers.counties.gadsden import GadsdenCountyScraper
from scrapers.counties.lake import LakeCountyScraper
from scrapers.counties.leon import LeonCountyScraper


# ── Bay (uniGUI JSON store) ──────────────────────────────────────────────────

BAY_STORE = (
    '{"metaData":{"totalProperty":"results","root":"rows","fields":["0","_0","1","_1","2","_2","3","_3","_x","_r"]},'
    '"success":true, "results":2, "rows":['
    '{"id":0,"0":"\\x3Cimg src=\\"/is/x.jpg\\"\\x3E","_0":"",'
    '"1":"Booking #: 2026-000123\\x3Cbr\\x3EDate In: 9/4/2026 7:08:00 AM\\x3Cbr\\x3E","_1":"",'
    '"2":"DOE, JOHN, QUINCY\\x3Cbr\\x3ECommissary #: 1\\x3Cbr\\x3ERace: W\\x3Cbr\\x3ESex: M","_2":"",'
    '"3":"Charge 1: PETIT THEFT\\x3Cbr\\x3EBond: $1,000\\x3Cbr\\x3ECharge 2: RESISTING\\x3Cbr\\x3EBond: $500\\x3Cbr\\x3E","_3":"","_r":0},'
    '{"id":1,"0":"","_0":"","1":"Date In: 9/5/2026 1:00:00 PM\\x3Cbr\\x3E","_1":"",'
    '"2":"ROE, JANE\\x3Cbr\\x3ERace: B\\x3Cbr\\x3ESex: F","_2":"","3":"Charge 1: X\\x3Cbr\\x3E","_3":"","_r":1}'
    ']}'
)


def test_bay_store_decodes_js_hex_escapes_and_parses_source_booking():
    rows = bay._decode_store(BAY_STORE)["rows"]
    parsed = bay.parse_store_row(rows[0])
    assert parsed["booking_number"] == "2026-000123"
    assert parsed["booking_date"] == "9/4/2026"
    assert parsed["booking_time"] == "7:08:00 AM"
    assert (parsed["last"], parsed["first"], parsed["middle"]) == ("DOE", "JOHN", "QUINCY")
    assert (parsed["race"], parsed["sex"]) == ("W", "M")
    assert parsed["charges"] == "PETIT THEFT | RESISTING"
    assert parsed["bond"] == "1500"


def test_bay_row_without_source_booking_number_is_dropped_not_synthesized():
    rows = bay._decode_store(BAY_STORE)["rows"]
    assert bay.parse_store_row(rows[1]) is None


def test_bay_fp_field_matches_unigui_encoding():
    assert bay._fp_field("O5C", "A") == "&O5C=%020%02%02A"


def test_bay_landing_contract_drift_raises():
    sess = bay.BayUniGuiSession(http=mock.Mock())
    sess.http.get.return_value = mock.Mock(text="<html>_S_ID=abc123</html>", raise_for_status=lambda: None)
    with pytest.raises(bay.BaySourceContractError):
        sess.open()


def test_bay_scraper_is_source_validated():
    assert bay.BayCountyScraper.SOURCE_CONTRACT_VALIDATED is True


# ── Suwannee (SmartCOP JAIL View) ─────────────────────────────────────────────

def _suw_card(bookno: str, text_bookno: str) -> str:
    return f"""
<tr><td><img src='ViewImage.aspx?bookno={bookno}'></td>
<td>DOE, JOHN Q (W/ MALE )</td></tr>
<tr><td>Status: In Jail</td></tr>
<tr><td>Booking No: {text_bookno} / MNI No: SCSO00MNI000000</td></tr>
<tr><td>Booking Date: 09/25/2026 06:36 AM</td></tr>
<tr><td><table class="JailViewCharges"><tr><td>HOLDS</td></tr><tr><td>OTHER AGENCY</td></tr></table></td></tr>
<tr><td><table class="JailViewCharges">
<tr class="SearchHeader"><td>CHARGES</td></tr>
<tr><td></td><td>810.08.2a</td><td>C1</td><td>TRESPASSING</td><td>M</td><td>2</td><td>$500.00</td><td></td></tr>
</table></td></tr>
"""


def test_suwannee_parses_booking_no_time_and_charges_after_holds_table():
    s = suwannee.SuwanneeCountyScraper()
    html = "<table>" + _suw_card("SCSO26JBN000001", "SCSO26JBN000001") + "</table>"
    recs = s._parse_html(html, set())
    assert len(recs) == 1
    r = recs[0]
    assert r.Booking_Number == "SCSO26JBN000001"
    assert (r.Booking_Date, r.Booking_Time) == ("09/25/2026", "06:36 AM")
    assert r.Charges == "810.08.2a - TRESPASSING"
    assert r.Bond_Amount == "500"


def test_suwannee_drops_card_when_image_and_text_booking_disagree():
    s = suwannee.SuwanneeCountyScraper()
    html = "<table>" + _suw_card("SCSO26JBN000001", "SCSO26JBN000002") + "</table>"
    assert s._parse_html(html, set()) == []


def test_suwannee_ajax_body_is_wrapped_search_vals_with_booking_window():
    vals = suwannee.SuwanneeCountyScraper()._search_vals("08/26/2026", "09/25/2026", 10)
    assert vals["BeginBookDate"] == "08/26/2026" and vals["EndBookDate"] == "09/25/2026"
    assert vals["LastName"] == "" and vals["TypeJailSearch"] == 0 and vals["RecordsLoaded"] == 10


# ── Holds ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cls", [LakeCountyScraper, LeonCountyScraper, GadsdenCountyScraper])
def test_fl_holds_fail_closed_without_network(cls):
    scraper = cls()
    assert scraper.SOURCE_CONTRACT_VALIDATED is False
    assert scraper.SOURCE_CONTRACT_REASON
    with mock.patch("requests.Session.request", side_effect=AssertionError("network")):
        assert scraper.scrape() == []


def test_lake_has_no_captcha_solver_path():
    import inspect
    from scrapers.counties import lake
    src = inspect.getsource(lake).lower()
    assert "solvecaptcha" not in src and "sitekey" not in src


def test_fl_holds_are_fail_closed_in_registry():
    from dashboard.extensions import SCRAPER_SOURCE_STATES
    for label in ("Lake (FL)", "Leon (FL)", "Gadsden (FL)"):
        assert SCRAPER_SOURCE_STATES.get(label) == "fail_closed"
    for label in ("Bay (FL)", "Suwannee (FL)"):
        assert SCRAPER_SOURCE_STATES.get(label) != "fail_closed"
