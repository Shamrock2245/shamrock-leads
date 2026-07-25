"""
Finance & Commissions API Blueprint — ShamrockLeads
Endpoints for Writing Agent 1099 Commission Splits, BUF Escrow Balances, and Financial Transactions.
"""

from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse
from typing import Optional

from dashboard.services.commission_ledger_service import (
    record_bond_financial_split,
    get_agent_buf_balances,
    get_commission_ledger
)

finance_bp = APIRouter(prefix="/api/finance", tags=["finance"])

@finance_bp.get("/commissions")
async def get_commissions_list(agent_name: Optional[str] = Query(None)):
    """Get itemized financial commission ledger."""
    try:
        records = await get_commission_ledger(writing_agent_name=agent_name)
        return JSONResponse(status_code=200, content={"success": True, "count": len(records), "records": records})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

@finance_bp.get("/buf-balances")
async def get_buf_balances_list():
    """Get cumulative Build-Up Fund (BUF) escrow balances per agent."""
    try:
        balances = await get_agent_buf_balances()
        return JSONResponse(status_code=200, content={"success": True, "count": len(balances), "agents": balances})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

@finance_bp.post("/record-split")
async def record_financial_split(request: Request):
    """Record financial commission split for a bond."""
    try:
        data = await request.json() or {}
        booking_number = data.get("booking_number")
        bond_amount = float(data.get("bond_amount", 0.0))
        agent_name = data.get("writing_agent_name", "House Agent")
        split_pct = float(data.get("agent_split_pct", 0.50))
        surety = data.get("surety_id", "osi")
        transfer_fee = float(data.get("transfer_fee", 0.0))

        if not booking_number or bond_amount <= 0:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid booking_number or bond_amount"})

        record = await record_bond_financial_split(
            booking_number=booking_number,
            bond_amount=bond_amount,
            writing_agent_name=agent_name,
            agent_split_pct=split_pct,
            surety_id=surety,
            transfer_fee=transfer_fee
        )
        return JSONResponse(status_code=200, content={"success": True, "record": record})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})
