# ── AUTO-MIGRATED: Quart Blueprint → FastAPI APIRouter (v3) ──
# _qp = dict(request.query_params) injected into fns that read query params.
# Review each endpoint and move _qp.get() calls to typed fn signatures.

"""
ShamrockLeads — Accounting & Revenue Intelligence API
Full-cycle financial tracking: SwipeSimple CSV import, cash ledger,
payment-to-case attribution, premium splits, QuickBooks export.

Collections:
  - transactions: Unified ledger (card, cash, check, wire, payment plan)
  - payments: Legacy payment events (kept for backward compat)
  - accounting_imports: Batch import tracking for SwipeSimple CSVs
"""

import csv
import io
import uuid
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request
from starlette.responses import Response
from fastapi.responses import JSONResponse
from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)
accounting_bp = APIRouter(prefix="/api", tags=["accounting"])
# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/accounting/dashboard — Revenue KPIs + summary
# ═══════════════════════════════════════════════════════════════════════════════
@accounting_bp.get("/accounting/dashboard")
async def api_accounting_dashboard():
    """Return revenue KPIs: MTD, YTD, outstanding, collected, by surety."""
    txns = get_collection("transactions")
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    pipeline_mtd = [
        {"$match": {"timestamp": {"$gte": month_start.isoformat()}, "status": {"$in": ["completed", "settled"]}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ]
    pipeline_ytd = [
        {"$match": {"timestamp": {"$gte": year_start.isoformat()}, "status": {"$in": ["completed", "settled"]}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ]
    pipeline_by_method = [
        {"$match": {"timestamp": {"$gte": year_start.isoformat()}, "status": {"$in": ["completed", "settled"]}}},
        {"$group": {"_id": "$method", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ]
    pipeline_by_surety = [
        {"$match": {"timestamp": {"$gte": year_start.isoformat()}, "status": {"$in": ["completed", "settled"]}}},
        {"$group": {"_id": "$surety", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ]
    pipeline_outstanding = [
        {"$match": {"status": {"$in": ["pending", "partial"]}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ]

    mtd = await txns.aggregate(pipeline_mtd).to_list(1)
    ytd = await txns.aggregate(pipeline_ytd).to_list(1)
    by_method = await txns.aggregate(pipeline_by_method).to_list(20)
    by_surety = await txns.aggregate(pipeline_by_surety).to_list(10)
    outstanding = await txns.aggregate(pipeline_outstanding).to_list(1)

    # Recent transactions (last 50)
    recent = []
    async for doc in txns.find().sort("timestamp", -1).limit(50):
        doc["_id"] = str(doc["_id"])
        recent.append(doc)

    return {
        "mtd": {"total": mtd[0]["total"] if mtd else 0, "count": mtd[0]["count"] if mtd else 0},
        "ytd": {"total": ytd[0]["total"] if ytd else 0, "count": ytd[0]["count"] if ytd else 0},
        "outstanding": {"total": outstanding[0]["total"] if outstanding else 0, "count": outstanding[0]["count"] if outstanding else 0},
        "by_method": {r["_id"]: {"total": r["total"], "count": r["count"]} for r in by_method if r["_id"]},
        "by_surety": {r["_id"]: {"total": r["total"], "count": r["count"]} for r in by_surety if r["_id"]},
        "recent_transactions": recent,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/accounting/transactions — Paginated ledger
# ═══════════════════════════════════════════════════════════════════════════════
@accounting_bp.get("/accounting/transactions")
async def api_accounting_transactions(request: Request):
    """Paginated transaction ledger with filters."""
    _qp = dict(request.query_params)
    txns = get_collection("transactions")
    page = int(_qp.get("page", 0))
    limit = min(int(_qp.get("limit", 50)), 200)
    method = _qp.get("method", "")
    status = _qp.get("status", "")
    search = _qp.get("search", "").strip()
    date_from = _qp.get("date_from", "")
    date_to = _qp.get("date_to", "")
    surety = _qp.get("surety", "")
    unattributed = _qp.get("unattributed", "").lower() == "true"

    query = {}
    if method:
        query["method"] = method
    if status:
        query["status"] = status
    if surety:
        query["surety"] = surety.upper()
    if unattributed:
        query["$or"] = [{"booking_number": {"$exists": False}}, {"booking_number": ""}, {"booking_number": None}]
    if search:
        query["$or"] = [
            {"defendant_name": {"$regex": search, "$options": "i"}},
            {"booking_number": {"$regex": search, "$options": "i"}},
            {"description": {"$regex": search, "$options": "i"}},
            {"reference_id": {"$regex": search, "$options": "i"}},
        ]
    if date_from:
        query.setdefault("timestamp", {})["$gte"] = date_from
    if date_to:
        query.setdefault("timestamp", {})["$lte"] = date_to + "T23:59:59"

    total = await txns.count_documents(query)
    docs = []
    async for doc in txns.find(query).sort("timestamp", -1).skip(page * limit).limit(limit):
        doc["_id"] = str(doc["_id"])
        docs.append(doc)

    return {"transactions": docs, "total": total, "page": page, "limit": limit}


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/accounting/transactions — Record a transaction (cash, check, etc.)
# ═══════════════════════════════════════════════════════════════════════════════
@accounting_bp.post("/accounting/transactions")
async def api_record_transaction(request: Request):
    """Record a manual transaction (cash, check, wire, etc.)."""
    data = await request.json() or {}
    amount = float(data.get("amount", 0))
    if amount <= 0:
        return JSONResponse({"error": "Amount must be > 0"}, status_code=400)

    now = datetime.now(timezone.utc)
    txn = {
        "transaction_id": f"TXN-{uuid.uuid4().hex[:12].upper()}",
        "amount": amount,
        "method": data.get("method", "cash"),
        "type": data.get("type", "premium"),  # premium, payment_plan, refund, fee, other
        "status": data.get("status", "completed"),
        "booking_number": data.get("booking_number", ""),
        "defendant_name": data.get("defendant_name", ""),
        "poa_number": data.get("poa_number", ""),
        "case_number": data.get("case_number", ""),
        "surety": (data.get("surety", "") or "").upper(),
        "county": data.get("county", ""),
        "description": data.get("description", ""),
        "reference_id": data.get("reference_id", ""),
        "indemnitor_name": data.get("indemnitor_name", ""),
        "agent_name": data.get("agent_name", "Brendan O'Neal"),
        "source": "manual",
        "timestamp": data.get("timestamp") or now.isoformat(),
        "created_at": now.isoformat(),
    }

    result = await get_collection("transactions").insert_one(txn)
    txn["_id"] = str(result.inserted_id)
    logger.info("[accounting] Transaction recorded: %s — $%.2f (%s)", txn["transaction_id"], amount, txn["method"])
    return {"success": True, "transaction": txn}, 201


# ═══════════════════════════════════════════════════════════════════════════════
#  PATCH /api/accounting/transactions/<txn_id>/attribute — Link txn to case
# ═══════════════════════════════════════════════════════════════════════════════
@accounting_bp.patch("/accounting/transactions/<txn_id>/attribute")
async def api_attribute_transaction(request: Request, txn_id: str):
    """Attribute a transaction to a specific case/defendant."""
    data = await request.json() or {}
    txns = get_collection("transactions")

    update = {"$set": {"updated_at": datetime.now(timezone.utc).isoformat()}}
    for field in ["booking_number", "defendant_name", "poa_number", "case_number", "surety", "county", "indemnitor_name"]:
        if field in data:
            update["$set"][field] = data[field]

    result = await txns.update_one({"transaction_id": txn_id}, update)
    if result.matched_count == 0:
        return JSONResponse({"error": "Transaction not found"}, status_code=404)
    return {"success": True}


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/accounting/import/swipesimple — Import SwipeSimple CSV
# ═══════════════════════════════════════════════════════════════════════════════
@accounting_bp.post("/accounting/import/swipesimple")
async def api_import_swipesimple(request: Request):
    """Parse and import a SwipeSimple CSV export into the transactions ledger."""
    files = await request.files
    csv_file = files.get("file")
    if not csv_file:
        # Try raw body as CSV text
        raw = (await request.get_data()).decode("utf-8", errors="replace")
        if not raw.strip():
            return JSONResponse({"error": "No CSV file provided"}, status_code=400)
    else:
        raw = csv_file.read().decode("utf-8", errors="replace")

    txns = get_collection("transactions")
    imports = get_collection("accounting_imports")
    now = datetime.now(timezone.utc)
    batch_id = f"SS-{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

    reader = csv.DictReader(io.StringIO(raw))
    imported = 0
    skipped = 0
    errors_list = []

    for i, row in enumerate(reader):
        try:
            # SwipeSimple CSV columns (flexible matching)
            amount_str = row.get("Amount") or row.get("amount") or row.get("Total") or "0"
            amount = abs(float(amount_str.replace("$", "").replace(",", "").strip()))
            if amount <= 0:
                skipped += 1
                continue

            ss_txn_id = row.get("Transaction ID") or row.get("transaction_id") or row.get("ID") or ""
            ss_status = (row.get("Status") or row.get("status") or "completed").strip().lower()
            if ss_status in ("void", "voided", "declined", "failed"):
                skipped += 1
                continue

            # Dedup check
            if ss_txn_id:
                existing = await txns.find_one({"reference_id": ss_txn_id, "source": "swipesimple"})
                if existing:
                    skipped += 1
                    continue

            # Parse date
            date_str = row.get("Date") or row.get("date") or row.get("Created") or ""
            time_str = row.get("Time") or row.get("time") or ""
            timestamp = now.isoformat()
            if date_str:
                try:
                    dt = datetime.strptime(f"{date_str} {time_str}".strip(), "%m/%d/%Y %I:%M %p")
                    timestamp = dt.replace(tzinfo=timezone.utc).isoformat()
                except ValueError:
                    try:
                        dt = datetime.strptime(date_str.strip(), "%m/%d/%Y")
                        timestamp = dt.replace(tzinfo=timezone.utc).isoformat()
                    except ValueError:
                        pass

            card_type = row.get("Card Type") or row.get("card_type") or ""
            last4 = row.get("Last 4") or row.get("last_four") or ""
            customer = row.get("Customer Name") or row.get("customer_name") or row.get("Name") or ""
            desc = row.get("Description") or row.get("description") or row.get("Memo") or ""

            txn_doc = {
                "transaction_id": f"SS-{ss_txn_id}" if ss_txn_id else f"SS-{uuid.uuid4().hex[:10].upper()}",
                "amount": amount,
                "method": "card",
                "card_type": card_type,
                "card_last4": last4,
                "type": "premium",
                "status": "completed" if ss_status in ("settled", "completed", "approved", "captured") else ss_status,
                "description": desc,
                "customer_name": customer,
                "reference_id": ss_txn_id,
                "source": "swipesimple",
                "import_batch": batch_id,
                "booking_number": "",  # Needs manual attribution
                "defendant_name": "",
                "timestamp": timestamp,
                "created_at": now.isoformat(),
            }
            await txns.insert_one(txn_doc)
            imported += 1
        except Exception as e:
            errors_list.append(f"Row {i+1}: {str(e)}")

    # Record import batch
    await imports.insert_one({
        "batch_id": batch_id,
        "source": "swipesimple",
        "imported": imported,
        "skipped": skipped,
        "errors": len(errors_list),
        "error_details": errors_list[:20],
        "timestamp": now.isoformat(),
    })

    logger.info("[accounting] SwipeSimple import: %d imported, %d skipped, %d errors (batch: %s)", imported, skipped, len(errors_list), batch_id)
    return {"success": True, "batch_id": batch_id, "imported": imported, "skipped": skipped, "errors": len(errors_list)}


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/accounting/import/history — Import batch history
# ═══════════════════════════════════════════════════════════════════════════════
@accounting_bp.get("/accounting/import/history")
async def api_import_history():
    imports = get_collection("accounting_imports")
    docs = []
    async for doc in imports.find().sort("timestamp", -1).limit(20):
        doc["_id"] = str(doc["_id"])
        docs.append(doc)
    return {"imports": docs}


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/accounting/premium-split — Calculate splits for a bond
# ═══════════════════════════════════════════════════════════════════════════════
@accounting_bp.get("/accounting/premium-split")
async def api_premium_split(request: Request):
    _qp = dict(request.query_params)
    bond_amount = float(_qp.get("bond_amount", 0))
    surety = _qp.get("surety", "osi").lower()
    if bond_amount <= 0:
        return JSONResponse({"error": "bond_amount required"}, status_code=400)

    premium = bond_amount * 0.10
    buf_rate = 0.05
    surety_rate = 0.075 if surety == "osi" else 0.10
    buf_owed = round(premium * buf_rate, 2)
    surety_owed = round(premium * surety_rate, 2)
    agent_retains = round(premium - surety_owed - buf_owed, 2)

    return {
        "bond_amount": bond_amount, "premium": premium, "surety": surety.upper(),
        "surety_rate": surety_rate, "buf_rate": buf_rate,
        "surety_owed": surety_owed, "buf_owed": buf_owed, "agent_retains": agent_retains,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/accounting/revenue/monthly — Monthly revenue for charts
# ═══════════════════════════════════════════════════════════════════════════════
@accounting_bp.get("/accounting/revenue/monthly")
async def api_revenue_monthly(request: Request):
    _qp = dict(request.query_params)
    txns = get_collection("transactions")
    months = int(_qp.get("months", 12))
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=months * 31)).isoformat()

    pipeline = [
        {"$match": {"timestamp": {"$gte": cutoff}, "status": {"$in": ["completed", "settled"]}}},
        {"$addFields": {"month": {"$substr": ["$timestamp", 0, 7]}}},
        {"$group": {"_id": "$month", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    results = await txns.aggregate(pipeline).to_list(months + 1)
    return {"monthly": [{"month": r["_id"], "total": r["total"], "count": r["count"]} for r in results]}


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/accounting/export/quickbooks — QuickBooks-compatible CSV export
# ═══════════════════════════════════════════════════════════════════════════════
@accounting_bp.get("/accounting/export/quickbooks")
async def api_export_quickbooks(request: Request):
    """Export transactions as QuickBooks-compatible CSV (General Journal format)."""
    _qp = dict(request.query_params)
    txns = get_collection("transactions")
    date_from = _qp.get("date_from", "")
    date_to = _qp.get("date_to", "")

    query = {"status": {"$in": ["completed", "settled"]}}
    if date_from:
        query.setdefault("timestamp", {})["$gte"] = date_from
    if date_to:
        query.setdefault("timestamp", {})["$lte"] = date_to + "T23:59:59"

    output = io.StringIO()
    writer = csv.writer(output)
    # QuickBooks General Journal Import format
    writer.writerow([
        "Date", "Transaction Type", "Num", "Name", "Memo",
        "Account", "Debit", "Credit", "Class"
    ])

    async for doc in txns.find(query).sort("timestamp", 1):
        date_str = doc.get("timestamp", "")[:10]
        try:
            dt = datetime.fromisoformat(date_str)
            date_str = dt.strftime("%m/%d/%Y")
        except (ValueError, TypeError):
            pass

        amount = doc.get("amount", 0)
        method = doc.get("method", "cash")
        surety = doc.get("surety", "")
        txn_type = doc.get("type", "premium")
        defendant = doc.get("defendant_name", "")
        poa = doc.get("poa_number", "")
        memo = f"{defendant} | POA: {poa}" if defendant else doc.get("description", "")

        # Income account based on type
        income_acct = "Bond Premium Income"
        if txn_type == "payment_plan":
            income_acct = "Payment Plan Income"
        elif txn_type == "fee":
            income_acct = "Fee Income"

        # Deposit account based on method
        deposit_acct = "Cash on Hand" if method == "cash" else "Merchant Account (SwipeSimple)"
        if method == "check":
            deposit_acct = "Undeposited Funds"
        elif method == "wire":
            deposit_acct = "Operating Account"

        class_name = surety or "General"

        # Debit deposit account
        writer.writerow([date_str, "General Journal", doc.get("transaction_id", ""), defendant, memo, deposit_acct, f"{amount:.2f}", "", class_name])
        # Credit income account
        writer.writerow([date_str, "General Journal", doc.get("transaction_id", ""), defendant, memo, income_acct, "", f"{amount:.2f}", class_name])

    csv_content = output.getvalue()
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=shamrock_quickbooks_{datetime.now().strftime('%Y%m%d')}.csv"},
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/accounting/export/csv — Raw transaction export
# ═══════════════════════════════════════════════════════════════════════════════
@accounting_bp.get("/accounting/export/csv")
async def api_export_csv(request: Request):
    """Export raw transaction data as CSV."""
    _qp = dict(request.query_params)
    txns = get_collection("transactions")
    date_from = _qp.get("date_from", "")
    date_to = _qp.get("date_to", "")
    query = {}
    if date_from:
        query.setdefault("timestamp", {})["$gte"] = date_from
    if date_to:
        query.setdefault("timestamp", {})["$lte"] = date_to + "T23:59:59"

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Transaction ID", "Amount", "Method", "Type", "Status", "Defendant", "Booking #", "POA #", "Case #", "Surety", "County", "Indemnitor", "Agent", "Description", "Source"])

    async for doc in txns.find(query).sort("timestamp", 1):
        writer.writerow([
            doc.get("timestamp", "")[:10], doc.get("transaction_id", ""), doc.get("amount", 0),
            doc.get("method", ""), doc.get("type", ""), doc.get("status", ""),
            doc.get("defendant_name", ""), doc.get("booking_number", ""), doc.get("poa_number", ""),
            doc.get("case_number", ""), doc.get("surety", ""), doc.get("county", ""),
            doc.get("indemnitor_name", ""), doc.get("agent_name", ""), doc.get("description", ""),
            doc.get("source", ""),
        ])

    return Response(
        output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=shamrock_transactions_{datetime.now().strftime('%Y%m%d')}.csv"},
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  DELETE /api/accounting/transactions/<txn_id> — Void a transaction
# ═══════════════════════════════════════════════════════════════════════════════
@accounting_bp.delete("/accounting/transactions/<txn_id>")
async def api_void_transaction(txn_id: str):
    """Void a transaction (soft-delete — marks as voided, doesn't remove)."""
    txns = get_collection("transactions")
    result = await txns.update_one(
        {"transaction_id": txn_id},
        {"$set": {"status": "voided", "voided_at": datetime.now(timezone.utc).isoformat()}},
    )
    if result.matched_count == 0:
        return JSONResponse({"error": "Transaction not found"}, status_code=404)
    return {"success": True}
