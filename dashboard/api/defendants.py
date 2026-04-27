"""
ShamrockLeads — Defendants API Blueprint
Endpoints: /api/defendants
"""

from datetime import datetime

from quart import Blueprint, jsonify, request
from dashboard.extensions import get_collection

defendants_bp = Blueprint("defendants", __name__)


@defendants_bp.route("/defendants")
async def api_defendants():
    """Full defendant profiles with all booking sheet fields."""
    arrests = get_collection("arrests")

    county = request.args.get("county", "")
    search = request.args.get("search", "")
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 20))
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
        "defendants": results,
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    })
