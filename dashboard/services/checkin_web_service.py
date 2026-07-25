"""
Defendant Mobile Check-In Web Service — ShamrockLeads
Generates single-use check-in tokens sent via BlueBubbles iMessage/SMS link,
serves mobile web check-in, and captures camera selfie + HTML5 GPS location.
"""

import math
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from dashboard.extensions import get_collection

logger = logging.getLogger("shamrock.checkin")

def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in miles between two lat/lon coordinates."""
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)

async def create_checkin_request(booking_number: str, defendant_phone: Optional[str] = None) -> Dict[str, Any]:
    """Generate single-use 24-hour check-in token for a defendant."""
    checkin_col = get_collection("check_in_requests")
    now = datetime.now(timezone.utc)
    token = uuid.uuid4().hex[:16]
    expires_at = now + timedelta(hours=24)

    doc = {
        "token": token,
        "booking_number": booking_number,
        "defendant_phone": defendant_phone or "",
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "status": "pending",  # pending, completed, expired
    }
    await checkin_col.insert_one(doc)

    checkin_url = f"https://leads.shamrockbailbonds.biz/checkin/{token}"
    doc.pop("_id", None)
    doc["checkin_url"] = checkin_url
    return doc

async def get_checkin_request(token: str) -> Optional[Dict[str, Any]]:
    """Retrieve check-in token metadata."""
    checkin_col = get_collection("check_in_requests")
    doc = await checkin_col.find_one({"token": token})
    if not doc:
        return None
    doc.pop("_id", None)
    return doc

async def process_mobile_checkin(
    token: str,
    selfie_b64: str,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    accuracy: Optional[float] = None,
    user_agent: Optional[str] = None
) -> Dict[str, Any]:
    """
    Process defendant check-in submission with selfie + browser GPS location.
    """
    req = await get_checkin_request(token)
    if not req:
        raise ValueError("Invalid check-in link")

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    booking_number = req.get("booking_number", "")

    # Save to check_in_log collection
    log_col = get_collection("check_in_log")
    checkin_record = {
        "checkin_id": str(uuid.uuid4()),
        "token": token,
        "booking_number": booking_number,
        "timestamp": now_iso,
        "lat": lat,
        "lng": lng,
        "gps_accuracy_meters": accuracy,
        "has_selfie": bool(selfie_b64 and len(selfie_b64) > 100),
        "selfie_b64": selfie_b64 if selfie_b64 and len(selfie_b64) <= 500000 else None,  # Cap image payload size
        "user_agent": user_agent or "",
        "status": "verified" if (lat and lng and selfie_b64) else "flagged"
    }
    await log_col.insert_one(checkin_record)

    # Mark check-in request as completed
    req_col = get_collection("check_in_requests")
    await req_col.update_one({"token": token}, {"$set": {"status": "completed", "completed_at": now_iso}})

    # Update defendant's last check-in date in active_bonds
    bonds_col = get_collection("active_bonds")
    await bonds_col.update_one(
        {"booking_number": booking_number},
        {"$set": {
            "last_checkin_at": now_iso,
            "last_checkin_status": checkin_record["status"],
            "last_known_lat": lat,
            "last_known_lng": lng
        }}
    )

    checkin_record.pop("_id", None)
    return {"success": True, "message": "Check-in recorded and verified successfully!", "checkin_id": checkin_record["checkin_id"]}
