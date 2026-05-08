"""
ShamrockLeads — Client Portal Service
Magic-link token generation, validation, and scoped data access for
the public-facing defendant/indemnitor self-service portal.

Security Model:
  - Each token is scoped to exactly ONE bond case (booking_number)
  - Tokens are role-specific: 'defendant' or 'indemnitor'
  - Tokens expire after 90 days by default (configurable)
  - Access is read-only except for check-in submissions
  - No PII is logged — only token hashes and booking references
"""
from __future__ import annotations

import os
import uuid
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
DEFAULT_TOKEN_TTL_DAYS = int(os.getenv("PORTAL_TOKEN_TTL_DAYS", "90"))
SWIPESIMPLE_PAYMENT_LINK = os.getenv(
    "SWIPESIMPLE_PAYMENT_LINK",
    "https://swipesimple.com/links/lnk_b6bf996f4c57bb340a150e297e769abd",
)
PUBLIC_URL = os.getenv("DASHBOARD_PUBLIC_URL", "https://leads.shamrockbailbonds.biz")


# ── Token Generation ──────────────────────────────────────────────────────────

async def generate_portal_token(
    booking_number: str,
    role: str = "indemnitor",
    ttl_days: int = DEFAULT_TOKEN_TTL_DAYS,
    created_by: str = "system",
) -> dict:
    """
    Generate a new magic-link token for a bond case.

    Args:
        booking_number: The booking number to scope this token to
        role: 'defendant' or 'indemnitor' — controls what data is visible
        ttl_days: Token expiration in days
        created_by: Who generated this token (staff name, 'system', etc.)

    Returns:
        dict with token, url, expires_at, etc.
    """
    tokens_col = get_collection("portal_tokens")
    active_bonds = get_collection("active_bonds")

    # Verify the bond exists
    bond = await active_bonds.find_one(
        {"booking_number": booking_number},
        {"_id": 0, "defendant_name": 1, "indemnitor_name": 1, "status": 1,
         "county": 1, "bond_amount": 1, "case_number": 1}
    )
    if not bond:
        return {"success": False, "error": "Bond not found"}

    # Generate cryptographically secure token
    token = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=ttl_days)

    token_doc = {
        "token": token,
        "booking_number": booking_number,
        "role": role,  # 'defendant' or 'indemnitor'
        "created_at": now,
        "expires_at": expires_at,
        "created_by": created_by,
        "last_accessed": None,
        "access_count": 0,
        "active": True,
        "defendant_name": bond.get("defendant_name", ""),
        "indemnitor_name": bond.get("indemnitor_name", ""),
    }

    await tokens_col.insert_one(token_doc)

    portal_url = f"{PUBLIC_URL.rstrip('/')}/c/{token}"

    logger.info(
        "Portal token generated for booking=%s role=%s by=%s",
        booking_number, role, created_by
    )

    return {
        "success": True,
        "token": token,
        "url": portal_url,
        "role": role,
        "booking_number": booking_number,
        "expires_at": expires_at.isoformat(),
        "defendant_name": bond.get("defendant_name", ""),
    }


async def revoke_portal_token(token: str) -> dict:
    """Revoke a portal token immediately."""
    tokens_col = get_collection("portal_tokens")
    result = await tokens_col.update_one(
        {"token": token, "active": True},
        {"$set": {"active": False, "revoked_at": datetime.now(timezone.utc)}}
    )
    if result.matched_count == 0:
        return {"success": False, "error": "Token not found or already revoked"}
    return {"success": True}


# ── Token Validation ──────────────────────────────────────────────────────────

async def validate_token(token: str) -> Optional[dict]:
    """
    Validate a portal token and return the associated bond data.
    Returns None if token is invalid, expired, or revoked.
    Updates access tracking on each valid access.
    """
    tokens_col = get_collection("portal_tokens")
    now = datetime.now(timezone.utc)

    token_doc = await tokens_col.find_one({
        "token": token,
        "active": True,
        "expires_at": {"$gt": now},
    })

    if not token_doc:
        return None

    # Update access tracking (non-blocking — don't fail if this errors)
    try:
        await tokens_col.update_one(
            {"token": token},
            {
                "$set": {"last_accessed": now},
                "$inc": {"access_count": 1},
            }
        )
    except Exception:
        pass  # Access tracking is nice-to-have, not critical

    return {
        "booking_number": token_doc["booking_number"],
        "role": token_doc["role"],
        "defendant_name": token_doc.get("defendant_name", ""),
        "indemnitor_name": token_doc.get("indemnitor_name", ""),
        "created_at": token_doc["created_at"],
        "expires_at": token_doc["expires_at"],
    }


# ── Scoped Data Access ────────────────────────────────────────────────────────

async def get_portal_case_status(booking_number: str, role: str) -> dict:
    """
    Get case status data scoped to the portal viewer's role.
    Defendants see: their own status, court dates, check-in requirements.
    Indemnitors see: defendant status, payment info, balance due.
    """
    active_bonds = get_collection("active_bonds")
    bond = await active_bonds.find_one(
        {"booking_number": booking_number},
        {"_id": 0}
    )

    if not bond:
        return {"error": "Case not found"}

    # Serialize datetimes
    for k, v in list(bond.items()):
        if isinstance(v, datetime):
            bond[k] = v.isoformat()

    # Base data — visible to both roles
    status_data = {
        "booking_number": booking_number,
        "defendant_name": bond.get("defendant_name", ""),
        "county": bond.get("county", ""),
        "status": bond.get("status", ""),
        "bond_amount": bond.get("bond_amount", 0),
        "case_number": bond.get("case_number", ""),
        "bond_date": bond.get("bond_date") or bond.get("created_at", ""),
        "court_date": bond.get("court_date", ""),
        "court_location": bond.get("court_location", ""),
    }

    if role == "defendant":
        # Defendants see: check-in info, court dates, compliance
        status_data.update({
            "check_in_required": bond.get("check_in_required", False),
            "check_in_frequency_days": bond.get("check_in_frequency_days", 30),
            "last_check_in": bond.get("last_check_in") or bond.get("last_checkin", ""),
            "next_check_in_due": bond.get("next_check_in_due") or bond.get("next_checkin_due", ""),
            "missed_check_ins": bond.get("missed_check_ins", 0),
        })

    elif role == "indemnitor":
        # Indemnitors see: payment info, balance, premium details
        status_data.update({
            "premium": bond.get("premium", 0),
            "indemnitor_name": bond.get("indemnitor_name", ""),
            "payment_link": SWIPESIMPLE_PAYMENT_LINK,
        })

        # Get payment plan info
        plans_col = get_collection("payment_plans")
        plan = await plans_col.find_one(
            {"booking_number": booking_number},
            {"_id": 0}
        )
        if plan:
            for k, v in list(plan.items()):
                if isinstance(v, datetime):
                    plan[k] = v.isoformat()
            status_data["payment_plan"] = {
                "total_amount": plan.get("total_amount", 0),
                "down_payment": plan.get("down_payment", 0),
                "balance": plan.get("balance", 0),
                "frequency": plan.get("frequency", ""),
                "installment_amount": plan.get("installment_amount", 0),
                "next_due_date": plan.get("next_due_date", ""),
                "status": plan.get("status", ""),
                "payments_made": plan.get("payments_made", 0),
            }

        # Get recent payments
        payments_col = get_collection("payments")
        payment_cursor = payments_col.find(
            {"booking_number": booking_number},
            {"_id": 0}
        ).sort("timestamp", -1).limit(20)
        payments = []
        async for p in payment_cursor:
            for k, v in list(p.items()):
                if isinstance(v, datetime):
                    p[k] = v.isoformat()
            payments.append({
                "amount": p.get("amount", 0),
                "method": p.get("method", ""),
                "status": p.get("status", ""),
                "timestamp": p.get("timestamp", ""),
                "notes": p.get("notes", ""),
            })
        status_data["payment_history"] = payments

    # Get upcoming court reminders
    reminders_col = get_collection("court_reminders")
    reminders = []
    async for r in reminders_col.find(
        {"booking_number": booking_number, "status": {"$in": ["scheduled", "pending"]}},
        {"_id": 0, "court_date": 1, "court_location": 1, "case_number": 1,
         "reminder_type": 1, "send_at": 1}
    ).sort("send_at", 1).limit(10):
        for k, v in list(r.items()):
            if isinstance(v, datetime):
                r[k] = v.isoformat()
        reminders.append(r)
    status_data["court_reminders"] = reminders

    return status_data


async def submit_portal_checkin(
    booking_number: str,
    lat: Optional[float],
    lng: Optional[float],
    accuracy: Optional[float],
    selfie_data: Optional[str] = None,
    notes: str = "",
) -> dict:
    """
    Submit a defendant check-in from the portal.
    Records GPS + optional selfie to the bond_checkins collection.
    """
    checkins_col = get_collection("bond_checkins")
    active_bonds = get_collection("active_bonds")
    audit_col = get_collection("audit_events")
    now = datetime.now(timezone.utc)

    checkin_doc = {
        "booking_number": booking_number,
        "checkin_at": now,
        "method": "portal_self_service",
        "gps_lat": lat,
        "gps_lon": lng,
        "gps_accuracy": accuracy,
        "has_selfie": bool(selfie_data),
        "notes": notes,
        "source": "client_portal",
    }

    # Store selfie reference (base64 data would be in a separate field/storage)
    if selfie_data:
        checkin_doc["selfie_thumbnail"] = selfie_data[:200]  # Store first 200 chars as thumbnail ref

    result = await checkins_col.insert_one(checkin_doc)

    # Update bond record
    await active_bonds.update_one(
        {"booking_number": booking_number},
        {
            "$set": {
                "last_check_in": now.isoformat(),
                "last_checkin": now.isoformat(),
                "last_checkin_method": "portal_self_service",
                "updated_at": now,
            },
            "$inc": {"total_check_ins": 1},
        }
    )

    # If GPS provided, add to location history
    if lat is not None and lng is not None:
        location_entry = {
            "lat": lat,
            "lng": lng,
            "accuracy": accuracy,
            "source": "portal_checkin",
            "ts": now.isoformat(),
        }
        await active_bonds.update_one(
            {"booking_number": booking_number},
            {
                "$push": {"location_history": location_entry},
                "$set": {"latest_location": location_entry},
            }
        )

    # Audit
    await audit_col.insert_one({
        "event_type": "portal_checkin",
        "entity_id": booking_number,
        "entity_type": "bond_case",
        "timestamp": now,
        "source": "client_portal",
        "details": {
            "has_gps": lat is not None,
            "has_selfie": bool(selfie_data),
        }
    })

    logger.info("Portal check-in submitted for booking=%s", booking_number)

    return {
        "success": True,
        "checkin_id": str(result.inserted_id),
        "checkin_at": now.isoformat(),
    }


async def get_portal_tokens_for_bond(booking_number: str) -> list:
    """Get all active portal tokens for a bond (staff use)."""
    tokens_col = get_collection("portal_tokens")
    now = datetime.now(timezone.utc)
    cursor = tokens_col.find(
        {"booking_number": booking_number, "active": True, "expires_at": {"$gt": now}},
        {"_id": 0, "token": 1, "role": 1, "created_at": 1, "expires_at": 1,
         "access_count": 1, "last_accessed": 1, "created_by": 1}
    ).sort("created_at", -1)
    tokens = []
    async for doc in cursor:
        for k, v in list(doc.items()):
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
        doc["url"] = f"{PUBLIC_URL.rstrip('/')}/c/{doc['token']}"
        tokens.append(doc)
    return tokens
