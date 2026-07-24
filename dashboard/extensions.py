"""
ShamrockLeads Dashboard — Extension Initialization
Centralized init for MongoDB (Motor), Redis, and BlueBubbles config.
Avoids circular imports by keeping all singletons here.
"""
from __future__ import annotations

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
    """Load BlueBubbles server configs from environment variables.

    Mutates BB_SERVERS in place (clear + update). Do not rebind the name —
    many modules hold ``from dashboard.extensions import BB_SERVERS`` and
    would keep pointing at a discarded empty dict if we assigned a new object.

    Preferred path for 0178 (best practice):
      1. Runtime override
      2. Tailscale direct (100.x iMac) when reachable
      3. BLUEBUBBLES_URL_0178 / BLUEBUBBLES_URL env (ngrok/frp)
      4. BLUEBUBBLES_FRP_URL last-resort
    """
    import socket
    from urllib.parse import urlparse

    loaded: dict = {}
    for suffix, label, email in _BB_PHONES:
        # Check runtime overrides first, then env vars
        url = _BB_URL_OVERRIDES.get(suffix, "") or os.getenv(f"BLUEBUBBLES_URL_{suffix}", "").rstrip("/")
        pw = os.getenv(f"BLUEBUBBLES_PASSWORD_{suffix}", "")
        # Legacy single-server fallback for first server
        if not url and suffix == "0178":
            url = _BB_URL_OVERRIDES.get("0178", "") or os.getenv("BLUEBUBBLES_URL", "").rstrip("/")
            pw = pw or os.getenv("BLUEBUBBLES_PASSWORD", "")
        # Prefer Tailscale direct for primary BB server when mesh is up
        if suffix == "0178" and not _BB_URL_OVERRIDES.get(suffix):
            try:
                from config.tailscale import ts_config
                preferred = ts_config.get_bb_url_with_fallback(url)
                if preferred:
                    url = preferred.rstrip("/")
            except Exception as ts_err:
                print(f"  ⚠️  BB Tailscale resolve skipped: {ts_err}")
        if url:
            loaded[f"239955{suffix}"] = {
                "url": url,
                "password": pw,
                "label": label,
                "email": email,
                "suffix": suffix,
            }
            # DNS resolution check at init time (helps diagnose tunnel connectivity)
            try:
                parsed = urlparse(url)
                hostname = parsed.hostname or ""
                port = parsed.port or (443 if (parsed.scheme or "https") == "https" else 80)
                if hostname:
                    resolved = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
                    ip = resolved[0][4][0] if resolved else "unknown"
                    print(f"  ✅ BB [{suffix}] DNS OK: {hostname}:{port} -> {ip}")
            except Exception as dns_err:
                print(f"  ⚠️  BB [{suffix}] DNS FAILED for {url}: {dns_err}")

    BB_SERVERS.clear()
    BB_SERVERS.update(loaded)


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
    {"surety_id": "palmetto", "prefix": "PSC2",   "max_bond": 2_000,   "start": 2644650, "end": 2644669, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC5",   "max_bond": 5_000,   "start": 2644670, "end": 2644777, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC15",  "max_bond": 15_000,  "start": 2644778, "end": 2644790, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC25",  "max_bond": 25_000,  "start": 2644791, "end": 2644809, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC50",  "max_bond": 50_000,  "start": 2644810, "end": 2644813, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC75",  "max_bond": 75_000,  "start": 2644814, "end": 2644814, "exp": None},
    {"surety_id": "palmetto", "prefix": "PSC105", "max_bond": 105_000, "start": 2644815, "end": 2644815, "exp": None},
]

# ── Master list of all registered scraper counties (FL + GA + SC + NC + TN + TX + LA) ──
# Format: "County (ST)" so multi-state name collisions (Lee, Sumter, Polk…) stay unique.
# Mongo stores bare county + separate state field — parsers strip the (ST) for queries.
REGISTERED_COUNTIES = sorted([
    # ── Florida ──
    "Alachua (FL)",
    "Bay (FL)",
    "Brevard (FL)",
    "Broward (FL)",
    "Charlotte (FL)",
    "Citrus (FL)",
    "Clay (FL)",
    "Collier (FL)",
    "Columbia (FL)",
    "DeSoto (FL)",
    "Dixie (FL)",
    "Duval (FL)",
    "Escambia (FL)",
    "Flagler (FL)",
    "Gadsden (FL)",
    "Glades (FL)",
    "Hardee (FL)",
    "Hendry (FL)",
    "Hernando (FL)",
    "Highlands (FL)",
    "Hillsborough (FL)",
    "Indian River (FL)",
    "Jackson (FL)",
    "Lake (FL)",
    "Lee (FL)",
    "Leon (FL)",
    "Manatee (FL)",
    "Marion (FL)",
    "Martin (FL)",
    "Miami-Dade (FL)",
    "Monroe (FL)",
    "Nassau (FL)",
    "Okaloosa (FL)",
    "Okeechobee (FL)",
    "Orange (FL)",
    "Osceola (FL)",
    "Palm Beach (FL)",
    "Pasco (FL)",
    "Pinellas (FL)",
    "Polk (FL)",
    "Putnam (FL)",
    "Santa Rosa (FL)",
    "Sarasota (FL)",
    "Seminole (FL)",
    "St. Johns (FL)",
    "St. Lucie (FL)",
    "Sumter (FL)",
    "Suwannee (FL)",
    "Taylor (FL)",
    "Volusia (FL)",
    "Walton (FL)",
    # ── Georgia ──
    "Bacon (GA)",
    "Baker (GA)",
    "Banks (GA)",
    "Barrow (GA)",
    "Bartow (GA)",
    "Bibb (GA)",
    "Brantley (GA)",
    "Bryan (GA)",
    "Bulloch (GA)",
    "Camden (GA)",
    "Carroll (GA)",
    "Catoosa (GA)",
    "Chatham (GA)",
    "Cherokee (GA)",
    "Clarke (GA)",
    "Cobb (GA)",
    "Columbia (GA)",
    "Coweta (GA)",
    "Crawford (GA)",
    "Dawson (GA)",
    "Decatur (GA)",
    "DeKalb (GA)",
    "Dodge (GA)",
    "Dougherty (GA)",
    "Douglas (GA)",
    "Echols (GA)",
    "Emanuel (GA)",
    "Fayette (GA)",
    "Floyd (GA)",
    "Forsyth (GA)",
    "Fulton (GA)",
    "Glynn (GA)",
    "Grady (GA)",
    "Gwinnett (GA)",
    "Habersham (GA)",
    "Hall (GA)",
    "Hancock (GA)",
    "Haralson (GA)",
    "Heard (GA)",
    "Henry (GA)",
    "Houston (GA)",
    "Jasper (GA)",
    "Johnson (GA)",
    "Jones (GA)",
    "Lee (GA)",
    "Liberty (GA)",
    "Lowndes (GA)",
    "Lumpkin (GA)",
    "Macon (GA)",
    "McIntosh (GA)",
    "Miller (GA)",
    "Murray (GA)",
    "Muscogee (GA)",
    "Oconee (GA)",
    "Oglethorpe (GA)",
    "Paulding (GA)",
    "Pickens (GA)",
    "Polk (GA)",
    "Pulaski (GA)",
    "Putnam (GA)",
    "Randolph (GA)",
    "Richmond (GA)",
    "Rockdale (GA)",
    "Spalding (GA)",
    "Sumter (GA)",
    "Tattnall (GA)",
    "Taylor (GA)",
    "Thomas (GA)",
    "Toombs (GA)",
    "Treutlen (GA)",
    "Troup (GA)",
    "Twiggs (GA)",
    "Upson (GA)",
    "Walton (GA)",
    # ── South Carolina (all 46) ──
    "Abbeville (SC)",
    "Aiken (SC)",
    "Allendale (SC)",
    "Anderson (SC)",
    "Bamberg (SC)",
    "Barnwell (SC)",
    "Beaufort (SC)",
    "Berkeley (SC)",
    "Calhoun (SC)",
    "Charleston (SC)",
    "Cherokee (SC)",
    "Chester (SC)",
    "Chesterfield (SC)",
    "Clarendon (SC)",
    "Colleton (SC)",
    "Darlington (SC)",
    "Dillon (SC)",
    "Dorchester (SC)",
    "Edgefield (SC)",
    "Fairfield (SC)",
    "Florence (SC)",
    "Georgetown (SC)",
    "Greenville (SC)",
    "Greenwood (SC)",
    "Hampton (SC)",
    "Horry (SC)",
    "Jasper (SC)",
    "Kershaw (SC)",
    "Lancaster (SC)",
    "Laurens (SC)",
    "Lee (SC)",
    "Lexington (SC)",
    "Marion (SC)",
    "Marlboro (SC)",
    "McCormick (SC)",
    "Newberry (SC)",
    "Oconee (SC)",
    "Orangeburg (SC)",
    "Pickens (SC)",
    "Richland (SC)",
    "Saluda (SC)",
    "Spartanburg (SC)",
    "Sumter (SC)",
    "Union (SC)",
    "Williamsburg (SC)",
    "York (SC)",
    # ── North Carolina (wave-1 registered scrapers) ──
    "Alamance (NC)",
    "Anson (NC)",
    "Brunswick (NC)",
    "Cabarrus (NC)",
    "Cleveland (NC)",
    "Davidson (NC)",
    "Davie (NC)",
    "Duplin (NC)",
    "Durham (NC)",
    "Edgecombe (NC)",
    "Gaston (NC)",
    "Harnett (NC)",
    "Henderson (NC)",
    "Hoke (NC)",
    "Iredell (NC)",
    "Lincoln (NC)",
    "Mecklenburg (NC)",
    "New Hanover (NC)",
    "Pender (NC)",
    "Polk (NC)",
    "Rutherford (NC)",
    "Sampson (NC)",
    "Scotland (NC)",
    "Stokes (NC)",
    "Surry (NC)",
    "Transylvania (NC)",
    "Union (NC)",
    "Wake (NC)",
    "Guilford (NC)",
    "Forsyth (NC)",
    "Cumberland (NC)",
    # ── Tennessee (wave-1 + TnCIS statewide + wave-2) ──
    "Davidson (TN)",
    "Hamilton (TN)",
    "Knox (TN)",
    "Rutherford (TN)",
    "Shelby (TN)",
    "TnCIS (TN)",
    # ── Texas (wave-1 + wave-2 + wave-3 registered scrapers) ──
    "Bexar (TX)",
    "Collin (TX)",
    "Dallas (TX)",
    "Denton (TX)",
    "El Paso (TX)",
    "Fort Bend (TX)",
    "Harris (TX)",
    "Hidalgo (TX)",
    "Montgomery (TX)",
    "Tarrant (TX)",
    "Travis (TX)",
    "Williamson (TX)",
    # ── Louisiana (wave-1 + wave-2 registered scrapers) ──
    "East Baton Rouge (LA)",
    "Jefferson (LA)",
    "Lafayette (LA)",
    "Orleans (LA)",
    # ── Alabama (wave-1 registered scrapers) ──
    "Jefferson (AL)",
    "Madison (AL)",
    "Mobile (AL)",
    # ── Connecticut (wave-1 + wave-2 registered scrapers) ──
    "CT DOC (CT)",
    "Statewide (CT)",
    # ── Mississippi (wave-1 registered scrapers) ──
    "Hinds (MS)",
    "Jackson (MS)",
])


# States we actively scrape / surface in the dashboard
ACTIVE_STATE_CODES = ("FL", "GA", "SC", "NC", "TN", "TX", "LA", "AL", "CT", "MS")

# SWFL core — never drop from schedule; UI must always show them as registered.
# Lee is primary production; Sarasota is required coverage for the bond desk.
KEY_FL_COUNTIES = (
    "Lee",
    "Sarasota",
    "Collier",
    "Charlotte",
    "Manatee",
    "DeSoto",
    "Hendry",
)

KEY_FL_COUNTY_LABELS = tuple(f"{c} (FL)" for c in KEY_FL_COUNTIES)


def parse_registered_county(label: str) -> tuple[str, str | None]:
    """Split ``Lee (FL)`` → ``("Lee", "FL")``; bare names keep state None."""
    raw = (label or "").strip()
    m = re_mod.match(r"^(.+?)\s*\(([A-Za-z]{2})\)$", raw)
    if m:
        return m.group(1).strip(), m.group(2).upper()
    return raw, None


def county_label(name: str, state: str | None = None) -> str:
    """Normalize to ``County (ST)`` for UI / registry keys."""
    bare, st = parse_registered_county(name or "")
    st = (st or state or "FL").upper()
    if not bare:
        return ""
    return f"{bare} ({st})"


def registered_county_to_trigger_key(label: str) -> str:
    """Map REGISTERED_COUNTIES label to scheduler trigger key.

    FL → bare county (``lee``). Other states → ``sc_lee`` / ``nc_mecklenburg``.
    """
    name, st = parse_registered_county(label)
    slug = name.lower().replace(" ", "_").replace("-", "_")
    if not st or st == "FL":
        return slug
    return f"{st.lower()}_{slug}"


def _registered_by_bare() -> dict[str, list[str]]:
    """Map lowercased bare county name → list of ``County (ST)`` labels."""
    by_bare: dict[str, list[str]] = {}
    for label in REGISTERED_COUNTIES:
        bare, _st = parse_registered_county(label)
        if not bare:
            continue
        by_bare.setdefault(bare.lower(), []).append(label)
    return by_bare


def merge_county_list_for_ui(db_counties: list | None = None) -> list[str]:
    """Build a de-duplicated ``County (ST)`` list for dashboard dropdowns.

    Mongo historically stores bare county names (``Lee``) while the registry
    uses multi-state labels (``Lee (FL)``). Naively unioning those lists makes
    the UI show both. This helper:

    1. Always includes every registered label
    2. Absorbs bare DB names into matching registered labels (no bare leftover)
    3. Labels unknown DB counties as ``County (FL)`` for legacy FL data
    """
    out: set[str] = set(REGISTERED_COUNTIES)
    by_bare = _registered_by_bare()

    for raw in db_counties or []:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        bare, st = parse_registered_county(text)
        if not bare:
            continue
        if st:
            out.add(county_label(bare, st))
            continue
        # Bare name from Mongo — if registered under any state, labels already cover it
        if bare.lower() in by_bare:
            continue
        out.add(county_label(bare, "FL"))

    return sorted(out)


def normalize_county_ui_name(name: str, state: str | None = None) -> str:
    """Normalize a free-form county string to the UI label form."""
    bare, st = parse_registered_county(name or "")
    if not bare:
        return ""
    return county_label(bare, st or state or "FL")


def index_scraper_status_docs(docs: list[dict]) -> dict[str, dict]:
    """Index scraper_status docs under bare, labeled, and trigger-key forms.

    Writers historically stored bare county names (``Lee``). Multi-state needs
    ``Lee (FL)`` labels. Lookups try every known key so dashboards stay accurate.
    """
    by_key: dict[str, dict] = {}
    for doc in docs:
        if not doc:
            continue
        county_raw = (doc.get("county") or "").strip()
        if not county_raw:
            continue
        bare, st_from_label = parse_registered_county(county_raw)
        st = (doc.get("state") or st_from_label or "").upper() or None
        keys = {county_raw, bare}
        if st:
            keys.add(county_label(bare, st))
            keys.add(registered_county_to_trigger_key(county_label(bare, st)))
        sid = doc.get("scraper_id") or doc.get("job_id")
        if sid:
            keys.add(str(sid))
        for k in keys:
            if not k:
                continue
            # Prefer more recently updated docs when keys collide
            existing = by_key.get(k)
            if existing is None:
                by_key[k] = doc
                continue
            ex_ts = existing.get("last_run") or existing.get("updated_at")
            new_ts = doc.get("last_run") or doc.get("updated_at")
            if new_ts and (not ex_ts or new_ts >= ex_ts):
                by_key[k] = doc
    return by_key


def _registered_states_for_bare(bare: str) -> list[str]:
    """Which state codes have this bare county name in REGISTERED_COUNTIES."""
    target = (bare or "").strip().lower()
    if not target:
        return []
    out = []
    for label in REGISTERED_COUNTIES:
        name, st = parse_registered_county(label)
        if name.lower() == target and st:
            out.append(st)
    return out


def resolve_scraper_status(
    index: dict[str, dict],
    county_name: str,
    state: str | None = None,
) -> dict:
    """Resolve a scraper_status doc for a county (+ optional state).

    Matching order:
    1. Labeled key ``County (ST)`` / trigger key (preferred, multi-state safe)
    2. Bare key when name is unique across the registry (e.g. Cobb → GA only)
    3. Bare key for FL / unknown state (legacy Florida writers)
    """
    bare, st_from_label = parse_registered_county(county_name)
    st = (state or st_from_label or "").upper() or None
    candidates: list[str] = []
    if st:
        label = county_label(bare, st)
        candidates.append(label)
        candidates.append(registered_county_to_trigger_key(label))

    states_for_name = _registered_states_for_bare(bare)
    unique_name = len(states_for_name) <= 1
    allow_bare = (not st) or st == "FL" or unique_name
    if allow_bare:
        candidates.append(county_name)
        candidates.append(bare)

    seen: set[str] = set()
    for key in candidates:
        if not key or key in seen:
            continue
        seen.add(key)
        doc = index.get(key)
        if not doc:
            continue
        doc_st = (doc.get("state") or "").upper()
        if not doc_st:
            _, parsed = parse_registered_county(doc.get("county") or "")
            doc_st = (parsed or "").upper()
        if st and doc_st and doc_st != st:
            continue
        # Shared names (Lee): never attach unlabeled bare docs to non-FL
        if st and st != "FL" and not doc_st and not unique_name:
            continue
        return doc
    return {}



# ── SignNow Template IDs ──
# Single Source of Truth: SignNowPacketService.TEMPLATE_MAP
# Location: dashboard/services/signnow_packet_service.py
# Do NOT duplicate template IDs here — import from the service if needed.


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


# ── init_app() RETIRED (2026-05-19) ──────────────────────────────────────────
# The Quart app factory pattern has been replaced by FastAPI.
# Startup logic now lives in dashboard/main.py lifespan():
#   - init_bluebubbles() called directly
#   - _seed_poa_inventory_async() awaited at startup
#   - Settings injected via dashboard/deps.py get_settings()
# This stub is kept so any legacy caller (dashboard/__init__.py, run.py)
# does not crash at import time.

def init_app(app=None):
    """No-op stub — Quart factory retired. FastAPI lifespan handles startup."""
    pass
