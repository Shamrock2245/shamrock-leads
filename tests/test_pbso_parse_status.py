"""PBSO parse: status only from a published release date or status line; never default In Custody."""
import inspect

from scrapers import pbso_parse
from scrapers.pbso_parse import parse_pbso_card_text, row_to_ingest_dict

BASE = "Name: DOE, J\nBooking Date/Time: 05/15/2026 10:34\nBooking Number: 2026012709\n"


def test_no_in_custody_default_in_source():
    src = inspect.getsource(pbso_parse)
    assert '"status": "In Custody"' not in src
    assert 'or "In Custody"' not in src


def test_release_na_gives_blank_status():
    row = parse_pbso_card_text(BASE + "Release Date: N/A\n")
    assert row["status"] == "" and row["release_date"] == ""


def test_no_release_field_gives_blank():
    assert parse_pbso_card_text(BASE)["status"] == ""


def test_published_release_wins():
    row = parse_pbso_card_text(BASE + "Release Date: 05/16/2026 08:00\n")
    assert row["status"] == "Released"
    assert row["release_date"] == "05/16/2026 08:00"


def test_published_status_line_kept():
    assert parse_pbso_card_text(BASE + "Status: IN JAIL\n")["status"] == "IN JAIL"
    assert parse_pbso_card_text(BASE + "Custody Status: Released\n")["status"] == "Released"


def test_case_status_is_not_custody_status():
    assert parse_pbso_card_text(BASE + "Case Status: PRESENTENCED\n")["status"] == ""


def test_release_beats_status_line():
    row = parse_pbso_card_text(BASE + "Status: IN JAIL\nRelease Date: 05/16/2026 08:00\n")
    assert row["status"] == "Released"


def test_ingest_dict_blank_status_not_in_custody():
    row = parse_pbso_card_text(BASE)
    assert row_to_ingest_dict(row, pbso_parse.BLOTTER_URL)["status"] == ""
    assert row_to_ingest_dict({"booking_num": "1"}, pbso_parse.BLOTTER_URL)["status"] == ""
    assert row_to_ingest_dict(parse_pbso_card_text(BASE + "Release Date: 05/16/2026\n"), "u")["status"] == "Released"
