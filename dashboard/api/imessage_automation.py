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
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta

from quart import Blueprint, jsonify, request
from dashboard.extensions import (
    get_collection, get_db, format_phone,
    BB_SERVERS, get_bb_server, init_bluebubbles,
)
from dashboard.api.bb_private_api import BlueBubblesClient, EFFECTS, REACTIONS
from dashboard.api.agent_brain import process_inbound

logger = logging.getLogger(__name__)

imessage_auto_bp = Blueprint("imessage_automation", __name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Default Config
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "type": "auto_reply",
    "enabled": False,  # Off by default — user enables via dashboard
    "ai_enabled": True,
    "cooldown_minutes": 60,
    "first_reply_only": True,
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
            "A bondsman will be with you shortly. 🍀"
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

@imessage_auto_bp.route("/imessage/auto-reply/config", methods=["GET"])
async def get_auto_reply_config():
    """Get current auto-reply configuration."""
    cfg = await _get_config()
    return jsonify(cfg)


@imessage_auto_bp.route("/imessage/auto-reply/config", methods=["POST"])
async def update_auto_reply_config():
    """Update auto-reply settings."""
    body = await request.get_json(force=True)
    cfg = await _update_config(body)
    logger.info("🔧 Auto-reply config updated: enabled=%s, ai=%s",
                cfg.get("enabled"), cfg.get("ai_enabled"))
    return jsonify({"success": True, "config": cfg})


# ═══════════════════════════════════════════════════════════════════════════════
#  Inbox Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@imessage_auto_bp.route("/imessage/inbox", methods=["GET"])
async def get_inbox():
    """Fetch recent inbound messages from MongoDB outreach log."""
    limit = int(request.args.get("limit", "50"))
    outreach = get_collection("imessage_outreach")
    docs = []
    async for doc in outreach.find(
        {"direction": "inbound"},
        {"_id": 0},
    ).sort("sent_at", -1).limit(limit):
        docs.append(doc)
    return jsonify({"messages": docs, "count": len(docs)})


@imessage_auto_bp.route("/imessage/inbox/poll", methods=["POST"])
async def manual_poll():
    """Manually trigger one inbox poll cycle."""
    result = await _poll_inbox_once()
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════════════════════
#  Dedup Check
# ═══════════════════════════════════════════════════════════════════════════════

@imessage_auto_bp.route("/imessage/dedup-check", methods=["POST"])
async def dedup_check():
    """Check if a phone+booking combo was already messaged within cooldown."""
    body = await request.get_json(force=True)
    phone_raw = body.get("phone", "")
    booking_number = body.get("booking_number", "")
    cooldown_hours = body.get("cooldown_hours", 24)

    phone = format_phone(phone_raw)
    if not phone:
        return jsonify({"error": "Invalid phone number"}), 400

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
        return jsonify({
            "is_duplicate": True,
            "last_sent": recent.get("sent_at"),
            "message_preview": (recent.get("message", ""))[:80],
        })
    return jsonify({"is_duplicate": False})


# ═══════════════════════════════════════════════════════════════════════════════
#  Private API Proxy Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@imessage_auto_bp.route("/imessage/unsend", methods=["POST"])
async def unsend_message():
    """Unsend a previously sent message."""
    body = await request.get_json(force=True)
    message_guid = body.get("message_guid", "")
    if not message_guid:
        return jsonify({"error": "message_guid is required"}), 400

    client = _get_bb_client()
    if not client:
        return jsonify({"error": "BlueBubbles not configured"}), 503

    result = await client.unsend_message(message_guid)

    # Update MongoDB record
    if result.get("success"):
        outreach = get_collection("imessage_outreach")
        await outreach.update_one(
            {"bb_message_guid": message_guid},
            {"$set": {"status": "unsent", "unsent_at": datetime.now(timezone.utc).isoformat()}}
        )

    return jsonify(result), 200 if result.get("success") else 502


@imessage_auto_bp.route("/imessage/edit", methods=["POST"])
async def edit_message():
    """Edit a previously sent message."""
    body = await request.get_json(force=True)
    message_guid = body.get("message_guid", "")
    new_text = body.get("new_text", "").strip()
    if not message_guid or not new_text:
        return jsonify({"error": "message_guid and new_text are required"}), 400

    client = _get_bb_client()
    if not client:
        return jsonify({"error": "BlueBubbles not configured"}), 503

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

    return jsonify(result), 200 if result.get("success") else 502


@imessage_auto_bp.route("/imessage/react", methods=["POST"])
async def react_to_message():
    """Send a tapback reaction on a message."""
    body = await request.get_json(force=True)
    chat_guid = body.get("chat_guid", "")
    message_guid = body.get("message_guid", "")
    reaction = body.get("reaction", "love")

    if not chat_guid or not message_guid:
        return jsonify({"error": "chat_guid and message_guid are required"}), 400
    if reaction not in REACTIONS:
        return jsonify({"error": f"Invalid reaction. Valid: {list(REACTIONS.keys())}"}), 400

    client = _get_bb_client()
    if not client:
        return jsonify({"error": "BlueBubbles not configured"}), 503

    result = await client.react(chat_guid, message_guid, reaction)
    return jsonify(result), 200 if result.get("success") else 502


@imessage_auto_bp.route("/imessage/mark-read", methods=["POST"])
async def mark_chat_read():
    """Mark a chat as read."""
    body = await request.get_json(force=True)
    chat_guid = body.get("chat_guid", "")
    if not chat_guid:
        return jsonify({"error": "chat_guid is required"}), 400

    client = _get_bb_client()
    if not client:
        return jsonify({"error": "BlueBubbles not configured"}), 503

    result = await client.mark_read(chat_guid)
    return jsonify(result), 200 if result.get("success") else 502


@imessage_auto_bp.route("/imessage/typing", methods=["POST"])
async def typing_indicator():
    """Start typing indicator on a chat."""
    body = await request.get_json(force=True)
    chat_guid = body.get("chat_guid", "")
    if not chat_guid:
        return jsonify({"error": "chat_guid is required"}), 400

    client = _get_bb_client()
    if not client:
        return jsonify({"error": "BlueBubbles not configured"}), 503

    result = await client.start_typing(chat_guid)
    return jsonify(result), 200 if result.get("success") else 502


@imessage_auto_bp.route("/imessage/message-status/<message_guid>", methods=["GET"])
async def message_status(message_guid):
    """Check delivery/read status of a sent message."""
    client = _get_bb_client()
    if not client:
        return jsonify({"error": "BlueBubbles not configured"}), 503

    result = await client.get_message_status(message_guid)
    return jsonify(result), 200 if result.get("success") else 502


@imessage_auto_bp.route("/imessage/findmy", methods=["GET"])
async def findmy_locations():
    """Fetch FindMy device and friend locations."""
    client = _get_bb_client()
    if not client:
        return jsonify({"error": "BlueBubbles not configured"}), 503

    target = request.args.get("type", "friends")  # "friends" or "devices"
    refresh = request.args.get("refresh", "false").lower() == "true"

    if target == "devices":
        if refresh:
            await client.findmy_refresh_devices()
        result = await client.findmy_devices()
    else:
        if refresh:
            await client.findmy_refresh_friends()
        result = await client.findmy_friends()

    return jsonify(result), 200 if result.get("success") else 502


@imessage_auto_bp.route("/imessage/send-effect", methods=["POST"])
async def send_with_effect():
    """Send a message with an iMessage bubble/screen effect."""
    body = await request.get_json(force=True)
    phone_raw = body.get("phone", "")
    message = body.get("message", "").strip()
    effect = body.get("effect", "")

    if not phone_raw or not message or not effect:
        return jsonify({"error": "phone, message, and effect are required"}), 400

    phone = format_phone(phone_raw)
    if not phone:
        return jsonify({"error": f"Invalid phone: {phone_raw}"}), 400

    if effect not in EFFECTS and not effect.startswith("com.apple"):
        return jsonify({"error": f"Invalid effect. Valid: {list(EFFECTS.keys())}"}), 400

    client = _get_bb_client()
    if not client:
        return jsonify({"error": "BlueBubbles not configured"}), 503

    chat_guid = f"iMessage;-;{phone}"
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

    return jsonify(result), 200 if result.get("success") else 502


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

    for msg in inbound:
        msg_text = msg.get("text", "") or ""
        msg_guid = msg.get("guid", "")
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

        # Check if we already processed this message (dedup by BB GUID)
        existing = await outreach_coll.find_one({"bb_message_guid": msg_guid})
        if existing:
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
            # Run agent brain
            agent_result = await process_inbound(
                phone=sender_phone,
                message_text=msg_text,
                chat_guid=chat_guid,
                message_guid=msg_guid,
                bond_doc=bond,
                db=db,
                config=config,
                bb_client=client,
            )
            if agent_result.get("responded"):
                replied += 1

            # Post Slack alert
            _post_slack_alert(bond, sender_phone, msg_text, agent_result)
        else:
            # Log unmatched inbound for manual review
            await outreach_coll.insert_one({
                "recipient_phone": sender_phone,
                "message": msg_text,
                "chat_guid": chat_guid,
                "bb_message_guid": msg_guid,
                "direction": "inbound",
                "status": "unmatched",
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "sent_by": "unknown_lead",
            })
            logger.info("❓ Unmatched inbound from %s: %s", sender_phone[-4:], msg_text[:50])

    # Update poll timestamp
    await _update_config({"last_poll_at": datetime.now(timezone.utc).isoformat()})

    logger.info(
        "📬 Inbox poll: %d messages, %d inbound, %d matched, %d replied",
        len(messages), len(inbound), matched, replied
    )

    return {
        "success": True,
        "total_messages": len(messages),
        "inbound_count": len(inbound),
        "processed": processed,
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

    while True:
        try:
            config = await _get_config()
            interval = config.get("poll_interval_seconds", 30)

            if config.get("enabled", False):
                await _poll_inbox_once()
            else:
                # Even when disabled, check occasionally for config changes
                pass

            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("📬 Inbox poller stopped")
            break
        except Exception as e:
            logger.error("📬 Inbox poller error: %s", e, exc_info=True)
            await asyncio.sleep(30)  # Back off on error
