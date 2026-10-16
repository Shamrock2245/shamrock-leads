"""url_ingest_service Lee API path: a missing inCustody flag is "", never "In Custody"."""
import inspect

import pytest

from dashboard.services import url_ingest_service as uis
from dashboard.services.url_ingest_service import published_custody_status


@pytest.mark.parametrize("data,expected", [
    ({}, ""), ({"inCustody": None}, ""), ({"inCustody": "maybe"}, ""), ({"inCustody": 1}, ""),
    ({"inCustody": True}, "In Custody"), ({"inCustody": False}, "Released"),
    ({"inCustody": "true"}, "In Custody"), ({"inCustody": "FALSE"}, "Released"),
    ({"status": "Bonded Out", "inCustody": True}, "Bonded Out"),
    ({"status": "  ", "inCustody": False}, "Released"), (None, ""),
])
def test_published_custody_status(data, expected):
    assert published_custody_status(data) == expected


def test_lee_api_writer_has_no_custody_default():
    src = inspect.getsource(uis._ingest_lee_county_api)
    assert 'get("inCustody", True)' not in src
    assert "published_custody_status(b_data)" in src
