from __future__ import annotations

"""
ShamrockLeads — Phase 10: Outreach Sequencing API Blueprint

Endpoints:
  POST /api/outreach/start/<booking_number>/<county>  — Start sequence for an arrest
  POST /api/outreach/stop/<booking_number>/<county>   — Stop active sequence
  GET  /api/outreach/status/<booking_number>/<county> — Get sequence status
  POST /api/outreach/batch/start                      — Start sequences for new arrests
  GET  /api/outreach/sequences                        — List all sequences (paginated)
  POST /api/outreach/reply                            — Handle inbound reply (stop sequence)
"""
import logging
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from dashboard.services.outreach_sequencer import OutreachSequencer
from dashboard.extensions import get_collection, get_db

logger = logging.getLogger(__name__)
outreach_bp = APIRouter(prefix="/api", tags=["outreach"])
def _get_sequencer() -> OutreachSequencer:
    return OutreachSequencer(get_db())


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/outreach/start/<booking_number>/<county>
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.post("/outreach/start/{booking_number}/{county}")
async def start_sequence(booking_number: str, county: str):
    """Start an outreach sequence for a specific arrest record."""
    try:
        arrests = get_collection("arrests")
        arrest = await arrests.find_one(
            {"booking_number": booking_number,
             "county": {"$regex": f"^{county}$", "$options": "i"}},
            {"_id": 0},
        )
        if not arrest:
            return JSONResponse({"error": f"Arrest not found: {county}/{booking_number}"}, status_code=404)

        sequencer = _get_sequencer()
        result = await sequencer.start_sequence(arrest)
        return result

    except Exception as exc:
        logger.exception("start_sequence error for %s/%s", county, booking_number)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/outreach/stop/<booking_number>/<county>
# Body (optional): { "reason": "intake_submitted" }
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.post("/outreach/stop/{booking_number}/{county}")
async def stop_sequence(request: Request, booking_number: str, county: str):
    """Stop an active outreach sequence and cancel scheduled BB messages."""
    try:
        data = (await request.json()) or {}
        reason = data.get("reason", "manual_stop")

        sequencer = _get_sequencer()
        result = await sequencer.stop_sequence(
            booking_number=booking_number,
            county=county,
            reason=reason,
        )
        return result

    except Exception as exc:
        logger.exception("stop_sequence error for %s/%s", county, booking_number)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/outreach/status/<booking_number>/<county>
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.get("/outreach/status/{booking_number}/{county}")
async def get_sequence_status(booking_number: str, county: str):
    """Return the current outreach sequence status for an arrest record."""
    try:
        seqs_col = get_collection("outreach_sequences")
        seq = await seqs_col.find_one(
            {"booking_number": booking_number,
             "county": {"$regex": f"^{county}$", "$options": "i"}},
            {"_id": 0},
            sort=[("created_at", -1)],
        )
        if not seq:
            return {"found": False, "booking_number": booking_number, "county": county}

        # Serialize datetimes
        for field in ("created_at", "updated_at", "stopped_at"):
            if hasattr(seq.get(field), "isoformat"):
                seq[field] = seq[field].isoformat()
        for step in seq.get("steps", []):
            for f in ("scheduled_for", "sent_at"):
                if hasattr(step.get(f), "isoformat"):
                    step[f] = step[f].isoformat()

        return {"found": True, **seq}

    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/outreach/batch/start
# Body (optional): { "hours_back": 24, "limit": 100 }
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.post("/outreach/batch/start")
async def batch_start(request: Request):
    """
    Start outreach sequences for all new arrests in the last N hours.
    Skips arrests that already have an active sequence.
    """
    try:
        data = (await request.json()) or {}
        hours_back = int(data.get("hours_back", 24))
        limit = min(int(data.get("limit", 100)), 500)

        sequencer = _get_sequencer()
        result = await sequencer.batch_start_new_arrests(
            hours_back=hours_back,
            limit=limit,
        )
        return {"success": True, **result}

    except Exception as exc:
        logger.exception("batch_start error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/outreach/sequences
# Query params: status, county, limit, offset
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.get("/outreach/sequences")
async def list_sequences(status: str = Query(default=""), county: str = Query(default=""), limit: int = Query(default=50), offset: int = Query(default=0)):
    """Return paginated list of outreach sequences."""
    try:
        status = status
        county = county
        limit = min(int(limit), 200)
        offset = int(offset)

        query: dict = {}
        if status:
            query["status"] = status
        if county:
            query["county"] = {"$regex": f"^{county}$", "$options": "i"}

        seqs_col = get_collection("outreach_sequences")
        total = await seqs_col.count_documents(query)
        cursor = seqs_col.find(query, {"_id": 0, "steps": 0}).sort(
            "created_at", -1
        ).skip(offset).limit(limit)
        sequences = await cursor.to_list(length=limit)

        for seq in sequences:
            for field in ("created_at", "updated_at", "stopped_at"):
                if hasattr(seq.get(field), "isoformat"):
                    seq[field] = seq[field].isoformat()

        return {
            "success": True,
            "sequences": sequences,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/outreach/reply
# Called by the BB webhook receiver when an inbound iMessage arrives.
# Body: { "phone": "+12395551234", "message": "...", "chat_guid": "..." }
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.post("/outreach/reply")
async def handle_reply(request: Request):
    """
    Handle an inbound iMessage reply from a prospect.
    Stops any active outreach sequence for that phone number.
    This endpoint is called by the BlueBubbles webhook receiver.
    """
    try:
        data = (await request.json()) or {}
        phone = data.get("phone", "").strip()
        message_text = data.get("message", "").strip()

        if not phone:
            return JSONResponse({"error": "phone is required"}, status_code=400)

        sequencer = _get_sequencer()
        result = await sequencer.handle_reply(phone=phone, message_text=message_text)
        return {"success": True, **result}

    except Exception as exc:
        logger.exception("handle_reply error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/outreach/review-queue
# Returns pending review queue items (The Closer — Review Mode)
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.get("/outreach/review-queue")
async def get_review_queue(limit: int = Query(default=50)):
    """Return pending outreach items awaiting staff review."""
    try:
        sequencer = _get_sequencer()
        items = await sequencer.get_review_queue(limit=min(int(limit), 200))
        # Serialize ObjectId and datetime
        for item in items:
            for k, v in item.items():
                if hasattr(v, "isoformat"):
                    item[k] = v.isoformat()
            if "_id" in item:
                item["_id"] = str(item["_id"])
        return {"success": True, "queue": items, "count": len(items)}
    except Exception as exc:
        logger.exception("get_review_queue error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/outreach/review-queue/approve/<review_id>
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.post("/outreach/review-queue/approve/{review_id}")
async def approve_review(review_id: str):
    """Approve a queued outreach item — sends the message immediately."""
    try:
        sequencer = _get_sequencer()
        result = await sequencer.approve_review(review_id)
        return {"success": True, **result}
    except Exception as exc:
        logger.exception("approve_review error for %s", review_id)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/outreach/review-queue/reject/<review_id>
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.post("/outreach/review-queue/reject/{review_id}")
async def reject_review(review_id: str):
    """Reject a queued outreach item — marks it skipped."""
    try:
        sequencer = _get_sequencer()
        result = await sequencer.reject_review(review_id)
        return {"success": True, **result}
    except Exception as exc:
        logger.exception("reject_review error for %s", review_id)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/outreach/review-queue/bulk-approve
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.post("/outreach/review-queue/bulk-approve")
async def bulk_approve_reviews():
    """Approve ALL pending review items at once."""
    try:
        sequencer = _get_sequencer()
        items = await sequencer.get_review_queue(limit=200)
        approved = 0
        errors = []
        for item in items:
            rid = str(item.get("_id", ""))
            if not rid:
                continue
            try:
                await sequencer.approve_review(rid)
                approved += 1
            except Exception as e:
                errors.append({"review_id": rid, "error": str(e)})
        return {
            "success": True,
            "approved": approved,
            "errors": errors,
            "total_pending": len(items),
        }
    except Exception as exc:
        logger.exception("bulk_approve error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
#  The Closer — Drip Sequence Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@outreach_bp.get("/outreach/drip/sequences")
async def list_drip_sequences(
    status: str = Query(default=""),
    sequence_type: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List active drip sequences with optional filters."""
    try:
        from dashboard.extensions import get_db
        db = get_db()
        query: dict = {}
        if status:
            query["status"] = status
        if sequence_type:
            query["sequence_type"] = sequence_type
        cursor = db["outreach_sequences"].find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
        items = await cursor.to_list(length=limit)
        return {"success": True, "sequences": items, "count": len(items)}
    except Exception as exc:
        logger.exception("list_drip_sequences error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@outreach_bp.get("/outreach/drip/pending")
async def list_drip_pending(limit: int = Query(default=50, ge=1, le=200)):
    """List all drip queue items pending human approval."""
    try:
        from dashboard.extensions import get_db
        db = get_db()
        cursor = db["outreach_queue"].find(
            {"status": "pending_approval", "requires_approval": True},
            {"_id": 0},
        ).sort("created_at", -1).limit(limit)
        items = await cursor.to_list(length=limit)
        return {"success": True, "pending": items, "count": len(items)}
    except Exception as exc:
        logger.exception("list_drip_pending error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@outreach_bp.post("/outreach/drip/approve/{queue_id}")
async def approve_drip_message(queue_id: str):
    """Approve a pending drip message and send it via BlueBubbles.

    Also advances the sequence to queue the next step.
    """
    try:
        from dashboard.extensions import get_db
        from dashboard.services.drip_sequences import DripSequenceRunner
        from dashboard.services.audit_service import AuditService
        db = get_db()

        item = await db["outreach_queue"].find_one({"queue_id": queue_id})
        if not item:
            return JSONResponse({"success": False, "error": "Queue item not found"}, status_code=404)
        if item.get("status") != "pending_approval":
            return JSONResponse({"success": False, "error": f"Item is not pending approval (status: {item.get('status')})"}, status_code=400)

        # Send via BlueBubbles
        from dashboard.extensions import BB_SERVERS, format_phone
        from dashboard.routers.bb_private_api import BlueBubblesClient

        phone = format_phone(item.get("phone", ""))
        message_text = item.get("message_text", "")
        send_result = {"success": False, "error": "No BB server configured"}

        bb_server = next(iter(BB_SERVERS.values()), None) if BB_SERVERS else None
        if bb_server and phone and message_text:
            bb_client = BlueBubblesClient(bb_server["url"], bb_server["password"])
            chat_guid = f"any;-;{phone}"
            send_result = await bb_client.send_human_like(chat_guid, message_text, typing_delay=2.0)

        now_iso = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        status = "sent" if send_result.get("success") else "send_failed"

        await db["outreach_queue"].update_one(
            {"queue_id": queue_id},
            {"$set": {
                "status": status,
                "approved_at": now_iso,
                "send_result": send_result,
                "bb_message_guid": (send_result.get("data") or {}).get("guid", ""),
            }},
        )

        # Advance sequence to next step
        sequence_id = item.get("sequence_id", "")
        if sequence_id and send_result.get("success"):
            runner = DripSequenceRunner(db)
            await runner.advance_sequence(sequence_id, approved_step=item.get("step", 1))

        # Audit event
        try:
            audit = AuditService(db)
            await audit.log(
                event_type="drip_message_approved",
                entity_type="outreach_queue",
                entity_id=queue_id,
                details={
                    "sequence_id": sequence_id,
                    "sequence_type": item.get("sequence_type"),
                    "booking_number": item.get("booking_number"),
                    "step": item.get("step"),
                    "channel": item.get("channel"),
                    "send_status": status,
                },
            )
        except Exception as audit_exc:
            logger.warning("[DripApprove] Audit log failed: %s", audit_exc)

        logger.info(
            "[DripApprove] queue_id=%s step=%d status=%s",
            queue_id, item.get("step", 0), status,
        )
        return {"success": True, "queue_id": queue_id, "send_status": status, "send_result": send_result}

    except Exception as exc:
        logger.exception("approve_drip_message error for %s", queue_id)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@outreach_bp.post("/outreach/drip/reject/{queue_id}")
async def reject_drip_message(queue_id: str, request: Request):
    """Reject a pending drip message (skip this step, do not send)."""
    try:
        from dashboard.extensions import get_db
        from dashboard.services.audit_service import AuditService
        db = get_db()

        data = await request.json() or {}
        reason = data.get("reason", "manual_reject")

        item = await db["outreach_queue"].find_one({"queue_id": queue_id})
        if not item:
            return JSONResponse({"success": False, "error": "Queue item not found"}, status_code=404)

        now_iso = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        await db["outreach_queue"].update_one(
            {"queue_id": queue_id},
            {"$set": {"status": "rejected", "rejected_at": now_iso, "reject_reason": reason}},
        )

        # Audit
        try:
            audit = AuditService(db)
            await audit.log(
                event_type="drip_message_rejected",
                entity_type="outreach_queue",
                entity_id=queue_id,
                details={
                    "sequence_id": item.get("sequence_id"),
                    "booking_number": item.get("booking_number"),
                    "step": item.get("step"),
                    "reason": reason,
                },
            )
        except Exception as audit_exc:
            logger.warning("[DripReject] Audit log failed: %s", audit_exc)

        return {"success": True, "queue_id": queue_id, "status": "rejected"}

    except Exception as exc:
        logger.exception("reject_drip_message error for %s", queue_id)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@outreach_bp.post("/outreach/drip/stop/{sequence_id}")
async def stop_drip_sequence(sequence_id: str):
    """Manually stop a drip sequence."""
    try:
        from dashboard.extensions import get_db
        from dashboard.services.drip_sequences import DripSequenceRunner
        db = get_db()
        runner = DripSequenceRunner(db)
        result = await runner.stop_sequence(sequence_id, reason="manual_stop")
        return result
    except Exception as exc:
        logger.exception("stop_drip_sequence error for %s", sequence_id)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@outreach_bp.get("/outreach/drip/stats")
async def drip_stats():
    """Get drip sequence performance metrics."""
    try:
        from dashboard.extensions import get_db
        db = get_db()
        seqs = db["outreach_sequences"]
        queue = db["outreach_queue"]

        total = await seqs.count_documents({"sequence_type": {"$exists": True}})
        active = await seqs.count_documents({"status": "active"})
        pending = await seqs.count_documents({"status": "pending_approval"})
        completed = await seqs.count_documents({"status": "completed"})
        stopped = await seqs.count_documents({"status": "stopped"})

        pending_approval = await queue.count_documents({"status": "pending_approval"})
        sent = await queue.count_documents({"status": "sent"})
        rejected = await queue.count_documents({"status": "rejected"})

        return {
            "success": True,
            "sequences": {
                "total": total,
                "active": active,
                "pending_approval": pending,
                "completed": completed,
                "stopped": stopped,
            },
            "messages": {
                "pending_approval": pending_approval,
                "sent": sent,
                "rejected": rejected,
            },
        }
    except Exception as exc:
        logger.exception("drip_stats error")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
