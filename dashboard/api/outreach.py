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
from __future__ import annotations
import logging
from quart import Blueprint, jsonify, request
from dashboard.services.outreach_sequencer import OutreachSequencer
from dashboard.extensions import get_collection, get_db

logger = logging.getLogger(__name__)
outreach_bp = Blueprint("outreach", __name__)


def _get_sequencer() -> OutreachSequencer:
    return OutreachSequencer(get_db())


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/outreach/start/<booking_number>/<county>
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.route("/outreach/start/<booking_number>/<county>", methods=["POST"])
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
            return jsonify({"error": f"Arrest not found: {county}/{booking_number}"}), 404

        sequencer = _get_sequencer()
        result = await sequencer.start_sequence(arrest)
        return jsonify(result)

    except Exception as exc:
        logger.exception("start_sequence error for %s/%s", county, booking_number)
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/outreach/stop/<booking_number>/<county>
# Body (optional): { "reason": "intake_submitted" }
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.route("/outreach/stop/<booking_number>/<county>", methods=["POST"])
async def stop_sequence(booking_number: str, county: str):
    """Stop an active outreach sequence and cancel scheduled BB messages."""
    try:
        data = (await request.get_json()) or {}
        reason = data.get("reason", "manual_stop")

        sequencer = _get_sequencer()
        result = await sequencer.stop_sequence(
            booking_number=booking_number,
            county=county,
            reason=reason,
        )
        return jsonify(result)

    except Exception as exc:
        logger.exception("stop_sequence error for %s/%s", county, booking_number)
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/outreach/status/<booking_number>/<county>
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.route("/outreach/status/<booking_number>/<county>", methods=["GET"])
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
            return jsonify({"found": False, "booking_number": booking_number, "county": county})

        # Serialize datetimes
        for field in ("created_at", "updated_at", "stopped_at"):
            if hasattr(seq.get(field), "isoformat"):
                seq[field] = seq[field].isoformat()
        for step in seq.get("steps", []):
            for f in ("scheduled_for", "sent_at"):
                if hasattr(step.get(f), "isoformat"):
                    step[f] = step[f].isoformat()

        return jsonify({"found": True, **seq})

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/outreach/batch/start
# Body (optional): { "hours_back": 24, "limit": 100 }
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.route("/outreach/batch/start", methods=["POST"])
async def batch_start():
    """
    Start outreach sequences for all new arrests in the last N hours.
    Skips arrests that already have an active sequence.
    """
    try:
        data = (await request.get_json()) or {}
        hours_back = int(data.get("hours_back", 24))
        limit = min(int(data.get("limit", 100)), 500)

        sequencer = _get_sequencer()
        result = await sequencer.batch_start_new_arrests(
            hours_back=hours_back,
            limit=limit,
        )
        return jsonify({"success": True, **result})

    except Exception as exc:
        logger.exception("batch_start error")
        return jsonify({"success": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/outreach/sequences
# Query params: status, county, limit, offset
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.route("/outreach/sequences", methods=["GET"])
async def list_sequences():
    """Return paginated list of outreach sequences."""
    try:
        status = request.args.get("status", "")
        county = request.args.get("county", "")
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))

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

        return jsonify({
            "success": True,
            "sequences": sequences,
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/outreach/reply
# Called by the BB webhook receiver when an inbound iMessage arrives.
# Body: { "phone": "+12395551234", "message": "...", "chat_guid": "..." }
# ─────────────────────────────────────────────────────────────────────────────
@outreach_bp.route("/outreach/reply", methods=["POST"])
async def handle_reply():
    """
    Handle an inbound iMessage reply from a prospect.
    Stops any active outreach sequence for that phone number.
    This endpoint is called by the BlueBubbles webhook receiver.
    """
    try:
        data = (await request.get_json()) or {}
        phone = data.get("phone", "").strip()
        message_text = data.get("message", "").strip()

        if not phone:
            return jsonify({"error": "phone is required"}), 400

        sequencer = _get_sequencer()
        result = await sequencer.handle_reply(phone=phone, message_text=message_text)
        return jsonify({"success": True, **result})

    except Exception as exc:
        logger.exception("handle_reply error")
        return jsonify({"success": False, "error": str(exc)}), 500
