"""
ShamrockLeads — Agent Brain (iMessage Conversation Engine)
Autonomous multi-turn AI agent for handling inbound iMessage conversations.

Architecture:
  Inbound message → match to lead → load conversation history →
  classify intent → generate contextual response (with history) →
  smart behaviors (typing, react, mark-read) → send via BB → log to MongoDB

The agent keeps talking and gathering relevant information across multiple
exchanges — it is NOT a one-shot auto-responder. It maintains conversation
context and progressively extracts lead qualification data.

Borrows conversational patterns from:
  - Shannon (ElevenLabs_AfterHoursAgent.js) — empathetic intake, data extraction
  - Manus Brain (Manus_Brain.js) — system prompt, OpenAI routing, intent classification
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
#  System Prompt — Multi-Turn Conversational Agent
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are "Shamrock", the digital outreach assistant for Shamrock Bail Bonds, \
the premier bail bond agency in Southwest Florida.

You handle iMessage conversations with prospective clients (indemnitors) \
who are looking to bond a loved one out of jail.

ROLE: Warm, professional, action-oriented. People texting you have a \
loved one in jail. Be empathetic but focused on gathering information \
and moving toward signing paperwork.

YOUR GOAL: Keep the conversation going and gather the following information \
naturally through conversation (do NOT ask for all at once):
1. Confirm they want to bond out the defendant
2. Their full name (the person texting — the indemnitor)
3. Their relationship to the defendant (spouse, parent, friend, etc.)
4. The best phone number to reach them at
5. Whether they can come to the office or prefer mobile signing
6. Any questions they have about the process

RULES:
- Never discuss specific pricing, percentages, or premium amounts via text
- Never make promises about release times
- If asked about cost: "Every case is different — a bondsman will walk you through all the options. Would a quick call work?"
- Keep responses SHORT (2-3 sentences max — this is texting, not email)
- Always end with a clear next step or question to keep the conversation going
- Never give legal advice
- Be conversational and natural, not robotic
- Use an occasional emoji but don't overdo it 🍀
- If they seem ready, offer to have a bondsman call them or send paperwork
- If they ask something you don't know, say "Let me have our bondsman reach out — what's the best number?"

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
- info_provided: person is providing requested information (name, phone, relationship, etc.)
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
        "Are you looking to get them bonded out? 🍀"
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
    "info_provided": (
        "Thanks for that info! A bondsman will be reaching out to you shortly "
        "to walk you through the next steps. 🍀"
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

async def _call_openai(system_prompt: str, messages: list,
                       temperature: float = 0.7,
                       max_tokens: int = 200) -> Optional[str]:
    """Call OpenAI GPT-4o-mini with full conversation history.
    
    Args:
        system_prompt: The system instruction
        messages: List of {"role": "user"|"assistant", "content": "..."} dicts
    
    Returns the response text or None on failure.
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set — falling back to templates")
        return None

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)
        
        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(messages)
        
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error("OpenAI call failed: %s", e)
        return None


async def _call_openai_simple(system_prompt: str, user_message: str,
                              temperature: float = 0.7,
                              max_tokens: int = 200) -> Optional[str]:
    """Simplified single-turn OpenAI call (for classification)."""
    return await _call_openai(
        system_prompt,
        [{"role": "user", "content": user_message}],
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Conversation History
# ═══════════════════════════════════════════════════════════════════════════════

async def _get_conversation_history(phone: str, booking_number: str,
                                     db, max_messages: int = 20) -> list:
    """Load recent conversation history from MongoDB for multi-turn context.
    
    Returns a list of {"role": "user"|"assistant", "content": "..."} dicts
    suitable for OpenAI's chat completions API.
    """
    outreach = db["imessage_outreach"]
    
    # Fetch recent messages for this phone+booking, oldest first
    cursor = outreach.find(
        {
            "recipient_phone": phone,
            "booking_number": booking_number,
            "status": {"$in": ["sent", "received"]},
        },
        {"direction": 1, "message": 1, "sent_at": 1, "_id": 0},
    ).sort("sent_at", 1).limit(max_messages)
    
    history = []
    async for doc in cursor:
        msg = doc.get("message", "").strip()
        if not msg:
            continue
        role = "user" if doc.get("direction") == "inbound" else "assistant"
        history.append({"role": role, "content": msg})
    
    return history


# ═══════════════════════════════════════════════════════════════════════════════
#  Intent Classification
# ═══════════════════════════════════════════════════════════════════════════════

VALID_INTENTS = {"interested", "question", "info_provided", "not_interested", "wrong_number", "spam"}


async def classify_intent(message_text: str) -> str:
    """Classify an inbound message into an intent category using GPT-4o-mini.
    Falls back to 'question' if classification fails.
    """
    prompt = CLASSIFY_PROMPT.format(message=message_text)
    result = await _call_openai_simple(
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
    # Check if they're providing info (name, number patterns)
    if any(w in lower for w in ["my name is", "i'm his", "i'm her", "call me at", "my number"]):
        return "info_provided"

    return "question"


# ═══════════════════════════════════════════════════════════════════════════════
#  Response Generation (Multi-Turn)
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
                             intent: str, ai_enabled: bool = True,
                             conversation_history: list = None,
                             is_first_message: bool = False) -> Optional[str]:
    """Generate a response to an inbound message with conversation history.

    Uses OpenAI with full conversation context if ai_enabled and available,
    otherwise falls back to templates.
    Returns None if we shouldn't respond (e.g. spam).
    """
    # Don't respond to spam
    if intent == "spam":
        logger.info("Spam detected — no response for: %s", message_text[:50])
        return None

    # Try AI-powered response with conversation history
    if ai_enabled:
        filled_prompt = SYSTEM_PROMPT.format(**lead_context)
        
        # Build message list with history
        messages = []
        if conversation_history:
            messages.extend(conversation_history)
        
        # Add current message
        messages.append({"role": "user", "content": message_text})
        
        ai_response = await _call_openai(
            filled_prompt,
            messages,
            temperature=0.7,
            max_tokens=200,
        )
        if ai_response:
            return ai_response

    # Fallback to templates
    if is_first_message:
        template = TEMPLATES.get("first_response", TEMPLATES["default"])
    else:
        template = TEMPLATES.get(intent, TEMPLATES["default"])
    
    if template is None:
        return None  # e.g. spam template is None

    try:
        return template.format(**lead_context)
    except KeyError:
        return template


# ═══════════════════════════════════════════════════════════════════════════════
#  Cooldown & Reply Guard
# ═══════════════════════════════════════════════════════════════════════════════

async def should_auto_reply(phone: str, booking_number: str,
                            db, config: dict) -> tuple[bool, str]:
    """Check whether we should auto-reply to this inbound message.

    In conversational mode (default), the agent keeps replying with a
    per-message cooldown. In first_reply_only mode, it only responds once.

    Returns:
        (should_reply: bool, reason: str)
    """
    if not config.get("enabled", False):
        return False, "auto_reply_disabled"

    outreach = db["imessage_outreach"]

    # Conversational mode: only enforce per-message cooldown
    # (prevents rapid-fire responses to multiple messages within seconds)
    cooldown_minutes = config.get("cooldown_minutes", 5)
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)).isoformat()
    
    recent_reply = await outreach.find_one({
        "recipient_phone": phone,
        "booking_number": booking_number,
        "sent_by": "auto_reply",
        "sent_at": {"$gte": cutoff},
    })
    if recent_reply:
        return False, "cooldown_active"

    # Check for terminal intents — don't keep messaging after wrong_number or not_interested
    terminal_reply = await outreach.find_one({
        "recipient_phone": phone,
        "booking_number": booking_number,
        "intent": {"$in": ["wrong_number", "not_interested"]},
        "direction": "inbound",
    })
    if terminal_reply:
        return False, "conversation_ended"

    # Check if lead was manually closed
    bonds = db["prospective_bonds"]
    bond = await bonds.find_one({
        "booking_number": booking_number,
        "status": {"$in": ["closed", "officialize"]},
    })
    if bond:
        return False, "lead_closed_or_converted"

    # Business hours check
    if config.get("business_hours_only", False):
        from dashboard.extensions import is_business_hours
        if not is_business_hours(config):
            return True, "after_hours"  # Still reply, but use after_hours template

    return True, "ok"


# ═══════════════════════════════════════════════════════════════════════════════
#  Information Extraction (from conversation)
# ═══════════════════════════════════════════════════════════════════════════════

async def _extract_lead_info(conversation_history: list, db,
                              booking_number: str) -> None:
    """Analyze conversation history and extract any lead info provided.
    
    Updates the prospective_bonds record with extracted data:
    - indemnitor_name, relationship, callback_phone, preference (office/mobile)
    """
    if not conversation_history or len(conversation_history) < 2:
        return  # Need at least one exchange
    
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return

    try:
        extract_prompt = """Analyze this conversation between a bail bond agent and a prospective client.
Extract any information the client has provided. Return ONLY a JSON object with these fields 
(use null for any field not mentioned):

{
    "indemnitor_name": "their full name if provided",
    "relationship": "their relationship to the defendant if mentioned",
    "callback_phone": "any phone number they provided for callbacks",
    "prefers_office": true/false if they mentioned preference,
    "ready_to_sign": true/false if they seem ready to proceed
}

Respond with ONLY the JSON object, nothing else."""

        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)
        
        messages = [{"role": "system", "content": extract_prompt}]
        messages.extend(conversation_history[-10:])  # Last 10 messages
        
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.1,
            max_tokens=200,
        )
        
        import json
        raw = response.choices[0].message.content.strip()
        # Clean any markdown fencing
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        
        extracted = json.loads(raw)
        
        # Update prospective_bonds with any non-null extracted data
        updates = {}
        if extracted.get("indemnitor_name"):
            updates["indemnitor.name"] = extracted["indemnitor_name"]
        if extracted.get("relationship"):
            updates["indemnitor.relationship"] = extracted["relationship"]
        if extracted.get("callback_phone"):
            updates["indemnitor.callback_phone"] = extracted["callback_phone"]
        if extracted.get("ready_to_sign") is True:
            updates["stage"] = "ready_to_sign"
        
        if updates:
            updates["indemnitor.updated_at"] = datetime.now(timezone.utc).isoformat()
            bonds = db["prospective_bonds"]
            result = await bonds.update_one(
                {"booking_number": booking_number},
                {"$set": updates}
            )
            if result.modified_count:
                logger.info(
                    "📋 Extracted lead info for %s: %s",
                    booking_number, list(updates.keys())
                )
    except Exception as e:
        logger.debug("Info extraction failed (non-critical): %s", e)


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
    3. Load conversation history
    4. Check cooldown/dedup
    5. Generate response (AI with history, or template)
    6. Smart behaviors (typing, react, mark-read)
    7. Send response
    8. Log everything to MongoDB
    9. Extract lead info from conversation

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
        {
            "$push": {"communication_log": {
                "channel": "imessage",
                "direction": "inbound",
                "message": message_text,
                "intent": intent,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": "lead",
            }},
            "$set": {"last_reply_at": datetime.now(timezone.utc).isoformat()},
        }
    )

    # 4. Load conversation history for multi-turn context
    conversation_history = await _get_conversation_history(
        phone, booking_number, db, max_messages=20
    )
    is_first_message = len(conversation_history) <= 1  # Only the current message

    # 5. Check if we should auto-reply
    should_reply, reason = await should_auto_reply(phone, booking_number, db, config)

    if not should_reply:
        logger.info("⏭️  No auto-reply: %s (phone=%s, bk=%s)", reason, phone[-4:], booking_number)
        return {
            "responded": False,
            "intent": intent,
            "response": None,
            "reason": reason,
        }

    # 6. Determine response type
    ai_enabled = config.get("ai_enabled", True)

    # Use after_hours template if outside business hours
    if reason == "after_hours":
        response_text = TEMPLATES["after_hours"].format(**context)
    else:
        response_text = await generate_response(
            context, message_text, intent, ai_enabled,
            conversation_history=conversation_history,
            is_first_message=is_first_message,
        )

    if not response_text:
        logger.info("⏭️  No response generated for intent=%s", intent)
        return {
            "responded": False,
            "intent": intent,
            "response": None,
            "reason": "no_response_for_intent",
        }

    # 7. Smart behaviors + send via BB client
    sent_guid = ""
    result = {"success": False, "error": "no_bb_client"}
    
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
        if result.get("success"):
            data = result.get("data", {})
            if isinstance(data, dict):
                sent_guid = data.get("guid", "")

    # 8. Log the auto-reply to MongoDB
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
        "ai_generated": ai_enabled,
        "conversation_turn": len(conversation_history),
    }
    await outreach_coll.insert_one(reply_doc)

    # 9. Append auto-reply to communication_log
    await bonds_coll.update_one(
        {"booking_number": booking_number},
        {"$push": {"communication_log": {
            "channel": "imessage",
            "direction": "outbound",
            "message": response_text,
            "intent": intent,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "auto_reply",
            "conversation_turn": len(conversation_history),
        }}}
    )

    # 10. Handle terminal intents
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

    elif intent == "not_interested":
        await bonds_coll.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "stage": "not_interested",
                "outcome": "declined",
            }}
        )
        logger.info("⛔ Not interested — marked lead %s", booking_number)

    # 11. Background: extract lead info from conversation (non-blocking)
    if len(conversation_history) >= 3 and intent in ("interested", "info_provided", "question"):
        try:
            await _extract_lead_info(conversation_history, db, booking_number)
        except Exception as e:
            logger.debug("Info extraction skipped: %s", e)

    logger.info(
        "✅ Auto-replied to %s (intent=%s, turn=%d, bk=%s)",
        phone[-4:], intent, len(conversation_history), booking_number
    )

    return {
        "responded": True,
        "intent": intent,
        "response": response_text,
        "reason": reason,
        "bb_message_guid": sent_guid,
        "conversation_turn": len(conversation_history),
    }
