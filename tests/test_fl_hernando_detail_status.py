"""Hernando (FL) detail pages, 2026-10-08: real custody status and per-case bonds.

The search includes released bookings, so status comes from
JailSearchDetails.aspx: "Release Date/Time: -" is In Custody, a date is Released,
and anything else is unknown, so the booking is skipped rather than defaulted
to In Custody. "$0.00" is the "no bond set" placeholder (ROR is a real $0), and
any unknown charge bond makes the total "". Synthetic fixtures; no network.
"""
from __future__ import annotations

import pytest

from core.models import ArrestRecord
from scrapers.counties import hernando
from scrapers.counties.hernando import (
    HernandoContractError,
    HernandoCountyScraper,
    HernandoDetailError,
    parse_detail,
)

BK = "HCSO26JBN009001"


def _case(court_case: str, charges: list[tuple[str, str]]) -> str:
    rows = "".join(
        f"<tr><td>843.02</td><td>RESIST OFFICER</td><td>1</td><td>{bond}</td><td>{other}</td></tr>"
        for bond, other in charges
    )
    return (
        "<table><tr><td>Case Seq.</td><td>Court Case #</td><td>Agency Case #</td><td></td><td>Arresting Agency</td></tr>"
        f"<tr><td>CASE0001</td><td>{court_case}</td><td>AG1</td><td></td><td>HCSO</td></tr>"
        "<tr><td colspan='5'><table><tr><td>Statute</td><td>Statute Description</td><td>Counts</td>"
        f"<td>Bond Amount</td><td>Other Information</td></tr>{rows}</table></td></tr></table>"
    )


def _detail(release="-", cases=None, booking=BK, release_span=True) -> str:
    cases = [_case("26 CF 0001", [("$1,500.00", "")])] if cases is None else cases
    span = f'<span id="{hernando.RELEASE_SPAN_ID}">{release}</span>' if release_span else release
    return (
        f'<html><table id="{hernando.BOOK_TABLE_ID}">'
        f"<tr><td>Inmate Name:</td><td>DOE, JANE</td></tr>"
        f"<tr><td>Booking #:</td><td>{booking}</td></tr>"
        f"<tr><td>Release Date/Time:</td><td>{span}</td></tr></table>"
        f"<table><tr><td>Case And Charge Information</td></tr></table>{''.join(cases)}</html>"
    )


@pytest.mark.parametrize(
    "release, status, date",
    [("-", "In Custody", ""), ("10/07/2026 14:05", "Released", "10/07/2026"), ("", None, ""), ("PENDING", None, "")],
)
def test_custody_status_from_release_label(release, status, date):
    d = parse_detail(_detail(release=release), BK)
    assert (d["status"], d["release_date"]) == (status, date)


@pytest.mark.parametrize(
    "charges, total",
    [
        ([("$1,500.00", ""), ("$2,500.00", "")], "4000"),
        ([("$1,500.00", ""), ("$0.00", "")], ""),  # $0.00 placeholder = unknown
        ([("$0.00", "")], ""),
        ([("$1,500.00", ""), ("", "")], ""),
        ([("$0.00", "ROR"), ("$500.00", "")], "500"),  # ROR is a real $0
        ([("$0.00", "ROR")], "0"),
        ([("NO BOND", "")], ""),
    ],
)
def test_total_bond_only_when_every_charge_publishes(charges, total):
    assert parse_detail(_detail(cases=[_case("26 CF 1", charges)]), BK)["bond_amount"] == total


def test_per_case_charges_keep_case_numbers():
    d = parse_detail(_detail(cases=[_case("26 CF 1", [("$100.00", "")]), _case("N/A", [("$200.00", "")])]), BK)
    assert [c["case_number"] for c in d["charges"]] == ["26 CF 1", ""]
    assert d["bond_amount"] == "300" and d["charge_grids"] == 2


def test_detail_drift_raises():
    with pytest.raises(HernandoDetailError):
        parse_detail("<html><p>maintenance</p></html>", BK)
    with pytest.raises(HernandoDetailError):
        parse_detail(_detail(booking="HCSO26JBN000002"), BK)


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status = text, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class _Session:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, **kw):
        page = self.pages[url.rsplit("=", 1)[1]]
        if isinstance(page, Exception):
            raise page
        return page


def _row(bk):
    return ArrestRecord(County="Hernando", Booking_Number=bk, Full_Name="DOE, JANE", Status="In Custody", Charges="ROSTER")


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(hernando, "REQUEST_PAUSE_S", 0)


def test_released_and_unreadable_and_failed_rows():
    pages = {
        "HCSO26JBN000001": _Resp(_detail(release="10/07/2026 14:05", booking="HCSO26JBN000001")),
        "HCSO26JBN000002": _Resp(_detail(release="-", booking="HCSO26JBN000002")),
        "HCSO26JBN000003": _Resp(_detail(release="??", booking="HCSO26JBN000003")),
        "HCSO26JBN000004": TimeoutError("timeout"),
        "HCSO26JBN000005": _Resp("<html>error</html>"),
    }
    out = HernandoCountyScraper()._enrich_from_details(_Session(pages), [_row(b) for b in pages])
    by = {r.Booking_Number: r for r in out}
    # Released is released (no longer "In Custody"); unreadable/failed/drifted are skipped, not blanked.
    assert set(by) == {"HCSO26JBN000001", "HCSO26JBN000002"}
    assert (by["HCSO26JBN000001"].Status, by["HCSO26JBN000001"].Release_Date) == ("Released", "10/07/2026")
    assert by["HCSO26JBN000002"].Status == "In Custody"
    assert by["HCSO26JBN000002"].Bond_Amount == "1500"
    assert by["HCSO26JBN000002"].Case_Number == "26 CF 0001"
    assert by["HCSO26JBN000002"].Detail_URL.endswith("BookNo=HCSO26JBN000002")


def test_run_raises_when_every_detail_fails_or_drifts_or_has_no_grid():
    bks = ["HCSO26JBN000001", "HCSO26JBN000002"]
    s = HernandoCountyScraper()
    with pytest.raises(HernandoContractError, match="detail fetches failed"):
        s._enrich_from_details(_Session({b: ConnectionError("x") for b in bks}), [_row(b) for b in bks])
    with pytest.raises(HernandoDetailError, match="no usable detail page"):
        s._enrich_from_details(_Session({b: _Resp("<html></html>") for b in bks}), [_row(b) for b in bks])
    with pytest.raises(HernandoDetailError, match="no detail page in the run has a charge grid"):
        s._enrich_from_details(
            _Session({b: _Resp(_detail(cases=[], booking=b)) for b in bks}), [_row(b) for b in bks]
        )


def test_blank_offenses_row_is_skipped_unless_the_detail_has_charges():
    """A blank roster Offenses cell must not $set blank charges over stored ones."""
    blank = _row("HCSO26JBN000001")
    blank.Charges = ""
    blank_with_detail = _row("HCSO26JBN000002")
    blank_with_detail.Charges = ""
    roster_only = _row("HCSO26JBN000003")  # roster offenses, no detail grid
    pages = {
        "HCSO26JBN000001": _Resp(_detail(cases=[], booking="HCSO26JBN000001")),
        "HCSO26JBN000002": _Resp(_detail(booking="HCSO26JBN000002")),
        "HCSO26JBN000003": _Resp(_detail(cases=[], booking="HCSO26JBN000003")),
    }
    out = HernandoCountyScraper()._enrich_from_details(_Session(pages), [blank, blank_with_detail, roster_only])
    by = {r.Booking_Number: r for r in out}
    assert set(by) == {"HCSO26JBN000002", "HCSO26JBN000003"}
    assert by["HCSO26JBN000002"].Charges == "843.02 - RESIST OFFICER"
    assert by["HCSO26JBN000003"].Charges == "ROSTER"
    assert by["HCSO26JBN000003"].Bond_Amount == ""  # no charge grid: unknown, never $0


def test_roster_parse_keeps_blank_offenses_for_the_detail_guard():
    html = (
        "<html><table><tr><th></th><th>Inmate Name Race/Sex/DOB Booking Number</th><th>Booking Date</th>"
        "<th>Offenses</th><th>Image</th></tr>"
        + "".join(
            f"<tr><td></td><td>DOE, JANE Q<br/>W/F- 08/09/1990<br/>HCSO26JBN00{i:04d}</td><td>10/06/2026</td>"
            f"<td>{'' if i == 1 else 'BATTERY'}</td><td></td></tr>"
            for i in range(1, 6)
        )
        + "</table></html>"
    )
    recs = HernandoCountyScraper()._parse(html)
    assert {r.Booking_Number: r.Charges for r in recs}["HCSO26JBN000001"] == ""
