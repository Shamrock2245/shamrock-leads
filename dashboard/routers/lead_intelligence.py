from __future__ import annotations

"""ShamrockLeads — AI Lead Intelligence API Blueprint

Endpoints:
  GET /api/leads/<booking_number>/intelligence  — Risk score explanation + similar cases
  GET /api/leads/trend-stats                    — KPI trend data (vs prior period)
  GET /api/leads/charge-severity/<booking>      — Charge severity classification

All routes use Quart (async) + Motor (async MongoDB).
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.extensions import get_db

logger = logging.getLogger(__name__)

lead_intel_bp = APIRouter(prefix="/api", tags=["lead_intelligence"])
# ── Charge severity classification ───────────────────────────────────────────
CAPITAL_KEYWORDS = [
    'murder', 'capital', 'first degree murder', '1st degree murder',
    'sexual battery', 'kidnapping', 'armed robbery', 'carjacking'
]
FELONY_KEYWORDS = [
    'felony', 'trafficking', 'burglary', 'robbery', 'aggravated',
    'assault with deadly', 'grand theft', 'manslaughter', 'arson',
    'home invasion', 'battery on law enforcement', 'fleeing'
]
MISDEMEANOR_KEYWORDS = [
    'misdemeanor', 'petit theft', 'trespass', 'disorderly',
    'battery', 'assault', 'dui', 'driving under', 'possession of marijuana',
    'possession of cannabis', 'resisting without violence'
]

DISQUALIFYING_KEYWORDS = ['capital', 'murder', 'federal']


def classify_charge(charge_text: str) -> dict:
    """Classify a charge string into severity tier."""
    if not charge_text:
        return {"severity": "unknown", "color": "#6b7280", "label": "Unknown"}
    text = charge_text.lower()

    if any(k in text for k in DISQUALIFYING_KEYWORDS):
        return {"severity": "capital", "color": "#dc2626", "label": "Capital / Disqualifying"}
    if any(k in text for k in CAPITAL_KEYWORDS):
        return {"severity": "capital", "color": "#dc2626", "label": "Capital Offense"}
    if any(k in text for k in FELONY_KEYWORDS):
        return {"severity": "felony", "color": "#ea580c", "label": "Felony"}
    if any(k in text for k in MISDEMEANOR_KEYWORDS):
        return {"severity": "misdemeanor", "color": "#ca8a04", "label": "Misdemeanor"}
    # Default: classify by F/M prefix common in Florida
    if re.search(r'\bF[1-3]\b|\bFC\b', charge_text):
        return {"severity": "felony", "color": "#ea580c", "label": "Felony"}
    if re.search(r'\bM[12]\b', charge_text):
        return {"severity": "misdemeanor", "color": "#ca8a04", "label": "Misdemeanor"}
    return {"severity": "unknown", "color": "#6b7280", "label": "Unknown"}


def build_score_explanation(arrest: dict) -> list[dict]:
    """Reconstruct score factor breakdown from an arrest document."""
    factors = []
    bond_amount = arrest.get("bond_amount", 0) or 0
    bond_type = (arrest.get("bond_type") or "").upper()
    status = (arrest.get("status") or "").upper()
    charges = arrest.get("charges") or ""

    # Bond amount factor
    if bond_amount == 0:
        factors.append({"factor": "Bond Amount", "points": -50, "reason": "$0 bond — no monetary bond set", "icon": "❌"})
    elif bond_amount < 500:
        factors.append({"factor": "Bond Amount", "points": -10, "reason": f"${bond_amount:,.0f} — below $500 minimum threshold", "icon": "⚠️"})
    elif bond_amount <= 50000:
        factors.append({"factor": "Bond Amount", "points": 30, "reason": f"${bond_amount:,.0f} — prime bondable range ($500–$50K)", "icon": "✅"})
    elif bond_amount <= 100000:
        factors.append({"factor": "Bond Amount", "points": 20, "reason": f"${bond_amount:,.0f} — high-value bond ($50K–$100K)", "icon": "✅"})
    else:
        factors.append({"factor": "Bond Amount", "points": 10, "reason": f"${bond_amount:,.0f} — very high bond (>$100K), harder to write", "icon": "⚠️"})

    # Bond type factor
    if 'NO BOND' in bond_type or 'HOLD' in bond_type:
        factors.append({"factor": "Bond Type", "points": -50, "reason": "No Bond / Hold — not bondable", "icon": "❌"})
    elif 'ROR' in bond_type or 'R.O.R' in bond_type:
        factors.append({"factor": "Bond Type", "points": -30, "reason": "Released on Own Recognizance — no premium", "icon": "❌"})
    elif 'CASH' in bond_type or 'SURETY' in bond_type:
        factors.append({"factor": "Bond Type", "points": 25, "reason": f"{bond_type} — bondable type", "icon": "✅"})

    # Custody status
    if 'IN CUSTODY' in status or 'INCUSTODY' in status:
        factors.append({"factor": "Custody Status", "points": 20, "reason": "Currently in custody — active opportunity", "icon": "✅"})
    elif 'RELEASED' in status:
        factors.append({"factor": "Custody Status", "points": -30, "reason": "Already released — bond no longer needed", "icon": "❌"})

    # Data completeness
    missing = []
    if not arrest.get("full_name"): missing.append("Name")
    if not arrest.get("charges"): missing.append("Charges")
    if not arrest.get("bond_amount"): missing.append("Bond Amount")
    if not arrest.get("court_date"): missing.append("Court Date")
    if not missing:
        factors.append({"factor": "Data Completeness", "points": 15, "reason": "All required fields present", "icon": "✅"})
    else:
        factors.append({"factor": "Data Completeness", "points": -10, "reason": f"Missing: {', '.join(missing)}", "icon": "⚠️"})

    # Disqualifying charges
    charges_lower = charges.lower()
    for kw in DISQUALIFYING_KEYWORDS:
        if kw in charges_lower:
            factors.append({"factor": "Charge Severity", "points": -100, "reason": f"Disqualifying charge: {kw}", "icon": "🚫"})
            break

    return factors


# ── Lead Intelligence Endpoint ────────────────────────────────────────────────
@lead_intel_bp.get("/leads/{booking_number}/intelligence")
async def lead_intelligence(booking_number: str):
    """Returns AI-style intelligence breakdown for a specific lead."""
    try:
        db = get_db()
        arrests_col = db["arrests"]

        arrest = await arrests_col.find_one({"booking_number": booking_number})
        if not arrest:
            return JSONResponse({"success": False, "error": "Lead not found"}, status_code=404)

        # Score explanation
        factors = build_score_explanation(arrest)
        total_score = sum(f["points"] for f in factors)

        # Charge severity classification
        charges = arrest.get("charges") or ""
        charge_list = [c.strip() for c in re.split(r'[;,\n]', charges) if c.strip()]
        classified_charges = [
            {**classify_charge(c), "charge": c}
            for c in charge_list[:10]  # Limit to 10 charges
        ]

        # Similar past cases (same county, similar bond range)
        county = arrest.get("county", "")
        bond_amount = arrest.get("bond_amount", 0) or 0
        bond_lo = bond_amount * 0.5
        bond_hi = bond_amount * 1.5

        similar_active = await db["active_bonds"].count_documents({
            "county": county,
            "bond_amount": {"$gte": bond_lo, "$lte": bond_hi}
        })
        similar_premium_pipe = await db["active_bonds"].aggregate([
            {"$match": {"county": county, "bond_amount": {"$gte": bond_lo, "$lte": bond_hi}}},
            {"$group": {"_id": None, "avg_premium": {"$avg": "$premium"}, "count": {"$sum": 1}}}
        ]).to_list(1)
        avg_similar_premium = similar_premium_pipe[0]["avg_premium"] if similar_premium_pipe else 0

        # Optimal contact time (based on historical response rates — simplified heuristic)
        # Best contact times for bail bond leads: 8-10am and 6-8pm
        optimal_times = ["8:00 AM – 10:00 AM", "6:00 PM – 8:00 PM"]

        return {
            "success": True,
            "booking_number": booking_number,
            "score_explanation": {
                "factors": factors,
                "total_score": total_score,
                "status": arrest.get("lead_status", "Unknown"),
                "summary": f"Score of {total_score} based on {len(factors)} factors"
            },
            "classified_charges": classified_charges,
            "similar_cases": {
                "county": county,
                "bond_range": f"${bond_lo:,.0f}–${bond_hi:,.0f}",
                "count": similar_active,
                "avg_premium": round(avg_similar_premium, 2),
                "insight": f"{similar_active} similar bonds written in {county} county — avg premium ${avg_similar_premium:,.0f}"
                           if similar_active > 0 else f"No similar bonds found in {county} county"
            },
            "optimal_contact": {
                "times": optimal_times,
                "insight": "Best response rates for bail bond leads are early morning and early evening"
            }
        }
    except Exception as exc:
        logger.exception("lead intelligence error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ── KPI Trend Stats ───────────────────────────────────────────────────────────
@lead_intel_bp.get("/leads/trend-stats")
async def trend_stats():
    """
    Returns current vs prior period stats for trend arrows on KPI cards.
    Compares last 7 days vs prior 7 days.
    """
    try:
        db = get_db()
        arrests_col = db["arrests"]
        prospective_col = db["prospective_bonds"]
        active_bonds_col = db["active_bonds"]
        payments_col = db["payments"]

        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        two_weeks_ago = now - timedelta(days=14)

        async def count_range(col, field, start, end, extra_match=None):
            match = {field: {"$gte": start, "$lt": end}}
            if extra_match:
                match.update(extra_match)
            return await col.count_documents(match)

        async def sum_range(col, field, value_field, start, end, extra_match=None):
            match = {field: {"$gte": start, "$lt": end}}
            if extra_match:
                match.update(extra_match)
            pipe = await col.aggregate([
                {"$match": match},
                {"$group": {"_id": None, "total": {"$sum": f"${value_field}"}}}
            ]).to_list(1)
            return pipe[0]["total"] if pipe else 0

        # Current period (last 7 days)
        curr_leads = await count_range(arrests_col, "scraped_at", week_ago, now)
        curr_hot = await count_range(arrests_col, "scraped_at", week_ago, now, {"lead_status": "Hot"})
        curr_bonds = await count_range(active_bonds_col, "created_at", week_ago, now)
        curr_revenue = await sum_range(payments_col, "timestamp", "amount", week_ago, now,
                                       {"status": {"$in": ["completed", "paid", "success"]}})

        # Prior period (7–14 days ago)
        prev_leads = await count_range(arrests_col, "scraped_at", two_weeks_ago, week_ago)
        prev_hot = await count_range(arrests_col, "scraped_at", two_weeks_ago, week_ago, {"lead_status": "Hot"})
        prev_bonds = await count_range(active_bonds_col, "created_at", two_weeks_ago, week_ago)
        prev_revenue = await sum_range(payments_col, "timestamp", "amount", two_weeks_ago, week_ago,
                                       {"status": {"$in": ["completed", "paid", "success"]}})

        def trend(curr, prev):
            if prev == 0:
                return {"direction": "up" if curr > 0 else "flat", "pct": 100 if curr > 0 else 0}
            pct = round((curr - prev) / prev * 100, 1)
            return {"direction": "up" if pct > 0 else ("down" if pct < 0 else "flat"), "pct": abs(pct)}

        return {
            "success": True,
            "period": "7d",
            "trends": {
                "leads": {"curr": curr_leads, "prev": prev_leads, **trend(curr_leads, prev_leads)},
                "hot_leads": {"curr": curr_hot, "prev": prev_hot, **trend(curr_hot, prev_hot)},
                "bonds_written": {"curr": curr_bonds, "prev": prev_bonds, **trend(curr_bonds, prev_bonds)},
                "revenue": {"curr": round(curr_revenue, 2), "prev": round(prev_revenue, 2), **trend(curr_revenue, prev_revenue)},
            }
        }
    except Exception as exc:
        logger.exception("trend-stats error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ── Charge Severity for a Lead ────────────────────────────────────────────────
@lead_intel_bp.get("/leads/{booking_number}/charge-severity")
async def charge_severity(booking_number: str):
    """Returns charge severity classification for a specific lead."""
    try:
        db = get_db()
        arrest = await db["arrests"].find_one(
            {"booking_number": booking_number},
            {"charges": 1, "booking_number": 1}
        )
        if not arrest:
            return JSONResponse({"success": False, "error": "Not found"}, status_code=404)

        charges = arrest.get("charges") or ""
        charge_list = [c.strip() for c in re.split(r'[;,\n]', charges) if c.strip()]
        classified = [
            {**classify_charge(c), "charge": c}
            for c in charge_list[:10]
        ]

        # Overall severity = worst charge
        severity_order = {"capital": 4, "felony": 3, "misdemeanor": 2, "unknown": 1}
        worst = max(classified, key=lambda x: severity_order.get(x["severity"], 0)) if classified else None

        return {
            "success": True,
            "booking_number": booking_number,
            "charges": classified,
            "worst_severity": worst,
        }
    except Exception as exc:
        logger.exception("charge-severity error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
