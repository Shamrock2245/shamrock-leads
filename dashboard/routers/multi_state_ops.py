from __future__ import annotations
"""
ShamrockLeads — Multi-State Operations API
Endpoints:
  GET /api/ops/state-summary          — KPIs per state (FL/GA/SC/NC/TN/TX/LA/CT/AL/MS)
  GET /api/ops/scraper-registry       — Full registry with state + platform metadata
  GET /api/ops/arrests/multi-state    — Recent arrests across all states with filters
  GET /api/ops/county-heatmap         — Arrest volume by county (all states)
  GET /api/ops/platform-breakdown     — Scraper platform distribution
  GET /api/ops/live-feed              — Last 50 arrests across all states (real-time feed)
"""
import os
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Query

from dashboard.extensions import (
    ACTIVE_STATE_CODES,
    REGISTERED_COUNTIES,
    get_collection,
    index_scraper_status_docs,
    parse_registered_county,
    registered_county_to_trigger_key,
    resolve_scraper_status,
)

logger = logging.getLogger(__name__)
multi_state_bp = APIRouter(prefix="/api/ops", tags=["multi_state_ops"])

# Source of truth for dashboard state cards — matches REGISTERED_COUNTIES states.
ACTIVE_STATES = tuple(ACTIVE_STATE_CODES)

# ─────────────────────────────────────────────────────────────────────────────
# SCRAPER REGISTRY — REGISTERED_COUNTIES first, disk platform enrichment second
# ─────────────────────────────────────────────────────────────────────────────

_PLATFORM_HINTS = (
    ("jailtracker_base", "JailTracker"),
    ("p2c_base", "P2C"),
    ("eas_base", "EAS"),
    ("interopweb_base", "InteropWeb"),
    ("zuercher_base", "Zuercher"),
    ("southern_sw_base", "Southern SW"),
    ("socrata_base", "Socrata"),
    ("xml_feed_base", "XML Feed"),
    ("new_world_base", "New World"),
    ("odyssey_base", "Tyler Odyssey"),
    ("kologik_base", "Kologik"),
    ("smartcop_base", "SmartCOP"),
    ("smartwebclient", "SmartCOP"),
    ("smartweb_parser", "SmartWeb"),
    ("dcn_base", "DCN"),
    ("curl_cffi", "Custom HTML"),
    ("base_scraper", "Custom HTML"),
)

_STATE_DIR = {
    "FL": "counties",
    "GA": "counties_ga",
    "SC": "counties_sc",
    "NC": "counties_nc",
    "TN": "counties_tn",
    "TX": "counties_tx",
    "LA": "counties_la",
    "CT": "counties_ct",
    "AL": "counties_al",
    "MS": "counties_ms",
}

# Explicit file map for labels that don't match slug filenames
_LABEL_FILE_OVERRIDES: dict[str, str] = {
    "CT DOC (CT)": "ct_doc.py",
    "Statewide (CT)": "statewide_docket.py",
    "TnCIS (TN)": "tncis.py",
    "Miami-Dade (FL)": "miami_dade.py",
    "St. Johns (FL)": "st_johns.py",
    "St. Lucie (FL)": "st_lucie.py",
    "Indian River (FL)": "indian_river.py",
    "Santa Rosa (FL)": "santa_rosa.py",
    "Palm Beach (FL)": "palm_beach.py",
    "New Hanover (NC)": "new_hanover.py",
    "East Baton Rouge (LA)": "east_baton_rouge.py",
    "Fort Bend (TX)": "fort_bend.py",
    "El Paso (TX)": "el_paso.py",
}

# scraper_id overrides when auto-derived id would double-prefix state
_SCRAPER_ID_OVERRIDES: dict[str, str] = {
    "CT DOC (CT)": "scraper_ct_doc",
}


def _scrapers_base() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "../../scrapers"))


def _slug(name: str) -> str:
    return (
        (name or "")
        .lower()
        .replace(".", "")
        .replace(" ", "_")
        .replace("-", "_")
        .strip("_")
    )


def _detect_platform(content: str) -> str:
    for key, val in _PLATFORM_HINTS:
        if key in content:
            return val
    return "Custom HTML"


def _platform_for_label(label: str, bare: str, state: str) -> tuple[str, str]:
    """Return (platform, filename) for a registered label."""
    base = _scrapers_base()
    fname = _LABEL_FILE_OVERRIDES.get(label)
    if not fname:
        fname = f"{_slug(bare)}.py"
    # CT special-case filenames already mapped
    sub = _STATE_DIR.get(state, "counties")
    candidates = [
        os.path.join(base, sub, fname),
        os.path.join(base, "counties", fname),  # legacy cross-state
    ]
    for fpath in candidates:
        if os.path.isfile(fpath):
            try:
                with open(fpath, encoding="utf-8", errors="ignore") as f:
                    content = f.read(12000)
                return _detect_platform(content), os.path.basename(fpath)
            except Exception:
                return "Custom HTML", os.path.basename(fpath)
    return "Custom HTML", fname


def _build_registry() -> list[dict]:
    """Build registry from REGISTERED_COUNTIES (dashboard source of truth).

    Disk scan only enriches platform/file metadata — counts always match the
    health tab and Multi-State Ops fleet size.
    """
    registry: list[dict] = []
    for label in sorted(REGISTERED_COUNTIES):
        bare, st = parse_registered_county(label)
        st = (st or "FL").upper()
        platform, fname = _platform_for_label(label, bare, st)
        trigger_key = registered_county_to_trigger_key(label)
        scraper_id = _SCRAPER_ID_OVERRIDES.get(label) or (
            f"scraper_{_slug(bare)}"
            if st == "FL"
            else f"scraper_{st.lower()}_{_slug(bare)}"
        )
        registry.append({
            "county": bare,
            "state": st,
            "platform": platform,
            "file": fname,
            "scraper_id": scraper_id,
            "trigger_key": trigger_key,
            "label": label,
        })
    return registry


_REGISTRY_CACHE: list[dict] = []
_REGISTRY_BUILT_AT: Optional[datetime] = None
_REGISTRY_TTL_SEC = 45  # short TTL so new counties appear quickly after deploy


def _get_registry() -> list[dict]:
    global _REGISTRY_CACHE, _REGISTRY_BUILT_AT
    now = datetime.now(timezone.utc)
    stale = (
        not _REGISTRY_CACHE
        or not _REGISTRY_BUILT_AT
        or (now - _REGISTRY_BUILT_AT).total_seconds() > _REGISTRY_TTL_SEC
        or len(_REGISTRY_CACHE) != len(REGISTERED_COUNTIES)
    )
    if stale:
        _REGISTRY_CACHE = _build_registry()
        _REGISTRY_BUILT_AT = now
    return _REGISTRY_CACHE


def _scraped_at_match(cutoff: datetime) -> dict:
    """Match scraped_at whether stored as datetime or ISO string."""
    return {"$or": [
        {"scraped_at": {"$gte": cutoff}},
        {"scraped_at": {"$gte": cutoff.isoformat()}},
        {"created_at": {"$gte": cutoff}},
        {"created_at": {"$gte": cutoff.isoformat()}},
    ]}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/ops/scraper-registry
# ─────────────────────────────────────────────────────────────────────────────
@multi_state_bp.get("/scraper-registry")
async def get_scraper_registry(state: str = ""):
    """Return the full scraper registry with optional state filter."""
    registry = _get_registry()
    if state:
        registry = [r for r in registry if r["state"].upper() == state.upper()]

    # Enrich with last-run data — multi-key index (bare / labeled / scraper_id)
    # Restored after Manus pass regressed same-name multi-state join (Lee FL ≠ Lee SC).
    scraper_status = get_collection("scraper_status")
    status_docs = []
    async for doc in scraper_status.find({}, {"_id": 0}):
        status_docs.append(doc)
    status_index = index_scraper_status_docs(status_docs)

    result = []
    for r in registry:
        status = resolve_scraper_status(
            status_index, r.get("county", ""), r.get("state")
        ) or {}
        if not status and r.get("scraper_id"):
            status = status_index.get(r["scraper_id"], {}) or {}
        last_run = status.get("last_run_at") or status.get("last_run")
        last_run_iso = last_run.isoformat() if hasattr(last_run, "isoformat") else last_run
        result.append({
            **r,
            "status": status.get("status", "never_run"),
            "last_run": last_run,
            "last_run_iso": last_run_iso,
            "records_last_run": status.get("records_last_run", status.get("records", 0)),
            "total_records": status.get("total_records", 0),
            "error_message": status.get("error_message") or status.get("error"),
            "enabled": status.get("enabled", True),
        })

    by_state = {}
    for r in result:
        by_state.setdefault(r["state"], []).append(r)

    return {
        "total": len(result),
        "by_state": {s: len(v) for s, v in by_state.items()},
        "states": list(ACTIVE_STATES),
        "scrapers": result,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/ops/state-summary
# ─────────────────────────────────────────────────────────────────────────────
@multi_state_bp.get("/state-summary")
async def get_state_summary():
    """Return high-level KPIs per state (registry-first, live status join)."""
    registry = _get_registry()
    arrests = get_collection("arrests")
    scraper_status = get_collection("scraper_status")

    # state → list of registry rows
    by_state: dict[str, list[dict]] = {}
    for r in registry:
        by_state.setdefault(r["state"], []).append(r)
    for st in ACTIVE_STATES:
        by_state.setdefault(st, [])

    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)

    # Load all scraper_status once and index multi-key (Lee FL ≠ Lee SC)
    status_docs = []
    async for doc in scraper_status.find({}, {"_id": 0}):
        status_docs.append(doc)
    status_index = index_scraper_status_docs(status_docs)

    result = {}
    total_fleet = 0
    total_active = 0
    total_errors = 0
    total_arrests_all = 0

    for state in ACTIVE_STATES:
        rows = by_state.get(state, [])
        total_counties = len(rows)
        total_fleet += total_counties

        # Arrest counts — FL includes legacy docs with missing state
        if state == "FL":
            state_match = {"$or": [
                {"state": {"$in": ["FL", "fl", "Florida", "FLORIDA"]}},
                {"state": None},
                {"state": ""},
                {"state": {"$exists": False}},
            ]}
        else:
            state_match = {"state": {"$in": [state, state.lower(), state.title()]}}

        arrests_24h = await arrests.count_documents({
            "$and": [state_match, _scraped_at_match(cutoff_24h)],
        })
        arrests_7d = await arrests.count_documents({
            "$and": [state_match, _scraped_at_match(cutoff_7d)],
        })
        total_arrests = await arrests.count_documents(state_match)
        total_arrests_all += total_arrests

        # Scraper health via multi-key resolve (not bare county $in)
        active = empty = errors = never_run = stale = 0
        for r in rows:
            live = resolve_scraper_status(status_index, r["county"], state) or {}
            if not live:
                never_run += 1
                continue
            s = (live.get("status") or "never_run").lower()
            recs = int(live.get("records") or live.get("records_last_run") or 0)
            last_run = live.get("last_run") or live.get("last_run_at")
            hours = 999.0
            if isinstance(last_run, datetime):
                lr = last_run if last_run.tzinfo else last_run.replace(tzinfo=timezone.utc)
                hours = (now - lr).total_seconds() / 3600
            if s in ("error", "failed", "fail", "offline"):
                errors += 1
            elif s in ("empty", "no_data", "blocked") or (
                s in ("ok", "healthy", "success") and recs <= 0
            ):
                empty += 1
            elif s in ("ok", "healthy", "success") and recs > 0:
                if hours < 6:
                    active += 1
                else:
                    stale += 1
            elif recs > 0 and hours < 6:
                active += 1
            elif recs > 0:
                stale += 1
            else:
                never_run += 1

        total_active += active
        total_errors += errors

        bond_stats: dict = {"avg_bond": 0.0, "max_bond": 0.0, "total_bond": 0.0}
        hot_leads = 0
        warm_leads = 0
        async for r in arrests.aggregate([
            {"$match": state_match},
            {"$group": {
                "_id": None,
                "avg_bond": {"$avg": {
                    "$cond": [{"$gt": ["$bond_amount", 0]}, "$bond_amount", "$$REMOVE"],
                }},
                "max_bond": {"$max": "$bond_amount"},
                "total_bond": {"$sum": {
                    "$cond": [{"$gt": ["$bond_amount", 0]}, "$bond_amount", 0],
                }},
                "hot": {"$sum": {"$cond": [{"$gte": ["$lead_score", 70]}, 1, 0]}},
                "warm": {"$sum": {"$cond": [
                    {"$and": [{"$gte": ["$lead_score", 40]}, {"$lt": ["$lead_score", 70]}]},
                    1, 0]}},
            }},
        ]):
            bond_stats = {
                "avg_bond": round(r.get("avg_bond") or 0, 2),
                "max_bond": round(r.get("max_bond") or 0, 2),
                "total_bond": round(r.get("total_bond") or 0, 2),
            }
            hot_leads = r.get("hot", 0)
            warm_leads = r.get("warm", 0)

        result[state] = {
            "state": state,
            "total_counties": total_counties,
            "active_scrapers": active,
            "empty_scrapers": empty,
            "error_scrapers": errors,
            "stale_scrapers": stale,
            "never_run": never_run,
            "arrests_24h": arrests_24h,
            "arrests_7d": arrests_7d,
            "total_arrests": total_arrests,
            "hot_leads": hot_leads,
            "warm_leads": warm_leads,
            **bond_stats,
        }

    return {
        "states": result,
        "state_order": list(ACTIVE_STATES),
        "fleet": {
            "total_registered": total_fleet,
            "active": total_active,
            "errors": total_errors,
            "total_arrests": total_arrests_all,
        },
        "generated_at": now.isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/ops/arrests/multi-state
# ─────────────────────────────────────────────────────────────────────────────
@multi_state_bp.get("/arrests/multi-state")
async def get_multi_state_arrests(
    state: str = "",
    county: str = "",
    platform: str = "",
    days: int = Query(default=1, ge=1, le=90),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    sort: str = "scraped_at",
    dir: int = Query(default=-1),
    q: str = "",
):
    """Return arrests across all states with rich filtering."""
    arrests = get_collection("arrests")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    query: dict = {"$or": [
        {"scraped_at": {"$gte": cutoff}},
        {"scraped_at": {"$gte": cutoff.isoformat()}},
    ]}
    if state:
        st = state.upper()
        query["state"] = {"$in": [st, st.lower()]}
    if county:
        # Accept "Mecklenburg" or "Mecklenburg (NC)"
        bare = re.sub(r"\s*\([A-Za-z]{2}\)\s*$", "", county).strip()
        query["county"] = {"$regex": f"^{re.escape(bare)}$", "$options": "i"}
    if q:
        query["$and"] = query.get("$and", []) + [{"$or": [
            {"full_name": {"$regex": q, "$options": "i"}},
            {"booking_number": {"$regex": q, "$options": "i"}},
            {"charges": {"$regex": q, "$options": "i"}},
        ]}]

    total = await arrests.count_documents(query)
    results = []
    async for doc in (
        arrests.find(query, {"_id": 0})
        .sort(sort, dir)
        .skip((page - 1) * limit)
        .limit(limit)
    ):
        # Serialize datetime fields
        for k, v in doc.items():
            if hasattr(v, "isoformat"):
                doc[k] = v.isoformat()
        results.append(doc)

    return {
        "arrests": results,
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
        "days": days,
        "query": q,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/ops/county-heatmap
# ─────────────────────────────────────────────────────────────────────────────
@multi_state_bp.get("/county-heatmap")
async def get_county_heatmap(days: int = Query(default=7, ge=1, le=90)):
    """Return arrest counts by county for heatmap visualization."""
    arrests = get_collection("arrests")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    pipeline = [
        {"$match": {"scraped_at": {"$gte": cutoff}}},
        {"$group": {
            "_id": {"county": "$county", "state": "$state"},
            "count": {"$sum": 1},
            "bail_total": {"$sum": "$bail_amount"},
        }},
        {"$sort": {"count": -1}},
    ]
    results = []
    async for doc in arrests.aggregate(pipeline):
        results.append({
            "county": doc["_id"].get("county", "Unknown"),
            "state": doc["_id"].get("state", "Unknown"),
            "count": doc["count"],
            "bail_total": doc.get("bail_total", 0),
        })

    return {"heatmap": results, "days": days, "total_counties": len(results)}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/ops/platform-breakdown
# ─────────────────────────────────────────────────────────────────────────────
@multi_state_bp.get("/platform-breakdown")
async def get_platform_breakdown():
    """Return scraper platform distribution across all states."""
    registry = _get_registry()
    breakdown: dict[str, dict] = {}
    for r in registry:
        p = r["platform"]
        if p not in breakdown:
            breakdown[p] = {"platform": p, "total": 0, "by_state": {}}
        breakdown[p]["total"] += 1
        breakdown[p]["by_state"][r["state"]] = breakdown[p]["by_state"].get(r["state"], 0) + 1

    return {
        "platforms": sorted(breakdown.values(), key=lambda x: -x["total"]),
        "total_scrapers": len(registry),
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/ops/live-feed
# ─────────────────────────────────────────────────────────────────────────────
@multi_state_bp.get("/live-feed")
async def get_live_feed(limit: int = Query(default=50, ge=1, le=200)):
    """Return the most recent arrests across all states — the live ticker.

    Normalizes field names so the Multi-State UI always sees:
    full_name, county, state, charges, bond_amount, bail_amount, scraped_at.
    """
    arrests = get_collection("arrests")
    results = []
    # Prefer scraped_at; fall back to created_at for legacy docs
    async for doc in arrests.find({}, {"_id": 0}).sort(
        [("scraped_at", -1), ("created_at", -1)]
    ).limit(limit):
        for k, v in list(doc.items()):
            if hasattr(v, "isoformat"):
                doc[k] = v.isoformat()
        # Normalize casing / aliases for the frontend
        state = (
            doc.get("state")
            or doc.get("State")
            or ""
        )
        if isinstance(state, str):
            state = state.upper()[:2] if len(state) >= 2 else state.upper()
        bond = doc.get("bond_amount")
        if bond is None:
            bond = doc.get("bail_amount")
        try:
            bond_num = float(bond) if bond not in (None, "") else 0
        except (TypeError, ValueError):
            bond_num = 0
        charges = doc.get("charges") or doc.get("Charges") or ""
        if isinstance(charges, list):
            charges = " | ".join(str(c) for c in charges)
        results.append({
            "full_name": doc.get("full_name") or doc.get("Full_Name") or "Unknown",
            "county": doc.get("county") or doc.get("County") or "?",
            "state": state or "??",
            "charges": charges,
            "bond_amount": bond_num,
            "bail_amount": bond_num,  # alias used by older UI
            "lead_score": doc.get("lead_score") or 0,
            "lead_status": doc.get("lead_status") or "",
            "booking_number": doc.get("booking_number") or doc.get("Booking_Number") or "",
            "scraped_at": doc.get("scraped_at") or doc.get("created_at") or "",
            "status": doc.get("status") or doc.get("custody_status") or "",
        })

    return {
        "feed": results,
        "count": len(results),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
