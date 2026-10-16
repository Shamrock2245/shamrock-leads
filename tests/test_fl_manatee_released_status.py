"""Manatee: only a published Released label or date sets Status, with status_source."""
import pytest

from scrapers.counties.manatee import build_records, parse_roster_page
from tests.test_fl_sarasota_manatee_audit import _page, _row


def _rec(*released):
    return build_records(parse_roster_page(_page([_row(charge=f"C{i}", released=r) for i, r in enumerate(released)])))[0]


@pytest.mark.parametrize("value", ["", "No", "N/A", "-"])
def test_blank_no_na_give_empty_status(value):
    rec = _rec(value)
    assert rec.Status == "" and "status_source" not in rec.extra_data


def test_in_custody_label():
    rec = _rec("In Custody")
    assert (rec.Status, rec.extra_data["status_source"]) == ("In Custody", "published_released_cell_label")


def test_release_date():
    rec = _rec("10/07/2026 09:15")
    assert (rec.Status, rec.Release_Date, rec.extra_data["status_source"]) == (
        "Released", "2026-10-07", "published_release_date")


def test_released_label():
    rec = _rec("Released")
    assert (rec.Status, rec.extra_data["status_source"]) == ("Released", "published_released_cell_label")


def test_mixed_rows_label_wins_over_blank():
    assert _rec("", "In Custody").Status == "In Custody"
    assert _rec("", "Released").Status == ""
