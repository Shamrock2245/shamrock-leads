"""
Test suite verifying instant indemnitor paperwork signing & post-sign defendant binding workflow.
Allows indemnitors to scan ID, complete paperwork first, and bind defendant later.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.routers.pin_portal import pin_portal_router
from dashboard.routers.paperwork import paperwork_bp


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(pin_portal_router)
    app.include_router(paperwork_bp)
    return app


@patch("dashboard.deps.get_collection")
@patch("dashboard.extensions.get_collection")
@patch("dashboard.routers.paperwork.get_collection")
@patch("dashboard.routers.pin_portal.get_collection")
def test_instant_indemnitor_packet_and_bind_workflow(
    mock_pin_col, mock_pw_col, mock_ext_col, mock_deps_col, mocker, test_app
):
    """
    Test end-to-end instant indemnitor packet creation without a defendant,
    and subsequent binding of defendant details post-creation/sign.
    """
    mock_packets = AsyncMock()
    mock_packets.insert_one = AsyncMock(return_value=MagicMock(inserted_id="test_id"))
    mock_packets.find_one = AsyncMock(return_value={"packet_id": "pkt_inst_123456", "defendant_name": "To Be Named"})
    mock_packets.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    
    mock_pin_col.return_value = mock_packets
    mock_pw_col.return_value = mock_packets
    mock_ext_col.return_value = mock_packets
    mock_deps_col.return_value = mock_packets

    client = TestClient(test_app)

    # Mock DocuSeal service to return clean submission URL
    mock_svc = mocker.patch("dashboard.services.docuseal_service.get_docuseal_service")
    mock_inst = mocker.MagicMock()
    mock_inst.is_configured = True

    async def mock_create_sub(template_id, submitters):
        return {
            "id": 998811,
            "submitters": [
                {
                    "id": 1234,
                    "role": "indemnitor",
                    "email": submitters[0]["email"],
                    "slug": "test_slug_1234",
                    "embed_src": "https://sign.shamrockbailbonds.biz/s/test_slug_1234",
                }
            ],
        }

    mock_inst.create_submission = mock_create_sub
    mock_inst.build_submitter.return_value = {
        "role": "indemnitor",
        "email": "test@shamrockbailbonds.biz",
        "phone": "+12395550199",
        "values": {"indemnitor_name": "Jane Doe", "defendant_name": "To Be Named"},
    }
    mock_inst.normalize_submitter_record.return_value = {
        "role": "indemnitor",
        "sign_url": "https://sign.shamrockbailbonds.biz/s/test_slug_1234",
    }
    mocker.patch("dashboard.services.docuseal_service.resolve_template_id_for_surety", return_value=1)
    mock_svc.return_value = mock_inst

    # Step 1: Create instant indemnitor packet after ID scan (no defendant attached)
    resp = client.post(
        "/api/portal/instant-indemnitor-packet",
        json={
            "indemnitor_name": "Jane Doe",
            "indemnitor_phone": "2395550199",
            "indemnitor_address": "123 Main St, Ft Myers FL",
            "indemnitor_dl": "D123456789",
            "surety_id": "osi",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["unassigned_defendant"] is True
    assert "sign_url" in data
    packet_id = data["packet_id"]

    # Step 2: Post-Sign Defendant Binding (Bind defendant to existing packet)
    bind_resp = client.post(
        f"/api/paperwork/packets/{packet_id}/bind-defendant",
        json={
            "defendant_name": "John Doe",
            "booking_number": "2026-998877",
            "county": "Lee",
            "case_number": "26-CF-009988",
        },
    )
    assert bind_resp.status_code == 200
    bind_data = bind_resp.json()
    assert bind_data["success"] is True
    assert bind_data["defendant_name"] == "John Doe"
