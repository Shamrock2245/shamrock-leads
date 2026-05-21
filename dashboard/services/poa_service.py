"""
ShamrockLeads — POA Service
POA tier lookup, inventory seeding, and assignment logic.
"""

from dashboard.extensions import POA_RECEIPT_DATA

# ── POA Tier Definitions ──
TIERS = {
    "osi": [
        (3000, "OSI3"), (6000, "OSI6"), (16000, "OSI16"),
        (51000, "OSI51"), (101000, "OSI101"), (251000, "OSI251"),
    ],
    "palmetto": [
        (2000, "PSC2"), (5000, "PSC5"), (15000, "PSC15"), (25000, "PSC25"),
        (50000, "PSC50"), (75000, "PSC75"), (105000, "PSC105"),
    ],
}


def get_poa_tier_for_bond(surety_id: str, bond_amount: float) -> str:
    """
    Return the smallest POA prefix that covers the bond amount for the given surety.
    OSI tiers:     OSI3→$3k, OSI6→$6k, OSI16→$16k, OSI51→$51k, OSI101→$101k, OSI251→$251k
    Palmetto tiers: PSC2->$2k, PSC5->$5k, PSC15->$15k, PSC25->$25k, PSC50->$50k, PSC75->$75k, PSC105->$105k
    """
    for cap, prefix in TIERS.get(surety_id.lower(), []):
        if bond_amount <= cap:
            return prefix
    # Bond exceeds all tiers — return highest available
    return TIERS.get(surety_id.lower(), [(0, "UNKNOWN")])[-1][1]


async def seed_poa_inventory(poa_inventory):
    """Seed poa_inventory collection from receipt data if it's empty."""
    try:
        count = await poa_inventory.count_documents({})
        if count > 0:
            return
        print("📋 Seeding POA inventory from receipt data...")
        docs = []
        for tier in POA_RECEIPT_DATA:
            for serial in range(tier["start"], tier["end"] + 1):
                docs.append({
                    "surety_id": tier["surety_id"],
                    "poa_prefix": tier["prefix"],
                    "poa_number": str(serial),
                    "poa_full": f"{tier['prefix']} {serial}",
                    "max_bond_value": tier["max_bond"],
                    "status": "available",
                    "expiration": tier["exp"],
                    "bond_case_id": None,
                    "used_at": None,
                })
        if docs:
            await poa_inventory.insert_many(docs)
            print(f"   ✅ Seeded {len(docs)} POA records ({sum(1 for d in docs if d['surety_id'] == 'osi')} OSI, {sum(1 for d in docs if d['surety_id'] == 'palmetto')} Palmetto)")
        # Create indexes
        await poa_inventory.create_index("poa_number")
        await poa_inventory.create_index([("surety_id", 1), ("poa_prefix", 1), ("status", 1)])
    except Exception as e:
        print(f"   ⚠️  POA seed skipped: {e}")

async def auto_release_poa(poa_number: str, reason: str, actor: str) -> bool:
    """
    Releases a POA back into the inventory if it was associated with an exonerated, 
    surrendered, or forfeited bond, creating an audit log.
    """
    from dashboard.extensions import get_db
    from dashboard.services.audit_service import AuditService
    from datetime import datetime

    db = get_db()
    poa_doc = await db.poa_inventory.find_one({"poa_number": poa_number})
    if not poa_doc:
        return False
        
    if poa_doc.get("status") == "available":
        return True # Already released

    await db.poa_inventory.update_one(
        {"poa_number": poa_number},
        {"$set": {
            "status": "available", 
            "bond_case_id": None,
            "released_at": datetime.utcnow(),
            "release_reason": reason
        }}
    )
    
    await AuditService.log_event(
        entity_type="poa",
        entity_id=poa_number,
        action="auto_released",
        details={"reason": reason},
        actor=actor
    )
    
    return True

