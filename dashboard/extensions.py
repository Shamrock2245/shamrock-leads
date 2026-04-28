"""
ShamrockLeads Dashboard — Extension Initialization
Centralized init for MongoDB (Motor), Redis, and BlueBubbles config.
Avoids circular imports by keeping all singletons here.
"""

import os
import re as re_mod
from motor.motor_asyncio import AsyncIOMotorClient

# ── MongoDB (Motor — async) ──
_mongo_client = None
_mongo_db = None


def get_mongo_client():
    """Lazy-init the Motor async MongoDB client."""
    global _mongo_client
    if _mongo_client is None:
        uri = os.getenv("MONGODB_URI", "")
        if not uri:
            raise RuntimeError("MONGODB_URI not set — copy .env.example to .env and fill it in")
        _mongo_client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=10000)
    return _mongo_client


def get_db():
    """Return the database handle (Motor async)."""
    global _mongo_db
    if _mongo_db is None:
        client = get_mongo_client()
        db_name = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")
        _mongo_db = client[db_name]
    return _mongo_db


# Convenience collection accessors
def get_collection(name: str):
    return get_db()[name]


# ── BlueBubbles iMessage Config (Multi-Server) ──

BB_SERVERS = {}

# Runtime URL overrides (updated dynamically by iMac sync script)
_BB_URL_OVERRIDES = {}

_BB_PHONES = [
    ("0178", "(239) 955-0178", "shamrockbailoffice@gmail.com"),
    ("0314", "(239) 955-0314", "admin@shamrockbailbonds.biz"),
]

# Shared API key for iMac → VPS config updates
BB_CONFIG_API_KEY = os.getenv("BB_CONFIG_API_KEY", "shamrock-bb-sync-2245")


def init_bluebubbles():
    """Load BlueBubbles server configs from environment variables."""
    global BB_SERVERS
    BB_SERVERS = {}
    for suffix, label, email in _BB_PHONES:
        # Check runtime overrides first, then env vars
        url = _BB_URL_OVERRIDES.get(suffix, "") or os.getenv(f"BLUEBUBBLES_URL_{suffix}", "").rstrip("/")
        pw = os.getenv(f"BLUEBUBBLES_PASSWORD_{suffix}", "")
        # Legacy single-server fallback for first server
        if not url and suffix == "0178":
            url = _BB_URL_OVERRIDES.get("0178", "") or os.getenv("BLUEBUBBLES_URL", "").rstrip("/")
            pw = os.getenv("BLUEBUBBLES_PASSWORD", "")
        if url:
            BB_SERVERS[f"239955{suffix}"] = {
                "url": url,
                "password": pw,
                "label": label,
                "email": email,
                "suffix": suffix,
            }


def update_bb_url(suffix: str, new_url: str):
    """Update a BlueBubbles server URL at runtime (no restart needed)."""
    _BB_URL_OVERRIDES[suffix] = new_url.rstrip("/")
    # Re-init to pick up the change
    init_bluebubbles()
    return BB_SERVERS


def get_bb_server(from_number: str):
    """Look up the BlueBubbles server config for a given from_number."""
    if from_number in BB_SERVERS:
        return BB_SERVERS[from_number]
    for key, srv in BB_SERVERS.items():
        if key.endswith(from_number[-4:]):
            return srv
    return next(iter(BB_SERVERS.values()), None)


# ── Phone Formatter ──

def format_phone(raw):
    """Normalize a US phone number to +1XXXXXXXXXX."""
    digits = re_mod.sub(r"\D", "", str(raw))
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


# ── Outreach Config Helpers (used by imessage_automation + agent_brain) ──

def is_business_hours(config: dict) -> bool:
    """Check if current time (US Eastern) is within configured business hours."""
    from datetime import datetime, timezone, timedelta
    # US Eastern = UTC-4 (EDT) or UTC-5 (EST) — approximate with -4
    et_now = datetime.now(timezone.utc) + timedelta(hours=-4)
    hour = et_now.hour
    bh = config.get("business_hours", {"start": 8, "end": 20})
    return bh.get("start", 8) <= hour < bh.get("end", 20)


# ── POA Receipt Data (seed inventory on first boot) ──

POA_RECEIPT_DATA = [
    # OSI — Receipt dated 04/20/2026, exp 31-Dec-26
    {"surety_id": "osi", "prefix": "OSI3",   "max_bond": 3_000,   "start": 20134295, "end": 20134324, "exp": "2026-12-31"},
    {"surety_id": "osi", "prefix": "OSI6",   "max_bond": 6_000,   "start": 20132136, "end": 20132150, "exp": "2026-12-31"},
    {"surety_id": "osi", "prefix": "OSI16",  "max_bond": 16_000,  "start": 20136624, "end": 20136639, "exp": "2026-12-31"},
    {"surety_id": "osi", "prefix": "OSI51",  "max_bond": 51_000,  "start": 20127651, "end": 20127660, "exp": "2026-12-31"},
    {"surety_id": "osi", "prefix": "OSI101", "max_bond": 101_000, "start": 20128283, "end": 20128284, "exp": "2026-12-31"},
    {"surety_id": "osi", "prefix": "OSI251", "max_bond": 251_000, "start": 20129019, "end": 20129020, "exp": "2026-12-30"},
    # Palmetto — Package #192184, dated 04/20/2026
    {"surety_id": "palmetto", "prefix": "PSC5",   "max_bond": 5_000,   "start": 2644670, "end": 2644777, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC15",  "max_bond": 15_000,  "start": 2644778, "end": 2644790, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC25",  "max_bond": 25_000,  "start": 2644791, "end": 2644809, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC50",  "max_bond": 50_000,  "start": 2644810, "end": 2644813, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC75",  "max_bond": 75_000,  "start": 2644814, "end": 2644814, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC105", "max_bond": 105_000, "start": 2644815, "end": 2644815, "exp": None},
]

# ── Master list of all registered scraper counties ──
REGISTERED_COUNTIES = sorted([
    "Alachua", "Bay", "Brevard", "Broward", "Charlotte", "Citrus", "Clay",
    "Collier", "Columbia", "DeSoto", "Dixie", "Duval", "Escambia", "Flagler",
    "Gadsden", "Glades", "Hardee", "Hendry", "Hernando", "Highlands",
    "Hillsborough", "Indian River", "Jackson", "Lake", "Lee", "Leon",
    "Manatee", "Martin", "Monroe", "Nassau", "Okaloosa", "Okeechobee",
    "Orange", "Osceola", "Palm Beach", "Pasco", "Pinellas", "Polk",
    "Putnam", "Santa Rosa", "Sarasota", "Seminole", "St. Johns", "St. Lucie",
    "Sumter", "Suwannee", "Taylor", "Volusia", "Walton",
])


# ── SignNow Template IDs ──
SIGNNOW_TEMPLATES = {
    "paperwork-header": "9b9dad3e319f4b1580094e05f9844929d5a6f7de",
    "faq-cosigners": "0820b9fef3bd4c38a91643455881021f3f0c3a88",
    "faq-defendants": "1524f1c816c54a72be76d14fe128e4a6034579dc",
    "indemnity-agreement": "ed5e6ca0a3444796a127fbeb6a880658371aafd7",
    "defendant-application": "d50adc808f3245f087b218d33da89e4ace15ecd4",
    "promissory-note": "460bd43c2f514305a3b296481713a00ee8311c79",
    "disclosure-form": "fb8b57bf55ac4d5e8bff820b018a0bfd3b17a37a",
    "surety-terms": "192aeb246230446bb0d7f658765afd2832704964",
    "master-waiver": "3b0e71188b3049cc8760d144e6c49df227ccd741",
    "ssa-release": "4800defff07541079760889d83109059585b0cea",
    "collateral-receipt": "4b1f5611840f4de4bc891677617f5dbf6ff7ad05",
    "payment-plan": "1861b158d7a447d48be5ac1dd24755f727f0773b",
    "appearance-bond": "7ba703e101e04604a2f1458c21d3addfce9ca86b",
}


# ── App Factory Init ──

async def _seed_poa_inventory_async():
    """Async POA inventory seeding — runs once on first boot."""
    try:
        poa_inv = get_collection("poa_inventory")
        count = await poa_inv.count_documents({})
        if count > 0:
            return
        docs = []
        for tier in POA_RECEIPT_DATA:
            for serial in range(tier["start"], tier["end"] + 1):
                docs.append({
                    "poa_number": str(serial),
                    "poa_prefix": tier["prefix"],
                    "poa_full": f"{tier['prefix']} {serial}",
                    "surety_id": tier["surety_id"],
                    "max_bond_value": tier["max_bond"],
                    "status": "available",
                    "expiration": tier["exp"],
                    "book_number": "receipt_2026-04-20",
                    "assigned_to_agent": "Brendan",
                    "received_at": "2026-04-20T00:00:00Z",
                    "bond_case_id": None,
                    "used_at": None,
                })
        if docs:
            await poa_inv.create_index("poa_number", unique=True)
            await poa_inv.create_index([("surety_id", 1), ("status", 1)])
            await poa_inv.create_index([("poa_prefix", 1), ("status", 1)])
            await poa_inv.insert_many(docs, ordered=False)
            osi_count = sum(1 for d in docs if d["surety_id"] == "osi")
            palm_count = len(docs) - osi_count
            print(f"✅ POA inventory seeded: {len(docs)} powers ({osi_count} OSI + {palm_count} Palmetto)")
    except Exception as e:
        print(f"⚠️  POA seed: {e}")


def init_app(app):
    """Initialize extensions for the Quart app factory."""
    import secrets

    # Secret key for session cookies
    app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

    # Init BlueBubbles
    init_bluebubbles()

    # Seed POA inventory on first request
    _seeded = {"done": False}

    @app.before_serving
    async def on_startup():
        if not _seeded["done"]:
            await _seed_poa_inventory_async()
            _seeded["done"] = True
            print(f"☘️  Dashboard ready — Motor connected to {os.getenv('MONGODB_DB_NAME', 'ShamrockBailDB')}")
