
"""
ShamrockLeads — POA Inventory API Blueprint
Endpoints: /api/poa/next, /api/poa/assign, /api/poa/inventory,
           /api/poa/list, /api/poa/add, /api/poa/void,
           /api/poa/release, /api/poa/reassign, /api/poa/restore
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from dashboard.extensions import get_collection
from dashboard.services.poa_service import get_poa_tier_for_bond

poa_bp = APIRouter(prefix="/api", tags=["poa"])
@poa_bp.get("/poa/next")
async def api_poa_next(surety: str | None = Query(default=None), bond_amount: int = Query(default=0), count: int = Query(default=1)):
    """
    Suggest the next available POA number(s) for a given surety + bond amount.
    Query params: surety, bond_amount, count
    """
    poa_inventory = get_collection("poa_inventory")

    surety = (surety or "").lower().strip()
    if surety not in ("osi", "palmetto"):
        return JSONResponse({"error": "surety must be 'osi' or 'palmetto'"}, status_code=400)
    try:
        bond_amount = float(bond_amount or 0)
    except ValueError:
        bond_amount = 0.0
    count = max(1, int(count or 1))

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

    return {
        "surety": surety,
        "prefix": prefix,
        "bond_amount": bond_amount,
        "available_in_tier": total_available,
        "available_total": total_surety,
        "suggested": suggested,
        "warning": ("Low inventory in this tier" if total_available <= 3 else None),
    }


@poa_bp.post("/poa/assign")
@poa_bp.post("/poa/assign")
async def api_poa_assign(request: Request):
    """Mark a POA as assigned to a bond case."""
    poa_inventory = get_collection("poa_inventory")

    body = (await request.json()) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    poa_prefix = str(body.get("poa_prefix", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()
    bond_case_id = body.get("bond_case_id") or body.get("booking_number", "")

    if not poa_number or not surety_id:
        return JSONResponse({"error": "poa_number and surety_id are required"}, status_code=400)

    doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return JSONResponse({"error": f"POA {poa_number} not found for surety {surety_id}"}, status_code=404)
    if doc.get("status") != "available":
        return JSONResponse({"error": f"POA {poa_number} is already {doc.get('status')} — cannot assign"}, status_code=409)

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

    return {
        "success": True,
        "poa_number": poa_number,
        "poa_prefix": doc.get("poa_prefix", poa_prefix),
        "poa_full": doc.get("poa_full", f"{poa_prefix} {poa_number}"),
        "surety_id": surety_id,
        "bond_case_id": str(bond_case_id),
        "remaining_in_tier": remaining,
    }


@poa_bp.get("/poa/inventory")
async def api_poa_inventory(surety: str | None = Query(default=None)):
    """Return a summary of available POA inventory by surety and tier."""
    poa_inventory = get_collection("poa_inventory")

    surety_filter = (surety or "").lower().strip()
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
    return {"tiers": result, "totals": totals}


@poa_bp.get("/poa/inventory-summary")
@poa_bp.get("/poa/inventory-summary")
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

    return {"tiers": tiers}


@poa_bp.get("/poa/list")
async def api_poa_list(page: int = Query(default=1), limit: int = Query(default=50), surety: str | None = Query(default=None), status: str | None = Query(default=None), search: str | None = Query(default=None)):
    """Paginated list of all POA powers with filters."""
    poa_inventory = get_collection("poa_inventory")

    page = max(1, int(page or 1))
    limit = min(200, max(1, int(limit or 50)))
    surety = (surety or "").lower().strip()
    status = (status or "").lower().strip()
    search = (search or "").strip()

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
         "bond_case_id": 1, "defendant_name": 1, "charge": 1,
         "appearance_bond_number": 1, "used_at": 1, "expiration": 1},
    ).sort([("surety_id", 1), ("poa_prefix", 1), ("poa_number", 1)]).skip(skip).limit(limit)

    powers = []
    async for doc in cursor:
        powers.append(doc)

    return {"powers": powers, "total": total, "page": page, "pages": pages}


@poa_bp.post("/poa/add")
@poa_bp.post("/poa/add")
async def api_poa_add(request: Request):
    """Add one or more POA numbers to inventory (manual replenishment)."""
    poa_inventory = get_collection("poa_inventory")
    body = (await request.json()) or {}

    surety_id = str(body.get("surety_id", "")).lower().strip()
    poa_prefix = str(body.get("poa_prefix", "")).strip()
    start = str(body.get("start", "")).strip()
    end = str(body.get("end", start)).strip()
    max_bond = body.get("max_bond_value", 0)
    expiration = body.get("expiration")

    if not surety_id or surety_id not in ("osi", "palmetto"):
        return JSONResponse({"error": "surety_id must be 'osi' or 'palmetto'"}, status_code=400)
    if not poa_prefix or not start:
        return JSONResponse({"error": "poa_prefix and start are required"}, status_code=400)

    try:
        start_int = int(start)
        end_int = int(end)
    except ValueError:
        return JSONResponse({"error": "start and end must be numeric"}, status_code=400)

    if end_int < start_int:
        return JSONResponse({"error": "end must be >= start"}, status_code=400)
    if (end_int - start_int) > 500:
        return JSONResponse({"error": "Cannot add more than 500 at once"}, status_code=400)

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

    return {
        "success": True,
        "count": len(docs),
        "skipped": skipped,
        "surety_id": surety_id,
        "poa_prefix": poa_prefix,
    }


@poa_bp.post("/poa/void")
async def api_poa_void(request: Request):
    """Mark a POA as voided (unusable)."""
    poa_inventory = get_collection("poa_inventory")
    body = (await request.json()) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()
    reason = body.get("reason", "Manual void")

    if not poa_number or not surety_id:
        return JSONResponse({"error": "poa_number and surety_id required"}, status_code=400)

    doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return JSONResponse({"error": f"POA {poa_number} not found"}, status_code=404)

    await poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {
            "status": "voided",
            "voided_at": datetime.now(timezone.utc).isoformat(),
            "void_reason": reason,
        }},
    )
    return {"success": True, "poa_number": poa_number, "message": f"POA {poa_number} voided"}


@poa_bp.post("/poa/release")
async def api_poa_release(request: Request):
    """Release an assigned POA back to available status."""
    poa_inventory = get_collection("poa_inventory")
    body = (await request.json()) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()

    if not poa_number or not surety_id:
        return JSONResponse({"error": "poa_number and surety_id required"}, status_code=400)

    doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return JSONResponse({"error": f"POA {poa_number} not found"}, status_code=404)
    if doc.get("status") != "assigned":
        return JSONResponse({"error": f"POA {poa_number} is {doc.get('status')}, not assigned"}, status_code=409)

    await poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {"status": "available", "bond_case_id": None, "used_at": None},
         "$unset": {"voided_at": "", "void_reason": ""}},
    )
    return {"success": True, "poa_number": poa_number, "message": f"POA {poa_number} released back to available"}


@poa_bp.post("/poa/reassign")
async def api_poa_reassign(request: Request):
    """Reassign a POA from one case to another."""
    poa_inventory = get_collection("poa_inventory")
    body = (await request.json()) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()
    new_booking = str(body.get("new_booking_number", "")).strip()

    if not poa_number or not surety_id or not new_booking:
        return JSONResponse({"error": "poa_number, surety_id, and new_booking_number required"}, status_code=400)

    doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return JSONResponse({"error": f"POA {poa_number} not found"}, status_code=404)

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
    return {
        "success": True, "poa_number": poa_number,
        "message": f"POA {poa_number} reassigned from {old_case} → {new_booking}",
    }


@poa_bp.post("/poa/restore")
async def api_poa_restore(request: Request):
    """Restore a voided POA back to available."""
    poa_inventory = get_collection("poa_inventory")
    body = (await request.json()) or {}
    poa_number = str(body.get("poa_number", "")).strip()
    surety_id = str(body.get("surety_id", "")).lower().strip()

    if not poa_number or not surety_id:
        return JSONResponse({"error": "poa_number and surety_id required"}, status_code=400)

    doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety_id})
    if not doc:
        return JSONResponse({"error": f"POA {poa_number} not found"}, status_code=404)
    if doc.get("status") != "voided":
        return JSONResponse({"error": f"POA {poa_number} is {doc.get('status')}, not voided"}, status_code=409)

    await poa_inventory.update_one(
        {"poa_number": poa_number, "surety_id": surety_id},
        {"$set": {"status": "available", "bond_case_id": None, "used_at": None},
         "$unset": {"voided_at": "", "void_reason": ""}},
    )
    return {"success": True, "poa_number": poa_number, "message": f"POA {poa_number} restored to available"}


@poa_bp.post("/poa/bulk-assign")
async def api_poa_bulk_assign(request: Request):
    """Assign multiple POAs to a single defendant/case in one operation.

    Supports two formats:

    NEW format (charge-level mapping):
    {
        assignments: [
            { poa_number: "12345", charge: "BATTERY", appearance_bond_number: "26-CF-001234" },
            { poa_number: "12346", charge: "DUI", appearance_bond_number: "26-CF-001235" }
        ],
        surety_id: "osi" | "palmetto",
        bond_case_id: "booking_number or case reference",
        defendant_name: "optional — for audit trail"
    }

    LEGACY format (flat list, no charge data):
    {
        poa_numbers: ["12345", "12346"],
        surety_id: "osi" | "palmetto",
        bond_case_id: "booking_number or case reference",
        defendant_name: "optional"
    }
    """
    poa_inventory = get_collection("poa_inventory")
    body = (await request.json()) or {}

    surety_id = str(body.get("surety_id", "")).lower().strip()
    bond_case_id = str(body.get("bond_case_id", "")).strip()
    defendant_name = body.get("defendant_name", "")

    # ── Normalize both formats into per-POA assignment dicts ──
    assignments_raw = body.get("assignments", [])
    poa_numbers_legacy = body.get("poa_numbers", [])

    if assignments_raw and isinstance(assignments_raw, list):
        # New format: each entry has poa_number + optional charge/bond info
        work_items = []
        for a in assignments_raw:
            if isinstance(a, dict) and a.get("poa_number"):
                work_items.append({
                    "poa_number": str(a["poa_number"]).strip(),
                    "charge": str(a.get("charge", "")).strip() or None,
                    "appearance_bond_number": str(a.get("appearance_bond_number", "")).strip() or None,
                })
    elif poa_numbers_legacy and isinstance(poa_numbers_legacy, list):
        # Legacy format: flat list, no charge data
        work_items = [{"poa_number": str(n).strip(), "charge": None, "appearance_bond_number": None}
                      for n in poa_numbers_legacy]
    else:
        return JSONResponse({"error": "Either 'assignments' or 'poa_numbers' must be a non-empty array"}, status_code=400)

    if not bond_case_id:
        return JSONResponse({"error": "bond_case_id (booking number) is required"}, status_code=400)
    if len(work_items) > 50:
        return JSONResponse({"error": "Cannot bulk-assign more than 50 POAs at once"}, status_code=400)

    now = datetime.now(timezone.utc).isoformat()
    assigned = []
    skipped = []
    errors = []

    for item in work_items:
        poa_num = item["poa_number"]
        query = {"poa_number": poa_num}
        if surety_id in ("osi", "palmetto"):
            query["surety_id"] = surety_id

        doc = await poa_inventory.find_one(query)
        if not doc:
            errors.append({"poa_number": poa_num, "reason": "not found"})
            continue
        if doc.get("status") != "available":
            skipped.append({
                "poa_number": poa_num,
                "reason": f"already {doc.get('status')}",
                "current_case": doc.get("bond_case_id"),
            })
            continue

        update_fields = {
            "status": "assigned",
            "bond_case_id": bond_case_id,
            "defendant_name": defendant_name or None,
            "used_at": now,
            "bulk_assigned": True,
        }
        # Attach charge-level data when provided
        if item["charge"]:
            update_fields["charge"] = item["charge"]
        if item["appearance_bond_number"]:
            update_fields["appearance_bond_number"] = item["appearance_bond_number"]

        await poa_inventory.update_one(
            {"_id": doc["_id"]},
            {"$set": update_fields},
        )
        assigned.append({
            "poa_number": poa_num,
            "poa_full": doc.get("poa_full", f"{doc.get('poa_prefix', '')} {poa_num}"),
            "poa_prefix": doc.get("poa_prefix", ""),
            "charge": item["charge"],
            "appearance_bond_number": item["appearance_bond_number"],
        })

    return {
        "success": True,
        "bond_case_id": bond_case_id,
        "defendant_name": defendant_name,
        "assigned_count": len(assigned),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "assigned": assigned,
        "skipped": skipped,
        "errors": errors,
    }


@poa_bp.post("/poa/alert-check")
async def api_poa_alert_check():
    """Check all POA tiers and fire Slack alerts for low/critical inventory.

    Called on-demand from the dashboard or on a cron schedule.
    Thresholds: CRITICAL ≤ 2, LOW ≤ 5.
    """
    import os, aiohttp, logging

    poa_inventory = get_collection("poa_inventory")
    LOW = 5
    CRITICAL = 2

    pipeline = [
        {"$match": {"status": "available"}},
        {"$group": {
            "_id": {"surety_id": "$surety_id", "poa_prefix": "$poa_prefix"},
            "available": {"$sum": 1},
            "max_bond_value": {"$max": "$max_bond_value"},
        }},
        {"$sort": {"_id.surety_id": 1, "_id.max_bond_value": 1}},
    ]

    critical_tiers = []
    low_tiers = []
    all_tiers = []

    async for r in poa_inventory.aggregate(pipeline):
        tier = {
            "surety": r["_id"]["surety_id"].upper(),
            "prefix": r["_id"]["poa_prefix"],
            "available": r["available"],
            "max_bond": r.get("max_bond_value", 0),
        }
        all_tiers.append(tier)
        if tier["available"] <= CRITICAL:
            critical_tiers.append(tier)
        elif tier["available"] <= LOW:
            low_tiers.append(tier)

    alerts_sent = 0
    webhook = os.getenv("SLACK_WEBHOOK_LEADS", "")

    if (critical_tiers or low_tiers) and webhook:
        blocks = []
        if critical_tiers:
            lines = [f"• *{t['prefix']}* ({t['surety']}): *{t['available']}* left" for t in critical_tiers]
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"🔴 *CRITICAL POA INVENTORY*\n{'chr(10)'.join(lines)}"},
            })

        if low_tiers:
            lines = [f"• *{t['prefix']}* ({t['surety']}): {t['available']} left" for t in low_tiers]
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"⚠️ *Low POA Stock*\n{'chr(10)'.join(lines)}"},
            })

        payload = {
            "text": f"POA Inventory Alert: {len(critical_tiers)} critical, {len(low_tiers)} low",
            "blocks": blocks,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        alerts_sent = 1
        except Exception as exc:
            logging.getLogger(__name__).warning("POA Slack alert failed: %s", exc)

    return {
        "success": True,
        "critical_count": len(critical_tiers),
        "low_count": len(low_tiers),
        "critical_tiers": critical_tiers,
        "low_tiers": low_tiers,
        "slack_alert_sent": alerts_sent > 0,
    }
