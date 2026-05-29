from __future__ import annotations

"""
ShamrockLeads — BlueBubbles iMessage-First Prospecting
======================================================
"The First Mover" — Reach out to relatives of newly arrested defendants
via iMessage (BlueBubbles) before competitors, prioritizing iPhone users
for higher deliverability and trust (blue bubble effect).

Architecture
------------
  New arrest detected (Scout / scraper)
      │
      ▼
  IRB / Contact Discovery → relative phone numbers
      │
      ▼
  BlueBubbles with `any;-;` chat GUID prefix
      ├─ iPhone detected → iMessage (blue bubble)
      └─ Not iPhone     → SMS via Mac Messages relay (green bubble)
      │
      ▼
  AI Agent Brain handles replies (same as inbound flow)

Key Differentiators vs. Twilio-only approach
---------------------------------------------
  - iMessage = blue bubble = higher open rate and trust
  - Typing indicators make the bot feel human
  - Tapback reactions on replies show engagement
  - Group chat capability for multi-indemnitor cases
  - Read receipts confirm the message was seen

Endpoints
---------
  POST   /api/prospecting/outreach         — Send outreach to a list of phones
  POST   /api/prospecting/batch            — Batch outreach for a new arrest
  GET    /api/prospecting/queue            — View pending outreach queue
  POST   /api/prospecting/retry-failed     — Retry failed iMessage sends
  GET    /api/prospecting/stats            — Outreach stats by channel
"""
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.routers.bb_private_api import BlueBubblesClient
from dashboard.extensions import BB_SERVERS, get_bb_server, get_collection, format_phone

logger = logging.getLogger(__name__)

bb_prospecting_bp = APIRouter(prefix="/api", tags=["bb_prospecting"])
# ─────────────────────────────────────────────────────────────────────────────
#  Outreach Templates (iMessage-optimized — conversational, short)
# ─────────────────────────────────────────────────────────────────────────────

OUTREACH_TEMPLATES = {
    "initial_family": (
        "Hi {name}! This is Shamrock Bail Bonds 🍀 — we're a local bail bond "
        "agency in {county} County. We noticed {defendant_name} was recently "
        "booked and wanted to reach out in case you're looking to help get them "
        "home. Bond is ${bond_amount:,.0f}. Reply and we can walk you through "
        "everything — it's quick and easy."
    ),
    "initial_no_name": (
        "Hi! Shamrock Bail Bonds here 🍀 — {defendant_name} was just booked "
        "into {county} County jail (bond: ${bond_amount:,.0f}). "
        "If you're family or a close friend looking to help, reply and we'll "
        "get you all the info you need. We make the process fast and simple."
    ),
    "high_value": (
        "Hi {name}! This is Shamrock Bail Bonds 🍀 — {defendant_name} was "
        "just booked into {county} County. Bond is set at ${bond_amount:,.0f}. "
        "We specialize in bonds at this level and can often get people home "
        "within hours. Reply or call (239) 955-0178 anytime — we're available "
        "24/7."
    ),
    "follow_up_1": (
        "Hi {name} — just following up from Shamrock Bail Bonds 🍀 about "
        "{defendant_name}. Are you still looking for help with their bond? "
        "We're here whenever you're ready."
    ),
    "follow_up_2": (
        "Hi {name}, Shamrock Bail Bonds here one more time 🍀 — just wanted "
        "to make sure you have our info if you need us for {defendant_name}. "
        "Call or text anytime: (239) 955-0178."
    ),
    "magic_link": (
        "Hi {name}! Shamrock Bail Bonds here 🍀 — to get started on "
        "{defendant_name}'s bond, you can fill out our quick intake form here: "
        "{intake_url} — takes about 2 minutes and we'll take it from there!"
    ),
}


def _build_outreach_message(template_key: str, context: dict) -> str:
    """Build an outreach message from a template and context dict."""
    template = OUTREACH_TEMPLATES.get(template_key, OUTREACH_TEMPLATES["initial_no_name"])
    try:
        return template.format(**context)
    except KeyError:
        # Fall back to no-name template if context is incomplete
        return OUTREACH_TEMPLATES["initial_no_name"].format(**context)


# ─────────────────────────────────────────────────────────────────────────────
#  Core Outreach Logic
# ─────────────────────────────────────────────────────────────────────────────

async def send_prospecting_outreach(
    phones: list[str],
    defendant_name: str,
    county: str,
    booking_number: str,
    bond_amount: float = 0.0,
    charges: str = "",
    template_key: str = "initial_family",
    contact_names: Optional[dict] = None,  # phone → name mapping
    intake_url: str = "",
) -> dict:
    """Send prospecting outreach to a list of phone numbers.

    Checks each number for iMessage availability first. iPhone users get
    a BlueBubbles iMessage; others are queued for Twilio SMS fallback.

    Returns:
        {
            "total": int,
            "imessage_sent": int,
            "imessage_failed": int,
            "sms_fallback_needed": list[str],
            "results": list[dict],
        }
    """
    if contact_names is None:
        contact_names = {}

    # Get BB client
    bb_server = next(iter(BB_SERVERS.values()), None) if BB_SERVERS else None
    if not bb_server:
        logger.error("No BlueBubbles server configured for prospecting outreach")
        return {
            "total": len(phones),
            "imessage_sent": 0,
            "imessage_failed": 0,
            "sms_fallback_needed": phones,
            "results": [],
            "error": "No BlueBubbles server configured",
        }

    bb_client = BlueBubblesClient(bb_server["url"], bb_server["password"])
    outreach_coll = get_collection("imessage_outreach")
    prospective_coll = get_collection("prospective_bonds")

    results = []
    imessage_sent = 0
    imessage_failed = 0
    sms_sent = 0
    sms_fallback = []

    for phone in phones:
        phone = format_phone(phone)
        if not phone:
            continue

        # ── Opt-out guard: skip phones that have sent STOP ────────────────────
        phone_digits = phone.replace("+1", "").replace("+", "")
        opted_out = await prospective_coll.find_one({
            "$or": [
                {"indemnitor.phone": phone},
                {"indemnitor.phone": phone_digits},
            ],
            "opted_out": True,
        })
        if opted_out:
            results.append({"phone": phone, "channel": "skipped", "status": "opted_out"})
            logger.info("[Prospecting] Skipping opted-out phone ...%s", phone[-4:])
            continue
        # ─────────────────────────────────────────────────────────────────────

        name = contact_names.get(phone, "")
        context = {
            "name": name or "there",
            "defendant_name": defendant_name,
            "county": county.replace(" County", "").title(),
            "bond_amount": bond_amount,
            "charges": charges,
            "intake_url": intake_url or "https://shamrockbailbonds.com/intake",
        }

        # Choose template based on bond amount
        if template_key == "initial_family" and bond_amount >= 10000:
            effective_template = "high_value"
        elif not name:
            effective_template = "initial_no_name"
        else:
            effective_template = template_key

        message = _build_outreach_message(effective_template, context)
        chat_guid = f"any;-;{phone}"

        # Check iMessage availability (for channel reporting only — BB routes automatically)
        try:
            avail = await bb_client.check_imessage_availability(phone)
            is_imessage = avail.get("available", False)
        except Exception:
            is_imessage = False
        channel = "imessage" if is_imessage else "sms"

        outreach_doc = {
            "booking_number": booking_number,
            "defendant_name": defendant_name,
            "county": county,
            "bond_amount": bond_amount,
            "recipient_phone": phone,
            "recipient_name": name,
            "message": message,
            "template_used": effective_template,
            "chat_guid": chat_guid,
            "channel": channel,
            "direction": "outbound",
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "source": "prospecting",
        }

        send_success = False
        actual_channel = "failed"

        # Send via BB with any;-; (auto-routes to iMessage or SMS natively)
        result = await bb_client.send_human_like(chat_guid, message, typing_delay=3.0)
        if result.get("success"):
            outreach_doc["status"] = "sent"
            outreach_doc["bb_message_guid"] = (result.get("data") or {}).get("guid", "")
            send_success = True
            actual_channel = channel
            if is_imessage:
                imessage_sent += 1
            else:
                sms_sent += 1
            logger.info("📤 Prospecting %s sent to ...%s for %s", channel, phone[-4:], defendant_name)
        else:
            outreach_doc["status"] = "failed"
            outreach_doc["error"] = result.get("error", "unknown")
            imessage_failed += 1
            sms_fallback.append(phone)
            logger.error("❌ BB send failed for ...%s: %s", phone[-4:], result.get("error"))

        # Create/update prospective bond record if any channel succeeded
        if send_success:
            await prospective_coll.update_one(
                {"booking_number": booking_number},
                {
                    "$setOnInsert": {
                        "booking_number": booking_number,
                        "defendant_name": defendant_name,
                        "county": county,
                        "bond_amount": bond_amount,
                        "charges": charges,
                        "status": "active",
                        "stage": "contacted",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                    "$push": {
                        "outreach_log": {
                            "phone": phone,
                            "channel": actual_channel,
                            "message": message,
                            "sent_at": datetime.now(timezone.utc).isoformat(),
                        }
                    },
                    "$set": {"last_contacted_at": datetime.now(timezone.utc).isoformat()},
                },
                upsert=True,
            )

        await outreach_coll.insert_one(outreach_doc)
        results.append({
            "phone": phone,
            "channel": actual_channel,
            "status": outreach_doc["status"],
        })

    return {
        "total": len(phones),
        "imessage_sent": imessage_sent,
        "imessage_failed": imessage_failed,
        "sms_sent": sms_sent,
        "sms_fallback_needed": sms_fallback,
        "results": results,
    }


async def send_group_chat_outreach(
    phones: list[str],
    defendant_name: str,
    county: str,
    booking_number: str,
    bond_amount: float = 0.0,
    group_name: Optional[str] = None,
) -> dict:
    """Create a group iMessage chat with multiple indemnitors for a bond case.

    This is useful when a defendant has multiple family members who all need
    to be kept in the loop (e.g., spouse + parent co-signing).

    Returns the group chat GUID for future messages.
    """
    bb_server = next(iter(BB_SERVERS.values()), None) if BB_SERVERS else None
    if not bb_server:
        return {"success": False, "error": "No BlueBubbles server configured"}

    bb_client = BlueBubblesClient(bb_server["url"], bb_server["password"])

    # Check all phones for iMessage availability
    imessage_phones = []
    for phone in phones:
        phone = format_phone(phone)
        avail = await bb_client.check_imessage_availability(phone)
        if avail.get("available"):
            imessage_phones.append(phone)

    if len(imessage_phones) < 2:
        return {
            "success": False,
            "error": "Need at least 2 iMessage-capable phones for a group chat",
            "imessage_phones": imessage_phones,
        }

    # Create the group chat
    display_name = group_name or f"Shamrock — {defendant_name.split()[0]} Bond"
    result = await bb_client.create_group_chat(imessage_phones, display_name=display_name)

    if result.get("success"):
        group_guid = (result.get("data") or {}).get("guid", "")

        # Send initial message to the group
        intro_message = (
            f"Hi everyone! This is Shamrock Bail Bonds 🍀 — we've added you all "
            f"to this group so we can keep everyone updated on {defendant_name}'s "
            f"bond (${bond_amount:,.0f} in {county} County). "
            f"Reply here with any questions and we'll get back to you right away."
        )
        await bb_client.send_human_like(group_guid, intro_message, typing_delay=3.0)

        # Log the group chat
        groups_coll = get_collection("bb_group_chats")
        await groups_coll.insert_one({
            "booking_number": booking_number,
            "defendant_name": defendant_name,
            "county": county,
            "group_guid": group_guid,
            "group_name": display_name,
            "participants": imessage_phones,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        logger.info("👥 Group chat created for %s: %s (%d participants)",
                    defendant_name, group_guid[:12], len(imessage_phones))

        return {
            "success": True,
            "group_guid": group_guid,
            "participants": imessage_phones,
            "group_name": display_name,
        }

    return {"success": False, "error": result.get("error", "Failed to create group chat")}


# ─────────────────────────────────────────────────────────────────────────────
#  API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@bb_prospecting_bp.post("/prospecting/outreach")
async def api_prospecting_outreach(request: Request):
    """Send iMessage-first prospecting outreach to a list of phone numbers.

    Body:
        {
            "phones": ["+12395550178", "+12395550314"],
            "defendant_name": "JOHN SMITH",
            "county": "Lee",
            "booking_number": "2024-00123",
            "bond_amount": 5000.00,
            "charges": "DUI",
            "template": "initial_family",     (optional)
            "contact_names": {"+12395550178": "Jane"},  (optional)
            "intake_url": "https://..."        (optional)
        }
    """
    try:
        data = await request.json() or {}
        phones = data.get("phones", [])
        defendant_name = (data.get("defendant_name") or "").strip()
        county = (data.get("county") or "").strip()
        booking_number = (data.get("booking_number") or "").strip()

        if not phones or not defendant_name or not county or not booking_number:
            return JSONResponse(status_code=400, content={
                "success": False,
                "error": "phones, defendant_name, county, and booking_number are required"
            })

        result = await send_prospecting_outreach(
            phones=phones,
            defendant_name=defendant_name,
            county=county,
            booking_number=booking_number,
            bond_amount=float(data.get("bond_amount", 0)),
            charges=data.get("charges", ""),
            template_key=data.get("template", "initial_family"),
            contact_names=data.get("contact_names", {}),
            intake_url=data.get("intake_url", ""),
        )

        return {"success": True, **result}

    except Exception as e:
        logger.error("Prospecting outreach error: %s", e, exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@bb_prospecting_bp.post("/prospecting/group-chat")
async def api_create_group_chat(request: Request):
    """Create a multi-indemnitor group iMessage chat for a bond case.

    Body:
        {
            "phones": ["+12395550178", "+12395550314"],
            "defendant_name": "JOHN SMITH",
            "county": "Lee",
            "booking_number": "2024-00123",
            "bond_amount": 5000.00,
            "group_name": "Shamrock — John Bond"  (optional)
        }
    """
    try:
        data = await request.json() or {}
        phones = data.get("phones", [])
        defendant_name = (data.get("defendant_name") or "").strip()
        county = (data.get("county") or "").strip()
        booking_number = (data.get("booking_number") or "").strip()

        if len(phones) < 2 or not defendant_name:
            return JSONResponse(status_code=400, content={
                "success": False,
                "error": "At least 2 phones and defendant_name are required"
            })

        result = await send_group_chat_outreach(
            phones=phones,
            defendant_name=defendant_name,
            county=county,
            booking_number=booking_number,
            bond_amount=float(data.get("bond_amount", 0)),
            group_name=data.get("group_name"),
        )

        return result

    except Exception as e:
        logger.error("Group chat creation error: %s", e, exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@bb_prospecting_bp.get("/prospecting/stats")
async def api_prospecting_stats():
    """Get outreach statistics broken down by channel."""
    try:
        outreach_coll = get_collection("imessage_outreach")
        total = await outreach_coll.count_documents({"source": "prospecting"})
        imessage = await outreach_coll.count_documents({"source": "prospecting", "channel": "imessage", "status": "sent"})
        sms = await outreach_coll.count_documents({"source": "prospecting", "channel": "sms_fallback"})
        failed = await outreach_coll.count_documents({"source": "prospecting", "status": "failed"})

        return {
            "success": True,
            "stats": {
                "total_outreach": total,
                "imessage_sent": imessage,
                "sms_fallback": sms,
                "failed": failed,
                "imessage_rate": round(imessage / total * 100, 1) if total > 0 else 0,
            }
        }

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@bb_prospecting_bp.get("/prospecting/templates")
async def api_prospecting_templates():
    """List available outreach message templates."""
    return {
        "success": True,
        "templates": {k: v for k, v in OUTREACH_TEMPLATES.items()},
    }