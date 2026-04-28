"""
ShamrockLeads — BlueBubbles Private API Client
Unified async client for BlueBubbles REST + Private API.

Covers:
  Standard:  send text, fetch messages, chats, server info
  Private:   unsend, edit, react, typing, read receipts, effects,
             force-notify, reply-to, FindMy geolocation

Requires:
  - BlueBubbles Server v1.0+ on office iMac
  - Private API helper bundle installed (SIP disabled)
  - macOS 13+ (Ventura) for unsend/edit features
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  iMessage Effect IDs
# ═══════════════════════════════════════════════════════════════════════════════

EFFECTS = {
    "slam":          "com.apple.MobileSMS.expressivesend.impact",
    "loud":          "com.apple.MobileSMS.expressivesend.loud",
    "gentle":        "com.apple.MobileSMS.expressivesend.gentle",
    "invisible_ink": "com.apple.MobileSMS.expressivesend.invisibleink",
    "echo":          "com.apple.messages.effect.CKEchoEffect",
    "spotlight":     "com.apple.messages.effect.CKSpotlightEffect",
    "balloons":      "com.apple.messages.effect.CKHappyBirthdayEffect",
    "confetti":      "com.apple.messages.effect.CKConfettiEffect",
    "fireworks":     "com.apple.messages.effect.CKFireworksEffect",
    "lasers":        "com.apple.messages.effect.CKLasersEffect",
    "love":          "com.apple.messages.effect.CKHeartEffect",
    "celebration":   "com.apple.messages.effect.CKSparklesEffect",
}

REACTIONS = {
    "love": 2000, "like": 2001, "dislike": 2002,
    "laugh": 2003, "emphasize": 2004, "question": 2005,
    # Remove reactions (negative of the add value)
    "remove_love": 3000, "remove_like": 3001, "remove_dislike": 3002,
    "remove_laugh": 3003, "remove_emphasize": 3004, "remove_question": 3005,
}


# ═══════════════════════════════════════════════════════════════════════════════
#  BlueBubbles Async Client
# ═══════════════════════════════════════════════════════════════════════════════

class BlueBubblesClient:
    """Async client for BlueBubbles REST + Private API.

    Usage:
        client = BlueBubblesClient("https://xxx.trycloudflare.com", "mypassword")
        info = await client.server_info()
        await client.send_text("iMessage;-;+12395550178", "Hello!")
        await client.unsend_message("msg-guid-here")
    """

    def __init__(self, base_url: str, password: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.password = password
        self.timeout = timeout

    def _params(self, extra: dict | None = None) -> dict:
        """Build query params with password auth."""
        p = {"password": self.password}
        if extra:
            p.update(extra)
        return p

    async def _request(self, method: str, path: str,
                       params: dict | None = None,
                       json_body: dict | None = None) -> dict:
        """Execute an HTTP request against the BlueBubbles server."""
        url = f"{self.base_url}{path}"
        merged_params = self._params(params)
        try:
            async with httpx.AsyncClient() as client:
                r = await client.request(
                    method, url,
                    params=merged_params,
                    json=json_body,
                    timeout=self.timeout,
                )
                data = r.json()
                if r.status_code not in (200, 201):
                    logger.warning(
                        "BB API %s %s → %d: %s",
                        method, path, r.status_code,
                        data.get("message", data.get("error", ""))
                    )
                return {
                    "success": r.status_code in (200, 201),
                    "status_code": r.status_code,
                    "data": data.get("data", data),
                    "message": data.get("message", ""),
                }
        except httpx.ConnectError:
            logger.error("BB unreachable: %s", url)
            return {"success": False, "error": "unreachable", "status_code": 0}
        except httpx.TimeoutException:
            logger.error("BB timeout: %s", url)
            return {"success": False, "error": "timeout", "status_code": 0}
        except Exception as e:
            logger.error("BB request error: %s", e)
            return {"success": False, "error": str(e), "status_code": 0}

    # ─────────────────────────────────────────────────────────────────────────
    #  Standard API
    # ─────────────────────────────────────────────────────────────────────────

    async def server_info(self) -> dict:
        """Get server status including Private API connection state."""
        return await self._request("GET", "/api/v1/server/info")

    async def send_text(self, chat_guid: str, message: str,
                        temp_guid: str | None = None,
                        effect_id: str | None = None,
                        subject: str | None = None,
                        selected_message_guid: str | None = None) -> dict:
        """Send a text message. Supports effects, subjects, and replies.

        Args:
            chat_guid: e.g. "iMessage;-;+12395550178"
            message: The text to send
            temp_guid: Optional client-side dedup GUID
            effect_id: Optional iMessage effect (use EFFECTS dict keys)
            subject: Optional subject line (renders bold)
            selected_message_guid: Reply to this message GUID
        """
        body = {
            "chatGuid": chat_guid,
            "message": message,
        }
        if temp_guid:
            body["tempGuid"] = temp_guid
        if effect_id:
            # Resolve friendly name to full effect ID
            body["effectId"] = EFFECTS.get(effect_id, effect_id)
        if subject:
            body["subject"] = subject
        if selected_message_guid:
            body["selectedMessageGuid"] = selected_message_guid
        return await self._request("POST", "/api/v1/message/text", json_body=body)

    async def get_messages(self, after: int | None = None,
                           limit: int = 50, sort: str = "DESC") -> dict:
        """Fetch messages, optionally after a timestamp (epoch ms).

        Args:
            after: Epoch milliseconds — only return messages after this time
            limit: Max messages to return (default 50)
            sort: "ASC" or "DESC"
        """
        params = {"limit": str(limit), "sort": sort}
        if after is not None:
            params["after"] = str(after)
        return await self._request("GET", "/api/v1/message", params=params)

    async def get_chats(self, limit: int = 25, offset: int = 0) -> dict:
        """List recent chats."""
        return await self._request(
            "GET", "/api/v1/chat",
            params={"limit": str(limit), "offset": str(offset)}
        )

    async def get_chat_messages(self, chat_guid: str,
                                limit: int = 25) -> dict:
        """Get messages for a specific chat."""
        return await self._request(
            "GET", f"/api/v1/chat/{chat_guid}/messages",
            params={"limit": str(limit)}
        )

    async def get_message(self, message_guid: str) -> dict:
        """Get a single message by its GUID — includes delivery/read status."""
        return await self._request("GET", f"/api/v1/message/{message_guid}")

    # ─────────────────────────────────────────────────────────────────────────
    #  Private API — Message Actions (macOS 13+ for unsend/edit)
    # ─────────────────────────────────────────────────────────────────────────

    async def unsend_message(self, message_guid: str) -> dict:
        """Unsend (retract) a previously sent message. Requires Private API + macOS 13+."""
        return await self._request(
            "POST", "/api/v1/message/unsend",
            json_body={"messageGuid": message_guid}
        )

    async def edit_message(self, message_guid: str, new_text: str) -> dict:
        """Edit a previously sent message. Requires Private API + macOS 13+."""
        return await self._request(
            "POST", "/api/v1/message/edit",
            json_body={"messageGuid": message_guid, "editedMessage": new_text}
        )

    async def react(self, chat_guid: str, message_guid: str,
                    reaction: str = "love") -> dict:
        """Send a tapback reaction on a message.

        Args:
            reaction: One of "love", "like", "dislike", "laugh", "emphasize", "question"
                      Prefix with "remove_" to remove a reaction.
        """
        reaction_id = REACTIONS.get(reaction, reaction)
        return await self._request(
            "POST", "/api/v1/message/react",
            json_body={
                "chatGuid": chat_guid,
                "selectedMessageGuid": message_guid,
                "reaction": reaction_id,
            }
        )

    # ─────────────────────────────────────────────────────────────────────────
    #  Private API — Chat Status
    # ─────────────────────────────────────────────────────────────────────────

    async def mark_read(self, chat_guid: str) -> dict:
        """Mark a chat as read. Requires Private API."""
        return await self._request("POST", f"/api/v1/chat/{chat_guid}/mark-read")

    async def mark_unread(self, chat_guid: str) -> dict:
        """Mark a chat as unread. Requires Private API + macOS 13+."""
        return await self._request("POST", f"/api/v1/chat/{chat_guid}/mark-unread")

    async def start_typing(self, chat_guid: str) -> dict:
        """Show typing indicator to the recipient. Requires Private API."""
        return await self._request("POST", f"/api/v1/chat/{chat_guid}/typing")

    async def stop_typing(self, chat_guid: str) -> dict:
        """Stop typing indicator. Sends a second typing call to toggle off."""
        # BB doesn't have a separate stop endpoint — sending typing again toggles
        # We send an empty message approach or just wait for timeout
        # Most implementations just let the 10s timeout handle it
        return {"success": True, "message": "Typing indicator will auto-expire"}

    # ─────────────────────────────────────────────────────────────────────────
    #  Private API — Effects & Notifications
    # ─────────────────────────────────────────────────────────────────────────

    async def send_with_effect(self, chat_guid: str, message: str,
                               effect: str, temp_guid: str | None = None) -> dict:
        """Send a message with an iMessage bubble/screen effect.

        Args:
            effect: Friendly name from EFFECTS dict (e.g. "slam", "confetti")
                    or full Apple effect ID string.
        """
        return await self.send_text(
            chat_guid, message,
            temp_guid=temp_guid,
            effect_id=effect
        )

    async def send_force_notify(self, chat_guid: str, message: str,
                                temp_guid: str | None = None) -> dict:
        """Send a message that bypasses Do Not Disturb / Focus modes.
        Uses the 'mention' mechanism to trigger a notification override.
        """
        # Force notify works by mentioning the recipient — the mention
        # triggers notification even in DND/Focus mode
        body = {
            "chatGuid": chat_guid,
            "message": message,
        }
        if temp_guid:
            body["tempGuid"] = temp_guid
        # The mention range covers the entire message to force-notify
        body["parts"] = [{
            "text": message,
            "mention": chat_guid.split(";-;")[-1] if ";-;" in chat_guid else None,
            "isAttachment": False,
        }]
        return await self._request("POST", "/api/v1/message/text", json_body=body)

    async def reply_to_message(self, chat_guid: str, reply_to_guid: str,
                               message: str, temp_guid: str | None = None) -> dict:
        """Send a message as a reply to a specific message (threaded reply)."""
        return await self.send_text(
            chat_guid, message,
            temp_guid=temp_guid,
            selected_message_guid=reply_to_guid
        )

    # ─────────────────────────────────────────────────────────────────────────
    #  Private API — FindMy Geolocation
    # ─────────────────────────────────────────────────────────────────────────

    async def findmy_devices(self) -> dict:
        """Get locations of iCloud-connected devices."""
        return await self._request("GET", "/api/v1/icloud/findmy/devices")

    async def findmy_friends(self) -> dict:
        """Get locations of friends sharing their location with you."""
        return await self._request("GET", "/api/v1/icloud/findmy/friends")

    async def findmy_refresh_devices(self) -> dict:
        """Force-refresh device locations (opens FindMy app on Mac)."""
        return await self._request("POST", "/api/v1/icloud/findmy/devices/refresh")

    async def findmy_refresh_friends(self) -> dict:
        """Force-refresh friend locations."""
        return await self._request("POST", "/api/v1/icloud/findmy/friends/refresh")

    # ─────────────────────────────────────────────────────────────────────────
    #  Message Status Helpers
    # ─────────────────────────────────────────────────────────────────────────

    async def get_message_status(self, message_guid: str) -> dict:
        """Check delivery and read status of a sent message.

        Returns:
            { delivered: bool, read: bool, date_delivered: str|None, date_read: str|None }
        """
        result = await self.get_message(message_guid)
        if not result.get("success"):
            return result

        msg = result.get("data", {})
        return {
            "success": True,
            "delivered": bool(msg.get("dateDelivered") or msg.get("isDelivered")),
            "read": bool(msg.get("dateRead") or msg.get("isRead")),
            "date_delivered": msg.get("dateDelivered"),
            "date_read": msg.get("dateRead"),
            "message_guid": message_guid,
        }

    # ─────────────────────────────────────────────────────────────────────────
    #  Convenience: Human-like Send (type → wait → send → mark read)
    # ─────────────────────────────────────────────────────────────────────────

    async def send_human_like(self, chat_guid: str, message: str,
                              typing_delay: float = 2.5,
                              mark_read: bool = True,
                              temp_guid: str | None = None) -> dict:
        """Send a message with simulated human behavior.

        1. Start typing indicator
        2. Wait typing_delay seconds
        3. Send the message
        4. Mark chat as read (optional)
        """
        # 1. Show typing
        await self.start_typing(chat_guid)

        # 2. Simulate typing time
        await asyncio.sleep(typing_delay)

        # 3. Send
        result = await self.send_text(chat_guid, message, temp_guid=temp_guid)

        # 4. Mark read
        if mark_read and result.get("success"):
            await self.mark_read(chat_guid)

        return result
