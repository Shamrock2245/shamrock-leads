"""
ShamrockLeads — POA Service
POA tier lookup, inventory seeding, and assignment logic.
"""

from dashboard.extensions import POA_RECEIPT_DATA

# ── POA Tier Definitions ──
TIERS = {
    "osi": [
        (3000, "OSI-P3", "OSI3"),
        (6000, "OSI-P6", "OSI6"),
        (16000, "OSI-P16", "OSI16"),
        (51000, "OSI-P51", "OSI51"),
        (101000, "OSI-P101", "OSI101"),
        (251000, "OSI-P251", "OSI251"),
    ],
    "palmetto": [
        (2000, "PSC2"), (5000, "PSC5"), (15000, "PSC15"), (25000, "PSC25"),
        (50000, "PSC50"), (75000, "PSC75"), (105000, "PSC105"),
    ],
}


def determine_surety_from_prefix(prefix: str) -> str:
    """Return 'osi' or 'palmetto' based on prefix string."""
    clean = str(prefix or "").strip().lower()
    if clean.startswith("osi") or "osi-p" in clean:
        return "osi"
    return "palmetto"


def parse_max_bond_from_prefix(prefix: str) -> float:
    """
    Extract the max bond value for a given prefix.
    Supports OSI-P3-116-26-, OSI-P3, OSI3, PSC5, etc.
    """
    pfx = str(prefix or "").strip().upper()
    import re
    # Check OSI-P<number> format
    m = re.search(r"OSI-?P?(\d+)", pfx)
    if m:
        num = int(m.group(1))
        # Match against known tier caps
        tier_map = {3: 3000, 6: 6000, 16: 16000, 51: 51000, 101: 101000, 251: 251000}
        if num in tier_map:
            return float(tier_map[num])
        return float(num * 1000) if num < 1000 else float(num)

    # Check PSC<number> format
    m_psc = re.search(r"PSC(\d+)", pfx)
    if m_psc:
        num = int(m_psc.group(1))
        return float(num * 1000)

    return 0.0


def get_poa_tier_for_bond(surety_id: str, bond_amount: float) -> str:
    """
    Return the smallest POA prefix that covers the bond amount for the given surety.
    OSI tiers:     OSI-P3 / OSI3→$3k, OSI-P6 / OSI6→$6k, OSI-P16 / OSI16→$16k, OSI-P51 / OSI51→$51k
    Palmetto tiers: PSC2->$2k, PSC5->$5k, PSC15->$15k, PSC25->$25k, PSC50->$50k, PSC75->$75k, PSC105->$105k
    """
    for item in TIERS.get(surety_id.lower(), []):
        cap = item[0]
        prefix = item[1]
        if bond_amount <= cap:
            return prefix
    # Bond exceeds all tiers — return highest available
    last = TIERS.get(surety_id.lower(), [(0, "UNKNOWN")])[-1]
    return last[1]


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
    from datetime import datetime, timezone

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
            "released_at": datetime.now(timezone.utc),
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


async def check_poa_inventory_thresholds(
    threshold: int = 3,
    *,
    notify: bool = False,
) -> dict:
    """
    Scan available POA inventory by prefix (the real inventory key is
    ``poa_prefix``, not ``tier``) and optionally fire a Slack digest.

    Returns:
        {
          "checked_at": ISO timestamp,
          "threshold": int,
          "low_stock": [
            { "prefix", "tier", "surety_id", "available", "max_bond" }, ...
          ],
        }
    """
    from datetime import datetime, timezone

    from dashboard.extensions import get_db

    db = get_db()
    low_stock: list[dict] = []

    for surety_id, prefix_list in TIERS.items():
        for item in prefix_list:
            cap = item[0]
            prefix = item[1]
            # Seeded inventory uses lowercase "available"; tolerate case variants.
            count = await db.poa_inventory.count_documents({
                "surety_id": surety_id,
                "status": {"$in": ["available", "Available", "AVAILABLE"]},
                "$or": [
                    {"poa_prefix": prefix},
                    {"poa_prefix": {"$regex": f"^{prefix}", "$options": "i"}},
                ],
            })
            if count <= threshold:
                low_stock.append({
                    "prefix": prefix,
                    "tier": prefix,  # digest_poa_low_stock historically labels this "tier"
                    "surety_id": surety_id,
                    "available": count,
                    "max_bond": cap,
                })

    if notify and low_stock:
        try:
            from dashboard.services.automation_digest import digest_poa_low_stock
            await digest_poa_low_stock(low_stock, threshold)
        except Exception:
            # Never let alert delivery break inventory checks
            pass

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "threshold": threshold,
        "low_stock": low_stock,
    }


