"""Lee single-booking refresh: a missing inCustody flag is "", and "" never reads as in custody."""
import inspect
from unittest.mock import MagicMock

import pytest

from core import lee_county_master as lee_master
import scrapers.counties.lee as lee_mod

SCRAPER_CLS = next(c for n, c in vars(lee_mod).items()
                   if inspect.isclass(c) and c.__module__ == lee_mod.__name__ and hasattr(c, "_fetch_single_booking"))


def _resp(payload):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    return r


def _refresh(b_data):
    scraper = SCRAPER_CLS()
    scraper._http_fetch = lambda url: _resp([] if "harge" in url else b_data)
    return scraper._fetch_single_booking("1029767", "")


@pytest.mark.parametrize("b_data,expected", [
    ({"givenName": "A", "surName": "B"}, ""),
    ({"givenName": "A", "surName": "B", "inCustody": None}, ""),
    ({"givenName": "A", "surName": "B", "inCustody": True}, "In Custody"),
    ({"givenName": "A", "surName": "B", "inCustody": False}, "Released"),
])
def test_refresh_status_only_from_source(b_data, expected):
    rec = _refresh(b_data)
    assert rec is not None
    assert rec.Status == expected


def test_blank_status_blocks_outreach():
    rec = _refresh({"givenName": "A", "surName": "B"})
    assert rec.Status == ""
    assert lee_master.is_in_custody_status(rec.Status) is False
    assert "" not in lee_master.IN_CUSTODY_STATUSES
    assert lee_master.is_in_custody_status("In Custody") is True


def test_no_custody_default_in_source():
    assert 'get("inCustody", True)' not in inspect.getsource(SCRAPER_CLS._fetch_single_booking)
