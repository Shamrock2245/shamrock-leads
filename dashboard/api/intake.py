"""
ShamrockLeads — Indemnitor Intake Queue API Blueprint
Receives indemnitor information from ALL sources that previously fed
Dashboard.html in the GAS project:
  Sources:
    1. wix_portal      — Wix/Velo indemnitor portal (IntakeQueue CMS collection)
    2. telegram        — Telegram Mini App intake form
    3. manual_entry    — Staff manually enters data in the dashboard
    4. walk_in         — Walk-in client (staff enters on their behalf)
    5. phone_call      — Phone intake (staff enters while on call)
    6. bookmarklet     — LCSO bookmarklet auto-scrape
  Endpoints:
    POST /api/intake/submit          — Accept new intake from any source
    GET  /api/intake/queue           — List pending intakes (staff dashboard queue)
    GET  /api/intake/<intake_id>     — Get single intake record
    POST /api/intake/<intake_id>/process  — Mark as processed / hydrate bond form
    POST /api/intake/<intake_id>/archive  — Archive / mark done
    PATCH /api/intake/<intake_id>    — Update intake fields
    GET  /api/intake/stats           — Queue stats (count by source, status)
    POST /api/intake/<intake_id>/match    — Phase 4: Run matching engine on this intake
  Indemnitor Field Schema (mirrors Dashboard.html addIndemnitor() exactly):
    Personal:   firstName, middleName, lastName, relationship, dob, ssn, dl, dlState
    Contact:    phone, email
    Address:    address, city, state, zip
    Employment: employer, employerPhone, employerCity, employerState, supervisor, supervisorPhone
    References: ref1Name, ref1Relation, ref1Phone, ref1Address
                ref2Name, ref2Relation, ref2Phone, ref2Address
    Defendant:  defendantName, defendantDOB, defendantFacility, defendantCounty,
                defendantBookingNumber, defendantCharges, defendantBondAmount
    Meta:       source, platform, timestamp, consentGiven, consentTimestamp,
                telegramUserId, telegramUsername, gpsLatitude, gpsLongitude
"""
from __future__ import annotations
import uuid
import logging
from datetime import datetime, timezone
from quart import Blueprint, request, jsonify, current_app
from dashboard.extensions import get_collection
logger = logging.getLogger(__name__)
intake_bp = Blueprint("intake", __name__)
# ── Valid intake sources ──────────────────────────────────────────────────────
VALID_SOURCES = {
    "wix_portal",
    "telegram",
    "telegram_mini_app",   # alias used by Telegram Mini App
    "manual_entry",
    "walk_in",
    "phone_call",
    "bookmarklet",
    "shamrock-leads-dashboard",
}
SOURCE_LABELS = {
    "wix_portal": "🌐 Wix Portal",
    "telegram": "📱 Telegram",
    "telegram_mini_app": "📱 Telegram Mini App",
    "manual_entry": "✏️ Manual Entry",
    "walk_in": "🚶 Walk-In",
    "phone_call": "📞 Phone Call",
    "bookmarklet": "🔖 Bookmarklet",
    "shamrock-leads-dashboard": "☘️ Dashboard",
}
def _normalize_source(raw: str) -> str:
    """Normalize source string to a canonical value."""
    raw = (raw or "manual_entry").lower().strip().replace(" ", "_").replace("-", "_")
    if raw in VALID_SOURCES:
        return raw
    if "telegram" in raw:
        return "telegram"
    if "wix" in raw or "portal" in raw:
        return "wix_portal"
    if "walk" in raw:
        return "walk_in"
    if "phone" in raw or "call" in raw:
        return "phone_call"
    if "bookmarklet" in raw or "lcso" in raw:
        return "bookmarklet"
    return "manual_entry"


def _extract_indemnitor(data: dict) -> dict:
    """
    Extract and normalize indemnitor fields from any source payload.
    Handles all field-name variants used by Wix, Telegram, GAS, and manual entry.
    Mirrors the Queue.process() hydration logic in Dashboard.html exactly.
    """
    g = lambda *keys: next((str(data.get(k, "")).strip() for k in keys if data.get(k)), "")

    return {
        # Personal
        "firstName":    g("IndFirstName", "indFirstName", "indemnitorFirstName", "firstName", "first_name", "FirstName"),
        "middleName":   g("IndMiddleName", "indemnitorMiddleName", "middleName", "middle_name"),
        "lastName":     g("IndLastName", "indLastName", "indemnitorLastName", "lastName", "last_name", "LastName"),
        "relationship": g("IndRelation", "indRelation", "indemnitorRelation", "relationship", "Relationship", "Role"),
        "dob":          g("IndDOB", "indemnitorDOB", "dob", "DOB"),
        "ssn":          g("IndSSN", "indemnitorSSN", "ssn", "SSN"),
        "dl":           g("IndDL", "indemnitorDL", "dlNumber", "dl", "DL"),
        "dlState":      g("IndDLState", "indemnitorDLState", "dlState", "DLState") or "FL",
        # Contact
        "phone":        g("IndPhone", "indPhone", "indemnitorPhone", "phone", "Phone"),
        "email":        g("IndEmail", "indEmail", "indemnitorEmail", "email", "Email"),
        # Address
        "address":      g("IndAddress", "indemnitorStreetAddress", "indemnitorAddress", "address", "Address"),
        "city":         g("IndCity", "indemnitorCity", "city", "City"),
        "state":        g("IndState", "indemnitorState", "state", "State") or "FL",
        "zip":          g("IndZip", "indemnitorZipCode", "indemnitorZip", "zip", "ZIP"),
        # Employment
        "employer":         g("IndEmployer", "indemnitorEmployerName", "employer", "Employer"),
        "employerPhone":    g("IndEmployerPhone", "indemnitorEmployerPhone", "employerPhone"),
        "employerCity":     g("IndEmployerCity", "indemnitorEmployerCity", "employerCity"),
        "employerState":    g("IndEmployerState", "indemnitorEmployerState", "employerState"),
        "supervisor":       g("IndJobTitle", "indemnitorSupervisorName", "supervisor", "jobTitle"),
        "supervisorPhone":  g("IndSupervisorPhone", "indemnitorSupervisorPhone", "supervisorPhone"),
        # References
        "ref1Name":     g("Ref1Name", "ref1Name", "reference1Name", "IndRef1Name"),
        "ref1Relation": g("Ref1Relation", "ref1Relation", "reference1Relation"),
        "ref1Phone":    g("Ref1Phone", "ref1Phone", "reference1Phone"),
        "ref1Address":  g("Ref1Address", "ref1Address", "reference1Address"),
        "ref2Name":     g("Ref2Name", "ref2Name", "reference2Name", "IndRef2Name"),
        "ref2Relation": g("Ref2Relation", "ref2Relation", "reference2Relation"),
        "ref2Phone":    g("Ref2Phone", "ref2Phone", "reference2Phone"),
        "ref2Address":  g("Ref2Address", "ref2Address", "reference2Address"),
    }


def _extract_defendant(data: dict) -> dict:
    """Extract defendant fields from any source payload."""
    g = lambda *keys: next((str(data.get(k, "")).strip() for k in keys if data.get(k)), "")
    return {
        "name":          g("DefName", "defName", "defendantName", "defendant_name", "DefendantName"),
        "firstName":     g("DefFirstName", "defFirstName", "defendant_first_name"),
        "lastName":      g("DefLastName", "defLastName", "defendant_last_name"),
        "dob":           g("DefDOB", "defDOB", "defendant_dob"),
        "facility":      g("DefFacility", "defFacility", "jailFacility", "facility", "Facility"),
        "county":        g("DefCounty", "defCounty", "county", "County"),
        "bookingNumber": g("DefBookingNumber", "bookingNumber", "booking_number", "Booking_Number"),
        "charges":       g("DefCharges", "defCharges", "charges", "Charges"),
        "bondAmount":    g("DefBondAmount", "defBondAmount", "bondAmount", "bond_amount", "Bond_Amount"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/intake/submit
#  Accept a new indemnitor intake from any source
# ═══════════════════════════════════════════════════════════════════════════════
@intake_bp.route("/intake/submit", methods=["POST"])
async def intake_submit():
    """
    Accept indemnitor intake from any source (Wix, Telegram, manual, walk-in, phone).
    Stores in MongoDB `intake_queue` collection.
    Mirrors handleNewIntake() / storeIntakeInQueue() from GAS WixPortalIntegration.js.
    After storing, auto-triggers Phase 4 matching engine.
    """
    data = await request.get_json(force=True) or {}

    source_raw = (
        data.get("source")
        or data.get("platform")
        or data.get("action", "")
        or "manual_entry"
    )
    source = _normalize_source(source_raw)

    indemnitor = _extract_indemnitor(data)
    defendant = _extract_defendant(data)

    # Build a full name for display
    ind_full_name = " ".join(filter(None, [indemnitor["firstName"], indemnitor["lastName"]])) or "Unknown"
    def_full_name = defendant["name"] or " ".join(filter(None, [defendant["firstName"], defendant["lastName"]])) or "Unknown"

    # Generate unique intake ID (TG- prefix for Telegram, WX- for Wix, IN- for others)
    prefix_map = {
        "telegram": "TG",
        "telegram_mini_app": "TG",
        "wix_portal": "WX",
        "walk_in": "WI",
        "phone_call": "PC",
        "bookmarklet": "BK",
        "manual_entry": "ME",
        "shamrock-leads-dashboard": "SL",
    }
    prefix = prefix_map.get(source, "IN")
    # Allow caller to supply their own ID (e.g. Wix caseId, Telegram TG-xxx)
    intake_id = (
        data.get("intakeId")
        or data.get("caseId")
        or data.get("intake_id")
        or f"{prefix}-{uuid.uuid4().hex[:10].upper()}"
    )

    now = datetime.now(timezone.utc)

    doc = {
        "intake_id": intake_id,
        "source": source,
        "source_label": SOURCE_LABELS.get(source, source),
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        # Indemnitor
        "indemnitor": indemnitor,
        "indemnitor_name": ind_full_name,
        "indemnitor_email": indemnitor.get("email", ""),
        "indemnitor_phone": indemnitor.get("phone", ""),
        # Defendant
        "defendant": defendant,
        "defendant_name": def_full_name,
        "defendant_booking_number": defendant.get("bookingNumber", ""),
        "defendant_county": defendant.get("county", ""),
        "defendant_facility": defendant.get("facility", ""),
        # Consent & Meta
        "consent_given": bool(data.get("consent") or data.get("consentGiven")),
        "consent_timestamp": data.get("consentTimestamp", now.isoformat()),
        "telegram_user_id": data.get("telegramUserId", ""),
        "telegram_username": data.get("telegramUsername", ""),
        "gps_latitude": data.get("gpsLatitude"),
        "gps_longitude": data.get("gpsLongitude"),
        "manual_location": data.get("manualLocation"),
        # AI fields (populated later by risk engine)
        "ai_risk": "",
        "ai_score": None,
        "ai_rationale": "",
        # GAS sync status
        "gas_sync_status": "pending",
        "gas_sync_timestamp": None,
        # Matching fields (populated by Phase 4 matching engine)
        "matched_booking_number": None,
        "matched_county": None,
        "matched_defendant_id": None,
        "match_confidence": None,
        "match_strategy": None,
        "match_timestamp": None,
        # Paperwork fields (populated by Phase 6)
        "paperwork_packet_id": None,
        "paperwork_status": None,
        # Raw payload preserved for full hydration
        "_raw": data,
    }

    intake_queue = get_collection("intake_queue")
    try:
        # Upsert by intake_id to prevent duplicates
        await intake_queue.update_one(
            {"intake_id": intake_id},
            {"$set": doc},
            upsert=True,
        )
        logger.info(f"[intake] New intake stored: {intake_id} | source={source} | defendant={def_full_name}")

        # ── Phase 4: Auto-trigger matching engine ─────────────────────────
        match_result = None
        try:
            from dashboard.services.matching_engine import MatchingEngine
            engine = MatchingEngine(current_app.db)
            match_result = await engine.match_intake(doc)
            logger.info(
                "[intake] Auto-match for %s: confidence=%s strategy=%s auto_linked=%s",
                intake_id,
                match_result.get("confidence"),
                match_result.get("strategy"),
                match_result.get("auto_linked"),
            )
        except Exception as match_err:
            logger.warning("[intake] Auto-match failed for %s: %s", intake_id, match_err)

        return jsonify({
            "success": True,
            "intake_id": intake_id,
            "source": source,
            "defendant_name": def_full_name,
            "indemnitor_name": ind_full_name,
            "message": f"Intake received from {SOURCE_LABELS.get(source, source)}",
            "match": match_result,
        })
    except Exception as e:
        logger.error(f"[intake] Failed to store intake {intake_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/intake/queue
#  Return pending intakes for the staff dashboard queue
# ═══════════════════════════════════════════════════════════════════════════════
@intake_bp.route("/intake/queue", methods=["GET"])
async def intake_queue_list():
    """
    Return pending intakes for the staff dashboard queue.
    Mirrors getWixIntakeQueue() from GAS WixPortalIntegration.js.
    Returns the same schema expected by Queue.render() in Dashboard.html.
    """
    intake_queue = get_collection("intake_queue")
    status_filter = request.args.get("status", "pending")
    limit = min(int(request.args.get("limit", 50)), 200)
    source_filter = request.args.get("source", "")

    query: dict = {}
    if status_filter and status_filter != "all":
        query["status"] = status_filter
    if source_filter:
        query["source"] = _normalize_source(source_filter)

    try:
        cursor = intake_queue.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
        items = await cursor.to_list(length=limit)

        # Transform to Dashboard.html Queue.render() schema
        result = []
        for item in items:
            ind = item.get("indemnitor", {})
            def_ = item.get("defendant", {})
            raw = item.get("_raw", {})
            result.append({
                "IntakeID": item["intake_id"],
                "DefendantName": item.get("defendant_name", "Unknown"),
                "FullName": item.get("indemnitor_name", "Unknown"),
                "FirstName": ind.get("firstName", ""),
                "LastName": ind.get("lastName", ""),
                "Email": item.get("indemnitor_email", ""),
                "Phone": item.get("indemnitor_phone", ""),
                "Role": ind.get("relationship", "Indemnitor"),
                "Status": item.get("status", "pending"),
                "Timestamp": item["created_at"].isoformat() if hasattr(item.get("created_at"), "isoformat") else str(item.get("created_at", "")),
                "Source": item.get("source", ""),
                "SourceLabel": item.get("source_label", ""),
                "County": item.get("defendant_county", ""),
                "BookingNumber": item.get("defendant_booking_number", ""),
                # Matching fields
                "MatchedBookingNumber": item.get("matched_booking_number"),
                "MatchConfidence": item.get("match_confidence"),
                "MatchStrategy": item.get("match_strategy"),
                # Paperwork fields
                "PaperworkPacketId": item.get("paperwork_packet_id"),
                "PaperworkStatus": item.get("paperwork_status"),
                # AI fields
                "AI_Risk": item.get("ai_risk", ""),
                "AI_Score": item.get("ai_score"),
                "AI_Rationale": item.get("ai_rationale", ""),
                # Full indemnitor data for hydration
                "_original": {
                    **raw,
                    # Ensure all normalized indemnitor fields are present
                    "indemnitorFirstName": ind.get("firstName", ""),
                    "indemnitorMiddleName": ind.get("middleName", ""),
                    "indemnitorLastName": ind.get("lastName", ""),
                    "indemnitorDOB": ind.get("dob", ""),
                    "indemnitorSSN": ind.get("ssn", ""),
                    "indemnitorDL": ind.get("dl", ""),
                    "indemnitorDLState": ind.get("dlState", "FL"),
                    "indemnitorStreetAddress": ind.get("address", ""),
                    "indemnitorCity": ind.get("city", ""),
                    "indemnitorState": ind.get("state", "FL"),
                    "indemnitorZipCode": ind.get("zip", ""),
                    "indemnitorPhone": ind.get("phone", ""),
                    "indemnitorEmail": ind.get("email", ""),
                    "indemnitorEmployerName": ind.get("employer", ""),
                    "indemnitorEmployerPhone": ind.get("employerPhone", ""),
                    "indemnitorEmployerCity": ind.get("employerCity", ""),
                    "indemnitorEmployerState": ind.get("employerState", ""),
                    "indemnitorSupervisorName": ind.get("supervisor", ""),
                    "indemnitorSupervisorPhone": ind.get("supervisorPhone", ""),
                    "reference1Name": ind.get("ref1Name", ""),
                    "reference1Relation": ind.get("ref1Relation", ""),
                    "reference1Phone": ind.get("ref1Phone", ""),
                    "reference1Address": ind.get("ref1Address", ""),
                    "reference2Name": ind.get("ref2Name", ""),
                    "reference2Relation": ind.get("ref2Relation", ""),
                    "reference2Phone": ind.get("ref2Phone", ""),
                    "reference2Address": ind.get("ref2Address", ""),
                },
            })

        return jsonify({
            "success": True,
            "intakes": result,
            "count": len(result),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.error(f"[intake] queue list error: {e}")
        return jsonify({"success": False, "error": str(e), "intakes": []}), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/intake/<intake_id>
#  Get a single intake record by ID
# ═══════════════════════════════════════════════════════════════════════════════
@intake_bp.route("/intake/<intake_id>", methods=["GET"])
async def intake_get(intake_id: str):
    """Fetch a single intake record by ID."""
    intake_queue = get_collection("intake_queue")
    try:
        item = await intake_queue.find_one({"intake_id": intake_id}, {"_id": 0})
        if not item:
            return jsonify({"success": False, "error": f"Intake {intake_id} not found"}), 404
        # Serialize datetime
        if hasattr(item.get("created_at"), "isoformat"):
            item["created_at"] = item["created_at"].isoformat()
        if hasattr(item.get("updated_at"), "isoformat"):
            item["updated_at"] = item["updated_at"].isoformat()
        return jsonify({"success": True, "intake": item})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/intake/<intake_id>/match
#  Phase 4: Run the matching engine on a specific intake record
# ═══════════════════════════════════════════════════════════════════════════════
@intake_bp.route("/intake/<intake_id>/match", methods=["POST"])
async def intake_match(intake_id: str):
    """
    Run the Phase 4 matching engine on a specific intake record.
    Returns best match + candidates for staff review.
    """
    intake_queue = get_collection("intake_queue")
    try:
        intake_doc = await intake_queue.find_one({"intake_id": intake_id}, {"_id": 0})
        if not intake_doc:
            return jsonify({"success": False, "error": f"Intake {intake_id} not found"}), 404

        from dashboard.services.matching_engine import MatchingEngine
        engine = MatchingEngine(current_app.db)
        result = await engine.match_intake(intake_doc)
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"[intake] match error for {intake_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/intake/<intake_id>/process
#  Mark intake as in_progress and return full hydration payload
# ═══════════════════════════════════════════════════════════════════════════════
@intake_bp.route("/intake/<intake_id>/process", methods=["POST"])
async def intake_process(intake_id: str):
    """
    Mark intake as in_progress and return the full hydration payload.
    Called when staff clicks 'Process' in the queue.
    Mirrors Queue.process() from Dashboard.html.
    """
    intake_queue = get_collection("intake_queue")
    try:
        now = datetime.now(timezone.utc)
        result = await intake_queue.find_one_and_update(
            {"intake_id": intake_id},
            {"$set": {"status": "in_progress", "updated_at": now, "processed_at": now}},
            return_document=True,
        )
        if not result:
            return jsonify({"success": False, "error": f"Intake {intake_id} not found"}), 404

        ind = result.get("indemnitor", {})
        def_ = result.get("defendant", {})

        # Return the hydration payload that the frontend uses to populate the bond form
        return jsonify({
            "success": True,
            "intake_id": intake_id,
            "status": "in_progress",
            "hydration": {
                "indemnitor": {
                    "firstName": ind.get("firstName", ""),
                    "middleName": ind.get("middleName", ""),
                    "lastName": ind.get("lastName", ""),
                    "relationship": ind.get("relationship", ""),
                    "dob": ind.get("dob", ""),
                    "ssn": ind.get("ssn", ""),
                    "dl": ind.get("dl", ""),
                    "dlState": ind.get("dlState", "FL"),
                    "phone": ind.get("phone", ""),
                    "email": ind.get("email", ""),
                    "address": ind.get("address", ""),
                    "city": ind.get("city", ""),
                    "state": ind.get("state", "FL"),
                    "zip": ind.get("zip", ""),
                    "employer": ind.get("employer", ""),
                    "employerPhone": ind.get("employerPhone", ""),
                    "employerCity": ind.get("employerCity", ""),
                    "employerState": ind.get("employerState", ""),
                    "supervisor": ind.get("supervisor", ""),
                    "supervisorPhone": ind.get("supervisorPhone", ""),
                    "ref1Name": ind.get("ref1Name", ""),
                    "ref1Relation": ind.get("ref1Relation", ""),
                    "ref1Phone": ind.get("ref1Phone", ""),
                    "ref1Address": ind.get("ref1Address", ""),
                    "ref2Name": ind.get("ref2Name", ""),
                    "ref2Relation": ind.get("ref2Relation", ""),
                    "ref2Phone": ind.get("ref2Phone", ""),
                    "ref2Address": ind.get("ref2Address", ""),
                },
                "defendant": {
                    "name": def_.get("name", ""),
                    "firstName": def_.get("firstName", ""),
                    "lastName": def_.get("lastName", ""),
                    "dob": def_.get("dob", ""),
                    "facility": def_.get("facility", ""),
                    "county": def_.get("county", ""),
                    "bookingNumber": def_.get("bookingNumber", ""),
                    "charges": def_.get("charges", ""),
                    "bondAmount": def_.get("bondAmount", ""),
                },
                "source": result.get("source", ""),
                "source_label": result.get("source_label", ""),
                "consent_given": result.get("consent_given", False),
                "gps_latitude": result.get("gps_latitude"),
                "gps_longitude": result.get("gps_longitude"),
                # Matching context
                "matched_booking_number": result.get("matched_booking_number"),
                "match_confidence": result.get("match_confidence"),
                "match_strategy": result.get("match_strategy"),
            },
        })
    except Exception as e:
        logger.error(f"[intake] process error for {intake_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/intake/<intake_id>/archive
#  Mark intake as done / archived (remove from queue)
# ═══════════════════════════════════════════════════════════════════════════════
@intake_bp.route("/intake/<intake_id>/archive", methods=["POST"])
async def intake_archive(intake_id: str):
    """
    Mark intake as archived (done).
    Mirrors Queue.archive() from Dashboard.html.
    """
    intake_queue = get_collection("intake_queue")
    try:
        now = datetime.now(timezone.utc)
        result = await intake_queue.update_one(
            {"intake_id": intake_id},
            {"$set": {"status": "archived", "updated_at": now, "archived_at": now}},
        )
        if result.matched_count == 0:
            return jsonify({"success": False, "error": f"Intake {intake_id} not found"}), 404
        return jsonify({"success": True, "intake_id": intake_id, "status": "archived"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  PATCH /api/intake/<intake_id>
#  Update intake fields (e.g., AI risk score, GAS sync status)
# ═══════════════════════════════════════════════════════════════════════════════
@intake_bp.route("/intake/<intake_id>", methods=["PATCH"])
async def intake_update(intake_id: str):
    """Update intake fields. Used by AI risk engine and GAS sync callbacks."""
    intake_queue = get_collection("intake_queue")
    data = await request.get_json(force=True) or {}
    allowed_fields = {
        "status", "ai_risk", "ai_score", "ai_rationale",
        "gas_sync_status", "gas_sync_timestamp",
        "defendant_booking_number", "defendant_county",
        "notes", "matched_booking_number",
        "paperwork_packet_id", "paperwork_status",
    }
    updates = {k: v for k, v in data.items() if k in allowed_fields}
    if not updates:
        return jsonify({"success": False, "error": "No valid fields to update"}), 400
    updates["updated_at"] = datetime.now(timezone.utc)
    try:
        result = await intake_queue.update_one(
            {"intake_id": intake_id},
            {"$set": updates},
        )
        if result.matched_count == 0:
            return jsonify({"success": False, "error": f"Intake {intake_id} not found"}), 404
        return jsonify({"success": True, "intake_id": intake_id, "updated": list(updates.keys())})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/intake/stats
#  Queue statistics by source and status
# ═══════════════════════════════════════════════════════════════════════════════
@intake_bp.route("/intake/stats", methods=["GET"])
async def intake_stats():
    """Return queue statistics — count by source and status."""
    intake_queue = get_collection("intake_queue")
    try:
        pipeline = [
            {"$group": {
                "_id": {"source": "$source", "status": "$status"},
                "count": {"$sum": 1},
            }},
            {"$sort": {"_id.source": 1, "_id.status": 1}},
        ]
        cursor = intake_queue.aggregate(pipeline)
        rows = await cursor.to_list(length=200)

        total_pending = await intake_queue.count_documents({"status": "pending"})
        total_all = await intake_queue.estimated_document_count()
        total_matched = await intake_queue.count_documents(
            {"matched_booking_number": {"$exists": True, "$ne": None}}
        )

        by_source: dict = {}
        by_status: dict = {}
        for row in rows:
            src = row["_id"]["source"]
            sts = row["_id"]["status"]
            cnt = row["count"]
            by_source.setdefault(src, 0)
            by_source[src] += cnt
            by_status.setdefault(sts, 0)
            by_status[sts] += cnt

        return jsonify({
            "success": True,
            "total": total_all,
            "pending": total_pending,
            "matched": total_matched,
            "by_source": by_source,
            "by_status": by_status,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
