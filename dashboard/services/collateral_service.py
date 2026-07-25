"""
Collateral Vault & Release Receipt Service — ShamrockLeads
Tracks physical collateral items taken against bonds (Deed, Vehicle Title, Cash Deposit, Jewelry, Firearms),
vault storage locations, depositor records, and generates official PDF Collateral Return Receipts upon exoneration.
"""

import io
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import fitz

from dashboard.extensions import get_collection

logger = logging.getLogger("shamrock.collateral")

async def add_collateral_item(data: Dict[str, Any]) -> Dict[str, Any]:
    """Record a new collateral item held in agency vault."""
    collateral_col = get_collection("collateral_items")
    now = datetime.now(timezone.utc)

    tag_number = f"COL-{now.strftime('%Y%m')}-{uuid.uuid4().hex[:6].upper()}"

    doc = {
        "collateral_id": str(uuid.uuid4()),
        "tag_number": tag_number,
        "booking_number": data.get("booking_number", ""),
        "defendant_name": data.get("defendant_name", ""),
        "depositor_name": data.get("depositor_name", ""),
        "depositor_phone": data.get("depositor_phone", ""),
        "item_type": data.get("item_type", "Other"),  # Real Estate, Vehicle, Cash Deposit, Jewelry, Firearms, Other
        "description": data.get("description", ""),
        "estimated_value": float(data.get("estimated_value", 0.0)),
        "storage_location": data.get("storage_location", "Main Safe / Cabinet A"),
        "status": "held",  # held, returned, liquidated
        "received_by": data.get("received_by", "Staff"),
        "received_at": now.isoformat(),
        "notes": data.get("notes", ""),
    }

    await collateral_col.insert_one(doc)

    # Log audit event
    audit_col = get_collection("audit_events")
    await audit_col.insert_one({
        "event_id": str(uuid.uuid4()),
        "event_type": "collateral_received",
        "booking_number": doc["booking_number"],
        "tag_number": tag_number,
        "item_type": doc["item_type"],
        "estimated_value": doc["estimated_value"],
        "created_at": now.isoformat()
    })

    doc.pop("_id", None)
    return doc

async def list_collateral_items(booking_number: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """List collateral items with optional filters."""
    collateral_col = get_collection("collateral_items")
    query = {}
    if booking_number:
        query["booking_number"] = booking_number
    if status:
        query["status"] = status

    cursor = collateral_col.find(query).sort("received_at", -1)
    items = await cursor.to_list(length=500)
    for it in items:
        it.pop("_id", None)
    return items

async def return_collateral_item(collateral_id: str, returned_by: str = "Staff", return_note: str = "") -> Dict[str, Any]:
    """Mark a collateral item as returned to depositor upon exoneration."""
    collateral_col = get_collection("collateral_items")
    now = datetime.now(timezone.utc)

    item = await collateral_col.find_one({"collateral_id": collateral_id})
    if not item:
        raise ValueError("Collateral item not found")

    await collateral_col.update_one(
        {"collateral_id": collateral_id},
        {"$set": {
            "status": "returned",
            "returned_by": returned_by,
            "returned_at": now.isoformat(),
            "return_note": return_note
        }}
    )

    # Audit log
    audit_col = get_collection("audit_events")
    await audit_col.insert_one({
        "event_id": str(uuid.uuid4()),
        "event_type": "collateral_returned",
        "booking_number": item.get("booking_number", ""),
        "tag_number": item.get("tag_number", ""),
        "returned_by": returned_by,
        "returned_at": now.isoformat()
    })

    item["status"] = "returned"
    item["returned_by"] = returned_by
    item["returned_at"] = now.isoformat()
    item.pop("_id", None)
    return item

def generate_collateral_receipt_pdf(item: Dict[str, Any]) -> bytes:
    """Generate a clean, printable PDF Collateral Receipt / Return Receipt."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)  # Standard Letter

    status = (item.get("status") or "held").upper()
    title_text = "OFFICIAL COLLATERAL RECEIPT" if status == "HELD" else "OFFICIAL COLLATERAL RETURN RECEIPT"

    # Header branding
    page.insert_text((50, 50), "SHAMROCK BAIL BONDS", fontsize=18, color=(0.09, 0.64, 0.29))
    page.insert_text((50, 70), "1528 Broadway, Ft. Myers, FL 33901 | Phone: (239) 955-0178", fontsize=9, color=(0.4, 0.4, 0.4))
    page.draw_line((50, 80), (562, 80), color=(0.8, 0.8, 0.8), width=1)

    # Document Title
    page.insert_text((50, 110), title_text, fontsize=14, color=(0.1, 0.1, 0.1))
    page.insert_text((420, 110), f"Tag #: {item.get('tag_number', 'N/A')}", fontsize=10, color=(0.3, 0.3, 0.3))

    # Details Grid
    y = 140
    fields = [
        ("Defendant Name:", item.get("defendant_name", "N/A")),
        ("Booking Number:", item.get("booking_number", "N/A")),
        ("Depositor Name:", item.get("depositor_name", "N/A")),
        ("Depositor Phone:", item.get("depositor_phone", "N/A")),
        ("Item Category:", item.get("item_type", "Other")),
        ("Estimated Value:", f"${float(item.get('estimated_value', 0)):,.2f}"),
        ("Vault Location:", item.get("storage_location", "Main Safe")),
        ("Status:", status),
        ("Received Date:", item.get("received_at", "")[:10]),
        ("Received By:", item.get("received_by", "Staff")),
    ]

    if status == "RETURNED":
        fields.append(("Returned Date:", (item.get("returned_at") or "")[:10]))
        fields.append(("Returned By:", item.get("returned_by", "Staff")))

    for label, val in fields:
        page.insert_text((50, y), label, fontsize=10, color=(0.2, 0.2, 0.2))
        page.insert_text((180, y), str(val), fontsize=10, color=(0, 0, 0))
        y += 22

    # Description Box
    y += 10
    page.insert_text((50, y), "Item Description / Serial Numbers:", fontsize=10, color=(0.2, 0.2, 0.2))
    y += 18
    desc_rect = fitz.Rect(50, y, 562, y + 60)
    page.draw_rect(desc_rect, color=(0.8, 0.8, 0.8), width=1)
    page.insert_textbox(desc_rect, item.get("description", "No description specified."), fontsize=9, color=(0, 0, 0))

    # Signatures
    y += 90
    page.draw_line((50, y), (250, y), color=(0, 0, 0), width=1)
    page.insert_text((50, y + 14), "Depositor Signature", fontsize=9, color=(0.3, 0.3, 0.3))

    page.draw_line((340, y), (540, y), color=(0, 0, 0), width=1)
    page.insert_text((340, y + 14), "Authorized Agent Signature", fontsize=9, color=(0.3, 0.3, 0.3))

    buf = doc.tobytes(deflate=True)
    doc.close()
    return buf
