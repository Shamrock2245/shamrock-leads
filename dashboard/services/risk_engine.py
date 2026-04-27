"""
ShamrockLeads — Risk Engine
Computes 0-100 risk scores for active bonds.
Higher score = higher risk of FTA (failure to appear).
"""

HIGH_RISK_KEYWORDS = [
    "MURDER", "HOMICIDE", "ROBBERY", "TRAFFICKING", "ASSAULT",
    "WEAPON", "FIREARM", "FLEE", "ESCAPE", "FUGITIVE",
]


def compute_risk_score(bond_doc: dict) -> int:
    """
    Compute a 0-100 risk score for an active bond.
    Higher = higher risk of FTA (failure to appear).
    """
    score = 50  # baseline

    # Missed check-ins increase risk
    missed = bond_doc.get("missed_check_ins", 0)
    score += min(missed * 10, 30)

    # Out-of-area pings increase risk
    out_of_area = bond_doc.get("out_of_area_count", 0)
    score += min(out_of_area * 8, 24)

    # High bond amount = higher risk
    bond_amount = float(bond_doc.get("bond_amount", 0) or 0)
    if bond_amount >= 50000:
        score += 10
    elif bond_amount >= 25000:
        score += 5

    # Violent/drug charges increase risk
    charges_raw = (bond_doc.get("charges_raw", "") or "").upper()
    for kw in HIGH_RISK_KEYWORDS:
        if kw in charges_raw:
            score += 5
            break

    # Recent location history reduces risk
    loc_history = bond_doc.get("location_history", [])
    if len(loc_history) >= 3:
        score -= 5

    return max(0, min(100, score))
