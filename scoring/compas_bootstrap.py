"""
ShamrockLeads — COMPAS Bootstrap Training Data Generator
==========================================================
Downloads ProPublica's Broward County pretrial risk assessment dataset and
maps it to our ArrestRecord feature schema for FTA risk model bootstrapping.

This solves the cold-start problem: we need hundreds of labeled FTA outcomes
to train a useful model, but our internal bonded case history is sparse.
COMPAS provides 7,214 real pretrial defendants with 2-year recidivism outcomes.

Feature Mapping Strategy:
  COMPAS Field           → ArrestRecord Feature
  ──────────────────────────────────────────────
  priors_count           → prior_arrest_count
  age / age_cat          → age_at_arrest
  c_charge_degree        → felony_degree, charge_severity_max
  c_charge_desc          → has_violence_charge, has_drug_charge, etc.
  juv_fel_count          → prior_fta_count (proxy for juvenile risk)
  is_recid               → fta_risk label (primary training target)
  two_year_recid         → fta_risk label (alternative)
  decile_score           → supervision_risk_score (COMPAS's own prediction)

Usage:
  from scoring.compas_bootstrap import generate_bootstrap_dataset
  X, y, feature_names = await generate_bootstrap_dataset(db)

Note: This is a BOOTSTRAP source only. Once internal outcome data exceeds
500+ labeled records, the model should be retrained on real Shamrock data
with COMPAS used only for transfer-learning regularization.
"""
from __future__ import annotations

import io
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Raw CSV URL from ProPublica's GitHub
COMPAS_CSV_URL = (
    "https://raw.githubusercontent.com/propublica/compas-analysis/"
    "master/compas-scores-two-years.csv"
)

# Charge description → keyword category mapping
_VIOLENCE_TERMS = {
    "battery", "assault", "robbery", "murder", "homicide", "manslaughter",
    "kidnapping", "stalking", "aggravated", "weapon", "firearm", "armed",
    "domestic violence", "strangulation", "carjacking",
}
_DRUG_TERMS = {
    "possession", "cocaine", "heroin", "marijuana", "cannabis", "trafficking",
    "controlled substance", "drug", "methamphetamine", "fentanyl",
}
_PROPERTY_TERMS = {
    "burglary", "theft", "larceny", "fraud", "forgery", "shoplifting",
    "stolen property", "criminal mischief", "arson", "grand theft",
}
_DUI_TERMS = {"dui", "driving under the influence", "dwi", "impaired"}
_FLIGHT_TERMS = {"failure to appear", "fta", "fugitive", "flee", "escape", "absconder"}


def _classify_charge_text(desc: str) -> Dict[str, float]:
    """Classify a COMPAS charge description into our keyword feature flags."""
    d = desc.lower() if desc else ""
    return {
        "has_violence_charge": float(any(t in d for t in _VIOLENCE_TERMS)),
        "has_drug_charge": float(any(t in d for t in _DRUG_TERMS)),
        "has_property_charge": float(any(t in d for t in _PROPERTY_TERMS)),
        "has_dui_charge": float(any(t in d for t in _DUI_TERMS)),
        "has_flight_risk_charge": float(any(t in d for t in _FLIGHT_TERMS)),
        "has_capital_charge": float("murder" in d or "homicide" in d),
    }


def _charge_degree_to_felony(degree: str) -> Tuple[float, float]:
    """Map COMPAS c_charge_degree to our felony_degree and severity scales.

    COMPAS uses: 'F' (felony), 'M' (misdemeanor), 'CO3' (3rd degree), etc.
    Returns: (felony_degree, charge_severity_max)
    """
    if not degree:
        return 0.0, 0.0
    d = degree.upper().strip()
    if d.startswith("F"):
        # Generic felony → map to F3 (conservative)
        if "1" in d:
            return 4.0, 4.0
        if "2" in d:
            return 3.0, 3.0
        return 2.0, 3.0  # Default felony → F3
    if d.startswith("M"):
        if "1" in d:
            return 1.0, 2.0
        return 0.5, 2.0
    return 0.0, 1.0


def _age_category_to_float(age_cat: str) -> float:
    """Map COMPAS age_cat to numeric midpoint."""
    cats = {
        "Less than 25": 21.0,
        "25 - 45": 35.0,
        "Greater than 45": 55.0,
    }
    return cats.get(age_cat, 30.0)


def _map_compas_row(row: Dict[str, str]) -> Tuple[Dict[str, float], float]:
    """Map a single COMPAS CSV row to our feature vector + FTA label.

    Returns:
        (feature_dict, label) where label is 1.0 for FTA/recidivism, 0.0 otherwise.
    """
    import math

    features = {}

    # ── Financial (synthetic from charge severity) ──────────────────────
    # COMPAS doesn't have bond amounts. Estimate from charge degree.
    degree = row.get("c_charge_degree", "")
    felony_deg, severity = _charge_degree_to_felony(degree)

    # Synthetic bond amount based on charge severity
    bond_map = {0.0: 500, 0.5: 1000, 1.0: 2500, 2.0: 10000, 3.0: 25000, 4.0: 50000}
    bond_amount = bond_map.get(felony_deg, 5000)
    features["bond_amount_raw"] = float(bond_amount)
    features["bond_amount_log"] = math.log1p(bond_amount)
    features["bond_tier"] = (
        5.0 if bond_amount > 100000 else
        4.0 if bond_amount > 25000 else
        3.0 if bond_amount > 5000 else
        2.0 if bond_amount > 500 else
        1.0 if bond_amount > 0 else 0.0
    )
    features["premium_estimate"] = bond_amount * 0.10

    # ── Legal ─────────────────────────────────────────────────────────────
    charge_desc = row.get("c_charge_desc", "")
    charge_flags = _classify_charge_text(charge_desc)
    features.update(charge_flags)

    features["charge_count"] = float(max(1, int(row.get("c_case_number", "1") or "1")[:1]))
    features["charge_severity_max"] = severity
    features["bond_type_encoded"] = 1.0  # Assume bondable (surety)
    features["felony_degree"] = felony_deg
    features["misdemeanor_only"] = float(felony_deg <= 1.0 and severity <= 2.0)

    # ── Temporal ──────────────────────────────────────────────────────────
    age = float(row.get("age", "30") or "30")
    age_cat = row.get("age_cat", "")
    features["age_at_arrest"] = age if age > 0 else _age_category_to_float(age_cat)
    features["hour_of_day"] = 14.0   # Default afternoon
    features["day_of_week"] = 3.0    # Default Wednesday
    features["is_weekend"] = 0.0
    features["is_night"] = 0.0

    # ── Geographic (Broward County, FL) ───────────────────────────────────
    features["county_encoded"] = float(hash("broward") % 67)
    features["region_encoded"] = 3.0  # South FL
    features["is_swfl"] = 0.0

    # ── Behavioral ────────────────────────────────────────────────────────
    features["in_custody"] = 1.0  # COMPAS subjects were all in-custody at screening
    features["released"] = 0.0
    features["data_completeness"] = 0.85  # COMPAS data is reasonably complete

    # ── Enrichment (from COMPAS criminal history) ─────────────────────────
    priors = int(row.get("priors_count", "0") or "0")
    juv_fel = int(row.get("juv_fel_count", "0") or "0")
    juv_misd = int(row.get("juv_misd_count", "0") or "0")
    juv_other = int(row.get("juv_other_count", "0") or "0")

    features["prior_arrest_count"] = float(priors + juv_fel + juv_misd + juv_other)
    features["has_active_bond"] = 0.0
    features["prior_fta_count"] = float(juv_fel)  # Proxy: juvenile felonies → flight risk
    features["days_since_last_arrest"] = float(max(1, int(row.get("days_b_screening_arrest", "30") or "30")))
    features["prior_bond_total"] = float(priors * bond_amount * 0.5)  # Rough estimate

    # ── COMPAS-specific enrichment features ───────────────────────────────
    # These map to our extended feature set
    decile_score = int(row.get("decile_score", "5") or "5")
    features["compas_decile"] = float(decile_score)  # Will be stripped by feature_names filter
    features["supervision_risk_score"] = float(decile_score) / 10.0
    features["age_risk_bucket"] = (
        3.0 if age < 25 else
        2.0 if age < 35 else
        1.0 if age < 45 else
        0.5
    )
    features["prior_fta_ratio"] = (
        float(juv_fel) / max(1, priors + juv_fel + juv_misd + juv_other)
    )

    # ── Label: FTA / Recidivism ───────────────────────────────────────────
    # Primary: two_year_recid (did they reoffend within 2 years?)
    # This is the strongest FTA proxy available in COMPAS data.
    label = float(row.get("two_year_recid", "0") or "0")

    return features, label


async def fetch_compas_csv() -> List[Dict[str, str]]:
    """Download and parse the COMPAS CSV from ProPublica's GitHub.

    Returns:
        List of row dicts from the CSV.

    Raises:
        RuntimeError: If the download fails.
    """
    import csv

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(COMPAS_CSV_URL, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"COMPAS download failed: HTTP {resp.status}")
                text = await resp.text()
    except ImportError:
        # Fallback to sync download
        import urllib.request
        req = urllib.request.Request(COMPAS_CSV_URL, headers={"User-Agent": "ShamrockLeads/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")

    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    logger.info("📥 COMPAS dataset downloaded: %d rows", len(rows))
    return rows


def _filter_compas_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Apply ProPublica's recommended filters to clean COMPAS data.

    Filters (matching ProPublica's analysis notebook):
      - days_b_screening_arrest between -30 and 30
      - is_recid is not -1 (missing)
      - c_charge_degree is not 'O' (ordinance)
      - score_text is not 'N/A'
    """
    filtered = []
    for row in rows:
        try:
            days_b = int(row.get("days_b_screening_arrest", "0") or "0")
            if abs(days_b) > 30:
                continue
            if row.get("is_recid") == "-1":
                continue
            if row.get("c_charge_degree", "").strip().upper() == "O":
                continue
            if row.get("score_text", "").strip().upper() == "N/A":
                continue
            filtered.append(row)
        except (ValueError, TypeError):
            continue

    logger.info("📊 COMPAS filtered: %d → %d rows (ProPublica criteria)", len(rows), len(filtered))
    return filtered


async def generate_bootstrap_dataset(
    db=None,
    max_samples: int = 10000,
    include_internal: bool = True,
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """Generate a combined training dataset for FTA risk prediction.

    Combines COMPAS bootstrap data with any internal bonded case outcomes.
    The internal data is weighted 2x to ensure the model calibrates to
    our actual population, not just Broward County demographics.

    Args:
        db: Motor database instance (optional — for internal data)
        max_samples: Maximum total samples to return
        include_internal: Whether to include internal bonded case data

    Returns:
        (X, y, feature_names) ready for model training
    """
    from scoring.feature_engineering import get_feature_names

    feature_names = get_feature_names()
    X_rows = []
    y_labels = []

    # ── 1. COMPAS Bootstrap Data ──────────────────────────────────────────
    try:
        raw_rows = await fetch_compas_csv()
        filtered_rows = _filter_compas_rows(raw_rows)

        for row in filtered_rows[:max_samples]:
            try:
                features, label = _map_compas_row(row)
                # Extract only the features our model expects
                vec = [features.get(fn, 0.0) for fn in feature_names]
                X_rows.append(vec)
                y_labels.append(label)
            except Exception as e:
                logger.debug("Skipping COMPAS row: %s", e)
                continue

        logger.info(
            "📊 COMPAS bootstrap: %d samples, %.1f%% positive (FTA/recid)",
            len(y_labels),
            (sum(y_labels) / max(1, len(y_labels))) * 100,
        )
    except Exception as e:
        logger.warning("⚠️ COMPAS bootstrap failed: %s", e)

    # ── 2. Internal Bonded Case Outcomes (if available) ───────────────────
    if include_internal and db is not None:
        try:
            from scoring.feature_engineering import extract_features

            internal_count = 0
            bonds_col = db["active_bonds"]
            rearrest_col = db["rearrest_alerts"]

            # Build rearrest set
            rearrest_bookings = set()
            async for alert in rearrest_col.find({}, {"booking_number": 1}):
                bn = alert.get("booking_number", "")
                if bn:
                    rearrest_bookings.add(bn)

            async for bond in bonds_col.find({}).limit(5000):
                try:
                    enrichment = {
                        "prior_arrest_count": 0,
                        "has_active_bond": True,
                        "prior_fta_count": 0,
                        "days_since_last_arrest": 9999,
                        "prior_bond_total": float(bond.get("bond_amount", 0) or 0),
                    }

                    features = extract_features(bond, enrichment)
                    vec = [features.get(fn, 0.0) for fn in feature_names]

                    # Label
                    booking = bond.get("booking_number", "")
                    status = (bond.get("status") or "").lower()
                    is_fta = (
                        booking in rearrest_bookings
                        or status in ("forfeited", "surrendered")
                        or bond.get("rearrest_detected", False)
                    )

                    # Add internal samples 2x (upweight our real data)
                    for _ in range(2):
                        X_rows.append(vec)
                        y_labels.append(1.0 if is_fta else 0.0)
                    internal_count += 1
                except Exception:
                    continue

            logger.info(
                "📊 Internal outcome data: %d bonded cases (2x weighted → %d samples)",
                internal_count, internal_count * 2,
            )
        except Exception as e:
            logger.warning("⚠️ Internal data extraction failed: %s", e)

    if not X_rows:
        raise ValueError("No training data available (COMPAS download failed and no internal data)")

    X = np.array(X_rows, dtype=np.float64)
    y = np.array(y_labels, dtype=np.float64)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    logger.info(
        "✅ FTA bootstrap dataset: %d total samples, %d features, %.1f%% positive class",
        len(y), len(feature_names), (y.sum() / len(y) * 100) if len(y) > 0 else 0,
    )

    return X, y, feature_names


def get_compas_stats(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """Return summary statistics of the COMPAS dataset for display.

    Used by the dashboard to show bootstrap data quality metrics.
    """
    if not rows:
        return {"total": 0}

    ages = [int(r.get("age", "0") or "0") for r in rows if r.get("age")]
    priors = [int(r.get("priors_count", "0") or "0") for r in rows if r.get("priors_count")]
    recid = [int(r.get("two_year_recid", "0") or "0") for r in rows]
    felonies = sum(1 for r in rows if (r.get("c_charge_degree") or "").upper().startswith("F"))
    misdemeanors = sum(1 for r in rows if (r.get("c_charge_degree") or "").upper().startswith("M"))

    return {
        "total": len(rows),
        "recidivism_rate": round(sum(recid) / max(1, len(recid)) * 100, 1),
        "mean_age": round(sum(ages) / max(1, len(ages)), 1) if ages else 0,
        "mean_priors": round(sum(priors) / max(1, len(priors)), 1) if priors else 0,
        "felony_count": felonies,
        "misdemeanor_count": misdemeanors,
        "source": "ProPublica COMPAS — Broward County, FL",
        "dataset_years": "2013-2014",
        "outcome_window": "2-year recidivism",
    }
