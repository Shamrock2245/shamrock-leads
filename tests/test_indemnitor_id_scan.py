"""A later indemnitor save must not drop a stored ID scan.

Synthetic TEST- records only. No DocuSeal send and no payment charge.
"""
from unittest.mock import AsyncMock, patch

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
    return coll


def _bond_with_scan():
    return {
        "booking_number": "TEST-BK-1",
        "indemnitors": [
            {
                "name": "TEST INDEMNITOR",
                "phone": "555-0100",
                "role": "primary",
                "id_scan": {"scan_id": "TEST-SCAN-1"},
                "portrait_jpeg_b64": "TEST-PORTRAIT-1",
                "id_scan_history": [{"id_scan": {"scan_id": "TEST-SCAN-0"}}],
            }
        ],
    }


@patch("dashboard.routers.indemnitors.get_db")
@patch("dashboard.routers.indemnitors.get_collection")
def test_bond_update_keeps_id_scan_when_the_body_omits_it(mock_get_collection, mock_get_db, client):
    prospective = _collection(find_one=_bond_with_scan())
    active = _collection(find_one=None)
    mock_get_collection.side_effect = lambda name: {
        "prospective_bonds": prospective,
        "active_bonds": active,
        "indemnitors": _collection(),
    }[name]

    response = client.post(
        "/api/indemnitors/create",
        json={
            "firstName": "TEST",
            "lastName": "INDEMNITOR",
            "phone": "555-0100",
            "booking_number": "TEST-BK-1",
            "id_scan": {},
        },
    )

    assert response.status_code == 200
    assert response.json()["action"] == "updated_existing"
    saved = prospective.update_one.await_args.args[1]["$set"]["indemnitors"][0]
    assert saved["id_scan"] == {"scan_id": "TEST-SCAN-1"}
    assert saved["portrait_jpeg_b64"] == "TEST-PORTRAIT-1"
    assert saved["id_scan_history"] == [{"id_scan": {"scan_id": "TEST-SCAN-0"}}]


@patch("dashboard.routers.indemnitors.get_db")
@patch("dashboard.routers.indemnitors.get_collection")
def test_bond_update_archives_the_prior_id_scan(mock_get_collection, mock_get_db, client):
    prospective = _collection(find_one=_bond_with_scan())
    active = _collection(find_one=None)
    mock_get_collection.side_effect = lambda name: {
        "prospective_bonds": prospective,
        "active_bonds": active,
        "indemnitors": _collection(),
    }[name]

    response = client.post(
        "/api/indemnitors/create",
        json={
            "firstName": "TEST",
            "lastName": "INDEMNITOR",
            "phone": "555-0100",
            "booking_number": "TEST-BK-1",
            "id_scan": {"scan_id": "TEST-SCAN-2"},
            "portrait_jpeg_b64": "TEST-PORTRAIT-2",
        },
    )

    assert response.status_code == 200
    saved = prospective.update_one.await_args.args[1]["$set"]["indemnitors"][0]
    assert saved["id_scan"] == {"scan_id": "TEST-SCAN-2"}
    assert saved["portrait_jpeg_b64"] == "TEST-PORTRAIT-2"
    assert saved["id_scan_history"] == [
        {"id_scan": {"scan_id": "TEST-SCAN-0"}},
        {"id_scan": {"scan_id": "TEST-SCAN-1"}, "portrait_jpeg_b64": "TEST-PORTRAIT-1"},
    ]


@patch("dashboard.routers.indemnitors.get_db")
@patch("dashboard.routers.indemnitors.get_collection")
def test_unlinked_update_keeps_id_scan_when_the_body_omits_it(mock_get_collection, mock_get_db, client):
    stored = {
        "_id": "mongo-test-1",
        "Indemnitor_ID": "TEST-IND-1",
        "phone": "555-0100",
        "status": "unlinked",
        "id_scan": {"scan_id": "TEST-SCAN-1"},
        "id_scan_history": [{"id_scan": {"scan_id": "TEST-SCAN-0"}}],
    }
    indemnitors = _collection(find_one=stored)
    mock_get_collection.side_effect = lambda name: {
        "prospective_bonds": _collection(),
        "active_bonds": _collection(),
        "indemnitors": indemnitors,
    }[name]

    response = client.post(
        "/api/indemnitors/create",
        json={"firstName": "TEST", "lastName": "INDEMNITOR", "phone": "555-0100"},
    )

    assert response.status_code == 200
    assert response.json()["action"] == "updated_existing"
    saved = indemnitors.update_one.await_args.args[1]["$set"]
    assert saved["id_scan"] == {"scan_id": "TEST-SCAN-1"}
    assert saved["id_scan_history"] == [{"id_scan": {"scan_id": "TEST-SCAN-0"}}]
    indemnitors.insert_one.assert_not_called()
