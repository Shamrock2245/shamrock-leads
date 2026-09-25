"""FL gap queue 2026-09-25: Bay / Suwannee / Lake parsers + Leon / Gadsden holds.

Fixtures are synthetic (placeholder names, fabricated-but-well-formed IDs) and
exercise only the parsing contract; no network access.
"""
from __future__ import annotations

from unittest import mock

import pytest

from scrapers.counties import bay, lake, suwannee
from scrapers.counties.gadsden import GadsdenCountyScraper
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


# ── Lake (LCSO recent_data behind Turnstile; owner-approved SolveCaptcha) ─────

def _lake_row(**over):
    row = {
        "pin": "26000123", "lastname": "DOE", "firstname": "JOHN", "middle": "Q",
        "comdate": "2026-09-25 02:11:00", "ar_date": "2026-09-24 23:40:00", "birth": "1990-01-02",
        "arrest": "EXAMPLE P.D.", "race": "W", "sex": "M", "age": 36, "exemption": 0, "sysid": 1,
        "total_bond_amount": 1500, "total_bond_paid": 0, "total_bond_balance": 1500, "charge_count": 2,
        "charges": [
            {"chid": "1", "note": "PETIT THEFT", "morf": "SECOND DEGREE", "degree": "MISDEMEANOR", "casenum": "n/a"},
            {"chid": "2", "note": "RESISTING", "morf": "FIRST DEGREE", "degree": "MISDEMEANOR", "casenum": "2026-MM-000001"},
        ],
    }
    row.update(over)
    return row


def test_lake_parses_source_booking_pin_and_fields():
    rec = lake.parse_record(_lake_row())
    assert rec.Booking_Number == "26000123"
    assert (rec.County, rec.State) == ("Lake", "FL")
    assert (rec.Booking_Date, rec.Booking_Time) == ("09/25/2026", "02:11 AM")
    assert (rec.Arrest_Date, rec.Arrest_Time) == ("09/24/2026", "11:40 PM")
    assert rec.Full_Name == "DOE, JOHN Q"
    assert rec.Charges == "PETIT THEFT (SECOND DEGREE MISDEMEANOR) | RESISTING (FIRST DEGREE MISDEMEANOR)"
    assert rec.Case_Number == "2026-MM-000001"
    assert rec.Bond_Amount == "1500" and rec.Agency == "EXAMPLE P.D."


@pytest.mark.parametrize("over", [{"pin": ""}, {"pin": "ABC12345"}, {"pin": "123"}, {"exemption": 1}, {"lastname": ""}])
def test_lake_drops_rows_without_source_key_or_exempt(over):
    assert lake.parse_record(_lake_row(**over)) is None


def test_lake_no_bond_and_dedupe():
    assert lake.parse_record(_lake_row(total_bond_amount=-1)).Bond_Type == "NO BOND"
    recs = lake.parse_records([_lake_row(), _lake_row(), _lake_row(pin="26000124")])
    assert [r.Booking_Number for r in recs] == ["26000123", "26000124"]


def _resp(status=200, text="", js=None, ctype="application/json"):
    r = mock.Mock(status_code=status, text=text, headers={"content-type": ctype})
    r.raise_for_status = mock.Mock()
    r.json = mock.Mock(return_value=js) if js is not None else mock.Mock(side_effect=ValueError("no json"))
    return r


def test_lake_requires_solvecaptcha_key(monkeypatch):
    monkeypatch.setenv("SOLVECAPTCHA_KEY", "")
    with pytest.raises(RuntimeError, match="SOLVECAPTCHA_KEY"):
        lake.LakeCountyScraper().scrape()


def test_lake_scrape_posts_recent_data_with_one_token(monkeypatch):
    monkeypatch.setenv("SOLVECAPTCHA_KEY", "test-key")
    sess = mock.Mock()
    sess.headers = {}
    sess.get.return_value = _resp(text=f'data-sitekey="{lake.TURNSTILE_SITEKEY}"')
    sess.post.return_value = _resp(js={"records": [_lake_row()], "captcha": {"success": True}})
    with mock.patch.object(lake.requests, "Session", return_value=sess), \
         mock.patch.object(lake, "solve_turnstile", return_value="tok") as solver:
        recs = lake.LakeCountyScraper().scrape()
    assert [r.Booking_Number for r in recs] == ["26000123"]
    solver.assert_called_once()
    assert solver.call_args.kwargs["sitekey"] == lake.TURNSTILE_SITEKEY
    import json as _json
    body = _json.loads(sess.post.call_args.kwargs["data"])
    assert body == {"token": "tok", "recent_data": True}


def test_lake_sitekey_drift_and_bad_payload_raise(monkeypatch):
    from scrapers.scraper_resilience import AntiBotBlocked, ParseDriftError
    monkeypatch.setenv("SOLVECAPTCHA_KEY", "test-key")
    sess = mock.Mock()
    sess.headers = {}
    sess.get.return_value = _resp(text="<html>no widget</html>")
    with mock.patch.object(lake.requests, "Session", return_value=sess), \
         mock.patch.object(lake, "solve_turnstile", return_value="tok"):
        with pytest.raises(ParseDriftError):
            lake.LakeCountyScraper().scrape()
        sess.get.return_value = _resp(text=lake.TURNSTILE_SITEKEY)
        sess.post.return_value = _resp(js={"records": [_lake_row(pin="x")]})
        with pytest.raises(ParseDriftError):
            lake.LakeCountyScraper().scrape()
    with mock.patch.object(lake.requests, "Session", return_value=sess), \
         mock.patch.object(lake, "solve_turnstile", return_value=None):
        with pytest.raises(AntiBotBlocked):
            lake.LakeCountyScraper().scrape()


def test_lake_scraper_is_source_validated():
    assert lake.LakeCountyScraper.SOURCE_CONTRACT_VALIDATED is True


# ── Shared SolveCaptcha helper ────────────────────────────────────────────────

def test_solve_turnstile_passes_action_and_never_logs_key(caplog):
    from scrapers import solvecaptcha
    http = mock.Mock()
    http.post.return_value = mock.Mock(json=lambda: {"status": 1, "request": "42"})
    http.get.side_effect = [
        Exception("GET https://api.solvecaptcha.com/res.php?key=SECRETKEY failed"),
        mock.Mock(json=lambda: {"status": 0, "request": "CAPCHA_NOT_READY"}),
        mock.Mock(json=lambda: {"status": 1, "request": "TOKEN"}),
    ]
    with caplog.at_level("DEBUG"):
        tok = solvecaptcha.solve_turnstile(
            "SECRETKEY", sitekey="sk", pageurl="https://x/", action="arrest_search",
            http=http, sleep=lambda _s: None,
        )
    assert tok == "TOKEN"
    assert http.post.call_args.kwargs["data"]["action"] == "arrest_search"
    assert "SECRETKEY" not in caplog.text


def test_broward_uses_shared_solver():
    from scrapers.counties import broward
    with mock.patch.object(broward, "solve_turnstile", return_value="t") as solver:
        assert broward.BrowardCountyScraper()._solve_turnstile("k") == "t"
    kw = solver.call_args.kwargs
    assert kw["action"] == "arrest_search" and kw["sitekey"] == broward.TURNSTILE_SITEKEY


# ── Holds ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cls", [LeonCountyScraper, GadsdenCountyScraper])
def test_fl_holds_fail_closed_without_network(cls):
    scraper = cls()
    assert scraper.SOURCE_CONTRACT_VALIDATED is False
    assert scraper.SOURCE_CONTRACT_REASON
    with mock.patch("requests.Session.request", side_effect=AssertionError("network")):
        assert scraper.scrape() == []


def test_fl_holds_are_fail_closed_in_registry():
    from dashboard.extensions import SCRAPER_SOURCE_STATES
    for label in ("Leon (FL)", "Gadsden (FL)"):
        assert SCRAPER_SOURCE_STATES.get(label) == "fail_closed"
    # Promoted after the 2026-09-25 Mac write smokes (source booking keys).
    # Lake: owner decision 2026-09-25 (SolveCaptcha Turnstile, Broward precedent).
    for label in ("Bay (FL)", "Suwannee (FL)", "Lake (FL)"):
        assert SCRAPER_SOURCE_STATES.get(label) == "verified_public"


# ── Scheduling ────────────────────────────────────────────────────────────────

def _registered_intervals():
    import logging

    import main

    class _Collect:
        def __init__(self):
            self.intervals = {}

        def register_scraper(self, scraper, interval_minutes=None):
            self.intervals[f"{scraper.county} ({scraper.state})"] = interval_minutes

    logging.disable(logging.CRITICAL)
    try:
        sched = _Collect()
        main.register_scrapers(sched)
        return sched.intervals
    finally:
        logging.disable(logging.NOTSET)


def test_bay_runs_every_six_hours():
    # Brendan 2026-09-25: the 676-search Bay walk runs every 6 h, not every 120 min.
    assert _registered_intervals()["Bay (FL)"] == 360
