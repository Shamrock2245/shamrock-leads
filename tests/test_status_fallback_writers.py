"""Status-fallback writers never invent a custody status; unknown is ""."""
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from dashboard.services import booking_extract_merge as bem
from dashboard.services import url_ingest_service as uis
from dashboard.services.booking_extract_merge import merge_booking_extract, published_status
from tests.test_booking_extract_merge import _bookmarklet_payload, collections  # noqa: F401


@pytest.mark.parametrize("payload,expected", [
    ({}, ""), ({"status": ""}, ""), ({"status": "  "}, ""), ({"status": None}, ""),
    ({"status": "Released"}, "Released"), ({"Status": "IN CUSTODY"}, "IN CUSTODY"),
    ({"custodyStatus": "Bonded Out"}, "Bonded Out"), (None, ""), ({"status": 1}, ""),
])
def test_published_status(payload, expected):
    assert published_status(payload) == expected


@pytest.mark.asyncio
async def test_insert_without_published_status_is_blank(collections):  # noqa: F811
    await merge_booking_extract(_bookmarklet_payload())
    assert collections["arrests"].inserted[0]["status"] == ""


@pytest.mark.asyncio
async def test_insert_keeps_published_status(collections):  # noqa: F811
    await merge_booking_extract(_bookmarklet_payload(status="Released"))
    assert collections["arrests"].inserted[0]["status"] == "Released"


@pytest.mark.asyncio
async def test_update_does_not_invent_status(collections):  # noqa: F811
    collections["arrests"].docs.append({
        "_id": "a1", "booking_number": "1029767", "county": "Lee",
        "full_name": "PERKINS, MICHAEL JAMES", "bond_amount": 0, "charges": "", "status": "",
    })
    await merge_booking_extract(_bookmarklet_payload())
    assert collections["arrests"].docs[0].get("status", "") != "In Custody"


async def _pbso(doc, monkeypatch):
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value=doc)
    monkeypatch.setattr("dashboard.extensions.get_db", lambda: {"arrests": coll})
    return await uis._ingest_pbso_from_mongo(
        "2026012709", "https://www3.pbso.org/blotter/index.cfm?booking=2026012709")


@pytest.mark.asyncio
async def test_pbso_mongo_unknown_status_is_blank(monkeypatch):
    got = await _pbso({"booking_number": "2026012709", "county": "Palm Beach", "state": "FL"}, monkeypatch)
    assert got is not None and got["status"] == ""


@pytest.mark.asyncio
async def test_pbso_mongo_keeps_published_status(monkeypatch):
    got = await _pbso({"booking_number": "2026012709", "county": "Palm Beach", "state": "FL",
                       "Status": "Released"}, monkeypatch)
    assert got["status"] == "Released"


def test_no_in_custody_fallback_in_writers():
    assert '"In Custody"' not in inspect.getsource(bem)
    src = inspect.getsource(uis._ingest_pbso_from_mongo)
    assert "In Custody" not in src
