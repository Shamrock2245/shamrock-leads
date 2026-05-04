"""
ShamrockLeads — Phase 4: Matching Engine API Blueprint

Endpoints:
  POST /api/match/intake/<intake_id>       — Run matching for a single intake record
  POST /api/match/intake/<intake_id>/confirm — Manually confirm a match
  POST /api/match/batch                    — Run matching on all unmatched intakes
  GET  /api/match/intake/<intake_id>       — Get current match status for an intake
"""
import logging
from quart import Blueprint, jsonify, request
from dashboard.services.matching_engine import MatchingEngine
from dashboard.extensions import get_collection, get_db

logger = logging.getLogger(__name__)
matching_bp = Blueprint("matching", __name__)


def _get_engine() -> MatchingEngine:
    return MatchingEngine(get_db())


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/match/intake/<intake_id>
# Run the matching engine on a single intake record.
# ─────────────────────────────────────────────────────────────────────────────
@matching_bp.route("/match/intake/<intake_id>", methods=["POST"])
async def match_intake(intake_id: str):
    """
    Run the matching engine on a single intake record.
    Returns best match + up to 5 candidates for staff review.
    """
    try:
        intake_col = get_collection("intake_queue")
        intake_doc = await intake_col.find_one(
            {"intake_id": intake_id}, {"_id": 0}
        )
        if not intake_doc:
            return jsonify({"error": f"Intake {intake_id} not found"}), 404

        engine = _get_engine()
        result = await engine.match_intake(intake_doc)
        return jsonify(result)

    except Exception as exc:
        logger.exception("match_intake error for %s", intake_id)
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/match/intake/<intake_id>/confirm
# Manually confirm a match between an intake and an arrest record.
# Body: { "booking_number": "...", "county": "...", "agent": "..." }
# ─────────────────────────────────────────────────────────────────────────────
@matching_bp.route("/match/intake/<intake_id>/confirm", methods=["POST"])
async def confirm_match(intake_id: str):
    """
    Manually confirm a match between an intake record and an arrest record.
    Used when staff reviews candidates and selects the correct one.
    """
    try:
        data = (await request.get_json()) or {}
        booking_number = data.get("booking_number", "").strip()
        county = data.get("county", "").strip()
        agent = data.get("agent", "staff")

        if not booking_number or not county:
            return jsonify({"error": "booking_number and county are required"}), 400

        engine = _get_engine()
        result = await engine.confirm_match(
            intake_id=intake_id,
            booking_number=booking_number,
            county=county,
            agent=agent,
        )
        return jsonify(result)

    except Exception as exc:
        logger.exception("confirm_match error for %s", intake_id)
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/match/batch
# Run matching on all pending/unmatched intake records.
# Body (optional): { "limit": 200 }
# ─────────────────────────────────────────────────────────────────────────────
@matching_bp.route("/match/batch", methods=["POST"])
async def batch_match():
    """
    Run matching on all pending/unmatched intake records.
    Returns summary: total_processed, auto_linked, candidates_found, no_match.
    """
    try:
        data = (await request.get_json()) or {}
        limit = min(int(data.get("limit", 100)), 1000)

        engine = _get_engine()
        result = await engine.batch_match(limit=limit)
        return jsonify({"success": True, **result})

    except Exception as exc:
        logger.exception("batch_match error")
        return jsonify({"success": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/match/intake/<intake_id>
# Get the current match status for an intake record.
# ─────────────────────────────────────────────────────────────────────────────
@matching_bp.route("/match/intake/<intake_id>", methods=["GET"])
async def get_match_status(intake_id: str):
    """Return the current match status for an intake record."""
    try:
        intake_col = get_collection("intake_queue")
        doc = await intake_col.find_one(
            {"intake_id": intake_id},
            {
                "_id": 0,
                "intake_id": 1,
                "status": 1,
                "matched_booking_number": 1,
                "matched_county": 1,
                "matched_defendant_id": 1,
                "match_confidence": 1,
                "match_strategy": 1,
                "match_timestamp": 1,
                "confirmed_by": 1,
                "confirmed_at": 1,
            },
        )
        if not doc:
            return jsonify({"error": f"Intake {intake_id} not found"}), 404

        # Serialize datetimes
        for field in ("match_timestamp", "confirmed_at"):
            if hasattr(doc.get(field), "isoformat"):
                doc[field] = doc[field].isoformat()

        return jsonify(doc)

    except Exception as exc:
        logger.exception("get_match_status error for %s", intake_id)
        return jsonify({"error": str(exc)}), 500
