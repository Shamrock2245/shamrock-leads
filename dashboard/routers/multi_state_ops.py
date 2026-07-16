from __future__ import annotations
"""
ShamrockLeads — Multi-State Operations API
Endpoints:
  GET /api/ops/state-summary          — KPIs per state (FL/GA/SC/NC/TN/TX/LA)
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
    get_collection,
    index_scraper_status_docs,
    resolve_scraper_status,
)

logger = logging.getLogger(__name__)
multi_state_bp = APIRouter(prefix="/api/ops", tags=["multi_state_ops"])

# Live + scaffolding states (Palmetto footprint). Registry only includes dirs that exist.
ACTIVE_STATES = ("FL", "GA", "SC", "NC", "TN", "TX", "LA")

# ─────────────────────────────────────────────────────────────────────────────
# SCRAPER REGISTRY — built from the actual scraper files on disk
# ─────────────────────────────────────────────────────────────────────────────


def _build_registry() -> list[dict]:
    """Dynamically build the full scraper registry from the scrapers/ directory."""
    base = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../scrapers"))
    state_dirs = {
        "FL": os.path.join(base, "counties"),
        "GA": os.path.join(base, "counties_ga"),
        "SC": os.path.join(base, "counties_sc"),
        "NC": os.path.join(base, "counties_nc"),
        # Scaffolded packages (appear when scrapers are added):
        "TN": os.path.join(base, "counties_tn"),
        "TX": os.path.join(base, "counties_tx"),
        "CT": os.path.join(base, "counties_ct"),
        "LA": os.path.join(base, "counties_la"),
        "MS": os.path.join(base, "counties_ms"),
    }
    platform_map = {
        "jailtracker_base": "JailTracker",
        "p2c_base": "P2C",
        "eas_base": "EAS",
        "interopweb_base": "InteropWeb",
        "zuercher_base": "Zuercher",
        "southern_sw_base": "Southern SW",
        "socrata_base": "Socrata",
        "xml_feed_base": "XML Feed",
        "new_world_base": "New World",
        "odyssey_base": "Tyler Odyssey",
        "kologik_base": "Kologik",
        "smartcop_base": "SmartCOP",
        "smartweb_parser": "SmartWeb",
        "base_scraper": "Custom HTML",
    }
    registry = []
    for state, dirpath in state_dirs.items():
        dirpath = os.path.normpath(dirpath)
        if not os.path.exists(dirpath):
            continue
        for fname in sorted(os.listdir(dirpath)):
            if not fname.endswith(".py") or fname in ("__init__.py", "eas_batch_runner.py"):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath) as f:
                    content = f.read()
            except Exception:
                continue
            platform = "Custom HTML"
            for key, val in platform_map.items():
                if key in content:
                    platform = val
                    break
            # Prefer explicit county property / COUNTY_NAME over first string return
            m = re.search(r'COUNTY_NAME\s*=\s*["\']([^"\']+)["\']', content)
            if not m:
                m = re.search(
                    r'def county\(self\)[^:]*:\s*(?:.*?return\s+["\']([^"\']+)["\'])',
                    content,
                    re.DOTALL,
                )
            if not m:
                m = re.search(r'return\s+"([^"]+)"', content)
            county = m.group(1) if m else fname.replace(".py", "").replace("_", " ").title()
            county_slug = county.lower().replace(" ", "_").replace("-", "_")
            scraper_id = (
                f"scraper_{county_slug}" if state == "FL"
                else f"scraper_{state.lower()}_{county_slug}"
            )
            registry.append({
                "county": county,
                "state": state,
                "platform": platform,
                "file": fname,
                "scraper_id": scraper_id,
                "trigger_key": (
                    county_slug if state == "FL"
                    else f"{state.lower()}_{county_slug}"
                ),
                "label": f"{county} ({state})",
            })
    return registry


_REGISTRY_CACHE: list[dict] = []
_REGISTRY_BUILT_AT: Optional[datetime] = None


def _get_registry() -> list[dict]:
    global _REGISTRY_CACHE, _REGISTRY_BUILT_AT
    now = datetime.now(timezone.utc)
    if not _REGISTRY_CACHE or (
        _REGISTRY_BUILT_AT and (now - _REGISTRY_BUILT_AT).seconds > 300
    ):
        _REGISTRY_CACHE = _build_registry()
        _REGISTRY_BUILT_AT = now
    return _REGISTRY_CACHE


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
    """Return high-level KPIs per state."""
    registry = _get_registry()
    arrests = get_collection("arrests")
    scraper_status = get_collection("scraper_status")

    # Build state → county mapping
    state_counties: dict[str, list[str]] = {}
    for r in registry:
        state_counties.setdefault(r["state"], []).append(r["county"])

    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)

    # Always surface FL/GA/SC/NC even when a dir is empty (zeros).
    for st in ACTIVE_STATES:
        state_counties.setdefault(st, [])

    result = {}
    for state, counties in state_counties.items():
        total_counties = len(counties)

        # Arrest counts — accept both datetime and ISO string scraped_at
        state_match = {"$or": [
            {"state": state},
            {"state": state.lower()},
            {"State": state},
        ]}
        arrests_24h = await arrests.count_documents({
            **state_match,
            "$and": [{"$or": [
                {"scraped_at": {"$gte": cutoff_24h}},
                {"scraped_at": {"$gte": cutoff_24h.isoformat()}},
            ]}],
        })
        arrests_7d = await arrests.count_documents({
            **state_match,
            "$and": [{"$or": [
                {"scraped_at": {"$gte": cutoff_7d}},
                {"scraped_at": {"$gte": cutoff_7d.isoformat()}},
            ]}],
        })
        total_arrests = await arrests.count_documents(state_match)

        # Scraper health
        active = 0
        errors = 0
        never_run = 0
        if counties:
            async for doc in scraper_status.find(
                {"county": {"$in": counties}}, {"_id": 0, "status": 1}
            ):
                s = doc.get("status", "never_run")
                if s in ("ok", "healthy"):
                    active += 1
                elif s in ("error", "offline"):
                    errors += 1
                else:
                    never_run += 1
        accounted = active + errors + never_run
        if total_counties > accounted:
            never_run += total_counties - accounted

        # Bond + lead stats for this state
        bond_stats: dict = {"avg_bond": 0.0, "max_bond": 0.0, "total_bond": 0.0}
        hot_leads = 0
        warm_leads = 0
        async for r in arrests.aggregate([
            {"$match": {**state_match, "bond_amount": {"$gt": 0}}},
            {"$group": {
                "_id": None,
                "avg_bond": {"$avg": "$bond_amount"},
                "max_bond": {"$max": "$bond_amount"},
                "total_bond": {"$sum": "$bond_amount"},
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
            "error_scrapers": errors,
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
    """Return the most recent arrests across all states — the live ticker."""
    arrests = get_collection("arrests")
    results = []
    async for doc in arrests.find({}, {"_id": 0}).sort("scraped_at", -1).limit(limit):
        for k, v in doc.items():
            if hasattr(v, "isoformat"):
                doc[k] = v.isoformat()
        results.append(doc)

    return {"feed": results, "count": len(results)}
