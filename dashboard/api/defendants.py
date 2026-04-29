"""
ShamrockLeads — Defendants API Blueprint (Phase 2)

Endpoints:
  GET  /api/defendants                         — paginated list (normalized collection w/ arrests fallback)
  GET  /api/defendants/stats                   — collection-level stats
  GET  /api/defendants/lookup                  — identity lookup by name + DOB (Phase 4 hook)
  GET  /api/defendants/<defendant_id>          — single defendant profile
  GET  /api/defendants/<defendant_id>/arrests  — all linked arrest records
  POST /api/defendants/normalize               — normalize one arrest into defendants collection
  POST /api/defendants/normalize/batch         — backfill normalization for unlinked arrests
  PATCH /api/defendants/<defendant_id>/contact — update phone/email/address
  POST /api/defendants/merge                   — manually merge two defendant records
"""
import logging
from datetime import datetime
from quart import Blueprint, jsonify, request, current_app
from dashboard.extensions import get_collection
from dashboard.services.defendant_normalizer import (
    DefendantNormalizationService,
    normalize_name_part,
    normalize_dob,
)

logger = logging.getLogger(__name__)
defendants_bp = Blueprint("defendants", __name__)


def _get_svc() -> DefendantNormalizationService:
    """Instantiate the normalization service with the current app's DB."""
    return DefendantNormalizationService(current_app.db)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/defendants
# ─────────────────────────────────────────────────────────────────────────────
@defendants_bp.route("/defendants")
async def api_defendants():
    """
    Paginated defendant profiles.
    Serves from the normalized `defendants` collection when populated;
    falls back to the `arrests` collection during bootstrap.
    """
    svc = _get_svc()

    county = request.args.get("county", "")
    search = request.args.get("search", "")
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 20))
    min_arrests = int(request.args.get("min_arrests", 0))

    defendants_col = get_collection("defendants")
    total_defendants = await defendants_col.estimated_document_count()

    if total_defendants > 0:
        result = await svc.search_defendants(
            query_str=search,
            county=county,
            page=page,
            limit=limit,
            min_arrests=min_arrests,
        )
        return jsonify(result)

    # ── Bootstrap fallback: serve from arrests collection ──
    arrests = get_collection("arrests")
    sort_by = request.args.get("sort", "bond_amount")
    sort_dir = int(request.args.get("dir", -1))
    min_bond = request.args.get("min_bond", "")

    query = {}
    if county:
        query["county"] = county
    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"charges": {"$regex": search, "$options": "i"}},
            {"booking_number": {"$regex": search, "$options": "i"}},
            {"address": {"$regex": search, "$options": "i"}},
            {"case_number": {"$regex": search, "$options": "i"}},
        ]
    if min_bond:
        try:
            query["bond_amount"] = {"$gte": float(min_bond)}
        except ValueError:
            pass

    total = await arrests.count_documents(query)
    cursor = (
        arrests.find(query, {"_id": 0})
        .sort(sort_by, sort_dir)
        .skip((page - 1) * limit)
        .limit(limit)
    )
    results = []
    async for doc in cursor:
        for k, v in doc.items():
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
        results.append(doc)

    return jsonify({
        "defendants": results,
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
        "source": "arrests_fallback",
        "note": "Run POST /api/defendants/normalize/batch to build the defendants collection.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/defendants/stats
# ─────────────────────────────────────────────────────────────────────────────
@defendants_bp.route("/defendants/stats")
async def defendants_stats():
    """Collection-level statistics for the defendants collection."""
    try:
        defendants_col = get_collection("defendants")
        arrests_col = get_collection("arrests")

        total_defendants = await defendants_col.count_documents({"active": {"$ne": False}})
        total_arrests = await arrests_col.estimated_document_count()
        linked_arrests = await arrests_col.count_documents({"defendant_id": {"$exists": True}})
        unlinked_arrests = total_arrests - linked_arrests
        repeat_offenders = await defendants_col.count_documents(
            {"total_arrests": {"$gte": 2}, "active": {"$ne": False}}
        )

        pipeline = [
            {"$match": {"active": {"$ne": False}}},
            {"$unwind": "$counties"},
            {"$group": {"_id": "$counties", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]
        top_counties = []
        async for row in defendants_col.aggregate(pipeline):
            top_counties.append({"county": row["_id"], "defendants": row["count"]})

        return jsonify({
            "total_defendants": total_defendants,
            "total_arrests": total_arrests,
            "linked_arrests": linked_arrests,
            "unlinked_arrests": unlinked_arrests,
            "repeat_offenders": repeat_offenders,
            "normalization_coverage_pct": (
                round(linked_arrests / total_arrests * 100, 1) if total_arrests else 0
            ),
            "top_counties": top_counties,
        })
    except Exception as exc:
        logger.exception("defendants_stats error")
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/defendants/lookup
# ─────────────────────────────────────────────────────────────────────────────
@defendants_bp.route("/defendants/lookup")
async def lookup_defendant():
    """
    Identity lookup by normalized name + DOB.
    Used by the intake matching engine (Phase 4 hook).
    Query params: last_name, first_name, dob
    """
    try:
        last = request.args.get("last_name", "")
        first = request.args.get("first_name", "")
        dob = request.args.get("dob", "")

        if not last or not first:
            return jsonify({"error": "last_name and first_name are required"}), 400

        from dashboard.services.defendant_normalizer import make_identity_key
        identity_key = make_identity_key(last, first, dob)

        defendants_col = get_collection("defendants")
        doc = await defendants_col.find_one(
            {"identity_key": identity_key, "active": {"$ne": False}},
            {"_id": 0},
        )
        if doc:
            return jsonify({"found": True, "defendant": doc, "match_type": "exact"})

        # Fuzzy fallback
        svc = _get_svc()
        norm = {
            "first_name": first.strip().title(),
            "last_name": last.strip().title(),
            "norm_first": normalize_name_part(first),
            "norm_last": normalize_name_part(last),
            "dob": normalize_dob(dob),
        }
        fuzzy = await svc._fuzzy_lookup(norm)
        if fuzzy:
            return jsonify({"found": True, "defendant": fuzzy, "match_type": "fuzzy"})

        return jsonify({"found": False, "identity_key": identity_key})

    except Exception as exc:
        logger.exception("lookup_defendant error")
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/defendants/<defendant_id>
# ─────────────────────────────────────────────────────────────────────────────
@defendants_bp.route("/defendants/<defendant_id>")
async def get_defendant(defendant_id: str):
    """Fetch a single defendant profile by UUID."""
    svc = _get_svc()
    doc = await svc.get_defendant(defendant_id)
    if not doc:
        return jsonify({"error": "Defendant not found", "defendant_id": defendant_id}), 404
    return jsonify(doc)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/defendants/<defendant_id>/arrests
# ─────────────────────────────────────────────────────────────────────────────
@defendants_bp.route("/defendants/<defendant_id>/arrests")
async def get_defendant_arrests(defendant_id: str):
    """Return all arrest records linked to a defendant UUID."""
    svc = _get_svc()
    arrests = await svc.get_defendant_arrests(defendant_id)
    return jsonify({
        "defendant_id": defendant_id,
        "arrests": arrests,
        "total": len(arrests),
    })


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/defendants/normalize
# ─────────────────────────────────────────────────────────────────────────────
@defendants_bp.route("/defendants/normalize", methods=["POST"])
async def normalize_single():
    """
    Normalize one arrest record into the defendants collection.
    Body: { "county": "Lee", "booking_number": "2024-00001" }
      OR  { "arrest_doc": { ...full arrest document... } }
    """
    try:
        data = await request.get_json()
        if not data:
            return jsonify({"error": "Request body required"}), 400

        svc = _get_svc()

        if "arrest_doc" in data:
            arrest_doc = data["arrest_doc"]
        else:
            county = data.get("county", "").strip()
            booking_number = data.get("booking_number", "").strip()
            if not county or not booking_number:
                return jsonify({
                    "error": "Provide 'arrest_doc' OR both 'county' and 'booking_number'"
                }), 400

            arrests_col = get_collection("arrests")
            arrest_doc = await arrests_col.find_one(
                {"county": county, "booking_number": booking_number},
                {"_id": 0},
            )
            if not arrest_doc:
                return jsonify({"error": f"Arrest not found: {county}/{booking_number}"}), 404

        result = await svc.normalize_arrest(arrest_doc)
        return jsonify({"success": True, **result})

    except Exception as exc:
        logger.exception("normalize_single error")
        return jsonify({"success": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/defendants/normalize/batch
# ─────────────────────────────────────────────────────────────────────────────
@defendants_bp.route("/defendants/normalize/batch", methods=["POST"])
async def normalize_batch():
    """
    Backfill normalization for all arrest records without a defendant_id stamp.
    Body (optional): { "county": "Lee", "limit": 1000 }
    """
    try:
        data = (await request.get_json()) or {}
        county = data.get("county") or None
        limit = min(int(data.get("limit", 500)), 5000)

        svc = _get_svc()
        result = await svc.normalize_batch(county=county, limit=limit)
        return jsonify({"success": True, **result})

    except Exception as exc:
        logger.exception("normalize_batch error")
        return jsonify({"success": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/defendants/<defendant_id>/contact
# ─────────────────────────────────────────────────────────────────────────────
@defendants_bp.route("/defendants/<defendant_id>/contact", methods=["PATCH"])
async def update_contact(defendant_id: str):
    """
    Update contact fields on a defendant record.
    Body: { "phone": "...", "email": "...", "address": "...", "agent": "..." }
    """
    try:
        data = (await request.get_json()) or {}
        svc = _get_svc()
        updated = await svc.update_defendant_contact(
            defendant_id=defendant_id,
            phone=data.get("phone"),
            email=data.get("email"),
            address=data.get("address"),
            agent=data.get("agent", "dashboard"),
        )
        if updated:
            return jsonify({"success": True, "defendant_id": defendant_id})
        return jsonify({"success": False, "error": "Defendant not found or no changes"}), 404

    except Exception as exc:
        logger.exception("update_contact error for %s", defendant_id)
        return jsonify({"success": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/defendants/merge
# ─────────────────────────────────────────────────────────────────────────────
@defendants_bp.route("/defendants/merge", methods=["POST"])
async def merge_defendants():
    """
    Manually merge two defendant records.
    The secondary record is absorbed into the primary and tombstoned.
    Body: { "primary_id": "...", "secondary_id": "...", "agent": "..." }
    """
    try:
        data = (await request.get_json()) or {}
        primary_id = data.get("primary_id", "").strip()
        secondary_id = data.get("secondary_id", "").strip()
        agent = data.get("agent", "dashboard")

        if not primary_id or not secondary_id:
            return jsonify({"error": "Both primary_id and secondary_id are required"}), 400
        if primary_id == secondary_id:
            return jsonify({"error": "primary_id and secondary_id must be different"}), 400

        svc = _get_svc()
        result = await svc.merge_defendants(
            primary_id=primary_id,
            secondary_id=secondary_id,
            agent=agent,
        )
        return jsonify(result)

    except Exception as exc:
        logger.exception("merge_defendants error")
        return jsonify({"success": False, "error": str(exc)}), 500
