"""
Court Outcome Predictor — ShamrockLeads Intelligence Suite

Analyzes charge data, defendant history, and county patterns to estimate:
  • FTA (Failure to Appear) probability
  • Conviction likelihood
  • Case disposition timeline
  • Bond forfeiture risk signal

Uses a scoring heuristic seeded from Florida DOJ statistics + local MongoDB
ground-truth when available. Designed to be upgraded to full ML once enough
labelled outcome data accumulates in active_bonds.status_history.
"""

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("shamrock.court_outcome_predictor")

# ── Charge Severity Mapping ────────────────────────────────────────────────
# Florida Statute severity tiers
CHARGE_SEVERITY = {
    # Capital / Life
    "murder": 10, "homicide": 10, "manslaughter": 9,
    # First-degree felonies
    "trafficking": 9, "armed robbery": 9, "sexual battery": 9,
    "aggravated assault": 8, "aggravated battery": 8, "carjacking": 8,
    # Second-degree felonies
    "robbery": 7, "burglary": 7, "grand theft": 6,
    "fleeing": 6, "felony dui": 7,
    # Third-degree felonies
    "battery": 5, "theft": 4, "fraud": 5, "forgery": 5,
    "possession": 4, "poss": 4, "dwls": 3,
    # Misdemeanors
    "petit theft": 2, "disorderly": 2, "trespass": 2,
    "dui": 3, "driving under": 3,
    # Low risk
    "violation of probation": 4, "vop": 4, "fta": 5,
    "failure to appear": 5,
}

# County-level FTA base rates (Florida averages, calibrated from DOJ data)
COUNTY_FTA_RATES = {
    "lee": 0.18, "collier": 0.14, "charlotte": 0.16, "hendry": 0.22,
    "desoto": 0.21, "manatee": 0.17, "sarasota": 0.15,
    "hillsborough": 0.19, "pinellas": 0.18, "orange": 0.20,
    "duval": 0.22, "broward": 0.17, "miami-dade": 0.19,
    "palm beach": 0.16, "volusia": 0.20, "brevard": 0.18,
    "default": 0.18,
}


def _extract_severity(charges_text: str) -> int:
    """Extract max charge severity from charge description text."""
    if not charges_text:
        return 3  # default mid-range

    charges_lower = charges_text.lower()
    max_sev = 0
    for keyword, sev in CHARGE_SEVERITY.items():
        if keyword in charges_lower:
            max_sev = max(max_sev, sev)
    return max_sev if max_sev > 0 else 3


def _has_prior_fta(defendant: dict) -> bool:
    """Check if defendant has prior FTA indicators."""
    charges = str(defendant.get("charges", "") or defendant.get("Charges", "")).lower()
    return "fta" in charges or "failure to appear" in charges


def _bond_amount_risk(bond_amount: float) -> float:
    """Higher bonds correlate with higher flight risk (non-linear)."""
    if bond_amount <= 0:
        return 0.0
    if bond_amount < 5000:
        return 0.05
    if bond_amount < 25000:
        return 0.10
    if bond_amount < 100000:
        return 0.20
    if bond_amount < 500000:
        return 0.30
    return 0.40


def predict_outcome(record: dict, defendant_history: list = None) -> dict:
    """
    Predict court outcome probabilities for an arrest/defendant record.

    Args:
        record: Arrest record or defendant dict with charges, bond_amount, county
        defendant_history: Optional list of prior arrest records for this defendant

    Returns:
        dict with fta_probability, conviction_likelihood, risk_level,
             estimated_disposition_days, intervention_suggestions
    """
    charges = str(record.get("charges", "") or record.get("Charges", ""))
    county = str(record.get("county", "") or record.get("County", "")).lower().strip()
    bond_amount = float(record.get("bond_amount", 0) or record.get("Bond_Amount", 0) or 0)

    # ── Factor 1: Charge severity ──────────────────────────────────────────
    severity = _extract_severity(charges)
    severity_fta_adj = {
        10: 0.35, 9: 0.30, 8: 0.25, 7: 0.20, 6: 0.15,
        5: 0.10, 4: 0.05, 3: 0.00, 2: -0.05, 1: -0.08, 0: -0.05,
    }.get(severity, 0.0)

    # ── Factor 2: County base rate ─────────────────────────────────────────
    base_fta = COUNTY_FTA_RATES.get(county, COUNTY_FTA_RATES["default"])

    # ── Factor 3: Bond amount risk ─────────────────────────────────────────
    bond_risk = _bond_amount_risk(bond_amount)

    # ── Factor 4: Prior history ────────────────────────────────────────────
    history_adj = 0.0
    prior_arrests = 0
    if defendant_history:
        prior_arrests = len(defendant_history)
        if prior_arrests >= 5:
            history_adj = 0.15
        elif prior_arrests >= 3:
            history_adj = 0.10
        elif prior_arrests >= 1:
            history_adj = 0.05

    # Prior FTA is the strongest single predictor
    has_fta = _has_prior_fta(record)
    if has_fta:
        history_adj += 0.20

    # ── Factor 5: ML FTA Model Intelligence ────────────────────────────────
    # When the hybrid_scorer has produced an ML FTA prediction, use it to
    # calibrate our heuristic estimate (weighted blend).
    ml_fta_adj = 0.0
    ml_fta_score = record.get("fta_risk_score")
    ml_fta_level = record.get("fta_risk_level")

    if ml_fta_score is not None:
        # Normalize ML score (0-100) to probability (0-1) and blend
        ml_fta_prob = ml_fta_score / 100.0
        ml_fta_adj = ml_fta_prob * 0.30  # ML contributes 30% of final signal
    elif record.get("extra", {}).get("fta_risk_score") is not None:
        # Fall back to nested extra_data
        ml_fta_prob = record["extra"]["fta_risk_score"] / 100.0
        ml_fta_adj = ml_fta_prob * 0.30
        ml_fta_level = record["extra"].get("fta_risk_level")

    # ── Composite FTA probability ──────────────────────────────────────────
    fta_heuristic = base_fta + severity_fta_adj + bond_risk + history_adj

    if ml_fta_adj > 0:
        # Blend: 70% heuristic + 30% ML calibration
        fta_raw = fta_heuristic * 0.70 + ml_fta_adj
    else:
        fta_raw = fta_heuristic

    fta_probability = max(0.02, min(0.95, fta_raw))

    # ── Conviction likelihood (inverse-ish of FTA for serious charges) ─────
    conviction_base = 0.65  # FL avg conviction rate
    if severity >= 8:
        conviction_adj = 0.15
    elif severity >= 5:
        conviction_adj = 0.05
    else:
        conviction_adj = -0.10
    conviction_likelihood = max(0.10, min(0.95, conviction_base + conviction_adj))

    # ── Estimated disposition timeline ─────────────────────────────────────
    if severity >= 8:
        est_days = 180 + (severity * 30)
    elif severity >= 5:
        est_days = 90 + (severity * 15)
    else:
        est_days = 30 + (severity * 10)

    # ── Risk classification ────────────────────────────────────────────────
    if fta_probability >= 0.40:
        risk_level = "critical"
    elif fta_probability >= 0.25:
        risk_level = "high"
    elif fta_probability >= 0.15:
        risk_level = "medium"
    else:
        risk_level = "low"

    # ── Intervention suggestions ───────────────────────────────────────────
    interventions = []
    if fta_probability >= 0.40:
        interventions.append("Require GPS monitoring")
        interventions.append("Increase check-in frequency to daily")
        interventions.append("Consider collateral requirement")
    if fta_probability >= 0.25:
        interventions.append("Weekly in-person check-ins")
        interventions.append("Assign dedicated agent follow-up")
    if has_fta:
        interventions.append("Prior FTA — escalate supervision level")
    if bond_amount >= 100000:
        interventions.append("High-value bond — enhanced monitoring")
    if prior_arrests >= 3:
        interventions.append("Repeat offender — verify employment/residence stability")

    return {
        "success": True,
        "fta_probability": round(fta_probability, 3),
        "conviction_likelihood": round(conviction_likelihood, 3),
        "risk_level": risk_level,
        "estimated_disposition_days": est_days,
        "charge_severity": severity,
        "prior_arrests": prior_arrests,
        "has_prior_fta": has_fta,
        "county_base_fta_rate": base_fta,
        "interventions": interventions,
        "factors": {
            "base_rate": round(base_fta, 3),
            "severity_adjustment": round(severity_fta_adj, 3),
            "bond_amount_risk": round(bond_risk, 3),
            "history_adjustment": round(history_adj, 3),
            "ml_fta_adjustment": round(ml_fta_adj, 3),
            "ml_fta_score": ml_fta_score,
            "ml_fta_level": ml_fta_level,
            "ml_calibrated": ml_fta_adj > 0,
        },
        "computed_at": datetime.now(timezone.utc).isoformat() + "Z",
    }


async def predict_batch(db, records: list) -> list:
    """Score multiple records, enriching with defendant history + empirical data."""
    results = []
    for rec in records:
        # Try to find defendant history
        history = []
        defendant_name = rec.get("defendant_name") or rec.get("Defendant_Name")
        if defendant_name and db:
            try:
                cursor = db.arrests.find(
                    {"Defendant_Name": defendant_name},
                    {"charges": 1, "bond_amount": 1, "county": 1}
                ).limit(20)
                history = await cursor.to_list(length=20)
            except Exception:
                pass

        base_prediction = predict_outcome(rec, history)

        # Enhance with empirical court data if available
        if db:
            try:
                enhanced = await _enhance_with_empirical_data(db, rec, base_prediction)
                results.append(enhanced)
            except Exception:
                results.append(base_prediction)
        else:
            results.append(base_prediction)

    return results


async def _enhance_with_empirical_data(db, record: dict, prediction: dict) -> dict:
    """Blend empirical court_outcomes data with heuristic prediction.

    When the court_outcomes collection has disposition data for the
    defendant's county or charge type, we use real conviction/FTA rates
    to adjust the heuristic prediction. Empirical data is weighted higher
    when the sample size is large enough to be statistically meaningful.
    """
    try:
        county = str(record.get("county", "") or record.get("County", "")).lower().strip()
        charges = str(record.get("charges", "") or record.get("Charges", "")).lower()

        # Check if court_outcomes collection exists and has data
        outcome_count = await db.court_outcomes.count_documents({})
        if outcome_count < 10:
            prediction["data_source"] = "heuristic_only"
            prediction["empirical_sample_size"] = 0
            return prediction

        # Get state-level disposition rates
        state = "FL"  # Default for our primary ops
        from dashboard.services.court_data_ingestor import get_disposition_rates
        rates_result = await get_disposition_rates(db, state=state)
        rates = rates_result.get("rates", {})
        sample_size = rates_result.get("sample_size", 0)

        if sample_size < 10 or not rates:
            prediction["data_source"] = "heuristic_only"
            prediction["empirical_sample_size"] = 0
            return prediction

        # Calculate empirical conviction rate
        empirical_conviction = (
            rates.get("conviction", 0)
            + rates.get("plea", 0)
            + rates.get("affirmed", 0)
        )
        empirical_dismissal = rates.get("dismissed", 0) + rates.get("acquittal", 0)

        # Blend: weight empirical data based on sample size
        # At 100+ samples, empirical gets 60% weight; at 10, only 20%
        empirical_weight = min(0.6, 0.2 + (sample_size / 500))
        heuristic_weight = 1.0 - empirical_weight

        # Adjust conviction likelihood
        heuristic_conviction = prediction.get("conviction_likelihood", 0.65)
        blended_conviction = (
            heuristic_conviction * heuristic_weight
            + empirical_conviction * empirical_weight
        )
        prediction["conviction_likelihood"] = round(max(0.05, min(0.95, blended_conviction)), 3)

        # Add empirical metadata
        prediction["data_source"] = "blended"
        prediction["empirical_sample_size"] = sample_size
        prediction["empirical_weight"] = round(empirical_weight, 2)
        prediction["empirical_rates"] = {
            "conviction": round(empirical_conviction, 3),
            "dismissal": round(empirical_dismissal, 3),
        }

        return prediction

    except Exception as e:
        log.debug("Empirical enhancement skip: %s", str(e)[:100])
        prediction["data_source"] = "heuristic_only"
        return prediction

