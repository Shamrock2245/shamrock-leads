"""
Forfeiture Remittitur & Motion-to-Vacate Countdown Clock Service — ShamrockLeads
F.S. 903.26 Forfeiture Watchdog & Summary Judgment Countdown Engine.

Tracks:
  - 60-Day Motion to Vacate window (100% Remittitur)
  - 180-Day Remittitur window (Tiered Remittitur return to Surety)
  - Summary Judgment Entry Deadline Date
  - Motion to Vacate filing status & Attorney Assignment
  - Automated Slack + BlueBubbles alerts at 30d, 14d, 7d remaining thresholds
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from dashboard.extensions import get_collection

logger = logging.getLogger("shamrock.remittitur")

async def get_forfeiture_remittitur_clocks() -> List[Dict[str, Any]]:
    """
    Query active_bonds for bonds with status 'forfeited' or 'alert'
    and calculate F.S. 903.26 remittitur countdown clocks.
    """
    active_bonds_col = get_collection("active_bonds")
    now = datetime.now(timezone.utc)

    # Query bonds with status forfeited, alert, or monitoring with forfeiture flags
    cursor = active_bonds_col.find({
        "$or": [
            {"status": "forfeited"},
            {"status": "alert"},
            {"forfeiture_order_date": {"$exists": True, "$ne": None, "$ne": ""}}
        ]
    }).sort("created_at", -1)

    forfeited_bonds = await cursor.to_list(length=200)
    clocks = []

    for b in forfeited_bonds:
        order_date_str = b.get("forfeiture_order_date") or b.get("forfeited_at") or b.get("updated_at") or b.get("created_at") or ""
        dt_order = None

        if isinstance(order_date_str, datetime):
            dt_order = order_date_str
        elif isinstance(order_date_str, str) and order_date_str:
            try:
                dt_order = datetime.fromisoformat(order_date_str.replace("Z", "+00:00"))
            except Exception:
                pass

        if not dt_order:
            dt_order = now - timedelta(days=10)  # Default fallback to 10 days ago if missing

        # Calculate Florida F.S. 903.26 Deadlines
        deadline_60d = dt_order + timedelta(days=60)
        deadline_180d = dt_order + timedelta(days=180)

        days_elapsed = (now - dt_order).days
        days_left_60d = max(0, 60 - days_elapsed)
        days_left_180d = max(0, 180 - days_elapsed)

        # F.S. 903.26 Remittitur Percentage Scale
        if days_elapsed <= 60:
            remittitur_pct = 100.0
        elif days_elapsed <= 90:
            remittitur_pct = 95.0
        elif days_elapsed <= 120:
            remittitur_pct = 90.0
        elif days_elapsed <= 180:
            remittitur_pct = 85.0
        else:
            remittitur_pct = 0.0

        bond_amt = float(b.get("bond_amount") or b.get("amount") or 0.0)
        potential_remittitur = round(bond_amt * (remittitur_pct / 100.0), 2)

        motion_status = b.get("motion_to_vacate_status", "pending")  # pending, motion_filed, remittitur_granted, paid
        attorney = b.get("assigned_attorney", "Unassigned")

        clocks.append({
            "bond_id": str(b.get("_id", "")),
            "booking_number": b.get("booking_number", ""),
            "defendant_name": b.get("full_name") or b.get("defendant_name") or "Unknown",
            "county": b.get("county") or "Lee",
            "bond_amount": bond_amt,
            "surety_id": b.get("surety_id") or b.get("insurance_company") or "osi",
            "poa_number": b.get("poa_number", "N/A"),
            "forfeiture_order_date": dt_order.strftime("%Y-%m-%d"),
            "days_elapsed": days_elapsed,
            "deadline_60d": deadline_60d.strftime("%Y-%m-%d"),
            "days_left_60d": days_left_60d,
            "deadline_180d": deadline_180d.strftime("%Y-%m-%d"),
            "days_left_180d": days_left_180d,
            "remittitur_percentage": remittitur_pct,
            "potential_remittitur_amount": potential_remittitur,
            "motion_status": motion_status,
            "assigned_attorney": attorney,
            "urgency_level": "CRITICAL" if days_left_60d <= 7 else "HIGH" if days_left_60d <= 21 else "MEDIUM"
        })

    return clocks

async def update_remittitur_status(booking_number: str, motion_status: str, attorney: Optional[str] = None) -> Dict[str, Any]:
    """Update attorney assignment and motion status for a forfeited bond."""
    active_bonds_col = get_collection("active_bonds")
    now = datetime.now(timezone.utc)

    set_dict = {
        "motion_to_vacate_status": motion_status,
        "motion_status_updated_at": now.isoformat()
    }
    if attorney:
        set_dict["assigned_attorney"] = attorney

    res = await active_bonds_col.update_one(
        {"booking_number": booking_number},
        {"$set": set_dict}
    )

    if res.matched_count == 0:
        raise ValueError("Bond record not found")

    return {"success": True, "booking_number": booking_number, "motion_status": motion_status, "assigned_attorney": attorney}
