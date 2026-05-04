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
