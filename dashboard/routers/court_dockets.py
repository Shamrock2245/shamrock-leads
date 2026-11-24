
"""
Court Intelligence API — ShamrockLeads Dashboard
=================================================

Blueprint: /api/court-intel/
Endpoints for court opinion ingestion, disposition analytics,
and SE US coverage metrics.
"""

import logging
import os
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from dashboard.extensions import get_db

log = logging.getLogger("shamrock.api.court_intel")
court_intel_bp = APIRouter(prefix="/api", tags=["court_intel"])


def _make_cl_client():
    from dashboard.services.courtlistener_client import CourtListenerClient
    from dashboard.services.federal_bond_risk import (
        MAX_REQUESTS_PER_MINUTE,
        build_courtlistener_request_gate,
    )

    token = os.getenv("COURTLISTENER_API_TOKEN", "")
    client = CourtListenerClient(api_token=token if token else None)
    try:
        db = get_db()
    except Exception:
        db = None
    client.set_request_gate(
        build_courtlistener_request_gate(
            db,
            client=client,
            pause_s=0.0,
            run_limit=MAX_REQUESTS_PER_MINUTE,
            block_on_minute_limit=True,
        )
    )
    return client


# ── Coverage & Status ────────────────────────────────────────────────────────

@court_intel_bp.get("/court-intel/coverage")
async def api_court_intel_coverage():
    """Return multi-state court coverage registry + search presets."""
    try:
        from dashboard.services.courtlistener_client import BAIL_SEARCH_PRESETS

        client = _make_cl_client()
        summary = client.get_coverage_summary()
        summary["presets"] = [{"key": k, **v} for k, v in BAIL_SEARCH_PRESETS.items()]
        return JSONResponse(summary, status_code=200)
    except Exception as e:
        log.exception("Coverage error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@court_intel_bp.get("/court-intel/stats")
async def api_court_intel_stats():
    """Return ingestion statistics from court_outcomes collection."""
    try:
        from dashboard.services.court_data_ingestor import get_ingestion_stats
        db = get_db()
        stats = await get_ingestion_stats(db)
        return JSONResponse(stats, status_code=200)
    except Exception as e:
        log.exception("Stats error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Ingestion Trigger ────────────────────────────────────────────────────────

@court_intel_bp.post("/court-intel/ingest")
async def api_court_intel_ingest(request: Request):
    """Trigger a court opinion ingestion cycle.

    Body (JSON):
        days_back: int (default 30)
        states: list of state codes (default all SE US)
    """
    try:
        from dashboard.services.court_data_ingestor import run_ingestion
        db = get_db()
        body = await request.json() or {}
        days_back = body.get("days_back", 30)
        states = body.get("states", None)

        result = await run_ingestion(db, days_back=days_back, states=states)
        status = 200 if result.get("success") else 500
        return JSONResponse(content=result, status_code=status)
    except Exception as e:
        log.exception("Ingestion error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Disposition Rates ────────────────────────────────────────────────────────

@court_intel_bp.get("/court-intel/disposition-rates")
async def api_disposition_rates(state: str | None = Query(default=None)):
    """Get empirical disposition rates, optionally by state.
    Query params:
        state: two-letter state code (optional)
    """
    try:
        from dashboard.services.court_data_ingestor import get_disposition_rates
        db = get_db()
        state = state
        rates = await get_disposition_rates(db, state=state)
        return JSONResponse(rates, status_code=200)
    except Exception as e:
        log.exception("Disposition rates error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Search Court Outcomes ────────────────────────────────────────────────────

@court_intel_bp.get("/court-intel/search")

async def api_court_intel_search(q: str = Query(default=""), state: str = Query(default=""), disposition: str = Query(default=""), limit: int = Query(default=25)):
    """Search court outcomes by case name, state, or disposition.
    Query params:
        q: search query (case name)
        state: state code
        disposition: disposition type
        limit: max results (default 25)
    """
    try:
        db = get_db()
        q = q
        state = state
        disposition = disposition
        limit = min(int(limit), 100)

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

        return JSONResponse(status_code=200, content={
            "success": True,
            "count": len(results),
            "results": results,
        })
    except Exception as e:
        log.exception("Search error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Defendant Court History ──────────────────────────────────────────────────

@court_intel_bp.get("/court-intel/defendant/{defendant_id}")

async def api_defendant_court_history(defendant_id: str):
    """Get court outcomes linked to a specific defendant."""
    try:
        db = get_db()
        cursor = db.court_outcomes.find(
            {"matched_defendant_id": defendant_id}
        ).sort("date_filed", -1).limit(50)

        results = await cursor.to_list(length=50)
        for r in results:
            r["_id"] = str(r["_id"])

        return JSONResponse(status_code=200, content={
            "success": True,
            "defendant_id": defendant_id,
            "count": len(results),
            "outcomes": results,
        })
    except Exception as e:
        log.exception("Defendant history error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── API Health ───────────────────────────────────────────────────────────────

@court_intel_bp.get("/court-intel/api-health")
async def api_court_intel_health():
    """Return CourtListener API health metrics and maintenance status."""
    try:
        client = _make_cl_client()
        health = client.get_api_health()
        health["token_configured"] = bool(client.api_token)
        return JSONResponse(status_code=200, content={"success": True, **health})
    except Exception as e:
        log.exception("API health error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── High-Impact Opinions ─────────────────────────────────────────────────────

@court_intel_bp.get("/court-intel/high-impact")
async def api_high_impact_opinions(min_score: int = Query(default=50), limit: int = Query(default=20)):
    """Return the highest bail-impact opinions for dashboard surfacing.
    Query params:
        min_score: minimum bail_impact score (default 50)
        limit: max results (default 20)
    """
    try:
        db = get_db()
        min_score = int(min_score)
        limit = min(int(limit), 50)

        cursor = db.court_outcomes.find(
            {"bail_impact.score": {"$gte": min_score}},
            {"snippet": 0},
        ).sort("bail_impact.score", -1).limit(limit)

        results = await cursor.to_list(length=limit)
        for r in results:
            r["_id"] = str(r["_id"])

        return JSONResponse(status_code=200, content={
            "success": True,
            "count": len(results),
            "min_score": min_score,
            "results": results,
        })
    except Exception as e:
        log.exception("High-impact query error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Analytics Aggregation ────────────────────────────────────────────────────

@court_intel_bp.get("/court-intel/analytics")
async def api_court_intel_analytics(state: str | None = Query(default=None)):
    """Return aggregated CourtListener bail-intelligence scorecards and trends."""
    try:
        from dashboard.services.court_data_ingestor import get_court_intel_analytics

        db = get_db()
        payload = await get_court_intel_analytics(db, state=state)
        return JSONResponse(status_code=200, content=payload)
    except Exception as e:
        log.exception("Analytics error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ── Live Multi-Type Explorer ─────────────────────────────────────────────────

@court_intel_bp.get("/court-intel/live-search")
async def api_court_intel_live_search(
    q: str = Query(default=""),
    preset: str = Query(default=""),
    type: str = Query(default="o"),
    state: str = Query(default=""),
    court: str = Query(default=""),
    docket_number: str = Query(default=""),
    judge: str = Query(default=""),
    filed_after: str = Query(default=""),
    filed_before: str = Query(default=""),
    limit: int = Query(default=25),
):
    """Query live CourtListener /search/ API with instant bail analytics."""
    from dashboard.services.courtlistener_client import BAIL_SEARCH_PRESETS

    resolved_q = q.strip()
    resolved_type = (type or "o").strip().lower()
    if preset and preset in BAIL_SEARCH_PRESETS:
        p = BAIL_SEARCH_PRESETS[preset]
        if not resolved_q:
            resolved_q = p["query"]
        resolved_type = p.get("type", resolved_type)

    if not any([resolved_q, state, court, docket_number, judge, filed_after]):
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "Provide a query, preset, state, court, docket number, or judge."},
        )

    client = _make_cl_client()
    try:
        courts_list = [c.strip() for c in court.split(",") if c.strip()] if court else None
        result = await client.live_search(
            query=resolved_q,
            search_type=resolved_type,
            courts=courts_list,
            state=state or None,
            date_filed_after=filed_after or None,
            date_filed_before=filed_before or None,
            docket_number=docket_number or None,
            judge=judge or None,
            page_size=min(max(int(limit), 1), 50),
        )
        status = (
            200
            if result.get("success")
            else (429 if result.get("skipped") == "quota_exhausted" else 502)
        )
        return JSONResponse(status_code=status, content=result)
    except Exception as e:
        log.exception("Live search error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    finally:
        await client.close()


# ── Docket Number Lookup ─────────────────────────────────────────────────────

@court_intel_bp.get("/court-intel/docket-lookup")
async def api_court_intel_docket_lookup(
    docket_number: str = Query(default=""),
    court: str = Query(default=""),
    state: str = Query(default=""),
):
    """Lookup CourtListener Dockets, Clusters, and Search Hits by docket number."""
    if not docket_number.strip():
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "docket_number is required"},
        )

    client = _make_cl_client()
    try:
        result = await client.lookup_by_docket_number(
            docket_number=docket_number.strip(),
            court=court.strip() or None,
            state=state.strip() or None,
        )
        status = (
            200
            if result.get("success")
            else (429 if result.get("skipped") == "quota_exhausted" else 502)
        )
        return JSONResponse(status_code=status, content=result)
    except Exception as e:
        log.exception("Docket lookup error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    finally:
        await client.close()


# ── Case Dossier (Docket -> Cluster -> Opinion) ──────────────────────────────

@court_intel_bp.get("/court-intel/case-dossier")
async def api_court_intel_case_dossier(
    cluster_id: str = Query(default=""),
    url: str = Query(default=""),
    source_id: str = Query(default=""),
):
    """Assemble full CourtListener v4 case dossier and merge any stored local analysis."""
    target = (url or cluster_id or source_id).strip()
    if not target:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "cluster_id, url, or source_id is required"},
        )

    db = get_db()
    local_doc = None
    try:
        lookup_ids = [x for x in (cluster_id.strip(), source_id.strip()) if x]
        if lookup_ids:
            local_doc = await db.court_outcomes.find_one(
                {"source": "courtlistener", "$or": [{"source_id": {"$in": lookup_ids}}, {"cluster_id": {"$in": lookup_ids}}]}
            )
    except Exception:
        local_doc = None

    client = _make_cl_client()
    try:
        parsed_id = client.parse_cluster_id_from_url(target)
        if parsed_id and not local_doc:
            try:
                local_doc = await db.court_outcomes.find_one(
                    {"source": "courtlistener", "$or": [{"source_id": str(parsed_id)}, {"cluster_id": parsed_id}]}
                )
            except Exception:
                local_doc = None

        live_res = await client.get_full_case_dossier(target)
        if live_res.get("success"):
            dossier = live_res["dossier"]
            if local_doc:
                dossier["ai_analysis"] = local_doc.get("ai_analysis")
                dossier["matched_defendant_id"] = local_doc.get("matched_defendant_id")
                dossier["matched_defendant_name"] = local_doc.get("matched_defendant_name")
                dossier["match_confidence"] = local_doc.get("match_confidence")
                dossier["saved_in_corpus"] = True
            else:
                dossier["saved_in_corpus"] = False
            return JSONResponse(status_code=200, content=live_res)

        if local_doc:
            from dashboard.services.courtlistener_client import classify_disposition_category

            fallback_dossier = {
                "cluster_id": local_doc.get("cluster_id") or local_doc.get("source_id"),
                "source_id": str(local_doc.get("source_id") or ""),
                "case_name": local_doc.get("case_name") or "Unknown Case",
                "case_name_full": local_doc.get("case_name") or "",
                "absolute_url": local_doc.get("absolute_url") or "",
                "court": {
                    "id": local_doc.get("court_id") or "",
                    "name": local_doc.get("court_name") or local_doc.get("court_id") or "",
                    "state": local_doc.get("state") or "",
                    "type": local_doc.get("court_type") or "",
                    "tier": local_doc.get("court_tier") or "",
                },
                "docket": {
                    "id": local_doc.get("docket_id"),
                    "docket_number": local_doc.get("docket_number") or "",
                    "date_filed": local_doc.get("date_filed") or "",
                    "assigned_to_str": local_doc.get("assigned_to_str") or "",
                },
                "cluster": {
                    "id": local_doc.get("cluster_id") or local_doc.get("source_id"),
                    "date_filed": local_doc.get("date_filed") or "",
                    "judges": local_doc.get("judges") or "",
                    "citation_count": local_doc.get("citation_count") or 0,
                    "citations": local_doc.get("citation") or [],
                },
                "opinion": {
                    "text": local_doc.get("snippet") or "",
                    "text_truncated": False,
                },
                "disposition": local_doc.get("disposition") or "unknown",
                "disposition_category": classify_disposition_category(local_doc.get("disposition", "")),
                "bail_impact": local_doc.get("bail_impact") or {"score": 0, "factors": []},
                "ai_analysis": local_doc.get("ai_analysis"),
                "matched_defendant_id": local_doc.get("matched_defendant_id"),
                "matched_defendant_name": local_doc.get("matched_defendant_name"),
                "match_confidence": local_doc.get("match_confidence"),
                "saved_in_corpus": True,
            }
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "offline_fallback": True,
                    "warning": live_res.get("error"),
                    "dossier": fallback_dossier,
                },
            )

        return JSONResponse(
            status_code=(
                429
                if live_res.get("skipped") == "quota_exhausted"
                else (400 if "Invalid" in str(live_res.get("error", "")) else 502)
            ),
            content=live_res,
        )
    except Exception as e:
        log.exception("Case dossier error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    finally:
        await client.close()


# ── Save Case to Local Corpus ────────────────────────────────────────────────

@court_intel_bp.post("/court-intel/save-case")
async def api_court_intel_save_case(request: Request):
    """Save a CourtListener case/opinion result into local `court_outcomes`."""
    try:
        from dashboard.services.court_data_ingestor import save_court_case

        body = await request.json() or {}
        if not isinstance(body, dict):
            return JSONResponse({"success": False, "error": "JSON object expected"}, status_code=400)

        db = get_db()
        result = await save_court_case(db, body)
        status = 200 if result.get("success") else 400
        return JSONResponse(status_code=status, content=result)
    except Exception as e:
        log.exception("Save case error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
