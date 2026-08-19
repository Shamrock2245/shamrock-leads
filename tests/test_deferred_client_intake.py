from unittest.mock import AsyncMock, patch

import pytest

from dashboard.routers.pin_portal import (
    _normalize_client_role,
    _upsert_deferred_client_intake,
)


class _Collection:
    def __init__(self):
        self.update_one = AsyncMock()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("defendant", "defendant"),
        ("co-indemnitor", "coindemnitor"),
        ("co-signer", "indemnitor"),
        ("indemnitor", "indemnitor"),
        ("unknown", ""),
    ],
)
def test_normalize_client_role(raw, expected):
    assert _normalize_client_role(raw) == expected


@pytest.mark.asyncio
async def test_unassigned_indemnitor_intake_keeps_case_fields_blank_and_defers_match():
    intakes = _Collection()
    pins = _Collection()

    def collection_for(name):
        return {"intake_queue": intakes, "portal_pins": pins}[name]

    session = {
        "session_token": "PORTAL-test",
        "phone": "2395550101",
        "id_scanned_at": "2026-08-19T12:00:00+00:00",
        "id_extracted": {
            "full_name": "Jamie Client",
            "dob": "01/02/1990",
            "dl_number": "D1234567",
            "address": "1 Main Street",
            "city": "Fort Myers",
            "state": "FL",
            "zip": "33901",
        },
    }
    fields = {
        "indemnitor_name": "Jamie Client",
        "indemnitor_relationship": "Friend",
        "indemnitor_employer": "Acme",
    }

    with patch("dashboard.routers.pin_portal.get_collection", side_effect=collection_for):
        intake_id = await _upsert_deferred_client_intake(
            session=session,
            role="indemnitor",
            fields=fields,
            staff_review_acknowledged=True,
        )

    assert intake_id.startswith("WX-")
    saved = intakes.update_one.await_args.args[1]["$set"]
    assert saved["role"] == "indemnitor"
    assert saved["status"] == "pending"
    assert saved["match_strategy"] == "staff_deferred"
    assert saved["paperwork_packet_id"] is None
    assert saved["paperwork_status"] == "intake_complete"
    assert saved["defendant_name"] == ""
    assert saved["defendant"]["bondAmount"] == ""
    assert saved["indemnitor_name"] == "Jamie Client"
    assert saved["indemnitor"]["dl"] == "D1234567"
    pins.update_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_defendant_intake_uses_defendant_fields_without_making_a_bond_match():
    intakes = _Collection()
    pins = _Collection()

    def collection_for(name):
        return {"intake_queue": intakes, "portal_pins": pins}[name]

    session = {
        "session_token": "PORTAL-test",
        "phone": "2395550102",
        "id_extracted": {
            "full_name": "Taylor Defendant",
            "dob": "02/03/1991",
            "dl_number": "D7654321",
            "address": "2 Oak Avenue",
            "city": "Cape Coral",
            "state": "FL",
            "zip": "33990",
        },
    }

    with patch("dashboard.routers.pin_portal.get_collection", side_effect=collection_for):
        await _upsert_deferred_client_intake(
            session=session,
            role="defendant",
            fields={"defendant_name": "Taylor Defendant"},
            staff_review_acknowledged=True,
        )

    saved = intakes.update_one.await_args.args[1]["$set"]
    assert saved["role"] == "defendant"
    assert saved["defendant_name"] == "Taylor Defendant"
    assert saved["defendant"]["dl"] == "D7654321"
    assert saved["indemnitor_name"] == ""
    assert saved["matched_booking_number"] is None
    assert saved["match_strategy"] == "staff_deferred"


class _Request:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class _UpdateResult:
    matched_count = 1


@pytest.mark.asyncio
async def test_staff_can_attach_an_independent_indemnitor_without_overwriting_existing_cosigners():
    from dashboard.routers.match_manager import api_attach_client_intake_to_bond

    intakes = _Collection()
    intakes.find_one = AsyncMock(return_value={
        "intake_id": "WX-INDEMNITOR-1",
        "role": "indemnitor",
        "indemnitor_name": "Jordan Client",
        "indemnitor": {
            "firstName": "Jordan",
            "lastName": "Client",
            "phone": "2395550103",
            "relationship": "Parent",
        },
    })
    intakes.update_one = AsyncMock()
    bonds = _Collection()
    bonds.find_one = AsyncMock(return_value={
        "booking_number": "LEE-123",
        "county": "Lee",
        "indemnitor": {"name": "First Co-signer", "source_intake_id": "WX-INDEMNITOR-0"},
        "indemnitors": [{"name": "Second Co-signer", "source_intake_id": "WX-INDEMNITOR-00"}],
    })
    bonds.update_one = AsyncMock(return_value=_UpdateResult())
    audits = _Collection()
    audits.insert_one = AsyncMock()

    def collection_for(name):
        return {
            "intake_queue": intakes,
            "active_bonds": bonds,
            "audit_events": audits,
        }[name]

    request = _Request({"booking_number": "LEE-123", "agent": "Test Agent"})
    with patch("dashboard.routers.match_manager.get_collection", side_effect=collection_for):
        response = await api_attach_client_intake_to_bond(request, "WX-INDEMNITOR-1")

    assert response["success"] is True
    assert response["indemnitor"]["name"] == "Jordan Client"
    update = bonds.update_one.await_args.args[1]
    assert update["$addToSet"]["client_intake_ids"] == "WX-INDEMNITOR-1"
    assert update["$addToSet"]["indemnitors"]["source_intake_id"] == "WX-INDEMNITOR-1"
    assert "paperwork" not in str(update).lower()
    intake_update = intakes.update_one.await_args.args[1]["$set"]
    assert intake_update["match_strategy"] == "staff_attached"
    assert intake_update["matched_booking_number"] == "LEE-123"
