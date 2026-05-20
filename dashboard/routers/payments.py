
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