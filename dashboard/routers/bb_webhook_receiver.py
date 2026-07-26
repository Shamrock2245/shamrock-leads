from __future__ import annotations

"""
ShamrockLeads — BlueBubbles Webhook Receiver
=============================================
Real-time event handler for BlueBubbles Server webhooks.

Replaces the 30-second inbox polling loop with an instant push-based
architecture. The BlueBubbles server on the office iMac POSTs events to
this endpoint the moment they occur.

Architecture
------------
  BlueBubbles Server (iMac)
      │  POST /api/webhooks/bluebubbles
      ▼
  This handler (Quart async)
      ├─ new-message (inbound)   → agent_brain.process_inbound()
      ├─ updated-message         → update delivery/read status in MongoDB
      ├─ typing-indicator        → log / ignore
      └─ chat-read-status-changed → update read receipts in MongoDB

Webhook Registration
--------------------
On startup (or when the BB URL changes), call:
    POST /api/webhooks/bluebubbles/register
This will call BlueBubbles /api/v1/webhook to register our VPS URL.

Endpoints
---------
  POST   /api/webhooks/bluebubbles          — Receive BB event (called by BB server)
  POST   /api/webhooks/bluebubbles/register — Register webhook with BB server
  GET    /api/webhooks/bluebubbles/status   — List registered webhooks
  DELETE /api/webhooks/bluebubbles/<id>     — Remove a webhook registration
"""
import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.routers.agent_brain import process_inbound
from dashboard.routers.bb_private_api import BlueBubblesClient
from dashboard.routers.imessage_automation import _content_hash
from dashboard.extensions import BB_SERVERS, get_bb_server, get_collection, format_phone

logger = logging.getLogger(__name__)

bb_webhook_bp = APIRouter(prefix="/api", tags=["bb_webhook_receiver"])
# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

# Events we want to subscribe to from BlueBubbles
BB_WEBHOOK_EVENTS = [
    "new-message",
    "updated-message",
    "chat-read-status-changed",
    "typing-indicator",
]

# Our VPS public URL — used when registering the webhook with BB server
# Set BB_WEBHOOK_PUBLIC_URL in .env, e.g. "https://178.156.179.237:8088"
_VPS_PUBLIC_URL = os.getenv("BB_WEBHOOK_PUBLIC_URL", "")
_WEBHOOK_PATH = "/api/webhooks/bluebubbles"

# Optional HMAC secret for verifying BB webhook payloads
_BB_WEBHOOK_SECRET = os.getenv("BB_WEBHOOK_SECRET", "")


# ─────────────────────────────────────────────────────────────────────────────
#  Signature Verification
# ─────────────────────────────────────────────────────────────────────────────

def _verify_signature(payload: bytes, signature: str) -> bool:
    """Verify optional HMAC-SHA256 webhook signature.

    BlueBubbles Server does **not** send HMAC signatures by default (only URL +
    event list). If BB_WEBHOOK_SECRET is set but the request has no signature
    header, accept the event (still protected by public HTTPS + secret URL
    knowledge). Only reject when a signature *is* present and does not match.
    """
    if not _BB_WEBHOOK_SECRET:
        return True  # No secret configured — skip verification
    if not signature:
        # BB default webhooks omit signatures — do not drop all inbound events
        return True
    expected = hmac.new(
        _BB_WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    # Accept raw hex or sha256=<hex> forms
    sig = (signature or "").strip()
    if sig.lower().startswith("sha256="):
        sig = sig.split("=", 1)[1].strip()
    return hmac.compare_digest(expected, sig)


# ─────────────────────────────────────────────────────────────────────────────
#  Event Handlers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_bb_message(event_data: dict | None) -> dict:
    """Normalize BlueBubbles webhook / poll message shapes.

    Official BB Server posts::

        { "type": "new-message", "data": { "guid": "...", "text": "...", "isFromMe": false, "handle": {...} } }

    i.e. ``data`` *is* the message. Some proxies or older builds nest further
    as ``data.message`` or ``data.data``. Accept all of these.
    """
    if not isinstance(event_data, dict):
        return {}
    # Prefer nested wrappers when present without top-level message identity
    nested = event_data.get("message") or event_data.get("data")
    if (
        isinstance(nested, dict)
        and not event_data.get("guid")
        and "isFromMe" not in event_data
        and not event_data.get("text")
    ):
        return nested
    # Standard BB shape: data *is* the message
    if any(k in event_data for k in ("guid", "text", "isFromMe", "handle", "chats", "dateCreated")):
        return event_data
    if isinstance(nested, dict):
        return nested
    return event_data


async def _handle_new_message(event_data: dict, db) -> dict:
    """Process a new-message event from BlueBubbles.

    Mirrors the logic previously in _poll_inbox_once() but triggered
    instantly via webhook instead of every 30 seconds.
    """
    message = _extract_bb_message(event_data)
    if not message:
        return {"processed": False, "reason": "no_message_in_payload"}

    # Only process inbound messages (is_from_me = False)
    # BB uses isFromMe; tolerate is_from_me aliases
    is_from_me = message.get("isFromMe", message.get("is_from_me", True))
    if is_from_me:
        return {"processed": False, "reason": "outbound_message_skipped"}

    # Extract message details
    msg_guid = str(message.get("guid") or message.get("originalROWID") or "")
    msg_text = message.get("text", "") or message.get("subject", "") or ""
    chats = message.get("chats") or []
    chat = chats[0] if isinstance(chats, list) and chats else {}
    if not isinstance(chat, dict):
        chat = {}
    chat_guid = chat.get("guid", "") or message.get("chatGuid", "") or message.get("chat_guid", "")
    handle = message.get("handle") or {}
    if isinstance(handle, str):
        sender_address = handle
    else:
        sender_address = (handle.get("address", "") if isinstance(handle, dict) else "") or ""
    if not sender_address:
        # Fallbacks used by some BB builds / SMS
        sender_address = (
            message.get("address")
            or message.get("handleId")
            or (chat.get("chatIdentifier") if chat else "")
            or ""
        )
        # chatIdentifier may be "any;-;+1..." — strip prefix
        if ";-;" in str(sender_address):
            sender_address = str(sender_address).split(";-;")[-1]
    sender_phone = format_phone(sender_address)

    if not sender_phone:
        logger.warning(
            "BB webhook: could not parse sender phone from handle=%r chat=%r",
            handle, chat_guid,
        )
        return {"processed": False, "reason": "no_sender_phone"}

    if not msg_text.strip():
        # Reactions / stickers may have empty text — still surface a marker
        # so the thread updates (better than silent drop).
        assoc = message.get("associatedMessageType")
        if assoc:
            msg_text = f"[reaction:{assoc}]"
        else:
            return {"processed": False, "reason": "empty_message"}

    # ── STOP / Opt-Out Detection (must run before any other processing) ──────
    # Honour STOP, UNSUBSCRIBE, QUIT, CANCEL, END, STOP ALL (case-insensitive)
    _STOP_KEYWORDS = {"stop", "unsubscribe", "quit", "cancel", "end", "stop all", "optout", "opt out"}
    msg_normalised = msg_text.strip().lower()
    if msg_normalised in _STOP_KEYWORDS or any(
        msg_normalised.startswith(kw) for kw in _STOP_KEYWORDS
    ):
        # Mark the phone as opted-out across all outreach collections
        opted_out_at = datetime.now(timezone.utc).isoformat()
        outreach_coll_stop = get_collection("imessage_outreach")
        bonds_coll_stop = get_collection("prospective_bonds")
        seqs_coll_stop = get_collection("outreach_sequences")
        # 1. Flag all active sequences for this phone as stopped
        await seqs_coll_stop.update_many(
            {"phone": {"$in": [sender_phone, sender_phone.replace("+1", "")]}, "status": "active"},
            {"$set": {"status": "stopped", "stopped_at": opted_out_at, "stop_reason": "STOP_keyword"}},
        )
        # 2. Flag the prospective bond as opted-out
        await bonds_coll_stop.update_many(
            {"$or": [
                {"indemnitor.phone": sender_phone},
                {"indemnitor.phone": sender_phone.replace("+1", "")},
            ]},
            {"$set": {"opted_out": True, "opted_out_at": opted_out_at}},
        )
        # 3. Log the opt-out event
        await outreach_coll_stop.insert_one({
            "recipient_phone": sender_phone,
            "message": msg_text,
            "chat_guid": chat_guid,
            "bb_message_guid": msg_guid,
            "content_hash": _content_hash(sender_phone, msg_text, message.get("dateCreated")),
            "direction": "inbound",
            "status": "opted_out",
            "category": "opt_out",
            "sent_at": opted_out_at,
            "source": "webhook",
        })
        logger.warning(
            "🛑 STOP received from ...%s — opted out and stopped all sequences",
            sender_phone[-4:],
        )
        return {"processed": True, "opted_out": True}
    # ─────────────────────────────────────────────────────────────────────────

    # ── Layer 1: GUID dedup — avoid processing the same message twice ──
    outreach_coll = get_collection("imessage_outreach")
    existing = await outreach_coll.find_one({"bb_message_guid": msg_guid})
    if existing:
        return {"processed": False, "reason": "already_processed"}

    # ── Layer 2: Content-hash dedup (catches BB Issue #765 — re-emitted messages) ──
    msg_date_ms = message.get("dateCreated")
    chash = _content_hash(sender_phone, msg_text, msg_date_ms)
    existing_content = await outreach_coll.find_one({"content_hash": chash})
    if existing_content:
        logger.info(
            "🔁 Webhook content-hash dedup caught duplicate from ...%s (GUID %s, hash %s)",
            sender_phone[-4:], msg_guid[:12], chash[:8]
        )
        return {"processed": False, "reason": "content_hash_duplicate"}

    # Match to an active prospective bond
    bonds_coll = get_collection("prospective_bonds")
    phone_digits = sender_phone.replace("+1", "").replace("+", "")
    bond = await bonds_coll.find_one({
        "$or": [
            {"indemnitor.phone": sender_phone},
            {"indemnitor.phone": phone_digits},
            {"indemnitor.phone": f"+1{phone_digits}"},
            {"indemnitor.phone": {"$regex": phone_digits[-10:] if len(phone_digits) >= 10 else phone_digits}},
        ],
        "status": "active",
    })

    # Determine which BB server this came from (based on chat_guid prefix)
    bb_server = get_bb_server(chat_guid.split(";-;")[-1] if ";-;" in chat_guid else "")
    bb_client = None
    if bb_server:
        bb_client = BlueBubblesClient(bb_server["url"], bb_server["password"])

    # Prefer BlueBubbles dateCreated (ms) for correct thread ordering
    sent_at = datetime.now(timezone.utc).isoformat()
    if isinstance(msg_date_ms, (int, float)) and msg_date_ms > 0:
        try:
            # BB dateCreated is often Apple Cocoa ns since 2001 or unix ms — try ms first
            ts = float(msg_date_ms)
            if ts > 1e14:  # nanoseconds-ish
                ts = ts / 1e6
            if ts > 1e12:  # already ms
                sent_at = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).isoformat()
            elif ts > 1e9:  # seconds
                sent_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            pass

    if bond:
        # Run the AI agent brain (also logs inbound + optional auto-reply to Mongo)
        config_coll = get_collection("outreach_config")
        config = await config_coll.find_one({"type": "auto_reply"}, {"_id": 0}) or {}

        agent_result = await process_inbound(
            phone=sender_phone,
            message_text=msg_text,
            chat_guid=chat_guid,
            message_guid=msg_guid,
            bond_doc=bond,
            db=db,
            config=config,
            bb_client=bb_client,
            content_hash=chash,
        )

        # process_inbound already inserted the inbound row — enrich it for inbox UI
        _intent = agent_result.get("intent", "")
        _category_map = {
            "intake_inquiry": "intake",
            "interested": "intake",
            "question": "intake",
            "info_provided": "intake",
            "checkin": "checkin",
            "check_in": "checkin",
            "geo_response": "geo",
            "payment": "payment",
            "court": "court",
        }
        _category = _category_map.get(_intent, "general")
        await outreach_coll.update_one(
            {"bb_message_guid": msg_guid},
            {"$set": {
                "category": _category,
                "unread": True,
                "source": "webhook",
                "contact_name": bond.get("defendant_name") or bond.get("indemnitor", {}).get("name") or "",
                "booking_number": bond.get("booking_number", ""),
                "responded": agent_result.get("responded", False),
                "sent_at": sent_at,
            }},
            upsert=False,
        )

        logger.info(
            "📨 Webhook: inbound from %s → intent=%s responded=%s",
            sender_phone[-4:], agent_result.get("intent"), agent_result.get("responded")
        )

        # Real-time dashboard events — SLiMessage.onInboundMessage refreshes
        # the open thread so replies appear in the conversation immediately.
        try:
            from dashboard.routers.events import publish_event
            _evt_payload = {
                "phone_last4": sender_phone[-4:] if sender_phone else "",
                "phone": sender_phone,
                "booking_number": bond.get("booking_number", ""),
                "defendant_name": bond.get("defendant_name", ""),
                "message": msg_text[:120],
                "preview": msg_text[:80],
                "intent": _intent,
                "category": _category,
                "responded": agent_result.get("responded", False),
                "matched": True,
                "sent_at": sent_at,
            }
            await publish_event("message_received", _evt_payload)
            await publish_event("new_reply", _evt_payload)
        except Exception:
            pass

        return {"processed": True, "matched": True, "agent_result": agent_result}

    else:
        # Unmatched inbound — log for manual review (single insert)
        await outreach_coll.insert_one({
            "recipient_phone": sender_phone,
            "message": msg_text,
            "chat_guid": chat_guid,
            "bb_message_guid": msg_guid,
            "content_hash": chash,
            "direction": "inbound",
            "status": "unmatched",
            "category": "general",
            "unread": True,
            "sent_at": sent_at,
            "source": "webhook",
        })
        logger.info("❓ Webhook: unmatched inbound from %s: %s", sender_phone[-4:], msg_text[:50])

        try:
            from dashboard.routers.events import publish_event
            await publish_event("message_received", {
                "phone_last4": sender_phone[-4:] if sender_phone else "",
                "phone": sender_phone,
                "message": msg_text[:120],
                "preview": msg_text[:80],
                "category": "general",
                "matched": False,
                "sent_at": sent_at,
            })
        except Exception:
            pass

        return {"processed": True, "matched": False}


async def _handle_updated_message(event_data: dict) -> dict:
    """Update delivery and read receipt status in MongoDB."""
    message = event_data.get("message") or event_data.get("data") or {}
    msg_guid = message.get("guid", "")
    if not msg_guid:
        return {"processed": False}

    outreach_coll = get_collection("imessage_outreach")
    update = {}
    if message.get("dateDelivered") or message.get("isDelivered"):
        update["delivered"] = True
        update["date_delivered"] = message.get("dateDelivered")
    if message.get("dateRead") or message.get("isRead"):
        update["read"] = True
        update["date_read"] = message.get("dateRead")

    if update:
        await outreach_coll.update_one(
            {"bb_message_guid": msg_guid},
            {"$set": update}
        )
        logger.debug("📬 Updated message status for %s: %s", msg_guid[:8], update)

    return {"processed": True, "updated": bool(update)}


# ─────────────────────────────────────────────────────────────────────────────
#  Webhook Receiver Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@bb_webhook_bp.post("/webhooks/bluebubbles")
async def receive_bb_event(request: Request):
    """Receive a real-time event from the BlueBubbles server.

    BlueBubbles POSTs a JSON payload with:
        { "type": "new-message", "data": { ... } }
    """
    # Signature verification
    raw_body = await request.body()
    signature = request.headers.get("x-bb-signature", "")
    if not _verify_signature(raw_body, signature):
        logger.warning("BB webhook: invalid signature — rejecting")
        return JSONResponse({"error": "Invalid signature"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    if not payload:
        return JSONResponse({"error": "Empty payload"}, status_code=400)

    event_type = payload.get("type", "")
    event_data = payload.get("data", payload)

    logger.info("📡 BB webhook event: %s", event_type)

    # Route to appropriate handler
    from dashboard.extensions import get_db
    db = get_db()

    if event_type == "new-message":
        result = await _handle_new_message(event_data, db)
    elif event_type == "updated-message":
        result = await _handle_updated_message(event_data)
    elif event_type in ("typing-indicator", "chat-read-status-changed"):
        # Log but no action needed
        result = {"processed": True, "action": "logged_only"}
    else:
        result = {"processed": False, "reason": f"unhandled_event_type: {event_type}"}

    return {"success": True, "event_type": event_type, "result": result}


# ─────────────────────────────────────────────────────────────────────────────
#  Webhook Registration Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@bb_webhook_bp.post("/webhooks/bluebubbles/register")
async def register_bb_webhook(request: Request):
    """Register our VPS webhook URL with the BlueBubbles server.

    Call this endpoint once after startup (or when the BB ngrok tunnel URL changes).
    It is idempotent — safe to call multiple times.

    Body (optional):
        { "vps_url": "https://178.156.179.237:8088" }  — override the public URL
    """
    data = await request.json() or {}
    vps_url = data.get("vps_url", _VPS_PUBLIC_URL).rstrip("/")
    if not vps_url:
        return JSONResponse(status_code=400, content={
            "success": False,
            "error": "BB_WEBHOOK_PUBLIC_URL not set — provide vps_url in body or set env var"
        })

    webhook_url = f"{vps_url}{_WEBHOOK_PATH}"
    results = []

    for suffix, server in BB_SERVERS.items():
        client = BlueBubblesClient(server["url"], server["password"])
        result = await client.ensure_webhook(webhook_url, BB_WEBHOOK_EVENTS)
        results.append({
            "server": server["label"],
            "suffix": suffix,
            "webhook_url": webhook_url,
            "success": result.get("success", False),
            "already_existed": result.get("already_existed", False),
            "data": result.get("data", {}),
        })
        logger.info(
            "BB webhook registration for %s: success=%s already_existed=%s",
            server["label"], result.get("success"), result.get("already_existed")
        )

    return {"success": True, "registrations": results}


@bb_webhook_bp.get("/webhooks/bluebubbles/status")
async def bb_webhook_status():
    """List all webhooks registered on each BlueBubbles server."""
    results = {}
    for suffix, server in BB_SERVERS.items():
        client = BlueBubblesClient(server["url"], server["password"])
        result = await client.list_webhooks()
        results[server["label"]] = {
            "success": result.get("success", False),
            "webhooks": result.get("data", []),
        }
    return {"success": True, "servers": results}


@bb_webhook_bp.delete("/webhooks/bluebubbles/{webhook_id}")
async def delete_bb_webhook(webhook_id: int):
    """Remove a webhook registration from all BB servers."""
    results = {}
    for suffix, server in BB_SERVERS.items():
        client = BlueBubblesClient(server["url"], server["password"])
        result = await client.delete_webhook(webhook_id)
        results[server["label"]] = result.get("success", False)
    return {"success": True, "results": results}