# ── AUTO-MIGRATED: Quart Blueprint → FastAPI APIRouter (v3) ──
# _qp = dict(request.query_params) injected into fns that read query params.
# Review each endpoint and move _qp.get() calls to typed fn signatures.

"""
ShamrockLeads — Legacy API Blueprint
Migrated from app.py Flask endpoints to Quart async handlers.

Endpoints:
  /api/health-full       — Full MongoDB health check
  /api/cleanup           — Trigger manual data cleanup
  /api/db-health         — MongoDB Atlas M0 storage health
  /api/leads/update-custody  — Manual custody status override
  /api/imessage/status   — BlueBubbles server status
  /api/imessage/send     — Send iMessage via BlueBubbles
  /api/imessage/history/<booking_number> — iMessage outreach history
  /api/imessage/templates — Outreach templates
  /api/config/bluebubbles-url — Dynamic URL sync from iMac
"""
from __future__ import annotations

import logging
import os
import re as re_mod
import secrets
import uuid
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from dashboard.extensions import (
    get_collection, get_db, BB_SERVERS,
    get_bb_server, format_phone, init_bluebubbles,
    update_bb_url, BB_CONFIG_API_KEY,
)

legacy_bp = APIRouter(prefix="/api", tags=["legacy"])
# ═══════════════════════════════════════════════════════════════════════════════
#  BlueBubbles Dynamic URL Sync (iMac → VPS, no restart needed)
# ═══════════════════════════════════════════════════════════════════════════════

@legacy_bp.post("/config/bluebubbles-url")
async def update_bluebubbles_url(request: Request):
    """Accept a new ngrok tunnel URL from the iMac sync script."""
    auth = request.headers.get("X-API-Key", "")
    if auth != BB_CONFIG_API_KEY:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body = await request.json()
    suffix = body.get("suffix", "0178")
    url = body.get("url", "").strip()

    if not url:
        return JSONResponse({"error": "url is required"}, status_code=400)

    servers = update_bb_url(suffix, url)
    phone_key = f"239955{suffix}"
    active = servers.get(phone_key, {})

    return {
        "success": True,
        "suffix": suffix,
        "url": url,
        "active_servers": {k: {"url": v["url"], "label": v["label"]} for k, v in servers.items()},
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Health / System
# ═══════════════════════════════════════════════════════════════════════════════

@legacy_bp.get("/health-full")
async def api_health_full():
    """Full system health — checks MongoDB connectivity."""
    arrests = get_collection("arrests")
    mongo_ok = False
    try:
        db = get_db()
        await db.command("ping")
        mongo_ok = True
    except Exception as _ping_err:
        logger.warning("[legacy] MongoDB ping failed: %s", _ping_err)

    total_arrests = 0
    active_counties = 0
    try:
        total_arrests = await arrests.estimated_document_count()
        active_counties = len(await arrests.distinct("county"))
    except Exception as _count_err:
        logger.warning("[legacy] Stats count failed: %s", _count_err)

    status = "ok" if mongo_ok else "degraded"
    code = 200 if mongo_ok else 503
    return {
        "status": status,
        "mongodb": "connected" if mongo_ok else "disconnected",
        "total_arrests": total_arrests,
        "active_counties": active_counties,
        "uptime_check": datetime.now(timezone.utc).isoformat(),
    }, code


@legacy_bp.post("/cleanup")
async def api_cleanup():
    """Trigger manual data cleanup. Returns purge statistics."""
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        from maintenance.cleanup import run_cleanup
        result = run_cleanup()
        return {"success": True, **result}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@legacy_bp.get("/db-health")
async def api_db_health():
    """MongoDB Atlas storage health — monitors against 512MB M0 limit."""
    try:
        db = get_db()
        db_stats = await db.command("dbStats")
        data_size_mb = round(db_stats.get("dataSize", 0) / (1024 * 1024), 2)
        storage_size_mb = round(db_stats.get("storageSize", 0) / (1024 * 1024), 2)
        index_size_mb = round(db_stats.get("indexSize", 0) / (1024 * 1024), 2)

        M0_LIMIT_MB = 512
        usage_pct = round(storage_size_mb / M0_LIMIT_MB * 100, 1)

        # Per-collection breakdown
        collections_info = []
        for coll_name in ["arrests", "leads", "ingestion_log"]:
            try:
                coll_stats = await db.command("collStats", coll_name)
                collections_info.append({
                    "name": coll_name,
                    "documents": coll_stats.get("count", 0),
                    "data_size_mb": round(coll_stats.get("size", 0) / (1024 * 1024), 2),
                    "storage_size_mb": round(coll_stats.get("storageSize", 0) / (1024 * 1024), 2),
                    "index_size_mb": round(coll_stats.get("totalIndexSize", 0) / (1024 * 1024), 2),
                })
            except Exception:
                collections_info.append({"name": coll_name, "error": "not found"})

        status = "healthy"
        if usage_pct > 85:
            status = "critical"
        elif usage_pct > 70:
            status = "warning"

        return {
            "status": status,
            "limit_mb": M0_LIMIT_MB,
            "data_size_mb": data_size_mb,
            "storage_size_mb": storage_size_mb,
            "index_size_mb": index_size_mb,
            "usage_pct": usage_pct,
            "collections": collections_info,
        }
    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)


# ═══════════════════════════════════════════════════════════════════════════════
#  Manual Custody Status Override
# ═══════════════════════════════════════════════════════════════════════════════

@legacy_bp.post("/leads/update-custody")
async def update_custody(request: Request):
    """Manually override custody status for a defendant."""
    arrests = get_collection("arrests")
    body = await request.json()
    booking_number = body.get("booking_number", "").strip()
    new_status = body.get("custody_status", "").strip()

    if not booking_number:
        return JSONResponse({"error": "booking_number is required"}, status_code=400)

    valid_statuses = ["In Custody", "Not In Custody", "Released", "Bonded Out"]
    if new_status not in valid_statuses:
        return JSONResponse({"error": f"Invalid status. Must be one of: {valid_statuses}"}, status_code=400)

    try:
        existing = await arrests.find_one(
            {"booking_number": booking_number},
            {"status": 1, "custody_overrides": 1}
        )
        if not existing:
            return JSONResponse({"error": f"No record found for booking {booking_number}"}, status_code=404)

        old_status = existing.get("status", "Unknown")

        override_entry = {
            "old_status": old_status,
            "new_status": new_status,
            "changed_at": datetime.now(timezone.utc).isoformat(),
            "changed_by": body.get("changed_by", "dashboard_user"),
        }

        result = await arrests.update_one(
            {"booking_number": booking_number},
            {
                "$set": {
                    "status": new_status,
                    "custody_override": True,
                    "custody_override_at": datetime.now(timezone.utc).isoformat(),
                },
                "$push": {
                    "custody_overrides": override_entry,
                },
            },
        )

        if result.modified_count == 0:
            return JSONResponse({"error": "Record found but not modified"}, status_code=500)

        return {
            "success": True,
            "booking_number": booking_number,
            "old_status": old_status,
            "new_status": new_status,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ═══════════════════════════════════════════════════════════════════════════════
#  BlueBubbles iMessage Outreach Proxy
# ═══════════════════════════════════════════════════════════════════════════════

@legacy_bp.get("/imessage/status")
async def imessage_status():
    """Check status of all configured BlueBubbles servers."""
    import httpx

    if not BB_SERVERS:
        init_bluebubbles()  # Re-try loading config

    if not BB_SERVERS:
        return {"connected": False, "servers": [], "reason": "No BlueBubbles servers configured in .env"}

    servers = []
    any_connected = False
    any_private_api = False
    for phone_key, srv in BB_SERVERS.items():
        entry = {"phone": phone_key, "label": srv["label"], "email": srv["email"], "connected": False}
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{srv['url']}/api/v1/server/info",
                    params={"password": srv["password"]},
                    headers={
                        "ngrok-skip-browser-warning": "true",
                        "User-Agent": "ShamrockLeads-Dashboard/1.0 (BlueBubbles-Client)",
                        "Accept": "application/json",
                    },
                    timeout=5,
                )
                data = r.json()
                if r.status_code == 200:
                    entry["connected"] = True
                    entry["private_api"] = data.get("data", {}).get("private_api", False)
                    entry["os_version"] = data.get("data", {}).get("os_version", "")
                    any_connected = True
                    if entry.get("private_api"):
                        any_private_api = True
        except Exception:
            entry["error"] = "unreachable"
        servers.append(entry)

    return {
        "connected": any_connected,
        "private_api": any_private_api,
        "server_count": len(BB_SERVERS),
        "servers": servers,
    }


@legacy_bp.post("/imessage/send")
async def imessage_send(request: Request):
    """Send an iMessage via BlueBubbles server."""
    import httpx

    if not BB_SERVERS:
        init_bluebubbles()

    if not BB_SERVERS:
        return JSONResponse({"error": "No BlueBubbles servers configured. Set BLUEBUBBLES_URL_0178 and BLUEBUBBLES_PASSWORD_0178 in .env"}, status_code=503)

    body = await request.json()
    phone_raw = body.get("phone", "")
    message = body.get("message", "").strip()
    booking_number = body.get("booking_number", "")
    defendant_name = body.get("defendant_name", "")
    county = body.get("county", "")
    recipient_label = body.get("recipient_label", "Unknown")
    agent_name = body.get("agent_name", "Brendan")
    from_number = body.get("from_number", "2399550178")

    if not phone_raw or not message:
        return JSONResponse({"error": "phone and message are required"}, status_code=400)

    phone = format_phone(phone_raw)
    if not phone:
        return JSONResponse({"error": f"Invalid phone number: {phone_raw}"}, status_code=400)

    srv = get_bb_server(from_number)
    if not srv:
        return JSONResponse({"error": f"No BlueBubbles server configured for {from_number}"}, status_code=503)

    # NOTE: Geo-tracking links are NOT auto-appended to outbound messages.
    # Use /api/tracking/<booking>/send-geo-link for explicit geo-link delivery.
    # This prevents the internal dashboard URL from leaking to clients.

    chat_guid = f"any;-;{phone}"
    temp_guid = f"shamrock-{uuid.uuid4().hex[:16]}"
    imessage_outreach = get_collection("imessage_outreach")

    # ── Dedup Guard: block duplicate outreach within 24h ──
    cooldown_hours = body.get("cooldown_hours", 24)
    if booking_number and not body.get("force_send", False):
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)).isoformat()
        recent = await imessage_outreach.find_one({
            "recipient_phone": phone,
            "booking_number": booking_number,
            "status": "sent",
            "direction": {"$ne": "inbound"},
            "sent_at": {"$gte": cutoff},
        }, {"_id": 0, "sent_at": 1, "message": 1})
        if recent:
            return {
                "success": False,
                "error": "duplicate",
                "detail": f"Already messaged this number for booking {booking_number} within {cooldown_hours}h",
                "last_sent": recent.get("sent_at"),
                "message_preview": (recent.get("message", ""))[:80],
            }, 409

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{srv['url']}/api/v1/message/text",
                params={"password": srv["password"]},
                json={
                    "chatGuid": chat_guid,
                    "tempGuid": temp_guid,
                    "message": message,
                },
                timeout=15,
            )
            bb_resp = r.json()
            success = r.status_code in (200, 201)

        # Extract BB message GUID for unsend/edit capability
        bb_message_guid = ""
        if success:
            bb_data = bb_resp.get("data", {})
            if isinstance(bb_data, dict):
                bb_message_guid = bb_data.get("guid", "")

        # Log to MongoDB
        doc = {
            "booking_number": booking_number,
            "defendant_name": defendant_name,
            "county": county,
            "recipient_phone": phone,
            "recipient_label": recipient_label,
            "message": message,
            "chat_guid": chat_guid,
            "temp_guid": temp_guid,
            "bb_message_guid": bb_message_guid,
            "direction": "outbound",
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "status": "sent" if success else "failed",
            "bb_status_code": r.status_code,
            "bb_error": bb_resp.get("message", "") if not success else "",
            "sent_by": "dashboard",
            "agent_name": agent_name,
            "from_number": from_number,
            "from_email": srv.get("email", ""),
        }
        await imessage_outreach.insert_one(doc)
        doc.pop("_id", None)

        if success:
            return {"success": True, "record": doc}
        else:
            return JSONResponse({"success": False, "error": bb_resp.get("message", "BlueBubbles error"), "record": doc}, status_code=502)

    except httpx.ConnectError:
        return JSONResponse({"error": "Cannot reach BlueBubbles server. Is it running?"}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@legacy_bp.get("/imessage/history/<booking_number>")
async def imessage_history(booking_number):
    """Get outreach message history for a defendant."""
    imessage_outreach = get_collection("imessage_outreach")
    docs = []
    async for doc in imessage_outreach.find(
        {"booking_number": booking_number},
        {"_id": 0},
    ).sort("sent_at", -1).limit(50):
        docs.append(doc)
    return {"messages": docs, "count": len(docs)}


@legacy_bp.get("/imessage/templates")
async def imessage_templates():
    """Return available outreach message templates."""
    templates = [
        {
            "id": "standard",
            "name": "Standard Introduction",
            "body": "Hi, this is {agent} with Shamrock Bail Bonds. I see that {name} is currently in custody in the {county} County Jail. We can help get them home fast with flexible payment plans. Give us a call or reply here.",
        },
        {
            "id": "urgent",
            "name": "Urgent — Significant Bond",
            "body": "Hi, this is {agent} with Shamrock Bail Bonds. {name} is currently being held in {county} County on a significant bond. We specialize in quick releases and flexible payment options. Would you like help?",
        },
        {
            "id": "followup",
            "name": "Follow-Up",
            "body": "Hi, this is {agent} with Shamrock Bail Bonds, just following up about {name} in {county} County. We're still available to help if you'd like to get them home. No obligation to chat.",
        },
        {
            "id": "payment",
            "name": "Payment Plan Offer",
            "body": "Hi, this is {agent} with Shamrock Bail Bonds. We can help bond {name} out of {county} County Jail today. We offer flexible payment plans and fast service. Reply or call us anytime.",
        },
    ]
    return {"templates": templates}
