"""
Forfeiture Risk Predictor — ShamrockLeads Intelligence Suite

Scores active bonds for forfeiture probability using:
  • Days since bond posted vs avg disposition time
  • Defendant check-in compliance rate
  • Re-arrest history
  • Charge severity
  • Court date proximity (missed dates spike risk)
  • Bond amount tier
  • County forfeiture history

Provides early-warning flags + intervention recommendations before
a forfeiture event, giving agents time to locate and surrender.
"""

import logging
from datetime import datetime, timedelta

log = logging.getLogger("shamrock.forfeiture_predictor")

# County forfeiture base rates (annualized, estimated from FL clerk data)
COUNTY_FORFEITURE_RATES = {
    "lee": 0.08, "collier": 0.06, "charlotte": 0.07, "hendry": 0.11,
    "desoto": 0.10, "manatee": 0.07, "sarasota": 0.06,
    "hillsborough": 0.09, "pinellas": 0.08, "orange": 0.10,
    "duval": 0.11, "broward": 0.07, "miami-dade": 0.09,
    "default": 0.08,
}


def score_bond(bond: dict, defendant: dict = None, check_ins: list = None,
               court_dates: list = None, docket_events: list = None) -> dict:
    """
    Score an active bond for forfeiture risk.

    Args:
        bond: Active bond record from active_bonds collection
        defendant: Optional defendant record for enrichment
        check_ins: Optional list of check-in records
        court_dates: Optional list of court date records

    Returns:
        dict with forfeiture_probability, risk_tier, days_at_risk,
             warning_signals, interventions, priority_score
    """
    now = datetime.utcnow()
    signals = []
    risk_score = 0.0

    # ── Factor 1: Bond age ─────────────────────────────────────────────────
    posted_at = bond.get("posted_date") or bond.get("created_at")
    if posted_at:
        if isinstance(posted_at, str):
            try:
                posted_at = datetime.fromisoformat(posted_at.replace("Z", "+00:00").replace("+00:00", ""))
            except (ValueError, TypeError):
                posted_at = None
    days_active = (now - posted_at).days if posted_at else 0

    if days_active > 365:
        risk_score += 0.15
        signals.append(f"Bond active for {days_active} days (>1 year)")
    elif days_active > 180:
        risk_score += 0.08
        signals.append(f"Bond active for {days_active} days (>6 months)")
    elif days_active > 90:
        risk_score += 0.03

    # ── Factor 2: Bond amount tier ─────────────────────────────────────────
    bond_amount = float(bond.get("bond_amount", 0) or bond.get("Bond_Amount", 0) or 0)
    if bond_amount >= 100000:
        risk_score += 0.10
        signals.append(f"High-value bond: ${bond_amount:,.0f}")
    elif bond_amount >= 50000:
        risk_score += 0.06
    elif bond_amount >= 25000:
        risk_score += 0.03

    # ── Factor 3: County base rate ─────────────────────────────────────────
    county = str(bond.get("county", "") or bond.get("County", "")).lower().strip()
    county_rate = COUNTY_FORFEITURE_RATES.get(county, COUNTY_FORFEITURE_RATES["default"])
    risk_score += county_rate

    # ── Factor 4: Current bond status ──────────────────────────────────────
    status = str(bond.get("status", "active")).lower()
    if status == "alert":
        risk_score += 0.20
        signals.append("Bond in ALERT status — immediate attention required")
    elif status == "monitoring":
        risk_score += 0.10
        signals.append("Bond in MONITORING status — elevated attention")

    # ── Factor 5: Check-in compliance ──────────────────────────────────────
    if check_ins is not None:
        total = len(check_ins)
        if total > 0:
            completed = sum(1 for c in check_ins if c.get("status") == "completed")
            compliance_rate = completed / total
            if compliance_rate < 0.50:
                risk_score += 0.15
                signals.append(f"Check-in compliance: {compliance_rate:.0%} (<50%)")
            elif compliance_rate < 0.75:
                risk_score += 0.08
                signals.append(f"Check-in compliance: {compliance_rate:.0%} (<75%)")
        else:
            risk_score += 0.05
            signals.append("No check-ins recorded")

    # ── Factor 6: Missed court dates ───────────────────────────────────────
    if court_dates:
        missed = sum(1 for cd in court_dates if cd.get("status") == "missed")
        if missed > 0:
            risk_score += 0.20 * min(missed, 3)
            signals.append(f"{missed} missed court date(s) — critical FTA signal")

        # Upcoming court date within 7 days
        upcoming = [cd for cd in court_dates
                     if cd.get("date") and cd.get("status") != "completed"]
        for cd in upcoming:
            try:
                cd_date = datetime.fromisoformat(str(cd["date"]).replace("Z", ""))
                days_until = (cd_date - now).days
                if 0 <= days_until <= 7:
                    signals.append(f"Court date in {days_until} day(s) — monitor closely")
            except (ValueError, TypeError):
                pass

    # ── Factor 7: Re-arrest flag ───────────────────────────────────────────
    if bond.get("re_arrested") or bond.get("rearrest_detected"):
        risk_score += 0.25
        signals.append("DEFENDANT RE-ARRESTED on active bond")

    # ── Factor 8: Charge severity from defendant record ────────────────────
    charges = ""
    if defendant:
        charges = str(defendant.get("charges", "") or defendant.get("Charges", ""))
    if not charges:
        charges = str(bond.get("charges", "") or bond.get("Charges", ""))

    charges_lower = charges.lower()
    if any(kw in charges_lower for kw in ["murder", "homicide", "trafficking"]):
        risk_score += 0.15
        signals.append("Capital/trafficking charges — highest flight risk tier")
    elif any(kw in charges_lower for kw in ["robbery", "burglary", "assault", "battery"]):
        risk_score += 0.08
    elif any(kw in charges_lower for kw in ["fta", "failure to appear", "fleeing"]):
        risk_score += 0.15
        signals.append("Prior FTA/fleeing charges — elevated flight risk")

    # ── Factor 9: Docket event intelligence ──────────────────────────────────
    if docket_events:
        docket_risk = sum(e.get("risk_adjustment", 0) for e in docket_events)
        if docket_risk != 0:
            risk_score += docket_risk
            critical_dockets = sum(1 for e in docket_events if e.get("event_severity") == "critical")
            high_dockets = sum(1 for e in docket_events if e.get("event_severity") == "high")
            if critical_dockets:
                signals.append(f"{critical_dockets} CRITICAL docket event(s) detected — {docket_risk:+.0%} risk shift")
            elif high_dockets:
                signals.append(f"{high_dockets} HIGH docket event(s) detected — {docket_risk:+.0%} risk shift")
            else:
                signals.append(f"Docket activity detected — {docket_risk:+.0%} risk shift")

    # ── Composite ──────────────────────────────────────────────────────────
    forfeiture_prob = max(0.01, min(0.95, risk_score))

    # Risk tier
    if forfeiture_prob >= 0.50:
        tier = "critical"
    elif forfeiture_prob >= 0.30:
        tier = "high"
    elif forfeiture_prob >= 0.15:
        tier = "medium"
    else:
        tier = "low"

    # Priority score for ranking (0-100)
    priority = min(100, int(forfeiture_prob * 100 + (bond_amount / 10000)))

    # ── Interventions ──────────────────────────────────────────────────────
    interventions = []
    if forfeiture_prob >= 0.50:
        interventions.append("URGENT: Consider voluntary surrender")
        interventions.append("Deploy skip-trace / locate immediately")
        interventions.append("Contact all known indemnitors")
    if forfeiture_prob >= 0.30:
        interventions.append("Increase check-in frequency to daily")
        interventions.append("Verify current address and employment")
        interventions.append("Schedule agent welfare visit")
    if forfeiture_prob >= 0.15:
        interventions.append("Send court reminder via SMS + iMessage")
        interventions.append("Confirm transportation to court")
    if any("missed court" in s.lower() for s in signals):
        interventions.append("File motion for continuance if eligible")
    if bond_amount >= 50000:
        interventions.append("Alert surety company of elevated risk")

    return {
        "success": True,
        "bond_case_id": str(bond.get("Bond_Case_ID", bond.get("_id", ""))),
        "defendant_name": bond.get("defendant_name") or bond.get("Defendant_Name", "Unknown"),
        "bond_amount": bond_amount,
        "county": county,
        "status": status,
        "days_active": days_active,
        "forfeiture_probability": round(forfeiture_prob, 3),
        "risk_tier": tier,
        "priority_score": priority,
        "warning_signals": signals,
        "interventions": interventions,
        "scored_at": now.isoformat() + "Z",
    }


async def score_portfolio(db, limit: int = 50) -> dict:
    """
    Score all active bonds in the portfolio for forfeiture risk.
    Returns sorted by priority (highest risk first).
    """
    try:
        cursor = db.active_bonds.find(
            {"status": {"$in": ["active", "monitoring", "alert"]}},
        ).sort("bond_amount", -1).limit(limit)
        bonds = await cursor.to_list(length=limit)
    except Exception as e:
        log.error(f"Failed to fetch active bonds: {e}")
        return {"success": False, "error": str(e)}

    results = []
    for bond in bonds:
        # Fetch check-ins, court dates, and docket events
        check_ins = []
        court_dates = []
        docket_events = []
        defendant = None
        try:
            did = bond.get("Defendant_ID")
            bid = str(bond.get("Bond_Case_ID") or bond.get("_id", ""))
            if did:
                defendant = await db.defendants.find_one({"Defendant_ID": did})
                ci_cursor = db.check_ins.find({"defendant_id": did}).limit(20)
                check_ins = await ci_cursor.to_list(length=20)
                cd_cursor = db.court_reminders.find({"defendant_id": did}).limit(10)
                court_dates = await cd_cursor.to_list(length=10)
            # Fetch docket events for this bond
            if bid:
                de_cursor = db.docket_events.find({"bond_case_id": bid}).limit(50)
                docket_events = await de_cursor.to_list(length=50)
        except Exception:
            pass

        scored = score_bond(bond, defendant, check_ins, court_dates, docket_events)
        results.append(scored)

    # Sort by priority (highest risk first)
    results.sort(key=lambda x: x["priority_score"], reverse=True)

    # Summary stats
    critical = sum(1 for r in results if r["risk_tier"] == "critical")
    high = sum(1 for r in results if r["risk_tier"] == "high")
    total_exposure = sum(r["bond_amount"] for r in results if r["risk_tier"] in ("critical", "high"))

    return {
        "success": True,
        "bonds_scored": len(results),
        "critical_count": critical,
        "high_risk_count": high,
        "total_at_risk_exposure": total_exposure,
        "results": results,
        "scored_at": datetime.utcnow().isoformat() + "Z",
    }
