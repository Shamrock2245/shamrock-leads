"""Hernando (FL) JailSearch contract, 2026-10-08.

The results grid publishes no bond, so Bond_Amount is "" (never $0) and old
Hernando "0" rows hydrate as unknown. A row without a source booking number is
skipped (never keyed on the name), and a response without the results table
raises instead of returning an empty success. Synthetic fixtures only.
"""
from __future__ import annotations

import pytest

from dashboard.services.packet_builder_service import arrest_bond_value
from scrapers.counties import hernando
from scrapers.counties.hernando import HernandoContractError, HernandoCountyScraper

HEADER = "<tr><th></th><th>Inmate Name Race/Sex/DOB Booking Number</th><th>Booking Date</th><th>Offenses</th><th>Image</th></tr>"


def _row(name="DOE, JANE Q", booking="HCSO26JBN005351", offenses=("BATTERY [1ct(s)]",)):
    return (f'<tr><td><a href="JailSearchDetails.aspx?BookNo={booking}">i</a></td>'
            f"<td>{name}<br/>W/F- 08/09/1990<br/>{booking}</td><td>10/06/2026<br/>03:12</td>"
            f"<td>{'<br/>'.join(offenses)}</td><td></td></tr>")


def _page(*rows):
    filler = "".join(_row(booking=f"HCSO26JBN00{i:04d}") for i in range(9000, 9004))
    return f"<html><table>{HEADER}{''.join(rows)}{filler}</table></html>"


def test_bond_unknown_never_zero_and_source_booking_key():
    recs = HernandoCountyScraper()._parse(_page(_row()))
    rec = next(r for r in recs if r.Booking_Number == "HCSO26JBN005351")
    assert rec.Bond_Amount == "" and rec.extra_data["bond_published"] is False
    assert rec.Booking_Date == "10/06/2026" and rec.Charges == "BATTERY [1ct(s)]"


def test_row_without_booking_number_is_skipped_not_keyed_on_name():
    recs = HernandoCountyScraper()._parse(_page(_row(booking="PENDING")))
    assert all(r.Booking_Number.startswith("HCSO26JBN") for r in recs)
    assert "PENDING" not in {r.Booking_Number for r in recs} and "" not in {r.Booking_Number for r in recs}


def test_missing_results_table_or_no_keyed_rows_raise():
    with pytest.raises(HernandoContractError):
        HernandoCountyScraper()._parse("<html><body>Service unavailable</body></html>")
    no_keys = f"<html><table>{HEADER}{''.join(_row(booking='X') for _ in range(5))}</table></html>"
    with pytest.raises(HernandoContractError):
        HernandoCountyScraper()._parse(no_keys)


def test_legacy_hernando_zero_hydrates_unknown():
    legacy = {"county": "Hernando", "state": "FL", "bond_amount": 0.0, "bond_amount_raw": "0"}
    assert arrest_bond_value(legacy) == ""
    assert arrest_bond_value({**legacy, "bond_amount": 1500.0, "bond_override": True}) == 1500.0


def test_module_uses_plain_requests_only():
    src = open(hernando.__file__).read().split('"""', 2)[2]  # code after the module docstring
    for banned in ("curl_cffi", "impersonate=", "verify=False", "DrissionPage"):
        assert banned not in src, banned
