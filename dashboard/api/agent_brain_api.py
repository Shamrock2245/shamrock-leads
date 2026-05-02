"""
ShamrockLeads — Agent Brain API Blueprint
Provides the /api/agent-brain/* REST endpoints consumed by sl-pipeline-ui.js.

These routes wrap OpenAI GPT-4o-mini to generate contextual outreach messages,
lead summaries, objection handling, and batch rescoring for the Prospective
Bond Pipeline's AI feature bar.

Endpoints:
  POST   /api/agent-brain/opener          — Generate a personalized first-contact message
  POST   /api/agent-brain/suggest         — Generate follow-up / objection / general suggestions
  POST   /api/agent-brain/summary         — Generate a lead intelligence summary
  POST   /api/agent-brain/rescore-all     — Rescore all active prospective bond leads
  POST   /api/agent-brain/draft-sequence  — Draft a multi-lead outreach sequence
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from quart import Blueprint, request, jsonify
from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)

agent_brain_api_bp = Blueprint("agent_brain_api", __name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

async def _call_openai(system_prompt: str, user_message: str,
                       temperature: float = 0.7,
                       max_tokens: int = 400) -> Optional[str]:
    """Single-turn OpenAI GPT-4o-mini call. Returns text or None on failure."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set — AI features unavailable")
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


async def _get_lead_context(booking_number: str) -> Optional[dict]:
    """Fetch prospective bond + arrest data for a booking number."""
    pb_col = get_collection("prospective_bonds")
    arrests_col = get_collection("arrests")

    doc = await pb_col.find_one({"booking_number": booking_number}, {"_id": 0})
    if not doc:
        # Fallback: check arrests directly
        arrest = await arrests_col.find_one(
            {"booking_number": booking_number}, {"_id": 0}
        )
        if not arrest:
            return None
        doc = {
            "booking_number": booking_number,
            "defendant_name": arrest.get("full_name", "Unknown"),
            "county": arrest.get("county", ""),
            "bond_amount": arrest.get("bond_amount", 0),
            "charges": arrest.get("charges", ""),
            "lead_score": arrest.get("lead_score", 0),
            "stage": "new",
            "communication_log": [],
        }
    return doc


def _build_lead_summary_text(doc: dict) -> str:
    """Build a plain-text summary of a lead for OpenAI context."""
    parts = [
        f"Defendant: {doc.get('defendant_name', 'Unknown')}",
        f"County: {doc.get('county', 'N/A')}",
        f"Bond Amount: ${doc.get('bond_amount', 0):,.2f}",
        f"Charges: {doc.get('charges', 'N/A')}",
        f"Lead Score: {doc.get('lead_score', 0)}/100",
        f"Pipeline Stage: {doc.get('stage', 'N/A')}",
    ]
    indemnitor = doc.get("indemnitor", {})
    if indemnitor and indemnitor.get("name"):
        parts.append(f"Indemnitor: {indemnitor['name']} ({indemnitor.get('relationship', 'unknown')})")
    comms = doc.get("communication_log", [])
    if comms:
        parts.append(f"Messages exchanged: {len(comms)}")
        # Last 3 messages for context
        for msg in comms[-3:]:
            direction = msg.get("direction", "note")
            text = msg.get("text", msg.get("message", ""))[:100]
            parts.append(f"  [{direction}] {text}")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/agent-brain/opener — Generate AI opening message
# ═══════════════════════════════════════════════════════════════════════════════

OPENER_SYSTEM = """You are the outreach copywriter for Shamrock Bail Bonds, a premium bail bond agency in Southwest Florida.

Write a SHORT, warm, personalized first-contact iMessage to the likely family/friend of an arrestee.
The message should:
- Be 2-3 sentences max (this is a text message, not an email)
- Sound human and empathetic, not corporate
- Reference the defendant by name to show you know the situation
- Create urgency without being pushy
- End with a question or clear call to action
- Include one 🍀 emoji (brand identifier)

RULES:
- Never discuss specific pricing or percentages
- Never make promises about release times
- Never give legal advice
- Be conversational and natural
- Use proper grammar but casual tone"""

@agent_brain_api_bp.route("/agent-brain/opener", methods=["POST"])
async def api_agent_brain_opener():
    """Generate a personalized AI opening message for a lead."""
    try:
        data = await request.get_json(silent=True) or {}
        bk = (data.get("booking_number") or "").strip()
        if not bk:
            return jsonify({"error": "booking_number is required"}), 400

        doc = await _get_lead_context(bk)
        if not doc:
            return jsonify({"error": "Lead not found"}), 404

        context_text = _build_lead_summary_text(doc)
        user_prompt = (
            f"Write a first-contact iMessage for this lead:\n\n{context_text}\n\n"
            "Generate only the message text, nothing else."
        )

        message = await _call_openai(OPENER_SYSTEM, user_prompt, temperature=0.8)
        if not message:
            # Fallback template
            name = doc.get("defendant_name", "your loved one")
            message = (
                f"Hi! This is Shamrock Bail Bonds. We saw {name} was recently booked "
                f"in {doc.get('county', '')} County and wanted to reach out. "
                f"Are you looking to get them bonded out? We're here to help 🍀"
            )

        return jsonify({
            "success": True,
            "message": message,
            "booking_number": bk,
            "ai_generated": bool(os.getenv("OPENAI_API_KEY")),
        })

    except Exception as exc:
        logger.exception("agent_brain_opener error")
        return jsonify({"error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/agent-brain/suggest — Generate follow-up / objection suggestions
# ═══════════════════════════════════════════════════════════════════════════════

SUGGEST_SYSTEM = """You are an expert bail bond sales coach for Shamrock Bail Bonds.

Generate 2-3 short iMessage suggestions based on the conversation context and request type.
Each suggestion should be a complete, ready-to-send message (2-3 sentences max).

Types:
- "followup": Warm follow-up after initial contact or silence
- "objection": Responses to common objections (cost, distrust, already have help)
- "opener": First-contact messages (if no conversation history)

RULES:
- Keep each message SHORT (texting, not email)
- Sound human, not robotic
- Reference the defendant by name when natural
- Never discuss specific pricing
- End each with a question or call to action
- Be empathetic — their family member is in jail

Return each message on a separate line, numbered (1., 2., 3.)."""

@agent_brain_api_bp.route("/agent-brain/suggest", methods=["POST"])
async def api_agent_brain_suggest():
    """Generate AI message suggestions based on type and context."""
    try:
        data = await request.get_json(silent=True) or {}
        bk = (data.get("booking_number") or "").strip()
        suggest_type = data.get("type", "followup")
        if not bk:
            return jsonify({"error": "booking_number is required"}), 400

        doc = await _get_lead_context(bk)
        if not doc:
            return jsonify({"error": "Lead not found"}), 404

        context_text = _build_lead_summary_text(doc)
        user_prompt = (
            f"Generate {suggest_type} iMessage suggestions for this lead:\n\n"
            f"{context_text}\n\n"
            f"Type: {suggest_type}\n"
            "Return 2-3 numbered suggestions."
        )

        raw = await _call_openai(SUGGEST_SYSTEM, user_prompt, temperature=0.8)
        suggestions = []
        if raw:
            # Parse numbered lines
            for line in raw.split("\n"):
                line = line.strip()
                if not line:
                    continue
                # Remove numbering (1., 2., etc.)
                import re
                cleaned = re.sub(r"^\d+\.\s*", "", line).strip()
                if cleaned and len(cleaned) > 10:
                    suggestions.append(cleaned)

        if not suggestions:
            # Fallback templates
            name = doc.get("defendant_name", "your loved one")
            if suggest_type == "followup":
                suggestions = [
                    f"Hey, just checking in — did you have any questions about getting {name} out? We're available anytime 🍀",
                    f"Hi! Wanted to follow up — we can usually get the process started today if you're ready. Just let us know! 🍀",
                ]
            elif suggest_type == "objection":
                suggestions = [
                    "I understand the concern about cost. Our 10% rate is the state-regulated minimum — we can set up a payment plan that works for your family.",
                    "Many families feel overwhelmed at first. Let me walk you through exactly what happens step by step so there are no surprises.",
                    "The court date is set regardless, but we can get your loved one home while they wait. The sooner we start, the sooner they're released.",
                ]
            else:
                suggestions = [
                    f"Hi! This is Shamrock Bail Bonds reaching out about {name}. Are you looking to get them home? 🍀",
                ]

        return jsonify({
            "success": True,
            "suggestions": suggestions,
            "messages": suggestions,  # Alias for frontend compatibility
            "booking_number": bk,
            "type": suggest_type,
        })

    except Exception as exc:
        logger.exception("agent_brain_suggest error")
        return jsonify({"error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/agent-brain/summary — Generate lead intelligence summary
# ═══════════════════════════════════════════════════════════════════════════════

SUMMARY_SYSTEM = """You are an intelligence analyst for Shamrock Bail Bonds.

Generate a concise lead intelligence briefing that includes:
1. Quick situation assessment (1-2 sentences)
2. Bond viability analysis (can we write this bond?)
3. Risk factors (flight risk signals, charge severity)
4. Recommended next action
5. Key talking points for the bondsman

Keep it professional and actionable. Use bullet points.
Total length: 5-8 bullet points."""

@agent_brain_api_bp.route("/agent-brain/summary", methods=["POST"])
async def api_agent_brain_summary():
    """Generate an AI lead intelligence summary."""
    try:
        data = await request.get_json(silent=True) or {}
        bk = (data.get("booking_number") or "").strip()
        if not bk:
            return jsonify({"error": "booking_number is required"}), 400

        doc = await _get_lead_context(bk)
        if not doc:
            return jsonify({"error": "Lead not found"}), 404

        context_text = _build_lead_summary_text(doc)
        user_prompt = (
            f"Generate a lead intelligence briefing for this prospect:\n\n{context_text}"
        )

        summary = await _call_openai(SUMMARY_SYSTEM, user_prompt, temperature=0.5, max_tokens=500)
        if not summary:
            # Construct manual summary
            bond_amt = doc.get("bond_amount", 0) or 0
            premium = bond_amt * 0.10
            score = doc.get("lead_score", 0) or 0
            summary = (
                f"• Defendant: {doc.get('defendant_name', 'Unknown')} — {doc.get('county', 'N/A')} County\n"
                f"• Bond: ${bond_amt:,.0f} (est. premium: ${premium:,.0f})\n"
                f"• Charges: {doc.get('charges', 'N/A')}\n"
                f"• Lead Score: {score}/100 — {'Hot 🔥' if score >= 70 else 'Warm 🟡' if score >= 40 else 'Cold ❄️'}\n"
                f"• Stage: {doc.get('stage', 'N/A')}\n"
                f"• Messages: {len(doc.get('communication_log', []))}\n"
                f"• Action: {'Ready for outreach' if score >= 40 else 'Monitor — low priority'}"
            )

        return jsonify({
            "success": True,
            "summary": summary,
            "booking_number": bk,
        })

    except Exception as exc:
        logger.exception("agent_brain_summary error")
        return jsonify({"error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/agent-brain/rescore-all — Batch rescore all active leads
# ═══════════════════════════════════════════════════════════════════════════════

@agent_brain_api_bp.route("/agent-brain/rescore-all", methods=["POST"])
async def api_agent_brain_rescore_all():
    """Rescore all active prospective bond leads using the scoring engine."""
    try:
        pb_col = get_collection("prospective_bonds")
        arrests_col = get_collection("arrests")
        updated = 0
        errors = 0

        async for bond in pb_col.find({"status": "active"}, {"_id": 0, "booking_number": 1}):
            bk = bond.get("booking_number")
            if not bk:
                continue
            try:
                arrest = await arrests_col.find_one(
                    {"booking_number": bk},
                    {"_id": 0, "lead_score": 1, "lead_status": 1}
                )
                if arrest and arrest.get("lead_score") is not None:
                    await pb_col.update_one(
                        {"booking_number": bk},
                        {"$set": {
                            "lead_score": arrest["lead_score"],
                            "lead_status": arrest.get("lead_status", ""),
                            "updated_at": datetime.now(timezone.utc),
                        }},
                    )
                    updated += 1
            except Exception as e:
                logger.debug("Rescore error for %s: %s", bk, e)
                errors += 1

        return jsonify({
            "success": True,
            "updated": updated,
            "errors": errors,
        })

    except Exception as exc:
        logger.exception("agent_brain_rescore_all error")
        return jsonify({"error": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/agent-brain/draft-sequence — Draft multi-lead outreach sequence
# ═══════════════════════════════════════════════════════════════════════════════

SEQUENCE_SYSTEM = """You are an outreach strategist for Shamrock Bail Bonds.

Draft a coordinated multi-day outreach sequence for a batch of bail bond leads.
For each lead, suggest:
- Day 1: Initial contact message
- Day 2: Follow-up if no response
- Day 3: Final attempt with urgency

Keep messages SHORT (iMessage style, 2-3 sentences each).
Format as a structured plan with clear labels."""

@agent_brain_api_bp.route("/agent-brain/draft-sequence", methods=["POST"])
async def api_agent_brain_draft_sequence():
    """Draft a multi-lead outreach sequence."""
    try:
        data = await request.get_json(silent=True) or {}
        booking_numbers = data.get("booking_numbers", [])
        if not booking_numbers:
            return jsonify({"error": "booking_numbers list is required"}), 400

        # Gather context for all leads
        lead_summaries = []
        for bk in booking_numbers[:10]:  # Cap at 10 to avoid token overflow
            doc = await _get_lead_context(bk)
            if doc:
                lead_summaries.append(
                    f"- {doc.get('defendant_name', 'Unknown')} "
                    f"({doc.get('county', '?')} Co, ${doc.get('bond_amount', 0):,.0f}, "
                    f"score: {doc.get('lead_score', 0)})"
                )

        if not lead_summaries:
            return jsonify({"error": "No leads found for the given booking numbers"}), 404

        user_prompt = (
            f"Draft a 3-day outreach sequence for these {len(lead_summaries)} leads:\n\n"
            + "\n".join(lead_summaries)
        )

        sequence = await _call_openai(
            SEQUENCE_SYSTEM, user_prompt,
            temperature=0.7, max_tokens=800
        )
        if not sequence:
            sequence = (
                f"📋 Outreach Plan for {len(lead_summaries)} Leads\n\n"
                "Day 1: Initial contact — warm intro referencing defendant name, "
                "confirm interest in bonding out.\n"
                "Day 2: Follow-up — if no response, short check-in with soft urgency.\n"
                "Day 3: Final attempt — mention availability and offer to call.\n\n"
                "Note: AI drafting unavailable — using template sequence."
            )

        return jsonify({
            "success": True,
            "sequence": sequence,
            "lead_count": len(lead_summaries),
            "booking_numbers": booking_numbers[:10],
        })

    except Exception as exc:
        logger.exception("agent_brain_draft_sequence error")
        return jsonify({"error": str(exc)}), 500
