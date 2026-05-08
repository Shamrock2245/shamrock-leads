"""
ShamrockLeads — ML Feature Engineering Pipeline
=================================================
Extracts structured ML features from raw ArrestRecord data for model training
and inference. Converts messy jail roster text into clean numeric feature vectors.

Feature Categories:
  1. Financial — Bond amount, premium estimate, bond tier
  2. Legal — Charge severity, charge count, bond type
  3. Temporal — Time of day, day of week, weekend, age at arrest
  4. Geographic — County encoding, region classification
  5. Behavioral — Prior arrests, data completeness, custody status
  6. Textual — Charge keyword signals (violence, drugs, property, DUI)

All features are designed to be fast (no I/O) and deterministic.
The MongoDB-dependent features (prior_arrest_count, has_active_bond) are
injected by the training pipeline, not computed here.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import logging

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

# Florida counties grouped by judicial circuit for region encoding
SWFL_COUNTIES = {"lee", "collier", "charlotte", "hendry", "glades", "desoto"}
CENTRAL_FL_COUNTIES = {"orange", "osceola", "seminole", "brevard", "volusia", "lake", "sumter"}
SOUTH_FL_COUNTIES = {"miami-dade", "broward", "palm beach", "martin", "st. lucie", "indian river"}
TAMPA_BAY_COUNTIES = {"hillsborough", "pinellas", "pasco", "manatee", "sarasota", "polk"}
NORTH_FL_COUNTIES = {"duval", "clay", "st. johns", "nassau", "baker", "alachua", "marion", "putnam"}
PANHANDLE_COUNTIES = {"escambia", "santa rosa", "okaloosa", "walton", "bay", "jackson", "leon", "gadsden"}

# Charge keyword categories for feature extraction
VIOLENCE_KEYWORDS = [
    "murder", "homicide", "manslaughter", "assault", "battery", "robbery",
    "kidnapping", "carjacking", "stalking", "strangulation", "domestic",
    "weapon", "firearm", "gun", "knife", "armed", "aggravated",
]
DRUG_KEYWORDS = [
    "trafficking", "cocaine", "heroin", "fentanyl", "methamphetamine",
    "marijuana", "cannabis", "possession", "controlled substance",
    "drug paraphernalia", "delivery of", "manufacture",
]
PROPERTY_KEYWORDS = [
    "burglary", "theft", "larceny", "shoplifting", "fraud", "forgery",
    "identity theft", "grand theft", "petit theft", "stolen property",
    "criminal mischief", "arson", "vandalism",
]
DUI_KEYWORDS = [
    "dui", "driving under the influence", "dwi", "impaired driving",
    "intoxicated", "bac", "breathalyzer", "refusal to submit",
]
FLIGHT_RISK_KEYWORDS = [
    "fugitive", "flee", "escape", "fta", "failure to appear",
    "violation of probation", "vop", "absconder", "warrant",
]
CAPITAL_KEYWORDS = [
    "capital", "first degree murder", "1st degree murder",
    "sexual battery", "lewd and lascivious",
]

# Bond type mappings
BONDABLE_TYPES = {"cash", "surety", "cash or surety", "monetary"}
NON_BONDABLE_TYPES = {"no bond", "hold", "remand", "no bail"}
ROR_TYPES = {"ror", "r.o.r", "released on recognizance", "own recognizance", "pr bond"}


# ─────────────────────────────────────────────────────────────────────────────
#  Feature Extraction Functions
# ─────────────────────────────────────────────────────────────────────────────

def extract_features(record: Dict[str, Any], enrichment: Optional[Dict] = None) -> Dict[str, float]:
    """Extract a complete ML feature vector from an arrest record.

    Args:
        record: MongoDB arrest document (or ArrestRecord-like dict)
        enrichment: Optional dict with pre-computed fields:
            - prior_arrest_count: int
            - has_active_bond: bool
            - prior_fta_count: int
            - days_since_last_arrest: int

    Returns:
        Dict mapping feature names to numeric values (all floats).
    """
    features = {}

    # ── 1. Financial Features ────────────────────────────────────────────────
    bond_amount = _parse_bond_amount(record.get("bond_amount") or record.get("Bond_Amount", ""))
    features["bond_amount_raw"] = bond_amount
    features["bond_amount_log"] = _safe_log(bond_amount)
    features["bond_tier"] = _bond_tier(bond_amount)
    features["premium_estimate"] = bond_amount * 0.10  # Standard 10% premium

    # ── 2. Legal Features ────────────────────────────────────────────────────
    charges_text = record.get("charges") or record.get("Charges", "")
    bond_type = record.get("bond_type") or record.get("Bond_Type", "")

    features["charge_count"] = _count_charges(charges_text)
    features["charge_severity_max"] = _max_charge_severity(charges_text)
    features["has_violence_charge"] = float(_has_keyword(charges_text, VIOLENCE_KEYWORDS))
    features["has_drug_charge"] = float(_has_keyword(charges_text, DRUG_KEYWORDS))
    features["has_property_charge"] = float(_has_keyword(charges_text, PROPERTY_KEYWORDS))
    features["has_dui_charge"] = float(_has_keyword(charges_text, DUI_KEYWORDS))
    features["has_flight_risk_charge"] = float(_has_keyword(charges_text, FLIGHT_RISK_KEYWORDS))
    features["has_capital_charge"] = float(_has_keyword(charges_text, CAPITAL_KEYWORDS))
    features["bond_type_encoded"] = _encode_bond_type(bond_type)

    # Felony classification from Florida charge codes (F1, F2, F3, M1, M2)
    features["felony_degree"] = _extract_felony_degree(charges_text)
    features["misdemeanor_only"] = float(
        features["felony_degree"] == 0 and features["charge_severity_max"] <= 2
    )

    # ── 3. Temporal Features ─────────────────────────────────────────────────
    booking_date = record.get("booking_date") or record.get("Booking_Date_Formatted", "")
    scraped_at = record.get("scraped_at", "")

    dt = _parse_datetime(booking_date) or _parse_datetime(scraped_at)
    if dt:
        features["hour_of_day"] = float(dt.hour)
        features["day_of_week"] = float(dt.weekday())  # 0=Mon, 6=Sun
        features["is_weekend"] = float(dt.weekday() >= 5)
        features["is_night"] = float(dt.hour >= 22 or dt.hour < 6)
    else:
        features["hour_of_day"] = 12.0
        features["day_of_week"] = 3.0
        features["is_weekend"] = 0.0
        features["is_night"] = 0.0

    # Age at arrest (if DOB available)
    dob = record.get("dob") or record.get("DOB", "")
    features["age_at_arrest"] = _compute_age(dob, dt)

    # ── 4. Geographic Features ───────────────────────────────────────────────
    county = (record.get("county") or record.get("County", "")).lower().strip()
    features["county_encoded"] = _encode_county(county)
    features["region_encoded"] = _encode_region(county)
    features["is_swfl"] = float(county in SWFL_COUNTIES)

    # ── 5. Behavioral Features ───────────────────────────────────────────────
    status = record.get("status") or record.get("Status", "")
    features["in_custody"] = float(_is_in_custody(status))
    features["released"] = float(_is_released(status))
    features["data_completeness"] = _data_completeness_score(record)

    # ── 6. Enrichment Features (from DB lookups, injected) ───────────────────
    if enrichment:
        features["prior_arrest_count"] = float(enrichment.get("prior_arrest_count", 0))
        features["has_active_bond"] = float(enrichment.get("has_active_bond", False))
        features["prior_fta_count"] = float(enrichment.get("prior_fta_count", 0))
        features["days_since_last_arrest"] = float(enrichment.get("days_since_last_arrest", 9999))
        features["prior_bond_total"] = float(enrichment.get("prior_bond_total", 0))
    else:
        features["prior_arrest_count"] = 0.0
        features["has_active_bond"] = 0.0
        features["prior_fta_count"] = 0.0
        features["days_since_last_arrest"] = 9999.0
        features["prior_bond_total"] = 0.0

    return features


def get_feature_names() -> List[str]:
    """Return ordered list of all feature names for model training.
    Must match the output keys of extract_features().
    """
    return [
        # Financial
        "bond_amount_raw", "bond_amount_log", "bond_tier", "premium_estimate",
        # Legal
        "charge_count", "charge_severity_max", "has_violence_charge",
        "has_drug_charge", "has_property_charge", "has_dui_charge",
        "has_flight_risk_charge", "has_capital_charge", "bond_type_encoded",
        "felony_degree", "misdemeanor_only",
        # Temporal
        "hour_of_day", "day_of_week", "is_weekend", "is_night", "age_at_arrest",
        # Geographic
        "county_encoded", "region_encoded", "is_swfl",
        # Behavioral
        "in_custody", "released", "data_completeness",
        # Enrichment
        "prior_arrest_count", "has_active_bond", "prior_fta_count",
        "days_since_last_arrest", "prior_bond_total",
    ]


# ─────────────────────────────────────────────────────────────────────────────
#  Internal Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_bond_amount(raw: str) -> float:
    """Parse bond amount string to numeric, with $5M sanity cap."""
    if not raw or not str(raw).strip():
        return 0.0
    cleaned = str(raw).strip().upper()
    if any(t in cleaned for t in ["NO BOND", "NONE", "N/A", "HOLD"]):
        return 0.0
    cleaned = re.sub(r'[$,\s]', '', cleaned)
    try:
        val = float(cleaned)
        return min(val, 5_000_000)  # Sanity cap
    except (ValueError, TypeError):
        return 0.0


def _safe_log(value: float) -> float:
    """Log-transform with floor at 0."""
    import math
    return math.log1p(max(value, 0))


def _bond_tier(amount: float) -> float:
    """Encode bond amount into tier (0-5)."""
    if amount <= 0:
        return 0.0
    if amount < 500:
        return 1.0
    if amount <= 5_000:
        return 2.0
    if amount <= 25_000:
        return 3.0
    if amount <= 100_000:
        return 4.0
    return 5.0


def _count_charges(charges_text: str) -> float:
    """Count number of distinct charges."""
    if not charges_text:
        return 0.0
    parts = re.split(r'[;\n,]', charges_text)
    return float(len([p for p in parts if p.strip()]))


def _max_charge_severity(charges_text: str) -> float:
    """Classify highest severity charge (0=none, 1=infraction, 2=misdemeanor, 3=felony, 4=capital)."""
    if not charges_text:
        return 0.0
    text = charges_text.lower()
    if _has_keyword(text, CAPITAL_KEYWORDS):
        return 4.0
    if _has_keyword(text, VIOLENCE_KEYWORDS + DRUG_KEYWORDS) or re.search(r'\bF[1-3]\b|\bFC\b', charges_text):
        return 3.0
    if re.search(r'\bM[12]\b', charges_text) or _has_keyword(text, ["misdemeanor", "dui", "battery", "assault"]):
        return 2.0
    if charges_text.strip():
        return 1.0
    return 0.0


def _has_keyword(text: str, keywords: list) -> bool:
    """Check if any keyword appears in text (case-insensitive)."""
    if not text:
        return False
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)


def _encode_bond_type(bond_type: str) -> float:
    """Encode bond type: -1=non-bondable, 0=unknown/ROR, 1=bondable."""
    if not bond_type:
        return 0.0
    bt = bond_type.lower().strip()
    if any(t in bt for t in NON_BONDABLE_TYPES):
        return -1.0
    if any(t in bt for t in ROR_TYPES):
        return 0.0
    if any(t in bt for t in BONDABLE_TYPES):
        return 1.0
    return 0.0


def _extract_felony_degree(charges_text: str) -> float:
    """Extract highest felony degree from Florida charge codes."""
    if not charges_text:
        return 0.0
    # Capital felony
    if re.search(r'\bFC\b', charges_text):
        return 5.0
    # Life felony
    if re.search(r'\bFL\b', charges_text) or "life felony" in charges_text.lower():
        return 4.5
    # Degree felonies (F1 > F2 > F3)
    if re.search(r'\bF1\b', charges_text):
        return 4.0
    if re.search(r'\bF2\b', charges_text):
        return 3.0
    if re.search(r'\bF3\b', charges_text):
        return 2.0
    # Misdemeanors
    if re.search(r'\bM1\b', charges_text):
        return 1.0
    if re.search(r'\bM2\b', charges_text):
        return 0.5
    return 0.0


def _parse_datetime(raw: str) -> Optional[datetime]:
    """Parse various datetime formats."""
    if not raw:
        return None
    for fmt in [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S.%f+00:00",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
        "%Y-%m-%d",
    ]:
        try:
            return datetime.strptime(raw[:len(fmt) + 5], fmt)
        except (ValueError, IndexError):
            continue
    return None


def _compute_age(dob_raw: str, ref_date: Optional[datetime] = None) -> float:
    """Compute age at arrest from DOB string."""
    if not dob_raw:
        return 30.0  # Default imputation
    dt = _parse_datetime(dob_raw)
    if not dt:
        # Try digits-only (MMDDYYYY or YYYYMMDD)
        digits = re.sub(r'\D', '', dob_raw)
        if len(digits) == 8:
            try:
                if int(digits[:4]) > 1900:
                    dt = datetime(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
                else:
                    dt = datetime(int(digits[4:8]), int(digits[:2]), int(digits[2:4]))
            except ValueError:
                pass
    if not dt:
        return 30.0

    ref = ref_date or datetime.now(timezone.utc)
    if dt.tzinfo:
        ref = ref.replace(tzinfo=timezone.utc) if not ref.tzinfo else ref
        dt = dt.replace(tzinfo=timezone.utc) if not dt.tzinfo else dt
    age = (ref - dt).days / 365.25
    return max(0, min(age, 120))  # Sanity bounds


def _encode_county(county: str) -> float:
    """Encode county to a stable numeric hash (0-66 for FL)."""
    if not county:
        return 0.0
    # Use a simple hash mod for consistent encoding
    return float(hash(county.lower().strip()) % 67)


def _encode_region(county: str) -> float:
    """Encode county into region (0-5)."""
    c = county.lower().strip()
    if c in SWFL_COUNTIES:
        return 1.0
    if c in CENTRAL_FL_COUNTIES:
        return 2.0
    if c in SOUTH_FL_COUNTIES:
        return 3.0
    if c in TAMPA_BAY_COUNTIES:
        return 4.0
    if c in NORTH_FL_COUNTIES:
        return 5.0
    if c in PANHANDLE_COUNTIES:
        return 6.0
    return 0.0


def _is_in_custody(status: str) -> bool:
    """Check if defendant is in custody."""
    if not status:
        return False
    s = status.upper()
    return "IN CUSTODY" in s or "INCUSTODY" in s or "DETAINED" in s


def _is_released(status: str) -> bool:
    """Check if defendant has been released."""
    if not status:
        return False
    return "RELEASED" in status.upper()


def _data_completeness_score(record: dict) -> float:
    """Score 0.0-1.0 for how complete the arrest record data is."""
    fields = [
        "full_name", "Full_Name",
        "charges", "Charges",
        "bond_amount", "Bond_Amount",
        "county", "County",
        "dob", "DOB",
        "booking_date", "Booking_Date_Formatted",
        "bond_type", "Bond_Type",
        "status", "Status",
    ]
    # Deduplicate by checking both MongoDB and ArrestRecord field names
    checked = set()
    present = 0
    total = 0
    for f in fields:
        base = f.lower().replace("_", "")
        if base in checked:
            continue
        checked.add(base)
        total += 1
        val = record.get(f)
        if val and str(val).strip():
            present += 1

    return round(present / total, 2) if total > 0 else 0.0
