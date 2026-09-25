from __future__ import annotations

"""
ShamrockLeads — BlueBubbles Webhook Receiver
=============================================
Real-time event handler for BlueBubbles Server webhooks.

Replaces the 30-second inbox polling loop with an instant push-based
architecture. The BlueBubbles server on the office iMac POSTs events to
this endpoint the moment they occur.

Architecture
------------
  BlueBubbles Server (iMac)
      │  POST /api/webhooks/bluebubbles
      ▼
  This handler (Quart async)
      ├─ new-message (inbound)   → agent_brain.process_inbound()
      ├─ updated-message         → update delivery/read status in MongoDB
      ├─ typing-indicator        → log / ignore
      └─ chat-read-status-changed → update read receipts in MongoDB

Webhook Registration
--------------------
On startup ``dashboard/cron.py`` calls ``BlueBubblesClient.ensure_webhook()``
directly (server-side, no HTTP hop, no credential needed).  Manual
re-registration: ``POST /api/webhooks/bluebubbles/register`` with a staff
session or ``X-API-Key``/``X-Internal-Token`` (GAS_API_KEY / LEADS_INTERNAL_TOKEN).

Endpoints
---------
  POST   /api/webhooks/bluebubbles          — Receive BB event (called by BB server; open — BB sends no HMAC by default)
  POST   /api/webhooks/bluebubbles/register — Register webhook with BB server (staff / machine auth)
  GET    /api/webhooks/bluebubbles/status   — List registered webhooks (staff PIN)
  DELETE /api/webhooks/bluebubbles/<id>     — Remove a webhook registration (staff / machine auth)
"""
import asyncio
import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.routers.agent_brain import process_inbound
from dashboard.routers.bb_private_api import BlueBubblesClient
from dashboard.routers.imessage_automation import _content_hash
from dashboard.extensions import BB_SERVERS, get_bb_server, get_collection, format_phone
from dashboard.routers.automation_control import _require_control_auth
from dashboard.services.bb_party_matcher import find_party_matches, public_matches
from dashboard.services.sms_consent_ledger import (
    EVENT_OPT_IN,
    EVENT_OPT_OUT,
    classify_consent_keyword,
    record_consent_event,
)

logger = logging.getLogger(__name__)

bb_webhook_bp = APIRouter(prefix="/api", tags=["bb_webhook_receiver"])
# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

# Events we want to subscribe to from BlueBubbles
BB_WEBHOOK_EVENTS = [
    "new-message",
    "updated-message",
    "chat-read-status-changed",
    "typing-indicator",
]

# Our VPS public URL — used when registering the webhook with BB server
# Set BB_WEBHOOK_PUBLIC_URL in .env, e.g. "https://178.156.179.237:8088"
_VPS_PUBLIC_URL = os.getenv("BB_WEBHOOK_PUBLIC_URL", "")
_WEBHOOK_PATH = "/api/webhooks/bluebubbles"

# Optional HMAC secret for verifying BB webhook payloads
_BB_WEBHOOK_SECRET = os.getenv("BB_WEBHOOK_SECRET", "")


# ─────────────────────────────────────────────────────────────────────────────
#  Signature Verification
# ─────────────────────────────────────────────────────────────────────────────

def _verify_signature(payload: bytes, signature: str) -> bool:
    """Verify optional HMAC-SHA256 webhook signature.

    BlueBubbles Server does **not** send HMAC signatures by default (only URL +
    event list). If BB_WEBHOOK_SECRET is set but the request has no signature
    header, accept the event (still protected by public HTTPS + secret URL
    knowledge). Only reject when a signature *is* present and does not match.
    """
    if not _BB_WEBHOOK_SECRET:
        return True  # No secret configured — skip verification
    if not signature:
        # BB default webhooks omit signatures — do not drop all inbound events
        return True
    expected = hmac.new(
        _BB_WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    # Accept raw hex or sha256=<hex> forms
    sig = (signature or "").strip()
    if sig.lower().startswith("sha256="):
        sig = sig.split("=", 1)[1].strip()
    return hmac.compare_digest(expected, sig)


# ─────────────────────────────────────────────────────────────────────────────
#  Event Handlers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_bb_message(event_data: dict | None) -> dict:
    """Normalize BlueBubbles webhook / poll message shapes.

    Official BB Server posts::

        { "type": "new-message", "data": { "guid": "...", "text": "...", "isFromMe": false, "handle": {...} } }

    i.e. ``data`` *is* the message. Some proxies or older builds nest further
    as ``data.message`` or ``data.data``. Accept all of these.
    """
    if not isinstance(event_data, dict):
        return {}
    # Prefer nested wrappers when present without top-level message identity
    nested = event_data.get("message") or event_data.get("data")
    if (
        isinstance(nested, dict)
        and not event_data.get("guid")
        and "isFromMe" not in event_data
        and not event_data.get("text")
    ):
        return nested
    # Standard BB shape: data *is* the message
    if any(k in event_data for k in ("guid", "text", "isFromMe", "handle", "chats", "dateCreated")):
        return event_data
    if isinstance(nested, dict):
        return nested
    return event_data


def _extract_sender_and_chat(message: dict) -> tuple[str, str, str, dict]:
    """Return (raw_address, chat_guid, e164_phone_or_empty, chat_dict).

    For inbound messages ``handle`` is the sender.  For ``isFromMe`` messages the
    counter-party of a 1:1 chat is taken from the chat GUID (``any;-;+1...``)
    first, since that is always the other participant.
    """
    chats = message.get("chats") or []
    chat = chats[0] if isinstance(chats, list) and chats else {}
    if not isinstance(chat, dict):
        chat = {}
    chat_guid = chat.get("guid", "") or message.get("chatGuid", "") or message.get("chat_guid", "")
    handle = message.get("handle") or {}
    if isinstance(handle, str):
        sender_address = handle
    else:
        sender_address = (handle.get("address", "") if isinstance(handle, dict) else "") or ""
    if not sender_address:
        # Fallbacks used by some BB builds / SMS
        sender_address = (
            message.get("address")
            or message.get("handleId")
            or (chat.get("chatIdentifier") if chat else "")
            or ""
        )
        # chatIdentifier may be "any;-;+1..." — strip prefix
        if ";-;" in str(sender_address):
            sender_address = str(sender_address).split(";-;")[-1]
    return str(sender_address or ""), chat_guid, format_phone(sender_address) or "", chat


def _bb_sent_at(msg_date_ms) -> str:
    """Prefer BlueBubbles dateCreated (ms) for correct thread ordering."""
    sent_at = datetime.now(timezone.utc).isoformat()
    if isinstance(msg_date_ms, (int, float)) and msg_date_ms > 0:
        try:
            # BB dateCreated is often Apple Cocoa ns since 2001 or unix ms — try ms first
            ts = float(msg_date_ms)
            if ts > 1e14:  # nanoseconds-ish
                ts = ts / 1e6
            if ts > 1e12:  # already ms
                sent_at = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).isoformat()
            elif ts > 1e9:  # seconds
                sent_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            pass
    return sent_at


def _match_fields(match_result: dict, *, inbound: bool) -> dict:
    """imessage_outreach fields that attach a message to every matched party.

    ``booking_number`` / ``contact_name`` are only set when the match is
    unambiguous — we never auto-pick one party.  ``booking_numbers`` carries
    every matched booking so each case's lifecycle timeline shows the message.
    """
    matches = match_result.get("matches") or []
    if not matches:
        return {}
    ambiguous = bool(match_result.get("ambiguous"))
    pub = public_matches(matches)
    bookings = sorted({m["booking_number"] for m in pub if m.get("booking_number")})
    fields: dict = {
        "matched": True,
        "matches": pub,
        "match_count": len(match_result.get("parties") or []),
        "booking_numbers": bookings,
        "ambiguous_match": ambiguous,
    }
    if ambiguous:
        if inbound:
            fields["needs_staff_review"] = True
            fields["staff_review_reason"] = "ambiguous_phone_match"
    else:
        parties = match_result.get("parties") or []
        booking = (parties[0].get("booking_number") if parties else "") or next(
            (m["booking_number"] for m in pub if m.get("booking_number")), "")
        name = next((m["name"] for m in pub if m.get("name")), "")
        if booking:
            fields["booking_number"] = booking
            if booking not in bookings:
                fields["booking_numbers"] = sorted(bookings + [booking])
        if name:
            fields["contact_name"] = name
    return fields


def _unique_prospective_doc(match_result: dict) -> dict | None:
    """The prospective-bond doc when the phone maps to exactly one logical party.

    Preserves today's agent-brain behaviour exactly: the agent brain only runs
    for an active prospective bond matched on ``indemnitor.phone`` (the only
    field the webhook ever matched).  Newly matched parties (co-indemnitors,
    defendants, active bonds, intake) are attached without auto-reply.
    Ambiguous => None (staff review, no auto-reply, never auto-pick).
    """
    if match_result.get("ambiguous"):
        return None
    for m in match_result.get("matches") or []:
        if (
            m.get("collection") == "prospective_bonds"
            and "indemnitor.phone" in (m.get("fields") or [])
            and isinstance(m.get("_doc"), dict)
        ):
            return m["_doc"]
    return None


async def apply_opt_out(
    *,
    sender_phone: str,
    msg_text: str,
    chat_guid: str,
    msg_guid: str,
    msg_date_ms=None,
    keyword_info: dict,
    source: str = "webhook",
) -> dict:
    """Existing STOP path (sequences + prospective flag + outreach log) + consent ledger.

    Shared by the webhook and the inbox poller so a STOP is honoured whichever
    path sees it first.
    """
    opted_out_at = datetime.now(timezone.utc).isoformat()
    outreach_coll_stop = get_collection("imessage_outreach")
    bonds_coll_stop = get_collection("prospective_bonds")
    seqs_coll_stop = get_collection("outreach_sequences")

    # 0. Consent ledger (idempotent per message GUID) — this is what the send gate reads.
    ledger_ts = None
    _sent_at_iso = _bb_sent_at(msg_date_ms)
    try:
        ledger_ts = datetime.fromisoformat(_sent_at_iso)
    except ValueError:
        ledger_ts = None
    await record_consent_event(
        phone=sender_phone,
        event=EVENT_OPT_OUT,
        keyword=keyword_info.get("keyword", ""),
        match_type=keyword_info.get("match_type", "exact"),
        source_message_guid=msg_guid,
        chat_guid=chat_guid,
        source=f"bb_{source}",
        timestamp=ledger_ts,
    )
    # 1. Flag all active sequences for this phone as stopped
    await seqs_coll_stop.update_many(
        {"phone": {"$in": [sender_phone, sender_phone.replace("+1", "")]}, "status": "active"},
        {"$set": {"status": "stopped", "stopped_at": opted_out_at, "stop_reason": "STOP_keyword"}},
    )
    # 2. Flag the prospective bond as opted-out
    await bonds_coll_stop.update_many(
        {"$or": [
            {"indemnitor.phone": sender_phone},
            {"indemnitor.phone": sender_phone.replace("+1", "")},
        ]},
        {"$set": {"opted_out": True, "opted_out_at": opted_out_at}},
    )
    # 3. Log the opt-out event (once per BB message GUID)
    already = await outreach_coll_stop.find_one({"bb_message_guid": msg_guid}) if msg_guid else None
    if not already:
        await outreach_coll_stop.insert_one({
            "recipient_phone": sender_phone,
            "message": msg_text,
            "chat_guid": chat_guid,
            "bb_message_guid": msg_guid,
            "content_hash": _content_hash(sender_phone, msg_text, msg_date_ms),
            "direction": "inbound",
            "status": "opted_out",
            "category": "opt_out",
            "consent_keyword": keyword_info.get("keyword", ""),
            "consent_match_type": keyword_info.get("match_type", "exact"),
            "needs_staff_review": keyword_info.get("match_type") != "exact",
            "sent_at": opted_out_at,
            "source": source,
        })
    logger.warning(
        "🛑 STOP received from ...%s — opted out and stopped all sequences (keyword=%s match=%s)",
        sender_phone[-4:], keyword_info.get("keyword"), keyword_info.get("match_type"),
    )
    return {"processed": True, "opted_out": True, "keyword": keyword_info.get("keyword")}


async def apply_opt_in(*, sender_phone: str, chat_guid: str, msg_guid: str, msg_date_ms=None,
                       keyword_info: dict, source: str = "webhook") -> None:
    """START / UNSTOP → ledger opt-in (re-enables sends).  Sequences stay stopped."""
    ledger_ts = None
    try:
        ledger_ts = datetime.fromisoformat(_bb_sent_at(msg_date_ms))
    except ValueError:
        ledger_ts = None
    await record_consent_event(
        phone=sender_phone,
        event=EVENT_OPT_IN,
        keyword=keyword_info.get("keyword", ""),
        match_type=keyword_info.get("match_type", "exact"),
        source_message_guid=msg_guid,
        chat_guid=chat_guid,
        source=f"bb_{source}",
        timestamp=ledger_ts,
    )


# Outbound (isFromMe) ingest is deferred so a CRM send that is still waiting
# on the BlueBubbles HTTP response can log its own row (with bb_message_guid /
# temp_guid) first; the webhook copy then dedups against it instead of racing.
def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, "") or default))
    except ValueError:
        return default


_OUTBOUND_INGEST_DELAY_SECONDS = _env_float("BB_OUTBOUND_INGEST_DELAY_SECONDS", 20.0)
_OUTBOUND_TEXT_DEDUP_WINDOW_SECONDS = 15 * 60
_pending_outbound_tasks: set = set()


async def _ingest_outbound_message(message: dict, *, delay: float = 0.0) -> dict:
    """Store an ``isFromMe`` message (sent from the office iMac / any device on
    the line, or a CRM send echoed back) as ``direction="outbound"``.

    Never runs the agent brain, never treats the text as a STOP keyword.
    Dedup order: BB GUID → tempGuid → content hash → a CRM-logged outbound row
    to the same number with identical text inside a 15-minute window that has
    no BB GUID yet (that row is back-filled with the GUID instead).
    """
    if delay and delay > 0:
        await asyncio.sleep(delay)

    msg_guid = str(message.get("guid") or message.get("originalROWID") or "")
    temp_guid = str(message.get("tempGuid") or "")
    msg_text = message.get("text", "") or message.get("subject", "") or ""
    raw_address, chat_guid, _, _chat = _extract_sender_and_chat(message)
    # 1:1 chat GUID is the counter-party; prefer it over the handle for outbound.
    recipient_phone = ""
    if ";-;" in chat_guid:
        recipient_phone = format_phone(chat_guid.split(";-;")[-1]) or ""
    if not recipient_phone:
        recipient_phone = format_phone(raw_address) or ""
    if not recipient_phone:
        return {"processed": False, "direction": "outbound", "reason": "no_recipient_phone"}
    if not msg_text.strip():
        assoc = message.get("associatedMessageType")
        if assoc:
            msg_text = f"[reaction:{assoc}]"
        else:
            return {"processed": False, "direction": "outbound", "reason": "empty_message"}

    outreach_coll = get_collection("imessage_outreach")
    if msg_guid and await outreach_coll.find_one({"bb_message_guid": msg_guid}):
        return {"processed": False, "direction": "outbound", "reason": "already_processed"}
    if temp_guid:
        existing_tmp = await outreach_coll.find_one({"temp_guid": temp_guid})
        if existing_tmp:
            if msg_guid and not existing_tmp.get("bb_message_guid"):
                await outreach_coll.update_one({"_id": existing_tmp["_id"]},
                                               {"$set": {"bb_message_guid": msg_guid}})
            return {"processed": False, "direction": "outbound", "reason": "temp_guid_duplicate"}

    msg_date_ms = message.get("dateCreated")
    chash = _content_hash(recipient_phone, msg_text, msg_date_ms)
    if await outreach_coll.find_one({"content_hash": chash}):
        return {"processed": False, "direction": "outbound", "reason": "content_hash_duplicate"}

    sent_at = _bb_sent_at(msg_date_ms)
    last10 = recipient_phone[-10:]
    try:
        window_start = (datetime.fromisoformat(sent_at).timestamp() - _OUTBOUND_TEXT_DEDUP_WINDOW_SECONDS)
        window_iso = datetime.fromtimestamp(window_start, tz=timezone.utc).isoformat()
    except ValueError:
        window_iso = ""
    crm_query = {
        "direction": "outbound",
        "recipient_phone": {"$in": [recipient_phone, last10, f"1{last10}"]},
        "message": msg_text,
        "$or": [{"bb_message_guid": {"$in": ["", None]}}, {"bb_message_guid": {"$exists": False}}],
    }
    if window_iso:
        crm_query["sent_at"] = {"$gte": window_iso}
    crm_row = await outreach_coll.find_one(crm_query)
    if crm_row:
        if msg_guid:
            await outreach_coll.update_one({"_id": crm_row["_id"]}, {"$set": {"bb_message_guid": msg_guid}})
        return {"processed": False, "direction": "outbound", "reason": "crm_logged_duplicate"}

    match_result = await find_party_matches(recipient_phone)
    doc = {
        "recipient_phone": recipient_phone,
        "message": msg_text,
        "chat_guid": chat_guid,
        "bb_message_guid": msg_guid,
        "content_hash": chash,
        "direction": "outbound",
        "status": "sent",
        "category": "general",
        "unread": False,
        "sent_at": sent_at,
        "sent_by": "office_line",
        "source": "webhook",
    }
    if temp_guid:
        doc["temp_guid"] = temp_guid
    doc.update(_match_fields(match_result, inbound=False))
    await outreach_coll.insert_one(doc)
    logger.info("📤 Webhook: ingested outbound to ...%s (matched=%s ambiguous=%s)",
                recipient_phone[-4:], bool(match_result.get("matches")), match_result.get("ambiguous"))
    return {"processed": True, "direction": "outbound", "matched": bool(match_result.get("matches"))}


def _schedule_outbound_ingest(message: dict) -> None:
    task = asyncio.ensure_future(
        _ingest_outbound_message(dict(message), delay=_OUTBOUND_INGEST_DELAY_SECONDS)
    )
    _pending_outbound_tasks.add(task)

    def _done(t):
        _pending_outbound_tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.warning("BB webhook: outbound ingest failed: %s", type(t.exception()).__name__)

    task.add_done_callback(_done)


async def _handle_new_message(event_data: dict, db) -> dict:
    """Process a new-message event from BlueBubbles.

    Inbound: STOP/START consent → dedup → match phone against prospective
    bonds, active bonds (indemnitor / co-indemnitor / defendant) and the intake
    queue → attach to every match (staff review when ambiguous).
    Outbound (isFromMe): stored as direction="outbound" (deduped), no agent brain.
    """
    message = _extract_bb_message(event_data)
    if not message:
        return {"processed": False, "reason": "no_message_in_payload"}

    # BB uses isFromMe; tolerate is_from_me aliases.  A payload with neither key
    # is still skipped (unknown direction must never reach the agent brain).
    if "isFromMe" not in message and "is_from_me" not in message:
        return {"processed": False, "reason": "outbound_message_skipped"}
    is_from_me = bool(message.get("isFromMe", message.get("is_from_me")))
    if is_from_me:
        if _OUTBOUND_INGEST_DELAY_SECONDS > 0:
            _schedule_outbound_ingest(message)
            return {"processed": True, "direction": "outbound", "deferred": True}
        return await _ingest_outbound_message(message)

    # Extract message details
    msg_guid = str(message.get("guid") or message.get("originalROWID") or "")
    msg_text = message.get("text", "") or message.get("subject", "") or ""
    sender_address, chat_guid, sender_phone, _chat = _extract_sender_and_chat(message)
    handle = message.get("handle") or {}

    if not sender_phone:
        logger.warning(
            "BB webhook: could not parse sender phone from handle=%r chat=%r",
            handle, chat_guid,
        )
        return {"processed": False, "reason": "no_sender_phone"}

    if not msg_text.strip():
        # Reactions / stickers may have empty text — still surface a marker
        # so the thread updates (better than silent drop).
        assoc = message.get("associatedMessageType")
        if assoc:
            msg_text = f"[reaction:{assoc}]"
        else:
            return {"processed": False, "reason": "empty_message"}

    msg_date_ms = message.get("dateCreated")

    # ── STOP / START consent keywords (must run before any other processing) ──
    keyword_info = classify_consent_keyword(msg_text)
    if keyword_info and keyword_info["event"] == EVENT_OPT_OUT:
        return await apply_opt_out(
            sender_phone=sender_phone, msg_text=msg_text, chat_guid=chat_guid,
            msg_guid=msg_guid, msg_date_ms=msg_date_ms, keyword_info=keyword_info,
            source="webhook",
        )
    if keyword_info and keyword_info["event"] == EVENT_OPT_IN:
        # Record re-subscribe, then continue normal processing so staff see it.
        await apply_opt_in(sender_phone=sender_phone, chat_guid=chat_guid, msg_guid=msg_guid,
                           msg_date_ms=msg_date_ms, keyword_info=keyword_info, source="webhook")
    # ─────────────────────────────────────────────────────────────────────────

    # ── Layer 1: GUID dedup — avoid processing the same message twice ──
    outreach_coll = get_collection("imessage_outreach")
    existing = await outreach_coll.find_one({"bb_message_guid": msg_guid})
    if existing:
        return {"processed": False, "reason": "already_processed"}

    # ── Layer 2: Content-hash dedup (catches BB Issue #765 — re-emitted messages) ──
    chash = _content_hash(sender_phone, msg_text, msg_date_ms)
    existing_content = await outreach_coll.find_one({"content_hash": chash})
    if existing_content:
        logger.info(
            "🔁 Webhook content-hash dedup caught duplicate from ...%s (GUID %s, hash %s)",
            sender_phone[-4:], msg_guid[:12], chash[:8]
        )
        return {"processed": False, "reason": "content_hash_duplicate"}

    # Match against prospective bonds, active bonds and intake queue
    match_result = await find_party_matches(sender_phone)
    match_fields = _match_fields(match_result, inbound=True)
    bond = _unique_prospective_doc(match_result)

    # Determine which BB server this came from (based on chat_guid prefix)
    bb_server = get_bb_server(chat_guid.split(";-;")[-1] if ";-;" in chat_guid else "")
    bb_client = None
    if bb_server:
        bb_client = BlueBubblesClient(bb_server["url"], bb_server["password"])

    sent_at = _bb_sent_at(msg_date_ms)

    if bond:
        # Run the AI agent brain (also logs inbound + optional auto-reply to Mongo)
        config_coll = get_collection("outreach_config")
        config = await config_coll.find_one({"type": "auto_reply"}, {"_id": 0}) or {}

        agent_result = await process_inbound(
            phone=sender_phone,
            message_text=msg_text,
            chat_guid=chat_guid,
            message_guid=msg_guid,
            bond_doc=bond,
            db=db,
            config=config,
            bb_client=bb_client,
            content_hash=chash,
        )

        # process_inbound already inserted the inbound row — enrich it for inbox UI
        _intent = agent_result.get("intent", "")
        _category_map = {
            "intake_inquiry": "intake",
            "interested": "intake",
            "question": "intake",
            "info_provided": "intake",
            "checkin": "checkin",
            "check_in": "checkin",
            "geo_response": "geo",
            "payment": "payment",
            "court": "court",
        }
        _category = _category_map.get(_intent, "general")
        _enrich = {
            **match_fields,
            "category": _category,
            "unread": True,
            "source": "webhook",
            "contact_name": bond.get("defendant_name") or bond.get("indemnitor", {}).get("name") or "",
            "booking_number": bond.get("booking_number", ""),
            "responded": agent_result.get("responded", False),
            "sent_at": sent_at,
        }
        await outreach_coll.update_one(
            {"bb_message_guid": msg_guid},
            {"$set": _enrich},
            upsert=False,
        )

        logger.info(
            "📨 Webhook: inbound from %s → intent=%s responded=%s",
            sender_phone[-4:], agent_result.get("intent"), agent_result.get("responded")
        )

        # Real-time dashboard events — SLiMessage.onInboundMessage refreshes
        # the open thread so replies appear in the conversation immediately.
        try:
            from dashboard.routers.events import publish_event
            _evt_payload = {
                "phone_last4": sender_phone[-4:] if sender_phone else "",
                "phone": sender_phone,
                "booking_number": bond.get("booking_number", ""),
                "defendant_name": bond.get("defendant_name", ""),
                "message": msg_text[:120],
                "preview": msg_text[:80],
                "intent": _intent,
                "category": _category,
                "responded": agent_result.get("responded", False),
                "matched": True,
                "sent_at": sent_at,
            }
            await publish_event("message_received", _evt_payload)
            await publish_event("new_reply", _evt_payload)
        except Exception:
            pass

        return {"processed": True, "matched": True, "agent_result": agent_result}

    if match_fields:
        # Matched to active bond / intake party (or ambiguous) — attach, no auto-reply.
        doc = {
            "recipient_phone": sender_phone,
            "message": msg_text,
            "chat_guid": chat_guid,
            "bb_message_guid": msg_guid,
            "content_hash": chash,
            "direction": "inbound",
            "status": "received",
            "category": "general",
            "unread": True,
            "sent_at": sent_at,
            "sent_by": "lead",
            "source": "webhook",
            **match_fields,
        }
        await outreach_coll.insert_one(doc)
        logger.info(
            "📨 Webhook: inbound from ...%s attached to %d party(ies) ambiguous=%s",
            sender_phone[-4:], match_fields.get("match_count", 0), match_fields.get("ambiguous_match"),
        )
        try:
            from dashboard.routers.events import publish_event
            await publish_event("message_received", {
                "phone_last4": sender_phone[-4:] if sender_phone else "",
                "phone": sender_phone,
                "booking_number": match_fields.get("booking_number", ""),
                "message": msg_text[:120],
                "preview": msg_text[:80],
                "category": "general",
                "matched": True,
                "needs_staff_review": bool(match_fields.get("needs_staff_review")),
                "sent_at": sent_at,
            })
        except Exception:
            pass
        return {
            "processed": True,
            "matched": True,
            "ambiguous": bool(match_result.get("ambiguous")),
            "match_count": match_fields.get("match_count", 0),
        }

    # Unmatched inbound — log for manual review (single insert)
    await outreach_coll.insert_one({
        "recipient_phone": sender_phone,
        "message": msg_text,
        "chat_guid": chat_guid,
        "bb_message_guid": msg_guid,
        "content_hash": chash,
        "direction": "inbound",
        "status": "unmatched",
        "category": "general",
        "unread": True,
        "sent_at": sent_at,
        "source": "webhook",
    })
    logger.info("❓ Webhook: unmatched inbound from %s: %s", sender_phone[-4:], msg_text[:50])

    try:
        from dashboard.routers.events import publish_event
        await publish_event("message_received", {
            "phone_last4": sender_phone[-4:] if sender_phone else "",
            "phone": sender_phone,
            "message": msg_text[:120],
            "preview": msg_text[:80],
            "category": "general",
            "matched": False,
            "sent_at": sent_at,
        })
    except Exception:
        pass

    return {"processed": True, "matched": False}


async def _handle_updated_message(event_data: dict) -> dict:
    """Update delivery and read receipt status in MongoDB."""
    message = event_data.get("message") or event_data.get("data") or {}
    msg_guid = message.get("guid", "")
    if not msg_guid:
        return {"processed": False}

    outreach_coll = get_collection("imessage_outreach")
    update = {}
    if message.get("dateDelivered") or message.get("isDelivered"):
        update["delivered"] = True
        update["date_delivered"] = message.get("dateDelivered")
    if message.get("dateRead") or message.get("isRead"):
        update["read"] = True
        update["date_read"] = message.get("dateRead")

    if update:
        await outreach_coll.update_one(
            {"bb_message_guid": msg_guid},
            {"$set": update}
        )
        logger.debug("📬 Updated message status for %s: %s", msg_guid[:8], update)

    return {"processed": True, "updated": bool(update)}


# ─────────────────────────────────────────────────────────────────────────────
#  Webhook Receiver Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@bb_webhook_bp.post("/webhooks/bluebubbles")
async def receive_bb_event(request: Request):
    """Receive a real-time event from the BlueBubbles server.

    BlueBubbles POSTs a JSON payload with:
        { "type": "new-message", "data": { ... } }
    """
    # Signature verification
    raw_body = await request.body()
    signature = request.headers.get("x-bb-signature", "")
    if not _verify_signature(raw_body, signature):
        logger.warning("BB webhook: invalid signature — rejecting")
        return JSONResponse({"error": "Invalid signature"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    if not payload:
        return JSONResponse({"error": "Empty payload"}, status_code=400)

    event_type = payload.get("type", "")
    event_data = payload.get("data", payload)

    logger.info("📡 BB webhook event: %s", event_type)

    # Route to appropriate handler
    from dashboard.extensions import get_db
    db = get_db()

    if event_type == "new-message":
        result = await _handle_new_message(event_data, db)
    elif event_type == "updated-message":
        result = await _handle_updated_message(event_data)
    elif event_type in ("typing-indicator", "chat-read-status-changed"):
        # Log but no action needed
        result = {"processed": True, "action": "logged_only"}
    else:
        result = {"processed": False, "reason": f"unhandled_event_type: {event_type}"}

    return {"success": True, "event_type": event_type, "result": result}


# ─────────────────────────────────────────────────────────────────────────────
#  Webhook Registration Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@bb_webhook_bp.post("/webhooks/bluebubbles/register")
async def register_bb_webhook(request: Request):
    """Register our VPS webhook URL with the BlueBubbles server.

    Call this endpoint once after startup (or when the BB ngrok tunnel URL changes).
    It is idempotent — safe to call multiple times.

    Body (optional):
        { "vps_url": "https://178.156.179.237:8088" }  — override the public URL
    """
    # Staff session or machine key (GAS_API_KEY / LEADS_INTERNAL_TOKEN) required —
    # re-pointing BB webhooks is an admin action.  The inbound receiver above
    # stays open; boot-time registration calls ensure_webhook() directly.
    denied = _require_control_auth(request, allow_machine=True)
    if denied:
        return denied
    try:
        data = await request.json() or {}
    except Exception:
        data = {}
    vps_url = str(data.get("vps_url") or _VPS_PUBLIC_URL).rstrip("/")
    if not vps_url:
        return JSONResponse(status_code=400, content={
            "success": False,
            "error": "BB_WEBHOOK_PUBLIC_URL not set — provide vps_url in body or set env var"
        })

    webhook_url = f"{vps_url}{_WEBHOOK_PATH}"
    results = []

    for suffix, server in BB_SERVERS.items():
        client = BlueBubblesClient(server["url"], server["password"])
        result = await client.ensure_webhook(webhook_url, BB_WEBHOOK_EVENTS)
        results.append({
            "server": server["label"],
            "suffix": suffix,
            "webhook_url": webhook_url,
            "success": result.get("success", False),
            "already_existed": result.get("already_existed", False),
            "data": result.get("data", {}),
        })
        logger.info(
            "BB webhook registration for %s: success=%s already_existed=%s",
            server["label"], result.get("success"), result.get("already_existed")
        )

    return {"success": True, "registrations": results}


@bb_webhook_bp.get("/webhooks/bluebubbles/status")
async def bb_webhook_status():
    """List all webhooks registered on each BlueBubbles server."""
    results = {}
    for suffix, server in BB_SERVERS.items():
        client = BlueBubblesClient(server["url"], server["password"])
        result = await client.list_webhooks()
        results[server["label"]] = {
            "success": result.get("success", False),
            "webhooks": result.get("data", []),
        }
    return {"success": True, "servers": results}


@bb_webhook_bp.delete("/webhooks/bluebubbles/{webhook_id}")
async def delete_bb_webhook(webhook_id: int, request: Request):
    """Remove a webhook registration from all BB servers (staff / machine auth)."""
    denied = _require_control_auth(request, allow_machine=True)
    if denied:
        return denied
    results = {}
    for suffix, server in BB_SERVERS.items():
        client = BlueBubblesClient(server["url"], server["password"])
        result = await client.delete_webhook(webhook_id)
        results[server["label"]] = result.get("success", False)
    return {"success": True, "results": results}