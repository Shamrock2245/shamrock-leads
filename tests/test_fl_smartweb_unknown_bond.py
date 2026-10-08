"""SmartWEB JAIL View (fl_smartweb): an unknown bond stays empty, a real $0 stays.

Live 2026-10-08: Escambia/Putnam/Santa Rosa/Sumter publish real "$0.00" charge
cells alongside "NO BOND" and dollar amounts, so "$0.00" is a published value
and NO BOND / HOLD / blank is unknown. The booking total is set only when every
charge row publishes an amount. Synthetic fixtures; no network.
"""
from __future__ import annotations

import pytest

from scrapers import fl_smartweb


def _card(bookno: str, bond_cells: list[str], card_bond: str = "") -> str:
    rows = "".join(
        f"<tr><td></td><td>843.0{i}</td><td></td><td>CHARGE {i}</td><td>M</td><td>1</td><td>{b}</td></tr>"
        for i, b in enumerate(bond_cells)
    )
    charges = (
        '<tr><td><table class="JailViewCharges"><tr class="SearchHeader"><td>CHARGES</td></tr>'
        f"{rows}</table></td></tr>"
        if bond_cells
        else ""
    )
    card_line = f"<tr><td>Bond Amount: {card_bond}</td></tr>" if card_bond else ""
    return f"""
<tr class="InmateRecordRow">
  <td><img src='ViewImage.aspx?bookno={bookno}'></td>
  <td><table><thead><tr><td class="SearchHeader">ROE, JOHN A &nbsp; (B/ MALE )</td></tr></thead>
  <tbody>
    <tr><td>Status: In Jail</td></tr>
    <tr><td>Booking No: {bookno}</td></tr>
    <tr><td>Booking Date: 10/06/2026 08:21 AM</td></tr>
    {card_line}
  </tbody></table></td>
</tr>
{charges}
"""


def _bond(bond_cells: list[str], card_bond: str = "") -> str:
    recs = fl_smartweb._parse_html(
        "<table>" + _card("ECC26JBN000001", bond_cells, card_bond) + "</table>",
        set(),
        county="Escambia",
        facility="X",
        detail_url="https://example.test/",
    )
    assert len(recs) == 1
    return recs[0].Bond_Amount


@pytest.mark.parametrize(
    "cells, card, expected",
    [
        (["NO BOND"], "NO BOND", ""),  # main: "0"
        (["$1,500.00", "NO BOND"], "", ""),  # main: "1500" (understated)
        (["$1,500.00", ""], "", ""),  # main: "1500"
        (["HOLD"], "", ""),  # main: "0"
        (["$1,500.00", "$500.00 SURETY"], "", "2000"),
        (["$0.00"], "", "0"),  # real published $0 kept
        (["$0.00", "$1,500.00"], "", "1500"),
    ],
)
def test_total_bond_only_when_every_charge_publishes(cells, card, expected):
    assert _bond(cells, card) == expected


def test_card_level_bond_used_only_without_charge_rows():
    assert _bond([], "$750.00") == "750"
    assert _bond([], "NO BOND") == ""  # main: "0"
    assert _bond([], "") == ""  # main: "0"
    # Card-level $0.00 with no charges entered is the JAIL View default.
    assert _bond([], "$0.00") == ""  # main: "0"
    # Card says $0.00 but a charge is NO BOND: the charge grid wins (unknown).
    assert _bond(["NO BOND"], "$0.00") == ""


def test_run_raises_when_no_booking_has_charges(monkeypatch):
    """Charges-grid markup drift must not $set blank charges on every booking."""
    page = "<html><form><input name='tbBeginDate'></form></html>"
    results = "<table>" + _card("ECC26JBN000002", []) + _card("ECC26JBN000003", []) + "</table>"

    class _Resp:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    class _Session:
        headers: dict = {}

        def __init__(self):
            self.headers = {}

        def get(self, *a, **k):
            return _Resp(page)

        def post(self, *a, **k):
            return _Resp(results)

    monkeypatch.setattr(fl_smartweb.requests, "Session", _Session)
    with pytest.raises(RuntimeError, match="no booking in the run has charges"):
        fl_smartweb.scrape_smartweb_jail_view(
            county="Escambia", facility="X", base_url="https://example.test", lookback_days=1
        )
