# ── AUTO-MIGRATED: Quart Blueprint → FastAPI APIRouter (v3) ──
# _qp = dict(request.query_params) injected into fns that read query params.
# Review each endpoint and move _qp.get() calls to typed fn signatures.

"""
ShamrockLeads — BlueBubbles Contact Sync
=========================================
Keeps the Mac's Contacts.app synchronized with MongoDB so that
defendants and indemnitors show up by name in Messages.app on the iMac.

Why This Matters
----------------
When an agent looks at Messages.app on the iMac, they see phone numbers
instead of names unless the contact exists in Contacts.app. This module
automatically creates contacts for every defendant and indemnitor so
agents always see "Jane Smith (Bond - JOHN SMITH)" instead of "+12395550178".

Contact Naming Convention
--------------------------
  Indemnitors: "Jane Smith [Bond - JOHN SMITH]"
  Defendants:  "JOHN SMITH [Defendant - Lee]"

This makes it easy to search in Messages.app and identify who is who.

Endpoints
---------
  POST   /api/bb-contacts/sync-bond     — Sync contacts for a single bond
  POST   /api/bb-contacts/sync-all      — Sync all active bonds' contacts
  GET    /api/bb-contacts/list          — List all contacts on the Mac
  POST   /api/bb-contacts/check         — Check if a phone is on iMessage
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.api.bb_private_api import BlueBubblesClient
from dashboard.extensions import BB_SERVERS, get_collection, format_phone

logger = logging.getLogger(__name__)

bb_contacts_bp = APIRouter(prefix="/api", tags=["bb_contact_sync"])
async def _get_bb_client() -> Optional[BlueBubblesClient]:
    """Get the primary BlueBubbles client."""
    bb_server = next(iter(BB_SERVERS.values()), None) if BB_SERVERS else None
    if not bb_server:
        return None
    return BlueBubblesClient(bb_server["url"], bb_server["password"])


async def sync_bond_contacts(bond_doc: dict) -> dict:
    """Sync contacts for a single bond to the Mac's Contacts.app.

    Creates contacts for:
      - The defendant
      - The indemnitor(s)

    Returns:
        { "created": int, "skipped": int, "errors": list }
    """
    bb_client = await _get_bb_client()
    if not bb_client:
        return {"created": 0, "skipped": 0, "errors": ["No BlueBubbles server configured"]}

    defendant_name = bond_doc.get("defendant_name", "")
    county = bond_doc.get("county", "")
    booking_number = bond_doc.get("booking_number", "")
    indemnitor = bond_doc.get("indemnitor") or {}

    created = 0
    skipped = 0
    errors = []

    # ── Create indemnitor contact ──────────────────────────────────────────
    indemnitor_phone = format_phone(indemnitor.get("phone", ""))
    indemnitor_name = indemnitor.get("name", "")

    if indemnitor_phone and indemnitor_name:
        # Check if contact already exists
        existing = await bb_client.query_contacts([indemnitor_phone])
        existing_contacts = (existing.get("data") or [])
        already_exists = any(
            c.get("phoneNumbers") and any(
                p.get("address") == indemnitor_phone
                for p in c.get("phoneNumbers", [])
            )
            for c in existing_contacts
        )

        if not already_exists:
            name_parts = indemnitor_name.strip().split()
            first = name_parts[0] if name_parts else indemnitor_name
            last_parts = name_parts[1:] if len(name_parts) > 1 else []
            # Append bond identifier to last name for easy search
            last = " ".join(last_parts) + f" [Bond-{defendant_name.split()[0]}]" if last_parts else f"[Bond-{defendant_name.split()[0]}]"

            result = await bb_client.create_contact(
                first_name=first,
                last_name=last,
                phone=indemnitor_phone,
            )
            if result.get("success"):
                created += 1
                logger.info("👤 Created contact for indemnitor %s (...%s)", indemnitor_name, indemnitor_phone[-4:])
            else:
                errors.append(f"Failed to create indemnitor contact: {result.get('error', 'unknown')}")
        else:
            skipped += 1

    # ── Log sync ──────────────────────────────────────────────────────────
    sync_coll = get_collection("bb_contact_syncs")
    await sync_coll.update_one(
        {"booking_number": booking_number},
        {"$set": {
            "booking_number": booking_number,
            "defendant_name": defendant_name,
            "county": county,
            "contacts_created": created,
            "contacts_skipped": skipped,
            "last_synced": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )

    return {"created": created, "skipped": skipped, "errors": errors}


# ─────────────────────────────────────────────────────────────────────────────
#  API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@bb_contacts_bp.post("/bb-contacts/sync-bond")
async def api_sync_bond_contacts():
    """Sync contacts for a single bond to the Mac's Contacts.app.

    Body:
        { "booking_number": "2024-00123" }
    """
    try:
        data = await request.json() or {}
        booking_number = (data.get("booking_number") or "").strip()
        if not booking_number:
            return {"success": False, "error": "booking_number required"}, 400

        bonds_coll = get_collection("bonds")
        bond = await bonds_coll.find_one({"booking_number": booking_number}, {"_id": 0})
        if not bond:
            # Try prospective bonds
            prospective_coll = get_collection("prospective_bonds")
            bond = await prospective_coll.find_one({"booking_number": booking_number}, {"_id": 0})

        if not bond:
            return {"success": False, "error": f"Bond {booking_number} not found"}, 404

        result = await sync_bond_contacts(bond)
        return {"success": True, "booking_number": booking_number, **result}

    except Exception as e:
        logger.error("Sync bond contacts error: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}, 500


@bb_contacts_bp.post("/bb-contacts/sync-all")
async def api_sync_all_contacts():
    """Sync contacts for all active bonds to the Mac's Contacts.app.

    This is a bulk operation — run once to catch up, then sync-bond
    handles new bonds as they come in.
    """
    try:
        bonds_coll = get_collection("bonds")
        active_bonds = await bonds_coll.find(
            {"status": "active"},
            {"_id": 0, "booking_number": 1, "defendant_name": 1, "county": 1, "indemnitor": 1}
        ).to_list(length=500)

        total_created = 0
        total_skipped = 0
        total_errors = []

        for bond in active_bonds:
            result = await sync_bond_contacts(bond)
            total_created += result.get("created", 0)
            total_skipped += result.get("skipped", 0)
            total_errors.extend(result.get("errors", []))

        return {
            "success": True,
            "bonds_processed": len(active_bonds),
            "contacts_created": total_created,
            "contacts_skipped": total_skipped,
            "errors": total_errors[:10],  # Limit error list
        }

    except Exception as e:
        logger.error("Sync all contacts error: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}, 500


@bb_contacts_bp.post("/bb-contacts/check")
async def api_check_imessage():
    """Check if one or more phone numbers are on iMessage.

    Body:
        { "phones": ["+12395550178", "+12395550314"] }

    Returns:
        { "results": { "+12395550178": true, "+12395550314": false } }
    """
    try:
        data = await request.json() or {}
        phones = data.get("phones", [])
        if not phones:
            return {"success": False, "error": "phones array required"}, 400

        bb_client = await _get_bb_client()
        if not bb_client:
            return {"success": False, "error": "No BlueBubbles server configured"}, 503

        results = {}
        for phone in phones:
            phone = format_phone(phone)
            avail = await bb_client.check_imessage_availability(phone)
            results[phone] = avail.get("available", False)

        imessage_count = sum(1 for v in results.values() if v)
        return {
            "success": True,
            "results": results,
            "imessage_count": imessage_count,
            "sms_count": len(results) - imessage_count,
        }

    except Exception as e:
        logger.error("Check iMessage error: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}, 500


@bb_contacts_bp.get("/bb-contacts/list")
async def api_list_contacts():
    """List all contacts from the Mac's Contacts.app."""
    try:
        bb_client = await _get_bb_client()
        if not bb_client:
            return {"success": False, "error": "No BlueBubbles server configured"}, 503

        result = await bb_client.get_contacts()
        contacts = result.get("data", [])
        return {"success": True, "count": len(contacts), "contacts": contacts}

    except Exception as e:
        return {"success": False, "error": str(e)}, 500
