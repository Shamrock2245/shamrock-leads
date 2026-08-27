"""
Tests for Mobile PIN Portal Router (paperwork.shamrockbailbonds.biz)
"""
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from dashboard.main import app
from dashboard.routers.pin_portal import pin_portal_router, _extract_signing_link_from_packet
from dashboard.services.bb_client import normalize_bb_send_result, bb_send_accepted

app.include_router(pin_portal_router)
client = TestClient(app)


def test_public_sign_redirect_prefers_role():
    mock_packets = MagicMock()
    mock_packets.find_one = AsyncMock(return_value={
        "packet_id": "PKT-TEST1",
        "voided": False,
        "status": "pending_signature",
        "defendant_name": "DOE, JOHN",
        "docuseal_submitters": [
            {"role": "indemnitor", "slug": "ind-aaa", "status": "pending"},
            {"role": "Defendant", "slug": "def-bbb", "status": "pending"},
        ],
    })
    with patch("dashboard.routers.pin_portal.get_collection", return_value=mock_packets):
        response = client.get("/sign/PKT-TEST1/defendant", follow_redirects=False)
        raw = client.get("/sign/PKT-TEST1/defendant?raw=1", follow_redirects=False)
    assert response.status_code == 200
    assert "docuseal-form" in response.text
    assert "data-host" in response.text
    assert "/s/def-bbb" in response.text
    assert "You are signing as the defendant" in response.text
    assert raw.status_code == 302
    assert raw.headers["location"].endswith("/s/def-bbb")


def test_extract_signing_link_prefers_indemnitor():
    from dashboard.routers.pin_portal import _extract_signing_link_from_packet
    link = _extract_signing_link_from_packet({
        "packet_id": "PKT-X",
        "docuseal_submitters": [
            {"role": "indemnitor", "sign_url": "https://sign.shamrockbailbonds.biz/s/ind"},
            {"role": "Defendant", "sign_url": "https://sign.shamrockbailbonds.biz/s/def"},
        ],
    })
    assert link.endswith("/s/ind")


def test_portal_ui_route():
    response = client.get("/api/portal/portal-ui", follow_redirects=True)
    assert response.status_code == 200
    assert "Shamrock Bail Bonds" in response.text
    assert "Official E-Sign Paperwork Portal" in response.text


def test_done_landing_page():
    response = client.get("/api/portal/done", follow_redirects=True)
    assert response.status_code == 200
    assert "Paperwork Successfully Signed!" in response.text
    assert "Call Office" in response.text


def test_verify_admin_pin_bypass():
    """Staff smoke bypass only when PORTAL_STAFF_MASTER_PIN env is set."""
    mock_packets = MagicMock()
    mock_packets.find_one = AsyncMock(return_value={
        "signing_link": "https://sign.shamrockbailbonds.biz/s/abc123",
        "created_at": "2026-08-07",
    })
    mock_packets.update_one = AsyncMock()
    with patch("dashboard.routers.pin_portal.get_collection", return_value=mock_packets), \
         patch("dashboard.routers.pin_portal._MASTER_PIN", "999888"):
        payload = {"phone": "2395550199", "pin": "999888"}
        response = client.post("/api/portal/verify-pin", json=payload, follow_redirects=True)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["verified"] is True
    assert "PORTAL-ADMIN" in data["session_token"]
    assert data["signing_link"] == "https://sign.shamrockbailbonds.biz/s/abc123"


def test_hardcoded_master_pin_disabled():
    """Legacy hardcoded 224545 must not bypass portal auth when env pin unset."""
    mock_pins = MagicMock()
    mock_pins.find_one = AsyncMock(return_value=None)
    with patch("dashboard.routers.pin_portal.get_collection", return_value=mock_pins), \
         patch("dashboard.routers.pin_portal._MASTER_PIN", ""):
        response = client.post(
            "/api/portal/verify-pin",
            json={"phone": "2395550199", "pin": "224545"},
            follow_redirects=True,
        )
    assert response.status_code == 401


def test_send_pin_via_bluebubbles_mock():
    mock_pins_col = MagicMock()
    mock_pins_col.update_one = AsyncMock()

    with patch("dashboard.routers.pin_portal.get_collection", return_value=mock_pins_col), \
         patch("dashboard.services.bb_client.send_message_universal", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"success": True, "sent": True, "queued": False, "channel": "imessage"}

        payload = {"phone": "2395550199"}
        response = client.post("/api/portal/send-pin", json=payload, follow_redirects=True)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["channel"] == "imessage"
        assert mock_send.called


def test_send_pin_accepts_queued_bb_shape():
    """Regression: live BB returned status=queued without sent/queued booleans → false 503."""
    mock_pins_col = MagicMock()
    mock_pins_col.update_one = AsyncMock()

    with patch("dashboard.routers.pin_portal.get_collection", return_value=mock_pins_col), \
         patch("dashboard.services.bb_client.send_message_universal", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {
            "success": True,
            "status": "queued",
            "channel": "queued",
            "queued_id": "abc",
            "error": None,
        }
        response = client.post(
            "/api/portal/send-pin",
            json={"phone": "2395550100"},
            follow_redirects=True,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["success"] is True
        assert data["queued"] is True
        assert data["channel"] == "queued"


def test_normalize_bb_send_result_shapes():
    n = normalize_bb_send_result({
        "success": True, "status": "queued", "channel": "queued", "error": None,
    })
    assert n["queued"] is True
    assert n["sent"] is False
    assert bb_send_accepted(n) is True

    n2 = normalize_bb_send_result({"success": True, "channel": "imessage"})
    assert n2["sent"] is True
    assert bb_send_accepted(n2) is True

    n3 = normalize_bb_send_result({"success": False, "error": "boom"})
    assert bb_send_accepted(n3) is False


def test_extract_signing_link_from_submitters():
    link = _extract_signing_link_from_packet({
        "docuseal_submitters": [{"embed_src": "https://sign.example/s/xyz"}],
    })
    assert link == "https://sign.example/s/xyz"


def test_no_twilio_imports_in_pin_portal():
    with open("dashboard/routers/pin_portal.py", "r") as f:
        content = f.read()
    assert "twilio_service" not in content
    assert "TwilioService" not in content
    assert "OutreachSequencer" not in content
    assert "send_text" not in content


def test_sanitize_client_fields_drops_staff_keys():
    from dashboard.routers.pin_portal import _sanitize_client_fields
    clean = _sanitize_client_fields({
        "indemnitor_employer": "Publix",
        "poa_number": "OSI-P3-116-26-0016",
        "numeric_premium": "1500",
        "defendant_name": "DOE, JOHN",
        "indemnitor_relationship": "Mother",
        "bond_amount": "5000",
        "charges": "Grand Theft",
    })
    assert clean == {
        "indemnitor_employer": "Publix",
        "defendant_name": "DOE, JOHN",
        "indemnitor_relationship": "Mother",
    }


def test_portal_session_requires_token():
    response = client.get("/api/portal/session")
    assert response.status_code == 401


def test_portal_session_returns_packet():
    mock_pins = MagicMock()
    mock_pins.find_one = AsyncMock(return_value={
        "phone": "2395550100",
        "session_token": "PORTAL-abc",
        "verified": True,
        "expires_at": "2099-01-01T00:00:00+00:00",
        "id_extracted": {"full_name": "Jane Doe", "city": "Fort Myers", "state": "FL", "zip": "33901"},
    })
    mock_packets = MagicMock()
    mock_packets.find_one = AsyncMock(return_value={
        "packet_id": "PKT-1",
        "defendant_name": "DOE, JOHN",
        "status": "pending_signature",
        "docuseal_submitters": [
            {"role": "indemnitor", "sign_url": "https://sign.shamrockbailbonds.biz/s/ind"},
        ],
    })

    def _col(name):
        return mock_pins if name == "portal_pins" else mock_packets

    with patch("dashboard.routers.pin_portal.get_collection", side_effect=_col):
        response = client.get("/api/portal/session?token=PORTAL-abc")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["has_packet"] is True
    assert data["signing_link"].endswith("/s/ind")
    assert data["extracted"]["full_name"] == "Jane Doe"


def test_remaining_fields_requires_session():
    mock_pins = MagicMock()
    mock_pins.find_one = AsyncMock(return_value=None)
    with patch("dashboard.routers.pin_portal.get_collection", return_value=mock_pins):
        response = client.post(
            "/api/portal/remaining-fields",
            json={"session_token": "missing", "fields": {"indemnitor_employer": "Publix"}},
        )
    assert response.status_code == 401


def test_remaining_fields_pushes_allowlisted_values():
    mock_pins = MagicMock()
    mock_pins.find_one = AsyncMock(return_value={
        "phone": "2395550100",
        "session_token": "PORTAL-abc",
        "verified": True,
        "role": "indemnitor",
        "expires_at": "2099-01-01T00:00:00+00:00",
    })
    mock_pins.update_one = AsyncMock()
    mock_packets = MagicMock()
    mock_packets.find_one = AsyncMock(return_value={
        "packet_id": "PKT-1",
        "defendant_name": "DOE, JOHN",
        "status": "pending_signature",
        "docuseal_submitters": [
            {"id": 44, "role": "indemnitor", "sign_url": "https://sign.shamrockbailbonds.biz/s/ind"},
        ],
    })
    mock_packets.update_one = AsyncMock()

    def _col(name):
        return mock_pins if name == "portal_pins" else mock_packets

    mock_svc = MagicMock()
    mock_svc.update_submitter = AsyncMock(return_value={"id": 44})

    with patch("dashboard.routers.pin_portal.get_collection", side_effect=_col), \
         patch("dashboard.services.docuseal_service.DocuSealService", return_value=mock_svc):
        response = client.post("/api/portal/remaining-fields", json={
            "session_token": "PORTAL-abc",
            "role": "indemnitor",
            "staff_review_acknowledged": True,
            "address_confirmed": True,
            "fields": {
                "indemnitor_employer": "Publix",
                "indemnitor_city": "Fort Myers",
                "indemnitor_state": "FL",
                "indemnitor_zip": "33901",
                "poa_number": "SHOULD-DROP",
            },
        })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["pushed_to_docuseal"] is True
    assert data["field_count"] >= 3
    mock_svc.update_submitter.assert_awaited()
    sent_values = mock_svc.update_submitter.await_args.kwargs["values"]
    assert "poa_number" not in sent_values
    assert sent_values["indemnitor_employer"] == "Publix"
    assert "Fort Myers" in sent_values["indemnitor_city_state_zip"]


def test_remaining_fields_targets_defendant_submitter():
    mock_pins = MagicMock()
    mock_pins.find_one = AsyncMock(return_value={
        "phone": "2395550199",
        "session_token": "PORTAL-def",
        "verified": True,
        "role": "defendant",
        "expires_at": "2099-01-01T00:00:00+00:00",
    })
    mock_pins.update_one = AsyncMock()
    mock_packets = MagicMock()
    mock_packets.find_one = AsyncMock(return_value={
        "packet_id": "PKT-2",
        "defendant_name": "DOE, JOHN",
        "status": "pending_signature",
        "docuseal_submitters": [
            {"id": 10, "role": "indemnitor", "sign_url": "https://sign.shamrockbailbonds.biz/s/ind"},
            {"id": 11, "role": "Defendant", "sign_url": "https://sign.shamrockbailbonds.biz/s/def"},
        ],
    })
    mock_packets.update_one = AsyncMock()

    def _col(name):
        return mock_pins if name == "portal_pins" else mock_packets

    mock_svc = MagicMock()
    mock_svc.update_submitter = AsyncMock(return_value={"id": 11})

    with patch("dashboard.routers.pin_portal.get_collection", side_effect=_col), \
         patch("dashboard.services.docuseal_service.DocuSealService", return_value=mock_svc):
        response = client.post("/api/portal/remaining-fields", json={
            "session_token": "PORTAL-def",
            "role": "defendant",
            "staff_review_acknowledged": True,
            "address_confirmed": True,
            "fields": {"defendant_city": "Fort Myers", "defendant_dl": "D1234567"},
        })
    assert response.status_code == 200, response.text
    assert mock_svc.update_submitter.await_args.args[0] == 11
    sent = mock_svc.update_submitter.await_args.kwargs["values"]
    assert sent["defendant_city"] == "Fort Myers"
    assert sent["defendant_dl"] == "D1234567"


def test_id_ocr_maps_indemnitor_not_defendant():
    from dashboard.routers.pin_portal import client_fields_from_id_ocr
    fields = client_fields_from_id_ocr(
        {
            "full_name": "Mary Smith",
            "first_name": "Mary",
            "last_name": "Smith",
            "address": "12 Pine St",
            "city": "Fort Myers",
            "state": "FL",
            "zip": "33901",
            "dob": "1988-02-02",
            "dl_number": "S1234567",
        },
        "indemnitor",
    )
    assert fields["indemnitor_name"] == "Mary Smith"
    assert fields["indemnitor_dl"] == "S1234567"
    assert "defendant_name" not in fields
    assert "poa_number" not in fields


def test_id_ocr_maps_apostrophe_indemnitor_name():
    from dashboard.routers.pin_portal import client_fields_from_id_ocr
    fields = client_fields_from_id_ocr(
        {"full_name": "BRENDAN JOHN O’NEILL", "first_name": "BRENDAN", "last_name": "O’NEILL", "dl_number": "O123"},
        "indemnitor",
    )
    assert fields["indemnitor_name"] == "Brendan John O'Neill"
    assert "defendant_name" not in fields


def test_id_ocr_defendant_role_does_not_overwrite_indemnitor():
    from dashboard.routers.pin_portal import client_fields_from_id_ocr
    fields = client_fields_from_id_ocr(
        {"full_name": "John Inmate", "dl_number": "D999", "address": "Jail"},
        "defendant",
    )
    assert fields["defendant_name"] == "John Inmate"
    assert fields["defendant_dl"] == "D999"
    assert "indemnitor_name" not in fields
