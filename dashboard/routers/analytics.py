from __future__ import annotations

"""ShamrockLeads — Revenue Analytics API Blueprint

Endpoints:
  GET /api/analytics/revenue          — Revenue KPIs + time-series
  GET /api/analytics/funnel           — Lead → Bond conversion funnel
  GET /api/analytics/county-performance — Per-county revenue + lead volume
  GET /api/analytics/surety-breakdown — OSI vs Palmetto comparison
  GET /api/analytics/heatmap          — Arrests by county + hour of day
  GET /api/analytics/bond-distribution — Bond amount histogram

All routes use Quart (async) + Motor (async MongoDB).
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from dashboard.extensions import get_db

logger = logging.getLogger(__name__)

analytics_bp = APIRouter(prefix="/api", tags=["analytics"])
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _date_floor(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _range_filter(days: int | None) -> dict:
    """Return a MongoDB $gte filter dict for the given number of days back."""
    if not days:
        return {}
    cutoff = _utc_now() - timedelta(days=int(days))
    return {"$gte": cutoff}


# ─────────────────────────────────────────────────────────────────────────────
# REVENUE KPIs + TIME-SERIES
# ─────────────────────────────────────────────────────────────────────────────
@analytics_bp.get("/analytics/revenue")
async def revenue_metrics(days: int = Query(default=30)):
    """
    Returns:
      kpis: { total_collected, collected_30d, collected_7d,
              total_liability, avg_premium, conversion_rate,
              osi_collected, palmetto_collected }
      time_series: [ {date, amount} ... ] for the requested range
    """
    try:
        db = get_db()
        days = int(days)

        # ── Payment totals ────────────────────────────────────────────────────
        payments_col = db["payments"]
        active_bonds_col = db["active_bonds"]
        arrests_col = db["arrests"]
        prospective_col = db["prospective_bonds"]

        # All-time collected
        total_pipeline = await payments_col.aggregate([
            {"$match": {"status": {"$in": ["completed", "paid", "success"]}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]).to_list(1)
        total_collected = total_pipeline[0]["total"] if total_pipeline else 0.0

        # 30-day collected
        cutoff_30 = _utc_now() - timedelta(days=30)
        p30 = await payments_col.aggregate([
            {"$match": {"status": {"$in": ["completed", "paid", "success"]},
                        "timestamp": {"$gte": cutoff_30}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]).to_list(1)
        collected_30d = p30[0]["total"] if p30 else 0.0

        # 7-day collected
        cutoff_7 = _utc_now() - timedelta(days=7)
        p7 = await payments_col.aggregate([
            {"$match": {"status": {"$in": ["completed", "paid", "success"]},
                        "timestamp": {"$gte": cutoff_7}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]).to_list(1)
        collected_7d = p7[0]["total"] if p7 else 0.0

        # Total bond liability (sum of active bond amounts)
        liability_pipe = await active_bonds_col.aggregate([
            {"$match": {"status": {"$nin": ["released", "forfeited", "exonerated"]}}},
            {"$group": {"_id": None, "total": {"$sum": "$bond_amount"}}}
        ]).to_list(1)
        total_liability = liability_pipe[0]["total"] if liability_pipe else 0.0

        # Avg premium per bond
        avg_pipe = await active_bonds_col.aggregate([
            {"$match": {"premium": {"$gt": 0}}},
            {"$group": {"_id": None, "avg": {"$avg": "$premium"}, "count": {"$sum": 1}}}
        ]).to_list(1)
        avg_premium = round(avg_pipe[0]["avg"], 2) if avg_pipe else 0.0
        total_bonds = avg_pipe[0]["count"] if avg_pipe else 0

        # Conversion rate: leads → bonded
        total_leads = await arrests_col.estimated_document_count()
        conversion_rate = round((total_bonds / total_leads * 100), 2) if total_leads > 0 else 0.0

        # Surety breakdown
        osi_pipe = await active_bonds_col.aggregate([
            {"$match": {"insurance_company": {"$regex": "osi|o'shaughnahill", "$options": "i"}}},
            {"$group": {"_id": None, "total": {"$sum": "$premium"}, "count": {"$sum": 1}}}
        ]).to_list(1)
        palmetto_pipe = await active_bonds_col.aggregate([
            {"$match": {"insurance_company": {"$regex": "palmetto", "$options": "i"}}},
            {"$group": {"_id": None, "total": {"$sum": "$premium"}, "count": {"$sum": 1}}}
        ]).to_list(1)
        osi_collected = osi_pipe[0]["total"] if osi_pipe else 0.0
        palmetto_collected = palmetto_pipe[0]["total"] if palmetto_pipe else 0.0

        # ── Time-series: daily revenue for requested range ────────────────────
        cutoff = _utc_now() - timedelta(days=days)
        ts_pipe = await payments_col.aggregate([
            {"$match": {"status": {"$in": ["completed", "paid", "success"]},
                        "timestamp": {"$gte": cutoff}}},
            {"$group": {
                "_id": {
                    "y": {"$year": "$timestamp"},
                    "m": {"$month": "$timestamp"},
                    "d": {"$dayOfMonth": "$timestamp"}
                },
                "amount": {"$sum": "$amount"},
                "count": {"$sum": 1}
            }},
            {"$sort": {"_id.y": 1, "_id.m": 1, "_id.d": 1}}
        ]).to_list(None)

        time_series = []
        for row in ts_pipe:
            d = row["_id"]
            time_series.append({
                "date": f"{d['y']}-{d['m']:02d}-{d['d']:02d}",
                "amount": round(row["amount"], 2),
                "count": row["count"]
            })

        # Fill missing days with 0
        filled = {}
        for i in range(days):
            day = (_date_floor(_utc_now()) - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
            filled[day] = {"date": day, "amount": 0.0, "count": 0}
        for row in time_series:
            filled[row["date"]] = row
        time_series = sorted(filled.values(), key=lambda x: x["date"])

        return {
            "success": True,
            "kpis": {
                "total_collected": round(total_collected, 2),
                "collected_30d": round(collected_30d, 2),
                "collected_7d": round(collected_7d, 2),
                "total_liability": round(total_liability, 2),
                "avg_premium": avg_premium,
                "conversion_rate": conversion_rate,
                "total_bonds": total_bonds,
                "osi_collected": round(osi_collected, 2),
                "palmetto_collected": round(palmetto_collected, 2),
            },
            "time_series": time_series,
            "days": days
        }
    except Exception as exc:
        logger.exception("analytics/revenue error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# CONVERSION FUNNEL
# ─────────────────────────────────────────────────────────────────────────────
@analytics_bp.get("/analytics/funnel")
async def funnel_data():
    """Returns stage counts for the full lead → bonded → paid funnel."""
    try:
        db = get_db()
        arrests_col = db["arrests"]
        prospective_col = db["prospective_bonds"]
        active_bonds_col = db["active_bonds"]
        payments_col = db["payments"]

        total_leads = await arrests_col.estimated_document_count()
        contacted = await prospective_col.count_documents({"stage": "contacted"})
        negotiating = await prospective_col.count_documents({"stage": "negotiating"})
        paperwork = await prospective_col.count_documents({"stage": "paperwork"})
        ready = await prospective_col.count_documents({"stage": "ready"})
        bonded = await active_bonds_col.estimated_document_count()
        paid = await payments_col.count_documents(
            {"status": {"$in": ["completed", "paid", "success"]}}
        )

        stages = [
            {"stage": "Leads Scraped", "count": total_leads, "color": "#6366f1"},
            {"stage": "Contacted", "count": contacted, "color": "#8b5cf6"},
            {"stage": "Negotiating", "count": negotiating, "color": "#ec4899"},
            {"stage": "Paperwork", "count": paperwork, "color": "#f59e0b"},
            {"stage": "Ready", "count": ready, "color": "#10b981"},
            {"stage": "Bonded", "count": bonded, "color": "#22c55e"},
            {"stage": "Paid", "count": paid, "color": "#16a34a"},
        ]

        return {"success": True, "stages": stages}
    except Exception as exc:
        logger.exception("analytics/funnel error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# COUNTY PERFORMANCE
# ─────────────────────────────────────────────────────────────────────────────
@analytics_bp.get("/analytics/county-performance")
async def county_performance(days: int = Query(default=90)):
    """Returns per-county: lead volume, bond count, total premium, avg bond."""
    try:
        db = get_db()
        days = int(days)
        cutoff = (_utc_now() - timedelta(days=days)).isoformat()

        arrests_col = db["arrests"]
        active_bonds_col = db["active_bonds"]

        # Leads by county (scraped_at is ISO string — use string comparison)
        leads_pipe = await arrests_col.aggregate([
            {"$match": {"scraped_at": {"$gte": cutoff}}},
            {"$group": {"_id": "$county", "leads": {"$sum": 1}}},
            {"$sort": {"leads": -1}},
            {"$limit": 20}
        ]).to_list(None)

        # Bonds by county
        bonds_pipe = await active_bonds_col.aggregate([
            {"$match": {"created_at": {"$gte": cutoff}}},
            {"$group": {
                "_id": "$county",
                "bonds": {"$sum": 1},
                "total_premium": {"$sum": "$premium"},
                "avg_bond": {"$avg": "$bond_amount"}
            }},
        ]).to_list(None)

        bonds_map = {r["_id"]: r for r in bonds_pipe}

        result = []
        for row in leads_pipe:
            county = row["_id"] or "Unknown"
            b = bonds_map.get(county, {})
            result.append({
                "county": county,
                "leads": row["leads"],
                "bonds": b.get("bonds", 0),
                "total_premium": round(b.get("total_premium", 0), 2),
                "avg_bond": round(b.get("avg_bond", 0), 2),
                "conversion": round(b.get("bonds", 0) / row["leads"] * 100, 1) if row["leads"] > 0 else 0
            })

        result.sort(key=lambda x: x["total_premium"], reverse=True)
        return {"success": True, "counties": result, "days": days}
    except Exception as exc:
        logger.exception("analytics/county-performance error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@analytics_bp.get("/analytics/county")
async def county_performance_alias():
    """Alias for /analytics/county-performance — used by sl-analytics-apex.js."""
    return await county_performance()


# ─────────────────────────────────────────────────────────────────────────────
# SURETY BREAKDOWN
# ─────────────────────────────────────────────────────────────────────────────
@analytics_bp.get("/analytics/surety-breakdown")
async def surety_breakdown():
    """Returns OSI vs Palmetto bond counts, premium totals, avg bond amounts."""
    try:
        db = get_db()
        active_bonds_col = db["active_bonds"]

        pipe = await active_bonds_col.aggregate([
            {"$group": {
                "_id": "$insurance_company",
                "count": {"$sum": 1},
                "total_premium": {"$sum": "$premium"},
                "total_bond": {"$sum": "$bond_amount"},
                "avg_bond": {"$avg": "$bond_amount"}
            }},
            {"$sort": {"count": -1}}
        ]).to_list(None)

        sureties = []
        for row in pipe:
            label = row["_id"] or "Unknown"
            if not label:
                continue
            sureties.append({
                "surety": label,
                "count": row["count"],
                "total_premium": round(row["total_premium"] or 0, 2),
                "total_bond": round(row["total_bond"] or 0, 2),
                "avg_bond": round(row["avg_bond"] or 0, 2),
            })

        return {"success": True, "sureties": sureties}
    except Exception as exc:
        logger.exception("analytics/surety-breakdown error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# ARREST HEATMAP (county × hour-of-day)
# ─────────────────────────────────────────────────────────────────────────────
@analytics_bp.get("/analytics/heatmap")
async def arrest_heatmap(days: int = Query(default=30)):
    """Returns arrest counts grouped by county and hour of day."""
    try:
        db = get_db()
        days = int(days)
        cutoff = (_utc_now() - timedelta(days=days)).isoformat()
        arrests_col = db["arrests"]

        # scraped_at is ISO string — extract hour via $substr (pos 11, len 2)
        pipe = await arrests_col.aggregate([
            {"$match": {"scraped_at": {"$gte": cutoff}}},
            {"$addFields": {
                "_hour": {
                    "$cond": {
                        "if": {"$and": [{"$isArray": ["$scraped_at"]}, False]},
                        "then": 0,
                        "else": {
                            "$convert": {
                                "input": {"$substr": ["$scraped_at", 11, 2]},
                                "to": "int",
                                "onError": 0,
                                "onNull": 0
                            }
                        }
                    }
                }
            }},
            {"$group": {
                "_id": {
                    "county": "$county",
                    "hour": "$_hour"
                },
                "count": {"$sum": 1}
            }},
            {"$sort": {"count": -1}}
        ]).to_list(None)

        rows = [
            {"county": r["_id"]["county"] or "Unknown",
             "hour": r["_id"]["hour"],
             "count": r["count"]}
            for r in pipe
        ]
        return {"success": True, "rows": rows, "days": days}
    except Exception as exc:
        logger.exception("analytics/heatmap error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# BOND AMOUNT DISTRIBUTION (histogram)
# ─────────────────────────────────────────────────────────────────────────────
@analytics_bp.get("/analytics/bond-distribution")
async def bond_distribution():
    """Returns bond amount histogram buckets."""
    try:
        db = get_db()
        active_bonds_col = db["active_bonds"]

        buckets = [
            (0, 1000, "$0–1K"),
            (1000, 2500, "$1K–2.5K"),
            (2500, 5000, "$2.5K–5K"),
            (5000, 10000, "$5K–10K"),
            (10000, 25000, "$10K–25K"),
            (25000, 50000, "$25K–50K"),
            (50000, 100000, "$50K–100K"),
            (100000, 10_000_000, "$100K+"),
        ]

        result = []
        for lo, hi, label in buckets:
            count = await active_bonds_col.count_documents(
                {"bond_amount": {"$gte": lo, "$lt": hi}}
            )
            result.append({"label": label, "count": count, "min": lo, "max": hi})

        return {"success": True, "buckets": result}
    except Exception as exc:
        logger.exception("analytics/bond-distribution error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# PREDICTIVE PIPELINE (monthly revenue forecast)
# ─────────────────────────────────────────────────────────────────────────────
@analytics_bp.get("/analytics/forecast")
async def revenue_forecast():
    """Simple linear extrapolation of current-month pace to full-month estimate."""
    try:
        db = get_db()
        payments_col = db["payments"]

        now = _utc_now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        days_elapsed = (now - month_start).days + 1
        days_in_month = 30  # approximation

        mtd_pipe = await payments_col.aggregate([
            {"$match": {"status": {"$in": ["completed", "paid", "success"]},
                        "timestamp": {"$gte": month_start}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}}
        ]).to_list(1)

        mtd = mtd_pipe[0]["total"] if mtd_pipe else 0.0
        mtd_count = mtd_pipe[0]["count"] if mtd_pipe else 0
        daily_rate = mtd / days_elapsed if days_elapsed > 0 else 0.0
        forecast = round(daily_rate * days_in_month, 2)
        pct_complete = round(days_elapsed / days_in_month * 100, 1)

        return {
            "success": True,
            "mtd": round(mtd, 2),
            "mtd_count": mtd_count,
            "daily_rate": round(daily_rate, 2),
            "forecast": forecast,
            "days_elapsed": days_elapsed,
            "pct_complete": pct_complete
        }
    except Exception as exc:
        logger.exception("analytics/forecast error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
