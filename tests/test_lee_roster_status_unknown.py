"""Lee bulk roster: status only from the source; no flag/release/status is ""."""
import inspect

import pytest

from core import lee_county_master as lee_master
from scrapers.counties.lee import LeeCountyScraper

BASE = {"bookingNumber": "1029767", "givenName": "A", "surName": "B", "bookingDate": "2026-10-14T08:00:00"}


@pytest.mark.parametrize("extra,expected", [
    ({}, ""),
    ({"inCustody": None}, ""),
    ({"inCustody": True}, "In Custody"),
    ({"in_custody": True}, "In Custody"),
    ({"inCustody": False}, "Released"),
    ({"inCustody": True, "releaseDate": "2026-10-14T12:00:00"}, "Released"),
    ({"releaseDate": "2026-10-14T12:00:00"}, "Released"),
    ({"inCustody": True, "status": "Bonded Out"}, "Bonded Out"),
])
def test_roster_status_only_from_source(extra, expected):
    rec = LeeCountyScraper()._normalize_record({**BASE, **extra})
    assert rec is not None
    assert rec["status"] == expected


def test_unknown_roster_status_blocks_outreach():
    rec = LeeCountyScraper()._normalize_record(dict(BASE))
    assert lee_master.is_in_custody_status(rec["status"]) is False


def test_no_in_custody_default_in_roster_builder():
    src = inspect.getsource(LeeCountyScraper._normalize_record)
    assert '        status = ""\n' in src
    assert '\n        status = "In Custody"\n' not in src  # no unconditional default
