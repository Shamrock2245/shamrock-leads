"""
ShamrockLeads — SMS/iMessage consent (STOP / TCPA) ledger + per-recipient send gate
===================================================================================

All client texting on the office line goes through BlueBubbles.  This module is
the single source of truth for "may we text this number right now?".

Ledger collection: ``sms_consent_ledger`` (append-only; never TTL'd)

    {
      phone:               "+1XXXXXXXXXX"   # E.164
      phone_last10:        "XXXXXXXXXX"     # lookup key
      event:               "opt_out" | "opt_in"
      keyword:             "stop" | "unsubscribe" | ... | "start" | "unstop"
      match_type:          "exact" | "leading_keyword"
      needs_staff_review:  bool             # True for leading_keyword (e.g. "stop texting me pls")
      source_message_guid: str              # BlueBubbles message GUID ("" if unknown)
      chat_guid:           str
      source:              "bb_webhook" | "bb_poll"
      timestamp:           datetime (UTC)   # message time when BB supplied it, else receipt time
      recorded_at:         datetime (UTC)
    }

Send gate: :func:`check_send_allowed` is called by the BlueBubbles transport
(``BlueBubblesClient`` send methods) and by ``dashboard.services.bb_client``
entry points *before* anything is queued or sent.  If the latest ledger event
for the recipient is ``opt_out`` the send is blocked and a structured result is
returned (``blocked=True``, ``reason="recipient_opted_out"``, ``purpose=...``).

Pre-ledger history: when a number has no ledger rows yet, the gate falls back to
the legacy opt-out log that the webhook has always written to
``imessage_outreach`` (``category="opt_out"``, inbound) and re-classifies the
stored text with the same keyword rules.  No backfill writes are performed.

Owner decision (final, fail-closed):

* A STOP opt-out blocks EVERY outbound text to that number — marketing,
  DocuSeal signing links, payment links, AND court-date / FTA / payment
  reminders.  There is no purpose-based exemption and no staff override; the
  number stays blocked until it texts START / UNSTOP back.
* A bare stop word (case-insensitive, punctuation-tolerant) is an opt-out.  A
  longer message that STARTS with a stop word ("Stop texting me please") is
  also an opt-out AND is flagged for staff review (ledger row, conversation
  row in ``imessage_outreach``, and the prospective bond).  A stop word that
  only appears later in the message is NOT an opt-out.

PII: log lines only ever carry the last 4 digits of a phone number.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

LEDGER_COLLECTION = "sms_consent_ledger"

EVENT_OPT_OUT = "opt_out"
EVENT_OPT_IN = "opt_in"

BLOCK_REASON_OPTED_OUT = "recipient_opted_out"

# Carrier / CTIA standard opt-out words, "revoke" (FCC TCPA revocation rule),
# plus the phrases the legacy webhook path already honoured ("stop all",
# "optout", "opt out").
OPT_OUT_KEYWORDS = frozenset({
    "stop", "stopall", "stop all", "unsubscribe", "cancel", "end", "quit",
    "revoke", "optout", "opt out",
})
# Re-subscribe words (carrier standard).
OPT_IN_KEYWORDS = frozenset({"start", "unstop"})

# Longest phrases first so "stop all" wins over "stop".
_OPT_OUT_ORDERED = sorted(OPT_OUT_KEYWORDS, key=len, reverse=True)
_OPT_IN_ORDERED = sorted(OPT_IN_KEYWORDS, key=len, reverse=True)

_NON_WORD = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def _normalise_text(text: Any) -> str:
    """Lower-case, strip punctuation/emoji, collapse whitespace."""
    lowered = str(text or "").lower()
    lowered = _NON_WORD.sub(" ", lowered)
    return _SPACES.sub(" ", lowered).strip()


def _canonical(keyword: str) -> str:
    """Store multi-word keywords without spaces ("stop all" -> "stopall")."""
    return keyword.replace(" ", "")


def classify_consent_keyword(text: Any) -> Optional[Dict[str, Any]]:
    """Classify an inbound message as an opt-out / opt-in keyword reply.

    Returns ``None`` for ordinary messages, else::

        {"event": "opt_out"|"opt_in", "keyword": "stop", "match_type": "exact"|"leading_keyword"}

    * ``exact``           — the whole message is the keyword ("STOP", "Stop.", "stop all")
    * ``leading_keyword`` — the message *starts with* the keyword as a whole
      word ("Stop texting me", "UNSUBSCRIBE please").  This mirrors the legacy
      ``startswith`` behaviour but on word boundaries, so "Quite a day" or
      "Ending soon" no longer count as an opt-out.  These are flagged
      ``needs_staff_review`` in the ledger but are still honoured (fail safe).

    Opt-in (START / UNSTOP) is only recognised as an exact message so a
    conversational "start the paperwork" never silently re-subscribes anyone.
    """
    norm = _normalise_text(text)
    if not norm:
        return None
    if norm in OPT_OUT_KEYWORDS:
        return {"event": EVENT_OPT_OUT, "keyword": _canonical(norm), "match_type": "exact"}
    if norm in OPT_IN_KEYWORDS:
        return {"event": EVENT_OPT_IN, "keyword": norm, "match_type": "exact"}
    for kw in _OPT_OUT_ORDERED:
        if norm.startswith(kw + " "):
            return {"event": EVENT_OPT_OUT, "keyword": _canonical(kw), "match_type": "leading_keyword"}
    return None


def phone_last10(raw: Any) -> str:
    """Return the 10-digit US number for a phone / chat GUID, or ''."""
    value = str(raw or "")
    if ";-;" in value:
        value = value.split(";-;")[-1]
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def _mask(last10: str) -> str:
    return f"...{last10[-4:]}" if last10 else "(none)"


def _get_collection(name: str):
    from dashboard.extensions import get_collection
    return get_collection(name)


def _as_utc(ts: Any) -> datetime:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


async def record_consent_event(
    *,
    phone: str,
    event: str,
    keyword: str,
    match_type: str = "exact",
    source_message_guid: str = "",
    chat_guid: str = "",
    source: str = "bb_webhook",
    timestamp: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Persist one opt-out / opt-in event.  Idempotent per (message GUID, event)."""
    last10 = phone_last10(phone)
    if not last10 or event not in (EVENT_OPT_OUT, EVENT_OPT_IN):
        return {"recorded": False, "reason": "invalid_phone_or_event"}
    now = datetime.now(timezone.utc)
    doc = {
        "phone": f"+1{last10}",
        "phone_last10": last10,
        "event": event,
        "keyword": keyword,
        "match_type": match_type,
        "needs_staff_review": match_type != "exact",
        "source_message_guid": source_message_guid or "",
        "chat_guid": chat_guid or "",
        "source": source,
        "timestamp": _as_utc(timestamp) if timestamp else now,
        "recorded_at": now,
    }
    coll = _get_collection(LEDGER_COLLECTION)
    if source_message_guid:
        res = await coll.update_one(
            {"source_message_guid": source_message_guid, "event": event},
            {"$setOnInsert": doc},
            upsert=True,
        )
        created = bool(getattr(res, "upserted_id", None))
    else:
        await coll.insert_one(doc)
        created = True
    logger.warning(
        "[consent_ledger] %s recorded for %s keyword=%s match=%s new=%s",
        event, _mask(last10), keyword, match_type, created,
    )
    return {"recorded": True, "created": created, "event": event}


def _gate_fail_closed() -> bool:
    return (os.getenv("BB_OPTOUT_GATE_FAIL_CLOSED") or "").strip().lower() in ("1", "true", "yes")


async def get_consent_state(phone: Any) -> Dict[str, Any]:
    """Return ``{"opted_out": bool, "source": ..., "event": {...}|None}`` for a number."""
    last10 = phone_last10(phone)
    if not last10:
        return {"opted_out": False, "source": "no_phone", "event": None}

    ledger = _get_collection(LEDGER_COLLECTION)
    latest = await ledger.find_one(
        {"phone_last10": last10},
        sort=[("timestamp", -1), ("recorded_at", -1)],
    )
    if latest:
        return {
            "opted_out": latest.get("event") == EVENT_OPT_OUT,
            "source": "ledger",
            "event": {
                "event": latest.get("event"),
                "keyword": latest.get("keyword"),
                "match_type": latest.get("match_type"),
                "timestamp": latest.get("timestamp"),
            },
        }

    # Pre-ledger history: legacy STOP log rows written by the webhook.
    outreach = _get_collection("imessage_outreach")
    legacy = await outreach.find_one(
        {
            "category": "opt_out",
            "direction": "inbound",
            "recipient_phone": {"$in": [f"+1{last10}", last10, f"1{last10}"]},
        },
        sort=[("sent_at", -1)],
    )
    if legacy:
        cls = classify_consent_keyword(legacy.get("message"))
        if cls and cls["event"] == EVENT_OPT_OUT:
            return {
                "opted_out": True,
                "source": "legacy_outreach_log",
                "event": {"event": EVENT_OPT_OUT, "keyword": cls["keyword"],
                          "match_type": cls["match_type"], "timestamp": legacy.get("sent_at")},
            }
    return {"opted_out": False, "source": "none", "event": None}


def blocked_send_result(last10: str, purpose: str, state: Optional[Dict[str, Any]] = None,
                        reason: str = BLOCK_REASON_OPTED_OUT) -> Dict[str, Any]:
    """Structured, caller-visible result for a gated send (never contains the full phone)."""
    return {
        "success": False,
        "sent": False,
        "queued": False,
        "blocked": True,
        "status": "blocked",
        "channel": "blocked",
        "error": reason,
        "reason": reason,
        "purpose": purpose or "unspecified",
        "phone_last4": last10[-4:] if last10 else "",
        "consent_source": (state or {}).get("source"),
        "message": "Recipient opted out of texts (STOP). Send blocked until they reply START.",
    }


async def check_send_allowed(recipient: Any, purpose: str = "unspecified") -> Optional[Dict[str, Any]]:
    """Per-recipient send gate.

    Returns ``None`` when the send may proceed, or a blocked-result dict when the
    recipient has opted out.  ``recipient`` may be a phone number or a 1:1 chat
    GUID (``any;-;+1...``).  Group chats / email handles cannot be resolved to a
    single phone and are allowed (logged at debug).

    If the ledger lookup itself fails (e.g. Mongo unavailable) the send is
    allowed and an error is logged, unless ``BB_OPTOUT_GATE_FAIL_CLOSED`` is
    truthy, in which case it is blocked with ``reason=consent_check_unavailable``.
    """
    last10 = phone_last10(recipient)
    if not last10:
        logger.debug("[consent_gate] recipient not a resolvable phone; purpose=%s", purpose)
        return None
    try:
        state = await get_consent_state(last10)
    except Exception as exc:  # pragma: no cover - exercised via tests with a raising collection
        if _gate_fail_closed():
            logger.error("[consent_gate] ledger lookup failed for %s purpose=%s — BLOCKING (fail-closed): %s",
                         _mask(last10), purpose, type(exc).__name__)
            return blocked_send_result(last10, purpose, None, reason="consent_check_unavailable")
        logger.error("[consent_gate] ledger lookup failed for %s purpose=%s — allowing (fail-open): %s",
                     _mask(last10), purpose, type(exc).__name__)
        return None
    if state.get("opted_out"):
        logger.warning(
            "[consent_gate] BLOCKED send to %s reason=%s purpose=%s source=%s",
            _mask(last10), BLOCK_REASON_OPTED_OUT, purpose, state.get("source"),
        )
        return blocked_send_result(last10, purpose, state)
    return None
