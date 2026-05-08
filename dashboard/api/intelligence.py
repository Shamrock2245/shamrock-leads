"""
ShamrockLeads — Intelligence API Blueprint
============================================
Advanced analytics endpoints for the AI Intelligence Dashboard.

Endpoints:
  GET  /api/intelligence/forecast            — Revenue forecast (ES + Monte Carlo)
  GET  /api/intelligence/heatmap/counties     — County risk heatmap
  GET  /api/intelligence/heatmap/temporal     — Temporal arrest pattern heatmap
  GET  /api/intelligence/heatmap/charges      — Charge category by county
  GET  /api/intelligence/risk-trend           — Risk trend over time
  GET  /api/intelligence/dashboard            — Combined intelligence dashboard data
  GET  /api/intelligence/court-predictor      — Court outcome prediction (LLM-powered)

All routes are async (Quart) with Motor (async MongoDB).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict

from quart import Blueprint, jsonify, request

from dashboard.extensions import get_db

logger = logging.getLogger(__name__)

intelligence_bp = Blueprint("intelligence", __name__)


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/intelligence/forecast — Revenue Forecast
# ─────────────────────────────────────────────────────────────────────────────
@intelligence_bp.route("/intelligence/forecast")
async def api_forecast():
    """Revenue forecast using exponential smoothing + Monte Carlo.

    Query params:
        history: Days of historical data (default: 90)
        horizon: Forecast horizon in days (default: 30)
    """
    try:
        from dashboard.services.revenue_forecaster import generate_full_forecast

        history = int(request.args.get("history", 90))
        horizon = int(request.args.get("horizon", 30))

        db = get_db()
        forecast = await generate_full_forecast(db, days_history=history, horizon=horizon)

        return jsonify({"success": True, **forecast})

    except Exception as e:
        logger.exception("Forecast error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/intelligence/heatmap/counties — County Risk Heatmap
# ─────────────────────────────────────────────────────────────────────────────
@intelligence_bp.route("/intelligence/heatmap/counties")
async def api_county_heatmap():
    """County-level risk heatmap with composite scoring.

    Query params:
        days: Period to analyze (default: 30)
    """
    try:
        from dashboard.services.risk_heatmap import get_county_risk_heatmap

        days = int(request.args.get("days", 30))
        db = get_db()
        data = await get_county_risk_heatmap(db, days=days)

        return jsonify({"success": True, **data})

    except Exception as e:
        logger.exception("County heatmap error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/intelligence/heatmap/temporal — Temporal Heatmap
# ─────────────────────────────────────────────────────────────────────────────
@intelligence_bp.route("/intelligence/heatmap/temporal")
async def api_temporal_heatmap():
    """Hour × Day-of-week arrest pattern heatmap.

    Query params:
        days: Period to analyze (default: 30)
        county: Optional county filter
    """
    try:
        from dashboard.services.risk_heatmap import get_temporal_heatmap

        days = int(request.args.get("days", 30))
        county = request.args.get("county")

        db = get_db()
        data = await get_temporal_heatmap(db, days=days, county=county)

        return jsonify({"success": True, **data})

    except Exception as e:
        logger.exception("Temporal heatmap error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/intelligence/heatmap/charges — Charge Category Heatmap
# ─────────────────────────────────────────────────────────────────────────────
@intelligence_bp.route("/intelligence/heatmap/charges")
async def api_charge_heatmap():
    """County × Charge-category heatmap matrix.

    Query params:
        days: Period to analyze (default: 30)
    """
    try:
        from dashboard.services.risk_heatmap import get_charge_category_heatmap

        days = int(request.args.get("days", 30))
        db = get_db()
        data = await get_charge_category_heatmap(db, days=days)

        return jsonify({"success": True, **data})

    except Exception as e:
        logger.exception("Charge heatmap error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/intelligence/risk-trend — Risk Trend Over Time
# ─────────────────────────────────────────────────────────────────────────────
@intelligence_bp.route("/intelligence/risk-trend")
async def api_risk_trend():
    """Daily risk trend with 7-day moving averages.

    Query params:
        county: Optional county filter
        days: Period to analyze (default: 90)
    """
    try:
        from dashboard.services.risk_heatmap import get_risk_trend

        days = int(request.args.get("days", 90))
        county = request.args.get("county")

        db = get_db()
        data = await get_risk_trend(db, county=county, days=days)

        return jsonify({"success": True, **data})

    except Exception as e:
        logger.exception("Risk trend error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/intelligence/dashboard — Combined Intelligence Dashboard
# ─────────────────────────────────────────────────────────────────────────────
@intelligence_bp.route("/intelligence/dashboard")
async def api_intelligence_dashboard():
    """Aggregated intelligence dashboard — all key data in one call.

    Returns: forecast summary, top counties, temporal patterns, model status.
    """
    try:
        from dashboard.services.revenue_forecaster import generate_full_forecast
        from dashboard.services.risk_heatmap import (
            get_county_risk_heatmap, get_temporal_heatmap,
        )
        from scoring.model_trainer import get_all_model_status

        db = get_db()

        # Fetch all intelligence data in parallel-ish (serial for simplicity)
        forecast = await generate_full_forecast(db, days_history=60, horizon=30)
        county_risk = await get_county_risk_heatmap(db, days=30)
        temporal = await get_temporal_heatmap(db, days=30)
        model_status = get_all_model_status()

        # Build summary
        top_counties = county_risk.get("counties", [])[:10]
        critical_counties = [c for c in top_counties if c.get("risk_level") == "critical"]

        return jsonify({
            "success": True,
            "forecast_summary": forecast.get("summary", {}),
            "top_counties": top_counties,
            "critical_count": len(critical_counties),
            "temporal_peak": {
                "hour": temporal.get("peak_hour"),
                "day": temporal.get("peak_day"),
            },
            "total_arrests_30d": temporal.get("total_arrests", 0),
            "ml_models": model_status,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })

    except Exception as e:
        logger.exception("Intelligence dashboard error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/intelligence/court-predictor — Court Outcome Prediction
# ─────────────────────────────────────────────────────────────────────────────
@intelligence_bp.route("/intelligence/court-predictor/<booking_number>")
async def api_court_predictor(booking_number: str):
    """Predict court outcome (conviction/FTA odds) for a defendant.

    Uses charge analysis + ML model + optional LLM enrichment.
    Returns probability estimates and contributing factors.
    """
    try:
        from scoring.model_trainer import predict
        from scoring.feature_engineering import extract_features

        db = get_db()
        arrest = await db["arrests"].find_one({"booking_number": booking_number})
        if not arrest:
            return jsonify({"success": False, "error": "Arrest not found"}), 404

        # Extract features for analysis
        features = extract_features(arrest)

        # ML-based FTA risk
        fta_prediction = predict(arrest, target="fta_risk")

        # Charge-based risk assessment (heuristic)
        charges = (arrest.get("charges") or "").lower()
        charge_risk = _assess_charge_risk(charges, features)

        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "defendant_name": arrest.get("full_name", ""),
            "county": arrest.get("county", ""),
            "court_prediction": {
                "fta_probability": fta_prediction.get("probability", 0) if fta_prediction else None,
                "fta_confidence": fta_prediction.get("confidence", "unknown") if fta_prediction else "no_model",
                "charge_risk_assessment": charge_risk,
                "features": {
                    "bond_amount": features.get("bond_amount_raw", 0),
                    "charge_severity": features.get("charge_severity_max", 0),
                    "felony_degree": features.get("felony_degree", 0),
                    "charge_count": features.get("charge_count", 0),
                    "in_custody": bool(features.get("in_custody", 0)),
                    "age": features.get("age_at_arrest", 0),
                    "prior_arrests": features.get("prior_arrest_count", 0),
                },
            },
        })

    except Exception as e:
        logger.exception("Court predictor error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


def _assess_charge_risk(charges: str, features: dict) -> Dict:
    """Heuristic charge-based risk assessment."""
    severity = features.get("charge_severity_max", 0)
    felony_degree = features.get("felony_degree", 0)

    risk_factors = []
    overall_risk = "low"

    if features.get("has_violence_charge"):
        risk_factors.append({"factor": "Violence charges present", "impact": "high"})
    if features.get("has_flight_risk_charge"):
        risk_factors.append({"factor": "Flight risk indicators", "impact": "high"})
    if features.get("has_capital_charge"):
        risk_factors.append({"factor": "Capital offense", "impact": "critical"})
        overall_risk = "critical"
    if felony_degree >= 4:
        risk_factors.append({"factor": f"High felony degree ({felony_degree})", "impact": "high"})
    if features.get("has_drug_charge"):
        risk_factors.append({"factor": "Drug charges present", "impact": "medium"})
    if features.get("charge_count", 0) > 3:
        risk_factors.append({"factor": f"Multiple charges ({features['charge_count']})", "impact": "medium"})
    if features.get("prior_arrest_count", 0) > 2:
        risk_factors.append({"factor": "Multiple prior arrests", "impact": "high"})

    if not risk_factors:
        risk_factors.append({"factor": "No elevated risk indicators", "impact": "none"})

    if overall_risk != "critical":
        high_count = sum(1 for f in risk_factors if f["impact"] == "high")
        if high_count >= 2:
            overall_risk = "high"
        elif high_count == 1:
            overall_risk = "medium"

    return {
        "overall_risk": overall_risk,
        "factors": risk_factors,
        "severity_score": severity,
        "felony_degree": felony_degree,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/intelligence/court-prediction — Batch Court Outcome Prediction
# ─────────────────────────────────────────────────────────────────────────────
@intelligence_bp.route("/intelligence/court-prediction")
async def api_court_prediction_batch():
    """Predict court outcomes for recent high-value arrests.

    Query params:
        limit: Number of records to score (default: 20)
        min_bond: Minimum bond amount filter (default: 5000)
    """
    try:
        from dashboard.services.court_outcome_predictor import predict_outcome

        db = get_db()
        limit = int(request.args.get("limit", 20))
        min_bond = float(request.args.get("min_bond", 5000))

        cursor = db.arrests.find(
            {"bond_amount": {"$gte": min_bond}, "lead_status": {"$in": ["Hot", "Warm"]}},
            {"charges": 1, "bond_amount": 1, "county": 1, "full_name": 1,
             "booking_number": 1, "Defendant_Name": 1, "Charges": 1,
             "Bond_Amount": 1, "County": 1}
        ).sort("bond_amount", -1).limit(limit)
        records = await cursor.to_list(length=limit)

        results = []
        for rec in records:
            pred = predict_outcome(rec)
            pred["defendant_name"] = rec.get("full_name") or rec.get("Defendant_Name", "Unknown")
            pred["booking_number"] = rec.get("booking_number", "")
            pred["bond_amount"] = float(rec.get("bond_amount", 0) or rec.get("Bond_Amount", 0) or 0)
            results.append(pred)

        # Sort by FTA probability (highest risk first)
        results.sort(key=lambda x: x["fta_probability"], reverse=True)

        return jsonify({
            "success": True,
            "predictions": results,
            "total": len(results),
            "avg_fta": round(sum(r["fta_probability"] for r in results) / max(len(results), 1), 3),
        })

    except Exception as e:
        logger.exception("Court prediction batch error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/intelligence/forfeiture-risk — Portfolio Forfeiture Risk
# ─────────────────────────────────────────────────────────────────────────────
@intelligence_bp.route("/intelligence/forfeiture-risk")
async def api_forfeiture_risk():
    """Score all active bonds for forfeiture probability.

    Query params:
        limit: Max bonds to score (default: 50)
    """
    try:
        from dashboard.services.forfeiture_predictor import score_portfolio

        db = get_db()
        limit = int(request.args.get("limit", 50))
        data = await score_portfolio(db, limit=limit)

        return jsonify(data)

    except Exception as e:
        logger.exception("Forfeiture risk error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500
