"""
Legal NLP API + URL Ingestion — ShamrockLeads Dashboard
========================================================
Blueprint: /api/legal-nlp/ + /api/bonds/ingest-url
Endpoints for charge analysis, citation extraction, risk scoring,
and URL-based arrest data ingestion.
"""
import logging
from quart import Blueprint, jsonify, request
from dashboard.extensions import get_collection

log = logging.getLogger("shamrock.api.legal_nlp")
legal_nlp_bp = Blueprint("legal_nlp", __name__)


def _get_db():
    from quart import current_app
    return current_app.config.get("db") or current_app.db


# ── URL Ingestion (for Record Bond modal) ────────────────────────────────────

@legal_nlp_bp.route("/bonds/ingest-url", methods=["POST"])
async def api_ingest_url():
    """Fetch a booking URL and return structured arrest data.

    Body: { "url": "https://..." }
    Returns: { success, data: {full_name, booking_number, charges, ...} }
    """
    try:
        body = await request.get_json(silent=True) or {}
        url = body.get("url", "").strip()
        if not url:
            return jsonify({"success": False, "error": "URL required"}), 400

        from dashboard.services.url_ingest_service import ingest_url
        result = await ingest_url(url)
        status = 200 if result.get("success") else 422
        return jsonify(result), status
    except Exception as e:
        log.exception("URL ingest error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


# ── Charge Analysis ──────────────────────────────────────────────────────────

@legal_nlp_bp.route("/legal-nlp/analyze-charges", methods=["POST"])
async def api_analyze_charges():
    """Analyze charge text for severity, risk, and FL statute references.

    Body: { "charges": "Battery DV; Grand Theft F.S. 812.014" }
    """
    try:
        body = await request.get_json(silent=True) or {}
        charges = body.get("charges", "")
        if not charges:
            return jsonify({"error": "charges text required"}), 400

        from dashboard.services.legal_nlp_service import analyze_charges
        result = analyze_charges(charges)
        return jsonify({"success": True, **result}), 200
    except Exception as e:
        log.exception("Charge analysis error: %s", e)
        return jsonify({"error": str(e)}), 500


# ── Citation Extraction ──────────────────────────────────────────────────────

@legal_nlp_bp.route("/legal-nlp/extract-citations", methods=["POST"])
async def api_extract_citations():
    """Extract legal citations from text.

    Body: { "text": "See State v. Smith, 123 So. 2d 456..." }
    """
    try:
        body = await request.get_json(silent=True) or {}
        text = body.get("text", "")
        if not text:
            return jsonify({"error": "text required"}), 400

        from dashboard.services.legal_nlp_service import extract_citations
        citations = extract_citations(text)
        return jsonify({"success": True, "citations": citations, "count": len(citations)}), 200
    except Exception as e:
        log.exception("Citation extraction error: %s", e)
        return jsonify({"error": str(e)}), 500


# ── Entity Extraction ────────────────────────────────────────────────────────

@legal_nlp_bp.route("/legal-nlp/extract-entities", methods=["POST"])
async def api_extract_entities():
    """Extract legal entities (judges, courts, attorneys, statutes).

    Body: { "text": "Judge Williams of the 20th Circuit Court..." }
    """
    try:
        body = await request.get_json(silent=True) or {}
        text = body.get("text", "")
        if not text:
            return jsonify({"error": "text required"}), 400

        from dashboard.services.legal_nlp_service import extract_legal_entities
        entities = extract_legal_entities(text)
        return jsonify({"success": True, **entities}), 200
    except Exception as e:
        log.exception("Entity extraction error: %s", e)
        return jsonify({"error": str(e)}), 500


# ── Recidivism / FTA Risk Scoring ────────────────────────────────────────────

@legal_nlp_bp.route("/legal-nlp/risk-score/<booking_number>", methods=["GET"])
async def api_risk_score(booking_number: str):
    """Compute recidivism + FTA risk for a defendant based on arrest history.

    Looks up all arrests for the same defendant and scores risk.
    """
    try:
        db = _get_db()
        arrests_col = get_collection("arrests")

        # Find the current arrest
        current = await arrests_col.find_one(
            {"booking_number": booking_number}, {"_id": 0}
        )
        if not current:
            return jsonify({"error": f"No arrest found: {booking_number}"}), 404

        # Find all other arrests for same defendant (by name match)
        full_name = current.get("full_name", "")
        if not full_name:
            return jsonify({"error": "No name on arrest record"}), 422

        history_cursor = arrests_col.find(
            {"full_name": {"$regex": f"^{full_name}$", "$options": "i"},
             "booking_number": {"$ne": booking_number}},
            {"_id": 0}
        ).sort("scraped_at", -1).limit(20)
        history = await history_cursor.to_list(length=20)

        from dashboard.services.legal_nlp_service import compute_recidivism_risk
        result = compute_recidivism_risk(
            history, current.get("charges", "")
        )
        result["defendant_name"] = full_name
        result["booking_number"] = booking_number
        result["county"] = current.get("county", "")

        return jsonify({"success": True, **result}), 200
    except Exception as e:
        log.exception("Risk score error: %s", e)
        return jsonify({"error": str(e)}), 500


# ── Batch Risk Scoring ───────────────────────────────────────────────────────

@legal_nlp_bp.route("/legal-nlp/batch-risk", methods=["POST"])
async def api_batch_risk():
    """Score risk for multiple booking numbers.

    Body: { "booking_numbers": ["BK001", "BK002", ...] }
    """
    try:
        body = await request.get_json(silent=True) or {}
        bks = body.get("booking_numbers", [])
        if not bks:
            return jsonify({"error": "booking_numbers required"}), 400

        arrests_col = get_collection("arrests")
        from dashboard.services.legal_nlp_service import compute_recidivism_risk

        results = []
        for bk in bks[:50]:  # Cap at 50
            current = await arrests_col.find_one(
                {"booking_number": bk}, {"_id": 0}
            )
            if not current:
                continue
            name = current.get("full_name", "")
            if not name:
                continue
            hist_cursor = arrests_col.find(
                {"full_name": {"$regex": f"^{name}$", "$options": "i"},
                 "booking_number": {"$ne": bk}},
                {"_id": 0}
            ).sort("scraped_at", -1).limit(10)
            history = await hist_cursor.to_list(length=10)
            score = compute_recidivism_risk(history, current.get("charges", ""))
            score["booking_number"] = bk
            score["defendant_name"] = name
            results.append(score)

        return jsonify({"success": True, "scored": len(results), "results": results}), 200
    except Exception as e:
        log.exception("Batch risk error: %s", e)
        return jsonify({"error": str(e)}), 500


# ── Charge Enrichment (enrich an arrest record inline) ────────────────────────

@legal_nlp_bp.route("/legal-nlp/enrich/<booking_number>", methods=["POST"])
async def api_enrich_arrest(booking_number: str):
    """Enrich an arrest record with NLP-derived fields and persist."""
    try:
        arrests_col = get_collection("arrests")
        doc = await arrests_col.find_one({"booking_number": booking_number})
        if not doc:
            return jsonify({"error": "Arrest not found"}), 404

        from dashboard.services.legal_nlp_service import analyze_charges, extract_citations
        charges = doc.get("charges", "")
        analysis = analyze_charges(charges)
        citations = extract_citations(charges)

        enrichment = {
            "nlp_severity": analysis["max_severity"],
            "nlp_severity_level": analysis["severity_level"],
            "nlp_fta_risk": analysis["fta_risk_score"],
            "nlp_statutes": analysis["statutes"],
            "nlp_citations": citations,
            "nlp_risk_factors": analysis["risk_factors"],
            "nlp_enriched_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        }

        await arrests_col.update_one(
            {"_id": doc["_id"]},
            {"$set": enrichment}
        )

        return jsonify({"success": True, "enrichment": enrichment}), 200
    except Exception as e:
        log.exception("Enrich error: %s", e)
        return jsonify({"error": str(e)}), 500


# ── NLP Intelligence Dashboard Stats ─────────────────────────────────────────

@legal_nlp_bp.route("/legal-nlp/stats", methods=["GET"])
async def api_nlp_stats():
    """Aggregate NLP enrichment statistics for the Intelligence dashboard.

    Returns: severity distribution, enrichment coverage, top statutes,
    high-FTA records, risk factor frequencies, and charge pattern breakdown.
    """
    try:
        arrests_col = get_collection("arrests")

        # Total arrest count
        total_arrests = await arrests_col.count_documents({})
        enriched_count = await arrests_col.count_documents({"nlp_enriched_at": {"$exists": True}})
        unenriched_count = total_arrests - enriched_count

        # Severity distribution pipeline
        severity_pipeline = [
            {"$match": {"nlp_severity": {"$exists": True}}},
            {"$group": {"_id": "$nlp_severity", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        severity_dist = {}
        async for doc in arrests_col.aggregate(severity_pipeline):
            severity_dist[doc["_id"]] = doc["count"]

        # FTA risk distribution (bucket into tiers)
        fta_pipeline = [
            {"$match": {"nlp_fta_risk": {"$exists": True, "$gt": 0}}},
            {"$bucket": {
                "groupBy": "$nlp_fta_risk",
                "boundaries": [0, 0.1, 0.15, 0.20, 0.25, 0.50, 1.0],
                "default": "other",
                "output": {"count": {"$sum": 1}}
            }},
        ]
        fta_dist = []
        try:
            async for doc in arrests_col.aggregate(fta_pipeline):
                fta_dist.append({"bucket": str(doc["_id"]), "count": doc["count"]})
        except Exception:
            pass  # $bucket may fail on some field types

        # Top 10 highest FTA risk records
        high_fta_cursor = arrests_col.find(
            {"nlp_fta_risk": {"$exists": True, "$gt": 0.15}},
            {"_id": 0, "full_name": 1, "booking_number": 1, "county": 1,
             "charges": 1, "nlp_fta_risk": 1, "nlp_severity": 1,
             "nlp_severity_level": 1, "nlp_risk_factors": 1}
        ).sort("nlp_fta_risk", -1).limit(10)
        high_fta_records = await high_fta_cursor.to_list(length=10)

        # Top statutes across all enriched records
        statute_pipeline = [
            {"$match": {"nlp_statutes": {"$exists": True, "$ne": []}}},
            {"$unwind": "$nlp_statutes"},
            {"$group": {"_id": "$nlp_statutes.full", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 15},
        ]
        top_statutes = []
        async for doc in arrests_col.aggregate(statute_pipeline):
            top_statutes.append({"statute": doc["_id"], "count": doc["count"]})

        # Risk factor frequency
        rf_pipeline = [
            {"$match": {"nlp_risk_factors": {"$exists": True, "$ne": []}}},
            {"$unwind": "$nlp_risk_factors"},
            {"$group": {"_id": "$nlp_risk_factors.keyword", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]
        risk_factor_freq = []
        async for doc in arrests_col.aggregate(rf_pipeline):
            risk_factor_freq.append({"factor": doc["_id"], "count": doc["count"]})

        # Average severity level
        avg_pipeline = [
            {"$match": {"nlp_severity_level": {"$exists": True, "$gt": 0}}},
            {"$group": {"_id": None, "avg_severity": {"$avg": "$nlp_severity_level"},
                        "max_severity": {"$max": "$nlp_severity_level"}}},
        ]
        avg_severity = 0
        max_sev_level = 0
        async for doc in arrests_col.aggregate(avg_pipeline):
            avg_severity = round(doc.get("avg_severity", 0), 2)
            max_sev_level = doc.get("max_severity", 0)

        # Enrichment rate by county
        county_pipeline = [
            {"$group": {
                "_id": "$county",
                "total": {"$sum": 1},
                "enriched": {"$sum": {"$cond": [{"$ifNull": ["$nlp_enriched_at", False]}, 1, 0]}},
            }},
            {"$sort": {"total": -1}},
            {"$limit": 20},
        ]
        county_coverage = []
        async for doc in arrests_col.aggregate(county_pipeline):
            county_coverage.append({
                "county": doc["_id"],
                "total": doc["total"],
                "enriched": doc["enriched"],
                "coverage_pct": round(doc["enriched"] / doc["total"] * 100, 1) if doc["total"] > 0 else 0,
            })

        return jsonify({
            "success": True,
            "total_arrests": total_arrests,
            "enriched_count": enriched_count,
            "unenriched_count": unenriched_count,
            "coverage_pct": round(enriched_count / total_arrests * 100, 1) if total_arrests > 0 else 0,
            "severity_distribution": severity_dist,
            "fta_distribution": fta_dist,
            "high_fta_records": high_fta_records,
            "top_statutes": top_statutes,
            "risk_factor_frequency": risk_factor_freq,
            "avg_severity_level": avg_severity,
            "max_severity_level": max_sev_level,
            "county_coverage": county_coverage,
        }), 200
    except Exception as e:
        log.exception("NLP stats error: %s", e)
        return jsonify({"error": str(e)}), 500
