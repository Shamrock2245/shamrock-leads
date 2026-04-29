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
        await bb.send_text("iMessage;-;+12395550178", "Hello!")

    bb = get_default_bb_client()          # always returns first configured server
"""
import logging
from typing import Optional

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
        from dashboard.extensions import BB_SERVERS, get_bb_server_for
        from dashboard.api.bb_private_api import BlueBubblesClient

        if phone:
            server = get_bb_server_for(phone)
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


async def send_imessage(
    phone: str,
    message: str,
    geo_url: Optional[str] = None,
    method: str = "private-api",
) -> dict:
    """
    Send an iMessage via BlueBubbles with optional geolocator link.

    All outbound messages in ShamrockLeads must include a geolocator
    feature per project standards.

    Args:
        phone:    Recipient phone number (E.164 or 10-digit)
        message:  Message text to send
        geo_url:  Optional geolocator URL to append
        method:   BlueBubbles send method ("private-api" or "apple-script")

    Returns:
        BlueBubbles API response dict
    """
    bb = get_bb_client(phone)
    if not bb:
        return {"success": False, "error": "no_bb_server"}

    # Append geolocator link (mandatory per project standards)
    if geo_url:
        message = f"{message}\n\n📍 {geo_url}"

    chat_guid = f"iMessage;-;{phone}"
    try:
        return await bb.send_text(chat_guid, message, method=method)
    except Exception as exc:
        logger.error("[bb_client] send_imessage error to %s: %s", phone, exc)
        return {"success": False, "error": str(exc)}


async def send_imessage_with_attachment(
    phone: str,
    message: str,
    file_path: str,
    geo_url: Optional[str] = None,
) -> dict:
    """
    Send an iMessage with a file attachment via BlueBubbles.

    Args:
        phone:      Recipient phone number
        message:    Message text
        file_path:  Absolute path to the file to attach
        geo_url:    Optional geolocator URL to append to the message

    Returns:
        BlueBubbles API response dict
    """
    bb = get_bb_client(phone)
    if not bb:
        return {"success": False, "error": "no_bb_server"}

    if geo_url:
        message = f"{message}\n\n📍 {geo_url}"

    chat_guid = f"iMessage;-;{phone}"
    try:
        return await bb.send_attachment_url(chat_guid, file_path, message=message)
    except Exception as exc:
        logger.error("[bb_client] send_imessage_with_attachment error to %s: %s", phone, exc)
        return {"success": False, "error": str(exc)}
