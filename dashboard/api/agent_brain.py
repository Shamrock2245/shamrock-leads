"""
ShamrockLeads — Agent Brain (iMessage Conversation Engine)
Autonomous AI agent for handling inbound iMessage conversations.

Borrows conversational patterns from:
  - Shannon (ElevenLabs_AfterHoursAgent.js) — empathetic intake, data extraction
  - Manus Brain (Manus_Brain.js) — system prompt, OpenAI routing, intent classification

Architecture:
  Inbound message → match to lead → classify intent → generate response →
  smart behaviors (typing, react, mark-read) → send via BB → log to MongoDB
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
#  System Prompt — Adapted from Shannon + Manus
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are "Shamrock", the digital outreach assistant for Shamrock Bail Bonds, \
the premier bail bond agency in Southwest Florida.

You handle iMessage conversations with prospective clients (indemnitors) \
who are looking to bond a loved one out of jail.

ROLE: Warm, professional, action-oriented. People texting you have a \
loved one in jail. Be empathetic but focused on moving toward signing.

RULES:
- Never discuss pricing, percentages, or premium amounts via text
- Never make promises about release times
- If asked about cost: "Our bondsman will walk you through all the options — would a quick call work?"
- Keep responses SHORT (2-3 sentences max — this is texting, not email)
- Always end with a clear next step or question
- Never give legal advice
- Be conversational, not robotic. Use natural language.
- Include the occasional emoji but don't overdo it 🍀

CONTEXT (injected per-message):
- Defendant: {defendant_name}
- County: {county}
- Bond Amount: {bond_amount}
- Charges: {charges}
- Current Pipeline Stage: {stage}

RESPOND in plain text only. No JSON, no markdown, no formatting."""


CLASSIFY_PROMPT = """Classify this incoming text message into exactly one category:
- interested: person wants to bond someone out, asking how to proceed, or expressing urgency
- question: asking about the process, cost, timeline, logistics, or general inquiry
- not_interested: declining help, already bonded out, not relevant, or telling us to stop
- wrong_number: person says wrong number, doesn't know the defendant, or is confused
- spam: irrelevant, automated, abusive, or nonsensical

Message: "{message}"

Respond with ONLY the category name, nothing else."""


# ═══════════════════════════════════════════════════════════════════════════════
#  Response Templates (Fallback when OpenAI unavailable)
# ═══════════════════════════════════════════════════════════════════════════════

TEMPLATES = {
    "first_response": (
        "Hi! This is Shamrock Bail Bonds. Thanks for reaching out — "
        "we're here to help get {defendant_name} home. "
        "A bondsman will be with you shortly. 🍀"
    ),
    "after_hours": (
        "Thanks for your message! Our office is currently closed but "
        "we'll get back to you first thing in the morning. "
        "For urgent matters, call us at (239) 955-0178. 🍀"
    ),
    "interested": (
        "Great to hear from you! We can definitely help with "
        "{defendant_name}'s situation in {county} County. "
        "What's the best number to reach you at for a quick call?"
    ),
    "question": (
        "Great question! A licensed bondsman can walk you through everything. "
        "Would you like a quick call, or feel free to text your question here?"
    ),
    "not_interested": (
        "No problem at all. If anything changes, we're here 24/7. "
        "Good luck! 🍀"
    ),
    "wrong_number": (
        "Sorry about that! We must have the wrong number. "
        "We'll remove you from our list. Have a great day!"
    ),
    "spam": None,  # Don't respond to spam
    "default": (
        "Thanks for your message! A bondsman will get back to you shortly. "
        "If urgent, call us at (239) 955-0178. 🍀"
    ),
}


# ═══════════════════════════════════════════════════════════════════════════════
#  OpenAI Integration (mirrors Shannon's callOpenAI pattern)
# ═══════════════════════════════════════════════════════════════════════════════

async def _call_openai(system_prompt: str, user_message: str,
                       temperature: float = 0.7,
                       max_tokens: int = 200) -> Optional[str]:
    """Call OpenAI GPT-4o-mini for text generation.
    Returns the response text or None on failure.
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set — falling back to templates")
        return None

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error("OpenAI call failed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Intent Classification
# ═══════════════════════════════════════════════════════════════════════════════

VALID_INTENTS = {"interested", "question", "not_interested", "wrong_number", "spam"}


async def classify_intent(message_text: str) -> str:
    """Classify an inbound message into an intent category using GPT-4o-mini.
    Falls back to 'question' if classification fails.
    """
    prompt = CLASSIFY_PROMPT.format(message=message_text)
    result = await _call_openai(
        "You are a message classifier. Respond with exactly one word.",
        prompt,
        temperature=0.1,
        max_tokens=20,
    )

    if result and result.lower().strip() in VALID_INTENTS:
        return result.lower().strip()

    # Fallback: simple keyword heuristics
    lower = message_text.lower()
    if any(w in lower for w in ["wrong number", "don't know", "who is this", "stop"]):
        return "wrong_number"
    if any(w in lower for w in ["not interested", "no thanks", "already", "don't need"]):
        return "not_interested"
    if any(w in lower for w in ["how much", "cost", "price", "payment", "how does", "how long"]):
        return "question"
    if any(w in lower for w in ["yes", "help", "please", "need", "want", "asap", "urgent", "bail"]):
        return "interested"

    return "question"


# ═══════════════════════════════════════════════════════════════════════════════
#  Response Generation
# ═══════════════════════════════════════════════════════════════════════════════

def get_lead_context(bond_doc: dict) -> dict:
    """Extract lead context from a prospective_bonds document."""
    return {
        "defendant_name": bond_doc.get("defendant_name", "your loved one"),
        "county": bond_doc.get("county", "the"),
        "bond_amount": bond_doc.get("bond_amount", ""),
        "charges": bond_doc.get("charges_description", bond_doc.get("charges", "")),
        "stage": bond_doc.get("stage", "contacted"),
        "booking_number": bond_doc.get("booking_number", ""),
    }


async def generate_response(lead_context: dict, message_text: str,
                             intent: str, ai_enabled: bool = True) -> Optional[str]:
    """Generate a response to an inbound message.

    Uses OpenAI if ai_enabled and available, otherwise falls back to templates.
    Returns None if we shouldn't respond (e.g. spam).
    """
    # Don't respond to spam
    if intent == "spam":
        logger.info("Spam detected — no response for: %s", message_text[:50])
        return None

    # Try AI-powered response first
    if ai_enabled and intent in ("question", "interested"):
        filled_prompt = SYSTEM_PROMPT.format(**lead_context)
        ai_response = await _call_openai(
            filled_prompt,
            f'The client just texted: "{message_text}"',
            temperature=0.7,
            max_tokens=200,
        )
        if ai_response:
            return ai_response

    # Fallback to templates
    template = TEMPLATES.get(intent, TEMPLATES["default"])
    if template is None:
        return None  # e.g. spam template is None

    try:
        return template.format(**lead_context)
    except KeyError:
        return template


# ═══════════════════════════════════════════════════════════════════════════════
#  Cooldown & Dedup Guard
# ═══════════════════════════════════════════════════════════════════════════════

async def should_auto_reply(phone: str, booking_number: str,
                            db, config: dict) -> tuple[bool, str]:
    """Check whether we should auto-reply to this inbound message.

    Returns:
        (should_reply: bool, reason: str)
    """
    if not config.get("enabled", False):
        return False, "auto_reply_disabled"

    # First-reply-only guard: check if we already auto-replied to this lead
    if config.get("first_reply_only", True):
        outreach = db["imessage_outreach"]
        existing_reply = await outreach.find_one({
            "recipient_phone": phone,
            "booking_number": booking_number,
            "sent_by": "auto_reply",
        })
        if existing_reply:
            return False, "already_auto_replied"

    # Cooldown guard: check if any auto-reply was sent within cooldown window
    cooldown_minutes = config.get("cooldown_minutes", 60)
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)).isoformat()
    outreach = db["imessage_outreach"]
    recent = await outreach.find_one({
        "recipient_phone": phone,
        "sent_by": "auto_reply",
        "sent_at": {"$gte": cutoff},
    })
    if recent:
        return False, "cooldown_active"

    # Business hours check
    if config.get("business_hours_only", False):
        from dashboard.extensions import is_business_hours
        if not is_business_hours(config):
            return True, "after_hours"  # Still reply, but use after_hours template

    return True, "ok"


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Processing Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

async def process_inbound(phone: str, message_text: str,
                          chat_guid: str, message_guid: str,
                          bond_doc: dict, db, config: dict,
                          bb_client=None) -> dict:
    """Full inbound message processing pipeline.

    1. Get lead context
    2. Classify intent
    3. Check cooldown/dedup
    4. Generate response (AI or template)
    5. Smart behaviors (typing, react, mark-read)
    6. Send response
    7. Log everything to MongoDB

    Returns:
        { responded: bool, intent: str, response: str|None, reason: str }
    """
    context = get_lead_context(bond_doc)
    booking_number = context["booking_number"]

    # 1. Classify intent
    intent = await classify_intent(message_text)
    logger.info(
        "📨 Inbound from %s → intent=%s (defendant=%s, bk=%s)",
        phone[-4:], intent, context["defendant_name"], booking_number
    )

    # 2. Log the inbound message to MongoDB
    inbound_doc = {
        "booking_number": booking_number,
        "defendant_name": context["defendant_name"],
        "county": context["county"],
        "recipient_phone": phone,
        "message": message_text,
        "chat_guid": chat_guid,
        "bb_message_guid": message_guid,
        "direction": "inbound",
        "intent": intent,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "status": "received",
        "sent_by": "lead",
    }
    outreach_coll = db["imessage_outreach"]
    await outreach_coll.insert_one(inbound_doc)

    # 3. Append to prospective_bonds communication_log
    bonds_coll = db["prospective_bonds"]
    await bonds_coll.update_one(
        {"booking_number": booking_number},
        {"$push": {"communication_log": {
            "channel": "imessage",
            "direction": "inbound",
            "message": message_text,
            "intent": intent,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "lead",
        }}}
    )

    # 4. Check if we should auto-reply
    should_reply, reason = await should_auto_reply(phone, booking_number, db, config)

    if not should_reply:
        logger.info("⏭️  No auto-reply: %s (phone=%s, bk=%s)", reason, phone[-4:], booking_number)
        return {
            "responded": False,
            "intent": intent,
            "response": None,
            "reason": reason,
        }

    # 5. Determine response type
    ai_enabled = config.get("ai_enabled", True)

    # Use after_hours template if outside business hours
    if reason == "after_hours":
        response_text = TEMPLATES["after_hours"].format(**context)
    else:
        response_text = await generate_response(context, message_text, intent, ai_enabled)

    if not response_text:
        logger.info("⏭️  No response generated for intent=%s", intent)
        return {
            "responded": False,
            "intent": intent,
            "response": None,
            "reason": "no_response_for_intent",
        }

    # 6. Smart behaviors + send via BB client
    if bb_client:
        # Auto-react on "interested" replies
        if config.get("auto_react_interested", True) and intent == "interested":
            await bb_client.react(chat_guid, message_guid, "love")

        # Simulate typing if enabled
        if config.get("simulate_typing", True):
            typing_delay = config.get("typing_delay_seconds", 3)
            result = await bb_client.send_human_like(
                chat_guid, response_text,
                typing_delay=typing_delay,
                mark_read=config.get("auto_mark_read", True),
            )
        else:
            result = await bb_client.send_text(chat_guid, response_text)
            if config.get("auto_mark_read", True):
                await bb_client.mark_read(chat_guid)

        # Extract message GUID from send result for tracking
        sent_guid = ""
        if result.get("success"):
            data = result.get("data", {})
            if isinstance(data, dict):
                sent_guid = data.get("guid", "")
    else:
        result = {"success": False, "error": "no_bb_client"}
        sent_guid = ""

    # 7. Log the auto-reply to MongoDB
    reply_doc = {
        "booking_number": booking_number,
        "defendant_name": context["defendant_name"],
        "county": context["county"],
        "recipient_phone": phone,
        "message": response_text,
        "chat_guid": chat_guid,
        "bb_message_guid": sent_guid,
        "direction": "outbound",
        "intent": intent,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "status": "sent" if result.get("success") else "failed",
        "sent_by": "auto_reply",
        "ai_generated": ai_enabled and intent in ("question", "interested"),
    }
    await outreach_coll.insert_one(reply_doc)

    # 8. Append auto-reply to communication_log
    await bonds_coll.update_one(
        {"booking_number": booking_number},
        {"$push": {"communication_log": {
            "channel": "imessage",
            "direction": "outbound",
            "message": response_text,
            "intent": intent,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "auto_reply",
        }}}
    )

    # 9. Handle wrong_number — auto-close the lead
    if intent == "wrong_number":
        await bonds_coll.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "status": "closed",
                "outcome": "wrong_number",
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }}
        )
        logger.info("🚫 Wrong number — auto-closed lead %s", booking_number)

    logger.info(
        "✅ Auto-replied to %s (intent=%s, ai=%s, bk=%s)",
        phone[-4:], intent, reply_doc["ai_generated"], booking_number
    )

    return {
        "responded": True,
        "intent": intent,
        "response": response_text,
        "reason": reason,
        "bb_message_guid": sent_guid,
    }
