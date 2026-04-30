"""
ShamrockLeads — Prospective Bonds (In Progress Pipeline) API Blueprint
Handles the In Progress kanban pipeline: Contacted → Negotiating → Paperwork → Ready

Endpoints:
  POST   /api/prospective-bonds              — Create / track a new prospective bond
  GET    /api/prospective-bonds              — List with stage/status/search filters
  PATCH  /api/prospective-bonds/<bk>/stage  — Move to a new pipeline stage
  POST   /api/prospective-bonds/<bk>/note   — Add a note / communication log entry
  PATCH  /api/prospective-bonds/<bk>/indemnitor — Update indemnitor info
  POST   /api/prospective-bonds/<bk>/close  — Close with an outcome
  POST   /api/prospective-bonds/<bk>/officialize — Promote to active bond
  POST   /api/prospective-bonds/from-intake  — Promote an intake queue entry to In Progress
"""
from __future__ import annotations

from quart import Blueprint, request, jsonify
from datetime import datetime, timezone
from dashboard.extensions import get_collection
import logging

logger = logging.getLogger(__name__)

prospective_bonds_bp = Blueprint("prospective_bonds", __name__)

VALID_STAGES = ["contacted", "negotiating", "paperwork", "ready"]
VALID_OUTCOMES = [
    "bonded", "lost_to_competitor", "released_ror", "no_contact",
    "declined", "left_vm", "sent_text_to", "other",
]


def _serialize(doc: dict) -> dict:
    """Convert datetime fields to ISO strings for JSON serialization."""
    out = {}
    for k, v in doc.items():
        if k == "_id":
            continue
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, dict):
            out[k] = _serialize(v)
        elif isinstance(v, list):
            out[k] = [_serialize(i) if isinstance(i, dict) else i for i in v]
        else:
            out[k] = v
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/prospective-bonds  — Create / track a new prospective bond
# ─────────────────────────────────────────────────────────────────────────────
@prospective_bonds_bp.route("/prospective-bonds", methods=["POST"])
async def api_prospective_create():
    """Create a prospective bond from an arrest record or manual entry."""
    try:
        data = await request.get_json(silent=True) or {}
        booking_number = (data.get("booking_number") or "").strip()
        if not booking_number:
            return jsonify({"success": False, "error": "booking_number is required"}), 400

        col = get_collection("prospective_bonds")
        arrests = get_collection("arrests")

        # Duplicate check
        existing = await col.find_one({"booking_number": booking_number})
        if existing:
            return jsonify({
                "success": False,
                "error": "Already tracked as prospective bond",
                "stage": existing.get("stage", "contacted"),
            }), 409

        # Snapshot defendant data from arrests collection
        arrest_doc = await arrests.find_one({"booking_number": booking_number}, {"_id": 0})
        if not arrest_doc:
            arrest_doc = {}

        now = datetime.now(timezone.utc)
        initial_stage = data.get("stage", "contacted")
        if initial_stage not in VALID_STAGES:
            initial_stage = "contacted"

        doc = {
            "booking_number": booking_number,
            "defendant_name": data.get("defendant_name") or arrest_doc.get("full_name", "Unknown"),
            "county": data.get("county") or arrest_doc.get("county", ""),
            "bond_amount": float(data.get("bond_amount") or arrest_doc.get("bond_amount", 0) or 0),
            "charges": data.get("charges") or arrest_doc.get("charges", ""),
            "lead_score": int(data.get("lead_score") or arrest_doc.get("lead_score", 0) or 0),
            "lead_status": data.get("lead_status") or arrest_doc.get("lead_status", ""),
            "detail_url": arrest_doc.get("detail_url", ""),
            # Pipeline state
            "stage": initial_stage,
            "status": "active",
            # Indemnitor / Cosigner
            "indemnitor": {
                "name": data.get("indemnitor_name", ""),
                "phone": data.get("indemnitor_phone", ""),
                "email": data.get("indemnitor_email", ""),
                "relationship": data.get("indemnitor_relationship", ""),
            },
            # Communication & timeline
            "communication_log": [],
            "timeline": [{
                "timestamp": now.isoformat(),
                "event": "created",
                "detail": data.get("note") or "Marked as In Progress from dashboard",
                "agent": data.get("agent", "Dashboard"),
            }],
            # Closure
            "outcome": None,
            "outcome_note": "",
            "closed_at": None,
            # Full arrest snapshot
            "defendant_snapshot": arrest_doc,
            # Metadata
            "created_at": now,
            "updated_at": now,
            "created_by": data.get("agent", "Dashboard"),
        }

        await col.insert_one(doc)
        return jsonify({"success": True, "prospective_bond": _serialize(doc)})

    except Exception as exc:
        logger.exception("api_prospective_create error")
        return jsonify({"success": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/prospective-bonds  — List with filters
# ─────────────────────────────────────────────────────────────────────────────
@prospective_bonds_bp.route("/prospective-bonds", methods=["GET"])
async def api_prospective_list():
    """List prospective bonds with optional stage/status/search filters."""
    try:
        stage = request.args.get("stage", "").strip()
        status = request.args.get("status", "active").strip()
        search = request.args.get("search", "").strip()

        col = get_collection("prospective_bonds")

        query: dict = {}
        if stage:
            query["stage"] = stage
        if status and status != "all":
            query["status"] = status
        if search:
            query["$or"] = [
                {"defendant_name": {"$regex": search, "$options": "i"}},
                {"booking_number": {"$regex": search, "$options": "i"}},
                {"county": {"$regex": search, "$options": "i"}},
                {"indemnitor.name": {"$regex": search, "$options": "i"}},
            ]

        bonds = []
        async for doc in col.find(query, {"_id": 0}).sort("updated_at", -1).limit(200):
            bonds.append(_serialize(doc))

        # Stage counts for KPI cards
        stage_counts = {}
        for s in VALID_STAGES:
            stage_counts[s] = await col.count_documents({"stage": s, "status": "active"})
        total_active = sum(stage_counts.values())

        # Messages sent today
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        msgs_today = 0
        for b in bonds:
            for msg in b.get("communication_log", []):
                try:
                    ts = msg.get("timestamp", "")
                    if ts and ts >= today_start.isoformat():
                        msgs_today += 1
                except Exception:
                    pass

        return jsonify({
            "bonds": bonds,
            "total": len(bonds),
            "total_active": total_active,
            "stage_counts": stage_counts,
            "messages_today": msgs_today,
        })

    except Exception as exc:
        logger.exception("api_prospective_list error")
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  PATCH /api/prospective-bonds/<booking_number>/stage
# ─────────────────────────────────────────────────────────────────────────────
@prospective_bonds_bp.route("/prospective-bonds/<booking_number>/stage", methods=["PATCH"])
async def api_prospective_update_stage(booking_number: str):
    """Move a prospective bond to a new pipeline stage."""
    try:
        data = await request.get_json(silent=True) or {}
        new_stage = (data.get("stage") or "").strip()
        note = (data.get("note") or "").strip()
        agent = data.get("agent", "Dashboard")

        if new_stage not in VALID_STAGES:
            return jsonify({"error": f"Invalid stage. Must be one of: {VALID_STAGES}"}), 400

        col = get_collection("prospective_bonds")
        existing = await col.find_one({"booking_number": booking_number})
        if not existing:
            return jsonify({"error": "Prospective bond not found"}), 404
        if existing.get("status") in ("closed", "promoted"):
            return jsonify({"error": f"Cannot update stage — bond is {existing['status']}"}), 409

        now = datetime.now(timezone.utc)
        old_stage = existing.get("stage", "")
        timeline_entry = {
            "timestamp": now.isoformat(),
            "event": "stage_change",
            "detail": f"Stage: {old_stage} → {new_stage}" + (f" — {note}" if note else ""),
            "agent": agent,
        }

        await col.update_one(
            {"booking_number": booking_number},
            {
                "$set": {"stage": new_stage, "updated_at": now},
                "$push": {"timeline": timeline_entry},
            },
        )
        return jsonify({"success": True, "booking_number": booking_number, "stage": new_stage})

    except Exception as exc:
        logger.exception("api_prospective_update_stage error")
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/prospective-bonds/<booking_number>/note
# ─────────────────────────────────────────────────────────────────────────────
@prospective_bonds_bp.route("/prospective-bonds/<booking_number>/note", methods=["POST"])
async def api_prospective_add_note(booking_number: str):
    """Add a note or communication log entry."""
    try:
        data = await request.get_json(silent=True) or {}
        note_text = (data.get("note") or data.get("text") or "").strip()
        note_type = data.get("type", "note")
        agent = data.get("agent", "Dashboard")

        if not note_text:
            return jsonify({"error": "note text is required"}), 400

        col = get_collection("prospective_bonds")
        existing = await col.find_one({"booking_number": booking_number})
        if not existing:
            return jsonify({"error": "Prospective bond not found"}), 404

        now = datetime.now(timezone.utc)
        log_entry = {
            "timestamp": now.isoformat(),
            "type": note_type,
            "text": note_text,
            "agent": agent,
        }

        updates: dict = {
            "$set": {"updated_at": now},
            "$push": {
                "communication_log": log_entry,
                "timeline": {
                    "timestamp": now.isoformat(),
                    "event": note_type,
                    "detail": note_text,
                    "agent": agent,
                },
            },
        }
        await col.update_one({"booking_number": booking_number}, updates)
        return jsonify({"success": True, "booking_number": booking_number})

    except Exception as exc:
        logger.exception("api_prospective_add_note error")
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  PATCH /api/prospective-bonds/<booking_number>/indemnitor
# ─────────────────────────────────────────────────────────────────────────────
@prospective_bonds_bp.route("/prospective-bonds/<booking_number>/indemnitor", methods=["PATCH"])
async def api_prospective_update_indemnitor(booking_number: str):
    """Update the indemnitor/cosigner info on a prospective bond."""
    try:
        data = await request.get_json(silent=True) or {}
        col = get_collection("prospective_bonds")
        existing = await col.find_one({"booking_number": booking_number})
        if not existing:
            return jsonify({"error": "Prospective bond not found"}), 404

        now = datetime.now(timezone.utc)
        indemnitor = existing.get("indemnitor", {})
        for field in ["name", "phone", "email", "relationship", "address", "dob"]:
            if data.get(field) is not None:
                indemnitor[field] = data[field]

        await col.update_one(
            {"booking_number": booking_number},
            {
                "$set": {"indemnitor": indemnitor, "updated_at": now},
                "$push": {"timeline": {
                    "timestamp": now.isoformat(),
                    "event": "indemnitor_updated",
                    "detail": f"Indemnitor info updated: {indemnitor.get('name', '')}",
                    "agent": data.get("agent", "Dashboard"),
                }},
            },
        )
        return jsonify({"success": True, "indemnitor": indemnitor})

    except Exception as exc:
        logger.exception("api_prospective_update_indemnitor error")
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/prospective-bonds/<booking_number>/close
# ─────────────────────────────────────────────────────────────────────────────
@prospective_bonds_bp.route("/prospective-bonds/<booking_number>/close", methods=["POST"])
async def api_prospective_close(booking_number: str):
    """Close a prospective bond with an outcome reason."""
    try:
        data = await request.get_json(silent=True) or {}
        outcome = (data.get("outcome") or "").strip()
        outcome_note = (data.get("note") or "").strip()
        agent = data.get("agent", "Dashboard")

        if outcome not in VALID_OUTCOMES:
            return jsonify({"error": f"Invalid outcome. Must be one of: {VALID_OUTCOMES}"}), 400

        col = get_collection("prospective_bonds")
        existing = await col.find_one({"booking_number": booking_number})
        if not existing:
            return jsonify({"error": "Prospective bond not found"}), 404

        now = datetime.now(timezone.utc)
        await col.update_one(
            {"booking_number": booking_number},
            {
                "$set": {
                    "status": "closed",
                    "outcome": outcome,
                    "outcome_note": outcome_note,
                    "closed_at": now.isoformat(),
                    "updated_at": now,
                },
                "$push": {"timeline": {
                    "timestamp": now.isoformat(),
                    "event": "closed",
                    "detail": f"Closed: {outcome}" + (f" — {outcome_note}" if outcome_note else ""),
                    "agent": agent,
                }},
            },
        )
        return jsonify({"success": True, "outcome": outcome})

    except Exception as exc:
        logger.exception("api_prospective_close error")
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/prospective-bonds/<booking_number>/officialize
# ─────────────────────────────────────────────────────────────────────────────
@prospective_bonds_bp.route("/prospective-bonds/<booking_number>/officialize", methods=["POST"])
async def api_prospective_officialize(booking_number: str):
    """Promote a prospective bond to an active bond.

    Marks the prospective record as 'promoted' and returns defendant + indemnitor
    data needed to pre-fill the Write Bond modal.
    """
    try:
        col = get_collection("prospective_bonds")
        existing = await col.find_one({"booking_number": booking_number})
        if not existing:
            return jsonify({"error": "Prospective bond not found"}), 404
        if existing.get("status") == "promoted":
            return jsonify({"error": "Already promoted to active bond"}), 409

        now = datetime.now(timezone.utc)
        await col.update_one(
            {"booking_number": booking_number},
            {
                "$set": {
                    "status": "promoted",
                    "outcome": "bonded",
                    "closed_at": now.isoformat(),
                    "updated_at": now,
                },
                "$push": {"timeline": {
                    "timestamp": now.isoformat(),
                    "event": "promoted",
                    "detail": "Promoted to Active Bond — bond officialized",
                    "agent": "Dashboard",
                }},
            },
        )

        # Return data for pre-filling the Write Bond modal
        snapshot = existing.get("defendant_snapshot", {})
        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "defendant_name": existing.get("defendant_name", ""),
            "county": existing.get("county", ""),
            "bond_amount": existing.get("bond_amount", 0),
            "charges": existing.get("charges", ""),
            "indemnitor": existing.get("indemnitor", {}),
            "defendant_snapshot": _serialize(snapshot),
        })

    except Exception as exc:
        logger.exception("api_prospective_officialize error")
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/prospective-bonds/from-intake
#  Promote an intake queue entry directly to In Progress pipeline
# ─────────────────────────────────────────────────────────────────────────────
@prospective_bonds_bp.route("/prospective-bonds/from-intake", methods=["POST"])
async def api_prospective_from_intake():
    """
    Promote an intake queue entry to the In Progress pipeline.

    Accepts an intake_id or booking_number. Creates a prospective bond
    pre-populated with the indemnitor's information from the intake record.
    """
    try:
        data = await request.get_json(silent=True) or {}
        intake_id = (data.get("intake_id") or "").strip()
        booking_number = (data.get("booking_number") or "").strip()
        agent = data.get("agent", "Dashboard")

        if not intake_id and not booking_number:
            return jsonify({"error": "intake_id or booking_number is required"}), 400

        intake_col = get_collection("intake_queue")
        prospective_col = get_collection("prospective_bonds")
        arrests_col = get_collection("arrests")

        # Find the intake record
        intake_query: dict = {}
        if intake_id:
            from bson import ObjectId
            try:
                intake_query["_id"] = ObjectId(intake_id)
            except Exception:
                intake_query["intake_id"] = intake_id
        if booking_number:
            intake_query["booking_number"] = booking_number

        intake_doc = await intake_col.find_one(intake_query)
        if not intake_doc:
            # Allow creating a prospective bond without an intake record if booking_number given
            if not booking_number:
                return jsonify({"error": "Intake record not found"}), 404
            intake_doc = {}

        bk = booking_number or intake_doc.get("booking_number", "")
        if not bk:
            return jsonify({"error": "Could not determine booking_number"}), 400

        # Check for duplicate
        existing_pb = await prospective_col.find_one({"booking_number": bk})
        if existing_pb:
            return jsonify({
                "success": False,
                "error": "Already in In Progress pipeline",
                "stage": existing_pb.get("stage", "contacted"),
            }), 409

        # Snapshot from arrests
        arrest_doc = await arrests_col.find_one({"booking_number": bk}, {"_id": 0}) or {}

        now = datetime.now(timezone.utc)
        initial_stage = data.get("stage", "contacted")
        if initial_stage not in VALID_STAGES:
            initial_stage = "contacted"

        # Build indemnitor from intake record
        indemnitor = {
            "name": intake_doc.get("indemnitor_name") or intake_doc.get("name", ""),
            "phone": intake_doc.get("indemnitor_phone") or intake_doc.get("phone", ""),
            "email": intake_doc.get("indemnitor_email") or intake_doc.get("email", ""),
            "relationship": intake_doc.get("relationship", ""),
        }

        doc = {
            "booking_number": bk,
            "defendant_name": (
                data.get("defendant_name")
                or intake_doc.get("defendant_name")
                or arrest_doc.get("full_name", "Unknown")
            ),
            "county": (
                data.get("county")
                or intake_doc.get("county")
                or arrest_doc.get("county", "")
            ),
            "bond_amount": float(
                data.get("bond_amount")
                or intake_doc.get("bond_amount")
                or arrest_doc.get("bond_amount", 0)
                or 0
            ),
            "charges": (
                data.get("charges")
                or intake_doc.get("charges")
                or arrest_doc.get("charges", "")
            ),
            "lead_score": int(arrest_doc.get("lead_score", 0) or 0),
            "lead_status": arrest_doc.get("lead_status", ""),
            "detail_url": arrest_doc.get("detail_url", ""),
            "stage": initial_stage,
            "status": "active",
            "indemnitor": indemnitor,
            "communication_log": [],
            "timeline": [{
                "timestamp": now.isoformat(),
                "event": "created",
                "detail": f"Promoted from Intake Queue to In Progress (stage: {initial_stage})",
                "agent": agent,
            }],
            "outcome": None,
            "outcome_note": "",
            "closed_at": None,
            "defendant_snapshot": arrest_doc,
            "intake_id": str(intake_doc.get("_id", "")),
            "created_at": now,
            "updated_at": now,
            "created_by": agent,
        }

        await prospective_col.insert_one(doc)

        # Mark intake record as promoted
        if intake_doc.get("_id"):
            await intake_col.update_one(
                {"_id": intake_doc["_id"]},
                {"$set": {"status": "promoted", "promoted_at": now, "promoted_by": agent}},
            )

        return jsonify({"success": True, "prospective_bond": _serialize(doc)})

    except Exception as exc:
        logger.exception("api_prospective_from_intake error")
        return jsonify({"error": str(exc)}), 500
