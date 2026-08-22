"""
ShamrockLeads — Staff-Approved Write-Bond Forwarding & Observability Service
=============================================================================
Provides strictly gated, idempotent, and non-PII preflight verification and
event forwarding for write-bond paperwork events to the central Google Apps Script
(GAS) factory.

Non-negotiable ecosystem boundaries:
  1. The chain is law: ArrestLead → Defendant → Indemnitor → validated Match
     → BondCase → Packet → Signature → Payment.
  2. Never create a stub bond or infer defendant/indemnitor/case/POA/surety.
  3. Fail closed on missing match, case number, surety, or POA inventory tier.
  4. Non-PII correlation ID and non-PII audit logging only.
  5. Preserve GAS Web App URL stability (target must be configured and valid).
  6. Idempotent: rejects duplicate correlation IDs and preserves state.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)

# Validated match statuses permitted for bonded case paperwork
VALID_MATCH_STATUSES = {"validated", "approved", "matched"}
VALID_SURETIES = {"osi", "palmetto"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _hash_fingerprint(val: str) -> str:
    """Non-secret, non-PII short SHA256 fingerprint for logging and verification."""
    if not val:
        return "none"
    return hashlib.sha256(val.encode("utf-8")).hexdigest()[:10]


def generate_correlation_id(prefix: str = "corr") -> str:
    """Generate a clean, non-PII correlation identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


async def preflight_write_bond_forward(
    *,
    bond_case_id: Optional[str] = None,
    booking_number: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Perform rigorous, fail-closed preflight checks for forwarding a write-bond
    event to the central GAS factory.

    Returns safe, staff-facing non-PII summary dictionary:
      - state: "eligible_for_staff_approval" | "blocked"
      - correlation_id: str
      - block_reasons: list[str]
      - details: dict of non-PII properties
    """
    block_reasons: List[str] = []
    cid = (correlation_id or "").strip() or generate_correlation_id()

    # 1. Authoritative BondCase Lookup (No synthetic stubs)
    bond_doc: Optional[Dict[str, Any]] = None
    if bond_case_id:
        bond_doc = await get_collection("active_bonds").find_one(
            {"$or": [{"Bond_Case_ID": bond_case_id}, {"bond_case_id": bond_case_id}]},
            {"_id": 0},
        )
        if not bond_doc:
            bond_doc = await get_collection("bond_cases").find_one(
                {"$or": [{"Bond_Case_ID": bond_case_id}, {"bond_case_id": bond_case_id}]},
                {"_id": 0},
            )
    elif booking_number:
        bond_doc = await get_collection("active_bonds").find_one(
            {"$or": [{"Booking_Number": booking_number}, {"booking_number": booking_number}]},
            {"_id": 0},
        )
        if not bond_doc:
            bond_doc = await get_collection("bond_cases").find_one(
                {"$or": [{"Booking_Number": booking_number}, {"booking_number": booking_number}]},
                {"_id": 0},
            )

    if not bond_doc:
        block_reasons.append("missing_bond_case")
        return {
            "success": False,
            "state": "blocked",
            "correlation_id": cid,
            "block_reasons": block_reasons,
            "details": {
                "bond_case_id": bond_case_id or "",
                "booking_number": booking_number or "",
                "gas_configured": False,
            },
        }

    # Extract non-PII core identifiers
    resolved_bond_case_id = str(bond_doc.get("Bond_Case_ID") or bond_doc.get("bond_case_id") or bond_case_id or "")
    resolved_booking_number = str(bond_doc.get("Booking_Number") or bond_doc.get("booking_number") or booking_number or "")
    resolved_case_number = str(bond_doc.get("Case_Number") or bond_doc.get("case_number") or "").strip()
    resolved_surety_id = str(bond_doc.get("Surety_ID") or bond_doc.get("surety_id") or "").lower().strip()
    resolved_poa_number = str(bond_doc.get("POA_Number") or bond_doc.get("poa_number") or "").strip()
    
    try:
        resolved_bond_amount = float(bond_doc.get("Bond_Amount") or bond_doc.get("bond_amount") or 0.0)
    except (ValueError, TypeError):
        resolved_bond_amount = 0.0

    defendant_id = str(bond_doc.get("Defendant_ID") or bond_doc.get("defendant_id") or "").strip()
    indemnitor_id = str(bond_doc.get("Indemnitor_ID") or bond_doc.get("indemnitor_id") or "").strip()
    match_id = str(bond_doc.get("Match_ID") or bond_doc.get("match_id") or "").strip()

    # 2. Case Number Validation
    if not resolved_case_number:
        block_reasons.append("missing_case_number")

    # 3. Match Validation Gate
    match_doc: Optional[Dict[str, Any]] = None
    if match_id:
        match_doc = await get_collection("matches").find_one(
            {"$or": [{"Match_ID": match_id}, {"match_id": match_id}]},
            {"_id": 0},
        )
    elif defendant_id and indemnitor_id:
        match_doc = await get_collection("matches").find_one(
            {
                "$or": [
                    {"Defendant_ID": defendant_id, "Indemnitor_ID": indemnitor_id},
                    {"defendant_id": defendant_id, "indemnitor_id": indemnitor_id},
                ]
            },
            {"_id": 0},
        )

    if not match_doc:
        block_reasons.append("missing_match")
    else:
        match_status = str(match_doc.get("Status") or match_doc.get("status") or "").lower().strip()
        if match_status not in VALID_MATCH_STATUSES:
            block_reasons.append(f"unvalidated_match_status_{match_status or 'unknown'}")

    # Verify defendant existence
    if not defendant_id:
        block_reasons.append("missing_defendant_id")
    else:
        def_exists = await get_collection("defendants").find_one(
            {"$or": [{"Defendant_ID": defendant_id}, {"defendant_id": defendant_id}]},
            {"_id": 1},
        )
        if not def_exists:
            block_reasons.append("defendant_record_not_found")

    # Verify indemnitor existence
    if not indemnitor_id:
        block_reasons.append("missing_indemnitor_id")
    else:
        ind_exists = await get_collection("indemnitors").find_one(
            {"$or": [{"Indemnitor_ID": indemnitor_id}, {"indemnitor_id": indemnitor_id}]},
            {"_id": 1},
        )
        if not ind_exists:
            block_reasons.append("indemnitor_record_not_found")

    # 4. Surety & POA Inventory Validation Gate
    if not resolved_surety_id:
        block_reasons.append("missing_surety_id")
    elif resolved_surety_id not in VALID_SURETIES:
        block_reasons.append(f"invalid_surety_id_{resolved_surety_id}")

    if not resolved_poa_number:
        block_reasons.append("missing_poa_number")
    else:
        poa_doc = await get_collection("poa_inventory").find_one(
            {
                "poa_number": resolved_poa_number,
                "surety_id": resolved_surety_id,
                "status": {"$in": ["assigned", "used"]},
            },
            {"_id": 0, "max_bond_value": 1, "status": 1},
        )
        if not poa_doc:
            block_reasons.append("poa_not_assigned_in_inventory")
        else:
            try:
                poa_max = float(poa_doc.get("max_bond_value") or 0.0)
            except (ValueError, TypeError):
                poa_max = 0.0

            if resolved_bond_amount <= 0:
                block_reasons.append("invalid_bond_amount_zero_or_negative")
            elif poa_max <= 0 or resolved_bond_amount > poa_max:
                block_reasons.append("poa_tier_insufficient_for_bond_amount")

    # 5. GAS Factory Configuration Gate
    gas_url = (os.getenv("GAS_WEB_APP_URL") or "").strip()
    gas_key = (os.getenv("GAS_API_KEY") or "").strip()

    if not gas_url or not gas_key:
        block_reasons.append("gas_not_configured")
    elif not (gas_url.startswith("https://script.google.com/macros/s/") and gas_url.endswith("/exec")):
        block_reasons.append("invalid_gas_web_app_url_format")

    # 6. Idempotency Check on Correlation ID
    existing_cid = await get_collection("gas_event_log").find_one(
        {"correlation_id": cid},
        {"_id": 1, "status": 1},
    )
    if existing_cid:
        block_reasons.append("duplicate_correlation_id")

    # Compile state
    state = "blocked" if block_reasons else "eligible_for_staff_approval"
    success = state == "eligible_for_staff_approval"

    details = {
        "bond_case_id": resolved_bond_case_id,
        "booking_number": resolved_booking_number,
        "case_number": resolved_case_number,
        "match_id": match_id or (match_doc.get("Match_ID") if match_doc else ""),
        "surety_id": resolved_surety_id,
        "poa_number": resolved_poa_number,
        "bond_amount": resolved_bond_amount,
        "gas_configured": bool(gas_url and gas_key),
        "gas_target_fingerprint": _hash_fingerprint(gas_url),
        "idempotent": not bool(existing_cid),
    }

    return {
        "success": success,
        "state": state,
        "correlation_id": cid,
        "block_reasons": block_reasons,
        "details": details,
    }


async def execute_staff_approved_write_bond_forward(
    *,
    bond_case_id: str,
    staff_actor: str,
    correlation_id: str,
    confirmed: bool,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Execute staff-approved write-bond paperwork event forwarding to central GAS factory.
    Fails closed if preflight conditions are not strictly satisfied or if confirmation is missing.
    """
    actor = (staff_actor or "").strip()
    if not actor:
        return {
            "success": False,
            "state": "blocked",
            "error": "staff_actor_required",
            "message": "Staff actor identification is required for auditable event forwarding.",
        }

    if not confirmed:
        return {
            "success": False,
            "state": "blocked",
            "error": "staff_confirmation_required",
            "message": "Explicit staff confirmation (confirmed=True) is required to execute forwarding.",
        }

    # Run preflight
    preflight = await preflight_write_bond_forward(
        bond_case_id=bond_case_id,
        correlation_id=correlation_id,
    )
    if preflight.get("state") != "eligible_for_staff_approval":
        return {
            "success": False,
            "state": "blocked",
            "error": "preflight_failed",
            "block_reasons": preflight.get("block_reasons", []),
            "details": preflight.get("details", {}),
        }

    cid = preflight["correlation_id"]
    details = preflight["details"]

    # Dry-run bypass
    if dry_run:
        return {
            "success": True,
            "state": "eligible_for_staff_approval",
            "dry_run": True,
            "correlation_id": cid,
            "details": details,
            "message": "Preflight passed. Ready for staff-approved live execution.",
        }

    gas_url = (os.getenv("GAS_WEB_APP_URL") or "").strip()
    gas_key = (os.getenv("GAS_API_KEY") or "").strip()

    # Outbound non-PII GAS event payload
    gas_payload = {
        "action": "logWixEvent",
        "apiKey": gas_key,
        "event_type": "write_bond_forward",
        "source": "super_crm_bond_desk",
        "correlation_id": cid,
        "bond_case_id": details["bond_case_id"],
        "booking_number": details["booking_number"],
        "case_number": details["case_number"],
        "surety_id": details["surety_id"],
        "poa_number": details["poa_number"],
        "bond_amount": details["bond_amount"],
        "staff_actor": actor,
        "timestamp": _utc_now_iso(),
    }

    now = _utc_now()
    gas_event_col = get_collection("gas_event_log")
    audit_col = get_collection("audit_events")

    # Execute outbound call
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.post(gas_url, json=gas_payload)

        status_code = resp.status_code
        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {"raw_text": resp.text[:200]}

        gas_success = status_code == 200 and (
            isinstance(resp_json, dict) and (resp_json.get("success") is True or resp_json.get("status") == "ok")
        )

        if gas_success:
            event_doc = {
                "correlation_id": cid,
                "event_type": "write_bond_forward",
                "bond_case_id": details["bond_case_id"],
                "booking_number": details["booking_number"],
                "case_number": details["case_number"],
                "surety_id": details["surety_id"],
                "poa_number": details["poa_number"],
                "staff_actor": actor,
                "status": "forwarded",
                "gas_status_code": status_code,
                "gas_target_fingerprint": details["gas_target_fingerprint"],
                "forwarded_at": now,
            }
            await gas_event_col.insert_one(event_doc)

            audit_doc = {
                "Event_ID": str(uuid.uuid4()),
                "event_type": "write_bond_forwarded_to_gas",
                "correlation_id": cid,
                "bond_case_id": details["bond_case_id"],
                "booking_number": details["booking_number"],
                "case_number": details["case_number"],
                "surety_id": details["surety_id"],
                "poa_number": details["poa_number"],
                "bond_amount": details["bond_amount"],
                "staff_actor": actor,
                "status_code": status_code,
                "timestamp": now,
            }
            await audit_col.insert_one(audit_doc)

            return {
                "success": True,
                "state": "forwarded",
                "correlation_id": cid,
                "bond_case_id": details["bond_case_id"],
                "booking_number": details["booking_number"],
                "case_number": details["case_number"],
                "surety_id": details["surety_id"],
                "poa_number": details["poa_number"],
                "gas_response_status": status_code,
                "gas_target_fingerprint": details["gas_target_fingerprint"],
                "message": "Write-bond event forwarded to central GAS factory with verified receipt.",
            }
        else:
            err_msg = str(resp_json.get("error") if isinstance(resp_json, dict) else f"HTTP {status_code}")
            event_doc = {
                "correlation_id": cid,
                "event_type": "write_bond_forward",
                "bond_case_id": details["bond_case_id"],
                "booking_number": details["booking_number"],
                "case_number": details["case_number"],
                "surety_id": details["surety_id"],
                "poa_number": details["poa_number"],
                "staff_actor": actor,
                "status": "provider_rejected",
                "gas_status_code": status_code,
                "error": err_msg[:200],
                "failed_at": now,
            }
            await gas_event_col.insert_one(event_doc)

            return {
                "success": False,
                "state": "provider_rejected",
                "correlation_id": cid,
                "error": f"GAS factory returned status {status_code}: {err_msg}",
                "details": details,
            }

    except Exception as exc:
        logger.exception("Outbound GAS write-bond forward failed for correlation_id %s", cid)
        err_msg = str(exc)
        event_doc = {
            "correlation_id": cid,
            "event_type": "write_bond_forward",
            "bond_case_id": details["bond_case_id"],
            "booking_number": details["booking_number"],
            "case_number": details["case_number"],
            "surety_id": details["surety_id"],
            "poa_number": details["poa_number"],
            "staff_actor": actor,
            "status": "provider_rejected",
            "error": err_msg[:200],
            "failed_at": now,
        }
        try:
            await gas_event_col.insert_one(event_doc)
        except Exception:
            pass

        return {
            "success": False,
            "state": "provider_rejected",
            "correlation_id": cid,
            "error": f"Network transport failure to GAS factory: {err_msg[:200]}",
            "details": details,
        }
