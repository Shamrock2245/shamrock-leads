
"""
ShamrockLeads — Re-Arrest Notification Engine
==============================================
"The Loyalty Flow" — When a former defendant is re-arrested, automatically
notify their previous indemnitors (family members / co-signers) via iMessage
(BlueBubbles) with a warm, empathetic message.

Business Logic
--------------
1. The Scout (Node-RED) or the county scrapers detect a new arrest.
2. This module checks MongoDB's `bonds` collection for any historical bond
   where the defendant name + DOB matches the newly arrested person.
3. If a match is found, we extract the previous indemnitor(s)' phone numbers.
4. We send via BlueBubbles using `any;-;` chat GUID prefix, which auto-routes
   to iMessage for iPhones and SMS for everyone else.
5. All notifications are logged to `rearrest_notifications` collection.

This serves two purposes:
  a) Genuine customer service — the family already knows us and trusts us.
  b) New business — they are the most likely people to bond them out again.

Endpoints
---------
  POST   /api/rearrest/check        — Check a new arrest against historical bonds
  POST   /api/rearrest/notify       — Send re-arrest notification (manual trigger)
  GET    /api/rearrest/history      — View notification history
  GET    /api/rearrest/stats        — Notification stats (sent, converted, etc.)
  GET    /api/rearrest/pending      — Dashboard: fetch unreviewed alerts
  PATCH  /api/rearrest/<id>/dismiss — Dashboard: mark alert as reviewed
  PATCH  /api/rearrest/<id>/contacted — Dashboard: mark indemnitor as contacted
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from dashboard.api.bb_private_api import BlueBubblesClient
from dashboard.extensions import BB_SERVERS, get_bb_server, get_collection, format_phone

logger = logging.getLogger(__name__)

rearrest_bp = APIRouter(prefix="/api", tags=["rearrest_notifier"])
# ─────────────────────────────────────────────────────────────────────────────
#  Message Templates
# ─────────────────────────────────────────────────────────────────────────────

REARREST_TEMPLATES = {
    "default": (
        "Hi {indemnitor_first_name}! This is Shamrock Bail Bonds 🍀 — we helped "
        "you with {defendant_name}'s bond back in {prior_year}. "
        "We wanted to give you a heads-up as a courtesy: {defendant_first_name} "
        "was just booked into {county} County. "
        "If you'd like us to look into the bond amount and charges, just reply "
        "and we'll get right on it. We're here for you."
    ),
    "same_year": (
        "Hi {indemnitor_first_name}! Shamrock Bail Bonds here 🍀 — we worked "
        "with you on {defendant_name}'s bond earlier this year. "
        "We wanted to let you know right away: {defendant_first_name} was just "
        "booked into {county} County jail. "
        "Reply anytime and we'll pull up the details for you."
    ),
    "no_name": (
        "Hi! This is Shamrock Bail Bonds 🍀 — we helped with a bond for you "
        "previously. We wanted to let you know that {defendant_name} was just "
        "booked into {county} County. "
        "Reply if you'd like us to look into the bond details."
    ),
}


def _build_rearrest_message(indemnitor: dict, defendant_name: str,
                             county: str, prior_bond_date: str) -> str:
    """Build a personalized re-arrest notification message."""
    indemnitor_first = (indemnitor.get("name") or indemnitor.get("first_name") or "").split()[0]
    defendant_parts = defendant_name.split()
    defendant_first = defendant_parts[0] if defendant_parts else defendant_name

    try:
        prior_year = datetime.fromisoformat(prior_bond_date).year
        current_year = datetime.now().year
        same_year = (prior_year == current_year)
    except Exception:
        prior_year = "a previous case"
        same_year = False

    if not indemnitor_first:
        template = REARREST_TEMPLATES["no_name"]
    elif same_year:
        template = REARREST_TEMPLATES["same_year"]
    else:
        template = REARREST_TEMPLATES["default"]

    return template.format(
        indemnitor_first_name=indemnitor_first,
        defendant_name=defendant_name,
        defendant_first_name=defendant_first,
        county=county.replace(" County", "").title(),
        prior_year=prior_year,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Core Logic
# ─────────────────────────────────────────────────────────────────────────────

async def check_and_notify_rearrest(
    defendant_name: str,
    county: str,
    booking_number: str,
    dob: Optional[str] = None,
    bond_amount: Optional[float] = None,
    charges: Optional[str] = None,
) -> dict:
    """Main entry point: check for prior bonds and send notifications.

    Called by:
      - first_appearance_watcher.py when a bond is set on a watched record
      - The Scout (Node-RED) via POST /api/rearrest/check
      - Any scraper that detects a new arrest

    Returns:
        {
            "prior_bonds_found": int,
            "notifications_sent": int,
            "notifications_failed": int,
            "fallback_needed": list[str],  # phones needing Twilio SMS
        }
    """
    bonds_coll = get_collection("bonds")
    notifications_coll = get_collection("rearrest_notifications")

    # ── 1. Find prior bonds for this defendant ──────────────────────────────
    # Normalize name for matching (case-insensitive, strip extra spaces)
    name_parts = defendant_name.upper().split()
    if len(name_parts) >= 2:
        # Try last-name first match (common jail roster format: "SMITH JOHN")
        query_name = " ".join(name_parts)
    else:
        query_name = defendant_name.upper()

    match_filter = {
        "$or": [
            {"defendant_name": {"$regex": query_name, "$options": "i"}},
        ]
    }
    if dob:
        match_filter["$or"].append({"defendant_dob": dob})

    prior_bonds = await bonds_coll.find(match_filter, {"_id": 0}).to_list(length=20)

    if not prior_bonds:
        logger.info("🔍 Re-arrest check: no prior bonds for %s", defendant_name)
        return {
            "prior_bonds_found": 0,
            "notifications_sent": 0,
            "notifications_failed": 0,
            "fallback_needed": [],
        }

    logger.info("🔔 Re-arrest detected: %s — %d prior bond(s) found", defendant_name, len(prior_bonds))

    # ── 2. Collect unique indemnitors across all prior bonds ────────────────
    seen_phones = set()
    indemnitors_to_notify = []

    for bond in prior_bonds:
        indemnitor = bond.get("indemnitor") or {}
        raw_phone = indemnitor.get("phone", "")
        phone = format_phone(raw_phone)
        if phone and phone not in seen_phones:
            seen_phones.add(phone)
            indemnitors_to_notify.append({
                "phone": phone,
                "indemnitor": indemnitor,
                "prior_bond": bond,
            })

    if not indemnitors_to_notify:
        return {
            "prior_bonds_found": len(prior_bonds),
            "notifications_sent": 0,
            "notifications_failed": 0,
            "fallback_needed": [],
        }

    # ── 3. Get BB client ────────────────────────────────────────────────────
    bb_server = next(iter(BB_SERVERS.values()), None) if BB_SERVERS else None
    bb_client = BlueBubblesClient(bb_server["url"], bb_server["password"]) if bb_server else None

    # ── 4. Send notifications ───────────────────────────────────────────────
    sent = 0
    failed = 0
    fallback_needed = []

    for item in indemnitors_to_notify:
        phone = item["phone"]
        indemnitor = item["indemnitor"]
        prior_bond = item["prior_bond"]
        prior_date = prior_bond.get("created_at", prior_bond.get("bond_date", ""))

        message = _build_rearrest_message(indemnitor, defendant_name, county, prior_date)
        chat_guid = f"any;-;{phone}"

        # Check iMessage availability (for channel reporting only)
        channel = "sms"
        if bb_client:
            try:
                avail = await bb_client.check_imessage_availability(phone)
                if avail.get("available", False):
                    channel = "imessage"
            except Exception:
                pass

        notification_doc = {
            "defendant_name": defendant_name,
            "booking_number": booking_number,
            "county": county,
            "bond_amount": bond_amount,
            "charges": charges,
            "indemnitor_phone": phone,
            "indemnitor_name": indemnitor.get("name", ""),
            "prior_booking_number": prior_bond.get("booking_number", ""),
            "message": message,
            "channel": channel,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        }

        if bb_client:
            # Send via BB with any;-; (auto-routes to iMessage or SMS)
            result = await bb_client.send_human_like(chat_guid, message, typing_delay=2.5)
            if result.get("success"):
                notification_doc["status"] = "sent"
                notification_doc["bb_message_guid"] = (result.get("data") or {}).get("guid", "")
                sent += 1
                logger.info("✅ Re-arrest notification sent via %s to ...%s", channel, phone[-4:])
            else:
                notification_doc["status"] = "failed"
                notification_doc["error"] = result.get("error", "unknown")
                failed += 1
                fallback_needed.append(phone)
                logger.warning("❌ BB send failed for ...%s: %s", phone[-4:], result.get("error"))
        else:
            notification_doc["status"] = "failed"
            notification_doc["error"] = "no_bb_client"
            fallback_needed.append(phone)
            logger.warning("⚠️ No BB client configured — cannot send to ...%s", phone[-4:])

        await notifications_coll.insert_one(notification_doc)

    return {
        "prior_bonds_found": len(prior_bonds),
        "notifications_sent": sent,
        "notifications_failed": failed,
        "fallback_needed": fallback_needed,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@rearrest_bp.post("/rearrest/check")
async def api_rearrest_check(request: Request):
    """Check a new arrest against historical bonds and send notifications.

    Body:
        {
            "defendant_name": "JOHN SMITH",
            "county": "Lee",
            "booking_number": "2024-00123",
            "dob": "1985-03-15",          (optional)
            "bond_amount": 5000.00,       (optional)
            "charges": "DUI, BATTERY"     (optional)
        }
    """
    try:
        data = await request.json() or {}
        defendant_name = (data.get("defendant_name") or "").strip()
        county = (data.get("county") or "").strip()
        booking_number = (data.get("booking_number") or "").strip()

        if not defendant_name or not county or not booking_number:
            return {
                "success": False,
                "error": "defendant_name, county, and booking_number are required"
            }, 400

        result = await check_and_notify_rearrest(
            defendant_name=defendant_name,
            county=county,
            booking_number=booking_number,
            dob=data.get("dob"),
            bond_amount=data.get("bond_amount"),
            charges=data.get("charges"),
        )

        return {"success": True, **result}

    except Exception as e:
        logger.error("Re-arrest check error: %s", e, exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@rearrest_bp.post("/rearrest/notify")
async def api_rearrest_notify(request: Request):
    """Manually send a re-arrest notification to a specific phone number.

    Body:
        {
            "phone": "+12395550178",
            "defendant_name": "JOHN SMITH",
            "county": "Lee",
            "booking_number": "2024-00123",
            "message": "Custom message..."  (optional — overrides template)
        }
    """
    try:
        data = await request.json() or {}
        phone = format_phone(data.get("phone", ""))
        defendant_name = (data.get("defendant_name") or "").strip()
        county = (data.get("county") or "").strip()
        booking_number = (data.get("booking_number") or "").strip()
        custom_message = data.get("message", "")

        if not phone or not defendant_name:
            return JSONResponse({"success": False, "error": "phone and defendant_name required"}, status_code=400)

        bb_server = next(iter(BB_SERVERS.values()), None) if BB_SERVERS else None
        if not bb_server:
            return JSONResponse({"success": False, "error": "No BlueBubbles server configured"}, status_code=503)

        bb_client = BlueBubblesClient(bb_server["url"], bb_server["password"])
        chat_guid = f"any;-;{phone}"

        message = custom_message or _build_rearrest_message(
            {"name": ""}, defendant_name, county, ""
        )

        result = await bb_client.send_human_like(chat_guid, message, typing_delay=2.5)

        notifications_coll = get_collection("rearrest_notifications")
        await notifications_coll.insert_one({
            "defendant_name": defendant_name,
            "booking_number": booking_number,
            "county": county,
            "indemnitor_phone": phone,
            "message": message,
            "channel": "imessage",
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "status": "sent" if result.get("success") else "failed",
            "bb_message_guid": (result.get("data") or {}).get("guid", ""),
            "manual_trigger": True,
        })

        return {"success": result.get("success", False), "result": result}

    except Exception as e:
        logger.error("Manual re-arrest notify error: %s", e, exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@rearrest_bp.get("/rearrest/history")
async def api_rearrest_history(defendant_name: str = Query(default=""), county: str = Query(default=""), status: str = Query(default=""), limit: int = Query(default=50)):
    """Get re-arrest notification history with optional filters.
    Query params:
        defendant_name, county, status, limit (default 50)
    """
    try:
        defendant_name = defendant_name
        county = county
        status = status
        limit = int(limit)

        query = {}
        if defendant_name:
            query["defendant_name"] = {"$regex": defendant_name, "$options": "i"}
        if county:
            query["county"] = {"$regex": county, "$options": "i"}
        if status:
            query["status"] = status

        notifications_coll = get_collection("rearrest_notifications")
        cursor = notifications_coll.find(query, {"_id": 0}).sort("sent_at", -1).limit(limit)
        notifications = await cursor.to_list(length=limit)

        return {"success": True, "count": len(notifications), "notifications": notifications}

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@rearrest_bp.get("/rearrest/stats")

@rearrest_bp.get("/rearrest/stats")
async def api_rearrest_stats():
    """Get aggregate statistics for re-arrest notifications."""
    try:
        notifications_coll = get_collection("rearrest_notifications")
        total = await notifications_coll.count_documents({})
        sent = await notifications_coll.count_documents({"status": "sent"})
        failed = await notifications_coll.count_documents({"status": "failed"})
        fallback = await notifications_coll.count_documents({"status": "fallback_needed"})

        return {
            "success": True,
            "stats": {
                "total_notifications": total,
                "sent_via_imessage": sent,
                "failed": failed,
                "fallback_to_sms": fallback,
                "success_rate": round(sent / total * 100, 1) if total > 0 else 0,
            }
        }

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
#  Dashboard Alert Endpoints (consumed by sl-rearrest.js)
# ─────────────────────────────────────────────────────────────────────────────

@rearrest_bp.get("/rearrest/pending")
async def api_rearrest_pending(limit: int = Query(default=25)):
    """Get unreviewed re-arrest alerts for the dashboard Command Center.
    Returns rearrest_notifications with status 'pending_review' (written by
    the synchronous RearrestChecker in the scraper pipeline).

    Query params:
        limit (default 25)
    """
    try:
        limit = int(limit)
        notifications_coll = get_collection("rearrest_notifications")

        cursor = notifications_coll.find(
            {"status": "pending_review"},
        ).sort("created_at", -1).limit(limit)

        alerts = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            # Ensure datetime fields are serializable
            for dt_field in ("created_at", "updated_at", "reviewed_at", "contacted_at", "prior_bond_date"):
                val = doc.get(dt_field)
                if hasattr(val, "isoformat"):
                    doc[dt_field] = val.isoformat()
            alerts.append(doc)

        return {"success": True, "count": len(alerts), "alerts": alerts}

    except Exception as e:
        logger.error("Rearrest pending fetch error: %s", e, exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@rearrest_bp.patch("/rearrest/<notification_id>/dismiss")

@rearrest_bp.patch("/rearrest/<notification_id>/dismiss")
async def api_rearrest_dismiss(request: Request, notification_id):
    """Mark a re-arrest alert as reviewed/dismissed.

    Body (optional):
        {"reviewed_by": "Agent Name"}
    """
    try:
        data = await request.json() or {}
        reviewed_by = data.get("reviewed_by", "staff")

        notifications_coll = get_collection("rearrest_notifications")
        result = await notifications_coll.update_one(
            {"_id": ObjectId(notification_id)},
            {
                "$set": {
                    "status": "reviewed",
                    "reviewed_by": reviewed_by,
                    "reviewed_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )

        if result.modified_count == 0:
            return JSONResponse({"success": False, "error": "Notification not found"}, status_code=404)

        logger.info("✅ Rearrest alert %s dismissed by %s", notification_id, reviewed_by)
        return {"success": True, "status": "reviewed"}

    except Exception as e:
        logger.error("Rearrest dismiss error: %s", e, exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@rearrest_bp.patch("/rearrest/<notification_id>/contacted")
async def api_rearrest_contacted(request: Request, notification_id):
    """Mark a re-arrest alert as 'contacted' — indemnitor was reached.

    Body (optional):
        {"contacted_by": "Agent Name", "notes": "Called, left voicemail"}
    """
    try:
        data = await request.json() or {}
        contacted_by = data.get("contacted_by", "staff")
        notes = data.get("notes", "")

        notifications_coll = get_collection("rearrest_notifications")
        result = await notifications_coll.update_one(
            {"_id": ObjectId(notification_id)},
            {
                "$set": {
                    "status": "contacted",
                    "contacted_by": contacted_by,
                    "contacted_at": datetime.now(timezone.utc),
                    "contact_notes": notes,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )

        if result.modified_count == 0:
            return JSONResponse({"success": False, "error": "Notification not found"}, status_code=404)

        logger.info("📞 Rearrest alert %s marked contacted by %s", notification_id, contacted_by)
        return {"success": True, "status": "contacted"}

    except Exception as e:
        logger.error("Rearrest contacted error: %s", e, exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

