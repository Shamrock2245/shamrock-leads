"""
Legal NLP Service — ShamrockLeads Intelligence Suite
=====================================================
Provides legal text analysis using lightweight NLP techniques:
  • Named Entity Recognition for legal entities (judges, courts, statutes)
  • Charge classification and severity scoring
  • Citation extraction and normalization
  • Risk factor extraction from unstructured text
  • Florida Statute parsing and categorization

Uses regex-based + dictionary-based approaches (no heavy spaCy dependency).
Designed to run on M0-tier infrastructure within Docker.
"""
import re, logging
from typing import List, Optional
from datetime import datetime, timezone

log = logging.getLogger("shamrock.legal_nlp")

# ── Florida Statute Severity Classification ──────────────────────────────────
FL_CHARGE_SEVERITY = {
    # Capital / Life felonies
    "murder": {"severity": "capital", "level": 10, "fta_weight": 0.30},
    "homicide": {"severity": "capital", "level": 10, "fta_weight": 0.30},
    "capital sexual battery": {"severity": "capital", "level": 10, "fta_weight": 0.25},
    # First-degree felonies
    "trafficking": {"severity": "felony_1", "level": 9, "fta_weight": 0.25},
    "armed robbery": {"severity": "felony_1", "level": 9, "fta_weight": 0.22},
    "carjacking": {"severity": "felony_1", "level": 9, "fta_weight": 0.22},
    "kidnapping": {"severity": "felony_1", "level": 9, "fta_weight": 0.22},
    "aggravated battery": {"severity": "felony_1", "level": 8, "fta_weight": 0.18},
    "sexual battery": {"severity": "felony_1", "level": 9, "fta_weight": 0.20},
    "arson": {"severity": "felony_1", "level": 8, "fta_weight": 0.15},
    # Second-degree felonies
    "robbery": {"severity": "felony_2", "level": 7, "fta_weight": 0.18},
    "burglary": {"severity": "felony_2", "level": 7, "fta_weight": 0.16},
    "aggravated assault": {"severity": "felony_2", "level": 7, "fta_weight": 0.15},
    "grand theft auto": {"severity": "felony_2", "level": 7, "fta_weight": 0.14},
    "fleeing and eluding": {"severity": "felony_2", "level": 7, "fta_weight": 0.20},
    # Third-degree felonies
    "grand theft": {"severity": "felony_3", "level": 5, "fta_weight": 0.12},
    "battery": {"severity": "felony_3", "level": 5, "fta_weight": 0.12},
    "possession of controlled substance": {"severity": "felony_3", "level": 5, "fta_weight": 0.10},
    "possession of firearm by felon": {"severity": "felony_3", "level": 6, "fta_weight": 0.15},
    "fraud": {"severity": "felony_3", "level": 5, "fta_weight": 0.10},
    "forgery": {"severity": "felony_3", "level": 5, "fta_weight": 0.10},
    "dui manslaughter": {"severity": "felony_2", "level": 8, "fta_weight": 0.18},
    # Misdemeanors
    "petit theft": {"severity": "misdemeanor_1", "level": 2, "fta_weight": 0.08},
    "simple battery": {"severity": "misdemeanor_1", "level": 2, "fta_weight": 0.08},
    "disorderly conduct": {"severity": "misdemeanor_2", "level": 1, "fta_weight": 0.05},
    "trespass": {"severity": "misdemeanor_1", "level": 2, "fta_weight": 0.06},
    "dui": {"severity": "misdemeanor_1", "level": 3, "fta_weight": 0.10},
    "driving while suspended": {"severity": "misdemeanor_1", "level": 2, "fta_weight": 0.08},
    "domestic violence": {"severity": "misdemeanor_1", "level": 3, "fta_weight": 0.12},
    "violation of probation": {"severity": "varies", "level": 4, "fta_weight": 0.18},
    "failure to appear": {"severity": "varies", "level": 5, "fta_weight": 0.30},
    "contempt of court": {"severity": "varies", "level": 4, "fta_weight": 0.20},
}

# ── FTA Risk Indicators (COMPAS-inspired features) ──────────────────────────
FTA_RISK_KEYWORDS = {
    "high": ["failure to appear", "fta", "fugitive", "absconded", "fleeing",
             "bench warrant", "capias", "bond revoked", "bond forfeited"],
    "medium": ["violation of probation", "vop", "contempt", "no show",
               "driving while suspended", "dwls", "habitual offender"],
    "low": ["first offense", "no priors", "cooperative"],
}

# ── Florida Statute Pattern ──────────────────────────────────────────────────
FL_STATUTE_RE = re.compile(
    r"(?:F\.?S\.?|Fla\.?\s*Stat\.?)\s*[§]?\s*(\d{3,4})\.(\d{1,4})(?:\((\d+)\))?",
    re.IGNORECASE
)
GENERIC_STATUTE_RE = re.compile(
    r"(?:§|Section)\s*(\d{3,4})\.(\d{1,4})(?:\(([a-zA-Z0-9]+)\))?",
    re.IGNORECASE
)

# ── Legal Citation Pattern (eyecite-lite) ────────────────────────────────────
CASE_CITE_RE = re.compile(
    r"(\d+)\s+(So\.?\s*(?:2d|3d)?|F\.?\s*(?:2d|3d|4th)?|"
    r"Fla\.?\s*(?:App\.?)?|U\.S\.|S\.Ct\.)\s+(\d+)",
    re.IGNORECASE
)
FL_CASE_RE = re.compile(
    r"(\d{4})-?([A-Z]{2})-?(\d{4,6})",
    re.IGNORECASE
)


def analyze_charges(charge_text: str) -> dict:
    """Analyze charge text for severity, classification, and risk factors."""
    if not charge_text:
        return {"charges": [], "max_severity": "unknown", "severity_level": 0,
                "fta_risk_score": 0.0, "risk_factors": [], "statutes": []}

    charges_lower = charge_text.lower()
    found_charges = []
    max_level = 0
    max_severity = "unknown"
    total_fta = 0.0
    risk_factors = []

    # Match against known charge types
    for charge_key, info in FL_CHARGE_SEVERITY.items():
        if charge_key in charges_lower:
            found_charges.append({
                "charge": charge_key,
                "severity": info["severity"],
                "level": info["level"],
                "fta_weight": info["fta_weight"],
            })
            if info["level"] > max_level:
                max_level = info["level"]
                max_severity = info["severity"]
            total_fta = max(total_fta, info["fta_weight"])

    # FTA risk keyword scan
    for level, keywords in FTA_RISK_KEYWORDS.items():
        for kw in keywords:
            if kw in charges_lower:
                risk_factors.append({"keyword": kw, "risk_level": level})
                if level == "high":
                    total_fta = max(total_fta, 0.25)
                elif level == "medium":
                    total_fta = max(total_fta, 0.15)

    # Extract Florida statutes
    statutes = []
    for m in FL_STATUTE_RE.finditer(charge_text):
        statutes.append({
            "chapter": m.group(1), "section": m.group(2),
            "subsection": m.group(3) or "",
            "full": m.group(0),
        })
    for m in GENERIC_STATUTE_RE.finditer(charge_text):
        stat = {"chapter": m.group(1), "section": m.group(2),
                "subsection": m.group(3) or "", "full": m.group(0)}
        if stat not in statutes:
            statutes.append(stat)

    # Domestic violence enhancement
    if any(kw in charges_lower for kw in ["domestic", "dv", "injunction"]):
        risk_factors.append({"keyword": "domestic_violence_flag", "risk_level": "medium"})
        total_fta = max(total_fta, 0.12)

    # Multiple charges multiplier
    charge_count = len(found_charges)
    if charge_count >= 3:
        total_fta = min(0.95, total_fta * 1.3)
        risk_factors.append({"keyword": f"{charge_count}_charges_multi", "risk_level": "medium"})

    return {
        "charges": found_charges,
        "max_severity": max_severity,
        "severity_level": max_level,
        "fta_risk_score": round(min(0.95, total_fta), 3),
        "risk_factors": risk_factors,
        "statutes": statutes,
        "charge_count": charge_count,
    }


def extract_citations(text: str) -> List[dict]:
    """Extract legal citations from text (eyecite-lite)."""
    if not text:
        return []
    citations = []
    for m in CASE_CITE_RE.finditer(text):
        citations.append({
            "volume": m.group(1), "reporter": m.group(2).strip(),
            "page": m.group(3), "full": m.group(0), "type": "case_citation",
        })
    for m in FL_CASE_RE.finditer(text):
        citations.append({
            "year": m.group(1), "type_code": m.group(2),
            "number": m.group(3), "full": m.group(0), "type": "fl_case_number",
        })
    for m in FL_STATUTE_RE.finditer(text):
        citations.append({
            "chapter": m.group(1), "section": m.group(2),
            "subsection": m.group(3) or "", "full": m.group(0), "type": "fl_statute",
        })
    return citations


def extract_legal_entities(text: str) -> dict:
    """Extract named entities from legal text (Blackstone-lite NER)."""
    if not text:
        return {"judges": [], "courts": [], "attorneys": [], "provisions": []}

    judges = list(set(re.findall(
        r"(?:Judge|Hon\.|Honorable|Justice)\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)",
        text
    )))
    courts = list(set(re.findall(
        r"(\d+(?:st|nd|rd|th)\s+(?:Circuit|District|Judicial)\s+Court"
        r"|(?:Circuit|County|District)\s+Court\s+(?:of|in|for)\s+[A-Z][a-z]+(?:\s+County)?)",
        text
    )))
    attorneys = list(set(re.findall(
        r"(?:Attorney|Counsel|Esq\.?|(?:Public|State)\s+Defender)\s*:?\s*([A-Z][a-z]+\s+[A-Z][a-z]+)",
        text
    )))
    provisions = [m.group(0) for m in FL_STATUTE_RE.finditer(text)]

    return {
        "judges": judges, "courts": courts,
        "attorneys": attorneys, "provisions": provisions,
    }


def compute_recidivism_risk(arrest_history: List[dict], current_charges: str = "") -> dict:
    """
    COMPAS-inspired recidivism/FTA risk scoring.
    Uses arrest count, charge escalation, time between arrests, and FTA history.
    """
    if not arrest_history:
        charge_analysis = analyze_charges(current_charges) if current_charges else {}
        return {
            "recidivism_score": 0, "fta_score": charge_analysis.get("fta_risk_score", 0) * 100,
            "risk_tier": "low", "factors": ["first_known_arrest"],
            "prior_count": 0,
        }

    now = datetime.now(timezone.utc)
    prior_count = len(arrest_history)
    factors = []
    recid_score = 0.0
    fta_score = 0.0

    # Factor 1: Prior arrest count
    if prior_count >= 5:
        recid_score += 30
        factors.append(f"high_prior_count_{prior_count}")
    elif prior_count >= 3:
        recid_score += 20
        factors.append(f"moderate_prior_count_{prior_count}")
    elif prior_count >= 1:
        recid_score += 10
        factors.append(f"prior_arrests_{prior_count}")

    # Factor 2: Recency of last arrest
    dates = []
    for a in arrest_history:
        d = a.get("booking_date") or a.get("scraped_at") or a.get("created_at")
        if d:
            if isinstance(d, str):
                try:
                    d = datetime.fromisoformat(d.replace("Z", "+00:00").replace("+00:00", ""))
                except (ValueError, TypeError):
                    continue
            dates.append(d)

    if dates:
        dates.sort(reverse=True)
        days_since_last = (now - dates[0]).days
        if days_since_last < 30:
            recid_score += 20
            factors.append("arrested_within_30_days")
        elif days_since_last < 90:
            recid_score += 15
            factors.append("arrested_within_90_days")
        elif days_since_last < 180:
            recid_score += 8
            factors.append("arrested_within_6_months")

        # Factor 3: Arrest velocity (arrests per year)
        if len(dates) >= 2:
            span = (dates[0] - dates[-1]).days or 1
            velocity = (len(dates) / span) * 365
            if velocity > 4:
                recid_score += 15
                factors.append(f"high_velocity_{velocity:.1f}/yr")
            elif velocity > 2:
                recid_score += 8
                factors.append(f"moderate_velocity_{velocity:.1f}/yr")

    # Factor 4: Charge escalation
    severities = []
    for a in arrest_history:
        ch = a.get("charges", "")
        analysis = analyze_charges(ch)
        severities.append(analysis["severity_level"])

    if len(severities) >= 2 and severities[-1] > max(severities[:-1]):
        recid_score += 10
        factors.append("charge_escalation")

    # Factor 5: FTA history
    fta_count = sum(1 for a in arrest_history
                    if "failure to appear" in (a.get("charges", "") or "").lower()
                    or "fta" in (a.get("charges", "") or "").lower())
    if fta_count >= 2:
        fta_score += 40
        factors.append(f"multiple_fta_history_{fta_count}")
    elif fta_count == 1:
        fta_score += 25
        factors.append("prior_fta")

    # Factor 6: Current charge analysis
    if current_charges:
        analysis = analyze_charges(current_charges)
        fta_score += analysis.get("fta_risk_score", 0) * 50
        recid_score += analysis.get("severity_level", 0) * 2

    # Composite
    recid_score = min(100, int(recid_score))
    fta_score = min(100, int(fta_score))
    combined = (recid_score * 0.6 + fta_score * 0.4)

    if combined >= 70:
        tier = "critical"
    elif combined >= 50:
        tier = "high"
    elif combined >= 30:
        tier = "medium"
    else:
        tier = "low"

    return {
        "recidivism_score": recid_score,
        "fta_score": int(fta_score),
        "combined_risk": round(combined, 1),
        "risk_tier": tier,
        "factors": factors,
        "prior_count": prior_count,
        "fta_history_count": fta_count,
        "scored_at": now.isoformat() + "Z",
    }
