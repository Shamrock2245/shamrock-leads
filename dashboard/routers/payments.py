
"""
ShamrockLeads — Payments API Blueprint
Records payment events and retrieves payment history.

Uses extensions.get_collection() to avoid circular imports from app.py.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from datetime import datetime, timezone

from dashboard.extensions import get_collection

payments_bp = APIRouter(prefix="/api", tags=["payments"])
@payments_bp.post("/payments/log")
async def log_payment(request: Request):
    """Record a payment to MongoDB 'payments' collection."""
    from dashboard.routers.events import publish_event

    data = await request.json()
    if not data or 'booking_number' not in data or 'amount' not in data:
        return JSONResponse({"error": "Missing required fields"}, status_code=400)

    payments = get_collection("payments")

    payment_doc = {
        "booking_number": data['booking_number'],
        "amount": float(data['amount']),
        "method": data.get('method', 'Unknown'),
        "status": data.get('status', 'Completed'),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "transaction_id": data.get('transaction_id', ''),
        "notes": data.get('notes', '')
    }

    result = await payments.insert_one(payment_doc)
    payment_doc['_id'] = str(result.inserted_id)

    await publish_event('payment_received', payment_doc)

    return JSONResponse(status_code=201, content={"success": True, "payment_id": payment_doc['_id']})


@payments_bp.get("/payments/{booking_number}")
async def get_payments(booking_number):
    """Get payment history for a case."""
    payments = get_collection("payments")

    cursor = payments.find({"booking_number": booking_number}).sort("timestamp", -1)
    payment_list = []
    async for doc in cursor:
        doc['_id'] = str(doc['_id'])
        payment_list.append(doc)

    return JSONResponse(status_code=200, content={"payments": payment_list})

@payments_bp.post("/payments/plan/{booking_number}")
async def create_payment_plan(request: Request, booking_number):
    """Create a payment plan for a bond."""
    data = await request.json()
    if not data or 'total_amount' not in data or 'down_payment' not in data:
        return JSONResponse({"error": "Missing required fields"}, status_code=400)
        
    plans = get_collection("payment_plans")
    plan_doc = {
        "booking_number": booking_number,
        "total_amount": float(data['total_amount']),
        "down_payment": float(data['down_payment']),
        "balance": float(data['total_amount']) - float(data['down_payment']),
        "frequency": data.get('frequency', 'weekly'),
        "installment_amount": float(data.get('installment_amount', 0)),
        "next_due_date": data.get('next_due_date'),
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    result = await plans.insert_one(plan_doc)
    plan_doc['_id'] = str(result.inserted_id)
    
    return JSONResponse(status_code=201, content={"success": True, "plan_id": plan_doc['_id']})

@payments_bp.post("/payments/premium-split")
async def calculate_premium_split(request: Request):
    """Calculate the premium split between agent, surety, and BUF."""
    data = await request.json()
    if not data or 'bond_amount' not in data:
        return JSONResponse({"error": "Missing bond_amount"}, status_code=400)
        
    bond_amount = float(data['bond_amount'])
    surety_id = data.get('surety_id', 'osi').lower()
    
    premium = bond_amount * 0.10
    buf_owed = premium * 0.05
    
    if surety_id == 'osi':
        surety_owed = premium * 0.075
    else: # palmetto
        surety_owed = premium * 0.10
        
    agent_retains = premium - surety_owed - buf_owed
    
    return JSONResponse(status_code=200, content={
        "bond_amount": bond_amount,
        "premium": premium,
        "surety_owed": surety_owed,
        "buf_owed": buf_owed,
        "agent_retains": agent_retains
    })

@payments_bp.post("/ledger/entry")
async def add_ledger_entry(request: Request):
    """Add a manual entry to the financial ledger (e.g., cash payment, fee)."""
    from dashboard.services.ledger_service import LedgerService
    data = await request.json()
    if not data or 'booking_number' not in data or 'amount' not in data or 'type' not in data or 'category' not in data:
        return JSONResponse({"error": "Missing required fields"}, status_code=400)
    
    # ensure actor is present
    if 'actor' not in data:
        data['actor'] = "Dashboard Agent"
        
    try:
        transaction_id = await LedgerService.add_entry(data)
        return JSONResponse(status_code=201, content={"success": True, "transaction_id": transaction_id})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@payments_bp.get("/ledger/{booking_number}")
async def get_ledger(booking_number: str):
    """Retrieve full ledger history and balance for a booking."""
    from dashboard.services.ledger_service import LedgerService
    try:
        history = await LedgerService.get_ledger_history(booking_number)
        balance = await LedgerService.get_balance(booking_number)
        # convert datetimes to isoformat for json serialization
        for entry in history:
            if hasattr(entry.get('timestamp'), 'isoformat'):
                entry['timestamp'] = entry['timestamp'].isoformat()
        return JSONResponse(status_code=200, content={
            "success": True,
            "balance": balance,
            "history": history
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@payments_bp.post("/ledger/swipesimple/import")
async def import_swipesimple(request: Request):
    """Endpoint to upload a SwipeSimple CSV file and parse entries into the ledger."""
    from dashboard.services.ledger_service import LedgerService
    
    # We expect a multipart/form-data with a 'file' field
    form = await request.form()
    file_item = form.get("file")
    
    if not file_item or not hasattr(file_item, "filename"):
        return JSONResponse({"error": "No CSV file provided."}, status_code=400)
        
    try:
        content_bytes = await file_item.read()
        csv_content = content_bytes.decode('utf-8')
        
        # Optionally, get actor from form or use default
        actor = form.get("agent", "Dashboard Agent")
        if isinstance(actor, bytes):
            actor = actor.decode('utf-8')
            
        result = await LedgerService.import_swipesimple_csv(csv_content, actor)
        return JSONResponse(status_code=200, content=result)
        
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)