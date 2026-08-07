"""
ShamrockLeads — SwipeSimple Payment Reconciliation Service
===========================================================
Monitors SwipeSimple webhooks and Gmail email receipts for credit card payment confirmations.
Parses Defendant name / reference notes and reconciles payments to active bond cases.
"""
import re
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class SwipeSimpleReconciliationService:
    """
    Reconciles incoming SwipeSimple payments against MongoDB active_bonds & intake_queue.
    """

    def __init__(self, db):
        self.db = db

    @property
    def bonds(self):
        return self.db["active_bonds"]

    @property
    def payments(self):
        return self.db["payments"]

    @property
    def intake(self):
        return self.db["intake_queue"]

    @staticmethod
    def parse_swipesimple_receipt(receipt_text: str, subject: str = "") -> Dict[str, Any]:
        """
        Parse SwipeSimple email receipt or webhook payload text.
        """
        if not receipt_text:
            return {}

        out: Dict[str, Any] = {
            "amount": 0.0,
            "transaction_id": "",
            "cardholder_name": "",
            "defendant_name": "",
            "notes": "",
            "raw_text": receipt_text,
        }

        # Amount pattern ($1,250.00 or 1250.00)
        m_amt = re.search(r"\$\s*([\d,]+\.\d{2})", receipt_text)
        if m_amt:
            try:
                out["amount"] = float(m_amt.group(1).replace(",", ""))
            except ValueError:
                pass

        # Transaction ID (e.g., Transaction ID: TX-99887766 or ID: 99887766)
        m_tx = re.search(r"(?:Transaction\s*ID|Transaction\s*#|Txn\s*ID|Ref\s*#)[:\s]*#?\s*([A-Z0-9\-]{5,})", receipt_text, re.IGNORECASE)
        if m_tx:
            out["transaction_id"] = m_tx.group(1).strip()

        # Defendant Name in Notes / Custom Field / For line
        m_def = re.search(r"(?:Defendant|Note|Memo|Reference)[:\s]*(?:For\s+)?([^\r\n]+)", receipt_text, re.IGNORECASE)
        if m_def:
            out["defendant_name"] = m_def.group(1).strip().title()

        # Cardholder Name
        m_card = re.search(r"(?:Cardholder\s*Name|Cardholder|Payer)[:\s]*([^\r\n]+)", receipt_text, re.IGNORECASE)
        if m_card:
            out["cardholder_name"] = m_card.group(1).strip().title()

        return out

    async def reconcile_payment(self, payment_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Match payment data to bond record and update premium status in MongoDB.
        """
        defendant_name = payment_data.get("defendant_name") or ""
        amount = payment_data.get("amount", 0.0)
        tx_id = payment_data.get("transaction_id") or f"TX-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        now_iso = datetime.now(timezone.utc).isoformat()
        payment_record = {
            "transaction_id": tx_id,
            "amount": amount,
            "cardholder_name": payment_data.get("cardholder_name", ""),
            "defendant_name": defendant_name,
            "notes": payment_data.get("notes", ""),
            "received_at": now_iso,
            "status": "reconciled",
        }

        # Insert to payments collection
        await self.payments.update_one(
            {"transaction_id": tx_id},
            {"$set": payment_record},
            upsert=True,
        )

        matched_bond = None
        if defendant_name:
            # Query active_bonds for matching defendant
            matched_bond = await self.bonds.find_one(
                {"defendant_name": {"$regex": f"^{re.escape(defendant_name)}$", "$options": "i"}}
            )
            if matched_bond:
                await self.bonds.update_one(
                    {"_id": matched_bond["_id"]},
                    {
                        "$set": {
                            "premium_paid": True,
                            "premium_paid_amount": amount,
                            "last_payment_at": now_iso,
                            "last_payment_tx": tx_id,
                        },
                        "$push": {"payment_history": payment_record},
                    },
                )
                logger.info("[SwipeSimple] Payment %s reconciled to bond %s", tx_id, matched_bond.get("poa_number"))

        return {
            "reconciled": bool(matched_bond),
            "transaction_id": tx_id,
            "amount": amount,
            "matched_bond_id": str(matched_bond["_id"]) if matched_bond else None,
        }
