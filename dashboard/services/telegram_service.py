"""
ShamrockLeads — Telegram Delivery Service
Sends signing links, payment links, and notifications via Telegram Bot API.

Configuration (set in .env):
    TELEGRAM_BOT_TOKEN   — Bot token from @BotFather (required to enable Telegram)
    TELEGRAM_STAFF_CHAT  — Chat ID for internal staff alerts (optional)

Usage:
    from dashboard.services.telegram_service import TelegramService
    tg = TelegramService()
    await tg.send_signing_link(chat_id, defendant_name, signing_link)
    await tg.send_payment_link(chat_id, indemnitor_name, amount, payment_url)
    await tg.send_staff_alert(message)

Notes:
    - chat_id can be a Telegram user ID (integer) or @username (string)
    - If TELEGRAM_BOT_TOKEN is not set, all methods return gracefully with
      success=False and no exception is raised (safe to call unconditionally)
    - All methods are async-safe and use httpx for non-blocking HTTP
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramService:
    """
    Thin async wrapper around the Telegram Bot API.
    Handles signing link delivery, payment link delivery, and staff alerts.
    """

    def __init__(self, bot_token: Optional[str] = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.staff_chat_id = os.getenv("TELEGRAM_STAFF_CHAT", "")
        self._enabled = bool(self.bot_token)
        if not self._enabled:
            logger.debug("[telegram] TELEGRAM_BOT_TOKEN not set — Telegram delivery disabled")

    # ── Low-level send ────────────────────────────────────────────────────────

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        parse_mode: str = "Markdown",
        disable_web_page_preview: bool = False,
    ) -> dict:
        """
        Send a text message via Telegram Bot API.

        Returns:
            { "success": bool, "message_id": int|None, "error": str|None }
        """
        if not self._enabled:
            return {"success": False, "error": "telegram_not_configured"}

        url = _TELEGRAM_API_BASE.format(token=self.bot_token, method="sendMessage")
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("ok"):
                    msg_id = data.get("result", {}).get("message_id")
                    logger.info("[telegram] Message sent to %s (msg_id=%s)", chat_id, msg_id)
                    return {"success": True, "message_id": msg_id}
                else:
                    err = data.get("description", "unknown_error")
                    logger.warning("[telegram] Send failed to %s: %s", chat_id, err)
                    return {"success": False, "error": err}
        except Exception as exc:
            logger.error("[telegram] send_message exception for %s: %s", chat_id, exc)
            return {"success": False, "error": str(exc)}

    # ── High-level helpers ────────────────────────────────────────────────────

    async def send_signing_link(
        self,
        chat_id: str | int,
        defendant_name: str,
        signing_link: str,
        indemnitor_name: str = "",
        phase: int = 1,
    ) -> dict:
        """
        Send a SignNow signing link to an indemnitor via Telegram.

        Args:
            chat_id:         Telegram chat ID or @username of the recipient
            defendant_name:  Defendant's full name
            signing_link:    SignNow embedded signing URL
            indemnitor_name: Indemnitor's name (for personalization)
            phase:           1 = pre-release (indemnitor docs), 2 = post-release
        """
        first_name = indemnitor_name.split()[0] if indemnitor_name else "there"
        phase_label = "pre-release" if phase == 1 else "post-release"

        text = (
            f"Hi {first_name}! 🍀 *Shamrock Bail Bonds*\n\n"
            f"Please sign the {phase_label} bond documents for "
            f"*{defendant_name}*.\n\n"
            f"📝 *Tap to sign* (takes ~2 min):\n{signing_link}\n\n"
            f"Questions? Call/text: *(239) 332-2245*"
        )
        return await self.send_message(chat_id, text, disable_web_page_preview=False)

    async def send_payment_link(
        self,
        chat_id: str | int,
        indemnitor_name: str,
        amount: float,
        payment_url: str,
        defendant_name: str = "",
    ) -> dict:
        """
        Send a SwipeSimple payment link to an indemnitor via Telegram.

        Args:
            chat_id:         Telegram chat ID or @username
            indemnitor_name: Indemnitor's name
            amount:          Premium amount in dollars
            payment_url:     SwipeSimple payment URL
            defendant_name:  Defendant's name (optional, for context)
        """
        first_name = indemnitor_name.split()[0] if indemnitor_name else "there"
        bond_context = f" for {defendant_name}'s bond" if defendant_name else ""

        text = (
            f"Hi {first_name}! 🍀 *Shamrock Bail Bonds*\n\n"
            f"Your bond premium of *${amount:,.2f}*{bond_context} is ready to pay.\n\n"
            f"💳 *Pay securely here*:\n{payment_url}\n\n"
            f"Questions? Call/text: *(239) 332-2245*"
        )
        return await self.send_message(chat_id, text, disable_web_page_preview=False)

    async def send_walkout_notification(
        self,
        chat_id: str | int,
        indemnitor_name: str,
        defendant_name: str,
        county: str,
        next_court_date: str = "TBD",
        court_location: str = "Lee County Justice Center",
    ) -> dict:
        """
        Send a walk-out / release notification to an indemnitor via Telegram.
        """
        first_name = indemnitor_name.split()[0] if indemnitor_name else "there"

        text = (
            f"Hi {first_name}! 🎉 *Shamrock Bail Bonds*\n\n"
            f"Great news — *{defendant_name}* has been released from "
            f"{county} County Jail!\n\n"
            f"⚠️ They *MUST appear* for ALL court dates.\n"
            f"📅 Next date: *{next_court_date}*\n"
            f"📍 Location: {court_location}\n\n"
            f"We'll send the remaining paperwork shortly.\n"
            f"Questions? Call/text: *(239) 332-2245*"
        )
        return await self.send_message(chat_id, text)

    async def send_payment_receipt(
        self,
        chat_id: str | int,
        indemnitor_name: str,
        amount: float,
        defendant_name: str = "",
        card_brand: str = "",
        card_last4: str = "",
        transaction_id: str = "",
    ) -> dict:
        """
        Send a payment receipt confirmation to an indemnitor via Telegram.
        """
        first_name = indemnitor_name.split()[0] if indemnitor_name else "there"
        bond_context = f" for {defendant_name}'s bond" if defendant_name else ""
        card_info = f"{card_brand} ending in {card_last4}" if card_last4 else "card on file"
        txn_info = f"\nTXN: `{transaction_id}`" if transaction_id else ""

        text = (
            f"Hi {first_name}! ✅ *Payment Received*\n\n"
            f"We received *${amount:,.2f}*{bond_context} via {card_info}.{txn_info}\n\n"
            f"Thank you! Your receipt has been recorded.\n"
            f"— Shamrock Bail Bonds 🍀"
        )
        return await self.send_message(chat_id, text)

    async def send_staff_alert(self, message: str) -> dict:
        """
        Send an internal staff alert to the configured TELEGRAM_STAFF_CHAT.
        No-ops silently if TELEGRAM_STAFF_CHAT is not set.
        """
        if not self.staff_chat_id:
            return {"success": False, "error": "no_staff_chat_configured"}
        return await self.send_message(self.staff_chat_id, message)

    async def send_document_signed_alert(
        self,
        defendant_name: str,
        booking_number: str,
        drive_url: str = "",
    ) -> dict:
        """
        Fire a staff alert when a SignNow document is completed.
        """
        drive_link = f"\n📁 [View in Drive]({drive_url})" if drive_url else ""
        msg = (
            f"✅ *SignNow Complete*\n"
            f"Defendant: *{defendant_name}*\n"
            f"Booking: `{booking_number}`{drive_link}"
        )
        return await self.send_staff_alert(msg)

    async def send_new_lead_alert(
        self,
        defendant_name: str,
        county: str,
        bond_amount: float,
        score: int,
        source: str = "scraper",
    ) -> dict:
        """
        Fire a staff alert when a new high-score lead is scraped.
        """
        msg = (
            f"🚨 *New Lead — Score {score}*\n"
            f"Defendant: *{defendant_name}*\n"
            f"County: {county}\n"
            f"Bond: *${bond_amount:,.0f}*\n"
            f"Source: {source}"
        )
        return await self.send_staff_alert(msg)


# ── Module-level convenience singleton ───────────────────────────────────────

_default_service: Optional[TelegramService] = None


def get_telegram_service() -> TelegramService:
    """Return the module-level singleton TelegramService instance."""
    global _default_service
    if _default_service is None:
        _default_service = TelegramService()
    return _default_service


async def tg_send_signing_link(
    chat_id: str | int,
    defendant_name: str,
    signing_link: str,
    indemnitor_name: str = "",
    phase: int = 1,
) -> dict:
    """Module-level shortcut for send_signing_link."""
    return await get_telegram_service().send_signing_link(
        chat_id, defendant_name, signing_link, indemnitor_name, phase
    )


async def tg_send_payment_link(
    chat_id: str | int,
    indemnitor_name: str,
    amount: float,
    payment_url: str,
    defendant_name: str = "",
) -> dict:
    """Module-level shortcut for send_payment_link."""
    return await get_telegram_service().send_payment_link(
        chat_id, indemnitor_name, amount, payment_url, defendant_name
    )


async def tg_staff_alert(message: str) -> dict:
    """Module-level shortcut for send_staff_alert."""
    return await get_telegram_service().send_staff_alert(message)
