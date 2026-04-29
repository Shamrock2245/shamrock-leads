"""
ShamrockLeads — Defendant Lifecycle API
Endpoints for notes, contact log, DNB/DNC flags, preferred comms,
bond finalization (two-step), and outreach notes.

All data is stored in a separate `defendant_notes` collection keyed by
booking_number so the core `arrests` collection stays clean.
"""
from datetime import datetime, timezone
from quart import Blueprint, jsonify, request
from dashboard.extensions import get_collection

lifecycle_bp = Blueprint("defendant_lifecycle", __name__)

# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_notes_doc(booking_number: str):
    col = get_collection("defendant_notes")
    doc = await col.find_one({"booking_number": booking_number}, {"_id": 0})
    return doc or {}


# ─────────────────────────────────────────────────────────────────────────────
# GET  /api/defendant-notes/<booking_number>
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.route("/defendant-notes/<booking_number>", methods=["GET"])
async def get_defendant_notes(booking_number: str):
    doc = await _get_notes_doc(booking_number)
    return jsonify(doc)


# ─────────────────────────────────────────────────────────────────────────────
# PATCH  /api/defendant-notes/<booking_number>
# Body fields (all optional):
#   shamrock_status   — "new" | "contacted" | "negotiating" | "paperwork" | "ready" | "bonded" | "closed"
#   shamrock_notes    — free-text notes
#   follow_up_date    — ISO date string
#   next_action       — free-text
#   pref_comm         — "call" | "text" | "email" | "whatsapp"
#   dnb               — bool  (Do Not Bond)
#   dnc               — bool  (Do Not Call)
#   dnb_reason        — string
#   dnc_reason        — string
#   agent             — agent name who made the update
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.route("/defendant-notes/<booking_number>", methods=["PATCH"])
async def patch_defendant_notes(booking_number: str):
    col = get_collection("defendant_notes")
    body = await request.get_json(silent=True) or {}

    allowed = {
        "shamrock_status", "shamrock_notes", "follow_up_date", "next_action",
        "pref_comm", "dnb", "dnc", "dnb_reason", "dnc_reason", "agent",
    }
    update = {k: v for k, v in body.items() if k in allowed}
    update["updated_at"] = _now()
    update["booking_number"] = booking_number

    await col.update_one(
        {"booking_number": booking_number},
        {"$set": update},
        upsert=True,
    )
    doc = await _get_notes_doc(booking_number)
    return jsonify({"success": True, "notes": doc})


# ─────────────────────────────────────────────────────────────────────────────
# POST  /api/defendant-contact-log/<booking_number>
# Body:
#   method    — "call" | "text" | "email" | "in_person"
#   direction — "outbound" | "inbound"
#   summary   — what was said / outcome
#   agent     — agent name
#   contact   — who was contacted (defendant | cosigner name)
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.route("/defendant-contact-log/<booking_number>", methods=["POST"])
async def log_contact(booking_number: str):
    col = get_collection("defendant_notes")
    body = await request.get_json(silent=True) or {}

    entry = {
        "method":    body.get("method", "call"),
        "direction": body.get("direction", "outbound"),
        "summary":   body.get("summary", ""),
        "agent":     body.get("agent", ""),
        "contact":   body.get("contact", "defendant"),
        "ts":        _now(),
    }

    # Push to contact_log array; also set contacted_at if first contact
    update_ops = {"$push": {"contact_log": entry}, "$set": {"updated_at": _now()}}

    # Auto-bump shamrock_status to "contacted" if it was "new" or unset
    existing = await _get_notes_doc(booking_number)
    if not existing.get("shamrock_status") or existing.get("shamrock_status") == "new":
        update_ops["$set"]["shamrock_status"] = "contacted"
        update_ops["$set"]["first_contacted_at"] = _now()

    await col.update_one(
        {"booking_number": booking_number},
        update_ops,
        upsert=True,
    )
    doc = await _get_notes_doc(booking_number)
    return jsonify({"success": True, "notes": doc})


# ─────────────────────────────────────────────────────────────────────────────
# GET  /api/defendant-notes/bulk
# Query: booking_numbers=BN1,BN2,BN3,...
# Returns a map { booking_number: notes_doc }
# Used by the frontend to batch-load notes for all visible cards.
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.route("/defendant-notes/bulk", methods=["GET"])
async def bulk_get_notes():
    raw = request.args.get("booking_numbers", "")
    booking_numbers = [b.strip() for b in raw.split(",") if b.strip()]
    if not booking_numbers:
        return jsonify({})

    col = get_collection("defendant_notes")
    result = {}
    async for doc in col.find(
        {"booking_number": {"$in": booking_numbers}}, {"_id": 0}
    ):
        result[doc["booking_number"]] = doc
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────────
# POST  /api/finalize-bond/step1/<booking_number>
# Step 1: Review — returns a summary for the agent to confirm
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.route("/finalize-bond/step1/<booking_number>", methods=["POST"])
async def finalize_bond_step1(booking_number: str):
    arrests = get_collection("arrests")
    notes_col = get_collection("defendant_notes")

    arrest = await arrests.find_one({"booking_number": booking_number}, {"_id": 0})
    if not arrest:
        return jsonify({"success": False, "error": "Defendant not found"}), 404

    notes = await _get_notes_doc(booking_number)
    body = await request.get_json(silent=True) or {}

    # Build review checklist
    checklist = {
        "defendant_name":    arrest.get("full_name", "—"),
        "booking_number":    booking_number,
        "county":            arrest.get("county", "—"),
        "bond_amount":       arrest.get("bond_amount", 0),
        "charges":           arrest.get("charges", "—"),
        "insurance_company": body.get("insurance_company", notes.get("insurance_company", "—")),
        "premium":           round((arrest.get("bond_amount", 0) or 0) * 0.10, 2),
        "poa_number":        body.get("poa_number", notes.get("poa_number", "")),
        "indemnitor_name":   body.get("indemnitor_name", notes.get("indemnitor_name", "")),
        "indemnitor_phone":  body.get("indemnitor_phone", notes.get("indemnitor_phone", "")),
        "court_date":        arrest.get("court_date", "—"),
        "case_number":       arrest.get("case_number", "—"),
        "agent":             body.get("agent", notes.get("agent", "")),
        "notes":             notes.get("shamrock_notes", ""),
        "review_token":      f"REVIEW-{booking_number}-{int(datetime.now().timestamp())}",
    }

    # Save partial finalization data
    await notes_col.update_one(
        {"booking_number": booking_number},
        {"$set": {
            "finalization_step1": checklist,
            "finalization_step1_at": _now(),
            "updated_at": _now(),
        }},
        upsert=True,
    )

    return jsonify({"success": True, "review": checklist})


# ─────────────────────────────────────────────────────────────────────────────
# POST  /api/finalize-bond/step2/<booking_number>
# Step 2: Confirm — marks the bond as finalized
# Body:
#   review_token   — must match the token from step1
#   confirmed_by   — agent name
#   poa_number     — final POA number
#   notes          — any final notes
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.route("/finalize-bond/step2/<booking_number>", methods=["POST"])
async def finalize_bond_step2(booking_number: str):
    arrests = get_collection("arrests")
    notes_col = get_collection("defendant_notes")
    active_bonds = get_collection("active_bonds")

    body = await request.get_json(silent=True) or {}
    notes = await _get_notes_doc(booking_number)

    step1 = notes.get("finalization_step1", {})
    if not step1:
        return jsonify({"success": False, "error": "Step 1 review not found. Please complete Step 1 first."}), 400

    # Token validation (soft check — tokens are informational, not cryptographic)
    provided_token = body.get("review_token", "")
    expected_token = step1.get("review_token", "")
    if provided_token and expected_token and provided_token != expected_token:
        return jsonify({"success": False, "error": "Review token mismatch. Please restart the finalization process."}), 400

    arrest = await arrests.find_one({"booking_number": booking_number}, {"_id": 0})
    if not arrest:
        return jsonify({"success": False, "error": "Defendant not found"}), 404

    finalized_at = _now()
    confirmed_by = body.get("confirmed_by", step1.get("agent", ""))
    poa_number = body.get("poa_number", step1.get("poa_number", ""))
    final_notes = body.get("notes", step1.get("notes", ""))

    # Build active bond record
    active_bond = {
        "booking_number":    booking_number,
        "defendant_name":    arrest.get("full_name", ""),
        "county":            arrest.get("county", ""),
        "bond_amount":       arrest.get("bond_amount", 0),
        "premium":           round((arrest.get("bond_amount", 0) or 0) * 0.10, 2),
        "insurance_company": step1.get("insurance_company", ""),
        "poa_number":        poa_number,
        "indemnitor_name":   step1.get("indemnitor_name", ""),
        "indemnitor_phone":  step1.get("indemnitor_phone", ""),
        "court_date":        arrest.get("court_date", ""),
        "case_number":       arrest.get("case_number", ""),
        "charges":           arrest.get("charges", ""),
        "agent":             confirmed_by,
        "notes":             final_notes,
        "finalized_at":      finalized_at,
        "bond_status":       "active",
        "dob":               arrest.get("dob", ""),
        "address":           arrest.get("address", ""),
        "city":              arrest.get("city", ""),
        "state":             arrest.get("state", ""),
        "zip":               arrest.get("zip", ""),
    }

    # Upsert into active_bonds
    await active_bonds.update_one(
        {"booking_number": booking_number},
        {"$set": active_bond},
        upsert=True,
    )

    # Update defendant notes
    await notes_col.update_one(
        {"booking_number": booking_number},
        {"$set": {
            "shamrock_status":   "bonded",
            "bond_finalized":    True,
            "bond_finalized_at": finalized_at,
            "confirmed_by":      confirmed_by,
            "poa_number":        poa_number,
            "updated_at":        finalized_at,
        }},
        upsert=True,
    )

    return jsonify({
        "success": True,
        "message": f"Bond finalized for {arrest.get('full_name', booking_number)}",
        "finalized_at": finalized_at,
        "active_bond": active_bond,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET  /api/dnb-list   — returns all defendants marked DNB or DNC
# ─────────────────────────────────────────────────────────────────────────────
@lifecycle_bp.route("/dnb-list", methods=["GET"])
async def get_dnb_list():
    col = get_collection("defendant_notes")
    results = []
    async for doc in col.find(
        {"$or": [{"dnb": True}, {"dnc": True}]}, {"_id": 0}
    ).sort("updated_at", -1):
        results.append(doc)
    return jsonify({"count": len(results), "records": results})
