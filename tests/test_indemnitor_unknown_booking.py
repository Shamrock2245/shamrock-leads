from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.indemnitors import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _collection(*, find_one=None):
    coll = AsyncMock()
    coll.find_one = AsyncMock(return_value=find_one)
    coll.insert_one = AsyncMock()
    coll.update_one = AsyncMock()
    coll.update_many = AsyncMock()
    return coll


@patch("dashboard.routers.indemnitors.get_db")
@patch("dashboard.routers.indemnitors.get_collection")
def test_unknown_booking_saves_unlinked_and_does_not_invent_a_bond(
    mock_get_collection, mock_get_db, client
):
    prospective = _collection()
    active = _collection()
    indemnitors = _collection()

    def _by_name(name):
        return {
            "prospective_bonds": prospective,
            "active_bonds": active,
            "indemnitors": indemnitors,
        }[name]

    mock_get_collection.side_effect = _by_name
    arrests = MagicMock()
    arrests.find.return_value.to_list = AsyncMock(return_value=[])
    mock_get_db.return_value.arrests = arrests

    response = client.post(
        "/api/indemnitors/create",
        json={"firstName": "Pat", "lastName": "Lee", "booking_number": "NO-SUCH-BOOKING"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["linked"] is False
    assert str(body["booking_number"]).startswith("UNLINKED-")
    prospective.insert_one.assert_not_called()
    active.insert_one.assert_not_called()
    indemnitors.insert_one.assert_awaited_once()
    saved = indemnitors.insert_one.await_args.args[0]
    assert saved["status"] == "unlinked"
    assert saved["pending_booking_number"] == "NO-SUCH-BOOKING"


@patch("dashboard.routers.indemnitors.get_db")
@patch("dashboard.routers.indemnitors.get_collection")
def test_ambiguous_booking_saves_unlinked_instead_of_guessing(
    mock_get_collection, mock_get_db, client
):
    prospective = _collection()
    active = _collection()
    indemnitors = _collection()
    mock_get_collection.side_effect = lambda name: {
        "prospective_bonds": prospective,
        "active_bonds": active,
        "indemnitors": indemnitors,
    }[name]
    arrests = MagicMock()
    arrests.find.return_value.to_list = AsyncMock(
        return_value=[
            {"booking_number": "12", "county": "Lee", "state": "FL"},
            {"booking_number": "12", "county": "Lee", "state": "GA"},
        ]
    )
    mock_get_db.return_value.arrests = arrests

    response = client.post(
        "/api/indemnitors/create",
        json={"firstName": "Pat", "lastName": "Lee", "booking_number": "12"},
    )

    assert response.status_code == 200
    assert response.json()["linked"] is False
    prospective.insert_one.assert_not_called()
    indemnitors.insert_one.assert_awaited_once()
