"""Regression coverage for the retired unassigned-defendant packet route."""
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


@patch("dashboard.services.docuseal_service.resolve_template_id_for_surety", return_value=1)
@patch("dashboard.services.docuseal_service.get_docuseal_service")
@patch("dashboard.deps.get_collection")
@patch("dashboard.extensions.get_collection")
@patch("dashboard.routers.paperwork.get_collection")
@patch("dashboard.routers.pin_portal.get_collection")
def test_instant_indemnitor_packet_fails_closed_without_validated_case(
    mock_pin_col, mock_pw_col, mock_ext_col, mock_deps_col, mock_get_docuseal, mock_resolve_tmpl, test_app
):
    """An ID scan alone must never create a legal e-sign packet."""
    mock_packets = AsyncMock()
    mock_packets.insert_one = AsyncMock(return_value=MagicMock(inserted_id="test_id"))
    mock_packets.find_one = AsyncMock(return_value={"packet_id": "pkt_inst_123456", "defendant_name": "To Be Named"})
    mock_packets.update_one = AsyncMock(return_value=MagicMock(matched_count=1, modified_count=1))
    mock_packets.count_documents = AsyncMock(return_value=0)

    mock_pin_col.return_value = mock_packets
    mock_pw_col.return_value = mock_packets
    mock_ext_col.return_value = mock_packets
    mock_deps_col.return_value = mock_packets

    client = TestClient(test_app)

    # Mock DocuSeal service — real API often returns a LIST of submitters
    mock_inst = MagicMock()
    mock_inst.is_configured = True
    mock_inst.public_url = "https://sign.shamrockbailbonds.biz"
    mock_inst.sign_url_for_slug = lambda slug: (
        f"https://sign.shamrockbailbonds.biz/s/{slug}" if slug else ""
    )

    async def mock_create_sub(**kwargs):
        # List shape (most common DocuSeal create-submission response)
        return [
            {
                "id": 1234,
                "submission_id": 998811,
                "role": "indemnitor",
                "email": (kwargs.get("submitters") or [{}])[0].get("email"),
                "slug": "test_slug_1234",
                "embed_src": "https://sign.shamrockbailbonds.biz/s/test_slug_1234",
                "status": "sent",
            }
        ]

    # Use real normalizers so list responses are covered
    from dashboard.services.docuseal_service import DocuSealService

    real = DocuSealService(base_url="https://sign.shamrockbailbonds.biz", api_key="test")
    mock_inst.create_submission = mock_create_sub
    mock_inst.build_submitter = DocuSealService.build_submitter
    mock_inst.normalize_create_response = real.normalize_create_response
    mock_inst.normalize_submitter_record = real.normalize_submitter_record
    mock_get_docuseal.return_value = mock_inst

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
    assert resp.status_code == 409, resp.text
    data = resp.json()
    assert data == {
        "success": False,
        "error": "validated_bond_case_required",
        "message": (
            "Paperwork is not ready yet. A Shamrock bondsman must validate "
            "the match and bond case before creating your signing packet."
        ),
        "next_step": "request_pin_after_staff_creates_packet",
    }
    mock_packets.insert_one.assert_not_awaited()
