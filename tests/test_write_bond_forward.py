"""
Unit and integration tests for staff-approved write-bond forwarding to central GAS factory.
Tests fail-closed behavior on missing match, case number, surety, POA, GAS config, and duplicate correlation ID.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from dashboard.services.write_bond_forward_service import (
    generate_correlation_id,
    preflight_write_bond_forward,
    execute_staff_approved_write_bond_forward,
)


@pytest.fixture
def mock_db_collections():
    """Mock MongoDB collections for testing write-bond forwarding."""
    active_bonds_col = MagicMock()
    bond_cases_col = MagicMock()
    matches_col = MagicMock()
    defendants_col = MagicMock()
    indemnitors_col = MagicMock()
    poa_col = MagicMock()
    gas_event_col = MagicMock()
    audit_col = MagicMock()

    cols = {
        "active_bonds": active_bonds_col,
        "bond_cases": bond_cases_col,
        "matches": matches_col,
        "defendants": defendants_col,
        "indemnitors": indemnitors_col,
        "poa_inventory": poa_col,
        "gas_event_log": gas_event_col,
        "audit_events": audit_col,
    }

    # Setup defaults: no duplicate correlation ID, insert succeed
    gas_event_col.find_one = AsyncMock(return_value=None)
    gas_event_col.insert_one = AsyncMock(return_value=MagicMock(inserted_id="mock_id"))
    audit_col.insert_one = AsyncMock(return_value=MagicMock(inserted_id="mock_audit_id"))
    bond_cases_col.find_one = AsyncMock(return_value=None)

    def _get_collection(name: str):
        return cols.get(name, MagicMock())

    with patch("dashboard.services.write_bond_forward_service.get_collection", side_effect=_get_collection):
        yield {
            "active_bonds": active_bonds_col,
            "bond_cases": bond_cases_col,
            "matches": matches_col,
            "defendants": defendants_col,
            "indemnitors": indemnitors_col,
            "poa_inventory": poa_col,
            "gas_event_log": gas_event_col,
            "audit_events": audit_col,
        }


@pytest.fixture
def valid_bond_env():
    """Valid environment variables for GAS integration."""
    with patch.dict(os.environ, {
        "GAS_WEB_APP_URL": "https://script.google.com/macros/s/AKfycbyCIDPzA_EA1B1SGsfhYiXRGKM8z61EgACZdDPILT_MjjXee0wSDEI0RRYthE0CvP-Z/exec",
        "GAS_API_KEY": "test_gas_api_key_12345",
    }):
        yield


@pytest.fixture
def valid_records():
    """Valid set of authoritative records matching the required chain."""
    return {
        "bond": {
            "Bond_Case_ID": "BOND-TEST-001",
            "Booking_Number": "2026-009999",
            "Case_Number": "26-CF-009999",
            "Surety_ID": "osi",
            "POA_Number": "OSI3 20134296",
            "Bond_Amount": 5000.0,
            "Defendant_ID": "DEF-TEST-001",
            "Indemnitor_ID": "IND-TEST-001",
            "Match_ID": "MATCH-TEST-001",
        },
        "match": {
            "Match_ID": "MATCH-TEST-001",
            "Defendant_ID": "DEF-TEST-001",
            "Indemnitor_ID": "IND-TEST-001",
            "Status": "validated",
            "Confidence": 95,
        },
        "defendant": {
            "Defendant_ID": "DEF-TEST-001",
            "Full_Name": "Redacted Defendant",
        },
        "indemnitor": {
            "Indemnitor_ID": "IND-TEST-001",
            "Full_Name": "Redacted Indemnitor",
        },
        "poa": {
            "poa_number": "OSI3 20134296",
            "surety_id": "osi",
            "status": "assigned",
            "max_bond_value": 10000.0,
        },
    }


# ── TEST 1: Missing Bond Case ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preflight_missing_bond_case(mock_db_collections, valid_bond_env):
    mock_db_collections["active_bonds"].find_one = AsyncMock(return_value=None)
    mock_db_collections["bond_cases"].find_one = AsyncMock(return_value=None)

    res = await preflight_write_bond_forward(bond_case_id="NON_EXISTENT")
    assert res["success"] is False
    assert res["state"] == "blocked"
    assert "missing_bond_case" in res["block_reasons"]


# ── TEST 2: Missing Match Record ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preflight_missing_match(mock_db_collections, valid_bond_env, valid_records):
    mock_db_collections["active_bonds"].find_one = AsyncMock(return_value=valid_records["bond"])
    mock_db_collections["matches"].find_one = AsyncMock(return_value=None)
    mock_db_collections["defendants"].find_one = AsyncMock(return_value=valid_records["defendant"])
    mock_db_collections["indemnitors"].find_one = AsyncMock(return_value=valid_records["indemnitor"])
    mock_db_collections["poa_inventory"].find_one = AsyncMock(return_value=valid_records["poa"])

    res = await preflight_write_bond_forward(bond_case_id="BOND-TEST-001")
    assert res["success"] is False
    assert res["state"] == "blocked"
    assert "missing_match" in res["block_reasons"]


# ── TEST 3: Unvalidated Match Status ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_preflight_unvalidated_match_status(mock_db_collections, valid_bond_env, valid_records):
    unvalidated_match = dict(valid_records["match"], Status="pending")
    mock_db_collections["active_bonds"].find_one = AsyncMock(return_value=valid_records["bond"])
    mock_db_collections["matches"].find_one = AsyncMock(return_value=unvalidated_match)
    mock_db_collections["defendants"].find_one = AsyncMock(return_value=valid_records["defendant"])
    mock_db_collections["indemnitors"].find_one = AsyncMock(return_value=valid_records["indemnitor"])
    mock_db_collections["poa_inventory"].find_one = AsyncMock(return_value=valid_records["poa"])

    res = await preflight_write_bond_forward(bond_case_id="BOND-TEST-001")
    assert res["success"] is False
    assert res["state"] == "blocked"
    assert any("unvalidated_match_status" in r for r in res["block_reasons"])


# ── TEST 4: Missing Case Number ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preflight_missing_case_number(mock_db_collections, valid_bond_env, valid_records):
    bond_no_case = dict(valid_records["bond"], Case_Number="")
    mock_db_collections["active_bonds"].find_one = AsyncMock(return_value=bond_no_case)
    mock_db_collections["matches"].find_one = AsyncMock(return_value=valid_records["match"])
    mock_db_collections["defendants"].find_one = AsyncMock(return_value=valid_records["defendant"])
    mock_db_collections["indemnitors"].find_one = AsyncMock(return_value=valid_records["indemnitor"])
    mock_db_collections["poa_inventory"].find_one = AsyncMock(return_value=valid_records["poa"])

    res = await preflight_write_bond_forward(bond_case_id="BOND-TEST-001")
    assert res["success"] is False
    assert res["state"] == "blocked"
    assert "missing_case_number" in res["block_reasons"]


# ── TEST 5: Missing Surety ID ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preflight_missing_surety_id(mock_db_collections, valid_bond_env, valid_records):
    bond_no_surety = dict(valid_records["bond"], Surety_ID="")
    mock_db_collections["active_bonds"].find_one = AsyncMock(return_value=bond_no_surety)
    mock_db_collections["matches"].find_one = AsyncMock(return_value=valid_records["match"])
    mock_db_collections["defendants"].find_one = AsyncMock(return_value=valid_records["defendant"])
    mock_db_collections["indemnitors"].find_one = AsyncMock(return_value=valid_records["indemnitor"])
    mock_db_collections["poa_inventory"].find_one = AsyncMock(return_value=valid_records["poa"])

    res = await preflight_write_bond_forward(bond_case_id="BOND-TEST-001")
    assert res["success"] is False
    assert res["state"] == "blocked"
    assert "missing_surety_id" in res["block_reasons"]


# ── TEST 6: Missing / Unassigned POA in Inventory ─────────────────────────────

@pytest.mark.asyncio
async def test_preflight_poa_not_in_inventory(mock_db_collections, valid_bond_env, valid_records):
    mock_db_collections["active_bonds"].find_one = AsyncMock(return_value=valid_records["bond"])
    mock_db_collections["matches"].find_one = AsyncMock(return_value=valid_records["match"])
    mock_db_collections["defendants"].find_one = AsyncMock(return_value=valid_records["defendant"])
    mock_db_collections["indemnitors"].find_one = AsyncMock(return_value=valid_records["indemnitor"])
    mock_db_collections["poa_inventory"].find_one = AsyncMock(return_value=None)

    res = await preflight_write_bond_forward(bond_case_id="BOND-TEST-001")
    assert res["success"] is False
    assert res["state"] == "blocked"
    assert "poa_not_assigned_in_inventory" in res["block_reasons"]


# ── TEST 7: Insufficient POA Tier Limit ───────────────────────────────────────

@pytest.mark.asyncio
async def test_preflight_poa_tier_insufficient(mock_db_collections, valid_bond_env, valid_records):
    low_poa = dict(valid_records["poa"], max_bond_value=2500.0)  # Bond is 5000.0
    mock_db_collections["active_bonds"].find_one = AsyncMock(return_value=valid_records["bond"])
    mock_db_collections["matches"].find_one = AsyncMock(return_value=valid_records["match"])
    mock_db_collections["defendants"].find_one = AsyncMock(return_value=valid_records["defendant"])
    mock_db_collections["indemnitors"].find_one = AsyncMock(return_value=valid_records["indemnitor"])
    mock_db_collections["poa_inventory"].find_one = AsyncMock(return_value=low_poa)

    res = await preflight_write_bond_forward(bond_case_id="BOND-TEST-001")
    assert res["success"] is False
    assert res["state"] == "blocked"
    assert "poa_tier_insufficient_for_bond_amount" in res["block_reasons"]


# ── TEST 8: Absent GAS Configuration ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_preflight_absent_gas_config(mock_db_collections, valid_records):
    mock_db_collections["active_bonds"].find_one = AsyncMock(return_value=valid_records["bond"])
    mock_db_collections["matches"].find_one = AsyncMock(return_value=valid_records["match"])
    mock_db_collections["defendants"].find_one = AsyncMock(return_value=valid_records["defendant"])
    mock_db_collections["indemnitors"].find_one = AsyncMock(return_value=valid_records["indemnitor"])
    mock_db_collections["poa_inventory"].find_one = AsyncMock(return_value=valid_records["poa"])

    with patch.dict(os.environ, {"GAS_WEB_APP_URL": "", "GAS_API_KEY": ""}):
        res = await preflight_write_bond_forward(bond_case_id="BOND-TEST-001")
        assert res["success"] is False
        assert res["state"] == "blocked"
        assert "gas_not_configured" in res["block_reasons"]


# ── TEST 9: Duplicate Correlation ID ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_preflight_duplicate_correlation_id(mock_db_collections, valid_bond_env, valid_records):
    mock_db_collections["active_bonds"].find_one = AsyncMock(return_value=valid_records["bond"])
    mock_db_collections["matches"].find_one = AsyncMock(return_value=valid_records["match"])
    mock_db_collections["defendants"].find_one = AsyncMock(return_value=valid_records["defendant"])
    mock_db_collections["indemnitors"].find_one = AsyncMock(return_value=valid_records["indemnitor"])
    mock_db_collections["poa_inventory"].find_one = AsyncMock(return_value=valid_records["poa"])
    mock_db_collections["gas_event_log"].find_one = AsyncMock(return_value={"_id": "existing_event"})

    res = await preflight_write_bond_forward(
        bond_case_id="BOND-TEST-001",
        correlation_id="DUPLICATE_CORR_ID_123",
    )
    assert res["success"] is False
    assert res["state"] == "blocked"
    assert "duplicate_correlation_id" in res["block_reasons"]


# ── TEST 10: Successful Preflight ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preflight_success(mock_db_collections, valid_bond_env, valid_records):
    mock_db_collections["active_bonds"].find_one = AsyncMock(return_value=valid_records["bond"])
    mock_db_collections["matches"].find_one = AsyncMock(return_value=valid_records["match"])
    mock_db_collections["defendants"].find_one = AsyncMock(return_value=valid_records["defendant"])
    mock_db_collections["indemnitors"].find_one = AsyncMock(return_value=valid_records["indemnitor"])
    mock_db_collections["poa_inventory"].find_one = AsyncMock(return_value=valid_records["poa"])
    mock_db_collections["gas_event_log"].find_one = AsyncMock(return_value=None)

    res = await preflight_write_bond_forward(bond_case_id="BOND-TEST-001")
    assert res["success"] is True
    assert res["state"] == "eligible_for_staff_approval"
    assert res["block_reasons"] == []
    assert res["details"]["bond_case_id"] == "BOND-TEST-001"
    assert res["details"]["surety_id"] == "osi"
    assert res["details"]["poa_number"] == "OSI3 20134296"
    assert res["details"]["gas_configured"] is True


# ── TEST 11: Execute Forwarding Success with Mock GAS ─────────────────────────

@pytest.mark.asyncio
async def test_execute_forwarding_success(mock_db_collections, valid_bond_env, valid_records):
    mock_db_collections["active_bonds"].find_one = AsyncMock(return_value=valid_records["bond"])
    mock_db_collections["matches"].find_one = AsyncMock(return_value=valid_records["match"])
    mock_db_collections["defendants"].find_one = AsyncMock(return_value=valid_records["defendant"])
    mock_db_collections["indemnitors"].find_one = AsyncMock(return_value=valid_records["indemnitor"])
    mock_db_collections["poa_inventory"].find_one = AsyncMock(return_value=valid_records["poa"])
    mock_db_collections["gas_event_log"].find_one = AsyncMock(return_value=None)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value={"success": True, "data": {"received": True}})

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        res = await execute_staff_approved_write_bond_forward(
            bond_case_id="BOND-TEST-001",
            staff_actor="admin@shamrockbailbonds.biz",
            correlation_id="corr_test_12345",
            confirmed=True,
        )

        assert res["success"] is True
        assert res["state"] == "forwarded"
        assert res["gas_response_status"] == 200
        assert res["correlation_id"] == "corr_test_12345"

        # Verify audit & event log insertion
        mock_db_collections["gas_event_log"].insert_one.assert_called_once()
        mock_db_collections["audit_events"].insert_one.assert_called_once()

        # Check outbound payload contains no PII
        call_kwargs = mock_post.call_args[1]
        posted_json = call_kwargs["json"]
        assert posted_json["action"] == "logWixEvent"
        assert posted_json["event_type"] == "write_bond_forward"
        assert posted_json["correlation_id"] == "corr_test_12345"
        assert posted_json["bond_case_id"] == "BOND-TEST-001"
        assert "defendant_name" not in posted_json
        assert "indemnitor_name" not in posted_json
        assert "phone" not in posted_json
        assert "email" not in posted_json


# ── TEST 12: Execute Forwarding Provider Rejection ─────────────────────────────

@pytest.mark.asyncio
async def test_execute_forwarding_provider_rejection(mock_db_collections, valid_bond_env, valid_records):
    mock_db_collections["active_bonds"].find_one = AsyncMock(return_value=valid_records["bond"])
    mock_db_collections["matches"].find_one = AsyncMock(return_value=valid_records["match"])
    mock_db_collections["defendants"].find_one = AsyncMock(return_value=valid_records["defendant"])
    mock_db_collections["indemnitors"].find_one = AsyncMock(return_value=valid_records["indemnitor"])
    mock_db_collections["poa_inventory"].find_one = AsyncMock(return_value=valid_records["poa"])
    mock_db_collections["gas_event_log"].find_one = AsyncMock(return_value=None)

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.json = MagicMock(return_value={"success": False, "error": "Internal GAS failure"})

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        res = await execute_staff_approved_write_bond_forward(
            bond_case_id="BOND-TEST-001",
            staff_actor="admin@shamrockbailbonds.biz",
            correlation_id="corr_test_rejected",
            confirmed=True,
        )

        assert res["success"] is False
        assert res["state"] == "provider_rejected"
        assert "Internal GAS failure" in res["error"] or "500" in res["error"]
