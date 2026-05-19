# ── AUTO-MIGRATED: Quart Blueprint → FastAPI APIRouter (v3) ──
# _qp = dict(request.query_params) injected into fns that read query params.
# Review each endpoint and move _qp.get() calls to typed fn signatures.

"""
Court Intelligence API — ShamrockLeads Dashboard
=================================================

Blueprint: /api/court-intel/
Endpoints for court opinion ingestion, disposition analytics,
and SE US coverage metrics.
"""

import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("shamrock.api.court_intel")
court_intel_bp = APIRouter(prefix="/api", tags=["court_intel"])
def _get_db():
    """Get database from app context."""
    return # current_app_removed.config.get("db") or # current_app_removed.db


# ── Coverage & Status ────────────────────────────────────────────────────────

@court_intel_bp.get("/api/court-intel/coverage")
async def api_court_intel_coverage():
    """Return SE US court coverage registry."""
    try:
        from dashboard.services.courtlistener_client import CourtListenerClient
        client = CourtListenerClient()
        summary = client.get_coverage_summary()
        return JSONResponse(summary, status_code=200)
    except Exception as e:
        log.exception("Coverage error: %s", e)
        return {"error": str(e)}, 500


@court_intel_bp.get("/api/court-intel/stats")
async def api_court_intel_stats():
    """Return ingestion statistics from court_outcomes collection."""
    try:
        from dashboard.services.court_data_ingestor import get_ingestion_stats
        db = _get_db()
        stats = await get_ingestion_stats(db)
        return JSONResponse(stats, status_code=200)
    except Exception as e:
        log.exception("Stats error: %s", e)
        return {"error": str(e)}, 500


# ── Ingestion Trigger ────────────────────────────────────────────────────────

@court_intel_bp.post("/api/court-intel/ingest")
async def api_court_intel_ingest():
    """Trigger a court opinion ingestion cycle.

    Body (JSON):
        days_back: int (default 30)
        states: list of state codes (default all SE US)
    """
    try:
        from dashboard.services.court_data_ingestor import run_ingestion
        db = _get_db()
        body = await request.json() or {}
        days_back = body.get("days_back", 30)
        states = body.get("states", None)

        result = await run_ingestion(db, days_back=days_back, states=states)
        status = 200 if result.get("success") else 500
        return result, status
    except Exception as e:
        log.exception("Ingestion error: %s", e)
        return {"error": str(e)}, 500


# ── Disposition Rates ────────────────────────────────────────────────────────

@court_intel_bp.get("/api/court-intel/disposition-rates")
async def api_disposition_rates():
    """Get empirical disposition rates, optionally by state.

    _qp = dict(request.query_params)
    Query params:
        state: two-letter state code (optional)
    """
    try:
        from dashboard.services.court_data_ingestor import get_disposition_rates
        db = _get_db()
        state = _qp.get("state", None)
        rates = await get_disposition_rates(db, state=state)
        return JSONResponse(rates, status_code=200)
    except Exception as e:
        log.exception("Disposition rates error: %s", e)
        return {"error": str(e)}, 500


# ── Search Court Outcomes ────────────────────────────────────────────────────

@court_intel_bp.get("/api/court-intel/search")
async def api_court_intel_search():
    """Search court outcomes by case name, state, or disposition.

    _qp = dict(request.query_params)
    Query params:
        q: search query (case name)
        state: state code
        disposition: disposition type
        limit: max results (default 25)
    """
    try:
        db = _get_db()
        q = _qp.get("q", "")
        state = _qp.get("state", "")
        disposition = _qp.get("disposition", "")
        limit = min(int(_qp.get("limit", 25)), 100)

        query_filter = {}
        if q:
            query_filter["case_name"] = {"$regex": q, "$options": "i"}
        if state:
            query_filter["state"] = state.upper()
        if disposition:
            query_filter["disposition"] = disposition.lower()

        cursor = db.court_outcomes.find(
            query_filter,
            {"snippet": 0},  # Exclude snippet for performance
        ).sort("date_filed", -1).limit(limit)

        results = await cursor.to_list(length=limit)
        # Serialize ObjectIds
        for r in results:
            r["_id"] = str(r["_id"])

        return {
            "success": True,
            "count": len(results),
            "results": results,
        }, 200
    except Exception as e:
        log.exception("Search error: %s", e)
        return {"error": str(e)}, 500


# ── Defendant Court History ──────────────────────────────────────────────────

@court_intel_bp.get("/api/court-intel/defendant/<defendant_id>")
async def api_defendant_court_history(defendant_id: str):
    """Get court outcomes linked to a specific defendant."""
    try:
        db = _get_db()
        cursor = db.court_outcomes.find(
            {"matched_defendant_id": defendant_id}
        ).sort("date_filed", -1).limit(50)

        results = await cursor.to_list(length=50)
        for r in results:
            r["_id"] = str(r["_id"])

        return {
            "success": True,
            "defendant_id": defendant_id,
            "count": len(results),
            "outcomes": results,
        }, 200
    except Exception as e:
        log.exception("Defendant history error: %s", e)
        return {"error": str(e)}, 500


# ── API Health ───────────────────────────────────────────────────────────────

@court_intel_bp.get("/api/court-intel/api-health")
async def api_court_intel_health():
    """Return CourtListener API health metrics and maintenance status."""
    try:
        from dashboard.services.courtlistener_client import CourtListenerClient
        import os
        token = os.getenv("COURTLISTENER_API_TOKEN", "")
        client = CourtListenerClient(api_token=token if token else None)
        health = client.get_api_health()
        health["token_configured"] = bool(token)
        return {"success": True, **health}, 200
    except Exception as e:
        log.exception("API health error: %s", e)
        return {"error": str(e)}, 500


# ── High-Impact Opinions ─────────────────────────────────────────────────────

@court_intel_bp.get("/api/court-intel/high-impact")
async def api_high_impact_opinions():
    """Return the highest bail-impact opinions for dashboard surfacing.

    _qp = dict(request.query_params)
    Query params:
        min_score: minimum bail_impact score (default 50)
        limit: max results (default 20)
    """
    try:
        db = _get_db()
        min_score = int(_qp.get("min_score", 50))
        limit = min(int(_qp.get("limit", 20)), 50)

        cursor = db.court_outcomes.find(
            {"bail_impact.score": {"$gte": min_score}},
            {"snippet": 0},
        ).sort("bail_impact.score", -1).limit(limit)

        results = await cursor.to_list(length=limit)
        for r in results:
            r["_id"] = str(r["_id"])

        return {
            "success": True,
            "count": len(results),
            "min_score": min_score,
            "results": results,
        }, 200
    except Exception as e:
        log.exception("High-impact query error: %s", e)
        return {"error": str(e)}, 500
