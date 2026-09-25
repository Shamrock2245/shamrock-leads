"""NC/TX/SC gap queue 2026-09-25: Gaston / Pitt / Orange (NC) / Denton (TX)
parsers and Darlington (SC) DevExpress pager callbacks.

Fixtures are synthetic (placeholder names, fabricated-but-well-formed IDs) and
exercise only the parsing / request-shape contract; no network access.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest
from bs4 import BeautifulSoup

from scrapers import dcn_base
from scrapers.counties_nc import gaston, orange, pitt
from scrapers.counties_sc.darlington import DarlingtonScraper
from scrapers.counties_tx import denton
from scrapers.scraper_resilience import ParseDriftError


# ── Gaston (New World InmateInquiry) ─────────────────────────────────────────

GASTON_LISTING = """
<html><body><table>
<tr><th>Photo</th><th>Name</th><th>Subject Number</th><th>In Custody</th></tr>
<tr><td class="Photo"></td><td class="Name"><a href="/NewWorld.InmateInquiry/GastonCounty/Inmate/Detail/-111">DOE, JOHN Q</a></td>
<td class="SubjectNumber">123456</td><td class="InCustody">Yes</td></tr>
</table>
<a href="/NewWorld.InmateInquiry/GastonCounty?InCustody=True&amp;Page=2">Next</a>
</body></html>
"""


def _gaston_booking(number: str, release: str = "", bond: str = "$1,500.00") -> str:
    return f"""
<div class="Booking">
 <h3><label for="BookingNumberHeading">Booking</label><span id="BookingNumberHeading">{number}</span></h3>
 <div class="BookingData"><ul class="FieldList">
  <li class="BookingDate"><label>Booking Date</label><span id="BookingDate">9/24/2026 3:05 PM</span></li>
  <li class="ReleaseDate"><label>Release Date</label><span id="ReleaseDate">{release}</span></li>
  <li class="HousingFacility"><label>Housing Facility</label><span id="HousingFacility">Main Jail</span></li>
  <li class="TotalBondAmount"><label>Total Bond Amount</label><span id="TotalBondAmount">{bond}</span></li>
  <li class="BookingOrigin"><label>Booking Origin</label><span id="BookingOrigin">Test PD</span></li>
 </ul>
 <div class="BookingBonds"><table><thead><tr><th class="BondNumber">Bond Number</th></tr></thead>
  <tbody><tr><td class="BondNumber">B1</td><td class="BondType">Secured</td><td class="BondAmount">{bond}</td></tr></tbody></table></div>
 <div class="BookingCharges"><table><thead><tr><th>Number</th></tr></thead><tbody>
  <tr><td class="SeqNumber">1</td><td class="ChargeDescription">Larceny</td><td class="DocketNumber">26CR000001</td></tr>
 </tbody></table></div>
 </div>
</div>"""


def _gaston_detail(*bookings: str) -> str:
    return f"""
<html><body>
<ul><li class="Name"><label>Name</label><span>DOE, JOHN Q</span></li>
<li class="SubjectNumber"><label>Subject Number</label><span>123456</span></li>
<li class="DateOfBirth"><label>DOB</label><span>01/01/1990</span></li>
<li class="Gender"><label>Gender</label><span>Male</span></li></ul>
<div id="BookingHistory"><h2>Booking History</h2>{''.join(bookings)}</div>
</body></html>"""


def test_gaston_listing_links_and_next_page():
    links, nxt = gaston.parse_listing(GASTON_LISTING)
    assert links == [("DOE, JOHN Q", f"{gaston.PORTAL_URL}/Inmate/Detail/-111")]
    assert nxt and nxt.endswith("Page=2")


def test_gaston_detail_uses_booking_heading_not_subject_number():
    person, bookings = gaston.parse_detail(_gaston_detail(_gaston_booking("2026-00001234")))
    assert person["SubjectNumber"] == "123456"
    assert len(bookings) == 1
    bk = bookings[0]
    assert bk["booking_number"] == "2026-00001234"
    assert (bk["booking_date"], bk["booking_time"]) == ("9/24/2026", "3:05 PM")
    assert bk["bond"] == "1500.00" and bk["charges"] == "Larceny" and bk["dockets"] == "26CR000001"


def test_gaston_scrape_skips_released_and_unkeyed_bookings():
    detail = _gaston_detail(
        _gaston_booking("2026-00001234"),
        _gaston_booking("2025-00000999", release="1/2/2026"),
        _gaston_booking("not-a-number"),
    )

    def fake_get(url, params=None, timeout=None):
        body = GASTON_LISTING.replace(">Next<", "><") if "Detail" not in url else detail
        if params is not None:
            body = body + "<form><input name='BookingFromDate'></form><title>Inmate Search</title>"
        return mock.Mock(text=body, raise_for_status=lambda: None)

    with mock.patch.object(gaston.requests, "Session") as sess_cls:
        sess = sess_cls.return_value
        sess.headers = {}
        sess.get.side_effect = fake_get
        s = gaston.GastonScraper()
        s.DETAIL_DELAY_S = 0
        recs = s.scrape()
    assert [r.Booking_Number for r in recs] == ["2026-00001234"]
    assert recs[0].Person_ID == "123456" and recs[0].State == "NC"


def test_gaston_listing_without_any_source_booking_raises_drift():
    def fake_get(url, params=None, timeout=None):
        if "Detail" in url:
            return mock.Mock(text=_gaston_detail(), raise_for_status=lambda: None)
        body = GASTON_LISTING.replace(">Next<", "><") + "<input name='BookingFromDate'><title>Inmate Search</title>"
        return mock.Mock(text=body, raise_for_status=lambda: None)

    with mock.patch.object(gaston.requests, "Session") as sess_cls:
        sess_cls.return_value.headers = {}
        sess_cls.return_value.get.side_effect = fake_get
        s = gaston.GastonScraper()
        s.DETAIL_DELAY_S = 0
        with pytest.raises(ParseDriftError):
            s.scrape()


# ── Pitt (ASP.NET GridView) ──────────────────────────────────────────────────

def _pitt_page(rows: str, pager: str = "") -> BeautifulSoup:
    return BeautifulSoup(f"""
<table id="ctl00_mainContent_GridView1">
<tr><th></th><th>Last Name</th><th>Suffix</th><th>First Name</th><th>Middle Name</th><th>Date of Birth</th>
<th>Booking Number</th><th>Gender</th><th>Race</th><th>Print</th></tr>
{rows}
<tr><td colspan="10">{pager}</td></tr>
</table>""", "html.parser")


PITT_ROW = ('<tr><td><a href="javascript:__doPostBack(\'ctl00$mainContent$GridView1\',\'Select$0\')">Select</a></td>'
            '<td>DOE</td><td></td><td>JOHN</td><td>Q</td><td>01/01/1990</td><td>{bk}</td><td>M</td><td>W</td><td></td></tr>')


def test_pitt_grid_parses_booking_number_column():
    soup = _pitt_page(PITT_ROW.format(bk="123456") + PITT_ROW.format(bk=""))
    rows = pitt.parse_grid(soup)
    assert [r["booking"] for r in rows] == ["123456"]
    assert rows[0]["last"] == "DOE" and rows[0]["dob"] == "01/01/1990"


def test_pitt_header_drift_raises():
    soup = BeautifulSoup('<table id="ctl00_mainContent_GridView1"><tr><th>Name</th></tr>'
                         '<tr><td>x</td></tr></table>', "html.parser")
    with pytest.raises(ParseDriftError):
        pitt.parse_grid(soup)


def test_pitt_has_next_page_follows_pager_targets():
    pager = "<a href=\"javascript:__doPostBack('ctl00$mainContent$GridView1','Page$2')\">2</a>"
    assert pitt.has_next_page(_pitt_page(PITT_ROW.format(bk="1234"), pager), 1) is True
    assert pitt.has_next_page(_pitt_page(PITT_ROW.format(bk="1234"), pager), 2) is False


# ── Orange NC (daily PDF report) ─────────────────────────────────────────────

ORANGE_TEXT = """User: X, ORANGE COUNTY SHERIFF`S OFFICE 09/25/2026 07:53:44
Detainees In Confinement Report by Facility
Facility: MAIN JAIL
Days In
Name A/J R/S Bk # Alias Charge(s) / Arst /Jail Status / Docket / Bond $ / Status / Type /Crt/Crt Date Booked Date/TimeConfinement
DOE-SMITH, John Quincy A W M 70001 Larceny - Misdemeanor / X / X / 26CR000001-670 / $2,500 / X / X 09/10/2026 1631 15
Resisting Officer / X / X / 26CR000001-670 / $500 / X / X
ROE, Jane A B F 70002 Probation Violation / X / X / 26CR000002 / / X / X / 10/01/2026 08:30, 09/25/2026 0202
POE, Sam A B M 70003 Assault / X / X / 26CR000003 / $1,000 / X / Xyz09/21/2026 2132 4
"""


def test_orange_report_rows_use_source_bk_number():
    rows = orange.parse_report_text(ORANGE_TEXT)
    assert [r["booking"] for r in rows] == ["70001", "70002", "70003"]
    first = rows[0]
    assert (first["last"], first["first"], first["middle"]) == ("DOE-SMITH", "John", "Quincy")
    assert first["charges"] == ["Larceny - Misdemeanor", "Resisting Officer"]
    assert first["bond"] == 3000.0
    assert (first["booking_date"], first["booking_time"], first["days_in"]) == ("09/10/2026", "16:31", "15")
    assert rows[1]["booking_date"] == "09/25/2026" and rows[1]["days_in"] == ""
    assert rows[2]["booking_date"] == "09/21/2026"  # date glued to previous token


def test_orange_discovers_wix_usrfiles_and_site_pdf_links():
    html = ('<a href="https://abc-123.usrfiles.com/ugd/56522e_aaa.pdf">x</a>'
            '"https:\\/\\/www.ocsonc.com\\/_files\\/ugd\\/56522e_bbb.pdf"')
    assert orange.discover_pdf_urls(html) == [
        "https://abc-123.usrfiles.com/ugd/56522e_aaa.pdf",
        "https://www.ocsonc.com/_files/ugd/56522e_bbb.pdf",
    ]
    assert orange.report_timestamp(ORANGE_TEXT).isoformat() == "2026-09-25T07:53:44"


# ── Denton TX (Athena JailView) ──────────────────────────────────────────────

def test_denton_keeps_source_bookno_verbatim():
    payload = {"d": json.dumps([
        {"bookhandle": "11111", "bookno": "26000001", "datetimebooked": "09/24/2026 17:43",
         "name": "DOE, JOHN Q", "charges": "THEFT", "amount": "$2,500.00", "detainers": ""},
        {"bookhandle": "22222", "bookno": "", "name": "ROE, JANE"},
    ])}
    inmates = denton.decode_inmates(payload)
    s = denton.DentonScraper()
    recs = [r for r in (s.build_record(i) for i in inmates) if r]
    assert [r.Booking_Number for r in recs] == ["26000001"]  # no DEN_ prefix, no synthesis
    assert recs[0].Bond_Amount == "2500.00" and recs[0].Booking_Time == "17:43"
    assert recs[0].Person_ID == "11111"


def test_denton_envelope_drift_raises():
    with pytest.raises(ParseDriftError):
        denton.decode_inmates({"unexpected": []})


# ── Darlington SC (DCN DevExpress pager callbacks) ───────────────────────────

def _dcn_row(idx: int, bid: str) -> str:
    return (f'<tr id="gvInmates_DXDataRow{idx}"><td><a href="/DCN/inmate-details?id=x{idx}&bid={bid}">'
            f'DOE{idx}, JOHN</a></td><td>30</td><td>W</td><td>M</td><td>09/01/2026</td></tr>')


DCN_PAGE = (
    '<form method="post" action="./inmates" id="InmatesForm">'
    '<input type="hidden" name="__VIEWSTATE" value="VS" /><input type="hidden" name="__EVENTTARGET" value="" />'
    '<table>' + _dcn_row(0, "AAAA111%253d") + '</table></form>'
    "<script>ASPx.createControl(ASPxClientGridView,'gvInmates','grid',{'callBack':function(arg) {},"
    "'pageIndex':0,'pageCount':2,'stateObject':{'scrollState':null,'selection':'','callbackState':'CS1',"
    "'groupLevelState':{},'keys':['1']}});</script>"
)


def test_dcn_pager_callback_param_matches_devexpress_format():
    state = dcn_base.extract_grid_state(DCN_PAGE)
    assert state["callbackState"] == "CS1" and state["keys"] == ["1"]
    assert dcn_base.extract_page_count(DCN_PAGE) == 2
    assert dcn_base.pager_callback_param(state, 1) == 'c0:KV|5;["1"];GB|20;12|PAGERONCLICK3|PN1;'


def test_dcn_callback_response_unescapes_html_and_state():
    html = _dcn_row(100, "BBBB222%253d").replace("'", "\\'").replace("/", "\\/")
    reply = ("s/*DX*/({'result':{'html':'" + html + "\\r\\n','stateObject':{'scrollState':null,"
             "'callbackState':'CS2','keys':['2']}},'id':0})")
    got_html, state = dcn_base.parse_callback_response(reply)
    assert "inmate-details?id=x100&bid=BBBB222%253d" in got_html
    assert state == {"scrollState": None, "callbackState": "CS2", "keys": ["2"]}
    assert dcn_base.parse_callback_response("e0|boom") == (None, None)


def test_darlington_walks_callback_pages_with_source_bids_only():
    page2 = _dcn_row(100, "BBBB222%253d") + '<tr id="gvInmates_DXDataRow101"><td>NO LINK</td></tr>'
    reply = ("s/*DX*/({'result':{'html':'" + page2.replace("'", "\\'")
             + "','stateObject':{'callbackState':'CS2','keys':['2']}},'id':0})")
    sess = mock.Mock()
    sess.post.return_value = mock.Mock(text=reply, raise_for_status=lambda: None)
    s = DarlingtonScraper()
    s.callback_delay_s = 0
    origin = s._origin(s.inmates_url)
    roster = s._parse_roster(DCN_PAGE, origin)
    out = s._paginate_roster(sess, DCN_PAGE, origin, roster)
    assert [r["booking"] for r in out] == ["AAAA111=", "BBBB222="]
    sent = sess.post.call_args.kwargs["data"]
    assert sent["__CALLBACKID"] == "gvInmates"
    assert sent["__CALLBACKPARAM"].endswith("GB|20;12|PAGERONCLICK3|PN1;")
    assert json.loads(sent["gvInmates"])["callbackState"] == "CS1"
    assert sent["__VIEWSTATE"] == "VS"
    assert sess.post.call_count == 1  # pageCount=2 → one callback


def test_darlington_flags():
    assert DarlingtonScraper.require_source_bid is True
    assert DarlingtonScraper.paginate_callbacks is True
    assert DarlingtonScraper.max_detail_fetches >= 300


# ── Contract flags / schedule ────────────────────────────────────────────────

def test_rewritten_scrapers_are_plain_http_and_source_validated():
    for cls in (gaston.GastonScraper, pitt.PittScraper, orange.OrangeScraper, denton.DentonScraper):
        assert cls.SOURCE_CONTRACT_VALIDATED is True
    import inspect
    for mod in (gaston, pitt, orange, denton):
        src = inspect.getsource(mod)
        assert "create_stealth_session" not in src and "make_stealth_request" not in src
        assert "get_proxy" not in src and "impersonate" not in src
