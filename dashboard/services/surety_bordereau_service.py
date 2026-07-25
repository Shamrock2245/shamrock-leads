"""
Monthly Surety Bordereau Exporter Service — ShamrockLeads
Generates official monthly Bordereau liability reports required by:
  1. O'Shaughnahill Surety & Insurance (OSI - West Palm Beach, FL)
  2. Palmetto Surety Corporation (PSC)

Summarizes:
  - Bonds Executed during the month
  - Powers of Attorney (POA) Serial Numbers used
  - Gross Premium (10%) & Net Premium Owed to Surety
  - Build-Up Fund (BUF 1% deduction) Escrow Accruals
  - Discharges / Exonerations & Voided POAs
"""

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from dashboard.extensions import get_collection

logger = logging.getLogger("shamrock.bordereau")

async def generate_bordereau_data(
    surety_id: str = "osi",
    year: Optional[int] = None,
    month: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Query active_bonds, poa_inventory, and payments to compile the monthly bordereau report.
    """
    now = datetime.now(timezone.utc)
    target_year = year or now.year
    target_month = month or now.month

    surety_clean = (surety_id or "osi").lower().strip()
    surety_label = "O'Shaughnahill Surety & Insurance (OSI)" if surety_clean == "osi" else "Palmetto Surety Corporation (PSC)"

    # Define date range for target month
    start_date = datetime(target_year, target_month, 1, 0, 0, 0, tzinfo=timezone.utc)
    if target_month == 12:
        end_date = datetime(target_year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        end_date = datetime(target_year, target_month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    active_bonds_col = get_collection("active_bonds")

    query = {
        "$or": [
            {"surety_id": surety_clean},
            {"insurance_company": surety_clean}
        ]
    }
    cursor = active_bonds_col.find(query)
    bonds = await cursor.to_list(length=1000)

    rows = []
    total_bond_amount = 0.0
    total_gross_premium = 0.0
    total_buf_escrow = 0.0
    total_net_surety = 0.0
    exonerated_count = 0
    active_count = 0

    for b in bonds:
        created_str = b.get("created_at") or b.get("posted_at") or b.get("bond_date") or ""
        dt_exec = None
        if isinstance(created_str, datetime):
            dt_exec = created_str
        elif isinstance(created_str, str) and created_str:
            try:
                dt_exec = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            except Exception:
                pass

        if dt_exec and not (start_date <= dt_exec < end_date):
            continue

        bond_amt = float(b.get("bond_amount") or b.get("amount") or 0.0)
        gross_prem = float(b.get("gross_premium") or max(100.0, bond_amt * 0.10))
        buf_deduction = float(b.get("buf_deduction") or round(bond_amt * 0.01, 2))
        surety_cut_pct = 0.15 if surety_clean == "osi" else 0.18
        net_surety = float(b.get("net_surety_owed") or round(gross_prem * surety_cut_pct, 2))

        poa_num = b.get("poa_number") or b.get("poa_full") or "MANUAL"
        status = (b.get("status") or "active").lower()

        if status == "exonerated":
            exonerated_count += 1
        else:
            active_count += 1

        total_bond_amount += bond_amt
        total_gross_premium += gross_prem
        total_buf_escrow += buf_deduction
        total_net_surety += net_surety

        rows.append({
            "bond_id": str(b.get("_id", "")),
            "execution_date": dt_exec.strftime("%Y-%m-%d") if dt_exec else "N/A",
            "defendant_name": b.get("full_name") or b.get("defendant_name") or "Unknown",
            "booking_number": b.get("booking_number") or "",
            "case_number": b.get("case_number") or "",
            "county": b.get("county") or "Lee",
            "charge": b.get("charges") or b.get("charge") or "Unspecified Charge",
            "bond_amount": round(bond_amt, 2),
            "poa_number": poa_num,
            "gross_premium": round(gross_prem, 2),
            "buf_escrow_1pct": round(buf_deduction, 2),
            "net_surety_owed": round(net_surety, 2),
            "status": status.title(),
            "exonerated_at": b.get("exonerated_at", ""),
        })

    return {
        "surety_id": surety_clean,
        "surety_name": surety_label,
        "reporting_period": f"{target_year:04d}-{target_month:02d}",
        "summary": {
            "total_bonds_executed": len(rows),
            "active_bonds_count": active_count,
            "exonerated_bonds_count": exonerated_count,
            "total_bond_amount": round(total_bond_amount, 2),
            "total_gross_premium": round(total_gross_premium, 2),
            "total_buf_escrow_1pct": round(total_buf_escrow, 2),
            "total_net_surety_owed": round(total_net_surety, 2),
        },
        "rows": rows
    }

def export_bordereau_csv(bordereau_data: Dict[str, Any]) -> str:
    """
    Format bordereau data dictionary as a clean CSV string for Excel / Surety download.
    """
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["SHAMROCK BAIL BONDS — MONTHLY SURETY BORDEREAU REPORT"])
    writer.writerow(["Surety Company", bordereau_data.get("surety_name")])
    writer.writerow(["Reporting Period", bordereau_data.get("reporting_period")])
    writer.writerow(["Generated At", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")])
    writer.writerow([])

    headers = [
        "Execution Date", "Defendant Name", "Booking #", "Case #", "County",
        "Charge Description", "Bond Amount ($)", "POA Serial #",
        "Gross Premium (10%)", "BUF Escrow (1%)", "Net Owed to Surety", "Status"
    ]
    writer.writerow(headers)

    for r in bordereau_data.get("rows", []):
        writer.writerow([
            r["execution_date"],
            r["defendant_name"],
            r["booking_number"],
            r["case_number"],
            r["county"],
            r["charge"],
            f"${r['bond_amount']:,.2f}",
            r["poa_number"],
            f"${r['gross_premium']:,.2f}",
            f"${r['buf_escrow_1pct']:,.2f}",
            f"${r['net_surety_owed']:,.2f}",
            r["status"]
        ])

    summary = bordereau_data.get("summary", {})
    writer.writerow([])
    writer.writerow(["SUMMARY TOTALS"])
    writer.writerow(["Total Bonds Executed", summary.get("total_bonds_executed", 0)])
    writer.writerow(["Total Bond Amount Written", f"${summary.get('total_bond_amount', 0):,.2f}"])
    writer.writerow(["Total Gross Premium (10%)", f"${summary.get('total_gross_premium', 0):,.2f}"])
    writer.writerow(["Total BUF Escrow (1%)", f"${summary.get('total_buf_escrow_1pct', 0):,.2f}"])
    writer.writerow(["Total Net Owed to Surety", f"${summary.get('total_net_surety_owed', 0):,.2f}"])

    return output.getvalue()
