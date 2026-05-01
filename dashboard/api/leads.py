"""
ShamrockLeads — Leads API Blueprint
Endpoints: /api/leads, /api/leads/export
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from quart import Blueprint, jsonify, request, Response
from dashboard.extensions import get_collection

leads_bp = Blueprint("leads", __name__)


@leads_bp.route("/leads")
async def api_leads():
    """
    Paginated lead data with filtering, sorting, search.
    Query params: county, status, days, page, limit, sort, dir, search, min_bond
    """
    arrests = get_collection("arrests")

    county = request.args.get("county", "")
    status = request.args.get("status", "")
    days = int(request.args.get("days", 0))
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 50))
    sort_by = request.args.get("sort", "scraped_at")
    sort_dir = int(request.args.get("dir", -1))
    search = request.args.get("search", "")
    min_bond = request.args.get("min_bond", "")

    query = {}
    if county:
        query["county"] = county
    if status:
        query["lead_status"] = status
    if days > 0:
        from datetime import timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query["scraped_at"] = {"$gte": cutoff}
    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"charges": {"$regex": search, "$options": "i"}},
            {"booking_number": {"$regex": search, "$options": "i"}},
        ]
    if min_bond:
        try:
            query["bond_amount"] = {"$gte": float(min_bond)}
        except ValueError:
            pass

    # Feature D: Has Indemnitor filter — cross-reference bonds collections
    has_indemnitor = request.args.get("has_indemnitor", "").lower()
    if has_indemnitor == "true":
        active = get_collection("active_bonds")
        prospective = get_collection("prospective_bonds")
        # Find booking numbers that have indemnitor info
        ind_bookings = set()
        async for doc in active.find(
            {"$or": [
                {"indemnitor.phone": {"$exists": True, "$ne": ""}},
                {"indemnitor_phone": {"$exists": True, "$ne": ""}},
                {"indemnitors.0": {"$exists": True}},
            ]},
            {"booking_number": 1},
        ):
            if doc.get("booking_number"):
                ind_bookings.add(doc["booking_number"])
        async for doc in prospective.find(
            {"$or": [
                {"indemnitor.phone": {"$exists": True, "$ne": ""}},
                {"indemnitor_phone": {"$exists": True, "$ne": ""}},
                {"indemnitors.0": {"$exists": True}},
            ]},
            {"booking_number": 1},
        ):
            if doc.get("booking_number"):
                ind_bookings.add(doc["booking_number"])
        if ind_bookings:
            query["booking_number"] = {"$in": list(ind_bookings)}
        else:
            # No matches — return empty
            return jsonify({"leads": [], "total": 0, "page": 1, "pages": 1})

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
        "leads": results,
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    })


@leads_bp.route("/leads/export")
async def api_leads_export():
    """Export leads as CSV."""
    arrests = get_collection("arrests")

    county = request.args.get("county", "")
    status = request.args.get("status", "")
    days = int(request.args.get("days", 0))

    query = {}
    if county:
        query["county"] = county
    if status:
        query["lead_status"] = status
    if days > 0:
        from datetime import timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query["scraped_at"] = {"$gte": cutoff}

    cursor = arrests.find(query, {"_id": 0}).sort("scraped_at", -1).limit(5000)

    output = io.StringIO()
    writer = None
    async for doc in cursor:
        for k, v in doc.items():
            if isinstance(v, (list, dict)):
                doc[k] = str(v)
            elif isinstance(v, datetime):
                doc[k] = v.isoformat()
        if writer is None:
            writer = csv.DictWriter(output, fieldnames=list(doc.keys()))
            writer.writeheader()
        writer.writerow(doc)

    content = output.getvalue()
    return Response(
        content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=shamrock_leads_export.csv"},
    )
