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
