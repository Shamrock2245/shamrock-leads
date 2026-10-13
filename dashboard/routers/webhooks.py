from __future__ import annotations

"""
ShamrockLeads — Webhooks API Blueprint
Handles inbound webhooks from retired e-sign callbacks, Twilio, SwipeSimple, and DocuSeal.

Uses extensions.get_collection() to avoid circular imports from app.py.

Security:
  - Retired e-sign callbacks: explicitly rejected without reading payload data.
  - SwipeSimple: HMAC-SHA256 signature verification (SWIPESIMPLE_WEBHOOK_SECRET).
  - Twilio: Twilio request validator (TWILIO_AUTH_TOKEN).

Data Flow (DocuSeal submission.completed): validated by the active DocuSeal webhook handler.
"""

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import JSONResponse
import hmac
import hashlib
import logging
import os
from datetime import datetime, timezone

from dashboard.extensions import get_collection

webhooks_bp = APIRouter(prefix="/api", tags=["webhooks"])
logger = logging.getLogger(__name__)

GMAIL_PUBSUB_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


def _env_is_production() -> bool:
    """True when ENV or ENVIRONMENT is production/prod (repo convention)."""
    env = (os.getenv("ENV") or os.getenv("ENVIRONMENT") or "").lower()
    return env in ("production", "prod")


_TERMINAL_PAYMENT_EVENTS = {
    "payment.refunded": "refunded",
    "payment.failed": "failed",
}
_TERMINAL_PAYMENT_STATUSES = {"refunded", "failed", "declined", "voided", "void"}


def _terminal_payment_status(event_type: str, status: str) -> str | None:
    """Status a later SwipeSimple lifecycle event must store, or None.

    ``payment.completed`` / ``approved`` is not a later lifecycle. A refund,
    failure, decline, or void is. ``void`` is stored as ``voided``.
    """
    event = str(event_type or "").strip().lower()
    raw = str(status or "").strip().lower()
    if raw in _TERMINAL_PAYMENT_STATUSES:
        return "voided" if raw == "void" else raw
    return _TERMINAL_PAYMENT_EVENTS.get(event)


async def _payment_rows_for_txn(payments, transaction_id: str):
    """Rows for this transaction id, or None when the read failed.

    More than 20 rows is too many to update safely; the caller fails closed.
    """
    try:
        count = await payments.count_documents({"transaction_id": transaction_id})
    except Exception:
        return None
    if count == 0 or count > 20:
        return [] if count == 0 else None
    try:
        cursor = payments.find({"transaction_id": transaction_id})
        if hasattr(cursor, "to_list"):
            rows = await cursor.to_list(length=20)
        else:
            rows = []
            async for doc in cursor:
                rows.append(doc)
    except Exception:
        return None
    if len(rows) != count:
        return None
    return rows


def _row_reflects_lifecycle(row: dict, event_type: str, new_status: str) -> bool:
    raw = row.get("raw_payload") if isinstance(row.get("raw_payload"), dict) else {}
    stored_event = str(raw.get("event_type") or row.get("lifecycle_event") or "").strip().lower()
    stored_status = str(row.get("status") or "").strip().lower()
    return stored_event == str(event_type or "").strip().lower() and stored_status == new_status


def _association_pin(row: dict) -> dict:
    """Match the plan association observed on this read, including an absent one."""
    plan_id = str(row.get("plan_id") or "").strip()
    if plan_id:
        return {"plan_id": plan_id}
    return {"$or": [
        {"plan_id": {"$exists": False}},
        {"plan_id": None},
        {"plan_id": ""},
    ]}


def _exact_field_pin(row: dict, key: str) -> dict:
    if key not in row:
        return {key: {"$exists": False}}
    return {key: row.get(key)}


async def _store_lifecycle_status(payments, row: dict, event_type: str, new_status: str, payload: dict, now: datetime):
    """Write the lifecycle status while the observed plan association still holds.

    Returns the row whose association was pinned. A concurrent ``/pay`` that
    links the row makes the first update miss; the second attempt pins the
    plan id from a fresh read so the caller can reverse that plan. None when
    the write cannot be confirmed.
    """
    current = row
    for _attempt in range(2):
        status_now = str(current.get("status") or "").strip().lower()
        fields = {
            "status": new_status,
            "raw_payload": payload,
            "lifecycle_event": event_type,
            "lifecycle_updated_at": now.isoformat(),
        }
        if status_now != new_status:
            fields["lifecycle_status_before"] = current.get("status")
        try:
            res = await payments.update_one(
                {"_id": current["_id"], **_association_pin(current)},
                {"$set": fields},
            )
        except Exception as exc:
            logger.warning(
                "[payment_webhook] lifecycle row update failed err=%s", type(exc).__name__,
            )
            return None
        if getattr(res, "modified_count", 0) or getattr(res, "matched_count", 0):
            return current
        try:
            fresh = await payments.find_one({"_id": current["_id"]})
        except Exception as exc:
            logger.warning(
                "[payment_webhook] lifecycle reread failed err=%s", type(exc).__name__,
            )
            return None
        if not isinstance(fresh, dict):
            return None
        current = fresh
    return None


async def _ensure_initial_payment_audit(transaction_id: str, status: str, payload: dict, now: datetime) -> bool:
    """Write the first-insert audit when the row exists and the audit does not.

    A provider retry must not be acknowledged while this record is missing.
    False when the read or the insert failed.
    """
    event_type = f"payment.{status}"
    audit_events = get_collection("audit_events")
    try:
        existing = await audit_events.find_one({
            "source": "swipesimple_webhook",
            "event_type": event_type,
            "$or": [
                {"transaction_id": transaction_id},
                {"payload.transaction_id": transaction_id},
            ],
        })
    except Exception as exc:
        logger.warning("[payment_webhook] initial audit read failed err=%s", type(exc).__name__)
        return False
    if existing is not None:
        return True
    try:
        await audit_events.insert_one({
            "source": "swipesimple_webhook",
            "event_type": event_type,
            "payload": payload,
            "timestamp": now.isoformat(),
            "transaction_id": transaction_id,
            "actor": "swipesimple_webhook",
            "agent_name": "payment_webhook",
            "reason": "SwipeSimple payment webhook",
            "confidence": 1.0,
        })
    except Exception as exc:
        logger.warning("[payment_webhook] initial audit failed err=%s", type(exc).__name__)
        return False
    return True


def _webhook_ownership(booking_number: str, custom_fields) -> dict | None:
    """Complete booking identity from a completed webhook, when it sends one.

    None when the webhook carries no booking, county, or state. ``conflict``
    when those fields disagree with each other. A partial identity is returned
    so the caller can refuse a conflicting booking without inventing a place.
    """
    from dashboard.routers.payment_plans import _booking_token
    from dashboard.services.swipesimple_receipt_poller import jurisdiction_from_bond

    cf = custom_fields if isinstance(custom_fields, dict) else {}
    booking = _booking_token(booking_number or cf.get("booking_number"))
    county = cf.get("county") if str(cf.get("county") or "").strip() else cf.get("County")
    state = cf.get("state") if str(cf.get("state") or "").strip() else cf.get("State")
    has_place = bool(str(county or "").strip() or str(state or "").strip())
    parsed = jurisdiction_from_bond({"county": county, "state": state}) if has_place else None
    if has_place and not parsed:
        return {"conflict": True}
    if parsed and booking:
        return {
            "booking_number": booking,
            "county": parsed["county"],
            "state": parsed["state"],
        }
    if booking or parsed:
        return {
            "partial": True,
            "booking_number": booking,
            "county": (parsed or {}).get("county") or "",
            "state": (parsed or {}).get("state") or "",
        }
    return None


def _row_ownership_conflict(row: dict, incoming: dict) -> bool:
    """True when the row already names a different booking, county, or state."""
    from dashboard.routers.payment_plans import _booking_token, _identity_sources, _place_pair

    target_place = (incoming.get("county") or "").casefold(), (incoming.get("state") or "").casefold()
    for src in _identity_sources(row, nested=True):
        if "booking_number" in src:
            booking = _booking_token(src.get("booking_number"))
            if booking and incoming.get("booking_number") and booking != incoming["booking_number"]:
                return True
            if booking and incoming.get("partial") and incoming.get("booking_number") and booking != incoming["booking_number"]:
                return True
        pair = _place_pair(src)
        if pair is None:
            return True
        if pair != ("", "") and incoming.get("county") and pair != target_place:
            return True
    return False


def _row_has_ownership(row: dict, incoming: dict) -> bool:
    booking = str(row.get("booking_number") or "").strip()
    county = str(row.get("county") or "").strip()
    state = str(row.get("state") or "").strip()
    return (
        booking == incoming["booking_number"]
        and county.casefold() == incoming["county"].casefold()
        and state.casefold() == incoming["state"].casefold()
    )


async def _finish_ownership_audit(row: dict, transaction_id: str, now: datetime) -> bool:
    """Save the enrichment audit stashed on the row, then clear that stash.

    False when the audit was not saved. The stash keeps the before-image when
    the previous attempt updated the row and lost the audit insert.
    """
    pending = row.get("ownership_enrichment")
    if not isinstance(pending, dict):
        return True
    audit_events = get_collection("audit_events")
    payment_id = str(row.get("_id"))
    try:
        existing = await audit_events.find_one({
            "source": "swipesimple_webhook",
            "event_type": "payment.ownership_enriched",
            "transaction_id": transaction_id,
            "payment_id": payment_id,
        })
    except Exception as exc:
        logger.warning("[payment_webhook] enrichment audit read failed err=%s", type(exc).__name__)
        return False
    if existing is None:
        try:
            await audit_events.insert_one({
                "source": "swipesimple_webhook",
                "event_type": "payment.ownership_enriched",
                "actor": "swipesimple_webhook",
                "agent_name": "payment_webhook",
                "timestamp": now.isoformat(),
                "transaction_id": transaction_id,
                "payment_id": payment_id,
                "booking_before": pending.get("booking_before") or "",
                "booking_after": pending.get("booking_after") or "",
                "county_before": pending.get("county_before") or "",
                "county_after": pending.get("county_after") or "",
                "state_before": pending.get("state_before") or "",
                "state_after": pending.get("state_after") or "",
                "reason": "completed webhook supplied booking, county, and state for an existing transaction",
                "confidence": 1.0,
            })
        except Exception as exc:
            logger.warning("[payment_webhook] enrichment audit failed err=%s", type(exc).__name__)
            return False
    payments = get_collection("payments")
    try:
        await payments.update_one({"_id": row["_id"]}, {"$unset": {"ownership_enrichment": ""}})
    except Exception as exc:
        logger.warning("[payment_webhook] enrichment stash clear failed err=%s", type(exc).__name__)
        return False
    return True


def _completed_replay_refusal(rows, amount, booking_number: str, custom_fields, transaction_id: str):
    """409 when a duplicate completed event must not be audited.

    None when the amount matches and the ownership does not conflict. A
    partial identity is not a refusal; enrichment decides whether to fill it.
    """
    from dashboard.routers.payment_plans import _same_amount

    if not all(_same_amount(row.get("amount"), amount) for row in rows):
        return JSONResponse(
            {"error": "payment_lifecycle_amount_mismatch", "transaction_id": transaction_id},
            status_code=409,
        )
    incoming = _webhook_ownership(booking_number, custom_fields)
    if incoming and (
        incoming.get("conflict") or any(_row_ownership_conflict(row, incoming) for row in rows)
    ):
        return JSONResponse(
            {"error": "logged_payment_jurisdiction_unverified", "transaction_id": transaction_id},
            status_code=409,
        )
    return None


async def _enrich_completed_payment(
    payments,
    rows: list,
    *,
    transaction_id: str,
    booking_number: str,
    custom_fields,
    amount,
    now: datetime,
):
    """Copy a completed webhook's booking identity onto the claimed row.

    Returns a JSONResponse when the amount or the ownership conflicts, or when
    the write or its audit fails. Returns None when the row needed nothing.
    Returns ``enriched`` when the identity was written and audited.
    """
    from dashboard.routers.payment_plans import _same_amount

    for row in rows:
        if not await _finish_ownership_audit(row, transaction_id, now):
            return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
    incoming = _webhook_ownership(booking_number, custom_fields)
    if incoming is None:
        return None
    if not all(_same_amount(row.get("amount"), amount) for row in rows):
        return JSONResponse(
            {"error": "payment_lifecycle_amount_mismatch", "transaction_id": transaction_id},
            status_code=409,
        )
    if incoming.get("conflict") or any(_row_ownership_conflict(row, incoming) for row in rows):
        return JSONResponse(
            {"error": "logged_payment_jurisdiction_unverified", "transaction_id": transaction_id},
            status_code=409,
        )
    if incoming.get("partial"):
        return None
    enriched = False
    for row in rows:
        if _row_has_ownership(row, incoming):
            continue
        cf = dict(row.get("custom_fields")) if isinstance(row.get("custom_fields"), dict) else {}
        cf["booking_number"] = incoming["booking_number"]
        cf["county"] = incoming["county"]
        cf["state"] = incoming["state"]
        raw = dict(row.get("raw_payload")) if isinstance(row.get("raw_payload"), dict) else {}
        raw_cf = dict(raw.get("custom_fields")) if isinstance(raw.get("custom_fields"), dict) else {}
        raw_cf["booking_number"] = incoming["booking_number"]
        raw_cf["county"] = incoming["county"]
        raw_cf["state"] = incoming["state"]
        raw["custom_fields"] = raw_cf
        if not raw.get("event_type"):
            raw["event_type"] = "payment.completed"
        fields = {
            "booking_number": incoming["booking_number"],
            "county": incoming["county"],
            "state": incoming["state"],
            "custom_fields": cf,
            "raw_payload": raw,
            "ownership_enrichment": {
                "booking_before": row.get("booking_number") or "",
                "county_before": row.get("county") or "",
                "state_before": row.get("state") or "",
                "booking_after": incoming["booking_number"],
                "county_after": incoming["county"],
                "state_after": incoming["state"],
            },
        }
        pin = {
            "_id": row["_id"],
            **_exact_field_pin(row, "booking_number"),
            **_exact_field_pin(row, "county"),
            **_exact_field_pin(row, "state"),
        }
        try:
            res = await payments.update_one(pin, {"$set": fields})
        except Exception as exc:
            logger.warning("[payment_webhook] ownership enrich failed err=%s", type(exc).__name__)
            return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
        if not (getattr(res, "modified_count", 0) or getattr(res, "matched_count", 0)):
            try:
                fresh = await payments.find_one({"_id": row["_id"]})
            except Exception:
                fresh = None
            if not isinstance(fresh, dict):
                return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
            if _row_ownership_conflict(fresh, incoming):
                return JSONResponse(
                    {"error": "logged_payment_jurisdiction_unverified", "transaction_id": transaction_id},
                    status_code=409,
                )
            if not _row_has_ownership(fresh, incoming):
                return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
            row = fresh
        else:
            row = {**row, **fields}
        if not await _finish_ownership_audit(row, transaction_id, now):
            return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
        enriched = True
    if not enriched:
        return None
    return JSONResponse(
        status_code=200,
        content={"success": True, "duplicate": True, "enriched": True},
    )


_REVERSAL_FIELDS = (
    "balance_remaining_before",
    "balance_remaining_after",
    "total_paid_before",
    "total_paid_after",
    "payments_made_before",
    "payments_made_after",
    "plan_status_before",
    "plan_status_after",
)


def _snapshot_path(transaction_id: str, event_type: str) -> str:
    """One stored field per transaction and event, not one field for the plan.

    The digest is the Mongo key so a transaction id that contains ``.`` or
    ``$`` cannot land in another transaction's snapshot. The snapshot body
    still carries the transaction id and event type.
    """
    digest = hashlib.sha256(f"{transaction_id}\n{event_type}".encode()).hexdigest()[:20]
    return f"reversal_snapshots.{digest}"


def _snapshot_complete(snap) -> bool:
    return isinstance(snap, dict) and all(key in snap for key in _REVERSAL_FIELDS)


def _reversal_snapshot_for(plan, transaction_id: str, event_type: str):
    """The before/after values stored for this transaction, or None when absent.

    A retry that no longer sees the transaction in ``applied_transaction_ids``
    still has to audit the reversal that already happened. Snapshots are keyed
    by transaction so a later reversal on the same plan does not replace this
    one. A single ``reversal_snapshot`` from an older write is still read.
    """
    if not isinstance(plan, dict):
        return None
    snaps = plan.get("reversal_snapshots")
    if isinstance(snaps, dict):
        for snap in snaps.values():
            if (
                isinstance(snap, dict)
                and snap.get("transaction_id") == transaction_id
                and snap.get("event_type") == event_type
                and _snapshot_complete(snap)
            ):
                return snap
    snap = plan.get("reversal_snapshot")
    if not isinstance(snap, dict):
        return None
    if snap.get("transaction_id") != transaction_id or snap.get("event_type") != event_type:
        return None
    if not _snapshot_complete(snap):
        return None
    return snap


def _build_reversal_snapshot(plan: dict, transaction_id: str, event_type: str, restored: float, balance_now: float) -> dict:
    paid_now = float(plan["total_paid"])
    made_now = int(plan["payments_made"])
    status_before = plan.get("status")
    status_after = status_before
    if status_before == "paid_in_full" and balance_now + restored > 0:
        status_after = "active"
    return {
        "transaction_id": transaction_id,
        "event_type": event_type,
        "balance_remaining_before": balance_now,
        "balance_remaining_after": balance_now + restored,
        "total_paid_before": paid_now,
        "total_paid_after": paid_now - restored,
        "payments_made_before": made_now,
        "payments_made_after": made_now - 1,
        "plan_status_before": status_before,
        "plan_status_after": status_after,
    }


async def _invalidate_applied_plan_claim(transaction_id: str, plan_id: str | None, event_type: str, now: datetime) -> bool:
    """Move an applied or in-flight plan claim to ``reversed``.

    True when no ``applied`` or ``claimed`` claim remains. False when the
    write did not confirm, so the caller does not report success. ``/pay``
    treats ``applied`` as an idempotent success and ``claimed`` as a claim it
    may still finish. A refund that arrives after the payment row is linked
    and before the plan balance moves must block that claim. A retry still
    flips the claim after the balance was already restored.
    """
    claims = get_collection("payment_plan_payment_claims")
    try:
        claim = await claims.find_one({"transaction_id": transaction_id})
    except Exception as exc:
        logger.warning("[payment_webhook] plan claim read failed err=%s", type(exc).__name__)
        return False
    if not isinstance(claim, dict):
        return True
    claim_status = claim.get("status")
    if claim_status in ("reversed", "failed"):
        return True
    if claim_status not in ("applied", "claimed"):
        logger.warning("[payment_webhook] plan claim status is not reversible")
        return False
    claim_plan = str(claim.get("plan_id") or "").strip()
    if plan_id and claim_plan and claim_plan != plan_id:
        logger.warning("[payment_webhook] plan claim belongs to a different plan")
        return False
    for _ in range(2):
        try:
            await claims.update_one(
                {"transaction_id": transaction_id, "status": {"$in": ["applied", "claimed"]}},
                {"$set": {
                    "status": "reversed",
                    "reversed_at": now.isoformat(),
                    "reversed_event": event_type,
                }},
            )
        except Exception as exc:
            logger.warning("[payment_webhook] plan claim reversal failed err=%s", type(exc).__name__)
        try:
            fresh = await claims.find_one({"transaction_id": transaction_id})
        except Exception as exc:
            logger.warning("[payment_webhook] plan claim readback failed err=%s", type(exc).__name__)
            fresh = None
        # A failed read is not proof the claim moved. Only a confirmed
        # claim that /pay can no longer finish lets the lifecycle succeed.
        if isinstance(fresh, dict) and fresh.get("status") not in ("applied", "claimed"):
            return True
        if fresh is None:
            try:
                still = await claims.count_documents({"transaction_id": transaction_id})
            except Exception:
                still = None
            if still == 0:
                return True
    return False


async def _reverse_plan_balance(plans, plan, plan_id, transaction_id, event_type, amount, now):
    """Restore the plan when this transaction is still applied.

    Returns ``(reversed, snapshot, error)``. ``error`` is a response the
    caller returns. The snapshot is stored under this transaction's own key.
    """
    applied = list(plan.get("applied_transaction_ids") or [])
    if transaction_id not in applied:
        return False, None, None
    if any(key not in plan for key in ("balance_remaining", "total_paid", "payments_made")):
        return False, None, JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
    try:
        restored = float(amount)
        balance_now = float(plan["balance_remaining"])
        snapshot = _build_reversal_snapshot(
            plan, transaction_id, event_type, restored, balance_now,
        )
    except (TypeError, ValueError):
        return False, None, JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
    set_fields = {
        _snapshot_path(transaction_id, event_type): snapshot,
        "updated_at": now.isoformat(),
    }
    if snapshot["plan_status_after"] != snapshot["plan_status_before"]:
        set_fields["status"] = snapshot["plan_status_after"]
    reversal = {
        "$inc": {
            "balance_remaining": restored,
            "total_paid": -restored,
            "payments_made": -1,
        },
        "$pull": {"applied_transaction_ids": transaction_id},
        "$set": set_fields,
    }
    try:
        res = await plans.update_one(
            {"plan_id": plan_id, "applied_transaction_ids": transaction_id},
            reversal,
        )
    except Exception as exc:
        logger.warning("[payment_webhook] plan reversal failed err=%s", type(exc).__name__)
        return False, None, JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
    moved = bool(getattr(res, "modified_count", 0) or getattr(res, "matched_count", 0))
    if moved:
        return True, snapshot, None
    try:
        fresh = await plans.find_one({"plan_id": plan_id})
    except Exception:
        fresh = None
    still_applied = (
        not isinstance(fresh, dict)
        or transaction_id in list(fresh.get("applied_transaction_ids") or [])
    )
    if still_applied:
        return False, None, JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
    stored = _reversal_snapshot_for(fresh, transaction_id, event_type)
    if stored is None:
        return False, None, JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
    return True, stored, None


async def _apply_later_payment_lifecycle(
    payments,
    *,
    transaction_id: str,
    event_type: str,
    new_status: str,
    amount,
    payload: dict,
    now: datetime,
):
    """Apply a refund or failure to the payment row that already exists.

    The log claim blocks a second insert. That must not drop the later event.
    A linked ``completed`` row is updated so revenue stops counting it, and a
    plan that still lists this transaction has its balance restored. Success
    is returned only after that update and its audit are saved. A replay of
    the same event is ``duplicate`` and does not send another receipt.
    """
    from dashboard.routers.payment_plans import _same_amount

    rows = await _payment_rows_for_txn(payments, transaction_id)
    if not rows:
        logger.warning(
            "[payment_webhook] later lifecycle has no payment row txn_set=%s",
            bool(transaction_id),
        )
        return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
    if not all(_same_amount(row.get("amount"), amount) for row in rows):
        return JSONResponse(
            {"error": "payment_lifecycle_amount_mismatch", "transaction_id": transaction_id},
            status_code=409,
        )

    already = all(_row_reflects_lifecycle(row, event_type, new_status) for row in rows)
    status_before = []
    pinned_rows = []
    if already:
        pinned_rows = list(rows)
        status_before = [row.get("lifecycle_status_before") for row in rows]
    else:
        for row in rows:
            stored = await _store_lifecycle_status(
                payments, row, event_type, new_status, payload, now,
            )
            if stored is None:
                return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
            pinned_rows.append(stored)
            status_before.append(stored.get("status"))

    plan_ids: list[str] = []
    for row in pinned_rows:
        plan_id = str(row.get("plan_id") or "").strip()
        if plan_id and plan_id not in plan_ids:
            plan_ids.append(plan_id)
    if len(plan_ids) > 1:
        logger.warning("[payment_webhook] later lifecycle spans multiple plans txn_set=%s", bool(transaction_id))
        return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)

    plans = get_collection("payment_plans")
    plan = None
    needs_reversal = False
    if plan_ids:
        try:
            plan = await plans.find_one({"plan_id": plan_ids[0]})
        except Exception as exc:
            logger.warning(
                "[payment_webhook] plan read failed for lifecycle err=%s", type(exc).__name__,
            )
            return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
        if not isinstance(plan, dict):
            return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
        applied = list(plan.get("applied_transaction_ids") or [])
        needs_reversal = transaction_id in applied
        if needs_reversal and any(
            key not in plan for key in ("balance_remaining", "total_paid", "payments_made")
        ):
            return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)

    audit_events = get_collection("audit_events")
    try:
        existing_audit = await audit_events.find_one({
            "source": "swipesimple_webhook",
            "transaction_id": transaction_id,
            "event_type": event_type,
        })
    except Exception as exc:
        logger.warning("[payment_webhook] lifecycle audit read failed err=%s", type(exc).__name__)
        return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)

    balance_reversed = False
    reversal_snapshot = None
    if isinstance(plan, dict) and plan_ids:
        did_reverse, snap, reverse_error = await _reverse_plan_balance(
            plans, plan, plan_ids[0], transaction_id, event_type, rows[0].get("amount"), now,
        )
        if reverse_error is not None:
            return reverse_error
        if did_reverse:
            balance_reversed = True
            reversal_snapshot = snap
        else:
            stored = _reversal_snapshot_for(plan, transaction_id, event_type)
            if stored is not None:
                balance_reversed = True
                reversal_snapshot = stored

    if balance_reversed and reversal_snapshot is None:
        return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)

    # The balance restore and the claim are separate writes. A retry whose
    # transaction is already pulled still has to leave the claim reversed.
    # An in-flight ``claimed`` claim is blocked here too: /pay must not apply
    # a payment this event has already marked terminal.
    linked_plan = plan_ids[0] if plan_ids else None
    if not await _invalidate_applied_plan_claim(transaction_id, linked_plan, event_type, now):
        return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)

    # /pay can add the transaction after the first plan read and before this
    # claim block. Re-read once and reverse that late balance move.
    if plan_ids and not balance_reversed:
        try:
            fresh_plan = await plans.find_one({"plan_id": plan_ids[0]})
        except Exception as exc:
            logger.warning(
                "[payment_webhook] plan reread failed for lifecycle err=%s", type(exc).__name__,
            )
            return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
        if isinstance(fresh_plan, dict):
            did_reverse, snap, reverse_error = await _reverse_plan_balance(
                plans, fresh_plan, plan_ids[0], transaction_id, event_type, rows[0].get("amount"), now,
            )
            if reverse_error is not None:
                return reverse_error
            if did_reverse:
                balance_reversed = True
                reversal_snapshot = snap
                needs_reversal = True

    if already and not needs_reversal and existing_audit is not None:
        return JSONResponse(status_code=200, content={"success": True, "duplicate": True})

    if existing_audit is None:
        # No phone, name, or address. The payment row keeps the provider payload.
        audit = {
            "source": "swipesimple_webhook",
            "event_type": event_type,
            "actor": "swipesimple_webhook",
            "agent_name": "payment_webhook",
            "timestamp": now.isoformat(),
            "transaction_id": transaction_id,
            "payment_ids": [str(row.get("_id")) for row in rows],
            "status_before": status_before[0] if len(status_before) == 1 else status_before,
            "status_after": new_status,
            "plan_id": plan_ids[0] if plan_ids else None,
            "amount": rows[0].get("amount"),
            "balance_reversed": balance_reversed,
            "reason": "later SwipeSimple lifecycle event for an existing transaction",
            "confidence": 1.0,
        }
        if reversal_snapshot is not None:
            for key in _REVERSAL_FIELDS:
                audit[key] = reversal_snapshot[key]
        try:
            await audit_events.insert_one(audit)
        except Exception as exc:
            logger.warning("[payment_webhook] lifecycle audit failed err=%s", type(exc).__name__)
            return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)

    return JSONResponse(
        status_code=200,
        content={"success": True, "lifecycle_updated": True, "status": new_status},
    )


def _payment_secret_unset_allowed() -> bool:
    """An unset SWIPESIMPLE_WEBHOOK_SECRET is tolerated only in explicit dev.

    Same precedence as the no-PIN gate (#176, ``no_pin_passthrough_allowed``):
    ``ENV``, or ``ENVIRONMENT`` when ``ENV`` is unset/blank, and
    ``REQUIRE_DASHBOARD_PIN`` closes it. Stricter on the value: it must be
    exactly ``development`` (case-sensitive, no trimming). Production, prod,
    staging, test, ``dev``, ``Development``, blank/unset ENV and typos all
    fail closed (503, before the body is read).
    """
    if os.getenv("REQUIRE_DASHBOARD_PIN", "").strip().lower() in ("1", "true", "yes"):
        return False
    env = os.getenv("ENV") or os.getenv("ENVIRONMENT") or ""
    return env == "development"


def verify_gmail_pubsub_token(token: str, audience: str) -> dict:
    """Verify a Google-signed Pub/Sub push OIDC token."""
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import id_token

    claims = id_token.verify_oauth2_token(token, GoogleAuthRequest(), audience=audience)
    if claims.get("iss") not in GMAIL_PUBSUB_ISSUERS:
        raise ValueError("Unexpected token issuer")
    return claims


def _twilio_form_params(form_data) -> dict[str, str]:
    """Flatten Starlette form into Twilio-style string params (last value wins)."""
    params: dict[str, str] = {}
    for key, value in form_data.multi_items():
        params[str(key)] = "" if value is None else str(value)
    return params


def _twilio_candidate_urls(request: Request) -> list[str]:
    """URLs Twilio may have signed (exact console URL first, then proxy reconstructions)."""
    candidates: list[str] = []
    configured = (os.getenv("TWILIO_WEBHOOK_PUBLIC_URL") or "").strip()
    if configured:
        candidates.append(configured.rstrip("/"))
        candidates.append(configured if configured.endswith("/") else configured + "/")

    proto = (
        (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip()
        or request.url.scheme
        or "https"
    )
    host = (
        (request.headers.get("X-Forwarded-Host") or "").split(",")[0].strip()
        or (request.headers.get("Host") or "").strip()
        or request.url.netloc
    )
    path = request.url.path or "/api/webhooks/twilio"
    query = request.url.query

    if host:
        base = f"{proto}://{host}{path}"
        candidates.append(base)
        if query:
            candidates.append(f"{base}?{query}")
        # Trailing-slash variants (Twilio docs: try with/without)
        if path.endswith("/"):
            trimmed = f"{proto}://{host}{path.rstrip('/')}"
            candidates.append(trimmed)
            if query:
                candidates.append(f"{trimmed}?{query}")
        else:
            with_slash = f"{proto}://{host}{path}/"
            candidates.append(with_slash)
            if query:
                candidates.append(f"{with_slash}?{query}")

    # Final fallback: request.url as seen by the app
    try:
        raw = str(request.url)
        candidates.append(raw.rstrip("/"))
        candidates.append(raw if raw.endswith("/") else raw + "/")
    except Exception:
        pass

    # Deduplicate preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for url in candidates:
        if url and url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def verify_twilio_signature(
    auth_token: str,
    signature: str,
    urls: list[str],
    params: dict[str, str],
) -> bool:
    """
    Validate X-Twilio-Signature (HMAC-SHA1 over URL + sorted form params, Base64).

    No twilio SDK dependency — same algorithm as twilio.request_validator.RequestValidator.
    """
    import base64

    if not auth_token or not signature:
        return False
    provided = signature.strip()
    sorted_items = sorted((str(k), str(v)) for k, v in params.items())
    param_suffix = "".join(k + v for k, v in sorted_items)

    for url in urls:
        data = (url + param_suffix).encode("utf-8")
        digest = hmac.new(auth_token.encode("utf-8"), data, hashlib.sha1).digest()
        expected = base64.b64encode(digest).decode("utf-8")
        if hmac.compare_digest(expected, provided):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Security helpers


@webhooks_bp.post("/webhooks/twilio")
async def twilio_webhook(request: Request):
    """Handle inbound SMS from Twilio (signature required in production)."""
    from dashboard.routers.events import publish_event
    from starlette.responses import Response as StarletteResponse

    auth_token = (os.getenv("TWILIO_AUTH_TOKEN") or "").strip()
    is_prod = _env_is_production()

    if not auth_token:
        if is_prod:
            logger.error("[twilio_webhook] TWILIO_AUTH_TOKEN not configured — rejecting")
            return JSONResponse(
                {"error": "Webhook authentication not configured"},
                status_code=503,
            )
        logger.warning(
            "[twilio_webhook] TWILIO_AUTH_TOKEN unset — allowing in non-production only"
        )

    # Twilio sends application/x-www-form-urlencoded
    form_data = await request.form()
    params = _twilio_form_params(form_data)

    if auth_token:
        signature = (
            request.headers.get("X-Twilio-Signature")
            or request.headers.get("x-twilio-signature")
            or ""
        )
        urls = _twilio_candidate_urls(request)
        if not verify_twilio_signature(auth_token, signature, urls, params):
            logger.warning("[twilio_webhook] Invalid or missing X-Twilio-Signature — rejecting")
            return JSONResponse({"error": "Forbidden"}, status_code=403)

    audit_events = get_collection("audit_events")
    audit_doc = {
        "source": "twilio_webhook",
        "event_type": "inbound_sms",
        "payload": params,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await audit_events.insert_one(audit_doc)

    await publish_event("sms_received", {
        "from": params.get("From"),
        "body": params.get("Body"),
    })

    return StarletteResponse(
        content="<Response></Response>",
        status_code=200,
        media_type="text/xml",
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /webhooks/payment
# ─────────────────────────────────────────────────────────────────────────────

@webhooks_bp.post("/webhooks/payment")
async def payment_webhook(request: Request, booking_number: str = Query(default="")):
    """
    Handle SwipeSimple payment confirmation webhook.

    SwipeSimple sends a POST with JSON payload on payment events.
    Expected fields (SwipeSimple standard webhook schema):
        event_type:       "payment.completed" | "payment.failed" | "payment.refunded"
        transaction_id:   unique SwipeSimple transaction ID
        amount:           payment amount in dollars (float)
        status:           "approved" | "declined" | "refunded"
        card_last4:       last 4 digits of card
        card_brand:       "Visa" | "Mastercard" etc.
        customer_name:    cardholder name
        custom_fields:    { booking_number, county, indemnitor_name, indemnitor_phone }
        created_at:       ISO timestamp

    On success:
      1. Validate HMAC signature (SWIPESIMPLE_WEBHOOK_SECRET set); unset → 503
         unless ENV is exactly ``development``
      2. Parse booking_number from custom_fields or query params
      3. Update bond case payment status in active_bonds / prospective_bonds
      4. Log to payments collection
      5. Send BlueBubbles receipt to indemnitor
      6. Fire Slack alert
      7. Publish SSE event
      8. Log audit event
    """
    import httpx
    from dashboard.routers.events import publish_event
    from dashboard.services.bb_client import send_message_universal

    now = datetime.now(timezone.utc)

    # -- 1. HMAC signature validation / Auth check ---------------------------
    # Production = ENV or ENVIRONMENT (VPS uses ENV=production per .env.example).
    webhook_secret = (os.getenv("SWIPESIMPLE_WEBHOOK_SECRET") or "").strip()
    if webhook_secret:
        raw_body = await request.body()
        sig_header = request.headers.get("X-SwipeSimple-Signature", "")
        if not sig_header:
            logger.warning("[payment_webhook] Missing SwipeSimple signature — rejecting")
            return JSONResponse({"error": "Missing signature header"}, status_code=401)
        expected_sig = hmac.new(
            webhook_secret.encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_sig, sig_header):
            logger.warning("[payment_webhook] Invalid SwipeSimple signature — rejecting")
            return JSONResponse({"error": "Invalid signature"}, status_code=401)
    elif not _payment_secret_unset_allowed():
        logger.warning(
            "[payment_webhook] Rejected — SWIPESIMPLE_WEBHOOK_SECRET not configured "
            "(receipt poller remains source of truth if SwipeSimple never sends webhooks)"
        )
        return JSONResponse({"error": "Webhook secret not configured"}, status_code=503)
    else:
        logger.warning(
            "[payment_webhook] SWIPESIMPLE_WEBHOOK_SECRET unset — allowing in ENV=development only"
        )

    # -- 2. Payload validation (fail closed on empty or invalid payload) -------
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid or empty JSON body"}, status_code=400)

    if not data or not isinstance(data, dict):
        return JSONResponse({"error": "Empty payload"}, status_code=400)

    event_type = data.get("event_type")
    transaction_id = data.get("transaction_id")
    if not event_type or not transaction_id:
        return JSONResponse(
            {"error": "Missing required fields: event_type and transaction_id"},
            status_code=400,
        )

    # -- 3. Parse booking number -----------------------------------------------
    custom_fields = data.get("custom_fields", {})
    booking_number = (
        custom_fields.get("booking_number")
        or booking_number
    )

    # -- 3. Update bond case ---------------------------------------------------
    amount = data.get("amount", 0)
    status = data.get("status", "")
    transaction_id = data.get("transaction_id", "")
    card_last4 = data.get("card_last4", "")
    card_brand = data.get("card_brand", "")
    customer_name = data.get("customer_name", "")
    indemnitor_phone = custom_fields.get("indemnitor_phone", "")

    payment_update = {
        "last_payment_amount": amount,
        "last_payment_at": now.isoformat(),
        "last_payment_status": status,
        "last_transaction_id": transaction_id,
    }

    if booking_number:
        bond_cases = get_collection("bond_cases")
        await bond_cases.update_one(
            {"booking_number": booking_number},
            {"$set": payment_update},
        )
        # Also try active_bonds for legacy records
        active_bonds = get_collection("active_bonds")
        await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": payment_update},
        )

    # -- 4. Log to payments collection ----------------------------------------
    # payment_log_claims (unique on that collection only) is the atomic gate.
    # A second writer for this transaction id does not insert another row.
    # A later refund or failure still updates that row and writes an audit.
    # A replay of the same event does not send another receipt or Slack
    # message. payments is not indexed.
    payments = get_collection("payments")
    log_claims = get_collection("payment_log_claims")
    try:
        from dashboard.services.payment_txn_dedupe import insert_payment_once

        inserted = await insert_payment_once(payments, {
            "transaction_id": transaction_id,
            "booking_number": booking_number,
            "amount": amount,
            "status": status,
            "card_last4": card_last4,
            "card_brand": card_brand,
            "customer_name": customer_name,
            "indemnitor_phone": indemnitor_phone,
            "custom_fields": custom_fields,
            "raw_payload": data,
            "created_at": now.isoformat(),
        }, claims=log_claims, source="payment_webhook")
    except Exception as exc:
        logger.warning(
            "[payment_webhook] payment dedupe unavailable err=%s", type(exc).__name__,
        )
        return JSONResponse({"error": "payment_dedupe_unavailable"}, status_code=503)
    if not inserted:
        later_status = _terminal_payment_status(event_type, status)
        if later_status is None:
            rows = await _payment_rows_for_txn(payments, transaction_id)
            if not rows:
                logger.warning(
                    "[payment_webhook] duplicate completed event has no payment row txn_set=%s",
                    bool(transaction_id),
                )
                return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
            # Amount and ownership are checked before the initial audit.
            # A rejected replay must not leave payment.{status} holding the
            # mismatched payload: a later correct retry would keep that audit.
            refused = _completed_replay_refusal(
                rows, amount, booking_number or "", custom_fields, transaction_id,
            )
            if refused is not None:
                return refused
            if not await _ensure_initial_payment_audit(transaction_id, status, data, now):
                return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)
            enriched = await _enrich_completed_payment(
                payments,
                rows,
                transaction_id=transaction_id,
                booking_number=booking_number or "",
                custom_fields=custom_fields,
                amount=amount,
                now=now,
            )
            if enriched is not None:
                return enriched
            return JSONResponse(status_code=200, content={"success": True, "duplicate": True})
        return await _apply_later_payment_lifecycle(
            payments,
            transaction_id=transaction_id,
            event_type=str(event_type),
            new_status=later_status,
            amount=amount,
            payload=data,
            now=now,
        )

    # -- 5. Send BlueBubbles receipt ------------------------------------------
    if indemnitor_phone and status == "approved":
        try:
            receipt_msg = (
                f"✅ Payment received! Thank you, {customer_name}.\n"
                f"Amount: ${amount:.2f} ({card_brand} ending {card_last4})\n"
                f"Transaction: {transaction_id}\n"
                f"Shamrock Bail Bonds — (239) 332-2245"
            )
            await send_message_universal(indemnitor_phone, receipt_msg)
        except Exception as exc:
            logger.warning("[payment_webhook] BB receipt failed: %s", exc)

    # -- 6. Slack alert -------------------------------------------------------
    # Payment received → #intake (bond operations / intake channel)
    slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL") or os.getenv("SLACK_WEBHOOK_LEADS", "")
    if slack_webhook_url and status == "approved":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(slack_webhook_url, json={
                    "text": (
                        f":moneybag: *Payment Received* — ${amount:.2f} from {customer_name} "
                        f"({card_brand} ****{card_last4}) | Booking: {booking_number or 'N/A'}"
                    )
                })
        except Exception as exc:
            logger.warning("[payment_webhook] Slack alert failed: %s", exc)

    # -- 7. Publish SSE event -------------------------------------------------
    await publish_event('payment_received', {
        "transaction_id": transaction_id,
        "booking_number": booking_number,
        "amount": amount,
        "status": status,
        "customer_name": customer_name,
    })

    # -- 8. Log audit event ---------------------------------------------------
    # A retry must be able to recreate this record. Success waits on the insert.
    if not await _ensure_initial_payment_audit(transaction_id, status, data, now):
        return JSONResponse({"error": "payment_lifecycle_update_failed"}, status_code=503)

    return JSONResponse(status_code=200, content={"success": True})


# ─────────────────────────────────────────────────────────────────────────────
# POST /webhooks/wix-intake
# ─────────────────────────────────────────────────────────────────────────────

@webhooks_bp.post("/webhooks/wix-intake")
async def wix_intake_webhook(request: Request, api_key: str = Query(default="")):
    """
    Website (Wix) applications → CRM. MongoDB ``intake_queue`` is the source
    of truth.

    Caller: shamrock-bail-portal-site ``backend/leadsIntake.jsw`` (server-side,
    header ``X-Wix-Webhook-Secret`` from Wix Secrets Manager).

    Flow
      1. Auth (fail closed): WIX_WEBHOOK_SECRET (GAS_API_KEY fallback).
      2. Nested wizard payload (defendant / indemnitor forms) → flat keys via
         ``services/wix_wizard_adapter.py``; legacy flat payloads pass through.
      3. Save to ``intake_queue`` with role + form_type. No surety / county /
         state defaults for website intakes (staff choose at Write Bond).
      4. Idempotent on the client nonce (same nonce → same intake, no re-fan-out).
      5. Auto-match (same engine as /api/intake/submit).
      6. AFTER the save: non-blocking fan-out to Google Sheets ledger + Slack
         (``services/intake_fanout.py``); failures are logged + retried and
         never fail this request.

    Response: ``{success: true, intake_id, duplicate, payment_link}`` (201/200)
    — the wizard shows success ONLY when ``success`` is true.
    """
    import hmac

    from dashboard.routers.intake import _normalize_intake
    from dashboard.services import wix_wizard_adapter as wiz

    # Auth check — fail closed if no secret configured
    wix_secret = os.getenv("WIX_WEBHOOK_SECRET", "") or os.getenv("GAS_API_KEY", "")
    provided = (
        request.headers.get("X-Wix-Webhook-Secret", "")
        or request.headers.get("X-Api-Key", "")
        or api_key
    )
    if not wix_secret:
        logger.error("[wix_intake_webhook] WIX_WEBHOOK_SECRET/GAS_API_KEY not configured")
        return JSONResponse({"success": False, "error": "Webhook auth not configured"}, status_code=503)
    if not hmac.compare_digest(str(provided).encode(), str(wix_secret).encode()):
        logger.warning("[wix_intake_webhook] Unauthorized — invalid secret")
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid or empty JSON body"}, status_code=400)

    if not data or not isinstance(data, dict):
        return JSONResponse({"success": False, "error": "Empty or invalid JSON body"}, status_code=400)

    now_iso = datetime.now(timezone.utc).isoformat()

    # 2. Adapt nested wizard payloads
    extra: dict = {}
    if wiz.is_wizard_payload(data):
        flat, meta = wiz.flatten(data)
        extra = {
            "form_type": meta["form_type"],
            "submitted_by_role": meta["role"],
            "application": meta["application"],
            "client_nonce": meta["nonce"] or None,
            "landing": meta["landing"] or None,
            "intake_source": "website",
        }
        source_detail = f"wix_{meta['role']}_wizard"
    else:
        flat = data
        extra = {"intake_source": "website"}
        source_detail = "wix_legacy_flat"
    extra["source_detail"] = source_detail

    # Audit copy is redacted (no SSN, no uploads).
    try:
        await get_collection("audit_events").insert_one({
            "source": "wix_intake_webhook",
            "event_type": "intake_submission",
            "source_detail": source_detail,
            "payload": wiz.redact(data),
            "timestamp": now_iso,
        })
    except Exception as exc:
        logger.warning("[wix_intake_webhook] audit insert failed (non-fatal): %s", exc)

    # 4. Idempotency on the client nonce
    intake_queue = get_collection("intake_queue")
    nonce_id = flat.get("intakeId") if isinstance(flat, dict) else None
    if nonce_id and extra.get("client_nonce"):
        try:
            existing = await intake_queue.find_one({"intake_id": nonce_id}, {"_id": 0, "intake_id": 1})
        except Exception:
            existing = None
        if existing:
            from dashboard.services.payment_links import payment_link_for

            return JSONResponse(status_code=200, content={
                "success": True, "intake_id": nonce_id, "duplicate": True,
                "payment_link": payment_link_for("website"),
            })

    # 3. Save (source of truth)
    try:
        intake_id, intake_doc = await _normalize_intake(flat, source="wix_webhook", extra=extra)
    except Exception as exc:
        logger.exception("[wix_intake_webhook] Intake save failed")
        return JSONResponse({"success": False, "error": "Intake could not be saved"}, status_code=500)
    logger.info("[wix_intake_webhook] Intake %s saved (%s)", intake_id, source_detail)

    # 5. Auto-match (non-fatal)
    match_result = None
    try:
        from dashboard.extensions import get_db
        from dashboard.services.matching_engine import MatchingEngine

        match_result = await MatchingEngine(get_db()).match_intake(intake_doc)
    except Exception as match_err:
        logger.warning("[wix_intake_webhook] Auto-match failed for %s: %s", intake_id, match_err)

    # Real-time dashboard event — sl-core.js listens for 'new_intake'
    try:
        from dashboard.routers.events import publish_event
        await publish_event("new_intake", {
            "intake_id": intake_id,
            "defendant_name": (intake_doc or {}).get("defendant_name", ""),
            "county": (intake_doc or {}).get("defendant_county", ""),
            "booking_number": (intake_doc or {}).get("defendant_booking_number", ""),
            "source": "wix_webhook",
            "form_type": extra.get("form_type", ""),
        })
    except Exception:
        pass

    # 6. Fan-out AFTER the save — fire-and-forget, never fails the intake.
    try:
        from dashboard.services.intake_fanout import intake_for_fanout, schedule_after_save

        fanout_doc = await intake_for_fanout(intake_queue, intake_id, intake_doc, match_result)
        schedule_after_save(fanout_doc)
    except Exception as exc:
        logger.error("[wix_intake_webhook] fan-out scheduling failed (non-fatal): %s", exc)

    from dashboard.services.payment_links import payment_link_for

    return JSONResponse(status_code=201, content={
        "success": True,
        "intake_id": intake_id,
        "duplicate": False,
        "form_type": extra.get("form_type"),
        "matched": bool(isinstance(match_result, dict) and match_result.get("auto_linked")),
        "payment_link": payment_link_for("website"),
    })


# ─────────────────────────────────────────────────────────────────────────────
# POST /webhooks/scraper-event
# ─────────────────────────────────────────────────────────────────────────────

@webhooks_bp.post("/webhooks/scraper-event")
async def scraper_event_webhook(request: Request, api_key: str = Query(default="")):
    """
    Handle live events (e.g. new arrests) from scraper containers.
    
    Validates GAS_API_KEY and publishes to SSE stream for dashboard popups.
    """
    from dashboard.routers.events import publish_event
    
    # Auth check — fail closed if no key configured
    expected_key = os.getenv("GAS_API_KEY", "")
    provided = request.headers.get("X-Api-Key", "") or api_key
    if not expected_key:
        logger.error("[scraper_event_webhook] GAS_API_KEY not configured")
        return JSONResponse({"error": "Webhook auth not configured"}, status_code=503)
    if provided != expected_key:
        logger.warning("[scraper_event_webhook] Unauthorized — invalid secret")
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
        
    data = await request.json() or {}
    event_type = data.get("event_type", "new_arrest")
    payload = data.get("payload", {})
    
    # Publish to SSE connected clients
    await publish_event(event_type, payload)
    logger.info(f"[scraper_event_webhook] Published {event_type} event to SSE")
    
    return JSONResponse(status_code=200, content={"success": True})


# ─────────────────────────────────────────────────────────────────────────────
# POST /webhooks/gmail
# ─────────────────────────────────────────────────────────────────────────────

@webhooks_bp.post("/webhooks/gmail")
async def gmail_pubsub_webhook(request: Request):
    """
    Handle Google Cloud Pub/Sub push notifications for inbound court emails.

    Base64 decodes message.data, extracts historyId/messageId, and triggers
    instant real-time parsing, Google Calendar sync, Slack alerts, and client SMS.
    """
    import base64
    import json

    audience = os.getenv("GMAIL_PUBSUB_AUDIENCE", "").strip()
    expected_service_account = os.getenv("GMAIL_PUBSUB_SERVICE_ACCOUNT_EMAIL", "").strip().lower()
    expected_subscription = os.getenv("GMAIL_PUBSUB_SUBSCRIPTION", "").strip()
    monitored_mailbox = os.getenv("GMAIL_MONITORED_MAILBOX", "").strip().lower()
    if not all((audience, expected_service_account, expected_subscription, monitored_mailbox)):
        logger.error("[gmail_webhook] Required Pub/Sub authentication configuration is missing")
        return JSONResponse({"error": "Webhook authentication not configured"}, status_code=503)

    authorization = request.headers.get("Authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        logger.warning("[gmail_webhook] Rejected push with missing bearer token")
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        claims = verify_gmail_pubsub_token(token.strip(), audience)
    except Exception:
        logger.warning("[gmail_webhook] Rejected push with invalid identity token")
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    token_email = str(claims.get("email", "")).strip().lower()
    if token_email != expected_service_account or claims.get("email_verified") is not True:
        logger.warning("[gmail_webhook] Rejected push from unexpected service account")
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    try:
        data = await request.json() or {}
        if data.get("subscription") != expected_subscription:
            logger.warning("[gmail_webhook] Rejected push for unexpected subscription")
            return JSONResponse({"error": "Forbidden"}, status_code=403)
        pubsub_message = data.get("message", {})
        if not pubsub_message:
            return JSONResponse({"error": "Invalid Pub/Sub payload"}, status_code=400)

        # Base64 decode Pub/Sub data payload
        raw_data = pubsub_message.get("data", "")
        decoded_bytes = base64.b64decode(raw_data) if raw_data else b"{}"
        payload = json.loads(decoded_bytes.decode("utf-8"))

        email_address = payload.get("emailAddress")
        if not isinstance(email_address, str) or email_address.strip().lower() != monitored_mailbox:
            logger.warning("[gmail_webhook] Rejected push for unexpected monitored mailbox")
            return JSONResponse({"error": "Forbidden"}, status_code=403)
        history_id = payload.get("historyId")
        message_id = payload.get("messageId") or pubsub_message.get("messageId")

        logger.info("[gmail_webhook] Accepted authenticated Pub/Sub notification")

        # Log event to audit_events
        audit_events = get_collection("audit_events")
        await audit_events.insert_one({
            "source": "gmail_pubsub_webhook",
            "event_type": "court_email_notification",
            "history_id": history_id,
            "message_id": message_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Trigger real-time processing
        db = get_collection("court_email_log").database
        from dashboard.services.court_email_scheduler import CourtEmailScheduler
        scheduler = CourtEmailScheduler(db=db)

        if message_id:
            res = scheduler.process_single_message(message_id)
        else:
            res = scheduler.process_all()

        # Publish SSE event for live dashboard sessions
        from dashboard.routers.events import publish_event
        await publish_event("court_email_processed", {
            "result": res,
        })

        return JSONResponse(status_code=200, content={"success": True, "result": res})

    except Exception as exc:
        logger.exception("[gmail_webhook] Failed processing Google Pub/Sub push notification")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)



# ─────────────────────────────────────────────────────────────────────────────
# Adobe PDF Services — job completion CALLBACK webhook
# Docs: https://developer.adobe.com/document-services/docs/overview/pdf-services-api/howtos/webhook-notification
#
# Client must respond HTTP 200 with body: {"ack": "done"}
# After 50 errors in 10 minutes Adobe blocks webhooks for 20 minutes.
# ─────────────────────────────────────────────────────────────────────────────

def _verify_adobe_pdf_webhook(request: Request) -> bool:
    """
    Validate shared secret Adobe echoes back in CALLBACK headers.

    We register notifiers with:
      headers: { "x-shamrock-adobe-webhook-secret": ADOBE_PDF_WEBHOOK_SECRET }

    Fail-closed if secret is configured but missing/mismatched.
    If secret is unset: allow only when DEBUG=true (dev), else reject.
    """
    secret = (os.getenv("ADOBE_PDF_WEBHOOK_SECRET") or "").strip()
    provided = (
        request.headers.get("x-shamrock-adobe-webhook-secret")
        or request.headers.get("X-Shamrock-Adobe-Webhook-Secret")
        or ""
    ).strip()
    if not secret:
        if os.getenv("DEBUG", "false").lower() == "true":
            logger.warning("[adobe_pdf_webhook] ADOBE_PDF_WEBHOOK_SECRET unset — allowing in DEBUG")
            return True
        logger.error("[adobe_pdf_webhook] ADOBE_PDF_WEBHOOK_SECRET unset — rejecting")
        return False
    if not provided:
        logger.warning("[adobe_pdf_webhook] missing secret header — rejecting")
        return False
    return hmac.compare_digest(secret, provided)


@webhooks_bp.post("/webhooks/adobe-pdf-services")
async def adobe_pdf_services_webhook(request: Request):
    """
    Receive Adobe PDF Services job completion CALLBACK.

    Success payload:
      { "jobID": "...", "statusResponse": { "status": "done", "asset": { downloadUri, assetID, metadata } } }

    Failure payload:
      { "jobID": "...", "statusResponse": { "status": "failed", "error": { code, message, status } } }

    Must return HTTP 200 + {"ack": "done"} or Adobe treats it as error.
    """
    try:
        if not _verify_adobe_pdf_webhook(request):
            # Still return 200 ack? Adobe docs say non-200 counts as error and can ban webhooks.
            # We intentionally return 401 so misconfigured clients are visible, but use sparingly.
            # Prefer 200 with ack only when we processed; unauthorized should be rare after setup.
            return JSONResponse(status_code=401, content={"ack": "rejected", "error": "unauthorized"})

        raw = await request.body()
        try:
            payload = await request.json()
        except Exception:
            payload = {}
            try:
                import json as _json
                payload = _json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                payload = {"_raw": (raw[:500].decode("utf-8", errors="replace") if raw else "")}

        job_id = payload.get("jobID") or payload.get("jobId") or ""
        status_response = payload.get("statusResponse") or {}
        status = (status_response.get("status") or "").lower()
        now = datetime.now(timezone.utc)

        # Persist for ops / async consumers
        doc = {
            "job_id": job_id,
            "status": status or "unknown",
            "status_response": status_response,
            "received_at": now,
            "source": "adobe_pdf_services_callback",
        }
        try:
            await get_collection("adobe_pdf_jobs").update_one(
                {"job_id": job_id or f"unknown-{now.timestamp()}"},
                {"$set": doc, "$setOnInsert": {"created_at": now}},
                upsert=True,
            )
        except Exception as db_exc:
            logger.warning("[adobe_pdf_webhook] persist failed: %s", db_exc)

        # Soft audit (no PII in asset URLs ideally — still avoid logging full downloadUri at INFO)
        try:
            await get_collection("audit_events").insert_one({
                "Event_ID": f"adobe-pdf-{job_id or 'na'}-{int(now.timestamp())}",
                "event_type": "adobe_pdf_job_callback",
                "job_id": job_id,
                "status": status,
                "timestamp": now,
                "actor": "adobe_pdf_services",
            })
        except Exception:
            pass

        if status == "done":
            logger.info("[adobe_pdf_webhook] job %s done", job_id)
        elif status == "failed":
            err = status_response.get("error") or {}
            logger.warning(
                "[adobe_pdf_webhook] job %s failed code=%s msg=%s",
                job_id, err.get("code"), (err.get("message") or "")[:200],
            )
        else:
            logger.info("[adobe_pdf_webhook] job %s status=%s", job_id, status)

        # Required ack per Adobe docs
        return JSONResponse(status_code=200, content={"ack": "done"})

    except Exception as exc:
        logger.exception("[adobe_pdf_webhook] handler error")
        # Return 200 ack when possible to avoid Adobe temporary ban after 50 errors / 10 min
        return JSONResponse(status_code=200, content={"ack": "done", "handler_error": str(exc)[:120]})


# ─────────────────────────────────────────────────────────────────────────────
# DocuSeal webhooks (self-hosted e-sign)
# ─────────────────────────────────────────────────────────────────────────────

def verify_docuseal_signature(payload: bytes, signature: str) -> bool:
    """
    Verify DocuSeal webhook HMAC (self-hosted / OSS).

    DocuSeal (lib/webhook_urls/signatures.rb) sends header X-Docuseal-Signature as:
        "{unix_ts}.{hex}" where hex = HMAC-SHA256(secret, "{unix_ts}.{raw_body}")
    Secret is the auto-generated hmac_secret from DocuSeal SECURITY → HMAC
    (typically prefixed whsec_…). Custom SECURITY "Secret" headers are unrelated.

    Fail-closed unless DEBUG=true:
      - missing DOCUSEAL_WEBHOOK_SECRET → reject (except DEBUG)
      - missing/invalid signature → reject
      - timestamp outside ±5 minutes → reject (DocuSeal TOLERANCE)
    """
    import time

    secret = os.getenv("DOCUSEAL_WEBHOOK_SECRET", "").strip()
    debug = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")
    env = (os.getenv("ENV") or os.getenv("ENVIRONMENT") or "").lower()
    is_prod = env in ("production", "prod") or os.getenv("REQUIRE_DOCUSEAL_WEBHOOK_SECRET", "").lower() in (
        "1", "true", "yes",
    )

    if not secret:
        if debug and not is_prod:
            logger.warning("[docuseal_webhook] DOCUSEAL_WEBHOOK_SECRET unset — allowing in DEBUG only")
            return True
        logger.error(
            "[docuseal_webhook] DOCUSEAL_WEBHOOK_SECRET not set — rejecting "
            "(set secret in DocuSeal admin + env)"
        )
        return False

    if not signature:
        logger.warning("[docuseal_webhook] missing signature header — rejecting")
        return False

    sig = signature.strip()
    # DocuSeal canonical: "{unix_ts}.{hmac_hex}" over "{ts}.{body}"
    if "." in sig:
        ts_str, _, provided = sig.partition(".")
        try:
            ts = int(ts_str)
        except ValueError:
            ts = None
        if ts is not None and provided:
            now = int(time.time())
            # Match DocuSeal WebhookUrls::Signatures::TOLERANCE (5 minutes)
            if abs(now - ts) > 300:
                logger.warning(
                    "[docuseal_webhook] signature timestamp outside tolerance "
                    "(skew=%ss) — rejecting",
                    now - ts,
                )
                return False
            signed = f"{ts}.".encode("utf-8") + payload
            expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, provided.lower()):
                return True
            logger.warning("[docuseal_webhook] invalid DocuSeal timestamp.hmac signature — rejecting")
            return False

    # Legacy / non-DocuSeal hex or sha256=hex over raw body only
    legacy = sig.lower()
    if legacy.startswith("sha256="):
        legacy = legacy[7:]
    expected_legacy = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    ok = hmac.compare_digest(expected_legacy, legacy)
    if not ok:
        logger.warning("[docuseal_webhook] invalid signature — rejecting")
    return ok


@webhooks_bp.post("/webhooks/docuseal")
async def docuseal_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Handle DocuSeal form/submission webhooks.

    Events of interest:
      - form.completed       — one submitter finished
      - submission.completed — all submitters finished → shared exactly-once
                               completion handler (claim → 200 → background work)
      - form.declined / submission.expired / submission.created — audit + status

    Configure in DocuSeal admin → Webhooks →
      URL: https://leads.shamrockbailbonds.biz/api/webhooks/docuseal
    """
    from dashboard.routers.events import publish_event

    raw = await request.body()
    signature = (
        request.headers.get("X-DocuSeal-Signature")
        or request.headers.get("X-Webhook-Signature")
        or request.headers.get("X-Signature")
        or ""
    )
    if not verify_docuseal_signature(raw, signature):
        return JSONResponse({"error": "Invalid signature"}, status_code=401)

    try:
        data = await request.json() or {}
    except Exception:
        import json as _json

        try:
            data = _json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            data = {}

    now_iso = datetime.now(timezone.utc).isoformat()
    event_type = (
        data.get("event_type")
        or data.get("event")
        or data.get("type")
        or "unknown"
    )
    event_l = str(event_type).lower().strip()
    # Nested data payload (DocuSeal often wraps under "data")
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(payload, dict):
        payload = {}

    submission_id = (
        payload.get("submission_id")
        or payload.get("id")
        or (payload.get("submission") or {}).get("id")
        or data.get("submission_id")
        or ""
    )
    # form.completed is per-submitter — submission id may be nested
    if not submission_id and isinstance(payload.get("submission"), dict):
        submission_id = payload["submission"].get("id") or ""

    external_id = payload.get("external_id") or ""
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    packet_id_hint = metadata.get("packet_id") or ""
    if not packet_id_hint and external_id and ":" in str(external_id):
        packet_id_hint = str(external_id).split(":")[0]

    packets_col = get_collection("paperwork_packets")

    async def _find_packet():
        pkt = None
        if submission_id:
            sid = submission_id
            or_clause = [
                {"docuseal_submission_id": sid},
                {"docuseal_submission_id": str(sid)},
            ]
            if str(sid).isdigit():
                or_clause.append({"docuseal_submission_id": int(sid)})
            pkt = await packets_col.find_one({"$or": or_clause})
        if not pkt and packet_id_hint:
            pkt = await packets_col.find_one({"packet_id": packet_id_hint})
        return pkt

    # Audit first (immutable). A PKT-TEST- packet, or one stored with is_test,
    # is tagged so reports can leave it out. Other events stay the same shape.
    packet_for_audit = None
    try:
        packet_for_audit = await _find_packet()
    except Exception:
        packet_for_audit = None
    try:
        from dashboard.services.staff_test_case import (
            audit_test_marker,
            docuseal_event_is_test,
        )

        audit_events = get_collection("audit_events")
        audit_doc = {
            "source": "docuseal_webhook",
            "event_type": event_type,
            "payload": data,
            "timestamp": now_iso,
        }
        if docuseal_event_is_test(packet_for_audit, str(packet_id_hint or "")):
            tagged_id = str(packet_id_hint or "")
            booking = ""
            if isinstance(packet_for_audit, dict):
                tagged_id = str(packet_for_audit.get("packet_id") or tagged_id)
                booking = str(
                    packet_for_audit.get("booking_number")
                    or packet_for_audit.get("defendant_booking_number")
                    or ""
                )
            audit_doc.update(audit_test_marker(tagged_id, booking_number=booking))
        await audit_events.insert_one(audit_doc)
    except Exception as audit_exc:
        logger.warning("[docuseal_webhook] audit insert failed: %s", audit_exc)

    logger.info("[docuseal_webhook] event=%s", event_type)

    # Lifecycle events that do not complete the packet
    lifecycle_status_map = {
        "form.declined": "declined",
        "form_declined": "declined",
        "submission.declined": "declined",
        "submission.expired": "expired",
        "submission_expired": "expired",
        "submission.created": "sent",
        "submission_created": "sent",
        "form.started": "in_progress",
        "form.viewed": "viewed",
    }
    is_lifecycle = event_l in lifecycle_status_map or (
        "complet" not in event_l
        and any(x in event_l for x in ("declin", "expir", "created", "started", "viewed"))
    )
    if is_lifecycle:
        st = lifecycle_status_map.get(event_l)
        if not st:
            if "declin" in event_l:
                st = "declined"
            elif "expir" in event_l:
                st = "expired"
            elif "created" in event_l:
                st = "sent"
            else:
                st = "in_progress"
        packet = await _find_packet()
        if packet:
            await packets_col.update_one(
                {"packet_id": packet.get("packet_id")},
                {
                    "$set": {
                        "docuseal_last_event": event_type,
                        "docuseal_last_event_at": now_iso,
                        "docuseal_status": st,
                        "esign_provider": "docuseal",
                        **({"status": st} if st in ("declined", "expired") else {}),
                    }
                },
            )
            try:
                await publish_event(
                    f"docuseal_{st}",
                    {
                        "packet_id": packet.get("packet_id"),
                        "submission_id": submission_id,
                        "event": event_type,
                    },
                )
            except Exception:
                pass
        return JSONResponse(
            status_code=200,
            content={"success": True, "action": "lifecycle_logged", "event": event_type, "status": st},
        )

    # Completion-style events
    complete_events = {
        "form.completed",
        "submission.completed",
        "form_completed",
        "submission_completed",
        "completed",
    }
    if event_l not in complete_events and "complet" not in event_l:
        return JSONResponse(
            status_code=200,
            content={"success": True, "action": "logged_only", "event": event_type},
        )

    packet = await _find_packet()

    if not packet:
        logger.warning(
            "[docuseal_webhook] no packet for submission_id=%s packet_hint=%s",
            submission_id,
            packet_id_hint,
        )
        return JSONResponse(
            status_code=200,
            content={"success": True, "warning": "packet_not_found", "submission_id": submission_id},
        )

    packet_id = packet.get("packet_id", "")
    defendant_name = packet.get("defendant_name", "Unknown")

    # Full completion: submission.* OR bare "completed" with a submission_id
    is_full = (
        "submission" in event_l
        or event_l in ("completed", "submission_completed")
        or (event_l == "completed" and bool(submission_id))
    )
    # form.completed alone is per-party
    if event_l.startswith("form.") and "submission" not in event_l:
        is_full = False

    if not is_full:
        # Track individual submitter completion (do not Drive-file yet)
        role = payload.get("role") or metadata.get("party_role") or ""
        # Sanitize role for dotted Mongo keys
        role_key = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in str(role or "unknown"))[:64]
        await packets_col.update_one(
            {"packet_id": packet_id},
            {
                "$set": {
                    "docuseal_last_event": event_type,
                    "docuseal_last_event_at": now_iso,
                    f"docuseal_parties.{role_key}.completed_at": now_iso,
                    "esign_provider": "docuseal",
                    "docuseal_status": "partially_signed",
                },
                "$addToSet": {"docuseal_completed_roles": role or "unknown"},
            },
        )
        try:
            await publish_event(
                "docuseal_form_completed",
                {
                    "packet_id": packet_id,
                    "submission_id": submission_id,
                    "role": role,
                    "defendant_name": defendant_name,
                },
            )
        except Exception:
            pass
        return JSONResponse(
            status_code=200,
            content={"success": True, "action": "party_recorded", "packet_id": packet_id},
        )

    # ── Full submission complete → shared exactly-once handler ─────────────
    # dashboard/services/docuseal_completion.py owns every side effect (Drive,
    # packet/bond_cases status, SSE, legacy payment link switch, stage-only
    # share invoice, court sync, Slack) for BOTH this webhook and the cron
    # poller. We only (1) record that a completion webhook arrived, (2) try to
    # atomically claim the packet, and (3) return 200 right away; the slow work
    # runs in a background task so DocuSeal's 15s read timeout never triggers a
    # redelivery. A crashed background run is retried by the poller once its
    # lease expires.
    from dashboard.services.docuseal_completion import (
        SOURCE_WEBHOOK,
        claim_completion,
        mark_webhook_received,
        run_claimed_completion_safely,
    )

    await mark_webhook_received(packets_col, packet, event_type=event_type, now_iso=now_iso)
    claimed = await claim_completion(packets_col, packet, source=SOURCE_WEBHOOK)
    if not claimed:
        logger.info(
            "[docuseal_webhook] completion already claimed/done — ignoring delivery packet=%s",
            packet_id,
        )
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "action": "completion_duplicate_ignored",
                "packet_id": packet_id,
                "submission_id": submission_id,
            },
        )

    background_tasks.add_task(
        run_claimed_completion_safely,
        claimed,
        get_col=get_collection,
        source=SOURCE_WEBHOOK,
        submission_id=submission_id,
        event_type=event_type,
    )
    logger.info("[docuseal_webhook] completion claimed packet=%s — processing in background", packet_id)
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "action": "completion_accepted",
            "packet_id": packet_id,
            "submission_id": submission_id,
        },
    )
