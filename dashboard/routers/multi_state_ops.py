from __future__ import annotations
"""
ShamrockLeads — Multi-State Operations API
Endpoints:
  GET /api/ops/state-summary          — KPIs per state (FL/GA/SC)
  GET /api/ops/scraper-registry       — Full registry with state + platform metadata
  GET /api/ops/arrests/multi-state    — Recent arrests across all states with filters
  GET /api/ops/county-heatmap         — Arrest volume by county (all states)
  GET /api/ops/platform-breakdown     — Scraper platform distribution
  GET /api/ops/live-feed              — Last 50 arrests across all states (real-time feed)
"""
import json
import os
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from dashboard.extensions import get_collection, get_db

logger = logging.getLogger(__name__)
multi_state_bp = APIRouter(prefix="/api/ops", tags=["multi_state_ops"])

# ─────────────────────────────────────────────────────────────────────────────
# SCRAPER REGISTRY — built from the actual scraper files on disk
# ─────────────────────────────────────────────────────────────────────────────
def _build_registry() -> list[dict]:
    """Dynamically build the full scraper registry from the scrapers/ directory."""
    state_dirs = {
        "FL": os.path.join(os.path.dirname(__file__), "../../scrapers/counties"),
        "GA": os.path.join(os.path.dirname(__file__), "../../scrapers/counties_ga"),
        "SC": os.path.join(os.path.dirname(__file__), "../../scrapers/counties_sc"),
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
            m = re.search(r'return\s+"([^"]+)"', content)
            county = m.group(1) if m else fname.replace(".py", "").replace("_", " ").title()
            registry.append({
                "county": county,
                "state": state,
                "platform": platform,
                "file": fname,
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

    # Enrich with last-run data from MongoDB
    scraper_status = get_collection("scraper_status")
    status_docs = {}
    async for doc in scraper_status.find({}, {"_id": 0}):
        status_docs[doc.get("county", "")] = doc

    result = []
    for r in registry:
        status = status_docs.get(r["county"], {})
        result.append({
            **r,
            "status": status.get("status", "never_run"),
            "last_run": status.get("last_run_at", None),
            "last_run_iso": status.get("last_run_at", {}).isoformat() if hasattr(status.get("last_run_at"), "isoformat") else None,
            "records_last_run": status.get("records_last_run", 0),
            "total_records": status.get("total_records", 0),
            "error_message": status.get("error_message", None),
            "enabled": status.get("enabled", True),
        })

    by_state = {}
    for r in result:
        by_state.setdefault(r["state"], []).append(r)

    return {
        "total": len(result),
        "by_state": {s: len(v) for s, v in by_state.items()},
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

    result = {}
    for state, counties in state_counties.items():
        total_counties = len(counties)

        # Arrest counts
        arrests_24h = await arrests.count_documents({
            "state": state,
            "scraped_at": {"$gte": cutoff_24h},
        })
        arrests_7d = await arrests.count_documents({
            "state": state,
            "scraped_at": {"$gte": cutoff_7d},
        })
        total_arrests = await arrests.count_documents({"state": state})

        # Scraper health
        active = 0
        errors = 0
        never_run = 0
        async for doc in scraper_status.find({"county": {"$in": counties}}, {"_id": 0, "status": 1}):
            s = doc.get("status", "never_run")
            if s in ("ok", "healthy"):
                active += 1
            elif s in ("error", "offline"):
                errors += 1
            else:
                never_run += 1
        never_run += total_counties - active - errors - never_run

        result[state] = {
            "state": state,
            "total_counties": total_counties,
            "active_scrapers": active,
            "error_scrapers": errors,
            "never_run": never_run,
            "arrests_24h": arrests_24h,
            "arrests_7d": arrests_7d,
            "total_arrests": total_arrests,
        }

    return {"states": result, "generated_at": now.isoformat()}


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

    query: dict = {"scraped_at": {"$gte": cutoff}}
    if state:
        query["state"] = state.upper()
    if county:
        query["county"] = {"$regex": county, "$options": "i"}
    if q:
        query["$or"] = [
            {"full_name": {"$regex": q, "$options": "i"}},
            {"booking_number": {"$regex": q, "$options": "i"}},
            {"charges": {"$regex": q, "$options": "i"}},
        ]

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
