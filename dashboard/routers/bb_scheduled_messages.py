# ── AUTO-MIGRATED: Quart Blueprint → FastAPI APIRouter (v3) ──
# _qp = dict(request.query_params) injected into fns that read query params.
# Review each endpoint and move _qp.get() calls to typed fn signatures.

"""
ShamrockLeads — BlueBubbles Scheduled Message Engine
=====================================================
Leverages BlueBubbles Server-side message scheduling to send court date
reminders, payment reminders, and check-in messages via iMessage.

This is a significant upgrade from the Twilio-only approach because:
  1. iMessage reminders appear in the same thread as all prior conversations
  2. Read receipts confirm the defendant/indemnitor actually saw the reminder
  3. No per-message cost (unlike Twilio SMS)
  4. Typing indicators and reactions make follow-ups feel human

Reminder Types
--------------
  court_reminder_72h   — 3 days before court date
  court_reminder_24h   — 24 hours before court date
  court_reminder_2h    — 2 hours before court date (day-of)
  payment_reminder_3d  — 3 days before payment due
  payment_reminder_1d  — 1 day before payment due
  payment_overdue      — Payment is overdue
  check_in_weekly      — Weekly check-in for long-running bonds
  walk_out_watch       — Notify when defendant is released from custody

Architecture
------------
  This module works in two modes:
    A) BlueBubbles Server-side scheduling (preferred):
       - Uses POST /api/v1/message/schedule on the BB server
       - The Mac handles delivery even if the VPS is temporarily down
       - Best for fixed-time reminders (court dates, payment due dates)

    B) VPS-side scheduling (APScheduler fallback):
       - Used when BB server-side scheduling is not available
       - Stored in MongoDB `scheduled_messages` collection
       - Processed by the existing scheduler.py cron jobs

Endpoints
---------
  POST   /api/bb-schedule/court-reminders    — Schedule all court date reminders
  POST   /api/bb-schedule/payment-reminders  — Schedule payment reminders
  POST   /api/bb-schedule/check-in           — Schedule a check-in message
  GET    /api/bb-schedule/pending            — View all pending scheduled messages
  DELETE /api/bb-schedule/<id>               — Cancel a scheduled message
  POST   /api/bb-schedule/send-now           — Send a scheduled message immediately
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.api.bb_private_api import BlueBubblesClient
from dashboard.extensions import BB_SERVERS, get_bb_server, get_collection, format_phone

logger = logging.getLogger(__name__)

bb_schedule_bp = APIRouter(prefix="/api", tags=["bb_scheduled_messages"])
# ─────────────────────────────────────────────────────────────────────────────
#  Message Templates
# ─────────────────────────────────────────────────────────────────────────────

REMINDER_TEMPLATES = {
    "court_reminder_72h": (
        "Hi {name}! Shamrock Bail Bonds here 🍀 — just a reminder that "
        "{defendant_name} has a court date coming up in 3 days: "
        "{court_date} at {court_time}, {court_location}. "
        "Case #{case_number}. Reply if you have any questions!"
    ),
    "court_reminder_24h": (
        "⚖️ Court date TOMORROW for {defendant_name}! "
        "{court_date} at {court_time} — {court_location}. "
        "Case #{case_number}. Please make sure they're there on time. "
        "Missing court will result in a bond forfeiture. — Shamrock Bail Bonds 🍀"
    ),
    "court_reminder_2h": (
        "🚨 {defendant_name}'s court appearance is in 2 HOURS — "
        "{court_time} at {court_location}. "
        "Please confirm they are on their way. Reply 'confirmed' or call us at "
        "(239) 955-0178 if there's an issue. — Shamrock Bail Bonds 🍀"
    ),
    "payment_reminder_3d": (
        "Hi {name}! Shamrock Bail Bonds here 🍀 — your next bond payment of "
        "${amount:,.2f} is due in 3 days ({due_date}). "
        "Reply to arrange payment or call (239) 955-0178."
    ),
    "payment_reminder_1d": (
        "Hi {name} — your bond payment of ${amount:,.2f} is due TOMORROW "
        "({due_date}). Please call (239) 955-0178 or reply here to arrange "
        "payment and avoid any late fees. — Shamrock Bail Bonds 🍀"
    ),
    "payment_overdue": (
        "Hi {name} — your bond payment of ${amount:,.2f} was due on {due_date} "
        "and is now overdue. Please call (239) 955-0178 immediately to avoid "
        "further action. — Shamrock Bail Bonds 🍀"
    ),
    "check_in_weekly": (
        "Hi {name}! Just checking in from Shamrock Bail Bonds 🍀 — "
        "everything going okay with {defendant_name}? "
        "Next court date: {next_court_date}. Reply anytime if you need anything!"
    ),
    "walk_out_watch": (
        "Hi {name}! Great news — {defendant_name} has been released from "
        "{county} County jail 🎉 "
        "Remember: they must appear for all court dates. "
        "Next date: {next_court_date} at {court_location}. "
        "— Shamrock Bail Bonds 🍀"
    ),
    "document_ready": (
        "Hi {name}! Your bond documents for {defendant_name} are ready to sign. "
        "Click here to review and sign: {signing_url} 📝 "
        "Takes about 2 minutes. — Shamrock Bail Bonds 🍀"
    ),
}


def _build_reminder_message(template_key: str, context: dict) -> str:
    """Build a reminder message from a template and context dict."""
    template = REMINDER_TEMPLATES.get(template_key, "")
    if not template:
        return f"Reminder from Shamrock Bail Bonds regarding {context.get('defendant_name', 'your case')}."
    try:
        return template.format(**context)
    except KeyError as e:
        logger.warning("Missing template key %s for %s", e, template_key)
        return template.replace("{" + str(e).strip("'") + "}", "")


def _to_epoch_ms(dt: datetime) -> int:
    """Convert a datetime to epoch milliseconds."""
    return int(dt.timestamp() * 1000)


# ─────────────────────────────────────────────────────────────────────────────
#  Core Scheduling Logic
# ─────────────────────────────────────────────────────────────────────────────

async def schedule_court_reminders_for_case(
    booking_number: str,
    defendant_name: str,
    phone: str,
    court_date: datetime,
    court_location: str,
    case_number: str,
    indemnitor_name: str = "",
) -> dict:
    """Schedule the full set of court date reminders for a bond case.

    Creates 3 reminders:
      - 72 hours before court
      - 24 hours before court
      - 2 hours before court (day-of)

    Uses BlueBubbles server-side scheduling when available; falls back to
    MongoDB-based VPS scheduling.

    Returns:
        { "scheduled": list[dict], "bb_server_side": bool }
    """
    phone = format_phone(phone)
    chat_guid = f"any;-;{phone}"

    bb_server = next(iter(BB_SERVERS.values()), None) if BB_SERVERS else None
    bb_client = BlueBubblesClient(bb_server["url"], bb_server["password"]) if bb_server else None

    # Check iMessage availability (for channel reporting only)
    is_imessage = False
    if bb_client:
        try:
            avail = await bb_client.check_imessage_availability(phone)
            is_imessage = avail.get("available", False)
        except Exception as _avail_err:
            logger.debug("[bb_scheduled] iMessage availability check failed for %s: %s", phone[-4:] if phone else "?", _avail_err)

    context = {
        "name": indemnitor_name.split()[0] if indemnitor_name else "there",
        "defendant_name": defendant_name,
        "court_date": court_date.strftime("%A, %B %d, %Y"),
        "court_time": court_date.strftime("%I:%M %p"),
        "court_location": court_location,
        "case_number": case_number,
    }

    reminders = [
        ("court_reminder_72h", court_date - timedelta(hours=72)),
        ("court_reminder_24h", court_date - timedelta(hours=24)),
        ("court_reminder_2h", court_date - timedelta(hours=2)),
    ]

    scheduled = []
    scheduled_coll = get_collection("scheduled_messages")

    for template_key, send_at in reminders:
        # Skip if send time is in the past
        if send_at <= datetime.now(timezone.utc):
            logger.info("Skipping %s for %s — send time is in the past", template_key, booking_number)
            continue

        message = _build_reminder_message(template_key, context)
        send_at_ms = _to_epoch_ms(send_at)

        schedule_doc = {
            "booking_number": booking_number,
            "defendant_name": defendant_name,
            "phone": phone,
            "chat_guid": chat_guid,
            "channel": "imessage" if is_imessage else "sms",
            "template_key": template_key,
            "message": message,
            "send_at": send_at.isoformat(),
            "send_at_ms": send_at_ms,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reminder_type": "court",
        }

        # Try BB server-side scheduling for iMessage
        bb_schedule_id = None
        if is_imessage and bb_client:
            result = await bb_client.schedule_message(chat_guid, message, send_at_ms)
            if result.get("success"):
                bb_schedule_id = (result.get("data") or {}).get("id")
                schedule_doc["bb_schedule_id"] = bb_schedule_id
                schedule_doc["scheduling_method"] = "bb_server"
                logger.info("📅 BB server-side scheduled %s for %s at %s",
                            template_key, defendant_name, send_at.strftime("%m/%d %H:%M"))
            else:
                schedule_doc["scheduling_method"] = "vps_fallback"
        else:
            schedule_doc["scheduling_method"] = "vps_fallback"

        await scheduled_coll.insert_one(schedule_doc)
        scheduled.append({
            "template_key": template_key,
            "send_at": send_at.isoformat(),
            "channel": schedule_doc["channel"],
            "scheduling_method": schedule_doc["scheduling_method"],
            "bb_schedule_id": bb_schedule_id,
        })

    return {
        "booking_number": booking_number,
        "scheduled": scheduled,
        "total_scheduled": len(scheduled),
        "bb_server_side": is_imessage and bb_client is not None,
    }


async def schedule_payment_reminders_for_case(
    booking_number: str,
    defendant_name: str,
    phone: str,
    due_date: datetime,
    amount: float,
    indemnitor_name: str = "",
    overdue: bool = False,
) -> dict:
    """Schedule payment reminders for a bond case.

    Creates up to 2 reminders:
      - 3 days before payment due
      - 1 day before payment due
    Or 1 overdue notice if overdue=True.

    Returns:
        { "scheduled": list[dict], "bb_server_side": bool }
    """
    phone = format_phone(phone)
    chat_guid = f"any;-;{phone}"
    bb_server = next(iter(BB_SERVERS.values()), None) if BB_SERVERS else None
    bb_client = BlueBubblesClient(bb_server["url"], bb_server["password"]) if bb_server else None
    is_imessage = False
    if bb_client:
        try:
            avail = await bb_client.check_imessage_availability(phone)
            is_imessage = avail.get("available", False)
        except Exception:
            pass
    first_name = (indemnitor_name or defendant_name or "").split()[0] or "there"
    context = {
        "name": first_name,
        "defendant_name": defendant_name,
        "amount": amount,
        "due_date": due_date.strftime("%B %d, %Y"),
    }
    if overdue:
        reminders = [("payment_overdue", datetime.now(timezone.utc))]
    else:
        reminders = [
            ("payment_reminder_3d", due_date - timedelta(days=3)),
            ("payment_reminder_1d", due_date - timedelta(days=1)),
        ]
    scheduled = []
    scheduled_coll = get_collection("scheduled_messages")
    for template_key, send_at in reminders:
        if not overdue and send_at <= datetime.now(timezone.utc):
            logger.info("Skipping %s for %s — send time is in the past", template_key, booking_number)
            continue
        message = _build_reminder_message(template_key, context)
        send_at_ms = _to_epoch_ms(send_at)
        schedule_doc = {
            "booking_number": booking_number,
            "defendant_name": defendant_name,
            "phone": phone,
            "chat_guid": chat_guid,
            "channel": "imessage" if is_imessage else "sms",
            "template_key": template_key,
            "message": message,
            "send_at": send_at.isoformat(),
            "send_at_ms": send_at_ms,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reminder_type": "payment",
            "amount": amount,
        }
        bb_schedule_id = None
        if is_imessage and bb_client and not overdue:
            result = await bb_client.schedule_message(chat_guid, message, send_at_ms)
            if result.get("success"):
                bb_schedule_id = (result.get("data") or {}).get("id")
                schedule_doc["bb_schedule_id"] = bb_schedule_id
                schedule_doc["scheduling_method"] = "bb_server"
                logger.info("📅 BB server-side scheduled %s for %s at %s",
                            template_key, defendant_name, send_at.strftime("%m/%d %H:%M"))
            else:
                schedule_doc["scheduling_method"] = "vps_fallback"
        else:
            schedule_doc["scheduling_method"] = "vps_fallback"
        await scheduled_coll.insert_one(schedule_doc)
        scheduled.append({
            "template_key": template_key,
            "send_at": send_at.isoformat(),
            "channel": schedule_doc["channel"],
            "scheduling_method": schedule_doc["scheduling_method"],
        })
    return {
        "booking_number": booking_number,
        "scheduled": scheduled,
        "total_scheduled": len(scheduled),
        "bb_server_side": is_imessage and bb_client is not None,
    }


@bb_schedule_bp.post("/bb-schedule/payment-reminders")
async def api_schedule_payment_reminders():
    """Schedule payment reminders for a bond case.

    Body (JSON):
      booking_number  — required
      defendant_name  — required
      phone           — required (indemnitor or defendant)
      due_date        — required (ISO 8601 datetime)
      amount          — required (float, payment amount in dollars)
      indemnitor_name — optional
      overdue         — optional bool (default false); if true, sends overdue notice immediately
    """
    try:
        data = await request.json() or {}
        booking_number = data.get("booking_number", "").strip()
        defendant_name = data.get("defendant_name", "").strip()
        phone = data.get("phone", "").strip()
        due_date_str = data.get("due_date", "").strip()
        amount = float(data.get("amount", 0) or 0)
        indemnitor_name = data.get("indemnitor_name", "").strip()
        overdue = bool(data.get("overdue", False))
        if not booking_number or not phone or not due_date_str:
            return {"success": False, "error": "booking_number, phone, and due_date are required"}, 400
        due_date = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
        if due_date.tzinfo is None:
            due_date = due_date.replace(tzinfo=timezone.utc)
        result = await schedule_payment_reminders_for_case(
            booking_number=booking_number,
            defendant_name=defendant_name,
            phone=phone,
            due_date=due_date,
            amount=amount,
            indemnitor_name=indemnitor_name,
            overdue=overdue,
        )
        return {"success": True, **result}, 201
    except Exception as e:
        logger.error("Payment reminder scheduling error: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}, 500


async def process_vps_scheduled_messages() -> dict:
    """Process VPS-side scheduled messages that are due to be sent.

    Called by the scheduler (APScheduler) every 5 minutes.
    Only processes messages with scheduling_method = "vps_fallback" or "sms_fallback".
    """
    now = datetime.now(timezone.utc)
    scheduled_coll = get_collection("scheduled_messages")

    due_messages = await scheduled_coll.find({
        "status": "pending",
        "scheduling_method": {"$in": ["vps_fallback", "sms_fallback"]},
        "send_at": {"$lte": now.isoformat()},
    }).to_list(length=100)

    sent = 0
    failed = 0

    bb_server = next(iter(BB_SERVERS.values()), None) if BB_SERVERS else None
    bb_client = BlueBubblesClient(bb_server["url"], bb_server["password"]) if bb_server else None

    for msg in due_messages:
        phone = msg.get("phone", "")
        chat_guid = msg.get("chat_guid", f"any;-;{phone}")
        message = msg.get("message", "")
        channel = msg.get("channel", "sms")

        if bb_client:
            # Send via BB with any;-; (auto-routes iMessage or SMS)
            result = await bb_client.send_human_like(chat_guid, message, typing_delay=2.0)
            success = result.get("success", False)
        else:
            success = False

        status = "sent" if success else "failed"
        await scheduled_coll.update_one(
            {"_id": msg["_id"]},
            {"$set": {"status": status, "processed_at": now.isoformat()}}
        )

        if success:
            sent += 1
        else:
            failed += 1

    return {"processed": len(due_messages), "sent": sent, "failed": failed}


# ─────────────────────────────────────────────────────────────────────────────
#  API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@bb_schedule_bp.post("/bb-schedule/court-reminders")
async def api_schedule_court_reminders():
    """Schedule the full set of court date reminders for a bond case.

    Body:
        {
            "booking_number": "2024-00123",
            "defendant_name": "JOHN SMITH",
            "phone": "+12395550178",
            "court_date": "2024-03-15T09:00:00",
            "court_location": "Lee County Justice Center, Courtroom 4A",
            "case_number": "24-CF-001234",
            "indemnitor_name": "Jane Smith"  (optional)
        }
    """
    try:
        data = await request.json() or {}
        booking_number = (data.get("booking_number") or "").strip()
        defendant_name = (data.get("defendant_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        court_date_str = (data.get("court_date") or "").strip()
        court_location = (data.get("court_location") or "").strip()
        case_number = (data.get("case_number") or "").strip()

        if not all([booking_number, defendant_name, phone, court_date_str, court_location, case_number]):
            return {"success": False, "error": "All fields required"}, 400

        court_date = datetime.fromisoformat(court_date_str)
        if court_date.tzinfo is None:
            court_date = court_date.replace(tzinfo=timezone.utc)

        result = await schedule_court_reminders_for_case(
            booking_number=booking_number,
            defendant_name=defendant_name,
            phone=phone,
            court_date=court_date,
            court_location=court_location,
            case_number=case_number,
            indemnitor_name=data.get("indemnitor_name", ""),
        )

        return {"success": True, **result}

    except Exception as e:
        logger.error("Schedule court reminders error: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}, 500


@bb_schedule_bp.post("/bb-schedule/document-ready")
async def api_schedule_document_ready():
    """Send a 'documents ready to sign' notification via iMessage.

    Body:
        {
            "phone": "+12395550178",
            "indemnitor_name": "Jane Smith",
            "defendant_name": "JOHN SMITH",
            "signing_url": "https://app.signnow.com/..."
        }
    """
    try:
        data = await request.json() or {}
        phone = format_phone(data.get("phone", ""))
        indemnitor_name = data.get("indemnitor_name", "")
        defendant_name = data.get("defendant_name", "")
        signing_url = data.get("signing_url", "")

        if not phone or not defendant_name or not signing_url:
            return {"success": False, "error": "phone, defendant_name, signing_url required"}, 400

        bb_server = next(iter(BB_SERVERS.values()), None) if BB_SERVERS else None
        if not bb_server:
            return {"success": False, "error": "No BlueBubbles server configured"}, 503

        bb_client = BlueBubblesClient(bb_server["url"], bb_server["password"])
        chat_guid = f"any;-;{phone}"

        context = {
            "name": indemnitor_name.split()[0] if indemnitor_name else "there",
            "defendant_name": defendant_name,
            "signing_url": signing_url,
        }
        message = _build_reminder_message("document_ready", context)

        result = await bb_client.send_human_like(chat_guid, message, typing_delay=2.0)

        return {"success": result.get("success", False), "result": result}

    except Exception as e:
        logger.error("Document ready notification error: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}, 500


@bb_schedule_bp.get("/bb-schedule/pending")
async def api_pending_scheduled():
    """View all pending scheduled messages."""
    _qp = dict(request.query_params)
    try:
        limit = int(_qp.get("limit", 50))
        booking_number = _qp.get("booking_number", "")

        query = {"status": "pending"}
        if booking_number:
            query["booking_number"] = booking_number

        scheduled_coll = get_collection("scheduled_messages")
        messages = await scheduled_coll.find(
            query, {"_id": 0}
        ).sort("send_at", 1).limit(limit).to_list(length=limit)

        return {"success": True, "count": len(messages), "messages": messages}

    except Exception as e:
        return {"success": False, "error": str(e)}, 500


@bb_schedule_bp.post("/bb-schedule/process")
async def api_process_scheduled():
    """Process VPS-side scheduled messages that are due. Called by cron."""
    try:
        result = await process_vps_scheduled_messages()
        return {"success": True, **result}
    except Exception as e:
        logger.error("Process scheduled messages error: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}, 500
