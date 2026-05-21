from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import datetime
from uuid import UUID, uuid4

class LedgerEntry(BaseModel):
    transaction_id: str = Field(default_factory=lambda: str(uuid4()))
    booking_number: str
    indemnitor_id: Optional[str] = None
    type: Literal["charge", "payment", "refund", "fee", "forfeiture_penalty", "credit", "debit"]
    amount: float  # Positive for charges/fees, negative for payments/refunds
    category: Literal["premium", "recovery_fee", "transfer_fee", "buf", "other"]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    actor: str
    stripe_swipe_ref: Optional[str] = None
    notes: Optional[str] = None
