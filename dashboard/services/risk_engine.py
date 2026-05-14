"""
ShamrockLeads — Risk Engine
Computes 0-100 risk scores for active bonds.
Higher score = higher risk of FTA (failure to appear).

Factors (weighted):
  - Charge severity (from charge_classifier)       +0-25 pts
  - Bond amount tier                                +0-10 pts
  - Prior FTA / failure-to-appear history           +0-20 pts
  - Missed check-in streak                          +0-20 pts
  - Out-of-area GPS pings                           +0-15 pts
  - Days since booking (recency of release)         +0-10 pts
  - Court date proximity (< 7 days = urgency)       +0-10 pts
  - Active location history (reduces risk)          -0-10 pts
  - Consecutive on-time check-ins (reduces risk)    -0-10 pts

Return value: dict with keys 'score' (int 0-100), 'tier' (str), 'factors' (list).
For backward compat, also supports int comparison via __int__ on the dict (use .get("score")).
"""

from datetime import datetime, timezone
from typing import Optional

# ── Charge severity map ──────────────────────────────────────────────────────
SEVERITY_SCORES = {
    "violent":          25,
    "weapons":          22,
    "drug_trafficking": 20,
    "sex_offense":      20,
    "property_major":   15,
    "domestic":         14,
    "drug_possession":  12,
    "fraud":            10,
    "dui":              10,
    "property_minor":    8,
    "traffic":           4,
    "other":             5,
    "unknown":           5,
}

_VIOLENT_KW  = ["MURDER", "HOMICIDE", "ROBBERY", "ASSAULT", "BATTERY",
                "KIDNAP", "CARJACK", "ARSON", "RAPE", "SEXUAL BATTERY"]
_WEAPONS_KW  = ["WEAPON", "FIREARM", "GUN", "AMMUNITION", "EXPLOSIVE"]
_FLEE_KW     = ["FLEE", "ELUDE", "ESCAPE", "FUGITIVE", "RESIST"]
_DRUG_TRAF   = ["TRAFFICKING", "MANUFACTURE", "DISTRIBUTION",
                "DELIVER COCAINE", "DELIVER HEROIN", "DELIVER METH",
                "DELIVER FENTANYL"]


def _severity_from_charges(charges_raw: str) -> int:
    try:
        from scoring.charge_classifier import classify_charge
        result = classify_charge(charges_raw)
        return SEVERITY_SCORES.get(result.get("category", "unknown"), 5)
    except Exception:
        pass
    upper = (charges_raw or "").upper()
    if any(k in upper for k in _DRUG_TRAF):
        return 20
    if any(k in upper for k in _VIOLENT_KW):
        return 25
    if any(k in upper for k in _WEAPONS_KW):
        return 22
    if any(k in upper for k in _FLEE_KW):
        return 15
    return 5


def _days_since(dt_value) -> Optional[int]:
    if dt_value is None:
        return None
    try:
        if isinstance(dt_value, str):
            dt_value = datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
        if dt_value.tzinfo is None:
            dt_value = dt_value.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - dt_value).days)
    except Exception:
        return None


def _days_until(dt_value) -> Optional[int]:
    if dt_value is None:
        return None
    try:
        if isinstance(dt_value, str):
            dt_value = datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
        if dt_value.tzinfo is None:
            dt_value = dt_value.replace(tzinfo=timezone.utc)
        return (dt_value - datetime.now(timezone.utc)).days
    except Exception:
        return None


def compute_risk_score(bond_doc: dict) -> dict:
    """
    Compute a 0-100 risk score for an active bond.
    Returns dict: {'score': int, 'tier': str, 'factors': list, 'computed_at': str}
    """
    score = 40  # baseline
    factors = []

    # 1. Charge severity
    sev = _severity_from_charges(bond_doc.get("charges_raw", "") or "")
    if sev:
        score += sev
        factors.append({"factor": "charge_severity", "delta": sev,
                         "detail": f"Charge severity: +{sev}"})

    # 2. Bond amount tier
    bond_amount = float(bond_doc.get("bond_amount", 0) or 0)
    if bond_amount >= 100_000:
        score += 10; factors.append({"factor": "bond_amount", "delta": 10, "detail": "Bond ≥ $100k: +10"})
    elif bond_amount >= 50_000:
        score += 7;  factors.append({"factor": "bond_amount", "delta": 7,  "detail": "Bond ≥ $50k: +7"})
    elif bond_amount >= 25_000:
        score += 4;  factors.append({"factor": "bond_amount", "delta": 4,  "detail": "Bond ≥ $25k: +4"})

    # 3. Prior FTA history
    prior_fta = int(bond_doc.get("prior_fta_count", 0) or 0)
    if prior_fta >= 3:
        score += 20; factors.append({"factor": "prior_fta", "delta": 20, "detail": f"{prior_fta} prior FTAs: +20"})
    elif prior_fta == 2:
        score += 14; factors.append({"factor": "prior_fta", "delta": 14, "detail": "2 prior FTAs: +14"})
    elif prior_fta == 1:
        score += 8;  factors.append({"factor": "prior_fta", "delta": 8,  "detail": "1 prior FTA: +8"})

    # 4. Missed check-in streak
    consec_missed = int(bond_doc.get("consecutive_missed", 0) or 0)
    missed_total  = int(bond_doc.get("missed_check_ins", 0) or 0)
    if consec_missed >= 3:
        d = min(20, consec_missed * 5)
        score += d; factors.append({"factor": "missed_streak", "delta": d,
                                     "detail": f"{consec_missed} consecutive misses: +{d}"})
    elif missed_total > 0:
        d = min(10, missed_total * 3)
        score += d; factors.append({"factor": "missed_total", "delta": d,
                                     "detail": f"{missed_total} total misses: +{d}"})

    # 5. Out-of-area GPS pings
    ooa = int(bond_doc.get("out_of_area_count", 0) or 0)
    if ooa > 0:
        d = min(15, ooa * 5)
        score += d; factors.append({"factor": "out_of_area", "delta": d,
                                     "detail": f"{ooa} out-of-area pings: +{d}"})

    # 6. Days since booking
    dsb = _days_since(bond_doc.get("booking_date"))
    if dsb is not None:
        if dsb <= 7:
            score += 10; factors.append({"factor": "recency", "delta": 10, "detail": f"Booked {dsb}d ago: +10"})
        elif dsb <= 30:
            score += 5;  factors.append({"factor": "recency", "delta": 5,  "detail": f"Booked {dsb}d ago: +5"})

    # 7. Court date proximity
    duc = _days_until(bond_doc.get("next_court_date"))
    if duc is not None:
        if duc < 0:
            score += 20; factors.append({"factor": "court_overdue", "delta": 20,
                                          "detail": f"Court was {abs(duc)}d ago (OVERDUE): +20"})
        elif duc <= 3:
            score += 10; factors.append({"factor": "court_proximity", "delta": 10,
                                          "detail": f"Court in {duc}d (imminent): +10"})
        elif duc <= 7:
            score += 6;  factors.append({"factor": "court_proximity", "delta": 6,
                                          "detail": f"Court in {duc}d (this week): +6"})

    # 8. Active location history (reduces risk)
    loc_len = len(bond_doc.get("location_history", []) or [])
    if loc_len >= 10:
        score -= 10; factors.append({"factor": "location_active", "delta": -10,
                                      "detail": f"{loc_len} GPS check-ins: -10"})
    elif loc_len >= 5:
        score -= 5;  factors.append({"factor": "location_active", "delta": -5,
                                      "detail": f"{loc_len} GPS check-ins: -5"})

    # 9. Consecutive on-time check-ins (reduces risk)
    consec_ontime = int(bond_doc.get("consecutive_ontime", 0) or 0)
    if consec_ontime >= 5:
        score -= 10; factors.append({"factor": "ontime_streak", "delta": -10,
                                      "detail": f"{consec_ontime} on-time check-ins: -10"})
    elif consec_ontime >= 3:
        score -= 5;  factors.append({"factor": "ontime_streak", "delta": -5,
                                      "detail": f"{consec_ontime} on-time check-ins: -5"})

    final = max(0, min(100, score))
    tier = ("critical" if final >= 75 else
            "high"     if final >= 55 else
            "medium"   if final >= 35 else "low")

    return {
        "score": final,
        "tier": tier,
        "factors": factors,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
