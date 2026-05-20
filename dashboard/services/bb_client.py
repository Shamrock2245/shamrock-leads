"""
ShamrockLeads — BlueBubbles Client Factory
==========================================
Provides a single get_bb_client() helper used by all services and API
blueprints that need to send or receive iMessages via BlueBubbles.

Wraps the BlueBubblesClient from dashboard/api/bb_private_api.py and
resolves the correct server instance from BB_SERVERS based on the
target phone number.

Usage:
    from dashboard.services.bb_client import get_bb_client, get_default_bb_client

    bb = get_bb_client("+12395550178")   # resolves to correct BB server
    if bb:
        await bb.send_text("any;-;+12395550178", "Hello!")  # auto-selects iMessage/SMS

    bb = get_default_bb_client()          # always returns first configured server
"""
import logging
from typing import Optional
from datetime import datetime, timezone
from bson import ObjectId

logger = logging.getLogger(__name__)


def get_bb_client(phone: Optional[str] = None):
    """
    Return a BlueBubblesClient instance for the given phone number.
    Resolves the correct server from BB_SERVERS (extensions.py).

    Args:
        phone: Target phone number (E.164 or 10-digit). Used to select
               the correct outbound BlueBubbles server. If None, returns
               the first configured server.

    Returns:
        BlueBubblesClient instance, or None if no server is configured.
    """
    try:
        from dashboard.extensions import BB_SERVERS, get_bb_server
        from dashboard.routers.bb_private_api import BlueBubblesClient

        if phone:
            server = get_bb_server(phone)
        else:
            server = next(iter(BB_SERVERS.values()), None)

        if not server:
            logger.warning("[bb_client] No BlueBubbles server configured for phone=%s", phone)
            return None

        url = server.get("url", "")
        password = server.get("password", "")
        if not url or not password:
            logger.warning("[bb_client] BB server missing url or password: %s", server)
            return None

        return BlueBubblesClient(url, password)

    except Exception as exc:
        logger.error("[bb_client] Failed to create BB client: %s", exc)
        return None


def get_default_bb_client():
    """Return the first configured BlueBubbles server client."""
    return get_bb_client(None)


async def check_imessage(phone: str) -> bool:
    """
    Check if a phone number has iMessage enabled via BlueBubbles.

    Args:
        phone: Phone number to check (E.164 or 10-digit)

    Returns:
        True if iMessage is available, False otherwise.
    """
    bb = get_bb_client(phone)
    if not bb:
        return False
    try:
        result = await bb.check_imessage_availability(phone)
        return result.get("success", False) and result.get("data", {}).get("available", False)
    except Exception as exc:
        logger.error("[bb_client] check_imessage error for %s: %s", phone, exc)
        return False


async def _send_message_direct(phone: str, message: str) -> dict:
    """Send text directly via BlueBubbles without writing to queue first."""
    bb = get_bb_client(phone)
    if not bb:
        return {"success": False, "error": "no_bb_server"}
    chat_guid = f"any;-;{phone}"
    try:
        return await bb.send_text(chat_guid, message)
    except Exception as exc:
        logger.error("[bb_client] _send_message_direct error to %s: %s", phone, exc)
        return {"success": False, "error": str(exc)}


async def _send_attachment_direct(phone: str, message: str, file_path: str) -> dict:
    """Send attachment directly via BlueBubbles without writing to queue first."""
    bb = get_bb_client(phone)
    if not bb:
        return {"success": False, "error": "no_bb_server"}
    chat_guid = f"any;-;{phone}"
    try:
        return await bb.send_attachment_url(chat_guid, file_path, message=message)
    except Exception as exc:
        logger.error("[bb_client] _send_attachment_direct error to %s: %s", phone, exc)
        return {"success": False, "error": str(exc)}


async def send_imessage(
    phone: str,
    message: str,
    method: str = "private-api",
) -> dict:
    """
    Send a message via BlueBubbles.

    Uses `any;-;` chat GUID prefix so BlueBubbles auto-selects the best
    transport: iMessage for iPhones, SMS/RCS for everyone else.

    Args:
        phone:    Recipient phone number (E.164 or 10-digit)
        message:  Message text to send
        method:   BlueBubbles send method ("private-api" or "apple-script")

    Returns:
        BlueBubbles API response dict
    """
    return await _send_message_direct(phone, message)


async def send_message_universal(
    phone: str,
    message: str,
    method: str = "private-api",
) -> dict:
    """
    Universal send via BlueBubbles using `any;-;` chat GUID prefix.

    BlueBubbles auto-selects the best transport:
      - iMessage for iPhones (blue bubble)
      - SMS for non-iPhones (green bubble via Mac Messages relay)
      - RCS where supported

    This standard entry point writes to the outreach_queue collection first,
    then attempts direct dispatch. If direct dispatch fails, the message remains
    pending in the queue for background retries and returns a queued status.

    Args:
        phone:    Recipient phone number (E.164 or 10-digit)
        message:  Message text to send
        method:   BlueBubbles send method ("private-api" or "apple-script")

    Returns:
        { success: bool, channel: "imessage"|"sms"|"queued"|"failed", ... }
    """
    from dashboard.services.outreach_queue import enqueue_message
    from dashboard.extensions import get_collection
    
    # 1. Write to outreach queue first
    queue_id = await enqueue_message(phone, message, context="universal")
    
    bb = get_bb_client(phone)
    if not bb:
        logger.error("[bb_client] No BB server configured for %s", phone)
        # Leave it in the queue to retry in case server gets configured/restored later
        return {"success": True, "status": "queued", "channel": "queued", "queued_id": queue_id, "error": "no_bb_server"}

    # 2. Check iMessage availability for channel reporting (not routing)
    channel = "sms"  # default assumption
    try:
        avail = await bb.check_imessage_availability(phone)
        if avail.get("available", False) or avail.get("data", {}).get("available", False):
            channel = "imessage"
    except Exception:
        pass  # availability check failed — still send via any;-;

    # 3. Attempt immediate direct send
    try:
        result = await _send_message_direct(phone, message)
        if result.get("success"):
            logger.info("[bb_client] ✅ Message sent to ...%s via %s (Queue ID: %s)", phone[-4:], channel, queue_id)
            # Update queue record to sent
            await get_collection("outreach_queue").update_one(
                {"_id": ObjectId(queue_id)},
                {"$set": {"status": "sent", "sent_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}}
            )
            return {"success": True, "channel": channel, "data": result.get("data")}
        else:
            logger.warning("[bb_client] Direct BB send failed for %s, remains queued (Queue ID: %s): %s", phone, queue_id, result.get("error"))
            return {"success": True, "status": "queued", "channel": "queued", "queued_id": queue_id, "error": result.get("error")}
    except Exception as exc:
        logger.error("[bb_client] Direct BB send exception for %s, remains queued (Queue ID: %s): %s", phone, queue_id, exc)
        return {"success": True, "status": "queued", "channel": "queued", "queued_id": queue_id, "error": str(exc)}


async def send_imessage_with_attachment(
    phone: str,
    message: str,
    file_path: str,
) -> dict:
    """
    Send an iMessage with a file attachment via BlueBubbles.
    Writes to outreach_queue first, then attempts direct dispatch.

    Args:
        phone:      Recipient phone number
        message:    Message text
        file_path:  Absolute path to the file to attach

    Returns:
        BlueBubbles API response dict or queued status
    """
    from dashboard.services.outreach_queue import enqueue_message
    from dashboard.extensions import get_collection
    
    # 1. Write to outreach queue first
    queue_id = await enqueue_message(phone, message, file_path=file_path, context="attachment")
    
    # 2. Attempt immediate direct send
    try:
        result = await _send_attachment_direct(phone, message, file_path)
        if result.get("success"):
            logger.info("[bb_client] ✅ Attachment sent to ...%s (Queue ID: %s)", phone[-4:], queue_id)
            # Update queue record to sent
            await get_collection("outreach_queue").update_one(
                {"_id": ObjectId(queue_id)},
                {"$set": {"status": "sent", "sent_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}}
            )
            return {"success": True, "channel": "imessage", "data": result.get("data")}
        else:
            logger.warning("[bb_client] Direct attachment send failed for %s, remains queued (Queue ID: %s): %s", phone, queue_id, result.get("error"))
            return {"success": True, "status": "queued", "channel": "queued", "queued_id": queue_id, "error": result.get("error")}
    except Exception as exc:
        logger.error("[bb_client] Direct attachment send exception for %s, remains queued (Queue ID: %s): %s", phone, queue_id, exc)
        return {"success": True, "status": "queued", "channel": "queued", "queued_id": queue_id, "error": str(exc)}
