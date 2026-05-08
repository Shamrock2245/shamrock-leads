"""
ShamrockLeads — Arrests API Blueprint
Endpoints: /api/counties, /api/counties/<name>/arrests
"""

from datetime import datetime

from quart import Blueprint, jsonify, request
from dashboard.extensions import get_collection, REGISTERED_COUNTIES

arrests_bp = Blueprint("arrests", __name__)


@arrests_bp.route("/counties-detail")
async def api_counties():
    """Return per-county arrest counts + scraper health."""
    arrests = get_collection("arrests")

    pipeline = [
        {"$group": {
            "_id": "$county",
            "total": {"$sum": 1},
            "latest": {"$max": "$scraped_at"},
        }},
        {"$sort": {"_id": 1}},
    ]
    rows = []
    async for row in arrests.aggregate(pipeline):
        rows.append(row)

    county_data = {r["_id"]: r for r in rows}
    result = []
    for county_name in REGISTERED_COUNTIES:
        data = county_data.get(county_name, {})
        result.append({
            "county": county_name,
            "total": data.get("total", 0),
            "latest": data.get("latest", None),
            "active": data.get("total", 0) > 0,
        })

    # Also include any counties in DB not in REGISTERED list
    for name, data in county_data.items():
        if name not in REGISTERED_COUNTIES:
            result.append({
                "county": name,
                "total": data.get("total", 0),
                "latest": data.get("latest", None),
                "active": True,
            })

    return jsonify({"counties": result})


@arrests_bp.route("/counties/<county_name>/arrests")
async def api_county_arrests(county_name):
    """Get arrests for a specific county, sorted newest first."""
    arrests = get_collection("arrests")

    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 50))
    sort_by = request.args.get("sort", "created_at")
    sort_dir = int(request.args.get("dir", -1))
    search = request.args.get("search", "")

    query = {"county": county_name}
    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"charges": {"$regex": search, "$options": "i"}},
        ]

    total = await arrests.count_documents(query)
    cursor = arrests.find(query, {"_id": 0}).sort(
        sort_by, sort_dir
    ).skip((page - 1) * limit).limit(limit)

    results = []
    async for doc in cursor:
        for k, v in doc.items():
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
        results.append(doc)

    return jsonify({
        "county": county_name,
        "arrests": results,
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/arrests/search — Universal search across ALL counties
# Used by Record Bond form to find any defendant and auto-populate fields
# ─────────────────────────────────────────────────────────────────────────────
@arrests_bp.route("/arrests/search")
async def api_arrests_search():
    """
    Search arrests across all counties by name, booking#, case#, or charges.
    Query params:
        q       — search text (required, min 2 chars)
        county  — optional county filter
        limit   — max results (default 20, max 50)
        status  — custody status filter (optional)
    Returns the full arrest record for auto-population.
    """
    q = request.args.get("q", "").strip()
    county = request.args.get("county", "").strip()
    limit = min(int(request.args.get("limit", 20)), 50)
    status = request.args.get("status", "").strip()

    if len(q) < 2:
        return jsonify({"error": "Search query must be at least 2 characters", "arrests": []}), 400

    arrests = get_collection("arrests")

    # Build $or query for flexible search
    search_conditions = [
        {"full_name": {"$regex": q, "$options": "i"}},
        {"booking_number": {"$regex": q, "$options": "i"}},
        {"case_number": {"$regex": q, "$options": "i"}},
        {"charges": {"$regex": q, "$options": "i"}},
    ]

    query = {"$or": search_conditions}
    if county:
        query["county"] = county
    if status:
        query["custody_status"] = {"$regex": status, "$options": "i"}

    total = await arrests.count_documents(query)
    cursor = arrests.find(query, {"_id": 0}).sort(
        "scraped_at", -1
    ).limit(limit)

    results = []
    async for doc in cursor:
        for k, v in doc.items():
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
        results.append(doc)

    return jsonify({
        "arrests": results,
        "total": total,
        "query": q,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/arrests/by-booking/<booking_number> — Direct lookup
# Returns full arrest data for auto-populating the Record Bond form
# ─────────────────────────────────────────────────────────────────────────────
@arrests_bp.route("/arrests/by-booking/<booking_number>")
async def api_arrest_by_booking(booking_number):
    """
    Fetch a single arrest record by exact booking number.
    Returns all fields for form auto-population.
    """
    arrests = get_collection("arrests")
    doc = await arrests.find_one(
        {"booking_number": booking_number},
        {"_id": 0}
    )
    if not doc:
        # Try case-insensitive match
        doc = await arrests.find_one(
            {"booking_number": {"$regex": f"^{booking_number}$", "$options": "i"}},
            {"_id": 0}
        )
    if not doc:
        return jsonify({"found": False, "error": f"No arrest found for booking {booking_number}"}), 404

    for k, v in doc.items():
        if isinstance(v, datetime):
            doc[k] = v.isoformat()

    return jsonify({"found": True, "arrest": doc})

