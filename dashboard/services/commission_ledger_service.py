"""
Commission & Build-Up Fund (BUF) Ledger Service — ShamrockLeads
Tracks 1099 writing agent commission splits, agency revenue share,
transfer fees, and 1% Build-Up Fund (BUF) escrow balances per agent.
"""

import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from dashboard.extensions import get_collection

logger = logging.getLogger("shamrock.finance")

async def record_bond_financial_split(
    booking_number: str,
    bond_amount: float,
    writing_agent_name: str,
    agent_split_pct: float = 0.50,
    surety_id: str = "osi",
    transfer_fee: float = 0.0
) -> Dict[str, Any]:
    """
    Calculate and log writing agent commission, 1% BUF escrow deduction,
    agency revenue share, and update agent's cumulative BUF balance.
    """
    finance_col = get_collection("financial_ledger")
    buf_col = get_collection("buf_escrow_balances")
    now = datetime.now(timezone.utc)

    gross_premium = max(100.0, round(bond_amount * 0.10, 2))
    buf_deduction = round(bond_amount * 0.01, 2)  # 1% BUF deduction
    surety_fee = round(gross_premium * (0.15 if surety_id.lower() == "osi" else 0.18), 2)

    net_after_surety = max(0.0, gross_premium - surety_fee - buf_deduction)
    agent_commission = round((net_after_surety * agent_split_pct) + transfer_fee, 2)
    house_revenue = round(net_after_surety * (1.0 - agent_split_pct), 2)

    record = {
        "transaction_id": str(uuid.uuid4()),
        "booking_number": booking_number,
        "writing_agent_name": writing_agent_name,
        "surety_id": surety_id,
        "bond_amount": round(bond_amount, 2),
        "gross_premium": gross_premium,
        "buf_deduction_1pct": buf_deduction,
        "surety_fee": surety_fee,
        "transfer_fee": transfer_fee,
        "agent_commission": agent_commission,
        "house_revenue": house_revenue,
        "created_at": now.isoformat()
    }
    await finance_col.insert_one(record)

    # Update cumulative BUF balance for agent
    buf_doc = await buf_col.find_one({"agent_name": writing_agent_name})
    if not buf_doc:
        buf_doc = {
            "agent_name": writing_agent_name,
            "cumulative_buf_balance": buf_deduction,
            "bonds_written_count": 1,
            "last_updated_at": now.isoformat()
        }
        await buf_col.insert_one(buf_doc)
    else:
        new_balance = round(float(buf_doc.get("cumulative_buf_balance", 0.0)) + buf_deduction, 2)
        count = int(buf_doc.get("bonds_written_count", 0)) + 1
        await buf_col.update_one(
            {"agent_name": writing_agent_name},
            {"$set": {
                "cumulative_buf_balance": new_balance,
                "bonds_written_count": count,
                "last_updated_at": now.isoformat()
            }}
        )

    record.pop("_id", None)
    return record

async def get_agent_buf_balances() -> List[Dict[str, Any]]:
    """Get list of writing agents and cumulative BUF escrow balances."""
    buf_col = get_collection("buf_escrow_balances")
    cursor = buf_col.find({}).sort("cumulative_buf_balance", -1)
    docs = await cursor.to_list(length=100)
    for d in docs:
        d.pop("_id", None)
    return docs

async def get_commission_ledger(writing_agent_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get financial commission transactions log."""
    finance_col = get_collection("financial_ledger")
    query = {}
    if writing_agent_name:
        query["writing_agent_name"] = writing_agent_name

    cursor = finance_col.find(query).sort("created_at", -1)
    records = await cursor.to_list(length=500)
    for r in records:
        r.pop("_id", None)
    return records
