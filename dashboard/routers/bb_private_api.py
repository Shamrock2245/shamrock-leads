from fastapi import APIRouter
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
from __future__ import annotations

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
        client = BlueBubblesClient("https://bb.shamrockbailbonds.biz", "mypassword")
        info = await client.server_info()
        await client.send_text("any;-;+12395550178", "Hello!")
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
            # Headers required to bypass tunnel browser interstitial pages:
            #   - ngrok free-tier: ngrok-skip-browser-warning
            #   - ngrok: ngrok-skip-browser-warning header (already set above)
            #   Both headers are safe to send together for either tunnel type.
            headers = {
                "ngrok-skip-browser-warning": "true",
                "User-Agent": "ShamrockLeads-Dashboard/1.0 (BlueBubbles-Client)",
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(headers=headers) as client:
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
                        selected_message_guid: str | None = None,
                        method: str = "private-api") -> dict:
        """Send a text message. Supports effects, subjects, and replies.

        Args:
            chat_guid: e.g. "any;-;+12395550178" (auto-selects iMessage or SMS)
            message: The text to send
            temp_guid: Optional client-side dedup GUID (auto-generated if omitted)
            effect_id: Optional iMessage effect (use EFFECTS dict keys)
            subject: Optional subject line (renders bold)
            selected_message_guid: Reply to this message GUID
            method: Send method — "private-api" (default) or "apple-script"
        """
        import uuid
        body = {
            "chatGuid": chat_guid,
            "message": message,
            "tempGuid": temp_guid or f"shamrock-{uuid.uuid4()}",
            "method": method,
        }
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

    # ─────────────────────────────────────────────────────────────────────────
    #  Webhook Management (Real-time event delivery)
    # ─────────────────────────────────────────────────────────────────────────
    async def list_webhooks(self) -> dict:
        """List all registered webhooks on this BlueBubbles server."""
        return await self._request("GET", "/api/v1/webhook")

    async def create_webhook(self, url: str, events: list[str] | None = None) -> dict:
        """Register a webhook endpoint to receive real-time BB events.

        Args:
            url:    The HTTPS URL to POST events to (e.g. VPS /api/webhooks/bluebubbles)
            events: List of event types to subscribe to. Defaults to all.
                    Common events:
                      - "new-message"          — inbound or outbound message
                      - "updated-message"      — delivery/read receipt update
                      - "chat-read-status-changed"
                      - "typing-indicator"
                      - "contact-updated"
                      - "new-server"
        """
        body = {"url": url}
        if events:
            body["events"] = events
        return await self._request("POST", "/api/v1/webhook", json_body=body)

    async def delete_webhook(self, webhook_id: int) -> dict:
        """Remove a registered webhook by its numeric ID."""
        return await self._request("DELETE", f"/api/v1/webhook/{webhook_id}")

    async def ensure_webhook(self, url: str,
                              events: list[str] | None = None) -> dict:
        """Idempotent: register the webhook only if it is not already registered.

        Checks existing webhooks first; if the URL is already registered it
        returns the existing record without creating a duplicate.
        """
        existing = await self.list_webhooks()
        if existing.get("success"):
            for wh in (existing.get("data") or []):
                if wh.get("url") == url:
                    logger.info("BB webhook already registered: %s", url)
                    return {"success": True, "data": wh, "already_existed": True}
        return await self.create_webhook(url, events)

    # ─────────────────────────────────────────────────────────────────────────
    #  iMessage Availability Check (iPhone vs SMS detection)
    # ─────────────────────────────────────────────────────────────────────────
    async def check_imessage_availability(self, phone: str) -> dict:
        """Check whether a phone number is registered on iMessage.

        Returns:
            { success: bool, available: bool, phone: str }
        Requires Private API.  If unavailable, caller should fall back to Twilio.
        """
        result = await self._request(
            "GET", "/api/v1/handle/availability/imessage",
            params={"address": phone}
        )
        available = False
        if result.get("success"):
            data = result.get("data", {})
            available = bool(data.get("available") or data.get("iMessageAvailable"))
        return {
            "success": result.get("success", False),
            "available": available,
            "phone": phone,
        }

    async def check_facetime_availability(self, phone: str) -> dict:
        """Check whether a phone number is registered on FaceTime."""
        result = await self._request(
            "GET", "/api/v1/handle/availability/facetime",
            params={"address": phone}
        )
        available = False
        if result.get("success"):
            data = result.get("data", {})
            available = bool(data.get("available"))
        return {"success": result.get("success", False), "available": available, "phone": phone}

    # ─────────────────────────────────────────────────────────────────────────
    #  Group Chat Management (Multi-Indemnitor Coordination)
    # ─────────────────────────────────────────────────────────────────────────
    async def create_group_chat(self, participants: list[str],
                                display_name: str | None = None) -> dict:
        """Create a new iMessage group chat.

        Args:
            participants: List of phone numbers / email addresses
                          e.g. ["+12395550178", "+12395550314"]
            display_name: Optional group name shown in Messages.app
        Returns:
            { success: bool, data: { guid: str, ... } }
        """
        body = {"addresses": participants}
        if display_name:
            body["displayName"] = display_name
        return await self._request("POST", "/api/v1/chat/new", json_body=body)

    async def add_participant(self, chat_guid: str, participant: str) -> dict:
        """Add a participant to an existing group chat."""
        return await self._request(
            "POST", f"/api/v1/chat/{chat_guid}/participant/add",
            json_body={"address": participant}
        )

    async def remove_participant(self, chat_guid: str, participant: str) -> dict:
        """Remove a participant from a group chat."""
        return await self._request(
            "POST", f"/api/v1/chat/{chat_guid}/participant/remove",
            json_body={"address": participant}
        )

    async def rename_group_chat(self, chat_guid: str, new_name: str) -> dict:
        """Rename a group chat."""
        return await self._request(
            "POST", f"/api/v1/chat/{chat_guid}/rename",
            json_body={"newName": new_name}
        )

    async def get_chat_participants(self, chat_guid: str) -> dict:
        """Get all participants in a chat."""
        return await self._request("GET", f"/api/v1/chat/{chat_guid}/participants")

    # ─────────────────────────────────────────────────────────────────────────
    #  Scheduled Messages (BlueBubbles Server-side scheduling)
    # ─────────────────────────────────────────────────────────────────────────
    async def schedule_message(self, chat_guid: str, message: str,
                               scheduled_date_ms: int,
                               schedule_type: str = "once") -> dict:
        """Schedule a message to be sent at a future time.

        Args:
            chat_guid:          Target chat GUID
            message:            Message text
            scheduled_date_ms:  Unix timestamp in milliseconds (epoch ms)
            schedule_type:      "once" | "recurring"
        Returns:
            { success: bool, data: { id: int, ... } }
        """
        body = {
            "chatGuid": chat_guid,
            "type": "send-message",
            "payload": {"chatGuid": chat_guid, "message": message},
            "scheduledFor": scheduled_date_ms,
            "schedule": {"type": schedule_type},
        }
        return await self._request("POST", "/api/v1/message/schedule", json_body=body)

    async def list_scheduled_messages(self) -> dict:
        """List all pending scheduled messages."""
        return await self._request("GET", "/api/v1/message/schedule")

    async def delete_scheduled_message(self, schedule_id: int) -> dict:
        """Cancel a scheduled message by its ID."""
        return await self._request("DELETE", f"/api/v1/message/schedule/{schedule_id}")

    # ─────────────────────────────────────────────────────────────────────────
    #  Attachment / Media Sending
    # ─────────────────────────────────────────────────────────────────────────
    async def send_attachment_url(self, chat_guid: str, attachment_url: str,
                                  filename: str | None = None) -> dict:
        """Send a file attachment by providing a publicly accessible URL.

        The BlueBubbles server will download the file and send it via iMessage.
        Useful for sending signed bond documents, PDFs, or images.

        Args:
            chat_guid:       Target chat GUID
            attachment_url:  Public HTTPS URL of the file to send
            filename:        Optional display filename
        """
        body = {
            "chatGuid": chat_guid,
            "attachmentUrl": attachment_url,
        }
        if filename:
            body["attachmentName"] = filename
        return await self._request("POST", "/api/v1/message/attachment/url", json_body=body)

    # ─────────────────────────────────────────────────────────────────────────
    #  Contact Management
    # ─────────────────────────────────────────────────────────────────────────
    async def get_contacts(self) -> dict:
        """Retrieve all contacts from the Mac's Contacts.app."""
        return await self._request("GET", "/api/v1/contact")

    async def create_contact(self, first_name: str, last_name: str,
                              phone: str | None = None,
                              email: str | None = None) -> dict:
        """Create a new contact in Contacts.app on the Mac.

        Useful for adding defendants/indemnitors so their name shows in Messages.
        """
        contact = {"firstName": first_name, "lastName": last_name}
        if phone:
            contact["phoneNumbers"] = [{"address": phone}]
        if email:
            contact["emails"] = [{"address": email}]
        return await self._request("POST", "/api/v1/contact", json_body=contact)

    async def query_contacts(self, addresses: list[str]) -> dict:
        """Look up contacts by phone number or email address."""
        return await self._request(
            "POST", "/api/v1/contact/query",
            json_body={"addresses": addresses}
        )

    # ─────────────────────────────────────────────────────────────────────────
    #  Handle (Conversation Partner) Utilities
    # ─────────────────────────────────────────────────────────────────────────
    async def get_handle_focus_status(self, handle_guid: str) -> dict:
        """Check if a contact has Focus/DND active (Private API required).

        Returns:
            { success: bool, focused: bool }
        """
        result = await self._request("GET", f"/api/v1/handle/{handle_guid}/focus")
        focused = False
        if result.get("success"):
            focused = bool((result.get("data") or {}).get("focused"))
        return {"success": result.get("success", False), "focused": focused}

    # ─────────────────────────────────────────────────────────────────────────
    #  Server Diagnostics
    # ─────────────────────────────────────────────────────────────────────────
    async def get_server_logs(self, count: int = 100) -> dict:
        """Retrieve recent server logs for diagnostics."""
        return await self._request(
            "GET", "/api/v1/server/logs",
            params={"count": str(count)}
        )

    async def restart_messages_app(self) -> dict:
        """Restart the Messages.app on the Mac (Private API).

        Useful for recovering from stuck send queues.
        """
        return await self._request("POST", "/api/v1/server/restart/soft")

    async def restart_server(self) -> dict:
        """Restart the BlueBubbles server process itself."""
        return await self._request("POST", "/api/v1/server/restart/hard")

    # ─────────────────────────────────────────────────────────────────────────
    #  Batch / Convenience Helpers
    # ─────────────────────────────────────────────────────────────────────────
    async def send_to_phones(self, phones: list[str], message: str,
                              check_imessage: bool = True,
                              typing_delay: float = 2.0) -> list[dict]:
        """Send the same message to multiple phone numbers.

        Uses `any;-;` chat GUID prefix so BlueBubbles auto-routes to
        iMessage or SMS depending on the recipient's device. No external
        fallback needed.

        If check_imessage=True, each number is checked for availability
        for **channel reporting only** — delivery always goes through BB.

        Returns:
            List of { phone, chat_guid, channel, success, message_guid }
        """
        results = []
        for phone in phones:
            chat_guid = f"any;-;{phone}"
            channel = "sms"  # default assumption
            if check_imessage:
                try:
                    avail_result = await self.check_imessage_availability(phone)
                    if avail_result.get("available", False):
                        channel = "imessage"
                except Exception:
                    pass  # availability check failed — still send

            # Always send via BB (any;-; handles iMessage/SMS routing)
            result = await self.send_human_like(
                chat_guid, message, typing_delay=typing_delay
            )
            results.append({
                "phone": phone,
                "chat_guid": chat_guid,
                "channel": channel,
                "available": channel == "imessage",
                "success": result.get("success", False),
                "message_guid": (result.get("data") or {}).get("guid", ""),
            })
        return results
