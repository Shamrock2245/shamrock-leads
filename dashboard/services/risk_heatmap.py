"""
ShamrockLeads — Risk Heatmap Analytics Service
================================================
Geographic risk visualization data for the dashboard.
Aggregates arrest data, lead scores, and bond outcomes by county
to produce heatmap-ready data structures.

Features:
  - County-level risk scoring (arrest volume, severity, conversion rate)
  - Temporal heatmaps (hour-of-day × day-of-week arrest patterns)
  - Charge category heatmaps (violence, drugs, property by county)
  - Bond outcome heatmaps (FTA rate, re-arrest rate by county)

Output is JSON-ready for frontend chart rendering (Chart.js heatmap plugin).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Geographic Risk Heatmap
# ─────────────────────────────────────────────────────────────────────────────

async def get_county_risk_heatmap(db, days: int = 30) -> Dict[str, Any]:
    """Generate county-level risk heatmap data.

    Each county gets a composite risk score (0-100) based on:
      - Arrest volume (normalized)
      - Average charge severity
      - Hot lead percentage
      - Bond conversion rate (if data available)
      - Re-arrest rate
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    arrests_col = db["arrests"]
    bonds_col = db["active_bonds"]

    # Aggregate arrests by county
    pipeline = [
        {"$match": {"scraped_at": {"$gte": cutoff}}},
        {"$group": {
            "_id": "$county",
            "total_arrests": {"$sum": 1},
            "avg_lead_score": {"$avg": {"$ifNull": ["$lead_score", 0]}},
            "hot_count": {"$sum": {"$cond": [{"$eq": ["$lead_status", "Hot"]}, 1, 0]}},
            "warm_count": {"$sum": {"$cond": [{"$eq": ["$lead_status", "Warm"]}, 1, 0]}},
            "avg_bond_amount": {"$avg": {"$ifNull": [
                {"$toDouble": {"$ifNull": ["$bond_amount_numeric", 0]}}, 0
            ]}},
            "total_bond_value": {"$sum": {"$ifNull": [
                {"$toDouble": {"$ifNull": ["$bond_amount_numeric", 0]}}, 0
            ]}},
        }},
        {"$sort": {"total_arrests": -1}},
    ]

    county_stats = {}
    async for row in arrests_col.aggregate(pipeline):
        county = row["_id"] or "Unknown"
        total = row["total_arrests"]
        county_stats[county] = {
            "county": county,
            "total_arrests": total,
            "avg_lead_score": round(row["avg_lead_score"], 1),
            "hot_count": row["hot_count"],
            "warm_count": row["warm_count"],
            "hot_percentage": round((row["hot_count"] / total * 100) if total > 0 else 0, 1),
            "avg_bond_amount": round(row["avg_bond_amount"], 2),
            "total_bond_value": round(row["total_bond_value"], 2),
        }

    # Enrich with bond data (conversion rate)
    bond_pipeline = [
        {"$match": {"created_at": {"$gte": cutoff}}},
        {"$group": {
            "_id": "$county",
            "bonds_written": {"$sum": 1},
            "bonds_active": {"$sum": {"$cond": [
                {"$in": ["$status", ["active", "monitoring"]]}, 1, 0
            ]}},
            "bonds_forfeited": {"$sum": {"$cond": [
                {"$eq": ["$status", "forfeited"]}, 1, 0
            ]}},
        }},
    ]

    try:
        async for row in bonds_col.aggregate(bond_pipeline):
            county = row["_id"]
            if county in county_stats:
                cs = county_stats[county]
                cs["bonds_written"] = row["bonds_written"]
                cs["bonds_active"] = row["bonds_active"]
                cs["bonds_forfeited"] = row["bonds_forfeited"]
                cs["conversion_rate"] = round(
                    (row["bonds_written"] / cs["total_arrests"] * 100)
                    if cs["total_arrests"] > 0 else 0, 1
                )
                cs["forfeiture_rate"] = round(
                    (row["bonds_forfeited"] / row["bonds_written"] * 100)
                    if row["bonds_written"] > 0 else 0, 1
                )
    except Exception as e:
        logger.debug("Bond enrichment error: %s", e)

    # Compute composite risk score (0-100) for each county
    if county_stats:
        max_arrests = max(cs["total_arrests"] for cs in county_stats.values())
    else:
        max_arrests = 1

    for county, cs in county_stats.items():
        # Volume component (0-40)
        volume_score = (cs["total_arrests"] / max_arrests) * 40 if max_arrests > 0 else 0

        # Lead quality component (0-30)
        quality_score = cs["avg_lead_score"] * 0.3

        # Hot lead density (0-20)
        hot_density = cs["hot_percentage"] * 0.2

        # Forfeiture penalty (0-10)
        forfeit_penalty = cs.get("forfeiture_rate", 0) * 0.1

        cs["risk_score"] = round(min(100, volume_score + quality_score + hot_density + forfeit_penalty), 1)
        cs["risk_level"] = _risk_level(cs["risk_score"])

    # Sort by risk score
    counties = sorted(county_stats.values(), key=lambda x: x["risk_score"], reverse=True)

    return {
        "counties": counties,
        "period_days": days,
        "total_counties": len(counties),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Temporal Heatmap (Hour × Day)
# ─────────────────────────────────────────────────────────────────────────────

async def get_temporal_heatmap(db, days: int = 30, county: Optional[str] = None) -> Dict[str, Any]:
    """Generate hour-of-day × day-of-week arrest heatmap.

    Returns a 24×7 grid showing arrest density patterns.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    match_filter = {"scraped_at": {"$gte": cutoff}}
    if county:
        match_filter["county"] = {"$regex": f"^{county}$", "$options": "i"}

    pipeline = [
        {"$match": match_filter},
        {"$addFields": {
            "parsed_date": {"$cond": {
                "if": {"$isNumber": "$scraped_at"},
                "then": {"$toDate": "$scraped_at"},
                "else": "$scraped_at"
            }}
        }},
        {"$group": {
            "_id": {
                "hour": {"$hour": "$parsed_date"},
                "dow": {"$dayOfWeek": "$parsed_date"},  # 1=Sun, 7=Sat
            },
            "count": {"$sum": 1},
            "avg_score": {"$avg": {"$ifNull": ["$lead_score", 0]}},
        }},
    ]

    # Initialize 24×7 grid
    grid = [[0] * 7 for _ in range(24)]
    score_grid = [[0.0] * 7 for _ in range(24)]

    try:
        async for row in db["arrests"].aggregate(pipeline):
            hour = row["_id"]["hour"]
            dow = (row["_id"]["dow"] - 2) % 7  # Convert to Mon=0, Sun=6
            if 0 <= hour < 24 and 0 <= dow < 7:
                grid[hour][dow] = row["count"]
                score_grid[hour][dow] = round(row["avg_score"], 1)
    except Exception as e:
        logger.debug("Temporal heatmap error: %s", e)

    # Find peak patterns
    peak_hour = 0
    peak_count = 0
    for h in range(24):
        total = sum(grid[h])
        if total > peak_count:
            peak_count = total
            peak_hour = h

    peak_day = 0
    day_totals = [sum(grid[h][d] for h in range(24)) for d in range(7)]
    if day_totals:
        peak_day = day_totals.index(max(day_totals))

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    return {
        "grid": grid,
        "score_grid": score_grid,
        "hours": list(range(24)),
        "days": day_names,
        "peak_hour": peak_hour,
        "peak_day": day_names[peak_day],
        "peak_day_index": peak_day,
        "total_arrests": sum(sum(row) for row in grid),
        "county_filter": county,
        "period_days": days,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Charge Category Heatmap
# ─────────────────────────────────────────────────────────────────────────────

async def get_charge_category_heatmap(db, days: int = 30) -> Dict[str, Any]:
    """Generate county × charge-category heatmap.

    Categories: Violence, Drugs, Property, DUI, Flight Risk, Other
    """
    from scoring.feature_engineering import (
        VIOLENCE_KEYWORDS, DRUG_KEYWORDS, PROPERTY_KEYWORDS,
        DUI_KEYWORDS, FLIGHT_RISK_KEYWORDS,
    )

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cursor = db["arrests"].find(
        {"scraped_at": {"$gte": cutoff}},
        {"county": 1, "charges": 1}
    )

    categories = ["Violence", "Drugs", "Property", "DUI", "Flight Risk", "Other"]
    county_cats = defaultdict(lambda: defaultdict(int))

    async for arrest in cursor:
        county = arrest.get("county", "Unknown")
        charges = (arrest.get("charges") or "").lower()

        categorized = False
        if any(kw in charges for kw in VIOLENCE_KEYWORDS):
            county_cats[county]["Violence"] += 1
            categorized = True
        if any(kw in charges for kw in DRUG_KEYWORDS):
            county_cats[county]["Drugs"] += 1
            categorized = True
        if any(kw in charges for kw in PROPERTY_KEYWORDS):
            county_cats[county]["Property"] += 1
            categorized = True
        if any(kw in charges for kw in DUI_KEYWORDS):
            county_cats[county]["DUI"] += 1
            categorized = True
        if any(kw in charges for kw in FLIGHT_RISK_KEYWORDS):
            county_cats[county]["Flight Risk"] += 1
            categorized = True
        if not categorized:
            county_cats[county]["Other"] += 1

    # Build matrix
    counties_sorted = sorted(county_cats.keys())
    matrix = []
    for county in counties_sorted:
        row = {cat: county_cats[county].get(cat, 0) for cat in categories}
        row["county"] = county
        row["total"] = sum(row.get(c, 0) for c in categories)
        matrix.append(row)

    return {
        "matrix": matrix,
        "categories": categories,
        "counties": counties_sorted,
        "period_days": days,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Risk Trend Analysis
# ─────────────────────────────────────────────────────────────────────────────

async def get_risk_trend(db, county: Optional[str] = None, days: int = 90) -> Dict[str, Any]:
    """Generate daily risk trend for a county or all counties.

    Shows how the average lead score and arrest volume change over time.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    match_filter = {"scraped_at": {"$gte": cutoff}}
    if county:
        match_filter["county"] = {"$regex": f"^{county}$", "$options": "i"}

    pipeline = [
        {"$match": match_filter},
        {"$group": {
            "_id": {
                "$dateToString": {
                    "format": "%Y-%m-%d",
                    "date": {"$cond": {
                        "if": {"$isNumber": "$scraped_at"},
                        "then": {"$toDate": "$scraped_at"},
                        "else": "$scraped_at"
                    }}
                }
            },
            "count": {"$sum": 1},
            "avg_score": {"$avg": {"$ifNull": ["$lead_score", 0]}},
            "hot_count": {"$sum": {"$cond": [{"$eq": ["$lead_status", "Hot"]}, 1, 0]}},
            "total_bond": {"$sum": {"$ifNull": [
                {"$toDouble": {"$ifNull": ["$bond_amount_numeric", 0]}}, 0
            ]}},
        }},
        {"$sort": {"_id": 1}},
    ]

    data_points = []
    try:
        async for row in db["arrests"].aggregate(pipeline):
            data_points.append({
                "date": row["_id"],
                "arrest_count": row["count"],
                "avg_score": round(row["avg_score"], 1),
                "hot_count": row["hot_count"],
                "total_bond_value": round(row["total_bond"], 2),
            })
    except Exception as e:
        logger.debug("Risk trend error: %s", e)

    # Compute moving averages
    if len(data_points) >= 7:
        scores = [d["avg_score"] for d in data_points]
        counts = [d["arrest_count"] for d in data_points]

        for i in range(len(data_points)):
            window_start = max(0, i - 6)
            data_points[i]["score_7d_avg"] = round(
                sum(scores[window_start:i+1]) / len(scores[window_start:i+1]), 1
            )
            data_points[i]["count_7d_avg"] = round(
                sum(counts[window_start:i+1]) / len(counts[window_start:i+1]), 1
            )

    return {
        "data": data_points,
        "county_filter": county,
        "period_days": days,
        "total_arrests": sum(d["arrest_count"] for d in data_points),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _risk_level(score: float) -> str:
    """Classify risk score into level."""
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"
