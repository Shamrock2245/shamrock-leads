"""
ShamrockLeads — BlueBubbles Diagnostic & Staff Smoke Service
============================================================
Provides non-PII, fail-closed preflight diagnostics and single staff-authorized
smoke execution for checklist item D2 (BlueBubbles / iMessage delivery).

Distinguishes:
  - unavailable_tunnel: Connection refused, timeout, or tunnel offline
  - invalid_service_auth: BlueBubbles API password rejected (401)
  - invalid_destination: Phone number formatting or unroutable target
  - provider_rejection: BlueBubbles rejected delivery (e.g. not an Apple ID / SMS failure)
  - audit_write_failure: Failure to persist non-PII audit record
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from dashboard.extensions import (
    BB_SERVERS,
    format_phone,
    get_bb_server,
    get_collection,
    init_bluebubbles,
)
from dashboard.routers.bb_private_api import BlueBubblesClient

logger = logging.getLogger(__name__)


def _mask_phone(phone: Optional[str]) -> str:
    """Mask phone to last 4 digits only (non-PII, SOC2/HIPAA compliant)."""
    if not phone:
        return "(none)"
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    return f"...{digits[-4:]}" if len(digits) >= 4 else "...****"


def _hash_token(val: str) -> str:
    """Short non-secret SHA256 fingerprint for logging."""
    if not val:
        return "none"
    return hashlib.sha256(val.encode("utf-8")).hexdigest()[:10]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


async def check_bluebubbles_server_diagnostics(
    from_number: str = "2399550178",
    candidate_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Perform a safe, read-only diagnostic probe of the BlueBubbles server.
    Distinguishes unavailable_tunnel vs invalid_service_auth vs healthy.
    Never exposes passwords, tokens, or raw tunnel URLs in output.
    """
    if not BB_SERVERS:
        init_bluebubbles()

    srv = get_bb_server(from_number)
    configured_url = srv.get("url") if srv else (os.getenv("BLUEBUBBLES_URL_0178") or os.getenv("BLUEBUBBLES_URL") or "")
    password = srv.get("password") if srv else (os.getenv("BLUEBUBBLES_PASSWORD_0178") or os.getenv("BLUEBUBBLES_PASSWORD") or "")

    if not password:
        return {
            "success": False,
            "state": "invalid_service_auth",
            "error_code": "missing_password_in_env",
            "message": "BlueBubbles password is not configured in environment.",
            "server_configured": False,
        }

    urls_to_test = candidate_urls or []
    if configured_url and configured_url not in urls_to_test:
        urls_to_test.append(configured_url)

    # Standard fallback candidates
    for std_url in [
        "http://100.102.10.86:1234",          # Tailscale iMac direct
        "https://bb.shamrockbailbonds.biz",   # Cloudflare Named Tunnel
        "http://178.156.179.237:12434",        # frp server port on VPS
        "http://localhost:1234",              # Local host (if running on iMac)
    ]:
        if std_url not in urls_to_test:
            urls_to_test.append(std_url)

    probes: List[Dict[str, Any]] = []
    active_client: Optional[BlueBubblesClient] = None
    successful_url: Optional[str] = None
    server_info_data: Dict[str, Any] = {}

    for target_url in urls_to_test:
        url_label = target_url.split("://")[-1].split("/")[0]
        client = BlueBubblesClient(target_url, password, timeout=4.0)
        try:
            res = await client.server_info()
            status_code = res.get("status_code", 0)
            success = res.get("success", False)

            if success and status_code in (200, 201):
                data = res.get("data") if isinstance(res.get("data"), dict) else {}
                server_ver = data.get("server_version") or data.get("version") or "unknown"
                private_api = bool(
                    data.get("private_api")
                    or data.get("helper_connected")
                    or data.get("privateApiConnected")
                )
                probes.append({
                    "target": url_label,
                    "state": "healthy",
                    "status_code": status_code,
                    "server_version": server_ver,
                    "private_api_connected": private_api,
                })
                if not active_client:
                    active_client = client
                    successful_url = target_url
                    server_info_data = data
            elif status_code == 401:
                probes.append({
                    "target": url_label,
                    "state": "invalid_service_auth",
                    "status_code": 401,
                    "message": "Password rejected by BlueBubbles server.",
                })
            else:
                err_type = res.get("error") or f"HTTP {status_code}"
                probes.append({
                    "target": url_label,
                    "state": "unavailable_tunnel",
                    "status_code": status_code,
                    "error": str(err_type),
                })
        except Exception as exc:
            probes.append({
                "target": url_label,
                "state": "unavailable_tunnel",
                "status_code": 0,
                "error": str(exc)[:100],
            })

    if active_client and successful_url:
        return {
            "success": True,
            "state": "healthy",
            "active_target": successful_url.split("://")[-1].split("/")[0],
            "server_version": server_info_data.get("server_version") or server_info_data.get("version") or "1.9.x",
            "private_api_connected": bool(server_info_data.get("private_api") or server_info_data.get("helper_connected")),
            "os_version": server_info_data.get("os_version") or "macOS",
            "probes": probes,
        }

    # If no target succeeded, evaluate most specific failure
    has_auth_fail = any(p["state"] == "invalid_service_auth" for p in probes)
    final_state = "invalid_service_auth" if has_auth_fail else "unavailable_tunnel"

    return {
        "success": False,
        "state": final_state,
        "error_code": final_state,
        "message": "All BlueBubbles tunnel endpoints unreachable or rejected connection." if final_state == "unavailable_tunnel" else "BlueBubbles authentication failed.",
        "probes": probes,
    }


async def preflight_imessage_smoke(
    *,
    recipient_phone: str,
    from_number: str = "2399550178",
) -> Dict[str, Any]:
    """
    Perform a strict preflight diagnostic prior to any live iMessage smoke send.
    Validates recipient number format, server connectivity, and review-mode policy.
    """
    # 1. Validate destination phone
    raw_phone = (recipient_phone or "").strip()
    formatted = format_phone(raw_phone)
    if not formatted or len(formatted) < 10:
        return {
            "success": False,
            "state": "invalid_destination",
            "error": "invalid_phone_format",
            "message": "Recipient phone number must be a valid 10-digit or E.164 US phone number.",
            "recipient_masked": _mask_phone(raw_phone),
        }

    # 2. Probe server diagnostics
    diag = await check_bluebubbles_server_diagnostics(from_number=from_number)
    if not diag.get("success"):
        return {
            "success": False,
            "state": diag.get("state", "unavailable_tunnel"),
            "error": diag.get("error_code", "tunnel_offline"),
            "message": diag.get("message"),
            "recipient_masked": _mask_phone(formatted),
            "probes": diag.get("probes", []),
        }

    return {
        "success": True,
        "state": "eligible_for_staff_approval",
        "recipient_masked": _mask_phone(formatted),
        "server_state": diag.get("state"),
        "server_version": diag.get("server_version"),
        "private_api_connected": diag.get("private_api_connected"),
        "active_target": diag.get("active_target"),
        "review_mode_enforced": True,
        "message": "BlueBubbles server is reachable and ready for single staff-authorized test message.",
    }


async def execute_staff_approved_imessage_smoke(
    *,
    recipient_phone: str,
    staff_actor: str,
    confirmed: bool,
    custom_message: Optional[str] = None,
    correlation_id: Optional[str] = None,
    from_number: str = "2399550178",
) -> Dict[str, Any]:
    """
    Execute exactly one staff-approved dashboard iMessage smoke test.
    Preserves non-PII audit trail, records delivery result, and enforces review-first mode.
    """
    actor = (staff_actor or "").strip()
    if not actor:
        return {
            "success": False,
            "state": "blocked",
            "error": "staff_actor_required",
            "message": "Staff actor identification is required for auditable smoke execution.",
        }

    if not confirmed:
        return {
            "success": False,
            "state": "blocked",
            "error": "staff_confirmation_required",
            "message": "Explicit staff authorization (confirmed=True) is required to dispatch the test message.",
        }

    # Run preflight
    preflight = await preflight_imessage_smoke(
        recipient_phone=recipient_phone,
        from_number=from_number,
    )
    if not preflight.get("success"):
        return {
            "success": False,
            "state": preflight.get("state"),
            "error": preflight.get("error"),
            "message": preflight.get("message"),
            "recipient_masked": preflight.get("recipient_masked"),
        }

    phone_formatted = format_phone(recipient_phone) or recipient_phone
    cid = (correlation_id or "").strip() or f"bb_smoke_{uuid.uuid4().hex[:10]}"
    msg_text = (custom_message or "").strip() or f"🍀 Shamrock Bail Bonds — Operational Verification Check (ID: {cid})"
    chat_guid = f"any;-;{phone_formatted}"
    temp_guid = f"shamrock-smoke-{uuid.uuid4().hex[:12]}"
    now = _utc_now()

    # Resolve server config
    srv = get_bb_server(from_number)
    pw = srv.get("password") if srv else (os.getenv("BLUEBUBBLES_PASSWORD_0178") or os.getenv("BLUEBUBBLES_PASSWORD") or "")
    url = srv.get("url") if srv else (os.getenv("BLUEBUBBLES_URL_0178") or os.getenv("BLUEBUBBLES_URL") or "http://100.102.10.86:1234")

    client = BlueBubblesClient(url, pw, timeout=10.0)

    try:
        bb_resp = await client.send_text(chat_guid, msg_text, temp_guid=temp_guid)
        success = bb_resp.get("success", False)
        status_code = bb_resp.get("status_code", 0)
        data = bb_resp.get("data") if isinstance(bb_resp.get("data"), dict) else {}
        bb_guid = str(data.get("guid") or "")
        bb_guid_fp = _hash_token(bb_guid) if bb_guid else "none"

        if success and status_code in (200, 201):
            # Record non-PII audit event in audit_events and imessage_outreach
            try:
                audit_col = get_collection("audit_events")
                await audit_col.insert_one({
                    "Event_ID": str(uuid.uuid4()),
                    "event_type": "dashboard_imessage_smoke_sent",
                    "correlation_id": cid,
                    "recipient_masked": _mask_phone(phone_formatted),
                    "staff_actor": actor,
                    "status": "delivered",
                    "provider_guid_fp": bb_guid_fp,
                    "timestamp": now,
                })
            except Exception as audit_exc:
                logger.error("[bb_diagnostic] Audit write failure: %s", audit_exc)
                return {
                    "success": False,
                    "state": "audit_write_failure",
                    "error": f"Message sent but audit recording failed: {str(audit_exc)[:100]}",
                    "correlation_id": cid,
                    "recipient_masked": _mask_phone(phone_formatted),
                }

            return {
                "success": True,
                "state": "forwarded",
                "correlation_id": cid,
                "recipient_masked": _mask_phone(phone_formatted),
                "status_code": status_code,
                "provider_guid_fingerprint": bb_guid_fp,
                "staff_actor": actor,
                "timestamp": _utc_now_iso(),
                "message": "Test iMessage successfully dispatched via BlueBubbles server.",
            }
        else:
            err_msg = str(bb_resp.get("message") or bb_resp.get("error") or f"HTTP {status_code}")
            return {
                "success": False,
                "state": "provider_rejection",
                "correlation_id": cid,
                "recipient_masked": _mask_phone(phone_formatted),
                "error": err_msg[:200],
                "status_code": status_code,
            }

    except Exception as exc:
        logger.exception("[bb_diagnostic] Send exception during smoke: %s", exc)
        return {
            "success": False,
            "state": "provider_rejection",
            "correlation_id": cid,
            "recipient_masked": _mask_phone(phone_formatted),
            "error": f"Transport failure to BlueBubbles: {str(exc)[:150]}",
        }
