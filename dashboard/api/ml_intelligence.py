"""
ShamrockLeads — ML Intelligence API Blueprint
===============================================
REST endpoints for the ML prediction engine.

Endpoints:
  POST  /api/ml/train                      — Trigger model training
  GET   /api/ml/predict/<booking_number>    — Get ML prediction for a lead
  GET   /api/ml/model-status               — Status of all trained models
  POST  /api/ml/batch-predict               — Batch predictions for multiple leads
  GET   /api/ml/feature-importance          — Feature importance analysis
  GET   /api/ml/predictions/compare/<bn>    — Rule-based vs ML score comparison

All routes are async (Quart) with Motor (async MongoDB).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from quart import Blueprint, jsonify, request

from dashboard.extensions import get_db

logger = logging.getLogger(__name__)

ml_bp = Blueprint("ml_intelligence", __name__)


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/ml/train — Trigger model training
# ─────────────────────────────────────────────────────────────────────────────
@ml_bp.route("/ml/train", methods=["POST"])
async def api_train_model():
    """Train an ML model on historical data.

    Body (optional):
        {
            "target": "lead_quality" | "fta_risk",
            "algorithm": "random_forest" | "xgboost",
            "limit": 50000
        }
    """
    try:
        from scoring.model_trainer import train_model

        data = await request.get_json(silent=True) or {}
        target = data.get("target", "lead_quality")
        algorithm = data.get("algorithm", "random_forest")
        limit = int(data.get("limit", 50000))

        if target not in ("lead_quality", "fta_risk"):
            return jsonify({"success": False, "error": "target must be 'lead_quality' or 'fta_risk'"}), 400
        if algorithm not in ("random_forest", "xgboost", "ensemble"):
            return jsonify({"success": False, "error": "algorithm must be 'random_forest', 'xgboost', or 'ensemble'"}), 400

        db = get_db()
        result = await train_model(db, target=target, algorithm=algorithm, limit=limit)

        if result.get("success"):
            logger.info("🧠 ML model trained: %s/%s — F1=%.3f",
                       target, algorithm, result.get("metrics", {}).get("f1_score", 0))
        else:
            logger.warning("⚠️ ML training failed: %s", result.get("error"))

        return jsonify(result)

    except Exception as e:
        logger.exception("ML training error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/ml/predict/<booking_number> — ML prediction for a lead
# ─────────────────────────────────────────────────────────────────────────────
@ml_bp.route("/ml/predict/<booking_number>")
async def api_predict(booking_number: str):
    """Get ML prediction for a specific arrest record.

    Query params:
        target: "lead_quality" (default) | "fta_risk"
        algorithm: "random_forest" (default) | "xgboost"
    """
    try:
        from scoring.model_trainer import predict

        target = request.args.get("target", "lead_quality")
        algorithm = request.args.get("algorithm", "random_forest")

        db = get_db()
        arrest = await db["arrests"].find_one({"booking_number": booking_number})
        if not arrest:
            return jsonify({"success": False, "error": "Arrest not found"}), 404

        # Build enrichment from DB
        enrichment = await _build_enrichment(db, arrest)

        result = predict(arrest, target=target, algorithm=algorithm, enrichment=enrichment)
        if result is None:
            return jsonify({
                "success": False,
                "error": f"No trained model found for {target}/{algorithm}. POST /api/ml/train first."
            }), 404

        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "defendant_name": arrest.get("full_name", ""),
            "county": arrest.get("county", ""),
            "target": target,
            "prediction": result,
            # Include the rule-based score for comparison
            "rule_based_score": arrest.get("lead_score"),
            "rule_based_status": arrest.get("lead_status"),
        })

    except Exception as e:
        logger.exception("ML predict error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/ml/batch-predict — Batch predictions
# ─────────────────────────────────────────────────────────────────────────────
@ml_bp.route("/ml/batch-predict", methods=["POST"])
async def api_batch_predict():
    """Batch ML predictions for multiple arrest records.

    Body:
        {
            "booking_numbers": ["2024-001", "2024-002", ...],
            "target": "lead_quality",
            "algorithm": "random_forest"
        }
    """
    try:
        from scoring.model_trainer import predict

        data = await request.get_json(silent=True) or {}
        booking_numbers = data.get("booking_numbers", [])
        target = data.get("target", "lead_quality")
        algorithm = data.get("algorithm", "random_forest")

        if not booking_numbers:
            return jsonify({"success": False, "error": "booking_numbers required"}), 400

        db = get_db()
        results = []

        for bn in booking_numbers[:100]:  # Cap at 100
            arrest = await db["arrests"].find_one({"booking_number": bn})
            if not arrest:
                results.append({"booking_number": bn, "error": "not_found"})
                continue

            enrichment = await _build_enrichment(db, arrest)
            result = predict(arrest, target=target, algorithm=algorithm, enrichment=enrichment)

            if result:
                results.append({
                    "booking_number": bn,
                    "defendant_name": arrest.get("full_name", ""),
                    "county": arrest.get("county", ""),
                    "ml_score": result["ml_score"],
                    "prediction": result["prediction"],
                    "confidence": result["confidence"],
                    "rule_based_score": arrest.get("lead_score"),
                })
            else:
                results.append({"booking_number": bn, "error": "no_model"})

        return jsonify({
            "success": True,
            "count": len(results),
            "target": target,
            "predictions": results,
        })

    except Exception as e:
        logger.exception("Batch predict error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/ml/model-status — All model status
# ─────────────────────────────────────────────────────────────────────────────
@ml_bp.route("/ml/model-status")
async def api_model_status():
    """Get status and metrics of all trained ML models."""
    try:
        from scoring.model_trainer import get_all_model_status
        status = get_all_model_status()
        return jsonify({"success": True, **status})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/ml/feature-importance — Feature importance analysis
# ─────────────────────────────────────────────────────────────────────────────
@ml_bp.route("/ml/feature-importance")
async def api_feature_importance():
    """Get feature importance rankings for a trained model.

    Query params:
        target: "lead_quality" (default) | "fta_risk"
        algorithm: "random_forest" (default) | "xgboost"
    """
    try:
        from scoring.model_trainer import load_model

        target = request.args.get("target", "lead_quality")
        algorithm = request.args.get("algorithm", "random_forest")

        _, metadata = load_model(target, algorithm)
        if not metadata:
            return jsonify({
                "success": False,
                "error": f"No model found for {target}/{algorithm}"
            }), 404

        importance = metadata.get("feature_importance", [])
        return jsonify({
            "success": True,
            "target": target,
            "algorithm": algorithm,
            "trained_at": metadata.get("trained_at"),
            "training_samples": metadata.get("training_samples"),
            "feature_importance": [
                {"feature": name, "importance": round(imp * 100, 2)}
                for name, imp in importance
            ],
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/ml/predictions/compare/<bn> — Rule-based vs ML comparison
# ─────────────────────────────────────────────────────────────────────────────
@ml_bp.route("/ml/predictions/compare/<booking_number>")
async def api_compare_predictions(booking_number: str):
    """Compare rule-based and ML predictions side-by-side.

    Shows exactly where the models agree and disagree, helping calibrate
    the ML model against the existing rule-based system.
    """
    try:
        from scoring.model_trainer import predict
        from dashboard.api.lead_intelligence import build_score_explanation, classify_charge

        db = get_db()
        arrest = await db["arrests"].find_one({"booking_number": booking_number})
        if not arrest:
            return jsonify({"success": False, "error": "Not found"}), 404

        enrichment = await _build_enrichment(db, arrest)

        # Rule-based
        rule_factors = build_score_explanation(arrest)
        rule_score = sum(f["points"] for f in rule_factors)
        rule_status = arrest.get("lead_status", "Unknown")

        # ML predictions
        ml_lead = predict(arrest, target="lead_quality", enrichment=enrichment)
        ml_fta = predict(arrest, target="fta_risk", enrichment=enrichment)

        # Compute agreement
        agreement = "N/A"
        if ml_lead:
            rule_positive = rule_score >= 40  # Warm+Hot
            ml_positive = ml_lead["probability"] >= 0.5
            agreement = "agree" if rule_positive == ml_positive else "disagree"

        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "defendant_name": arrest.get("full_name", ""),
            "comparison": {
                "rule_based": {
                    "score": rule_score,
                    "status": rule_status,
                    "factors": rule_factors,
                },
                "ml_lead_quality": ml_lead,
                "ml_fta_risk": ml_fta,
                "agreement": agreement,
            },
        })

    except Exception as e:
        logger.exception("Compare predictions error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  Enrichment Builder (shared)
# ─────────────────────────────────────────────────────────────────────────────

async def _build_enrichment(db, arrest: dict) -> dict:
    """Build enrichment features from MongoDB for a single arrest."""
    full_name = (arrest.get("full_name") or "").lower().strip()
    booking_number = arrest.get("booking_number", "")

    enrichment = {
        "prior_arrest_count": 0,
        "has_active_bond": False,
        "prior_fta_count": 0,
        "days_since_last_arrest": 9999,
        "prior_bond_total": 0,
    }

    if not full_name:
        return enrichment

    try:
        # Prior arrests for same name
        prior_count = await db["arrests"].count_documents({
            "full_name": {"$regex": f"^{full_name}$", "$options": "i"},
            "booking_number": {"$ne": booking_number},
        })
        enrichment["prior_arrest_count"] = prior_count

        # Active bond check
        active_bond = await db["active_bonds"].find_one({
            "defendant_name": {"$regex": full_name, "$options": "i"},
            "status": {"$in": ["active", "monitoring"]},
        })
        enrichment["has_active_bond"] = active_bond is not None

        # Prior bond total
        if active_bond:
            enrichment["prior_bond_total"] = float(active_bond.get("bond_amount", 0) or 0)

        # Re-arrest history
        rearrest_count = await db["rearrest_alerts"].count_documents({
            "defendant_name": {"$regex": full_name, "$options": "i"},
        })
        enrichment["prior_fta_count"] = rearrest_count

    except Exception as e:
        logger.debug("Enrichment lookup error: %s", e)

    return enrichment
