"""
ShamrockLeads — Unified Financial Ledger Service
Handles all ledger entries, SwipeSimple CSV imports, and balance calculations.
Uses integer cents for internal calculations to prevent floating point inaccuracies.
"""

import logging
import csv
import io
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from dashboard.extensions import get_db

logger = logging.getLogger(__name__)

# Standard date formats for SwipeSimple parsing
_SS_DATE_FORMATS = [
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%m/%d/%Y",
]

class LedgerService:
    """Service for managing the unified financial ledger."""

    @staticmethod
    def to_cents(amount: float) -> int:
        """Convert float dollar amount to integer cents."""
        return int(round(float(amount) * 100))

    @staticmethod
    def from_cents(cents: int) -> float:
        """Convert integer cents back to float dollar amount."""
        return float(cents) / 100.0

    @classmethod
    async def add_entry(cls, data: Dict[str, Any]) -> str:
        """
        Add an entry to the financial ledger.
        Ensures amount is stored as integer cents for precision.
        """
        db = get_db()
        
        # Convert amount to cents for storage if not already provided as cents
        amount_raw = data.get("amount", 0)
        if isinstance(amount_raw, float) or (isinstance(amount_raw, str) and "." in amount_raw):
            amount_cents = cls.to_cents(float(amount_raw))
        else:
            amount_cents = int(amount_raw)
        
        # Ensure timestamp is a datetime object or ISO string
        ts = data.get("timestamp")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                ts = datetime.now(timezone.utc)
        elif not ts:
            ts = datetime.now(timezone.utc)
            
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        entry = {
            "booking_number": data.get("booking_number", "").strip().upper(),
            "type": data.get("type", "payment"),  # payment, fee, premium, refund
            "amount": amount_cents,  # Stored as integer cents
            "category": data.get("category", "premium"),
            "timestamp": ts,
            "actor": data.get("actor", "System"),
            "notes": data.get("notes", ""),
            "stripe_swipe_ref": data.get("stripe_swipe_ref", ""),
            "created_at": datetime.now(timezone.utc),
        }
        
        # Deduplicate using stripe_swipe_ref if present
        if entry["stripe_swipe_ref"]:
            existing = await db.financial_ledger.find_one({"stripe_swipe_ref": entry["stripe_swipe_ref"]})
            if existing:
                logger.info(f"Ledger: Skipping duplicate entry {entry['stripe_swipe_ref']}")
                return str(existing.get("transaction_id") or existing.get("_id"))

        # Generate a transaction ID if not present
        if "transaction_id" not in entry:
            import uuid
            entry["transaction_id"] = f"TXN-{uuid.uuid4().hex[:12].upper()}"

        await db.financial_ledger.insert_one(entry)
        return entry["transaction_id"]

    @classmethod
    async def get_balance(cls, booking_number: str) -> float:
        """Calculate current balance for a booking using integer aggregation."""
        db = get_db()
        
        pipeline = [
            {"$match": {"booking_number": booking_number.strip().upper()}},
            {"$group": {"_id": "$booking_number", "balance_cents": {"$sum": "$amount"}}}
        ]
        
        results = await db.financial_ledger.aggregate(pipeline).to_list(1)
        if not results:
            return 0.0
            
        return cls.from_cents(results[0]["balance_cents"])

    @classmethod
    async def get_ledger_history(cls, booking_number: str) -> List[Dict[str, Any]]:
        """Retrieves the full ledger history for a booking number, newest first."""
        db = get_db()
        cursor = (
            db.financial_ledger
            .find({"booking_number": booking_number.strip().upper()}, {"_id": 0})
            .sort("timestamp", -1)
        )
        history = await cursor.to_list(None)
        # Convert cents back to dollars for UI consumption
        for entry in history:
            entry["amount_dollars"] = cls.from_cents(entry["amount"])
        return history

    @classmethod
    async def import_swipesimple_csv(cls, csv_text: str, actor: str = "CSV Import") -> Dict[str, Any]:
        """
        Robustly parse SwipeSimple CSV and import into ledger.
        Handles various column name variations and ensures idempotency.
        """
        imported = 0
        skipped = 0
        errors = []
        
        # Strip preamble if present (find true header row)
        lines = csv_text.splitlines()
        header_idx = 0
        for idx, line in enumerate(lines):
            line_lower = line.lower()
            if "amount" in line_lower or "transaction id" in line_lower or "total" in line_lower:
                header_idx = idx
                break
        
        clean_csv = "\n".join(lines[header_idx:])
        reader = csv.DictReader(io.StringIO(clean_csv))
        
        for i, row in enumerate(reader):
            row_num = i + header_idx + 2
            try:
                # Normalize keys (strip whitespace, handle case for flexible matching)
                row_clean = {str(k).strip().lower(): v for k, v in row.items() if k}
                
                # Flexible column matching for Amount
                amount_str = row_clean.get("amount") or row_clean.get("total") or row_clean.get("net amount") or "0"
                amount_str = re.sub(r"[$,\s]", "", str(amount_str))
                try:
                    amount = float(amount_str)
                except ValueError:
                    errors.append(f"Row {row_num}: Invalid amount '{amount_str}'")
                    continue
                
                if amount <= 0:
                    skipped += 1
                    continue
                
                # Flexible column matching for Transaction ID
                txn_id = str(row_clean.get("transaction id") or row_clean.get("id") or row_clean.get("transaction #") or "").strip()
                if not txn_id:
                    errors.append(f"Row {row_num}: Missing Transaction ID")
                    continue

                # Status check
                status = str(row_clean.get("status") or row_clean.get("result") or "completed").strip().lower()
                if status not in ("approved", "completed", "settled", "success", "captured"):
                    skipped += 1
                    continue
                
                # Date parsing
                date_str = str(row_clean.get("date") or row_clean.get("created") or "").strip()
                time_str = str(row_clean.get("time") or "").strip()
                full_date_str = f"{date_str} {time_str}".strip()
                
                timestamp = datetime.now(timezone.utc)
                if date_str:
                    for fmt in _SS_DATE_FORMATS:
                        try:
                            timestamp = datetime.strptime(full_date_str if " " in full_date_str else date_str, fmt)
                            timestamp = timestamp.replace(tzinfo=timezone.utc)
                            break
                        except ValueError:
                            continue

                # Booking Number / Reference
                booking_number = str(row_clean.get("reference") or row_clean.get("booking #") or "").strip().upper()
                if not booking_number:
                    # Fallback: check notes/description for keywords
                    notes_text = str(row_clean.get("description") or row_clean.get("notes") or row_clean.get("memo") or "").lower()
                    for prefix in ("booking:", "booking #", "bk:", "ref:"):
                        if prefix in notes_text:
                            after = notes_text.split(prefix, 1)[1].strip()
                            booking_number = after.split()[0].upper()
                            break

                # Customer Info for notes
                cust = str(row_clean.get("customer name") or row_clean.get("name") or "").strip()
                desc = str(row_clean.get("description") or row_clean.get("memo") or "").strip()
                notes = f"SwipeSimple Import | Customer: {cust} | {desc}".strip(" | ")

                # Add to ledger (payments reduce balance, so amount is negative)
                await cls.add_entry({
                    "booking_number": booking_number,
                    "type": "payment",
                    "amount": -amount,
                    "category": "premium",
                    "timestamp": timestamp,
                    "actor": actor,
                    "stripe_swipe_ref": txn_id,
                    "notes": notes
                })
                imported += 1
                
            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")
                logger.exception("Error importing SwipeSimple row %d", row_num)
                
        return {
            "imported": imported,
            "skipped": skipped,
            "errors": len(errors),
            "error_details": errors[:10]
        }
