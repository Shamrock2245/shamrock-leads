"""FL Brevard + Walton/Flagler (New World) source booking contracts, 2026-10-07.

Fixtures are synthetic (placeholder names, fabricated-but-well-formed IDs) and
exercise only the parsing contract; no network access.
"""
from __future__ import annotations

import pytest

import requests

from scoring.lead_scorer import LeadScorer
from scrapers import fl_newworld
from scrapers.counties import brevard
from scrapers.counties.flagler import FlaglerCountyScraper
from scrapers.counties.walton import WaltonCountyScraper


# ── Brevard ──────────────────────────────────────────────────────────────────

BREVARD_RESULTS = """
<html><body><table>
<thead><tr><th>Booking #</th><th>Name</th><th>DOB</th><th>Booking Date</th><th>Released</th></tr></thead>
<tbody>
<tr><td><a href="/Details/-100002">2026-00000002</a></td><td>DOE, JANE Q</td><td>1/15/1990</td><td>10/6/2026 11:44 PM</td><td>No</td></tr>
<tr><td><a href="/Details/-100001">2026-00000001</a></td><td>ROE, JOHN</td><td>2/2/1980</td><td>10/6/2026 8:05 AM</td><td>Yes</td></tr>
</tbody></table></body></html>
"""

BREVARD_DETAIL = """
<main>
<div class="card"><div class="card-header">Booking Details - 2026-00000002</div>
<div class="card-body"><dl class="row">
<dt>Name</dt><dd>DOE, JANE Q</dd><dt>Gender</dt><dd>Female</dd><dt>Race</dt><dd>White</dd>
</dl></div></div>
<div class="card"><div class="card-header">Bonds</div><div class="card-body"><table>
<thead><tr><th>BCSO Bond Number</th><th>Bond Type</th><th>Bond Amount</th><th>Charge Count</th><th>Comments</th></tr></thead>
<tbody><tr><td>2026-00000901</td><td>Surety</td><td>$2,500.00</td><td>1</td><td></td></tr>
<tr><td>2026-00000902</td><td>No Bond</td><td>$0.00</td><td>1</td><td></td></tr></tbody></table></div></div>
<div class="card"><div class="card-header">Charges</div><div class="card-body"><table>
<thead><tr><th>Bond Number</th><th>Charge</th><th>Offense Date</th></tr></thead>
<tbody><tr><td>2026-00000901</td><td>BATTERY</td><td>10/06/2026</td></tr>
<tr><td>2026-00000902</td><td>VOP</td><td>10/06/2026</td></tr></tbody></table></div></div>
<h3>Booking History</h3>
<div class="card"><div class="card-header">Booking #: 2019-00000077 | Date: 01/01/2019</div>
<div class="card-body"><h5>Bonds</h5><table><thead><tr><th>Bond #</th><th>Type</th><th>Amount</th></tr></thead>
<tbody><tr><td>2019-1</td><td>Cash</td><td>$99,999.00</td></tr></tbody></table></div></div>
</main>
"""


def test_brevard_results_map_columns_by_header():
    rows = brevard.parse_results_html(BREVARD_RESULTS)
    assert [r["booking"] for r in rows] == ["2026-00000002", "2026-00000001"]
    assert rows[0]["name"] == "DOE, JANE Q"
    assert rows[0]["dob"] == "1/15/1990"
    assert rows[0]["booking_date"] == "10/6/2026 11:44 PM"
    assert rows[1]["released"] == "Yes"
    assert rows[0]["detail_url"].endswith("/Details/-100002")


def test_brevard_results_header_drift_raises():
    html = BREVARD_RESULTS.replace("<th>Booking #</th>", "<th>Jacket</th>")
    with pytest.raises(brevard.BrevardContractError):
        brevard.parse_results_html(html)


def test_brevard_no_results_is_empty():
    assert brevard.parse_results_html('<div class="alert">No results found.</div>') == []


def test_brevard_detail_sums_current_booking_bonds_only():
    d = brevard.parse_detail_html(BREVARD_DETAIL, "2026-00000002")
    assert d is not None
    assert d["bond"] == "2500.00"  # history card $99,999 ignored
    assert d["charges"] == "BATTERY | VOP"
    # Type from the positive-amount rows: the $0 No Bond row must not mask Surety.
    assert d["bond_type"] == "Surety"
    assert d["sex"] == "Female"


def test_brevard_detail_rejects_mismatched_booking():
    assert brevard.parse_detail_html(BREVARD_DETAIL, "2026-00000003") is None


def test_brevard_scrape_end_to_end(monkeypatch):
    home = (
        '<form method="post" action="/Results">'
        '<input type="date" max="2026-10-06" name="SearchForm.ToDate" />'
        '<input name="__RequestVerificationToken" type="hidden" value="tok" /></form>'
    )

    class Resp:
        def __init__(self, text, status=200):
            self.text, self.status_code = text, status

        def raise_for_status(self):
            pass

    calls = []

    class FakeSession:
        headers = {}

        def get(self, url, **kw):
            calls.append(("GET", url))
            return Resp(BREVARD_DETAIL if "/Details/" in url else home)

        def post(self, url, data=None, **kw):
            calls.append(("POST", url))
            assert ("__RequestVerificationToken", "tok") in data
            assert ("SearchForm.ToDate", "2026-10-06") in data
            return Resp(BREVARD_RESULTS)

    monkeypatch.setattr(brevard.requests, "Session", FakeSession)
    monkeypatch.setattr(brevard, "REQUEST_PAUSE_S", 0)
    recs = brevard.BrevardCountyScraper().scrape()
    assert [r.Booking_Number for r in recs] == ["2026-00000002", "2026-00000001"]
    jane, john = recs
    assert jane.Full_Name == "DOE, JANE Q" and jane.Booking_Date == "10/6/2026" and jane.Booking_Time == "11:44 PM"
    assert jane.Bond_Amount == "2500.00" and jane.Status == "In Custody" and jane.Sex == "F"
    # Released row: no detail fetched, so bond is unknown (""), never an invented $0.
    assert john.Status == "Released" and john.Bond_Amount == "" and john.Charges == ""
    # Detail fetched only for the in-custody row.
    assert sum(1 for m, u in calls if "/Details/" in u) == 1
    assert ("POST", brevard.BASE_URL + "/?handler=Search") in calls


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        pass


_HOME = (
    '<form method="post" action="/Results">'
    '<input type="date" max="2026-10-06" name="SearchForm.ToDate" />'
    '<input name="__RequestVerificationToken" type="hidden" value="tok" /></form>'
)


def _scrape_with_detail(monkeypatch, detail_get):
    """Run the Brevard scraper with a fake session whose detail GET is ``detail_get(url)``."""

    class FakeSession:
        headers = {}

        def get(self, url, **kw):
            return detail_get(url) if "/Details/" in url else _Resp(_HOME)

        def post(self, url, data=None, **kw):
            return _Resp(BREVARD_RESULTS)

    monkeypatch.setattr(brevard.requests, "Session", FakeSession)
    monkeypatch.setattr(brevard, "REQUEST_PAUSE_S", 0)
    return brevard.BrevardCountyScraper().scrape()


def _timeout(url):
    raise requests.Timeout("read timed out")


@pytest.mark.parametrize(
    "detail_get",
    [
        _timeout,
        lambda url: _Resp("Service Unavailable", status=503),
        lambda url: _Resp(BREVARD_DETAIL.replace("2026-00000002", "2026-00000099")),  # mismatched booking
    ],
    ids=["timeout", "non-200", "mismatched-booking"],
)
def test_brevard_failed_detail_keeps_bond_unknown_not_zero(monkeypatch, detail_get):
    recs = _scrape_with_detail(monkeypatch, detail_get)
    jane = next(r for r in recs if r.Booking_Number == "2026-00000002")
    assert jane.Status == "In Custody"
    assert jane.Bond_Amount == "" and jane.Bond_Type == ""
    scorer = LeadScorer()
    scorer.score_arrest(jane)
    assert not any("Bond amount" in r for r in scorer.get_score_breakdown())  # no $0 (-50) penalty


def test_brevard_published_zero_bond_is_kept():
    html = BREVARD_DETAIL.replace("<td>Surety</td><td>$2,500.00</td>", "<td>No Bond</td><td>$0.00</td>")
    d = brevard.parse_detail_html(html, "2026-00000002")
    assert d["bond"] == "0.00" and d["bond_type"] == "No Bond"


def test_brevard_mixed_bond_scores_as_surety_not_no_bond(monkeypatch):
    recs = _scrape_with_detail(monkeypatch, lambda url: _Resp(BREVARD_DETAIL))
    jane = next(r for r in recs if r.Booking_Number == "2026-00000002")
    assert jane.Bond_Amount == "2500.00" and jane.Bond_Type == "Surety"
    scorer = LeadScorer()
    scorer.score_arrest(jane)
    breakdown = scorer.get_score_breakdown()
    assert any("CASH/SURETY" in r and "+25" in r for r in breakdown)
    assert not any("NO BOND" in r for r in breakdown)


def test_brevard_bond_type_dedupes_positive_rows():
    html = BREVARD_DETAIL.replace("<td>No Bond</td><td>$0.00</td>", "<td>Surety</td><td>$1,000.00</td>")
    d = brevard.parse_detail_html(html, "2026-00000002")
    assert d["bond"] == "3500.00" and d["bond_type"] == "Surety"


def test_brevard_released_column_required():
    html = BREVARD_RESULTS.replace("<th>Released</th>", "<th>Status</th>")
    with pytest.raises(brevard.BrevardContractError, match="released"):
        brevard.parse_results_html(html)


@pytest.mark.parametrize("value", ["", "Pending", "Transferred", "N"])
def test_brevard_unrecognized_released_value_fails_closed(value):
    html = BREVARD_RESULTS.replace("<td>No</td>", f"<td>{value}</td>")
    with pytest.raises(brevard.BrevardContractError, match="Released"):
        brevard.parse_results_html(html)


def test_brevard_released_values_case_insensitive():
    html = BREVARD_RESULTS.replace("<td>No</td>", "<td> NO </td>").replace("<td>Yes</td>", "<td>yes</td>")
    assert [r["released"].strip().lower() for r in brevard.parse_results_html(html)] == ["no", "yes"]


# ── New World (Walton / Flagler) ─────────────────────────────────────────────

def _booking_block(number: str, release: str, bond: str, charges: list[str]) -> str:
    rows = "".join(f'<tr><td class="SeqNumber">1</td><td class="ChargeDescription">{c}</td></tr>' for c in charges)
    return f"""
<div class="Booking"><h3><label>Booking</label><span>{number}</span></h3>
<div class="BookingData"><ul class="FieldList">
<li class="BookingDate"><label>Booking Date</label><span>10/5/2026 9:41 PM</span></li>
<li class="ReleaseDate"><label>Release Date</label><span>{release}</span></li>
<li class="TotalBondAmount"><label>Total Bond Amount</label><span>{bond}</span></li>
<li class="BookingOrigin"><label>Booking Origin</label><span>Sheriff's Office</span></li>
</ul>
<div class="BookingBonds"><table><thead><tr><th class="BondNumber">Bond Number</th></tr></thead>
<tbody><tr><td class="BondNumber">2026-00009999</td></tr></tbody></table></div>
<div class="BookingCharges"><table><thead><tr><th class="SeqNumber">Number</th><th class="ChargeDescription">Charge Description</th></tr></thead>
<tbody>{rows}</tbody></table></div></div></div>
"""


def _detail(*blocks: str) -> str:
    return f"""
<html><body><h1>Inmate Detail - DOE, JANE Q</h1>
<h2>Demographic Information</h2><ul class="FieldList">
<li class="Name"><label>Name</label><span>DOE, JANE Q</span></li>
<li class="SubjectNumber"><label>Subject Number</label><span>123</span></li>
<li class="DateOfBirth"><label>Date of Birth</label><span>01/15/1990</span></li>
<li class="Gender"><label>Gender</label><span>Female</span></li>
</ul>
<h2>Booking History</h2>
{''.join(blocks)}
</body></html>
"""


def test_newworld_uses_open_booking_not_history_heading():
    html = _detail(
        _booking_block("2026-00001234", "", "$1,500.00", ["VOP FELONY", "DUI"]),
        _booking_block("2025-00000999", "3/1/2025 10:00 AM", "$9,999.00", ["OLD"]),
    )
    rec = fl_newworld.detail_to_record(html, county="Walton", facility="Walton County Jail", detail_url="u")
    assert rec is not None
    assert rec.Booking_Number == "2026-00001234"  # never "History", never the subject number
    assert rec.Booking_Date == "10/5/2026" and rec.Booking_Time == "9:41 PM"
    assert rec.Bond_Amount == "1500.00"
    assert rec.Charges == "VOP FELONY | DUI"
    assert rec.Sex == "F" and rec.DOB == "01/15/1990" and rec.State == "FL"


def test_newworld_drops_detail_without_open_booking():
    html = _detail(_booking_block("2025-00000999", "3/1/2025 10:00 AM", "$0.00", ["OLD"]))
    assert fl_newworld.detail_to_record(html, county="Walton", facility="x", detail_url="u") is None


def test_newworld_drops_malformed_open_booking():
    html = _detail(_booking_block("History", "", "$0.00", []))
    assert fl_newworld.detail_to_record(html, county="Flagler", facility="x", detail_url="u") is None


def test_newworld_bond_unknown_when_source_blank():
    html = _detail(_booking_block("2026-00000001", "", "", ["TRESPASS"]))
    rec = fl_newworld.detail_to_record(html, county="Flagler", facility="x", detail_url="u")
    assert rec is not None and rec.Bond_Amount == ""
    scorer = LeadScorer()
    scorer.score_arrest(rec)
    assert not any("$0" in r for r in scorer.get_score_breakdown())


def test_newworld_bond_zero_only_when_source_publishes_zero():
    html = _detail(_booking_block("2026-00000001", "", "$0.00", ["TRESPASS"]))
    rec = fl_newworld.detail_to_record(html, county="Walton", facility="x", detail_url="u")
    assert rec is not None and rec.Bond_Amount == "0.00"


def test_newworld_listing_links_absolute_and_deduped():
    html = (
        '<table><tr><th>Name</th></tr>'
        '<tr><td><a href="/NewWorld.InmateInquiry/FL0180000/Inmate/Detail/-1">DOE, JANE</a></td></tr>'
        '<tr><td><a href="/NewWorld.InmateInquiry/FL0180000/Inmate/Detail/-1">DOE, JANE</a></td></tr>'
        '<tr><td><a href="/NewWorld.InmateInquiry/FL0180000/Inmate/Detail/-2">ROE, JOHN</a></td></tr></table>'
    )
    links = fl_newworld.parse_listing_links(html, "https://nwwebcad.fcpsn.org/NewWorld.InmateInquiry/FL0180000/")
    assert [u for _, u in links] == [
        "https://nwwebcad.fcpsn.org/NewWorld.InmateInquiry/FL0180000/Inmate/Detail/-1",
        "https://nwwebcad.fcpsn.org/NewWorld.InmateInquiry/FL0180000/Inmate/Detail/-2",
    ]


def test_newworld_roster_raises_on_layout_drift():
    class Resp:
        status_code = 200
        text = "<html><body>maintenance</body></html>"

    class S:
        headers = {}

        def get(self, *a, **k):
            return Resp()

    with pytest.raises(RuntimeError):
        fl_newworld.scrape_newworld_roster(county="Walton", facility="x", portal_url="https://x/p", session=S())


@pytest.mark.parametrize("cls,label", [(WaltonCountyScraper, "Walton"), (FlaglerCountyScraper, "Flagler")])
def test_newworld_counties_are_fl_and_validated(cls, label):
    s = cls()
    assert s.county == label and s.state == "FL"
    assert cls.SOURCE_CONTRACT_VALIDATED is True
