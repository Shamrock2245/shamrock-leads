"""
ShamrockLeads — POA Inventory API Blueprint
Endpoints: /api/poa/next, /api/poa/assign, /api/poa/inventory
"""

from datetime import datetime, timezone

from quart import Blueprint, jsonify, request
from dashboard.extensions import get_collection
from dashboard.services.poa_service import get_poa_tier_for_bond

poa_bp = Blueprint("poa", __name__)


@poa_bp.route("/poa/next", methods=["GET"])
async def api_poa_next():
    """
    Suggest the next available POA number(s) for a given surety + bond amount.
    Query params: surety, bond_amount, count
    """
    poa_inventory = get_collection("poa_inventory")

    surety = (request.args.get("surety") or "").lower().strip()
    if surety not in ("osi", "palmetto"):
        return jsonify({"error": "surety must be 'osi' or 'palmetto'"}), 400
    try:
        bond_amount = float(request.args.get("bond_amount", 0) or 0)
    except ValueError:
        bond_amount = 0.0
    count = max(1, int(request.args.get("count", 1) or 1))

    prefix = get_poa_tier_for_bond(surety, bond_amount)

    cursor = poa_inventory.find(
        {"surety_id": surety, "poa_prefix": prefix, "status": "available"},
        {"poa_number": 1, "poa_prefix": 1, "poa_full": 1, "_id": 0},
    ).sort("poa_number", 1).limit(count)
    suggested = []
    async for doc in cursor:
        suggested.append(doc)

    total_available = await poa_inventory.count_documents(
        {"surety_id": surety, "poa_prefix": prefix, "status": "available"}
    )
    total_surety = await poa_inventory.count_documents(
        {"surety_id": surety, "status": "available"}
    )

    return jsonify({
        "surety": surety,
        "prefix": prefix,
        "bond_amount": bond_amount,
        "available_in_tier": total_available,
        "available_total": total_surety,
        "suggested": suggested,
        "warning": ("Low inventory in this tier" if total_available <= 3 else None),
    })


@poa_bp.route("/poa/assign", methods=["POST"])
async def api_poa_assign():
    """Mark a POA as assigned to a bond case."""
    poa_inventory = get_collection("poa_inventory")

    body = (await request.get_json(force=True)) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    poa_prefix = str(body.get("poa_prefix", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()
    bond_case_id = body.get("bond_case_id") or body.get("booking_number", "")

    if not poa_number or not surety_id:
        return jsonify({"error": "poa_number and surety_id are required"}), 400

    doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return jsonify({"error": f"POA {poa_number} not found for surety {surety_id}"}), 404
    if doc.get("status") != "available":
        return jsonify({"error": f"POA {poa_number} is already {doc.get('status')} — cannot assign"}), 409

    await poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {
            "status": "assigned",
            "bond_case_id": str(bond_case_id),
            "used_at": datetime.now(timezone.utc).isoformat(),
        }},
    )

    remaining = await poa_inventory.count_documents(
        {"surety_id": surety_id, "poa_prefix": doc.get("poa_prefix", poa_prefix), "status": "available"}
    )

    return jsonify({
        "success": True,
        "poa_number": poa_number,
        "poa_prefix": doc.get("poa_prefix", poa_prefix),
        "poa_full": doc.get("poa_full", f"{poa_prefix} {poa_number}"),
        "surety_id": surety_id,
        "bond_case_id": str(bond_case_id),
        "remaining_in_tier": remaining,
    })


@poa_bp.route("/poa/inventory", methods=["GET"])
async def api_poa_inventory():
    """Return a summary of available POA inventory by surety and tier."""
    poa_inventory = get_collection("poa_inventory")

    surety_filter = (request.args.get("surety") or "").lower().strip()
    match = {"status": "available"}
    if surety_filter in ("osi", "palmetto"):
        match["surety_id"] = surety_filter

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {"surety_id": "$surety_id", "poa_prefix": "$poa_prefix", "max_bond_value": "$max_bond_value"},
            "available": {"$sum": 1},
            "next_serial": {"$min": "$poa_number"},
        }},
        {"$sort": {"_id.surety_id": 1, "_id.max_bond_value": 1}},
    ]
    result = []
    async for r in poa_inventory.aggregate(pipeline):
        result.append({
            "surety_id": r["_id"]["surety_id"],
            "poa_prefix": r["_id"]["poa_prefix"],
            "max_bond_value": r["_id"]["max_bond_value"],
            "available": r["available"],
            "next_serial": r["next_serial"],
            "next_poa_full": f"{r['_id']['poa_prefix']} {r['next_serial']}",
        })

    totals = {
        "osi": sum(r["available"] for r in result if r["surety_id"] == "osi"),
        "palmetto": sum(r["available"] for r in result if r["surety_id"] == "palmetto"),
    }
    return jsonify({"tiers": result, "totals": totals})
