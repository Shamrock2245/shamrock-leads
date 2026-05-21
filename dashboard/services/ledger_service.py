from datetime import datetime
from typing import List, Dict, Any
from dashboard.extensions import get_db
from dashboard.models.ledger import LedgerEntry

class LedgerService:
    """Service to handle immutable ledger entries and calculate balances."""
    
    @staticmethod
    async def add_entry(entry_data: Dict[str, Any]) -> str:
        """Adds a new immutable entry to the ledger."""
        db = get_db()
        # Validate data with Pydantic model
        entry = LedgerEntry(**entry_data)
        doc = entry.dict()
        
        await db.financial_ledger.insert_one(doc)
        return doc["transaction_id"]

    @staticmethod
    async def get_balance(booking_number: str) -> float:
        """Calculates the total outstanding balance for a given booking number."""
        db = get_db()
        pipeline = [
            {"$match": {"booking_number": booking_number}},
            {"$group": {"_id": "$booking_number", "total_balance": {"$sum": "$amount"}}}
        ]
        result = await db.financial_ledger.aggregate(pipeline).to_list(1)
        if result:
            return result[0].get("total_balance", 0.0)
        return 0.0

    @staticmethod
    async def get_ledger_history(booking_number: str) -> List[Dict[str, Any]]:
        """Retrieves the full ledger history for a booking number."""
        db = get_db()
        cursor = db.financial_ledger.find({"booking_number": booking_number}, {"_id": 0}).sort("timestamp", -1)
        return await cursor.to_list(None)

    @staticmethod
    async def import_swipesimple_csv(csv_content: str, actor: str) -> Dict[str, Any]:
        """Imports payment records from a SwipeSimple CSV string."""
        import csv
        import io
        from datetime import datetime
        
        db = get_db()
        reader = csv.DictReader(io.StringIO(csv_content))
        
        success_count = 0
        errors = []
        
        for row_num, row in enumerate(reader, start=2): # Header is line 1
            try:
                # Basic column extraction based on common SwipeSimple exports
                transaction_id = row.get("Transaction ID", "").strip()
                amount_str = row.get("Amount", "0").replace("$", "").replace(",", "").strip()
                amount = float(amount_str) if amount_str else 0.0
                status = row.get("Status", "").strip().lower()
                date_str = row.get("Date", "").strip() # Assuming MM/DD/YYYY HH:MM format
                customer = row.get("Customer Name", "").strip()
                booking_number = row.get("Reference", "").strip() # Using 'Reference' field for booking_number
                
                if not booking_number:
                    # Look in notes if Reference is empty
                    notes = row.get("Notes", "")
                    if "booking:" in notes.lower():
                        parts = notes.lower().split("booking:")
                        if len(parts) > 1:
                            booking_number = parts[1].split()[0].strip()
                            
                if not booking_number:
                    errors.append(f"Row {row_num}: Could not determine booking number.")
                    continue
                    
                if status != "approved":
                    errors.append(f"Row {row_num}: Skipping transaction with status '{status}'.")
                    continue
                    
                # Check for duplicates by transaction ID
                existing = await db.financial_ledger.find_one({"stripe_swipe_ref": transaction_id})
                if existing:
                    errors.append(f"Row {row_num}: Transaction {transaction_id} already imported.")
                    continue
                    
                # Parse timestamp
                try:
                    timestamp = datetime.strptime(date_str, "%m/%d/%Y %H:%M")
                except ValueError:
                    timestamp = datetime.utcnow() # Fallback
                
                # Construct Ledger Entry
                entry_data = {
                    "booking_number": booking_number,
                    "type": "payment",
                    "amount": -amount, # Payments reduce the balance
                    "category": "premium",
                    "timestamp": timestamp,
                    "actor": actor,
                    "stripe_swipe_ref": transaction_id,
                    "notes": f"SwipeSimple Import. Customer: {customer}"
                }
                
                await LedgerService.add_entry(entry_data)
                success_count += 1
                
            except Exception as e:
                errors.append(f"Row {row_num}: Error processing - {str(e)}")
                
        return {
            "imported": success_count,
            "errors": errors
        }
