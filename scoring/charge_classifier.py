"""
FL Statute Charge Classifier — ShamrockLeads Scoring Module

Lightweight, zero-dependency charge classification using Florida Statute lookups.
Replaces the need for heavy NLP models (Blackstone/spaCy) by mapping statute
numbers and charge keywords to:
  • Category (violent, property, drug, dui, traffic, public_order, sex, weapon, fraud, other)
  • Severity tier (capital, felony_1, felony_2, felony_3, misdemeanor_1, misdemeanor_2, infraction)
  • FTA risk signal (high-risk charges that historically correlate with FTA)
  • Disqualifying flags (capital crimes, federal holds)

Source data: Florida Statutes Title XLVI (Crimes), calibrated against
Lee County DOJ records and COMPAS recidivism analysis.
"""

import re
import logging
from typing import Optional

log = logging.getLogger("shamrock.charge_classifier")

# ═══════════════════════════════════════════════════════════════════════════════
# SEVERITY TIERS — maps to sentencing guidelines
# ═══════════════════════════════════════════════════════════════════════════════
SEVERITY_WEIGHT = {
    "capital": 10,
    "life_felony": 9,
    "felony_1": 8,
    "felony_2": 7,
    "felony_3": 6,
    "misdemeanor_1": 4,
    "misdemeanor_2": 2,
    "infraction": 1,
}

# ═══════════════════════════════════════════════════════════════════════════════
# FL STATUTE → CLASSIFICATION LOOKUP TABLE
# Format: "statute_prefix": (category, severity, fta_risk_boost, is_disqualifier)
#   fta_risk_boost: additional 0-20 points added to FTA risk for this charge type
#   is_disqualifier: True = no bond possible (capital/life/federal hold)
# ═══════════════════════════════════════════════════════════════════════════════
STATUTE_TABLE = {
    # ── Homicide / Capital ──────────────────────────────────────────────────
    "782.04":  ("violent", "capital", 0, True),       # Murder (1st degree)
    "782.051": ("violent", "capital", 0, True),       # Attempted murder
    "782.07":  ("violent", "felony_2", 10, False),    # Manslaughter
    "782.071": ("violent", "felony_1", 10, False),    # Vehicular homicide
    "782.09":  ("violent", "felony_1", 0, True),      # Killing unborn by injury to mother

    # ── Assault / Battery ──────────────────────────────────────────────────
    "784.011": ("violent", "misdemeanor_2", 5, False),  # Assault
    "784.021": ("violent", "felony_3", 10, False),      # Aggravated assault
    "784.03":  ("violent", "misdemeanor_1", 5, False),  # Battery
    "784.041": ("violent", "felony_3", 8, False),       # Felony battery
    "784.045": ("violent", "felony_2", 12, False),      # Aggravated battery
    "784.048": ("violent", "misdemeanor_1", 5, False),  # Stalking
    "784.07":  ("violent", "felony_3", 10, False),      # Battery on LEO/firefighter
    "784.074": ("violent", "felony_2", 10, False),      # Aggravated battery on LEO
    "784.08":  ("violent", "felony_3", 8, False),       # Battery on elderly

    # ── Robbery / Carjacking ───────────────────────────────────────────────
    "812.13":  ("violent", "felony_1", 15, False),  # Robbery
    "812.131": ("violent", "felony_1", 15, False),  # Robbery by sudden snatching
    "812.133": ("violent", "felony_1", 15, False),  # Carjacking

    # ── Kidnapping / False Imprisonment ─────────────────────────────────────
    "787.01":  ("violent", "felony_1", 15, False),  # Kidnapping
    "787.02":  ("violent", "felony_3", 10, False),  # False imprisonment

    # ── Sex Offenses ───────────────────────────────────────────────────────
    "794.011": ("sex", "life_felony", 0, True),     # Sexual battery
    "794.05":  ("sex", "felony_2", 5, False),       # Unlawful sexual activity with minor
    "800.04":  ("sex", "felony_2", 5, False),       # Lewd acts on child
    "810.145": ("sex", "felony_3", 5, False),       # Video voyeurism
    "847.0135": ("sex", "felony_3", 5, False),      # Computer pornography

    # ── Burglary / Trespass ────────────────────────────────────────────────
    "810.02":  ("property", "felony_2", 10, False),     # Burglary
    "810.06":  ("property", "misdemeanor_2", 3, False),  # Burglary tools
    "810.08":  ("property", "misdemeanor_1", 3, False),  # Trespass in structure
    "810.09":  ("property", "misdemeanor_1", 3, False),  # Trespass on property

    # ── Theft / Fraud ──────────────────────────────────────────────────────
    "812.014": ("property", "felony_3", 8, False),      # Theft (grand)
    "812.015": ("property", "misdemeanor_1", 5, False),  # Retail theft (shoplifting)
    "812.019": ("property", "felony_2", 8, False),      # Dealing in stolen property
    "817.034": ("fraud", "felony_2", 10, False),        # Organized fraud scheme
    "817.568": ("fraud", "felony_2", 8, False),         # Identity theft
    "831.01":  ("fraud", "felony_3", 5, False),         # Forgery
    "831.02":  ("fraud", "felony_3", 5, False),         # Uttering forged instrument
    "832.05":  ("fraud", "felony_3", 5, False),         # Bad checks

    # ── Drug Offenses ──────────────────────────────────────────────────────
    "893.13":  ("drug", "felony_3", 8, False),         # Drug possession/sale
    "893.135": ("drug", "felony_1", 12, False),        # Drug trafficking
    "893.147": ("drug", "misdemeanor_1", 3, False),    # Drug paraphernalia
    "893.149": ("drug", "felony_2", 10, False),        # Drug sale near school/park

    # ── Weapons ────────────────────────────────────────────────────────────
    "790.01":  ("weapon", "felony_3", 8, False),       # Carrying concealed weapon
    "790.07":  ("weapon", "felony_3", 10, False),      # Improper use of weapon
    "790.10":  ("weapon", "misdemeanor_1", 5, False),   # Gun on school property
    "790.19":  ("weapon", "felony_2", 10, False),      # Shooting into dwelling
    "790.221": ("weapon", "felony_2", 10, False),      # Possession of short-barreled rifle
    "790.23":  ("weapon", "felony_2", 12, False),      # Felon in possession of firearm

    # ── DUI ────────────────────────────────────────────────────────────────
    "316.193": ("dui", "misdemeanor_1", 5, False),     # DUI
    "316.1935": ("dui", "felony_3", 12, False),        # Fleeing/eluding (aggravated)
    "316.1939": ("dui", "felony_3", 10, False),        # Refusal to submit to testing (prior)

    # ── Traffic / Driving ──────────────────────────────────────────────────
    "322.03":  ("traffic", "misdemeanor_2", 3, False),  # No valid DL
    "322.34":  ("traffic", "felony_3", 8, False),       # DWLS/R (habitual)
    "316.027": ("traffic", "felony_3", 10, False),      # Hit and run (leaving scene)
    "316.191": ("traffic", "misdemeanor_1", 5, False),  # Racing on highway

    # ── Domestic / Family ──────────────────────────────────────────────────
    "741.28":  ("violent", "misdemeanor_1", 8, False),  # Domestic violence battery
    "741.31":  ("violent", "misdemeanor_1", 10, False),  # Violation of DV injunction
    "784.046": ("violent", "misdemeanor_1", 8, False),  # Stalking injunction violation

    # ── Public Order / Obstruction ─────────────────────────────────────────
    "843.01":  ("public_order", "felony_3", 5, False),     # Resisting officer with violence
    "843.02":  ("public_order", "misdemeanor_1", 3, False), # Resisting officer w/o violence
    "806.01":  ("property", "felony_2", 8, False),         # Arson
    "877.03":  ("public_order", "misdemeanor_2", 2, False), # Disorderly conduct
    "856.011": ("public_order", "misdemeanor_2", 2, False), # Disorderly intoxication
    "856.021": ("public_order", "misdemeanor_2", 2, False), # Loitering/prowling

    # ── Probation / FTA ────────────────────────────────────────────────────
    "948.06":  ("public_order", "felony_3", 15, False),     # Violation of probation
    "843.15":  ("public_order", "felony_3", 20, False),     # Failure to appear
    "901.36":  ("fraud", "misdemeanor_1", 5, False),        # False ID to LEO
}

# ═══════════════════════════════════════════════════════════════════════════════
# KEYWORD FALLBACK — for charges without parseable statute numbers
# ═══════════════════════════════════════════════════════════════════════════════
KEYWORD_MAP = {
    # Violent
    "murder": ("violent", "capital", 0, True),
    "homicide": ("violent", "capital", 0, True),
    "manslaughter": ("violent", "felony_2", 10, False),
    "aggravated assault": ("violent", "felony_3", 10, False),
    "aggravated battery": ("violent", "felony_2", 12, False),
    "armed robbery": ("violent", "felony_1", 15, False),
    "robbery": ("violent", "felony_1", 15, False),
    "carjacking": ("violent", "felony_1", 15, False),
    "kidnapping": ("violent", "felony_1", 15, False),
    "battery": ("violent", "misdemeanor_1", 5, False),
    "assault": ("violent", "misdemeanor_2", 5, False),
    "stalking": ("violent", "misdemeanor_1", 5, False),
    "domestic violence": ("violent", "misdemeanor_1", 8, False),
    "domestic battery": ("violent", "misdemeanor_1", 8, False),

    # Sex
    "sexual battery": ("sex", "life_felony", 0, True),
    "lewd": ("sex", "felony_2", 5, False),
    "molestation": ("sex", "felony_2", 5, False),

    # Property
    "burglary": ("property", "felony_2", 10, False),
    "grand theft": ("property", "felony_3", 8, False),
    "petit theft": ("property", "misdemeanor_1", 3, False),
    "shoplifting": ("property", "misdemeanor_1", 5, False),
    "retail theft": ("property", "misdemeanor_1", 5, False),
    "trespass": ("property", "misdemeanor_1", 3, False),
    "stolen property": ("property", "felony_2", 8, False),
    "arson": ("property", "felony_2", 8, False),

    # Drug
    "trafficking": ("drug", "felony_1", 12, False),
    "poss": ("drug", "felony_3", 5, False),
    "possession": ("drug", "felony_3", 5, False),
    "paraphernalia": ("drug", "misdemeanor_1", 3, False),
    "marijuana": ("drug", "misdemeanor_1", 3, False),
    "cocaine": ("drug", "felony_3", 8, False),
    "fentanyl": ("drug", "felony_1", 12, False),
    "methamphetamine": ("drug", "felony_2", 10, False),
    "heroin": ("drug", "felony_2", 10, False),
    "controlled substance": ("drug", "felony_3", 8, False),

    # DUI
    "dui": ("dui", "misdemeanor_1", 5, False),
    "driving under": ("dui", "misdemeanor_1", 5, False),
    "felony dui": ("dui", "felony_3", 10, False),

    # Traffic
    "fleeing": ("traffic", "felony_3", 12, False),
    "eluding": ("traffic", "felony_3", 12, False),
    "hit and run": ("traffic", "felony_3", 10, False),
    "leaving scene": ("traffic", "felony_3", 10, False),
    "dwls": ("traffic", "misdemeanor_2", 3, False),
    "no valid": ("traffic", "misdemeanor_2", 3, False),

    # Weapon
    "firearm": ("weapon", "felony_2", 10, False),
    "concealed weapon": ("weapon", "felony_3", 8, False),
    "weapon": ("weapon", "felony_3", 8, False),
    "felon in possession": ("weapon", "felony_2", 12, False),

    # Fraud
    "fraud": ("fraud", "felony_2", 8, False),
    "forgery": ("fraud", "felony_3", 5, False),
    "identity theft": ("fraud", "felony_2", 8, False),
    "uttering": ("fraud", "felony_3", 5, False),
    "bad check": ("fraud", "felony_3", 5, False),

    # Public order / FTA / VOP
    "failure to appear": ("public_order", "felony_3", 20, False),
    "fta": ("public_order", "felony_3", 20, False),
    "violation of probation": ("public_order", "felony_3", 15, False),
    "vop": ("public_order", "felony_3", 15, False),
    "resisting": ("public_order", "misdemeanor_1", 3, False),
    "disorderly": ("public_order", "misdemeanor_2", 2, False),
    "trespass": ("public_order", "misdemeanor_1", 3, False),

    # Disqualifiers
    "federal": ("other", "felony_1", 0, True),
    "no bond": ("other", "capital", 0, True),
    "hold": ("other", "capital", 0, True),
}

# ── Statute regex patterns ─────────────────────────────────────────────────
# Matches patterns like "782.04", "893.13(6)(a)", "F.S. 812.014"
_STATUTE_RE = re.compile(r"(\d{3}\.\d{2,3})")


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

class ChargeClassification:
    """Result of classifying a charge string."""

    __slots__ = ("category", "severity", "severity_weight", "fta_risk_boost",
                 "is_disqualifier", "matched_statute", "matched_keyword", "raw_input")

    def __init__(self, category: str, severity: str, fta_risk_boost: int,
                 is_disqualifier: bool, matched_statute: Optional[str] = None,
                 matched_keyword: Optional[str] = None, raw_input: str = ""):
        self.category = category
        self.severity = severity
        self.severity_weight = SEVERITY_WEIGHT.get(severity, 3)
        self.fta_risk_boost = fta_risk_boost
        self.is_disqualifier = is_disqualifier
        self.matched_statute = matched_statute
        self.matched_keyword = matched_keyword
        self.raw_input = raw_input

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "severity": self.severity,
            "severity_weight": self.severity_weight,
            "fta_risk_boost": self.fta_risk_boost,
            "is_disqualifier": self.is_disqualifier,
            "matched_statute": self.matched_statute,
            "matched_keyword": self.matched_keyword,
        }

    def __repr__(self):
        src = self.matched_statute or self.matched_keyword or "unknown"
        return f"<Charge: {self.category}/{self.severity} fta+{self.fta_risk_boost} [{src}]>"


def classify_charge(charge_text: str) -> ChargeClassification:
    """
    Classify a single charge string by FL statute or keyword lookup.

    Args:
        charge_text: Raw charge description (e.g., "BATTERY DOM VIOLENCE 784.03")

    Returns:
        ChargeClassification with category, severity, FTA risk boost, etc.
    """
    if not charge_text:
        return ChargeClassification("other", "misdemeanor_2", 0, False, raw_input="")

    text = charge_text.strip()
    text_lower = text.lower()

    # ── Strategy 1: Extract statute number and look up ──────────────────────
    statute_match = _STATUTE_RE.search(text)
    if statute_match:
        statute = statute_match.group(1)
        # Try exact match first, then prefix match (e.g., "893.13" matches "893.13(6)(a)")
        entry = STATUTE_TABLE.get(statute)
        if entry:
            return ChargeClassification(
                category=entry[0], severity=entry[1], fta_risk_boost=entry[2],
                is_disqualifier=entry[3], matched_statute=statute, raw_input=text,
            )
        # Try truncated (first 6 chars) for statutes like 893.135 → 893.13
        trunc = statute[:6]
        entry = STATUTE_TABLE.get(trunc)
        if entry:
            return ChargeClassification(
                category=entry[0], severity=entry[1], fta_risk_boost=entry[2],
                is_disqualifier=entry[3], matched_statute=trunc, raw_input=text,
            )

    # ── Strategy 2: Keyword fallback (longest match first) ──────────────────
    # Sort by length descending so "aggravated battery" matches before "battery"
    for keyword in sorted(KEYWORD_MAP.keys(), key=len, reverse=True):
        if keyword in text_lower:
            entry = KEYWORD_MAP[keyword]
            return ChargeClassification(
                category=entry[0], severity=entry[1], fta_risk_boost=entry[2],
                is_disqualifier=entry[3], matched_keyword=keyword, raw_input=text,
            )

    # ── Strategy 3: Default ─────────────────────────────────────────────────
    return ChargeClassification("other", "misdemeanor_2", 0, False, raw_input=text)


def classify_charges(charges_text: str, delimiter: str = ";") -> list[ChargeClassification]:
    """
    Classify multiple charges from a delimited string.

    Args:
        charges_text: Charge descriptions separated by delimiter
        delimiter: Separator character (default ";")

    Returns:
        List of ChargeClassification objects, one per charge
    """
    if not charges_text:
        return []

    # Split on common delimiters: semicolons, " / ", " | "
    parts = re.split(r"[;/|]", charges_text)
    return [classify_charge(p.strip()) for p in parts if p.strip()]


def get_charge_summary(charges_text: str) -> dict:
    """
    Get an aggregated summary of all charges for scoring and display.

    Returns:
        dict with: max_severity_weight, total_fta_boost, categories, has_disqualifier,
                   charge_count, classifications
    """
    classifications = classify_charges(charges_text)

    if not classifications:
        return {
            "max_severity_weight": 3,
            "total_fta_boost": 0,
            "categories": [],
            "has_disqualifier": False,
            "charge_count": 0,
            "classifications": [],
        }

    return {
        "max_severity_weight": max(c.severity_weight for c in classifications),
        "total_fta_boost": sum(c.fta_risk_boost for c in classifications),
        "categories": list(set(c.category for c in classifications)),
        "has_disqualifier": any(c.is_disqualifier for c in classifications),
        "charge_count": len(classifications),
        "classifications": [c.to_dict() for c in classifications],
    }
