"""Shared SwipeSimple payment-link dispatch for bail packets.

Staff endpoint and auto-packet hooks both go through this module so premium
amount + static checkout URL are delivered consistently via BlueBubbles/Gmail.

SwipeSimple merchant links are static (no amount query param on current plan);
premium is always placed in the accompanying SMS/email copy.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from dashboard.extensions import get_collection
from dashboard.services.bb_client import (
    bb_send_accepted,
    normalize_bb_send_result,
    send_message_universal,
)

logger = logging.getLogger(__name__)

# Idempotency window for auto-sends (staff force=True bypasses).
# NOTE: the real guard for auto-sends is the atomic send-once claim below
# (``payment_link_send_once``); this window is kept as an extra soft check.
_IDEMPOTENCY_HOURS = 24
# Atomic send-once marker for AUTO sends (maybe_send_packet_payment_link).
# Set by a conditional find_one_and_update BEFORE any message is sent; never
# cleared automatically. A claim that exists (claimed / sent / failed / stale)
# blocks every later auto-send for that packet (or booking, when there is no
# packet) → manual review. Staff's explicit endpoint
# (POST /api/paperwork/payment/swipesimple-link → send_swipesimple_payment_link)
# does not use this marker.
SEND_ONCE_FIELD = "payment_link_send_once"
# Staff-confirmed premium marker (NEW in PR #60 — no pre-existing field).
# An AUTO send (maybe_send_packet_payment_link) requires ALL THREE on the same
# document (paperwork packet, else active bond): a positive amount, a timestamp
# and who confirmed it. Stored ``premium`` / ``premium_amount`` values are NOT
# used for auto sends: they are often the 10%-of-bond estimate written by
# intake promote (intake.py), packet_builder_service, or the DocuSeal prefill,
# and are indistinguishable from staff-entered values.
# NOTHING in the codebase sets these fields yet (no staff confirmation UI) — so
# automatic sends stay skipped with reason=premium_unconfirmed until one exists.
# Never auto-set / backfill them.
PREMIUM_CONFIRMED_AMOUNT_FIELD = "premium_confirmed_amount"
PREMIUM_CONFIRMED_AT_FIELD = "premium_confirmed_at"
PREMIUM_CONFIRMED_BY_FIELD = "premium_confirmed_by"
_DEFAULT_SWIPESIMPLE_URL = (
    "https://swipesimple.com/links/lnk_b6bf996f4c57bb340a150e297e769abd"
)
_PAID_STATUSES = frozenset(
    {"paid", "collected", "complete", "completed", "approved", "received"}
)


def resolve_swipesimple_url() -> str:
    """Return configured static SwipeSimple checkout URL (amount lives in copy)."""
    url = (os.getenv("SWIPESIMPLE_PAYMENT_LINK") or "").strip()
    if not url:
        try:
            from dashboard.deps import get_settings

            url = (getattr(get_settings(), "SWIPESIMPLE_PAYMENT_LINK", None) or "").strip()
        except Exception:
            url = ""
    return url or _DEFAULT_SWIPESIMPLE_URL


def _packet_lookup_filter(packet_id: str) -> dict:
    clauses: list[dict] = [{"packet_id": packet_id}]
    try:
        from bson import ObjectId

        if ObjectId.is_valid(packet_id):
            clauses.append({"_id": ObjectId(packet_id)})
    except Exception:
        pass
    return {"$or": clauses} if len(clauses) > 1 else clauses[0]


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    try:
        raw = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _is_paid(doc: Optional[dict]) -> bool:
    if not doc:
        return False
    if doc.get("payment_received") is True:
        return True
    for key in ("payment_status", "last_payment_status", "premium_paid"):
        val = doc.get(key)
        if isinstance(val, bool) and val:
            return True
        if str(val or "").strip().lower() in _PAID_STATUSES:
            return True
    try:
        if float(doc.get("total_paid") or 0) > 0 and str(
            doc.get("payment_status") or ""
        ).lower() in _PAID_STATUSES.union({"partial"}):
            # Treat fully paid only when status says so; partial still needs link.
            if str(doc.get("payment_status") or "").lower() in _PAID_STATUSES:
                return True
    except (TypeError, ValueError):
        pass
    return False


def _recent_send(doc: Optional[dict], hours: int = _IDEMPOTENCY_HOURS) -> bool:
    if not doc:
        return False
    sent_at = _parse_ts(doc.get("last_payment_link_sent_at"))
    if not sent_at:
        return False
    return sent_at >= datetime.now(timezone.utc) - timedelta(hours=hours)


async def _load_context(
    *,
    packet_id: str = "",
    booking_number: str = "",
    packet_doc: Optional[dict] = None,
    bond_doc: Optional[dict] = None,
    intake_doc: Optional[dict] = None,
) -> Dict[str, Any]:
    """Resolve packet / bond / intake documents for contact + premium lookup."""
    pkts = get_collection("paperwork_packets")
    bonds_col = get_collection("active_bonds")

    if packet_doc is None and packet_id:
        try:
            packet_doc = await pkts.find_one(_packet_lookup_filter(packet_id))
        except Exception as exc:
            logger.warning("[payment_link] packet lookup failed: %s", exc)
            packet_doc = None

    if not booking_number and packet_doc:
        booking_number = str(
            packet_doc.get("booking_number")
            or packet_doc.get("defendant_booking_number")
            or ""
        ).strip()

    if bond_doc is None and booking_number:
        try:
            bond_doc = await bonds_col.find_one({"booking_number": booking_number})
        except Exception:
            bond_doc = None

    if intake_doc is None and packet_doc and packet_doc.get("intake_id"):
        intake_col = get_collection("intake_queue")
        intake_id = str(packet_doc.get("intake_id") or "").strip()
        intake_clauses: list[dict] = [{"intake_id": intake_id}]
        try:
            from bson import ObjectId

            if ObjectId.is_valid(intake_id):
                intake_clauses.append({"_id": ObjectId(intake_id)})
        except Exception:
            pass
        try:
            intake_doc = await intake_col.find_one(
                {"$or": intake_clauses} if len(intake_clauses) > 1 else intake_clauses[0]
            )
        except Exception:
            intake_doc = None

    return {
        "packet_id": packet_id or (str(packet_doc.get("packet_id") or "") if packet_doc else ""),
        "booking_number": booking_number,
        "packet_doc": packet_doc,
        "bond_doc": bond_doc,
        "intake_doc": intake_doc,
    }


def _money_or_zero(raw: Any) -> float:
    if raw is None or raw == "" or isinstance(raw, bool):
        return 0.0
    try:
        val = float(str(raw).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return 0.0
    return val if val > 0 else 0.0


def confirmed_premium(
    packet_doc: Optional[dict],
    bond_doc: Optional[dict],
) -> float:
    """Staff-confirmed premium, or 0.0 when there is none.

    Counts ONLY when ``premium_confirmed_amount`` > 0 AND ``premium_confirmed_at``
    AND ``premium_confirmed_by`` are all set on the same document (packet first,
    then active bond). Plain ``premium`` / ``premium_amount`` / ``total_premium``
    never count — they may be the 10% estimate.
    """
    for doc in (packet_doc, bond_doc):
        if not isinstance(doc, dict):
            continue
        amount = _money_or_zero(doc.get(PREMIUM_CONFIRMED_AMOUNT_FIELD))
        at = doc.get(PREMIUM_CONFIRMED_AT_FIELD)
        by = str(doc.get(PREMIUM_CONFIRMED_BY_FIELD) or "").strip()
        if amount > 0 and at and by:
            return amount
    return 0.0


def _resolve_premium(
    amount_val: Any,
    packet_doc: Optional[dict],
    bond_doc: Optional[dict],
    intake_doc: Optional[dict],
) -> float:
    """Amount for the STAFF endpoint copy: the staff-entered amount if > 0, else
    the staff-confirmed premium, else 0.0 (copy then says "Confirmed Amount").

    Stored ``premium`` / ``premium_amount`` fields are deliberately NOT used as a
    fallback: they may be the 10%-of-bond estimate (intake promote,
    packet_builder_service, DocuSeal prefill) and must never be quoted to a
    customer as the "Confirmed Premium". ``intake_doc`` is accepted for
    signature compatibility only.
    """
    amount = _money_or_zero(amount_val)
    if amount > 0:
        return amount
    return confirmed_premium(packet_doc, bond_doc)


def _resolve_contacts(
    *,
    phone: str,
    email_addr: str,
    defendant_name: str,
    packet_doc: Optional[dict],
    bond_doc: Optional[dict],
    intake_doc: Optional[dict],
) -> tuple[str, str, str]:
    if not phone:
        phone = (
            (packet_doc.get("indemnitor_phone") if packet_doc else "")
            or (packet_doc.get("phone") if packet_doc else "")
            or (intake_doc.get("indemnitor_phone") if intake_doc else "")
            or (bond_doc.get("indemnitor_phone") if bond_doc else "")
            or ((bond_doc.get("indemnitor") or {}).get("phone") if bond_doc else "")
            or ""
        )
    phone = str(phone or "").strip()

    if not email_addr:
        email_addr = (
            (packet_doc.get("indemnitor_email") if packet_doc else "")
            or (packet_doc.get("email") if packet_doc else "")
            or (intake_doc.get("indemnitor_email") if intake_doc else "")
            or (bond_doc.get("indemnitor_email") if bond_doc else "")
            or ((bond_doc.get("indemnitor") or {}).get("email") if bond_doc else "")
            or ""
        )
    email_addr = str(email_addr or "").strip()

    if not defendant_name:
        defendant_name = (
            (packet_doc.get("defendant_name") if packet_doc else "")
            or (bond_doc.get("defendant_name") if bond_doc else "")
            or (intake_doc.get("defendant_name") if intake_doc else "")
            or "Client"
        )
    defendant_name = str(defendant_name or "Client").strip() or "Client"
    return phone, email_addr, defendant_name


async def send_swipesimple_payment_link(
    *,
    packet_id: str = "",
    booking_number: str = "",
    amount: Any = None,
    phone: str = "",
    email: str = "",
    defendant_name: str = "",
    deliver: bool = True,
    deliver_text: Optional[bool] = None,
    deliver_email: Optional[bool] = None,
    packet_doc: Optional[dict] = None,
    bond_doc: Optional[dict] = None,
    intake_doc: Optional[dict] = None,
    source: str = "manual",
) -> Dict[str, Any]:
    """Core send: resolve premium/contacts, dispatch BB + Gmail, stamp packet.

    Never raises for delivery failures — returns structured soft-fail result.
    """
    deliver_text = deliver if deliver_text is None else bool(deliver_text)
    deliver_email = deliver if deliver_email is None else bool(deliver_email)

    ctx = await _load_context(
        packet_id=packet_id,
        booking_number=booking_number,
        packet_doc=packet_doc,
        bond_doc=bond_doc,
        intake_doc=intake_doc,
    )
    packet_id = ctx["packet_id"]
    booking_number = ctx["booking_number"]
    packet_doc = ctx["packet_doc"]
    bond_doc = ctx["bond_doc"]
    intake_doc = ctx["intake_doc"]

    phone, email_addr, defendant_name = _resolve_contacts(
        phone=phone,
        email_addr=email,
        defendant_name=defendant_name,
        packet_doc=packet_doc,
        bond_doc=bond_doc,
        intake_doc=intake_doc,
    )
    amount_f = _resolve_premium(amount, packet_doc, bond_doc, intake_doc)
    swipesimple_url = resolve_swipesimple_url()

    text_delivered = False
    text_queued = False
    text_channel = None
    email_delivered = False
    text_error = None
    email_error = None

    try:
        if deliver_text and phone:
            clean_phone = "".join(ch for ch in phone if ch.isdigit())
            if len(clean_phone) >= 10:
                try:
                    amount_str = f"${amount_f:,.2f}" if amount_f > 0 else "Confirmed Amount"
                    msg = (
                        f"💳 Shamrock Bail Bonds — Payment Request\n"
                        f"Defendant: {defendant_name}\n"
                        f"Case / Booking: {booking_number or 'N/A'}\n"
                        f"Confirmed Premium Amount: {amount_str}\n\n"
                        f"Pay Online via SwipeSimple:\n{swipesimple_url}\n\n"
                        f"Questions? Call or text us 24/7 at (239) 332-2245."
                    )
                    raw = await send_message_universal(phone, msg)
                    send_res = normalize_bb_send_result(raw)
                    text_delivered = bb_send_accepted(send_res)
                    text_queued = bool(send_res.get("queued"))
                    text_channel = send_res.get("channel")
                    if not text_delivered:
                        text_error = send_res.get("error") or "bb_send_failed"
                except Exception as bb_err:
                    text_error = str(bb_err)[:200]
                    logger.warning(
                        "[payment_link] BlueBubbles delivery warning (%s): %s",
                        source,
                        bb_err,
                    )
            else:
                text_error = "invalid_phone"

        if deliver_email and email_addr and "@" in email_addr:
            try:
                from dashboard.services.gmail_reader import GmailReaderService

                gmail_svc = GmailReaderService()
                if gmail_svc.is_configured:
                    amount_str = f"${amount_f:,.2f}" if amount_f > 0 else "Confirmed Amount"
                    subject = f"Shamrock Bail Bonds — Payment Request ({amount_str})"
                    body_text = (
                        f"Shamrock Bail Bonds — Payment Request\n\n"
                        f"Defendant Name: {defendant_name}\n"
                        f"Case / Booking Number: {booking_number or 'N/A'}\n"
                        f"Confirmed Premium Amount: {amount_str}\n\n"
                        f"Please click the link below to securely pay your bond premium "
                        f"online via SwipeSimple:\n"
                        f"{swipesimple_url}\n\n"
                        f"Shamrock Bail Bonds | 1528 Broadway, Ft. Myers, FL 33901\n"
                        f"24/7 Phone / Text: (239) 332-2245\n"
                    )
                    body_html = f"""
                    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 580px; margin: 0 auto; padding: 20px; background-color: #0f172a; color: #f8fafc; border-radius: 12px; border: 1px solid #1e293b;">
                      <div style="text-align: center; padding-bottom: 16px; border-bottom: 1px solid #334155;">
                        <h2 style="color: #10b981; margin: 0; font-size: 22px; letter-spacing: 0.5px;">☘️ SHAMROCK BAIL BONDS</h2>
                        <p style="color: #94a3b8; font-size: 13px; margin-top: 4px;">Fast. Frictionless. Everywhere.</p>
                      </div>
                      <div style="padding: 20px 0;">
                        <h3 style="color: #ffffff; margin-top: 0;">Payment Request — Confirmed Bond Premium</h3>
                        <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5;">
                          Your bond paperwork and premium amount have been verified. You can securely pay online using credit/debit card via SwipeSimple below:
                        </p>
                        <div style="background-color: #1e293b; border-radius: 8px; padding: 16px; margin: 16px 0; border: 1px solid #334155;">
                          <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
                            <tr>
                              <td style="color: #94a3b8; padding: 4px 0;">Defendant:</td>
                              <td style="color: #ffffff; font-weight: 600; text-align: right;">{defendant_name}</td>
                            </tr>
                            <tr>
                              <td style="color: #94a3b8; padding: 4px 0;">Booking / Case #:</td>
                              <td style="color: #ffffff; font-weight: 600; text-align: right;">{booking_number or 'N/A'}</td>
                            </tr>
                            <tr style="border-top: 1px dashed #475569;">
                              <td style="color: #10b981; font-weight: 700; padding: 8px 0 0 0; font-size: 15px;">Confirmed Premium:</td>
                              <td style="color: #10b981; font-weight: 700; text-align: right; padding: 8px 0 0 0; font-size: 18px;">{amount_str}</td>
                            </tr>
                          </table>
                        </div>
                        <div style="text-align: center; margin: 24px 0;">
                          <a href="{swipesimple_url}" target="_blank" style="background-color: #10b981; color: #ffffff; padding: 14px 28px; font-size: 15px; font-weight: 700; text-decoration: none; border-radius: 8px; display: inline-block; box-shadow: 0 4px 12px rgba(16, 185, 129, 0.3);">
                            💳 Pay {amount_str} Online Now
                          </a>
                        </div>
                        <p style="color: #64748b; font-size: 12px; text-align: center;">
                          Direct Link: <a href="{swipesimple_url}" style="color: #38bdf8; word-break: break-all;">{swipesimple_url}</a>
                        </p>
                      </div>
                      <div style="border-top: 1px solid #334155; padding-top: 14px; text-align: center; font-size: 12px; color: #64748b;">
                        <p style="margin: 2px 0;">Shamrock Bail Bonds | 1528 Broadway, Ft. Myers, FL 33901</p>
                        <p style="margin: 2px 0;">24/7 Support: (239) 332-2245 | admin@shamrockbailbonds.biz</p>
                      </div>
                    </div>
                    """
                    mail_res = gmail_svc.send_email(
                        to=email_addr,
                        subject=subject,
                        body_text=body_text,
                        body_html=body_html,
                    )
                    email_delivered = bool(mail_res and mail_res.get("success"))
                    if not email_delivered:
                        email_error = mail_res.get("error") if mail_res else "email_failed"
                else:
                    email_error = "gmail_not_configured"
            except Exception as mail_err:
                email_error = str(mail_err)[:200]
                logger.warning(
                    "[payment_link] Gmail delivery warning (%s): %s", source, mail_err
                )
    except Exception as exc:
        logger.exception("[payment_link] unexpected send error (%s): %s", source, exc)
        return {
            "success": False,
            "skipped": False,
            "error": str(exc)[:300],
            "packet_id": packet_id,
            "booking_number": booking_number,
            "amount": amount_f,
            "payment_link": swipesimple_url,
            "delivered": False,
            "source": source,
        }

    now_iso = datetime.now(timezone.utc).isoformat()
    delivered = text_delivered or email_delivered

    if packet_id and (delivered or deliver):
        try:
            pkts = get_collection("paperwork_packets")
            await pkts.update_one(
                _packet_lookup_filter(packet_id),
                {
                    "$set": {
                        "last_payment_link_sent_at": now_iso,
                        "last_payment_amount": amount_f,
                        "payment_link_delivered_text": text_delivered,
                        "payment_link_delivered_email": email_delivered,
                        "payment_link_text_channel": text_channel,
                        "payment_link_text_queued": text_queued,
                        "payment_link_last_source": source,
                    },
                    "$inc": {"payment_link_sent_count": 1},
                },
            )
        except Exception as db_err:
            logger.warning("[payment_link] Failed to update paperwork_packets: %s", db_err)

    if booking_number and delivered:
        try:
            bonds_col = get_collection("active_bonds")
            await bonds_col.update_one(
                {"booking_number": booking_number},
                {
                    "$set": {
                        "last_payment_link_sent_at": now_iso,
                        "last_payment_amount": amount_f,
                        "payment_link_last_source": source,
                    }
                },
            )
        except Exception as db_err:
            logger.warning("[payment_link] Failed to stamp active_bonds: %s", db_err)

    try:
        disp_col = get_collection("payment_dispatches")
        await disp_col.insert_one(
            {
                "packet_id": packet_id,
                "booking_number": booking_number,
                "amount": amount_f,
                "phone": phone,
                "email": email_addr,
                "swipesimple_url": swipesimple_url,
                "text_delivered": text_delivered,
                "text_queued": text_queued,
                "text_channel": text_channel,
                "text_error": text_error,
                "email_delivered": email_delivered,
                "email_error": email_error,
                "source": source,
                "created_at": now_iso,
            }
        )
    except Exception as log_err:
        logger.warning("[payment_link] Failed to log payment dispatch: %s", log_err)

    return {
        "success": True,
        "skipped": False,
        "packet_id": packet_id,
        "booking_number": booking_number,
        "amount": amount_f,
        "payment_link": swipesimple_url,
        "delivered": delivered,
        "text_delivered": text_delivered,
        "text_queued": text_queued,
        "text_channel": text_channel,
        "text_error": text_error,
        "email_delivered": email_delivered,
        "email_error": email_error,
        "recipient_phone": phone,
        "recipient_email": email_addr,
        "defendant_name": defendant_name,
        "source": source,
    }


async def _claim_send_once(*, packet_id: str, booking_number: str, source: str) -> Dict[str, Any]:
    """
    Atomically claim the one allowed auto-send BEFORE sending.

    Key: the paperwork packet when there is one, else the active bond by
    booking number. Returns {"claimed": True, "collection", "filter", "claim_id"}
    or {"claimed": False, "reason": ...}. Fails closed: no key, no matching
    document, or an existing claim (in any state, including a stale
    "claimed" from a crashed send) → not claimed → caller must not send.
    """
    import uuid

    from pymongo import ReturnDocument

    if packet_id:
        col_name, base = "paperwork_packets", _packet_lookup_filter(packet_id)
    elif booking_number:
        col_name, base = "active_bonds", {"booking_number": booking_number}
    else:
        return {"claimed": False, "reason": "send_once_no_key"}

    col = get_collection(col_name)
    claim_id = uuid.uuid4().hex
    filt = dict(base) if "$or" not in base else {"$and": [base]}
    filt[SEND_ONCE_FIELD] = {"$exists": False}
    doc = await col.find_one_and_update(
        filt,
        {"$set": {SEND_ONCE_FIELD: {
            "state": "claimed",
            "claim_id": claim_id,
            "claimed_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
        }}},
        return_document=ReturnDocument.AFTER,
    )
    if doc and ((doc.get(SEND_ONCE_FIELD) or {}).get("claim_id") == claim_id):
        return {"claimed": True, "collection": col_name, "filter": base, "claim_id": claim_id}
    existing = await col.find_one(base)
    if not existing:
        return {"claimed": False, "reason": "send_once_target_not_found"}
    state = (existing.get(SEND_ONCE_FIELD) or {}).get("state") or "unknown"
    return {"claimed": False, "reason": "already_sent_once", "prior_state": state}


async def _finish_send_once(claim: Dict[str, Any], state: str) -> None:
    try:
        filt = dict(claim["filter"]) if "$or" not in claim["filter"] else {"$and": [claim["filter"]]}
        filt[f"{SEND_ONCE_FIELD}.claim_id"] = claim["claim_id"]
        await get_collection(claim["collection"]).update_one(
            filt,
            {"$set": {
                f"{SEND_ONCE_FIELD}.state": state,
                f"{SEND_ONCE_FIELD}.finished_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
    except Exception as exc:  # claim stays "claimed" → still blocks (fail closed)
        logger.warning("[payment_link] send-once finish stamp failed err_type=%s", type(exc).__name__)


async def maybe_send_packet_payment_link(
    *,
    packet_id: str = "",
    booking_number: str = "",
    amount: Any = None,
    phone: str = "",
    email: str = "",
    defendant_name: str = "",
    packet_doc: Optional[dict] = None,
    bond_doc: Optional[dict] = None,
    intake_doc: Optional[dict] = None,
    force: bool = False,
    source: str = "auto",
) -> Dict[str, Any]:
    """Send-once auto-send for packet finalize / intake promote / DocuSeal completion.

    Gates, in order (each → ``skipped`` with ``reason``; never raises):
      * ``switch_off``            DOCUSEAL_COMPLETION_LEGACY_PAYMENT_LINK is off
                                  (default) — see legacy_payment_link_switch.
      * ``payment_already_collected`` / ``recently_sent`` (bypassed by force)
      * ``premium_unconfirmed``   no staff-confirmed premium (confirmed_premium);
                                  stored/estimated premiums never count and the
                                  caller's ``amount`` is ignored for auto sends.
      * ``no_contact``
      * send-once claim not won   (already sent / claimed / stale → manual review)
    ``force`` does NOT bypass the switch or the premium_unconfirmed rule.
    """
    from dashboard.services.legacy_payment_link_switch import (
        legacy_payment_link_enabled,
    )

    if not legacy_payment_link_enabled():
        logger.info("[payment_link] skip %s reason=switch_off", source)
        return {
            "success": True,
            "skipped": True,
            "reason": "switch_off",
            "packet_id": packet_id,
            "booking_number": booking_number,
            "source": source,
        }
    try:
        ctx = await _load_context(
            packet_id=packet_id,
            booking_number=booking_number,
            packet_doc=packet_doc,
            bond_doc=bond_doc,
            intake_doc=intake_doc,
        )
        packet_id = ctx["packet_id"]
        booking_number = ctx["booking_number"]
        packet_doc = ctx["packet_doc"]
        bond_doc = ctx["bond_doc"]
        intake_doc = ctx["intake_doc"]

        if not force:
            if _is_paid(bond_doc) or _is_paid(packet_doc):
                return {
                    "success": True,
                    "skipped": True,
                    "reason": "payment_already_collected",
                    "packet_id": packet_id,
                    "booking_number": booking_number,
                    "source": source,
                }
            if _recent_send(packet_doc) or _recent_send(bond_doc):
                return {
                    "success": True,
                    "skipped": True,
                    "reason": "recently_sent",
                    "packet_id": packet_id,
                    "booking_number": booking_number,
                    "source": source,
                }

        # AUTO sends use ONLY the staff-confirmed premium. The caller's `amount`
        # and any stored premium (possibly the 10% estimate) are ignored.
        amount_f = confirmed_premium(packet_doc, bond_doc)
        phone_r, email_r, defendant_name = _resolve_contacts(
            phone=phone,
            email_addr=email,
            defendant_name=defendant_name,
            packet_doc=packet_doc,
            bond_doc=bond_doc,
            intake_doc=intake_doc,
        )

        if amount_f <= 0:
            # No amount / PII in this log line — source + reason only.
            logger.info("[payment_link] skip %s reason=premium_unconfirmed", source)
            return {
                "success": True,
                "skipped": True,
                "reason": "premium_unconfirmed",
                "packet_id": packet_id,
                "booking_number": booking_number,
                "source": source,
            }

        if not phone_r and not (email_r and "@" in email_r):
            logger.info(
                "[payment_link] skip %s — no indemnitor contact (packet=%s booking=%s)",
                source,
                packet_id,
                booking_number,
            )
            return {
                "success": True,
                "skipped": True,
                "reason": "no_contact",
                "packet_id": packet_id,
                "booking_number": booking_number,
                "amount": amount_f,
                "source": source,
            }

        # Atomic send-once: claim BEFORE sending. Any failure to claim (already
        # claimed/sent, stale claim, no key, Mongo error) fails closed — no send,
        # no automatic retry; staff can review and use the explicit staff endpoint.
        try:
            claim = await _claim_send_once(
                packet_id=packet_id, booking_number=booking_number, source=source
            )
        except Exception as claim_exc:
            claim = {"claimed": False, "reason": "send_once_claim_error",
                     "error_type": type(claim_exc).__name__}
        if not claim.get("claimed"):
            logger.info(
                "[payment_link] skip %s — send-once not claimed reason=%s prior_state=%s (packet=%s booking=%s)",
                source,
                claim.get("reason"),
                claim.get("prior_state"),
                packet_id,
                booking_number,
            )
            return {
                "success": True,
                "skipped": True,
                "reason": claim.get("reason") or "send_once_not_claimed",
                "manual_review": True,
                "packet_id": packet_id,
                "booking_number": booking_number,
                "source": source,
            }

        try:
            sent = await send_swipesimple_payment_link(
                packet_id=packet_id,
                booking_number=booking_number,
                amount=amount_f,
                phone=phone_r,
                email=email_r,
                defendant_name=defendant_name,
                deliver=True,
                packet_doc=packet_doc,
                bond_doc=bond_doc,
                intake_doc=intake_doc,
                source=source,
            )
        except Exception:
            await _finish_send_once(claim, "send_error_manual_review")
            raise
        await _finish_send_once(
            claim, "sent" if (sent or {}).get("delivered") else "not_delivered_manual_review"
        )
        return sent
    except Exception as exc:
        logger.exception(
            "[payment_link] maybe_send soft-fail (%s): %s", source, exc
        )
        return {
            "success": False,
            "skipped": False,
            "error": str(exc)[:300],
            "packet_id": packet_id,
            "booking_number": booking_number,
            "source": source,
        }
