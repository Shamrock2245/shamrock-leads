"""
Accounts receivable for Shamrock Bail Bonds.

One row per active bond. Premium and down payment are entered once on Write
Bond / Record Bond and stored on ``active_bonds``. Later payments are
``financial_ledger`` entries (the same ledger SwipeSimple imports already
use). Balance is computed by ``ar_math`` and is never written back.

Reminder texts go out BlueBubbles on 239-955-0178 after a staff member
approves the draft. Shannon call briefs are prepared for the ElevenLabs
voice agent; Twilio is voice-only and is not used for SMS.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from dashboard.extensions import format_phone, get_collection, get_db
from dashboard.services import ar_math
from dashboard.services.ar_llm import (
    llm_from_env,
    reminder_system_prompt,
    reminder_user_prompt,
    template_draft,
)
from dashboard.services.ledger_service import LedgerService

logger = logging.getLogger(__name__)

_SENT_STATUSES = {"sent", "call_prepared"}


class MoneyChangeRequired(Exception):
    def __init__(self, payload: dict):
        super().__init__(payload.get("error") or "money_change_confirmation_required")
        self.payload = payload


def plan_money_write(
    before: Optional[dict],
    premium: Any,
    down_payment: Any,
    *,
    confirm: bool,
    reason: str,
) -> dict:
    err = ar_math.validate_money(premium, down_payment)
    if err:
        return {"ok": False, "error": err, "status": 400}
    fields = ar_math.money_fields(premium, down_payment)
    changed = ar_math.money_changed(before, fields["premium_cents"], fields["down_payment_cents"])
    if changed and not (confirm and str(reason or "").strip()):
        old_down = ar_math.down_payment_from_bond(before or {})
        return {
            "ok": False,
            "error": "money_change_confirmation_required",
            "status": 409,
            "current_premium_cents": ar_math.premium_cents_from_bond(before or {}),
            "current_down_payment_cents": old_down["cents"] if old_down["entered"] else None,
            "message": "Premium or down payment already on this bond. Confirm the change and include a reason. The balance is not edited directly.",
        }
    down_entered = ar_math.down_payment_from_bond(before or {})["entered"] if before else False
    premium_known = ar_math.premium_cents_from_bond(before or {}) is not None if before else False
    audit = before is None or not premium_known or not down_entered or changed
    return {"ok": True, "fields": fields, "changed": changed, "audit": audit}


async def _audit(event_type: str, booking: str, actor: str, payload: dict) -> None:
    now = datetime.now(timezone.utc)
    doc = {
        "event_type": event_type,
        "entity_type": "bond_case",
        "entity_id": booking,
        "actor": actor,
        "agent_name": actor,
        "timestamp": now,
        "created_at": now,
        **payload,
    }
    try:
        await get_collection("audit_events").insert_one(doc)
    except Exception as exc:
        logger.warning("[ar] audit write failed for %s: %s", booking, type(exc).__name__)


async def ensure_down_payment_ledger(
    *,
    booking_number: str,
    down_cents: int,
    method: str,
    reference: str,
    actor: str,
    when: datetime,
) -> str:
    """One ledger row per bond for the write-time down payment. Idempotent."""
    ref = ar_math.down_payment_ledger_ref(booking_number)
    db = get_db()
    existing = await db.financial_ledger.find_one({"stripe_swipe_ref": ref})
    if existing:
        if int(existing.get("amount") or 0) != -int(down_cents):
            await db.financial_ledger.update_one(
                {"_id": existing["_id"]},
                {"$set": {
                    "amount": -int(down_cents),
                    "method": method,
                    "reference": reference,
                    "actor": actor,
                    "entered_by": actor,
                    "adjusted_at": when,
                }},
            )
        return ref
    if down_cents <= 0:
        return ref
    await LedgerService.add_entry({
        "booking_number": ar_math.booking_key(booking_number),
        "type": "down_payment",
        "entry_kind": "down_payment",
        "amount": -int(down_cents),
        "amount_is_cents": True,
        "category": "premium",
        "method": method,
        "reference": reference,
        "actor": actor,
        "entered_by": actor,
        "source": "write_bond",
        "stripe_swipe_ref": ref,
        "timestamp": when,
        "notes": "Down payment entered on Write Bond",
    })
    return ref


async def commit_money_side_effects(
    *,
    booking_number: str,
    before: Optional[dict],
    plan: dict,
    actor: str,
    method: str,
    reference: str,
    reason: str,
    when: Optional[datetime] = None,
) -> None:
    when = when or datetime.now(timezone.utc)
    fields = plan["fields"]
    if plan.get("audit"):
        old_premium = ar_math.premium_cents_from_bond(before or {}) if before else None
        old_down = ar_math.down_payment_from_bond(before or {}) if before else {"entered": False, "cents": None}
        await _audit(
            "ar_money_changed" if plan.get("changed") else "ar_money_set",
            booking_number,
            actor,
            {
                "reason": reason or "write_bond",
                "old": {
                    "premium_cents": old_premium,
                    "down_payment_cents": old_down["cents"] if old_down.get("entered") else None,
                },
                "new": {
                    "premium_cents": fields["premium_cents"],
                    "down_payment_cents": fields["down_payment_cents"],
                },
            },
        )
    await ensure_down_payment_ledger(
        booking_number=booking_number,
        down_cents=fields["down_payment_cents"],
        method=method,
        reference=reference,
        actor=actor,
        when=when,
    )


def money_set_fields(plan: dict, *, method: str, reference: str, actor: str, next_due: str, when: datetime, before: Optional[dict]) -> dict:
    fields = dict(plan["fields"])
    fields.update({
        "down_payment_method": method,
        "down_payment_reference": reference,
        "down_payment_ledger_ref": ar_math.down_payment_ledger_ref(
            (before or {}).get("booking_number") or ""
        ),
        "down_payment_entered_at": (before or {}).get("down_payment_entered_at") or when.isoformat(),
        "down_payment_entered_by": (before or {}).get("down_payment_entered_by") or actor,
        "updated_at": when,
    })
    if plan.get("changed"):
        fields["down_payment_entered_by"] = actor
        fields["down_payment_adjusted_at"] = when.isoformat()
    if next_due:
        fields["next_payment_due"] = next_due
    return fields


async def apply_write_bond_money(payload: dict, actor: str) -> dict:
    """Upsert premium and down payment onto the bond. Does not store a balance."""
    booking_raw = str(payload.get("booking_number") or "").strip()
    if not booking_raw:
        return {"ok": False, "error": "booking_number_required", "status": 400}
    premium = payload.get("premium", payload.get("premium_amount"))
    if "down_payment" in payload and payload.get("down_payment") not in (None, ""):
        down = payload.get("down_payment")
    elif payload.get("default_down_to_premium"):
        down = premium
    else:
        return {"ok": False, "error": "down_payment_required", "status": 400}
    bonds = get_collection("active_bonds")
    before = await bonds.find_one({"booking_number": booking_raw})
    plan = plan_money_write(
        before,
        premium,
        down,
        confirm=bool(payload.get("confirm_money_change")),
        reason=str(payload.get("money_change_reason") or ""),
    )
    if not plan.get("ok"):
        return plan
    next_due = str(payload.get("next_payment_due") or "").strip()[:10]
    if next_due and not ar_math.parse_iso_date(next_due):
        return {"ok": False, "error": "invalid_next_payment_due", "status": 400}
    method = ar_math.normalize_method(payload.get("down_payment_method") or payload.get("payment_method"))
    reference = str(payload.get("down_payment_reference") or payload.get("reference") or "").strip()
    when = datetime.now(timezone.utc)
    set_fields = money_set_fields(
        plan, method=method, reference=reference, actor=actor,
        next_due=next_due, when=when, before=before,
    )
    set_fields["down_payment_ledger_ref"] = ar_math.down_payment_ledger_ref(booking_raw)
    for key in (
        "defendant_name", "indemnitor_name", "indemnitor_phone", "indemnitor_email",
        "defendant_phone", "case_number", "poa_number", "county",
    ):
        if payload.get(key):
            set_fields[key] = str(payload.get(key)).strip()
    insert = {
        "booking_number": booking_raw,
        "status": "active",
        "source": payload.get("source") or "write_bond",
        "created_at": when,
        "agent_name": actor,
        "ar_history": [],
    }
    await bonds.update_one(
        {"booking_number": booking_raw},
        {"$set": set_fields, "$setOnInsert": insert},
        upsert=True,
    )
    await commit_money_side_effects(
        booking_number=booking_raw,
        before=before,
        plan=plan,
        actor=actor,
        method=method,
        reference=reference,
        reason=str(payload.get("money_change_reason") or "write_bond"),
        when=when,
    )
    row = await bond_detail(booking_raw)
    return {"ok": True, "bond": row}


async def _load_related(bookings: list[str]) -> dict:
    keys = list({b for b in bookings if b})
    upper = list({ar_math.booking_key(b) for b in keys})
    db = get_db()
    ledger = await db.financial_ledger.find(
        {"booking_number": {"$in": upper}}
    ).to_list(length=5000)
    txns = []
    async for doc in get_collection("transactions").find({
        "booking_number": {"$in": keys + upper},
        "status": {"$nin": ["void", "voided"]},
    }):
        txns.append(doc)
    payments = []
    async for doc in get_collection("payments").find({"booking_number": {"$in": keys + upper}}):
        payments.append(doc)
    plans = {}
    async for doc in get_collection("payment_plans").find({
        "booking_number": {"$in": keys + upper},
        "status": "active",
    }):
        plans[doc.get("booking_number")] = doc
        plans[ar_math.booking_key(doc.get("booking_number"))] = doc
    return {"ledger": ledger, "transactions": txns, "payments": payments, "plans": plans}


def _group(rows: list[dict], booking: str) -> list[dict]:
    key = ar_math.booking_key(booking)
    return [row for row in rows if ar_math.booking_key(row.get("booking_number")) == key]


async def _inbound_texts(booking: str) -> list[str]:
    texts = []
    try:
        cursor = get_collection("imessage_outreach").find(
            {"booking_number": booking, "direction": "inbound"},
            {"message": 1, "text": 1},
        ).sort("sent_at", -1)
        async for doc in cursor:
            text = doc.get("message") or doc.get("text") or ""
            if text:
                texts.append(str(text))
            if len(texts) >= 20:
                break
    except Exception as exc:
        logger.warning("[ar] message history unavailable for %s: %s", booking, type(exc).__name__)
    return texts


async def build_row(bond: dict, related: Optional[dict] = None, *, with_messages: bool = False) -> dict:
    booking = bond.get("booking_number") or ""
    if related is None:
        related = await _load_related([booking])
    texts = await _inbound_texts(booking) if with_messages else []
    plan = (related.get("plans") or {}).get(booking) or (related.get("plans") or {}).get(ar_math.booking_key(booking))
    return ar_math.build_ar_row(
        bond,
        ledger_entries=_group(related.get("ledger") or [], booking),
        transactions=_group(related.get("transactions") or [], booking),
        legacy_payments=_group(related.get("payments") or [], booking),
        plan=plan,
        inbound_texts=texts,
    )


async def list_bonds(match: dict, *, filt: str, sort: str, query: str) -> dict:
    bonds = []
    cursor = get_collection("active_bonds").find(match or {})
    async for doc in cursor:
        doc.pop("_id", None)
        bonds.append(doc)
    related = await _load_related([b.get("booking_number") or "" for b in bonds])
    rows = []
    needle = (query or "").strip().lower()
    for bond in bonds:
        row = await build_row(bond, related, with_messages=False)
        if needle:
            hay = " ".join([
                row.get("defendant_name") or "",
                row.get("indemnitor_name") or "",
                row.get("case_number") or "",
                row.get("poa_number") or "",
                row.get("booking_number") or "",
            ]).lower()
            if needle not in hay:
                continue
        if not ar_math.row_matches_filter(row, filt):
            continue
        row.pop("payments", None)
        row["defendant_phone"] = _mask(row.get("defendant_phone"))
        row["indemnitor_phone"] = _mask(row.get("indemnitor_phone"))
        rows.append(row)
    rows = ar_math.sort_rows(rows, sort)
    open_cents = sum(r["balance_due_cents"] or 0 for r in rows if (r.get("balance_due_cents") or 0) > 0)
    return {
        "rows": rows,
        "count": len(rows),
        "open_balance_cents": open_cents,
        "open_balance_dollars": ar_math.cents_to_dollars(open_cents),
        "overdue_count": sum(1 for r in rows if (r.get("days_overdue") or 0) > 0),
        "paid_in_full_count": sum(1 for r in rows if r.get("ar_status") == "paid_in_full"),
    }


async def bond_detail(booking_number: str) -> Optional[dict]:
    bond = await get_collection("active_bonds").find_one({"booking_number": booking_number})
    if not bond:
        return None
    bond.pop("_id", None)
    row = await build_row(bond, with_messages=True)
    history = []
    for event in bond.get("ar_history") or []:
        item = dict(event)
        item.pop("recipient_phone", None)
        history.append(item)
    row["history"] = history
    row["swipesimple_hook"] = "swipesimple_match_hook"
    return row


def _mask(phone: Any) -> str:
    last = ar_math.phone_last10(phone)
    if len(last) < 4:
        return ""
    return f"•••-•••-{last[-4:]}"


async def log_later_payment(booking_number: str, payload: dict, actor: str) -> dict:
    bond = await get_collection("active_bonds").find_one({"booking_number": booking_number})
    if not bond:
        return {"ok": False, "error": "bond_not_found", "status": 404}
    try:
        cents = ar_math.to_cents(payload.get("amount"))
    except Exception:
        return {"ok": False, "error": "invalid_amount", "status": 400}
    if cents <= 0:
        return {"ok": False, "error": "amount_must_be_positive", "status": 400}
    method = ar_math.normalize_method(payload.get("method"))
    if method not in ("cash", "check", "swipesimple"):
        return {"ok": False, "error": "method_must_be_cash_check_or_swipesimple", "status": 400}
    reference = str(payload.get("reference") or "").strip()
    when = ar_math.as_utc(payload.get("paid_at") or payload.get("date")) or datetime.now(timezone.utc)
    txn_id = reference or f"AR-{uuid.uuid4().hex[:12].upper()}"
    db = get_db()
    if reference:
        existing = await db.financial_ledger.find_one({
            "booking_number": ar_math.booking_key(booking_number),
            "stripe_swipe_ref": reference,
        })
        if existing:
            return {"ok": True, "duplicate": True, "transaction_id": reference}
    await LedgerService.add_entry({
        "booking_number": ar_math.booking_key(booking_number),
        "type": "payment",
        "entry_kind": "payment",
        "amount": -cents,
        "amount_is_cents": True,
        "category": "premium",
        "method": method,
        "reference": reference,
        "actor": actor,
        "entered_by": actor,
        "source": "ar_manual" if method != "swipesimple" else "swipesimple",
        "stripe_swipe_ref": txn_id,
        "timestamp": when,
        "notes": str(payload.get("notes") or "")[:500],
    })
    payment_doc = {
        "booking_number": booking_number,
        "amount": float(ar_math.cents_to_dollars(cents)),
        "method": method,
        "type": "payment",
        "status": "completed",
        "source": "ar_manual",
        "transaction_id": txn_id,
        "reference": reference,
        "timestamp": when.isoformat(),
        "entered_by": actor,
        "agent_name": actor,
    }
    await get_collection("payments").insert_one(payment_doc)
    await get_collection("transactions").insert_one({
        "transaction_id": txn_id,
        "amount": float(ar_math.cents_to_dollars(cents)),
        "method": method,
        "type": "premium",
        "status": "completed",
        "booking_number": booking_number,
        "defendant_name": bond.get("defendant_name") or "",
        "poa_number": bond.get("poa_number") or "",
        "case_number": bond.get("case_number") or "",
        "reference_id": reference or txn_id,
        "source": "ar_manual" if method != "swipesimple" else "swipesimple",
        "agent_name": actor,
        "timestamp": when.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": str(payload.get("notes") or "AR payment")[:500],
    })
    await get_collection("active_bonds").update_one(
        {"booking_number": booking_number},
        {"$push": {"ar_history": {
            "event": "payment_logged",
            "status": "logged",
            "amount_cents": cents,
            "method": method,
            "reference": reference,
            "paid_at": when.isoformat(),
            "entered_by": actor,
            "at": datetime.now(timezone.utc).isoformat(),
        }}},
    )
    await _audit("ar_payment_logged", booking_number, actor, {
        "amount_cents": cents,
        "method": method,
        "reference": reference,
        "paid_at": when.isoformat(),
    })
    row = await bond_detail(booking_number)
    return {"ok": True, "transaction_id": txn_id, "bond": row}


def _party(bond: dict, role: str) -> dict:
    indemnitor = bond.get("indemnitor") if isinstance(bond.get("indemnitor"), dict) else {}
    if role == "defendant":
        return {
            "role": "defendant",
            "name": bond.get("defendant_name") or "",
            "phone": bond.get("defendant_phone") or "",
        }
    return {
        "role": "indemnitor",
        "name": bond.get("indemnitor_name") or indemnitor.get("name") or "",
        "phone": bond.get("indemnitor_phone") or indemnitor.get("phone") or "",
    }


def _prior_sends(bond: dict, phone: str) -> list:
    last = ar_math.phone_last10(phone)
    stamps = []
    for event in bond.get("ar_history") or []:
        if event.get("status") not in _SENT_STATUSES:
            continue
        if ar_math.phone_last10(event.get("recipient_phone")) != last:
            continue
        stamps.append(event.get("sent_at") or event.get("at"))
    return stamps


async def _opted_out(bond: dict, phone: str) -> bool:
    if bond.get("opted_out"):
        return True
    try:
        from dashboard.services.sms_consent_ledger import check_send_allowed
        blocked = await check_send_allowed(phone, "ar_premium_reminder")
        return bool(blocked)
    except Exception as exc:
        logger.error("[ar] consent lookup failed: %s", type(exc).__name__)
        if os.getenv("BB_OPTOUT_GATE_FAIL_CLOSED", "").lower() in ("1", "true", "yes"):
            return True
        return False


async def draft_reminder(booking_number: str, payload: dict, actor: str) -> dict:
    bond = await get_collection("active_bonds").find_one({"booking_number": booking_number})
    if not bond:
        return {"ok": False, "error": "bond_not_found", "status": 404}
    role = str(payload.get("recipient_role") or "indemnitor").lower()
    party = _party(bond, role)
    if role == "indemnitor" and not ar_math.phone_last10(party["phone"]):
        party = _party(bond, "defendant")
        role = "defendant" if ar_math.phone_last10(party["phone"]) else role
    channel = "call" if str(payload.get("channel") or "text").lower() in ("call", "shannon", "voice") else "text"
    row = await build_row(bond, with_messages=True)
    if not row.get("balance_due_cents"):
        return {"ok": False, "error": "no_balance_due", "status": 400, "bond": row}
    system = reminder_system_prompt()
    user = reminder_user_prompt(row, recipient_name=party["name"], recipient_role=role, channel=channel)
    draft = await llm_from_env().draft(system=system, user=user)
    provider = "openai" if draft else "template"
    if not draft:
        draft = template_draft(row, recipient_name=party["name"], channel=channel)
    hits = ar_math.prohibited_language(draft)
    draft_id = uuid.uuid4().hex[:16]
    now = datetime.now(timezone.utc).isoformat()
    event = {
        "event": "reminder_draft",
        "draft_id": draft_id,
        "status": "draft",
        "channel": channel,
        "recipient_role": role,
        "recipient_name": party["name"],
        "recipient_phone": party["phone"],
        "tone": row.get("tone"),
        "sentiment": row.get("sentiment"),
        "draft_text": draft,
        "provider": provider,
        "prohibited_hits": hits,
        "drafted_by": actor,
        "at": now,
    }
    await get_collection("active_bonds").update_one(
        {"booking_number": booking_number},
        {"$push": {"ar_history": event}},
    )
    await _audit("ar_reminder_draft", booking_number, actor, {
        "draft_id": draft_id,
        "channel": channel,
        "recipient_role": role,
        "tone": row.get("tone"),
        "provider": provider,
    })
    preview = dict(event)
    preview.pop("recipient_phone", None)
    preview["recipient_phone_masked"] = _mask(party["phone"])
    preview["balance_due_dollars"] = row.get("balance_due_dollars")
    preview["days_overdue"] = row.get("days_overdue")
    return {"ok": True, "draft": preview, "tone": row.get("tone"), "provider": provider}


async def _send_bluebubbles(phone: str, message: str) -> dict:
    from dashboard.services.bb_client import get_bb_client, normalize_bb_send_result
    from dashboard.services.sms_consent_ledger import check_send_allowed

    blocked = await check_send_allowed(phone, "ar_premium_reminder")
    if blocked:
        blocked["rail"] = "bluebubbles"
        blocked["from_number"] = ar_math.AR_FROM_LINE
        return blocked
    bb = get_bb_client(ar_math.AR_FROM_LINE)
    if not bb:
        return {
            "success": False,
            "sent": False,
            "error": "no_bb_server",
            "rail": "bluebubbles",
            "from_number": ar_math.AR_FROM_LINE,
        }
    e164 = format_phone(phone) or phone
    result = await bb.send_text(f"any;-;{e164}", message, purpose="ar_premium_reminder")
    normalized = normalize_bb_send_result(result if isinstance(result, dict) else {"success": False})
    normalized["rail"] = "bluebubbles"
    normalized["from_number"] = ar_math.AR_FROM_LINE
    return normalized


async def place_shannon_outbound_call(phone: str, script: str) -> dict:
    """
    Hook for a staff-approved Shannon call.

    Twilio is voice-only here (Calls API). SMS is never sent through Twilio.
    Live dial stays off unless AR_SHANNON_PLACE_CALLS=1 and a TwiML URL,
    voice caller ID, and Twilio credentials are all set.
    """
    hook = "place_shannon_outbound_call"
    enabled = os.getenv("AR_SHANNON_PLACE_CALLS", "").strip().lower() in ("1", "true", "yes")
    if not enabled:
        return {"placed": False, "reason": "outbound_dial_not_enabled", "hook": hook, "voice": "twilio"}
    twiml = (os.getenv("SHANNON_OUTBOUND_TWIML_URL") or "").strip()
    sid = (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.getenv("TWILIO_AUTH_TOKEN") or "").strip()
    from_number = (os.getenv("TWILIO_VOICE_FROM") or os.getenv("SHANNON_VOICE_FROM") or "").strip()
    if not (twiml and sid and token and from_number):
        return {"placed": False, "reason": "shannon_voice_not_configured", "hook": hook, "voice": "twilio"}
    import httpx
    e164 = format_phone(phone) or phone
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                url,
                auth=(sid, token),
                data={"To": e164, "From": from_number, "Url": twiml},
            )
        if resp.status_code >= 300:
            return {"placed": False, "reason": "twilio_voice_rejected", "hook": hook, "status_code": resp.status_code}
        body = resp.json()
        return {"placed": True, "hook": hook, "call_sid": body.get("sid"), "voice": "twilio"}
    except Exception as exc:
        logger.warning("[ar] Shannon voice call failed: %s", type(exc).__name__)
        return {"placed": False, "reason": "twilio_voice_error", "hook": hook}


async def send_reminder(booking_number: str, payload: dict, actor: str, *, now: Optional[datetime] = None) -> dict:
    """Nothing leaves the office until this is called by an explicit staff send."""
    bond = await get_collection("active_bonds").find_one({"booking_number": booking_number})
    if not bond:
        return {"ok": False, "error": "bond_not_found", "status": 404}
    draft_id = str(payload.get("draft_id") or "").strip()
    final_text = str(payload.get("final_text") or "").strip()
    if not draft_id or not final_text:
        return {"ok": False, "error": "draft_and_final_text_required", "status": 400}
    draft = None
    for event in bond.get("ar_history") or []:
        if event.get("draft_id") == draft_id and event.get("status") == "draft":
            draft = event
    if not draft:
        return {"ok": False, "error": "draft_not_found", "status": 404}
    role = draft.get("recipient_role") or "indemnitor"
    party = _party(bond, role)
    phone = party.get("phone") or ""
    now = now or datetime.now(timezone.utc)
    opted_out = await _opted_out(bond, phone)
    gate = ar_math.evaluate_reminder_send(
        now_utc=now,
        tz_name=str(payload.get("timezone") or ar_math.CONTACT_TZ),
        opted_out=opted_out,
        recipient_role=role,
        recipient_phone=phone,
        defendant_phone=bond.get("defendant_phone") or "",
        indemnitor_phone=(_party(bond, "indemnitor").get("phone") or ""),
        text=final_text,
        prior_sends=_prior_sends(bond, phone),
    )
    if not gate["allowed"]:
        await _push_history(booking_number, {
            "event": "reminder_blocked",
            "draft_id": draft_id,
            "status": "blocked",
            "channel": draft.get("channel"),
            "reasons": gate["reasons"],
            "approved_by": actor,
            "at": now.isoformat(),
        })
        await _audit("ar_reminder_blocked", booking_number, actor, {
            "draft_id": draft_id,
            "reasons": gate["reasons"],
        })
        return {"ok": False, "error": "send_blocked", "status": 403, "reasons": gate["reasons"]}

    channel = draft.get("channel") or "text"
    promise = str(payload.get("promise_date") or "").strip()[:10]
    if promise and not ar_math.parse_iso_date(promise):
        return {"ok": False, "error": "invalid_promise_date", "status": 400}
    delivery: dict
    status = "sent"
    if channel == "call":
        delivery = await place_shannon_outbound_call(phone, final_text)
        status = "call_prepared" if not delivery.get("placed") else "sent"
    else:
        delivery = await _send_bluebubbles(phone, final_text)
        if delivery.get("blocked") or delivery.get("reason") == "opted_out":
            return {"ok": False, "error": "opted_out", "status": 403, "reasons": ["opted_out"]}
        if not (delivery.get("sent") or delivery.get("queued") or delivery.get("success")):
            return {"ok": False, "error": delivery.get("error") or "send_failed", "status": 502, "delivery": {
                "rail": "bluebubbles",
                "from_number": ar_math.AR_FROM_LINE,
                "sent": False,
            }}
    event = {
        "event": "reminder_sent" if channel != "call" or delivery.get("placed") else "call_prepared",
        "draft_id": draft_id,
        "status": status,
        "channel": "shannon_voice" if channel == "call" else "bluebubbles",
        "recipient_role": role,
        "recipient_name": party.get("name") or "",
        "recipient_phone": phone,
        "draft_text": draft.get("draft_text") or "",
        "final_text": final_text,
        "approved_by": actor,
        "sent_at": now.isoformat(),
        "from_number": ar_math.AR_FROM_LINE if channel != "call" else "",
        "tone": draft.get("tone"),
        "promise_date": promise,
        "delivery": {
            "rail": delivery.get("rail") or ("twilio_voice" if channel == "call" else "bluebubbles"),
            "sent": bool(delivery.get("sent") or delivery.get("placed")),
            "queued": bool(delivery.get("queued")),
            "placed": bool(delivery.get("placed")),
            "reason": delivery.get("reason") or "",
        },
    }
    await _push_history(booking_number, event)
    await _audit(
        "ar_reminder_sent" if status == "sent" else "ar_call_prepared",
        booking_number,
        actor,
        {
            "draft_id": draft_id,
            "channel": event["channel"],
            "recipient_role": role,
            "approved_by": actor,
            "sent_at": now.isoformat(),
        },
    )
    safe = dict(event)
    safe.pop("recipient_phone", None)
    safe["recipient_phone_masked"] = _mask(phone)
    return {"ok": True, "reminder": safe}


async def _push_history(booking_number: str, event: dict) -> None:
    await get_collection("active_bonds").update_one(
        {"booking_number": booking_number},
        {"$push": {"ar_history": event}},
    )
