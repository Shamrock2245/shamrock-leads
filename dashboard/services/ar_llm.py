"""
Reminder-draft provider.

Uses the repo's existing OpenAI chat-completions call when OPENAI_API_KEY
(or AR_LLM_API_KEY) is set. No key is hard-coded. Without a key, a
fact-only template still produces an editable draft from the bond numbers
the caller already supplied.
"""

from __future__ import annotations

import os
from typing import Optional, Protocol

import httpx

from dashboard.services.ar_math import cents_to_dollars


class ReminderLlm(Protocol):
    async def draft(self, *, system: str, user: str) -> Optional[str]:
        """Return draft text, or None when the provider cannot draft."""


class OpenAIReminderLlm:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def draft(self, *, system: str, user: str) -> Optional[str]:
        if not self.api_key:
            return None
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "temperature": 0.4,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                    },
                )
            if resp.status_code != 200:
                return None
            data = resp.json()
            return (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            ) or None
        except Exception:
            return None


class TemplateReminderLlm:
    """Deterministic draft from facts already on the bond. Not a live model."""

    async def draft(self, *, system: str, user: str) -> Optional[str]:
        return None


def llm_from_env() -> ReminderLlm:
    key = (os.getenv("OPENAI_API_KEY") or os.getenv("AR_LLM_API_KEY") or "").strip()
    model = (os.getenv("OPENAI_MODEL") or os.getenv("AR_LLM_MODEL") or "gpt-4o-mini").strip()
    if key:
        return OpenAIReminderLlm(key, model)
    return TemplateReminderLlm()


def template_draft(row: dict, *, recipient_name: str, channel: str) -> str:
    """Fact-only wording. Amounts come from the computed row, never invented."""
    name = (recipient_name or "there").strip() or "there"
    balance = row.get("balance_due_dollars")
    balance_text = f"${balance}" if balance is not None else "the remaining premium"
    due = row.get("next_payment_due")
    overdue = row.get("days_overdue")
    tone = row.get("tone") or "neutral"
    due_bit = f" It was due {due}." if due else ""
    late_bit = ""
    if isinstance(overdue, int) and overdue > 0:
        late_bit = f" It is {overdue} day{'s' if overdue != 1 else ''} past due."
    office = "Shamrock Bail Bonds in Fort Myers"
    if channel == "call":
        opener = f"Call script for Shannon. Speak only to {name}, the {('indemnitor' if name else 'client')} on this bond."
        if tone == "firm":
            body = (
                f"This is {office}. The premium balance is {balance_text}.{due_bit}{late_bit} "
                "Say plainly that the balance is still owed and that we will keep following up "
                "until it is paid. Do not mention arrest, jail, police, a lawsuit, or anyone who "
                "is not the indemnitor or defendant."
            )
        elif tone == "friendly":
            body = (
                f"This is {office}. Friendly check-in: the premium balance is {balance_text}.{due_bit}{late_bit} "
                "Ask if they can pay or set a date. Thank them if they already sent it."
            )
        else:
            body = (
                f"This is {office}. The premium balance is {balance_text}.{due_bit}{late_bit} "
                "Ask them to pay or call the office so the balance can be marked."
            )
        return f"{opener}\n\n{body}"
    if tone == "firm":
        return (
            f"Hi {name}, this is {office}. The premium balance of {balance_text} is still owed."
            f"{due_bit}{late_bit} Please pay it or call us today. We will keep following up until "
            "the balance is paid."
        )
    if tone == "friendly":
        return (
            f"Hi {name}, this is {office}. Quick note — the premium balance is {balance_text}."
            f"{due_bit}{late_bit} If you already sent it, thank you, just reply so we can mark it. "
            "If not, a payment or a call today keeps this current."
        )
    return (
        f"Hi {name}, this is {office}. The premium balance is {balance_text}."
        f"{due_bit}{late_bit} Please reply or call the office so we can get it current."
    )


def reminder_system_prompt() -> str:
    return (
        "You draft a short premium-collection reminder for Shamrock Bail Bonds in Fort Myers, Florida. "
        "Shamrock is collecting its own bail-bond premium. Florida Statute 559.72 applies. "
        "Rules you must follow: "
        "Use only the dollar amounts, dates, and names in the user message. Never invent an amount, a date, or a promise. "
        "Contact only the indemnitor or defendant named in the user message. Never tell the user to contact an employer, "
        "relative, reference, or any other third party about the debt. "
        "Never threaten arrest, jail, a warrant, the police, a lawsuit, garnishment, or any legal action. "
        "Do not say the person will go back to jail. "
        "Tone: friendly = a nudge for someone who usually pays on time and missed once. "
        "direct = clear and brief. "
        "firm = plain about the balance and that Shamrock will keep following up until it is paid, still with no threats. "
        "If channel is call, write a spoken script for the Shannon voice agent, not a text message. "
        "If channel is text, write one iMessage under 320 characters. No hashtags. No subject line."
    )


def reminder_user_prompt(row: dict, *, recipient_name: str, recipient_role: str, channel: str) -> str:
    balance = row.get("balance_due_dollars")
    premium = row.get("premium_dollars")
    down = row.get("down_payment_dollars")
    return "\n".join([
        f"channel: {channel}",
        f"tone: {row.get('tone') or 'neutral'}",
        f"sentiment_of_past_replies: {row.get('sentiment') or 'no_history'}",
        f"recipient_role: {recipient_role}",
        f"recipient_name: {recipient_name or 'the client'}",
        f"defendant_name: {row.get('defendant_name') or ''}",
        f"premium_dollars: {premium if premium is not None else 'not entered'}",
        f"down_payment_dollars: {down if down is not None else 'not entered'}",
        f"balance_due_dollars: {balance if balance is not None else 'unknown'}",
        f"next_payment_due: {row.get('next_payment_due') or 'none'}",
        f"days_overdue: {row.get('days_overdue') if row.get('days_overdue') is not None else 'unknown'}",
        f"last_payment_at: {row.get('last_payment_at') or 'none'}",
        f"on_time_count: {row.get('on_time_count', 0)}",
        f"late_or_missed_count: {row.get('late_count', 0)}",
        f"broken_promises: {row.get('broken_promises', 0)}",
        "Office: Shamrock Bail Bonds, Fort Myers. Text line is staff-sent. Do not include a phone number unless one is in this prompt. None is.",
        f"balance_check_cents: {row.get('balance_due_cents')}",
        f"formatted_balance: {cents_to_dollars(row['balance_due_cents']) if row.get('balance_due_cents') is not None else 'unknown'}",
    ])
