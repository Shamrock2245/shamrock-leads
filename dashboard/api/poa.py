"""
ShamrockLeads — POA Inventory API Blueprint
Endpoints: /api/poa/next, /api/poa/assign, /api/poa/inventory,
           /api/poa/list, /api/poa/add, /api/poa/void,
           /api/poa/release, /api/poa/reassign, /api/poa/restore
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


@poa_bp.route("/poa/inventory-summary", methods=["GET"])
async def api_poa_inventory_summary():
    """Lightweight summary for the dashboard low-stock alert banner.
    Returns tiers with available count, prefix, and surety label."""
    poa_inventory = get_collection("poa_inventory")

    pipeline = [
        {"$match": {"status": "available"}},
        {"$group": {
            "_id": {"surety_id": "$surety_id", "poa_prefix": "$poa_prefix"},
            "available": {"$sum": 1},
        }},
        {"$sort": {"_id.surety_id": 1, "_id.poa_prefix": 1}},
    ]
    tiers = []
    async for r in poa_inventory.aggregate(pipeline):
        surety_label = "Palmetto Surety" if r["_id"]["surety_id"] == "palmetto" else "OSI"
        tiers.append({
            "prefix": r["_id"]["poa_prefix"],
            "surety": surety_label,
            "surety_id": r["_id"]["surety_id"],
            "available": r["available"],
        })

    return jsonify({"tiers": tiers})


@poa_bp.route("/poa/list", methods=["GET"])
async def api_poa_list():
    """Paginated list of all POA powers with filters."""
    poa_inventory = get_collection("poa_inventory")

    page = max(1, int(request.args.get("page", 1) or 1))
    limit = min(200, max(1, int(request.args.get("limit", 50) or 50)))
    surety = (request.args.get("surety") or "").lower().strip()
    status = (request.args.get("status") or "").lower().strip()
    search = (request.args.get("search") or "").strip()

    match = {}
    if surety in ("osi", "palmetto"):
        match["surety_id"] = surety
    if status in ("available", "assigned", "voided"):
        match["status"] = status
    if search:
        match["$or"] = [
            {"poa_number": {"$regex": search, "$options": "i"}},
            {"poa_full": {"$regex": search, "$options": "i"}},
            {"bond_case_id": {"$regex": search, "$options": "i"}},
        ]

    total = await poa_inventory.count_documents(match)
    pages = max(1, (total + limit - 1) // limit)
    skip = (page - 1) * limit

    cursor = poa_inventory.find(
        match,
        {"_id": 0, "poa_number": 1, "poa_full": 1, "poa_prefix": 1,
         "surety_id": 1, "max_bond_value": 1, "status": 1,
         "bond_case_id": 1, "used_at": 1, "expiration": 1},
    ).sort([("surety_id", 1), ("poa_prefix", 1), ("poa_number", 1)]).skip(skip).limit(limit)

    powers = []
    async for doc in cursor:
        powers.append(doc)

    return jsonify({"powers": powers, "total": total, "page": page, "pages": pages})


@poa_bp.route("/poa/add", methods=["POST"])
async def api_poa_add():
    """Add one or more POA numbers to inventory (manual replenishment)."""
    poa_inventory = get_collection("poa_inventory")
    body = (await request.get_json(force=True)) or {}

    surety_id = str(body.get("surety_id", "")).lower().strip()
    poa_prefix = str(body.get("poa_prefix", "")).strip()
    start = str(body.get("start", "")).strip()
    end = str(body.get("end", start)).strip()
    max_bond = body.get("max_bond_value", 0)
    expiration = body.get("expiration")

    if not surety_id or surety_id not in ("osi", "palmetto"):
        return jsonify({"error": "surety_id must be 'osi' or 'palmetto'"}), 400
    if not poa_prefix or not start:
        return jsonify({"error": "poa_prefix and start are required"}), 400

    try:
        start_int = int(start)
        end_int = int(end)
    except ValueError:
        return jsonify({"error": "start and end must be numeric"}), 400

    if end_int < start_int:
        return jsonify({"error": "end must be >= start"}), 400
    if (end_int - start_int) > 500:
        return jsonify({"error": "Cannot add more than 500 at once"}), 400

    docs = []
    skipped = 0
    for serial in range(start_int, end_int + 1):
        existing = await poa_inventory.find_one({"poa_number": str(serial), "surety_id": surety_id})
        if existing:
            skipped += 1
            continue
        docs.append({
            "surety_id": surety_id,
            "poa_prefix": poa_prefix,
            "poa_number": str(serial),
            "poa_full": f"{poa_prefix} {serial}",
            "max_bond_value": max_bond,
            "status": "available",
            "expiration": expiration,
            "book_number": f"manual_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
            "assigned_to_agent": "Brendan O'Neal",
            "received_at": datetime.now(timezone.utc).isoformat(),
            "bond_case_id": None,
            "used_at": None,
        })

    if docs:
        await poa_inventory.insert_many(docs, ordered=False)

    return jsonify({
        "success": True,
        "count": len(docs),
        "skipped": skipped,
        "surety_id": surety_id,
        "poa_prefix": poa_prefix,
    })


@poa_bp.route("/poa/void", methods=["POST"])
async def api_poa_void():
    """Mark a POA as voided (unusable)."""
    poa_inventory = get_collection("poa_inventory")
    body = (await request.get_json(force=True)) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()
    reason = body.get("reason", "Manual void")

    if not poa_number or not surety_id:
        return jsonify({"error": "poa_number and surety_id required"}), 400

    doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return jsonify({"error": f"POA {poa_number} not found"}), 404

    await poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {
            "status": "voided",
            "voided_at": datetime.now(timezone.utc).isoformat(),
            "void_reason": reason,
        }},
    )
    return jsonify({"success": True, "poa_number": poa_number, "message": f"POA {poa_number} voided"})


@poa_bp.route("/poa/release", methods=["POST"])
async def api_poa_release():
    """Release an assigned POA back to available status."""
    poa_inventory = get_collection("poa_inventory")
    body = (await request.get_json(force=True)) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()

    if not poa_number or not surety_id:
        return jsonify({"error": "poa_number and surety_id required"}), 400

    doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return jsonify({"error": f"POA {poa_number} not found"}), 404
    if doc.get("status") != "assigned":
        return jsonify({"error": f"POA {poa_number} is {doc.get('status')}, not assigned"}), 409

    await poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {"status": "available", "bond_case_id": None, "used_at": None},
         "$unset": {"voided_at": "", "void_reason": ""}},
    )
    return jsonify({"success": True, "poa_number": poa_number, "message": f"POA {poa_number} released back to available"})


@poa_bp.route("/poa/reassign", methods=["POST"])
async def api_poa_reassign():
    """Reassign a POA from one case to another."""
    poa_inventory = get_collection("poa_inventory")
    body = (await request.get_json(force=True)) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()
    new_booking = str(body.get("new_booking_number", "")).strip()

    if not poa_number or not surety_id or not new_booking:
        return jsonify({"error": "poa_number, surety_id, and new_booking_number required"}), 400

    doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return jsonify({"error": f"POA {poa_number} not found"}), 404

    old_case = doc.get("bond_case_id", "none")
    await poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {
            "status": "assigned",
            "bond_case_id": new_booking,
            "used_at": datetime.now(timezone.utc).isoformat(),
            "reassigned_from": old_case,
        }},
    )
    return jsonify({
        "success": True, "poa_number": poa_number,
        "message": f"POA {poa_number} reassigned from {old_case} → {new_booking}",
    })


@poa_bp.route("/poa/restore", methods=["POST"])
async def api_poa_restore():
    """Restore a voided POA back to available."""
    poa_inventory = get_collection("poa_inventory")
    body = (await request.get_json(force=True)) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()

    if not poa_number or not surety_id:
        return jsonify({"error": "poa_number and surety_id required"}), 400

    doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return jsonify({"error": f"POA {poa_number} not found"}), 404
    if doc.get("status") != "voided":
        return jsonify({"error": f"POA {poa_number} is {doc.get('status')}, not voided"}), 409

    await poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {"status": "available", "bond_case_id": None, "used_at": None},
         "$unset": {"voided_at": "", "void_reason": ""}},
    )
    return jsonify({"success": True, "poa_number": poa_number, "message": f"POA {poa_number} restored to available"})
