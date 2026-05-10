"""
ShamrockLeads — Hybrid Lead Scorer
====================================
Bridges the existing rule-based LeadScorer with ML predictions.

Strategy:
  - When a trained ML model is available AND has high confidence → use ML score
  - When ML is uncertain (confidence="low") → blend ML + rule-based
  - When no ML model exists → fall back to pure rule-based scoring
  - Always log both scores for comparison and model calibration

This is a drop-in replacement scorer that the scraper pipeline can call
instead of LeadScorer.score_arrest() directly.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def hybrid_score(record: Dict[str, Any], enrichment: Optional[Dict] = None) -> Dict[str, Any]:
    """Score an arrest record using the best available method.

    Returns:
        {
            "score": int (0-100),
            "status": "Hot" | "Warm" | "Cold" | "Disqualified",
            "method": "ml" | "hybrid" | "rule_based",
            "ml_score": float or None,
            "ml_confidence": str or None,
            "rule_score": int,
            "rule_status": str,
            "factors": list[dict],  # Top contributing factors
        }
    """
    # ── 1. Always compute rule-based score ───────────────────────────────────
    from scoring.lead_scorer import LeadScorer
    scorer = LeadScorer()
    rule_result = scorer.score_arrest(record)
    rule_score = rule_result.get("score", 0)
    rule_status = rule_result.get("status", "Unknown")

    # ── 2. Attempt ML prediction (Lead Quality) ────────────────────────────
    ml_score = None
    ml_confidence = None
    ml_prediction = None
    ml_factors = []

    try:
        from scoring.model_trainer import predict

        ml_result = predict(record, target="lead_quality", enrichment=enrichment)

        if ml_result is not None:
            ml_score = ml_result["ml_score"]
            ml_confidence = ml_result["confidence"]
            ml_prediction = ml_result["prediction"]
            ml_factors = ml_result.get("top_factors", [])
    except Exception as e:
        logger.debug("ML lead_quality prediction unavailable: %s", e)

    # ── 2b. Attempt FTA Risk prediction ──────────────────────────────────────
    fta_risk_score = None
    fta_risk_level = None
    fta_risk_confidence = None
    fta_factors = []

    try:
        from scoring.model_trainer import predict as predict_fn

        fta_result = predict_fn(record, target="fta_risk", enrichment=enrichment)

        if fta_result is not None:
            fta_risk_score = fta_result["ml_score"]
            fta_risk_confidence = fta_result["confidence"]
            fta_factors = fta_result.get("top_factors", [])
            # Classify FTA risk level
            if fta_risk_score >= 70:
                fta_risk_level = "critical"
            elif fta_risk_score >= 50:
                fta_risk_level = "high"
            elif fta_risk_score >= 30:
                fta_risk_level = "moderate"
            else:
                fta_risk_level = "low"
    except Exception as e:
        logger.debug("ML fta_risk prediction unavailable: %s", e)

    # ── 3. Determine final score using hybrid logic ──────────────────────────
    if ml_score is not None and ml_confidence == "high":
        # High confidence ML — trust the model
        final_score = int(round(ml_score))
        method = "ml"
        factors = ml_factors

    elif ml_score is not None and ml_confidence == "medium":
        # Medium confidence — blend 60% ML + 40% rule-based
        final_score = int(round(ml_score * 0.6 + rule_score * 0.4))
        method = "hybrid"
        factors = ml_factors

    elif ml_score is not None and ml_confidence == "low":
        # Low confidence — favor rule-based with ML nudge
        final_score = int(round(ml_score * 0.3 + rule_score * 0.7))
        method = "hybrid"
        factors = []

    else:
        # No ML model — pure rule-based
        final_score = rule_score
        method = "rule_based"
        factors = []

    # Clamp to 0-100
    final_score = max(0, min(100, final_score))

    # Derive status from final score
    final_status = _score_to_status(final_score)

    return {
        "score": final_score,
        "status": final_status,
        "method": method,
        "ml_score": ml_score,
        "ml_confidence": ml_confidence,
        "ml_prediction": ml_prediction,
        "rule_score": rule_score,
        "rule_status": rule_status,
        "factors": factors,
        # FTA Risk overlay
        "fta_risk_score": fta_risk_score,
        "fta_risk_level": fta_risk_level,
        "fta_risk_confidence": fta_risk_confidence,
        "fta_factors": fta_factors,
    }


def _score_to_status(score: int) -> str:
    """Convert numeric score to categorical status."""
    if score >= 80:
        return "Hot"
    if score >= 50:
        return "Warm"
    if score >= 30:
        return "Cold"
    return "Disqualified"


def batch_hybrid_score(records: list, enrichments: Optional[Dict[str, Dict]] = None) -> list:
    """Score a batch of records.

    Args:
        records: List of arrest record dicts
        enrichments: Optional dict mapping booking_number → enrichment dict

    Returns:
        List of hybrid score results
    """
    results = []
    for record in records:
        bn = record.get("booking_number", "")
        enrichment = (enrichments or {}).get(bn)
        result = hybrid_score(record, enrichment)
        result["booking_number"] = bn
        results.append(result)
    return results
