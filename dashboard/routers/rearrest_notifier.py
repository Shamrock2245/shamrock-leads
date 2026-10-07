from __future__ import annotations

"""
ShamrockLeads — Re-Arrest Notification Engine
==============================================
"The Loyalty Flow" — When a former defendant is re-arrested, automatically
notify their previous indemnitors (family members / co-signers) via iMessage
(BlueBubbles) with a warm, empathetic message.

Business Logic
--------------
1. A check against historical bonds queues a Book Watch review item.
2. Confidence decides the lane: pending_review, or unconfirmed_triage when low.
3. Indemnitor iMessage is not sent from a name match. Staff approve a text
   only when stored confidence is confirmed or high.
4. Triage actor is the signed session. Body actor / reviewed_by fields are ignored.
5. Revoke uses BondStateMachine.transition_bond when alert is a legal next status.

This serves two purposes:
  a) Genuine customer service — the family already knows us and trusts us.
  b) New business — they are the most likely people to bond them out again.

Endpoints
---------
  POST   /api/rearrest/check        — Check a new arrest against historical bonds
  POST   /api/rearrest/notify       — Send re-arrest notification (manual trigger)
  GET    /api/rearrest/history      — View notification history
  GET    /api/rearrest/stats        — Notification stats (sent, converted, etc.)
  GET    /api/rearrest/pending      — Dashboard: fetch unreviewed alerts
  PATCH  /api/rearrest/<id>/dismiss — Dashboard: mark alert as reviewed
  PATCH  /api/rearrest/<id>/contacted — Dashboard: mark indemnitor as contacted
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from dashboard.auth.pin_middleware import get_session_from_request
from dashboard.routers.bb_private_api import BlueBubblesClient
from dashboard.extensions import BB_SERVERS, get_bb_server, get_collection, format_phone
from dashboard.services.book_watch import (
    IDENTITY_CHECK_STATUS,
    INDEMNITOR_TEXT_CONFIDENCE,
    PENDING_REVIEW_STATUS,
    QUEUE_STATUSES,
)

logger = logging.getLogger(__name__)

rearrest_bp = APIRouter(prefix="/api", tags=["rearrest_notifier"])
# ─────────────────────────────────────────────────────────────────────────────
#  Message Templates
# ─────────────────────────────────────────────────────────────────────────────

REARREST_TEMPLATES = {
    "default": (
        "Hi {indemnitor_first_name}! This is Shamrock Bail Bonds 🍀 — we helped "
        "you with {defendant_name}'s bond back in {prior_year}. "
        "We wanted to give you a heads-up as a courtesy: {defendant_first_name} "
        "was just booked into {county} County. "
        "If you'd like us to look into the bond amount and charges, just reply "
        "and we'll get right on it. We're here for you."
    ),
    "same_year": (
        "Hi {indemnitor_first_name}! Shamrock Bail Bonds here 🍀 — we worked "
        "with you on {defendant_name}'s bond earlier this year. "
        "We wanted to let you know right away: {defendant_first_name} was just "
        "booked into {county} County jail. "
        "Reply anytime and we'll pull up the details for you."
    ),
    "no_name": (
        "Hi! This is Shamrock Bail Bonds 🍀 — we helped with a bond for you "
        "previously. We wanted to let you know that {defendant_name} was just "
        "booked into {county} County. "
        "Reply if you'd like us to look into the bond details."
    ),
}


def _build_rearrest_message(indemnitor: dict, defendant_name: str,
                             county: str, prior_bond_date: str) -> str:
    """Build a personalized re-arrest notification message."""
    indemnitor_first = (indemnitor.get("name") or indemnitor.get("first_name") or "").split()[0]
    defendant_parts = defendant_name.split()
    defendant_first = defendant_parts[0] if defendant_parts else defendant_name

    try:
        prior_year = datetime.fromisoformat(prior_bond_date).year
        current_year = datetime.now().year
        same_year = (prior_year == current_year)
    except Exception:
        prior_year = "a previous case"
        same_year = False

    if not indemnitor_first:
        template = REARREST_TEMPLATES["no_name"]
    elif same_year:
        template = REARREST_TEMPLATES["same_year"]
    else:
        template = REARREST_TEMPLATES["default"]

    return template.format(
        indemnitor_first_name=indemnitor_first,
        defendant_name=defendant_name,
        defendant_first_name=defendant_first,
        county=county.replace(" County", "").title(),
        prior_year=prior_year,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Core Logic
# ─────────────────────────────────────────────────────────────────────────────

def session_triage_actor(request: Request) -> Optional[str]:
    """Staff label from the signed session. Request-body actor fields are ignored."""
    sess = get_session_from_request(request) or {}
    if not sess.get("auth"):
        return None
    who = str(sess.get("agent_name") or sess.get("email") or sess.get("role") or "").strip()
    if not who:
        return None
    return f"session:{who}"


class _ActionRequest:
    """Same session cookie as the caller, with a body that cannot carry an actor."""

    def __init__(self, request: Request, payload: dict):
        self.cookies = request.cookies
        self._payload = payload

    async def json(self):
        return self._payload


def _action_request(request: Request, payload: dict) -> _ActionRequest:
    return _ActionRequest(request, payload)


def _session_required():
    return JSONResponse(
        {
            "success": False,
            "error": "session_required",
            "message": "Triage actor comes from the staff session.",
        },
        status_code=401,
    )


async def _audit_triage(audit_col, notification_id: str, action: str, actor: str, notes: str, doc: dict, to_status: str) -> None:
    await audit_col.insert_one({
        "event_type": f"rearrest_triage_{action}",
        "entity_type": "rearrest_notification",
        "entity_id": notification_id,
        "actor": actor,
        "notes": notes or "",
        "confidence": doc.get("confidence"),
        "from_status": doc.get("status"),
        "to_status": to_status,
        "timestamp": datetime.now(timezone.utc),
    })


def _queue_status_for_confidence(confidence: str) -> str:
    if confidence == "low":
        return IDENTITY_CHECK_STATUS
    return PENDING_REVIEW_STATUS


def _evidence_pair(doc: dict) -> dict:
    """Side-by-side arrest vs bond fields for the Book Watch card."""
    return {
        "arrest": {
            "name": doc.get("defendant_name") or "",
            "dob": doc.get("arrest_dob") or doc.get("dob") or "",
            "county": doc.get("county") or "",
            "booking_number": doc.get("booking_number") or "",
            "charges": doc.get("charges") or "",
            "bond_amount": doc.get("bond_amount") or 0,
            "custody_status": doc.get("custody_status") or "",
        },
        "bond": {
            "name": doc.get("prior_defendant_name") or "",
            "dob": doc.get("bond_dob") or doc.get("prior_dob") or "",
            "county": doc.get("prior_county") or "",
            "booking_number": doc.get("prior_booking_number") or "",
            "case_number": doc.get("original_case_number") or doc.get("prior_case_number") or "",
            "poa_number": doc.get("original_poa") or doc.get("prior_poa") or "",
            "bond_amount": doc.get("prior_bond_amount") if doc.get("prior_bond_amount") not in (None, "") else doc.get("original_bond_amount") or 0,
            "status": doc.get("prior_bond_status") or "",
        },
    }


def _lane_for(doc: dict) -> str:
    status = doc.get("status") or ""
    confidence = str(doc.get("confidence") or "").lower()
    if status == IDENTITY_CHECK_STATUS or confidence == "low":
        return "needs_identity_check"
    return "pending_review"


def _serialize_alert(doc: dict) -> dict:
    out = dict(doc)
    if out.get("_id") is not None:
        out["_id"] = str(out["_id"])
    for dt_field in ("created_at", "updated_at", "reviewed_at", "contacted_at", "prior_bond_date", "action_at"):
        val = out.get(dt_field)
        if hasattr(val, "isoformat"):
            out[dt_field] = val.isoformat()
    out["lane"] = _lane_for(out)
    out["evidence"] = _evidence_pair(out)
    return out


async def check_and_notify_rearrest(
    defendant_name: str,
    county: str,
    booking_number: str,
    dob: Optional[str] = None,
    bond_amount: Optional[float] = None,
    charges: Optional[str] = None,
) -> dict:
    """Queue a re-arrest for staff review. Never text an indemnitor from a name match.

    A regex or DOB hit is only a candidate. Confidence scoring decides the lane.
    Indemnitor iMessage stays blocked until a staff session approves a confirmed
    or high match through PATCH /api/rearrest/{id}/action.
    """
    from dashboard.routers.rearrest_detector import evaluate_match_confidence

    bonds_coll = get_collection("active_bonds")
    notifications_coll = get_collection("rearrest_notifications")

    name_parts = defendant_name.upper().split()
    query_name = " ".join(name_parts) if name_parts else defendant_name.upper()

    match_filter = {
        "$or": [
            {"defendant_name": {"$regex": query_name, "$options": "i"}},
        ]
    }
    if dob:
        match_filter["$or"].append({"defendant_dob": dob})

    prior_bonds = await bonds_coll.find(match_filter, {"_id": 0}).to_list(length=20)
    if not prior_bonds:
        legacy_coll = get_collection("bonds")
        prior_bonds = await legacy_coll.find(match_filter, {"_id": 0}).to_list(length=20)

    empty = {
        "prior_bonds_found": 0,
        "notifications_sent": 0,
        "notifications_queued": 0,
        "notifications_failed": 0,
        "fallback_needed": [],
        "auto_text": "blocked_pending_staff_approval",
        "mismatches_skipped": 0,
    }
    if not prior_bonds:
        logger.info("Re-arrest check: no prior bonds (booking %s)", booking_number)
        return empty

    now = datetime.now(timezone.utc)
    queued = 0
    mismatches = 0

    for bond in prior_bonds:
        bond_name = bond.get("defendant_name") or bond.get("full_name") or ""
        bond_dob = bond.get("dob") or bond.get("date_of_birth") or bond.get("defendant_dob") or ""
        confidence, reason = evaluate_match_confidence(
            arrest_name=defendant_name,
            bond_name=bond_name,
            arrest_dob=dob or "",
            bond_dob=bond_dob,
            arrest_county=county,
            bond_county=bond.get("county") or "",
        )
        if confidence == "mismatch":
            mismatches += 1
            continue

        prior_booking = bond.get("booking_number", "")
        existing = await notifications_coll.find_one({
            "booking_number": booking_number,
            "prior_booking_number": prior_booking,
        })
        if existing:
            continue

        indemnitor = bond.get("indemnitor") or {}
        phone = format_phone(
            bond.get("indemnitor_phone") or indemnitor.get("phone", "")
        )
        indemnitor_name = (
            bond.get("indemnitor_name")
            or indemnitor.get("name", "")
            or indemnitor.get("firstName", "")
        )
        await notifications_coll.insert_one({
            "defendant_name": defendant_name,
            "booking_number": booking_number,
            "county": county,
            "bond_amount": bond_amount,
            "charges": charges or "",
            "arrest_dob": dob or "",
            "bond_dob": bond_dob,
            "custody_status": "",
            "indemnitor_phone": phone,
            "indemnitor_name": indemnitor_name,
            "prior_booking_number": prior_booking,
            "prior_bond_amount": bond.get("bond_amount", 0),
            "prior_bond_date": bond.get("created_at", bond.get("bond_date", "")),
            "prior_defendant_name": bond_name,
            "prior_county": bond.get("county", ""),
            "prior_bond_status": bond.get("status", ""),
            "original_case_number": bond.get("case_number", ""),
            "original_poa": bond.get("poa_number", ""),
            "original_bond_amount": bond.get("bond_amount", 0),
            "confidence": confidence,
            "confidence_reason": reason,
            "book_watch_monitored": True,
            "indemnitor_auto_text": "blocked",
            "indemnitor_notify_status": "awaiting_staff_approval",
            "status": _queue_status_for_confidence(confidence),
            "created_at": now,
            "updated_at": now,
        })
        queued += 1

    logger.info(
        "Re-arrest check queued %s review item(s) for booking %s (auto-text blocked)",
        queued,
        booking_number,
    )
    return {
        "prior_bonds_found": len(prior_bonds),
        "notifications_sent": 0,
        "notifications_queued": queued,
        "notifications_failed": 0,
        "fallback_needed": [],
        "auto_text": "blocked_pending_staff_approval",
        "mismatches_skipped": mismatches,
    }


async def _send_staff_approved_indemnitor_text(doc: dict, actor: str) -> dict:
    """Send one indemnitor text after staff approval and a strong identity match."""
    confidence = str(doc.get("confidence") or "").lower()
    if confidence not in INDEMNITOR_TEXT_CONFIDENCE:
        return {
            "ok": False,
            "status_code": 409,
            "error": "indemnitor_text_blocked",
            "message": "Indemnitor text requires confirmed or high confidence and an explicit staff action.",
        }

    phone = format_phone(doc.get("indemnitor_phone") or "")
    if not phone:
        return {
            "ok": False,
            "status_code": 409,
            "error": "indemnitor_phone_missing",
            "message": "No indemnitor phone on this alert.",
        }

    bb_server = next(iter(BB_SERVERS.values()), None) if BB_SERVERS else None
    if not bb_server:
        return {
            "ok": False,
            "status_code": 503,
            "error": "no_bb_client",
            "message": "No BlueBubbles server configured.",
        }

    prior_date = doc.get("prior_bond_date") or ""
    if hasattr(prior_date, "isoformat"):
        prior_date = prior_date.isoformat()
    message = _build_rearrest_message(
        {"name": doc.get("indemnitor_name") or ""},
        doc.get("defendant_name") or "",
        doc.get("county") or "",
        str(prior_date),
    )
    bb_client = BlueBubblesClient(bb_server["url"], bb_server["password"])
    channel = "sms"
    try:
        avail = await bb_client.check_imessage_availability(phone)
        if avail.get("available", False):
            channel = "imessage"
    except Exception:
        pass

    result = await bb_client.send_human_like(f"any;-;{phone}", message, typing_delay=2.5)
    now = datetime.now(timezone.utc)
    notifications_coll = get_collection("rearrest_notifications")
    sent = bool(result.get("success"))
    await notifications_coll.update_one(
        {"_id": doc["_id"]},
        {"$set": {
            "indemnitor_notify_status": "sent" if sent else "failed",
            "indemnitor_text_approved_by": actor,
            "indemnitor_text_approved_at": now,
            "message": message,
            "channel": channel,
            "bb_message_guid": (result.get("data") or {}).get("guid", ""),
            "updated_at": now,
        }},
    )
    if not sent:
        return {
            "ok": False,
            "status_code": 502,
            "error": result.get("error") or "send_failed",
            "message": "BlueBubbles did not accept the indemnitor text.",
        }
    logger.info("Staff-approved indemnitor text sent via %s by %s", channel, actor)
    return {"ok": True, "channel": channel, "message": message}


# ─────────────────────────────────────────────────────────────────────────────
#  API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@rearrest_bp.post("/rearrest/check")
async def api_rearrest_check(request: Request):
    """Check a new arrest against historical bonds and send notifications.

    Body:
        {
            "defendant_name": "JOHN SMITH",
            "county": "Lee",
            "booking_number": "2024-00123",
            "dob": "1985-03-15",          (optional)
            "bond_amount": 5000.00,       (optional)
            "charges": "DUI, BATTERY"     (optional)
        }
    """
    try:
        data = await request.json() or {}
        defendant_name = (data.get("defendant_name") or "").strip()
        county = (data.get("county") or "").strip()
        booking_number = (data.get("booking_number") or "").strip()

        if not defendant_name or not county or not booking_number:
            return JSONResponse(status_code=400, content={
                "success": False,
                "error": "defendant_name, county, and booking_number are required"
            })

        result = await check_and_notify_rearrest(
            defendant_name=defendant_name,
            county=county,
            booking_number=booking_number,
            dob=data.get("dob"),
            bond_amount=data.get("bond_amount"),
            charges=data.get("charges"),
        )

        return {"success": True, **result}

    except Exception as e:
        logger.error("Re-arrest check error: %s", e, exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@rearrest_bp.post("/rearrest/notify")
async def api_rearrest_notify(request: Request):
    """Staff-approved indemnitor text for one queued alert.

    Body:
        {
            "notification_id": "<rearrest_notifications _id>",
            "staff_approved": true
        }

    Confidence is read from the stored alert. A client-supplied phone, confidence,
    or actor cannot authorize a send.
    """
    try:
        actor = session_triage_actor(request)
        if not actor:
            return _session_required()

        data = await request.json() or {}
        if data.get("staff_approved") is not True:
            return JSONResponse(
                {
                    "success": False,
                    "error": "indemnitor_text_blocked",
                    "message": "Set staff_approved true. Name matches never text indemnitors on their own.",
                },
                status_code=409,
            )

        notification_id = str(data.get("notification_id") or "").strip()
        if not notification_id:
            return JSONResponse(
                {"success": False, "error": "notification_id_required"},
                status_code=400,
            )

        try:
            oid = ObjectId(notification_id)
        except Exception:
            return JSONResponse({"success": False, "error": "invalid_notification_id"}, status_code=400)

        notifications_coll = get_collection("rearrest_notifications")
        doc = await notifications_coll.find_one({"_id": oid})
        if not doc:
            return JSONResponse({"success": False, "error": "Notification not found"}, status_code=404)

        sent = await _send_staff_approved_indemnitor_text(doc, actor)
        if not sent.get("ok"):
            return JSONResponse(
                {"success": False, "error": sent.get("error"), "message": sent.get("message")},
                status_code=sent.get("status_code") or 409,
            )
        return {"success": True, "action": "notify_indemnitor", "channel": sent.get("channel"), "actor": actor}

    except Exception as e:
        logger.error("Manual re-arrest notify error: %s", e, exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@rearrest_bp.get("/rearrest/history")
async def api_rearrest_history(defendant_name: str = Query(default=""), county: str = Query(default=""), status: str = Query(default=""), limit: int = Query(default=50)):
    """Get re-arrest notification history with optional filters.
    Query params:
        defendant_name, county, status, limit (default 50)
    """
    try:
        defendant_name = defendant_name
        county = county
        status = status
        limit = int(limit)

        query = {}
        if defendant_name:
            query["defendant_name"] = {"$regex": defendant_name, "$options": "i"}
        if county:
            query["county"] = {"$regex": county, "$options": "i"}
        if status:
            query["status"] = status

        notifications_coll = get_collection("rearrest_notifications")
        cursor = notifications_coll.find(query, {"_id": 0}).sort("sent_at", -1).limit(limit)
        notifications = await cursor.to_list(length=limit)

        return {"success": True, "count": len(notifications), "notifications": notifications}

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@rearrest_bp.get("/rearrest/stats")

async def api_rearrest_stats():
    """Get aggregate statistics for re-arrest notifications."""
    try:
        notifications_coll = get_collection("rearrest_notifications")
        total = await notifications_coll.count_documents({})
        sent = await notifications_coll.count_documents({"status": "sent"})
        failed = await notifications_coll.count_documents({"status": "failed"})
        fallback = await notifications_coll.count_documents({"status": "fallback_needed"})

        return {
            "success": True,
            "stats": {
                "total_notifications": total,
                "sent_via_imessage": sent,
                "failed": failed,
                "fallback_to_sms": fallback,
                "success_rate": round(sent / total * 100, 1) if total > 0 else 0,
            }
        }

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
#  Dashboard Alert Endpoints (consumed by sl-rearrest.js)
# ─────────────────────────────────────────────────────────────────────────────

@rearrest_bp.get("/rearrest/pending")
async def api_rearrest_pending(
    limit: int = Query(default=25),
    include: str = Query(default="pending_review,unconfirmed_triage"),
):
    """Book Watch queue.

    Default include is pending_review plus unconfirmed_triage. Low-confidence
    rows are returned on the needs_identity_check lane. `limit` applies per lane.
    """
    try:
        limit = max(1, min(100, int(limit)))
        requested = [part.strip() for part in (include or "").split(",") if part.strip()]
        statuses = [s for s in requested if s in QUEUE_STATUSES] or list(QUEUE_STATUSES)

        notifications_coll = get_collection("rearrest_notifications")
        pending_review = []
        needs_identity = []
        for status in statuses:
            cursor = notifications_coll.find({"status": status}).sort("created_at", -1).limit(limit)
            async for doc in cursor:
                alert = _serialize_alert(doc)
                if alert["lane"] == "needs_identity_check":
                    needs_identity.append(alert)
                else:
                    pending_review.append(alert)

        alerts = pending_review + needs_identity
        return {
            "success": True,
            "count": len(alerts),
            "alerts": alerts,
            "lanes": {
                "pending_review": pending_review,
                "needs_identity_check": needs_identity,
            },
            "counts": {
                "pending_review": len(pending_review),
                "needs_identity_check": len(needs_identity),
            },
        }

    except Exception as e:
        logger.error("Rearrest pending fetch error: %s", e, exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@rearrest_bp.patch("/rearrest/{notification_id}/dismiss")
async def api_rearrest_dismiss(request: Request, notification_id):
    """Mark a re-arrest alert reviewed. Actor is the session, not reviewed_by."""
    try:
        actor = session_triage_actor(request)
        if not actor:
            return _session_required()
        data = await request.json() or {}
        return await api_rearrest_action(
            _action_request(request, {"action": "dismiss", "notes": data.get("notes") or ""}),
            notification_id,
        )
    except Exception as e:
        logger.error("Rearrest dismiss error: %s", e, exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@rearrest_bp.patch("/rearrest/{notification_id}/contacted")
async def api_rearrest_contacted(request: Request, notification_id):
    """Mark indemnitor contacted. Actor is the session, not contacted_by."""
    try:
        actor = session_triage_actor(request)
        if not actor:
            return _session_required()
        data = await request.json() or {}
        return await api_rearrest_action(
            _action_request(request, {"action": "contacted", "notes": data.get("notes") or ""}),
            notification_id,
        )
    except Exception as e:
        logger.error("Rearrest contacted error: %s", e, exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@rearrest_bp.patch("/rearrest/{notification_id}/action")
async def api_rearrest_action(request: Request, notification_id: str):
    """
    Execute comprehensive triage action on a re-arrest alert.

    Supported actions:
      - 'revoke' / 'surrender': Flag active bond for surrender/revocation; log audit event.
      - 'second_bond': Mark as new bonding opportunity; return prefilled intake link.
      - 'false_positive': Dismiss as false positive with reason.
      - 'contacted': Record indemnitor contact note.
      - 'dismiss': Standard review dismissal.
    """
    try:
        data = await request.json() or {}
        action = str(data.get("action") or "").lower().strip()
        actor = session_triage_actor(request)
        if not actor:
            return _session_required()
        notes = data.get("notes") or ""

        notifications_coll = get_collection("rearrest_notifications")
        bonds_col = get_collection("active_bonds")
        audit_col = get_collection("audit_events")

        try:
            oid = ObjectId(notification_id)
        except Exception:
            return JSONResponse({"success": False, "error": "invalid_notification_id"}, status_code=400)

        doc = await notifications_coll.find_one({"_id": oid})
        if not doc:
            return JSONResponse({"success": False, "error": "Notification not found"}, status_code=404)

        now = datetime.now(timezone.utc)
        booking = doc.get("booking_number", "")
        prior_bk = doc.get("prior_booking_number", "")
        defendant_name = doc.get("defendant_name", "")
        county = doc.get("county", "")

        if action in ("revoke", "surrender"):
            reason = f"New arrest on booking {booking} ({county}): {notes}".strip()
            transition = None
            transition_note = None
            bond_status_unchanged = False
            if prior_bk:
                from dashboard.services.state_machine import BondStateMachine

                bond = await bonds_col.find_one({"booking_number": prior_bk})
                current = (bond or {}).get("status") or ""
                allowed = BondStateMachine.VALID_TRANSITIONS.get(current, [])
                if bond and (current == "alert" or "alert" in allowed):
                    try:
                        transition = await BondStateMachine.transition_bond(
                            prior_bk,
                            "alert",
                            actor,
                            reason=reason,
                        )
                    except Exception as exc:
                        logger.error("Rearrest revoke transition failed for %s: %s", prior_bk, exc)
                        return JSONResponse(
                            {
                                "success": False,
                                "error": "bond_transition_failed",
                                "message": "Bond status was not changed. Retry the revoke action.",
                            },
                            status_code=409,
                        )
                else:
                    bond_status_unchanged = True
                    transition_note = (
                        "bond_not_found" if not bond else f"invalid_transition:{current}->alert"
                    )
                if bond:
                    await bonds_col.update_one(
                        {"booking_number": prior_bk},
                        {"$set": {
                            "bond_revocation_flag": True,
                            "revocation_initiated_at": now.isoformat(),
                            "revocation_reason": reason,
                        }},
                    )
            await notifications_coll.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "status": "revocation_initiated",
                    "action_taken": "revoke",
                    "action_by": actor,
                    "action_at": now,
                    "action_notes": notes,
                    "updated_at": now,
                }}
            )
            await audit_col.insert_one({
                "event_type": "bond_revocation_initiated_rearrest",
                "entity_type": "rearrest_notification",
                "entity_id": notification_id,
                "prior_booking_number": prior_bk,
                "new_booking_number": booking,
                "new_county": county,
                "actor": actor,
                "notes": notes,
                "bond_status_unchanged": bond_status_unchanged,
                "transition_note": transition_note,
                "timestamp": now,
            })
            logger.info("Bond revocation initiated for prior booking %s by %s", prior_bk, actor)
            return {
                "success": True,
                "action": "revoke",
                "status": "revocation_initiated",
                "prior_booking_number": prior_bk,
                "actor": actor,
                "bond_transition": transition,
                "bond_status_unchanged": bond_status_unchanged,
                "transition_note": transition_note,
            }

        elif action in ("second_bond", "bond_second"):
            await notifications_coll.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "status": "second_bond_opportunity",
                    "action_taken": "second_bond",
                    "action_by": actor,
                    "action_at": now,
                    "updated_at": now,
                }}
            )
            intake_url = f"/api/portal?booking={booking}&county={county}&defendant={defendant_name}"
            await _audit_triage(audit_col, notification_id, "second_bond", actor, notes, doc, "second_bond_opportunity")
            return {
                "success": True,
                "action": "second_bond",
                "status": "second_bond_opportunity",
                "new_booking_number": booking,
                "county": county,
                "intake_url": intake_url,
                "actor": actor,
            }

        elif action in ("false_positive", "mismatch"):
            await notifications_coll.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "status": "false_positive",
                    "action_taken": "false_positive",
                    "reviewed_by": actor,
                    "reviewed_at": now,
                    "false_positive_reason": notes,
                    "updated_at": now,
                }}
            )
            # Remove rearrest flag from bond if no other active alerts
            if prior_bk:
                await bonds_col.update_one(
                    {"booking_number": prior_bk},
                    {"$set": {"rearrest_detected": False}}
                )
            await audit_col.insert_one({
                "event_type": "rearrest_triage_false_positive",
                "entity_type": "rearrest_notification",
                "entity_id": notification_id,
                "prior_booking_number": prior_bk,
                "actor": actor,
                "notes": notes,
                "confidence": doc.get("confidence"),
                "timestamp": now,
            })
            logger.info("Dismissed rearrest alert %s as false positive", notification_id)
            return {"success": True, "action": "false_positive", "status": "false_positive", "actor": actor}

        elif action == "contacted":
            await notifications_coll.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "status": "contacted",
                    "contacted_by": actor,
                    "contacted_at": now,
                    "contact_notes": notes,
                    "updated_at": now,
                }}
            )
            await _audit_triage(audit_col, notification_id, "contacted", actor, notes, doc, "contacted")
            return {"success": True, "action": "contacted", "status": "contacted", "actor": actor}

        elif action in ("dismiss", "reviewed"):
            await notifications_coll.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "status": "reviewed",
                    "reviewed_by": actor,
                    "reviewed_at": now,
                    "updated_at": now,
                }}
            )
            await _audit_triage(audit_col, notification_id, "dismiss", actor, notes, doc, "reviewed")
            return {"success": True, "action": "dismiss", "status": "reviewed", "actor": actor}

        elif action == "notify_indemnitor":
            sent = await _send_staff_approved_indemnitor_text(doc, actor)
            if not sent.get("ok"):
                return JSONResponse(
                    {"success": False, "error": sent.get("error"), "message": sent.get("message")},
                    status_code=sent.get("status_code") or 409,
                )
            await _audit_triage(
                audit_col, notification_id, "notify_indemnitor", actor, notes, doc, doc.get("status") or ""
            )
            return {
                "success": True,
                "action": "notify_indemnitor",
                "status": doc.get("status"),
                "actor": actor,
                "channel": sent.get("channel"),
            }

        else:
            return JSONResponse(
                {
                    "success": False,
                    "error": (
                        f"Unknown action: {action}. Must be revoke, second_bond, "
                        "false_positive, contacted, dismiss, or notify_indemnitor."
                    ),
                },
                status_code=400
            )

    except Exception as e:
        logger.error("Rearrest action error: %s", e, exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

