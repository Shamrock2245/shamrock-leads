
"""
ShamrockLeads — iMessage Automation Blueprint
Background inbox polling, auto-reply orchestration, and Private API proxy.

Endpoints:
  GET    /api/imessage/auto-reply/config       — Get auto-reply configuration
  POST   /api/imessage/auto-reply/config       — Update auto-reply settings
  GET    /api/imessage/inbox                    — Fetch recent inbound messages
  POST   /api/imessage/inbox/poll               — Manually trigger one poll cycle
  POST   /api/imessage/dedup-check              — Check outreach dedup status
  POST   /api/imessage/unsend                   — Unsend a message (Private API)
  POST   /api/imessage/edit                     — Edit a message (Private API)
  POST   /api/imessage/react                    — Tapback reaction (Private API)
  POST   /api/imessage/mark-read                — Mark chat as read
  POST   /api/imessage/typing                   — Start/stop typing indicator
  GET    /api/imessage/message-status/<guid>     — Delivery/read status
  GET    /api/imessage/findmy                   — FindMy device/friend locations
  POST   /api/imessage/send-effect              — Send with iMessage effect

Background:
  start_inbox_poller(app) — asyncio task polling BB every 30s for inbound messages
"""

import asyncio
import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from dashboard.extensions import (
    get_collection, get_db, format_phone,
    BB_SERVERS, get_bb_server, init_bluebubbles,
)
from dashboard.routers.bb_private_api import BlueBubblesClient, EFFECTS, REACTIONS
from dashboard.routers.agent_brain import process_inbound

logger = logging.getLogger(__name__)

imessage_auto_bp = APIRouter(prefix="/api", tags=["imessage_automation"])
# ─────────────────────────────────────────────────────────────────────────────
#  Content-Hash Dedup (protects against BB Issue #765 — re-emitted messages)
# ─────────────────────────────────────────────────────────────────────────────

def _content_hash(sender: str, text: str, timestamp_ms: int | None = None,
                  window_seconds: int = 60) -> str:
    """Generate a dedup hash from sender + normalized text + time bucket.

    BlueBubbles can re-emit old messages with brand-new GUIDs (Issue #765).
    This catches duplicates by hashing the *content* instead of trusting the GUID.
    The timestamp is bucketed into `window_seconds` intervals so that the same
    message arriving within the window produces the same hash.
    """
    normalized = text.strip().lower()
    # Bucket the timestamp (default: 60s window)
    if timestamp_ms:
        bucket = timestamp_ms // (window_seconds * 1000)
    else:
        bucket = int(datetime.now(timezone.utc).timestamp()) // window_seconds
    raw = f"{sender}|{normalized}|{bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]

# ─────────────────────────────────────────────────────────────────────────────
#  Default Config
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "type": "auto_reply",
    "enabled": False,  # Off by default — user enables via dashboard
    "ai_enabled": True,
    "cooldown_minutes": 5,  # Per-message cooldown (not per-lead)
    "conversational_mode": True,  # Keep talking across multiple exchanges
    "business_hours_only": False,
    "business_hours": {"start": 8, "end": 20},
    "simulate_typing": True,
    "typing_delay_seconds": 3,
    "auto_mark_read": True,
    "auto_react_interested": True,
    "templates": {
        "first_response": (
            "Hi! This is Shamrock Bail Bonds. Thanks for reaching out — "
            "we're here to help get your loved one home. "
            "Are you looking to get them bonded out? 🍀"
        ),
        "after_hours": (
            "Thanks for your message! Our office is currently closed but "
            "we'll get back to you first thing in the morning. "
            "For urgent matters, call us at (239) 955-0178. 🍀"
        ),
    },
    "last_poll_at": None,
    "poll_interval_seconds": 30,
}


# ─────────────────────────────────────────────────────────────────────────────
#  Config Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_config() -> dict:
    """Load auto-reply config from MongoDB (creates default if missing)."""
    config_coll = get_collection("outreach_config")
    cfg = await config_coll.find_one({"type": "auto_reply"}, {"_id": 0})
    if not cfg:
        await config_coll.insert_one(DEFAULT_CONFIG.copy())
        return DEFAULT_CONFIG.copy()
    return cfg


async def _update_config(updates: dict) -> dict:
    """Update auto-reply config in MongoDB."""
    config_coll = get_collection("outreach_config")
    # Don't allow overwriting the type field
    updates.pop("type", None)
    updates.pop("_id", None)
    await config_coll.update_one(
        {"type": "auto_reply"},
        {"$set": updates},
        upsert=True,
    )
    return await _get_config()


def _get_bb_client() -> BlueBubblesClient | None:
    """Get a BlueBubblesClient for the primary server."""
    if not BB_SERVERS:
        init_bluebubbles()
    if not BB_SERVERS:
        return None
    srv = next(iter(BB_SERVERS.values()))
    return BlueBubblesClient(srv["url"], srv["password"])


# ═══════════════════════════════════════════════════════════════════════════════
#  Auto-Reply Configuration Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@imessage_auto_bp.get("/imessage/auto-reply/config")
async def get_auto_reply_config():
    """Get current auto-reply configuration."""
    cfg = await _get_config()
    return cfg


@imessage_auto_bp.post("/imessage/auto-reply/config")
async def update_auto_reply_config(request: Request):
    """Update auto-reply settings."""
    body = await request.json()
    cfg = await _update_config(body)
    logger.info("🔧 Auto-reply config updated: enabled=%s, ai=%s",
                cfg.get("enabled"), cfg.get("ai_enabled"))
    return {"success": True, "config": cfg}


# ═══════════════════════════════════════════════════════════════════════════════
#  Inbox Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@imessage_auto_bp.get("/imessage/inbox")
async def get_inbox(limit: str = Query(default="50")):
    """Fetch recent messages from MongoDB outreach log.
    Returns the latest message per unique phone number (grouped threads)
    with both inbound and outbound messages, so the sidebar shows
    all conversations — not just inbound.
    """
    limit = int(limit)
    outreach = get_collection("imessage_outreach")

    # Aggregate: group by recipient_phone, get latest message per thread
    pipeline = [
        {"$sort": {"sent_at": -1}},
        {"$group": {
            "_id": "$recipient_phone",
            "recipient_phone": {"$first": "$recipient_phone"},
            "message": {"$first": "$message"},
            "direction": {"$first": "$direction"},
            "sent_at": {"$first": "$sent_at"},
            "status": {"$first": "$status"},
            "contact_name": {"$first": "$contact_name"},
            "booking_number": {"$first": "$booking_number"},
            "unread": {"$first": "$unread"},
            "category": {"$first": "$category"},
            "total_messages": {"$sum": 1},
        }},
        {"$sort": {"sent_at": -1}},
        {"$limit": limit},
    ]

    docs = []
    async for doc in outreach.aggregate(pipeline):
        doc.pop("_id", None)
        docs.append(doc)

    return {"messages": docs, "count": len(docs)}


@imessage_auto_bp.post("/imessage/inbox/poll")

async def manual_poll():
    """Manually trigger one inbox poll cycle."""
    result = await _poll_inbox_once()
    return result


def _phone_match_query(clean_phone: str) -> dict:
    """Match recipient_phone across common storage formats (+1…, 10-digit, etc.)."""
    digits = (clean_phone or "").replace("+", "").replace("-", "")
    last10 = digits[-10:] if len(digits) >= 10 else digits
    variants = {clean_phone, digits, last10, f"+{digits}", f"+1{last10}", f"1{last10}"}
    variants = {v for v in variants if v}
    return {
        "$or": [
            {"recipient_phone": {"$in": list(variants)}},
            # Loose suffix match for legacy rows
            {"recipient_phone": {"$regex": f"{last10}$"}},
        ]
    }


@imessage_auto_bp.get("/imessage/thread/{phone}")
async def get_thread(
    phone,
    limit: str = Query(default="100"),
    hydrate: str = Query(default="1", description="1=also pull from BlueBubbles chat"),
):
    """Fetch full conversation history for a specific phone number.

    Returns all inbound + outbound messages sorted chronologically (oldest first)
    so the UI can render a chat-style thread view.

    When ``hydrate=1`` (default), also pulls recent messages for this chat from
    BlueBubbles and merges any missing rows into Mongo so replies appear even
    if the webhook/poller briefly lagged.
    """
    limit = int(limit)
    clean_phone = format_phone(phone)
    if not clean_phone:
        # Fall back to raw digits if format_phone rejects (e.g. partial)
        digits = "".join(c for c in str(phone) if c.isdigit())
        if len(digits) >= 10:
            clean_phone = format_phone(digits[-10:]) or f"+1{digits[-10:]}"
        else:
            return JSONResponse({"error": "Invalid phone number"}, status_code=400)

    outreach = get_collection("imessage_outreach")
    query = _phone_match_query(clean_phone)

    # Optionally hydrate from live BB so desktop shows replies immediately
    hydrated = 0
    if str(hydrate).lower() not in ("0", "false", "no"):
        try:
            hydrated = await _hydrate_thread_from_bb(clean_phone, limit=min(limit, 80))
        except Exception as e:
            logger.warning("Thread BB hydrate failed for ...%s: %s", clean_phone[-4:], e)

    docs = []
    async for doc in outreach.find(
        query,
        {"_id": 0},
    ).sort("sent_at", 1).limit(limit):
        docs.append(doc)

    # Mark inbound messages as read in MongoDB
    if docs:
        await outreach.update_many(
            {**query, "direction": "inbound", "unread": {"$ne": False}},
            {"$set": {"unread": False}},
        )

    return {
        "messages": docs,
        "count": len(docs),
        "phone": clean_phone,
        "hydrated_from_bb": hydrated,
    }


async def _hydrate_thread_from_bb(clean_phone: str, limit: int = 50) -> int:
    """Pull chat history from BB for one phone and upsert missing outreach rows.

    Returns number of newly inserted messages.
    """
    client = _get_bb_client()
    if not client:
        return 0

    chat_guid = f"any;-;{clean_phone}"
    result = await client.get_chat_messages(chat_guid, limit=limit, sort="ASC")
    if not result.get("success"):
        # Try without +1 prefix variants
        digits = clean_phone.replace("+", "")
        last10 = digits[-10:] if len(digits) >= 10 else digits
        for alt in (f"any;-;+1{last10}", f"any;-;{last10}", f"iMessage;-;+1{last10}"):
            result = await client.get_chat_messages(alt, limit=limit, sort="ASC")
            if result.get("success"):
                chat_guid = alt
                break
    if not result.get("success"):
        return 0

    messages = result.get("data") or []
    if isinstance(messages, dict):
        messages = messages.get("data") or []
    if not isinstance(messages, list):
        return 0

    outreach = get_collection("imessage_outreach")
    inserted = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        msg_guid = msg.get("guid") or ""
        msg_text = (msg.get("text") or "").strip()
        if not msg_text and not msg_guid:
            continue
        is_from_me = bool(msg.get("isFromMe", msg.get("is_from_me", False)))
        direction = "outbound" if is_from_me else "inbound"

        # Dedup by GUID when present
        if msg_guid:
            existing = await outreach.find_one({"bb_message_guid": msg_guid}, {"_id": 1})
            if existing:
                continue

        sent_at = datetime.now(timezone.utc).isoformat()
        date_ms = msg.get("dateCreated")
        if isinstance(date_ms, (int, float)) and date_ms > 0:
            try:
                ts = float(date_ms)
                if ts > 1e14:
                    ts = ts / 1e6
                if ts > 1e12:
                    sent_at = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).isoformat()
                elif ts > 1e9:
                    sent_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            except (OSError, OverflowError, ValueError):
                pass

        chash = _content_hash(clean_phone, msg_text or msg_guid, date_ms)
        if chash:
            existing_c = await outreach.find_one({"content_hash": chash}, {"_id": 1})
            if existing_c:
                continue

        doc = {
            "recipient_phone": clean_phone,
            "message": msg_text or "[attachment]",
            "chat_guid": chat_guid,
            "bb_message_guid": msg_guid,
            "content_hash": chash,
            "direction": direction,
            "status": "received" if direction == "inbound" else "sent",
            "unread": direction == "inbound",
            "sent_at": sent_at,
            "sent_by": "lead" if direction == "inbound" else "dashboard",
            "source": "bb_hydrate",
            "category": "general",
        }
        await outreach.insert_one(doc)
        inserted += 1

        # Real-time UI ping for newly discovered inbound
        if direction == "inbound":
            try:
                from dashboard.routers.events import publish_event
                await publish_event("message_received", {
                    "phone": clean_phone,
                    "phone_last4": clean_phone[-4:],
                    "message": (msg_text or "")[:120],
                    "preview": (msg_text or "")[:80],
                    "matched": False,
                    "source": "bb_hydrate",
                    "sent_at": sent_at,
                })
            except Exception:
                pass

    if inserted:
        logger.info(
            "💧 Hydrated %d message(s) from BB for ...%s",
            inserted, clean_phone[-4:],
        )
    return inserted





# ═══════════════════════════════════════════════════════════════════════════════
#  Dedup Check
# ═══════════════════════════════════════════════════════════════════════════════

@imessage_auto_bp.post("/imessage/dedup-check")

async def dedup_check(request: Request):
    """Check if a phone+booking combo was already messaged within cooldown."""
    body = await request.json()
    phone_raw = body.get("phone", "")
    booking_number = body.get("booking_number", "")
    cooldown_hours = body.get("cooldown_hours", 24)

    phone = format_phone(phone_raw)
    if not phone:
        return JSONResponse({"error": "Invalid phone number"}, status_code=400)

    outreach = get_collection("imessage_outreach")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)).isoformat()
    recent = await outreach.find_one({
        "recipient_phone": phone,
        "booking_number": booking_number,
        "status": "sent",
        "direction": {"$ne": "inbound"},
        "sent_at": {"$gte": cutoff},
    }, {"_id": 0})

    if recent:
        return {
            "is_duplicate": True,
            "last_sent": recent.get("sent_at"),
            "message_preview": (recent.get("message", ""))[:80],
        }
    return {"is_duplicate": False}


# ═══════════════════════════════════════════════════════════════════════════════
#  Private API Proxy Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@imessage_auto_bp.post("/imessage/unsend")
async def unsend_message(request: Request):
    """Unsend a previously sent message."""
    body = await request.json()
    message_guid = body.get("message_guid", "")
    if not message_guid:
        return JSONResponse({"error": "message_guid is required"}, status_code=400)

    client = _get_bb_client()
    if not client:
        return JSONResponse({"error": "BlueBubbles not configured"}, status_code=503)

    result = await client.unsend_message(message_guid)

    # Update MongoDB record
    if result.get("success"):
        outreach = get_collection("imessage_outreach")
        await outreach.update_one(
            {"bb_message_guid": message_guid},
            {"$set": {"status": "unsent", "unsent_at": datetime.now(timezone.utc).isoformat()}}
        )

    return result, 200 if result.get("success") else 502


@imessage_auto_bp.post("/imessage/edit")
async def edit_message(request: Request):
    """Edit a previously sent message."""
    body = await request.json()
    message_guid = body.get("message_guid", "")
    new_text = body.get("new_text", "").strip()
    if not message_guid or not new_text:
        return JSONResponse({"error": "message_guid and new_text are required"}, status_code=400)

    client = _get_bb_client()
    if not client:
        return JSONResponse({"error": "BlueBubbles not configured"}, status_code=503)

    result = await client.edit_message(message_guid, new_text)

    # Update MongoDB record
    if result.get("success"):
        outreach = get_collection("imessage_outreach")
        await outreach.update_one(
            {"bb_message_guid": message_guid},
            {"$set": {
                "message": new_text,
                "edited_at": datetime.now(timezone.utc).isoformat(),
            },
            "$push": {
                "edit_history": {
                    "new_text": new_text,
                    "edited_at": datetime.now(timezone.utc).isoformat(),
                }
            }}
        )

    return result, 200 if result.get("success") else 502


@imessage_auto_bp.post("/imessage/react")
async def react_to_message(request: Request):
    """Send a tapback reaction on a message."""
    body = await request.json()
    chat_guid = body.get("chat_guid", "")
    message_guid = body.get("message_guid", "")
    reaction = body.get("reaction", "love")

    if not chat_guid or not message_guid:
        return JSONResponse({"error": "chat_guid and message_guid are required"}, status_code=400)
    if reaction not in REACTIONS:
        return JSONResponse({"error": f"Invalid reaction. Valid: {list(REACTIONS.keys())}"}, status_code=400)

    client = _get_bb_client()
    if not client:
        return JSONResponse({"error": "BlueBubbles not configured"}, status_code=503)

    result = await client.react(chat_guid, message_guid, reaction)
    return result, 200 if result.get("success") else 502


@imessage_auto_bp.post("/imessage/mark-read")
async def mark_chat_read(request: Request):
    """Mark a chat as read (BlueBubbles + MongoDB unread flags).

    Accepts either:
      - chat_guid: \"any;-;+1…\"
      - phone / handle: normalized to chat_guid
    """
    body = await request.json() or {}
    chat_guid = (body.get("chat_guid") or "").strip()
    phone_raw = body.get("phone") or body.get("handle") or ""
    clean = format_phone(phone_raw) if phone_raw else None

    if not chat_guid and clean:
        chat_guid = f"any;-;{clean}"
    if not chat_guid:
        return JSONResponse(
            {"error": "chat_guid or phone/handle is required"}, status_code=400
        )

    # Always clear unread in Mongo for this conversation
    if clean or phone_raw:
        try:
            phone_key = clean or format_phone(phone_raw) or phone_raw
            outreach = get_collection("imessage_outreach")
            await outreach.update_many(
                {**_phone_match_query(str(phone_key)), "direction": "inbound"},
                {"$set": {"unread": False}},
            )
        except Exception as exc:
            logger.warning("mark-read Mongo update failed: %s", exc)

    client = _get_bb_client()
    if not client:
        # Still success for dashboard — Mongo flags cleared
        return {"success": True, "bb": False, "chat_guid": chat_guid}

    result = await client.mark_read(chat_guid)
    if isinstance(result, dict):
        result.setdefault("chat_guid", chat_guid)
        return result if result.get("success") else JSONResponse(result, status_code=502)
    return result


@imessage_auto_bp.post("/imessage/typing")
async def typing_indicator(request: Request):
    """Start typing indicator on a chat."""
    body = await request.json()
    chat_guid = body.get("chat_guid", "")
    if not chat_guid:
        return JSONResponse({"error": "chat_guid is required"}, status_code=400)

    client = _get_bb_client()
    if not client:
        return JSONResponse({"error": "BlueBubbles not configured"}, status_code=503)

    result = await client.start_typing(chat_guid)
    return result, 200 if result.get("success") else 502


@imessage_auto_bp.get("/imessage/message-status/{message_guid}")
async def message_status(message_guid):
    """Check delivery/read status of a sent message."""
    client = _get_bb_client()
    if not client:
        return JSONResponse({"error": "BlueBubbles not configured"}, status_code=503)

    result = await client.get_message_status(message_guid)
    return result, 200 if result.get("success") else 502


@imessage_auto_bp.get("/imessage/findmy")
async def findmy_locations(type_: str = Query(default="friends"), refresh: str = Query(default="false")):
    """Fetch FindMy device and friend locations."""
    client = _get_bb_client()
    if not client:
        return JSONResponse({"error": "BlueBubbles not configured"}, status_code=503)

    target = type_  # "friends" or "devices"
    refresh = refresh.lower() == "true"

    if target == "devices":
        if refresh:
            await client.findmy_refresh_devices()
        result = await client.findmy_devices()
    else:
        if refresh:
            await client.findmy_refresh_friends()
        result = await client.findmy_friends()

    return result, 200 if result.get("success") else 502


@imessage_auto_bp.post("/imessage/send-effect")
async def send_with_effect(request: Request):
    """Send a message with an iMessage bubble/screen effect."""
    body = await request.json()
    phone_raw = body.get("phone", "")
    message = body.get("message", "").strip()
    effect = body.get("effect", "")

    if not phone_raw or not message or not effect:
        return JSONResponse({"error": "phone, message, and effect are required"}, status_code=400)

    phone = format_phone(phone_raw)
    if not phone:
        return JSONResponse({"error": f"Invalid phone: {phone_raw}"}, status_code=400)

    if effect not in EFFECTS and not effect.startswith("com.apple"):
        return JSONResponse({"error": f"Invalid effect. Valid: {list(EFFECTS.keys())}"}, status_code=400)

    client = _get_bb_client()
    if not client:
        return JSONResponse({"error": "BlueBubbles not configured"}, status_code=503)

    chat_guid = f"any;-;{phone}"
    temp_guid = f"shamrock-fx-{uuid.uuid4().hex[:12]}"

    result = await client.send_with_effect(chat_guid, message, effect, temp_guid)

    # Log to MongoDB
    if result.get("success"):
        outreach = get_collection("imessage_outreach")
        await outreach.insert_one({
            "recipient_phone": phone,
            "message": message,
            "chat_guid": chat_guid,
            "temp_guid": temp_guid,
            "direction": "outbound",
            "effect": effect,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "status": "sent",
            "sent_by": "dashboard",
        })

    return result, 200 if result.get("success") else 502


# ═══════════════════════════════════════════════════════════════════════════════
#  Background Inbox Poller
# ═══════════════════════════════════════════════════════════════════════════════

async def _poll_inbox_once() -> dict:
    """Execute one inbox poll cycle.

    1. Fetch messages from BB after last_poll_at
    2. Filter inbound only (isFromMe == false)
    3. Match to prospective bonds
    4. Run agent brain for matched messages
    5. Update last_poll_at
    """
    client = _get_bb_client()
    if not client:
        return {"success": False, "error": "BlueBubbles not configured"}

    db = get_db()
    config = await _get_config()

    # Determine poll window
    last_poll = config.get("last_poll_at")
    if last_poll:
        # Convert ISO string to epoch milliseconds for BB API
        try:
            dt = datetime.fromisoformat(last_poll.replace("Z", "+00:00"))
            after_ms = int(dt.timestamp() * 1000)
        except (ValueError, AttributeError):
            after_ms = int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp() * 1000)
    else:
        # First poll — look back 5 minutes
        after_ms = int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp() * 1000)

    # Fetch from BB
    result = await client.get_messages(after=after_ms, limit=50, sort="DESC")
    if not result.get("success"):
        logger.warning("Inbox poll failed: %s", result.get("error", "unknown"))
        return {"success": False, "error": result.get("error", "BB request failed")}

    messages = result.get("data", [])
    if isinstance(messages, dict):
        messages = messages.get("data", []) if "data" in messages else []

    # Filter inbound only
    inbound = []
    for msg in messages:
        if isinstance(msg, dict) and not msg.get("isFromMe", True):
            inbound.append(msg)

    if not inbound:
        # Update poll timestamp even if no messages
        await _update_config({"last_poll_at": datetime.now(timezone.utc).isoformat()})
        return {"success": True, "processed": 0, "inbound_count": 0}

    processed = 0
    matched = 0
    replied = 0

    bonds_coll = db["prospective_bonds"]
    outreach_coll = db["imessage_outreach"]

    dedup_skipped = 0

    for msg in inbound:
        msg_text = msg.get("text", "") or ""
        msg_guid = msg.get("guid", "")
        msg_date_ms = msg.get("dateCreated")  # epoch ms from BB
        chat_guid = msg.get("chats", [{}])[0].get("guid", "") if msg.get("chats") else ""

        # Extract sender phone from handle
        handle = msg.get("handle", {}) or {}
        sender_address = handle.get("address", "") if isinstance(handle, dict) else ""
        if not sender_address:
            # Try alternate path
            sender_address = msg.get("address", "")

        sender_phone = format_phone(sender_address)
        if not sender_phone or not msg_text.strip():
            continue

        # ── Layer 1: GUID dedup (original check) ──
        existing = await outreach_coll.find_one({"bb_message_guid": msg_guid})
        if existing:
            continue

        # ── Layer 2: Content-hash dedup (catches BB Issue #765) ──
        # Same sender + same text + same 60s window = duplicate even with new GUID
        chash = _content_hash(sender_phone, msg_text, msg_date_ms)
        existing_content = await outreach_coll.find_one({"content_hash": chash})
        if existing_content:
            dedup_skipped += 1
            logger.info(
                "🔁 Content-hash dedup caught duplicate from ...%s (GUID %s, hash %s)",
                sender_phone[-4:], msg_guid[:12], chash[:8]
            )
            continue

        processed += 1

        # Match to prospective bond by indemnitor phone
        bond = await bonds_coll.find_one({
            "indemnitor.phone": sender_phone,
            "status": "active",
        })

        if not bond:
            # Try matching with phone variations
            phone_digits = sender_phone.replace("+1", "").replace("+", "")
            bond = await bonds_coll.find_one({
                "$or": [
                    {"indemnitor.phone": sender_phone},
                    {"indemnitor.phone": phone_digits},
                    {"indemnitor.phone": f"+1{phone_digits}"},
                    {"indemnitor.phone": {"$regex": phone_digits[-10:]}},
                ],
                "status": "active",
            })

        if bond:
            matched += 1
            # Run agent brain (logs inbound + optional auto-reply)
            agent_result = await process_inbound(
                phone=sender_phone,
                message_text=msg_text,
                chat_guid=chat_guid,
                message_guid=msg_guid,
                bond_doc=bond,
                db=db,
                config=config,
                bb_client=client,
                content_hash=chash,
            )
            if agent_result.get("responded"):
                replied += 1

            await outreach_coll.update_one(
                {"bb_message_guid": msg_guid},
                {"$set": {"unread": True, "source": "poll", "category": agent_result.get("intent") or "general"}},
            )
            _post_slack_alert(bond, sender_phone, msg_text, agent_result)

            # Push to open dashboard threads
            try:
                from dashboard.routers.events import publish_event
                await publish_event("message_received", {
                    "phone": sender_phone,
                    "phone_last4": sender_phone[-4:],
                    "booking_number": bond.get("booking_number", ""),
                    "defendant_name": bond.get("defendant_name", ""),
                    "message": msg_text[:120],
                    "preview": msg_text[:80],
                    "matched": True,
                })
                await publish_event("new_reply", {
                    "phone": sender_phone,
                    "defendant_name": bond.get("defendant_name", ""),
                    "message": msg_text[:120],
                    "preview": msg_text[:80],
                })
            except Exception:
                pass
        else:
            # Log unmatched inbound for manual review
            await outreach_coll.insert_one({
                "recipient_phone": sender_phone,
                "message": msg_text,
                "chat_guid": chat_guid,
                "bb_message_guid": msg_guid,
                "content_hash": chash,
                "direction": "inbound",
                "status": "unmatched",
                "unread": True,
                "category": "general",
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "sent_by": "unknown_lead",
                "source": "poll",
            })
            logger.info("❓ Unmatched inbound from %s: %s", sender_phone[-4:], msg_text[:50])
            try:
                from dashboard.routers.events import publish_event
                await publish_event("message_received", {
                    "phone": sender_phone,
                    "phone_last4": sender_phone[-4:],
                    "message": msg_text[:120],
                    "preview": msg_text[:80],
                    "matched": False,
                })
            except Exception:
                pass

    # Update poll timestamp
    await _update_config({"last_poll_at": datetime.now(timezone.utc).isoformat()})

    if dedup_skipped:
        logger.warning(
            "🔁 Content-hash dedup blocked %d duplicate(s) this cycle", dedup_skipped
        )

    logger.info(
        "📬 Inbox poll: %d messages, %d inbound, %d processed, %d dedup-blocked, %d matched, %d replied",
        len(messages), len(inbound), processed, dedup_skipped, matched, replied
    )

    return {
        "success": True,
        "total_messages": len(messages),
        "inbound_count": len(inbound),
        "processed": processed,
        "dedup_blocked": dedup_skipped,
        "matched": matched,
        "replied": replied,
    }


def _post_slack_alert(bond: dict, phone: str, message: str, agent_result: dict):
    """Post a Slack alert for an inbound lead reply."""
    webhook_url = os.getenv("SLACK_WEBHOOK_LEADS", "")
    if not webhook_url:
        return

    try:
        import httpx
        defendant = bond.get("defendant_name", "Unknown")
        county = bond.get("county", "")
        intent = agent_result.get("intent", "unknown")
        responded = "✅ Auto-replied" if agent_result.get("responded") else "⏳ Manual follow-up needed"

        text = (
            f"🔔 *Lead Replied via iMessage!*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"*Defendant:* {defendant}\n"
            f"*County:* {county}\n"
            f"*Phone:* ...{phone[-4:]}\n"
            f"*Message:* _{message[:100]}_\n"
            f"*Intent:* {intent}\n"
            f"*Status:* {responded}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )

        # Fire-and-forget (sync is fine for Slack webhook)
        import threading
        def _send():
            try:
                import httpx as hx
                hx.post(webhook_url, json={"text": text}, timeout=5)
            except Exception:
                pass
        threading.Thread(target=_send, daemon=True).start()
    except Exception as e:
        logger.warning("Slack alert failed: %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
#  Background Task Launcher
# ═══════════════════════════════════════════════════════════════════════════════

async def start_inbox_poller(app):
    """Background task: poll BlueBubbles inbox on a configurable interval.
    Registered via app.before_serving in __init__.py.
    """
    logger.info("📬 Inbox poller starting...")

    # Wait for app to be fully initialized
    await asyncio.sleep(5)

    # Ensure indexes for dedup lookups (idempotent — safe to call every startup)
    try:
        outreach_coll = get_collection("imessage_outreach")
        await outreach_coll.create_index("bb_message_guid", sparse=True)
        await outreach_coll.create_index("content_hash", sparse=True)
        logger.info("📬 Dedup indexes ensured on imessage_outreach")
    except Exception as e:
        logger.warning("📬 Index creation failed (non-critical): %s", e)

    _backoff = 30  # seconds — reset on success, doubles on consecutive errors
    _max_backoff = 300  # cap at 5 minutes
    _consecutive_errors = 0

    while True:
        try:
            config = await _get_config()
            # Always poll for inbound so the desktop inbox shows replies even when
            # auto-reply AI is off. `enabled` only gates auto-replies inside process_inbound.
            interval = int(config.get("poll_interval_seconds") or 30)
            interval = max(10, min(interval, 120))

            poll_result = await _poll_inbox_once()
            # Reset backoff on successful poll
            if poll_result.get("success", True):
                _consecutive_errors = 0
                _backoff = 30

            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("📬 Inbox poller stopped")
            break
        except Exception as e:
            _consecutive_errors += 1
            _backoff = min(_backoff * 2, _max_backoff)
            logger.error(
                "📬 Inbox poller error (attempt %d, backoff %ds): %s",
                _consecutive_errors, _backoff, e,
            )
            await asyncio.sleep(_backoff)  # Exponential back-off on error
