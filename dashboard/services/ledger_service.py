"""
ShamrockLeads — Ledger Service
================================
Immutable double-entry ledger for bond financial tracking.

Rules:
  - Entries are NEVER deleted or updated (immutable audit trail)
  - Dedup key for SwipeSimple imports: stripe_swipe_ref (Transaction ID)
  - Balances are rounded to 2dp to avoid IEEE-754 floating-point drift
  - All timestamps stored as timezone-aware UTC datetimes
"""
import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from dashboard.extensions import get_db
from dashboard.models.ledger import LedgerEntry

logger = logging.getLogger(__name__)

# Columns we expect from a SwipeSimple CSV export.
_SS_REQUIRED_COLS = {"Transaction ID", "Amount", "Status", "Date"}
_SS_DATE_FORMATS = [
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%m/%d/%Y",
]


class LedgerService:
    """Service to handle immutable ledger entries and calculate balances."""

    @staticmethod
    async def add_entry(entry_data: Dict[str, Any]) -> str:
        """Adds a new immutable entry to the ledger."""
        db = get_db()
        entry = LedgerEntry(**entry_data)
        doc = entry.dict()
        # Ensure timestamp is timezone-aware
        if isinstance(doc.get("timestamp"), datetime) and doc["timestamp"].tzinfo is None:
            doc["timestamp"] = doc["timestamp"].replace(tzinfo=timezone.utc)
        await db.financial_ledger.insert_one(doc)
        return doc["transaction_id"]

    @staticmethod
    async def get_balance(booking_number: str) -> float:
        """Calculates the total outstanding balance for a given booking number.

        Uses MongoDB $group aggregation. Rounds to 2 decimal places to
        eliminate IEEE-754 floating-point drift. Returns 0.0 on any error.
        """
        db = get_db()
        try:
            pipeline = [
                {"$match": {"booking_number": booking_number}},
                {
                    "$group": {
                        "_id": "$booking_number",
                        "total_balance": {"$sum": "$amount"},
                    }
                },
            ]
            result = await db.financial_ledger.aggregate(pipeline).to_list(1)
            if result:
                raw = result[0].get("total_balance", 0.0) or 0.0
                return round(float(raw), 2)
            return 0.0
        except Exception as exc:
            logger.error("LedgerService.get_balance error for %s: %s", booking_number, exc)
            return 0.0

    @staticmethod
    async def get_ledger_history(booking_number: str) -> List[Dict[str, Any]]:
        """Retrieves the full ledger history for a booking number, newest first."""
        db = get_db()
        try:
            cursor = (
                db.financial_ledger
                .find({"booking_number": booking_number}, {"_id": 0})
                .sort("timestamp", -1)
            )
            return await cursor.to_list(None)
        except Exception as exc:
            logger.error("LedgerService.get_ledger_history error for %s: %s", booking_number, exc)
            return []

    @staticmethod
    async def import_swipesimple_csv(csv_content: str, actor: str) -> Dict[str, Any]:
        """Imports payment records from a SwipeSimple CSV string.

        Robust against:
          - Missing / renamed columns (logs warning, attempts fallback)
          - Non-approved transactions (skipped, not errored)
          - Duplicate Transaction IDs (idempotent)
          - Unparseable amounts or dates (logged per-row, processing continues)
          - Empty booking reference (tries 'Notes'/'Description' field as fallback)
        """
        db = get_db()
        reader = csv.DictReader(io.StringIO(csv_content))

        # Validate column presence up-front
        if reader.fieldnames:
            missing = _SS_REQUIRED_COLS - set(reader.fieldnames)
            if missing:
                logger.warning(
                    "SwipeSimple CSV is missing expected columns: %s — "
                    "import will attempt best-effort parsing.",
                    missing,
                )
        else:
            return {"imported": 0, "errors": ["CSV appears empty or has no header row."]}

        success_count = 0
        errors: List[str] = []

        for row_num, row in enumerate(reader, start=2):  # Header is line 1
            try:
                transaction_id = (row.get("Transaction ID") or "").strip()
                if not transaction_id:
                    errors.append(f"Row {row_num}: Missing Transaction ID — skipped.")
                    continue

                # ── Amount ────────────────────────────────────────────────
                raw_amount = (row.get("Amount") or "0").replace("$", "").replace(",", "").strip()
                try:
                    amount = float(raw_amount) if raw_amount else 0.0
                except ValueError:
                    errors.append(
                        f"Row {row_num}: Unparseable amount '{raw_amount}' — skipped."
                    )
                    continue

                # ── Status ────────────────────────────────────────────────
                status = (row.get("Status") or "").strip().lower()
                if status not in ("approved", "completed", "success"):
                    errors.append(
                        f"Row {row_num}: Skipping transaction {transaction_id} "
                        f"with status '{status}'."
                    )
                    continue

                # ── Booking number ────────────────────────────────────────
                booking_number = (row.get("Reference") or "").strip()
                if not booking_number:
                    notes = row.get("Notes") or row.get("Description") or ""
                    lower_notes = notes.lower()
                    for prefix in ("booking:", "booking #", "bk:"):
                        if prefix in lower_notes:
                            after = lower_notes.split(prefix, 1)[1]
                            booking_number = after.split()[0].strip().upper()
                            break
                if not booking_number:
                    errors.append(
                        f"Row {row_num}: Could not determine booking number "
                        f"for transaction {transaction_id} — skipped."
                    )
                    continue

                # ── Duplicate check ───────────────────────────────────────
                existing = await db.financial_ledger.find_one(
                    {"stripe_swipe_ref": transaction_id}
                )
                if existing:
                    errors.append(
                        f"Row {row_num}: Transaction {transaction_id} already imported — skipped."
                    )
                    continue

                # ── Timestamp ─────────────────────────────────────────────
                date_str = (row.get("Date") or "").strip()
                timestamp: datetime = datetime.now(timezone.utc)
                for fmt in _SS_DATE_FORMATS:
                    try:
                        parsed = datetime.strptime(date_str, fmt)
                        timestamp = parsed.replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        continue
                else:
                    if date_str:
                        logger.warning(
                            "Row %d: Could not parse date '%s' — using now().",
                            row_num,
                            date_str,
                        )

                customer = (row.get("Customer Name") or "").strip()

                entry_data = {
                    "booking_number": booking_number,
                    "type": "payment",
                    "amount": -round(amount, 2),  # Payments reduce the balance
                    "category": "premium",
                    "timestamp": timestamp,
                    "actor": actor,
                    "stripe_swipe_ref": transaction_id,
                    "notes": f"SwipeSimple Import. Customer: {customer}",
                }
                await LedgerService.add_entry(entry_data)
                success_count += 1

            except Exception as exc:
                errors.append(f"Row {row_num}: Unexpected error — {exc}")
                logger.exception("SwipeSimple import error at row %d", row_num)

        return {"imported": success_count, "errors": errors}
