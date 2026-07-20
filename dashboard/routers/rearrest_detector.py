
"""
ShamrockLeads — Re-Arrest Detector
Cross-references new arrests against active bonds.

If a bonded defendant is re-arrested:
  - Creates a CRITICAL notification
  - Posts to Slack #rearrest channel
  - Flags the bond record

Endpoints:
  POST /rearrest/scan   — Manual full scan
  GET  /rearrest/alerts  — Recent re-arrest alerts
"""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from datetime import datetime, timezone, timedelta
import re
import os

from dashboard.extensions import get_collection

rearrest_bp = APIRouter(prefix="/api", tags=["rearrest"])
# Slack webhook for re-arrest alerts
SLACK_REARREST_WEBHOOK = os.getenv("SLACK_WEBHOOK_ARRESTS", "")


def _normalize_name(name: str) -> str:
    """Normalize a name for fuzzy matching: lowercase, strip suffixes, collapse whitespace."""
    if not name:
        return ""
    name = name.lower().strip()
    # Remove common suffixes
    for suffix in [" jr", " sr", " ii", " iii", " iv", "."]:
        name = name.replace(suffix, "")
    # Collapse multiple spaces
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def _name_parts(name: str) -> tuple:
    """Extract (last, first) from 'LAST, FIRST' or 'FIRST LAST' formats."""
    norm = _normalize_name(name)
    if ',' in norm:
        parts = norm.split(',', 1)
        return (parts[0].strip(), parts[1].strip())
    parts = norm.split()
    if len(parts) >= 2:
        return (parts[-1], parts[0])
    return (norm, "")


def _names_match(name_a: str, name_b: str) -> bool:
    """Check if two names refer to the same person (fuzzy)."""
    last_a, first_a = _name_parts(name_a)
    last_b, first_b = _name_parts(name_b)

    if not last_a or not last_b:
        return False

    # Last name must match exactly
    if last_a != last_b:
        return False

    # First name: at least first 3 chars must match (handles Robert/Rob/Bobby edge cases poorly
    # but prevents false positives better than full fuzzy)
    if first_a and first_b:
        min_len = min(len(first_a), len(first_b), 3)
        return first_a[:min_len] == first_b[:min_len]

    return False


def _dob_matches(dob_a: str, dob_b: str) -> bool:
    """Check if two DOBs match (various formats)."""
    if not dob_a or not dob_b:
        return False  # Can't confirm without DOB
    # Normalize to digits only
    digits_a = re.sub(r'\D', '', str(dob_a))
    digits_b = re.sub(r'\D', '', str(dob_b))
    # Match if same 8 digits (MMDDYYYY or YYYYMMDD)
    if len(digits_a) == 8 and len(digits_b) == 8:
        return digits_a == digits_b or digits_a == digits_b[4:] + digits_b[:4]
    return digits_a == digits_b


async def scan_for_rearrests(hours: int = 24) -> dict:
    """
    Scan recent arrests (last N hours) against active bonds.
    Returns detected re-arrests.

    Writes to `rearrest_notifications` collection — the same collection
    consumed by the Command Center UI (/api/rearrest/pending).
    """
    arrests_col = get_collection("arrests")
    bonds_col = get_collection("active_bonds")
    rearrest_col = get_collection("rearrest_notifications")

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=hours)).isoformat()

    # Get all active bonds with defendant info
    active_bonds = []
    async for bond in bonds_col.find({"status": "active"}):
        active_bonds.append(bond)

    if not active_bonds:
        return {"scanned": 0, "detected": 0, "message": "No active bonds to check against"}

    # Get recent arrests
    recent_arrests = []
    async for arrest in arrests_col.find({"scraped_at": {"$gte": cutoff}}):
        recent_arrests.append(arrest)

    detected = []

    for arrest in recent_arrests:
        arrest_name = arrest.get("full_name", "") or arrest.get("defendant_name", "")
        arrest_dob = arrest.get("dob", "") or arrest.get("date_of_birth", "")

        for bond in active_bonds:
            bond_name = bond.get("defendant_name", "") or bond.get("full_name", "")
            bond_dob = bond.get("dob", "") or bond.get("date_of_birth", "")

            if not _names_match(arrest_name, bond_name):
                continue

            # Name matches — check DOB for confirmation (if available)
            confidence = "probable"
            if arrest_dob and bond_dob:
                if _dob_matches(arrest_dob, bond_dob):
                    confidence = "confirmed"
                else:
                    continue  # DOBs don't match, skip

            # Check if we already alerted on this
            # Check both old field name (bond_id) and new schema (defendant_name_norm + booking_number)
            arrest_booking = arrest.get("booking_number", "")
            existing = await rearrest_col.find_one({
                "$or": [
                    {"booking_number": arrest_booking, "bond_id": str(bond.get("_id", ""))},
                    {"defendant_name_norm": _normalize_name(arrest_name).upper(), "booking_number": arrest_booking},
                    {"booking_number": arrest_booking, "prior_booking_number": bond.get("booking_number", "")},
                ]
            })
            if existing:
                continue

            # ── Build notification in the unified schema expected by the UI ──
            # Matches the document shape written by writers/rearrest_checker.py
            # and consumed by sl-rearrest.js via /api/rearrest/pending
            indemnitor = bond.get("indemnitor") or {}
            indemnitor_name = (
                bond.get("indemnitor_name")
                or indemnitor.get("name", "")
                or indemnitor.get("firstName", "")
            )
            indemnitor_phone = (
                bond.get("indemnitor_phone")
                or indemnitor.get("phone", "")
            )
            indemnitor_email = (
                bond.get("indemnitor_email")
                or indemnitor.get("email", "")
            )

            # Normalize charges to string for UI consistency
            raw_charges = arrest.get("charges", "")
            if isinstance(raw_charges, list):
                charges_str = "; ".join(str(c) for c in raw_charges)
            else:
                charges_str = str(raw_charges)

            alert = {
                # New arrest details
                "defendant_name": arrest_name,
                "defendant_name_norm": _normalize_name(arrest_name).upper(),
                "booking_number": arrest_booking,
                "county": arrest.get("county", ""),
                "charges": charges_str,
                "bond_amount": arrest.get("bond_amount", 0),
                "arrest_date": arrest.get("arrest_date", ""),
                "custody_status": arrest.get("custody_status", ""),
                # Prior bond / indemnitor details
                "indemnitor_name": indemnitor_name,
                "indemnitor_phone": indemnitor_phone,
                "indemnitor_email": indemnitor_email,
                "prior_booking_number": bond.get("booking_number", ""),
                "prior_bond_amount": bond.get("bond_amount", 0),
                "prior_bond_date": bond.get("created_at", bond.get("bond_date", "")),
                "prior_defendant_name": bond.get("defendant_name", ""),
                "prior_county": bond.get("county", ""),
                "prior_bonds_count": 1,
                "prior_bonds_source": "active_bonds",
                # Backward compat fields
                "bond_id": str(bond.get("_id", "")),
                "original_bond_amount": bond.get("bond_amount", 0),
                "original_case_number": bond.get("case_number", ""),
                "original_poa": bond.get("poa_number", ""),
                "confidence": confidence,
                # Workflow state — matches what the UI queries
                "status": "pending_review",
                "created_at": now,
                "updated_at": now,
                "detected_at": now.isoformat(),
                "reviewed_by": None,
                "reviewed_at": None,
                "contacted_at": None,
            }

            await rearrest_col.insert_one(alert)
            alert["_id"] = str(alert.get("_id", ""))
            detected.append(alert)

            # Real-time dashboard event — sl-core.js + SLRearrest listen for
            # 'rearrest_detected' (toast, activity feed, panel refresh).
            try:
                from dashboard.routers.events import publish_event
                await publish_event("rearrest_detected", {
                    "full_name": arrest_name,
                    "booking_number": arrest_booking,
                    "county": arrest.get("county", ""),
                    "state": arrest.get("state", ""),
                    "charges": charges_str[:200],
                    "original_poa": bond.get("poa_number", ""),
                    "original_bond_amount": bond.get("bond_amount", 0),
                    "confidence": confidence,
                })
            except Exception:
                pass

            # Create notification
            try:
                from dashboard.routers.notifications import create_notification
                await create_notification(
                    notification_type="rearrest",
                    title=f"🔄 RE-ARREST: {arrest_name}",
                    message=f"{arrest.get('county', '')} County — Active bond #{bond.get('poa_number', 'N/A')} (${bond.get('bond_amount', 0):,.0f})",
                    entity_id=arrest.get("booking_number", ""),
                    entity_type="rearrest",
                    metadata={
                        "confidence": confidence,
                        "new_county": arrest.get("county"),
                        "original_poa": bond.get("poa_number"),
                    },
                )
            except Exception:
                pass

            # Flag the bond
            try:
                await bonds_col.update_one(
                    {"_id": bond["_id"]},
                    {"$set": {
                        "rearrest_detected": True,
                        "rearrest_date": now.isoformat(),
                        "rearrest_booking": arrest.get("booking_number"),
                    }}
                )
            except Exception:
                pass

    return {
        "scanned_arrests": len(recent_arrests),
        "active_bonds_checked": len(active_bonds),
        "detected": len(detected),
        "alerts": detected,
    }


@rearrest_bp.post("/rearrest/scan")
async def manual_scan(request: Request):
    """Run a manual re-arrest scan."""
    data = await request.json() or {}
    hours = int(data.get("hours", 24))
    result = await scan_for_rearrests(hours=hours)
    return result


@rearrest_bp.get("/rearrest/alerts")
async def get_alerts(limit: int = Query(default=20)):
    """Get recent re-arrest alerts (reads from unified rearrest_notifications)."""
    rearrest_col = get_collection("rearrest_notifications")
    limit = min(50, int(limit))

    cursor = rearrest_col.find().sort("created_at", -1).limit(limit)
    results = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        for dt_field in ("created_at", "updated_at", "reviewed_at", "contacted_at", "prior_bond_date"):
            val = doc.get(dt_field)
            if hasattr(val, "isoformat"):
                doc[dt_field] = val.isoformat()
        results.append(doc)

    return {"alerts": results, "total": len(results)}

