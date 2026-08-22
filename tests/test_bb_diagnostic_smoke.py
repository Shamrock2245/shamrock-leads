"""
Unit and regression tests for BlueBubbles diagnostic preflight and staff-approved smoke execution.
Tests unavailable_tunnel, invalid_service_auth, invalid_destination, provider_rejection, audit_write_failure, and success.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from dashboard.services.bb_diagnostic_service import (
    _mask_phone,
    _hash_token,
    check_bluebubbles_server_diagnostics,
    preflight_imessage_smoke,
    execute_staff_approved_imessage_smoke,
)


@pytest.fixture(autouse=True)
def mock_bb_env():
    with patch.dict(os.environ, {
        "BLUEBUBBLES_URL_0178": "http://100.102.10.86:1234",
        "BLUEBUBBLES_PASSWORD_0178": "test_pass_123",
        "BLUEBUBBLES_URL": "http://100.102.10.86:1234",
        "BLUEBUBBLES_PASSWORD": "test_pass_123",
    }):
        # Clear cached BB_SERVERS for tests
        import dashboard.extensions as ext
        ext.BB_SERVERS.clear()
        yield


def test_mask_phone_and_hash():
    assert _mask_phone("+12395550178") == "...0178"
    assert _mask_phone("2399550178") == "...0178"
    assert _mask_phone("") == "(none)"
    assert _hash_token("test-guid-12345") != "none"


# ── TEST 1: Invalid Destination Fails Closed ──────────────────────────────────

@pytest.mark.asyncio
async def test_preflight_invalid_destination():
    res = await preflight_imessage_smoke(recipient_phone="123")
    assert res["success"] is False
    assert res["state"] == "invalid_destination"
    assert res["error"] == "invalid_phone_format"


# ── TEST 2: Unavailable Tunnel Diagnostic ─────────────────────────────────────

@pytest.mark.asyncio
async def test_check_diagnostics_unavailable_tunnel():
    with patch("dashboard.routers.bb_private_api.httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
        mock_req.side_effect = Exception("Connection refused")
        res = await check_bluebubbles_server_diagnostics(candidate_urls=["http://100.102.10.86:1234"])
        assert res["success"] is False
        assert res["state"] == "unavailable_tunnel"


# ── TEST 3: Invalid Service Auth Diagnostic ───────────────────────────────────

@pytest.mark.asyncio
async def test_check_diagnostics_invalid_service_auth():
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json = MagicMock(return_value={"error": "Unauthorized"})

    with patch("dashboard.routers.bb_private_api.httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_resp
        res = await check_bluebubbles_server_diagnostics(candidate_urls=["http://100.102.10.86:1234"])
        assert res["success"] is False
        assert res["state"] == "invalid_service_auth"


# ── TEST 4: Healthy Server Diagnostics & Preflight ────────────────────────────

@pytest.mark.asyncio
async def test_preflight_healthy_server():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value={
        "data": {
            "server_version": "1.9.9",
            "private_api": True,
            "os_version": "macOS 14.4.1",
        }
    })

    with patch("dashboard.routers.bb_private_api.httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_resp
        res = await preflight_imessage_smoke(recipient_phone="2395550100")
        assert res["success"] is True
        assert res["state"] == "eligible_for_staff_approval"
        assert res["server_version"] == "1.9.9"
        assert res["private_api_connected"] is True
        assert res["recipient_masked"] == "...0100"


# ── TEST 5: Smoke Execution Success & Non-PII Audit ───────────────────────────

@pytest.mark.asyncio
async def test_execute_smoke_success_and_audit():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value={
        "data": {
            "guid": "BB-GUID-TEST-12345",
            "server_version": "1.9.9",
            "private_api": True,
        }
    })

    mock_audit_col = MagicMock()
    mock_audit_col.insert_one = AsyncMock(return_value=MagicMock(inserted_id="mock_id"))

    with patch("dashboard.routers.bb_private_api.httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_resp
        with patch("dashboard.services.bb_diagnostic_service.get_collection", return_value=mock_audit_col):
            res = await execute_staff_approved_imessage_smoke(
                recipient_phone="2395550199",
                staff_actor="admin@shamrockbailbonds.biz",
                confirmed=True,
                correlation_id="bb_smoke_test_01",
            )
            assert res["success"] is True
            assert res["state"] == "forwarded"
            assert res["recipient_masked"] == "...0199"
            assert "2395550199" not in str(res)  # Non-PII check
            mock_audit_col.insert_one.assert_called_once()


# ── TEST 6: Smoke Execution Provider Rejection ────────────────────────────────

@pytest.mark.asyncio
async def test_execute_smoke_provider_rejection():
    async def _mock_request(method, url, **kwargs):
        mock_r = MagicMock()
        if "server/info" in str(url):
            mock_r.status_code = 200
            mock_r.json = MagicMock(return_value={"data": {"server_version": "1.9.9", "private_api": True}})
            return mock_r
        else:
            mock_r.status_code = 500
            mock_r.json = MagicMock(return_value={"error": "Failed to send message: not an iMessage address"})
            return mock_r

    with patch("dashboard.routers.bb_private_api.httpx.AsyncClient.request", side_effect=_mock_request):
        res = await execute_staff_approved_imessage_smoke(
            recipient_phone="2395550199",
            staff_actor="admin@shamrockbailbonds.biz",
            confirmed=True,
        )
        assert res["success"] is False
        assert res["state"] == "provider_rejection"
        assert "not an iMessage address" in res["error"] or "500" in res["error"]



# ── TEST 7: Audit Write Failure Fails Closed ──────────────────────────────────

@pytest.mark.asyncio
async def test_execute_smoke_audit_write_failure():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value={"data": {"guid": "BB-GUID-123", "server_version": "1.9.9"}})

    mock_audit_col = MagicMock()
    mock_audit_col.insert_one = AsyncMock(side_effect=Exception("MongoDB disk full"))

    with patch("dashboard.routers.bb_private_api.httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_resp
        with patch("dashboard.services.bb_diagnostic_service.get_collection", return_value=mock_audit_col):
            res = await execute_staff_approved_imessage_smoke(
                recipient_phone="2395550199",
                staff_actor="admin@shamrockbailbonds.biz",
                confirmed=True,
            )
            assert res["success"] is False
            assert res["state"] == "audit_write_failure"
