"""
ShamrockLeads — ML Model Training Pipeline
============================================
Trains scikit-learn and XGBoost models on historical arrest data from MongoDB.
Supports two prediction targets:

  1. **Lead Quality** — Will this arrest convert to a written bond? (classification)
  2. **FTA Risk** — If bonded, will the defendant fail to appear? (classification)

Training Pipeline:
  1. Export historical data from MongoDB (arrests + active_bonds + rearrest_alerts)
  2. Engineer features via feature_engineering.py
  3. Create labels from outcome data (bond written? FTA? re-arrested?)
  4. Train RandomForest + XGBoost ensemble
  5. Evaluate with cross-validation + holdout metrics
  6. Persist best model to disk (joblib)

Model Storage:
  Models are saved to /app/models/ (Docker) or ./models/ (local dev).
  Each model file includes metadata: training date, sample count, accuracy, feature importance.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Model storage directory
MODEL_DIR = Path(os.getenv("MODEL_DIR", "./models"))
MODEL_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
#  Training Data Builder (async — reads from MongoDB)
# ─────────────────────────────────────────────────────────────────────────────

async def build_training_dataset(db, target: str = "lead_quality", limit: int = 50000) -> Tuple[Any, Any, List[str]]:
    """Build feature matrix X and label vector y from MongoDB.

    Args:
        db: Motor database instance
        target: "lead_quality" or "fta_risk"
        limit: Max records to process

    Returns:
        (X: np.ndarray, y: np.ndarray, feature_names: list[str])
    """
    from scoring.feature_engineering import extract_features, get_feature_names

    arrests_col = db["arrests"]
    bonds_col = db["active_bonds"]
    rearrest_col = db.get("rearrest_alerts") if hasattr(db, "get") else db["rearrest_alerts"]

    feature_names = get_feature_names()
    X_rows = []
    y_labels = []

    if target == "lead_quality":
        # Label: 1 = arrest became a written bond, 0 = did not
        # Get all booking numbers that resulted in bonds
        bonded_bookings = set()
        async for bond in bonds_col.find({}, {"booking_number": 1, "defendant_name": 1}):
            bn = bond.get("booking_number", "")
            if bn:
                bonded_bookings.add(bn)

        # Build enrichment cache: prior arrest counts per defendant name
        name_counts = {}
        async for arrest in arrests_col.find(
            {},
            {"full_name": 1, "booking_number": 1, "bond_amount": 1}
        ).sort("scraped_at", 1).limit(limit * 2):
            name = (arrest.get("full_name") or "").lower().strip()
            if name:
                name_counts.setdefault(name, []).append(arrest)

        # Process arrests
        cursor = arrests_col.find({}).sort("scraped_at", -1).limit(limit)
        async for arrest in cursor:
            try:
                name = (arrest.get("full_name") or "").lower().strip()
                prior_records = name_counts.get(name, [])

                enrichment = {
                    "prior_arrest_count": max(0, len(prior_records) - 1),
                    "has_active_bond": arrest.get("booking_number", "") in bonded_bookings,
                    "prior_fta_count": 0,
                    "days_since_last_arrest": 9999,
                    "prior_bond_total": 0,
                }

                features = extract_features(arrest, enrichment)
                row = [features.get(fn, 0.0) for fn in feature_names]
                X_rows.append(row)

                # Label
                booking = arrest.get("booking_number", "")
                y_labels.append(1.0 if booking in bonded_bookings else 0.0)

            except Exception as e:
                logger.debug("Skipping record in training: %s", e)
                continue

    elif target == "fta_risk":
        # Label: 1 = defendant was re-arrested or had FTA, 0 = compliant
        rearrest_bookings = set()
        try:
            async for alert in rearrest_col.find({}, {"booking_number": 1}):
                bn = alert.get("booking_number", "")
                if bn:
                    rearrest_bookings.add(bn)
        except Exception:
            pass

        # Only train on bonded cases (need outcome data)
        cursor = bonds_col.find({}).limit(limit)
        async for bond in cursor:
            try:
                enrichment = {
                    "prior_arrest_count": 0,
                    "has_active_bond": True,
                    "prior_fta_count": 0,
                    "days_since_last_arrest": 9999,
                    "prior_bond_total": float(bond.get("bond_amount", 0) or 0),
                }

                features = extract_features(bond, enrichment)
                row = [features.get(fn, 0.0) for fn in feature_names]
                X_rows.append(row)

                # Label: re-arrested or forfeited = FTA risk
                booking = bond.get("booking_number", "")
                status = (bond.get("status") or "").lower()
                is_fta = (
                    booking in rearrest_bookings or
                    status in ("forfeited", "surrendered") or
                    bond.get("rearrest_detected", False)
                )
                y_labels.append(1.0 if is_fta else 0.0)

            except Exception as e:
                logger.debug("Skipping bond in training: %s", e)
                continue

        # ── COMPAS Bootstrap: cold-start fallback when internal data is sparse ──
        if len(X_rows) < 100:
            logger.info(
                "📊 FTA internal data sparse (%d samples) — bootstrapping from COMPAS dataset",
                len(X_rows)
            )
            try:
                from scoring.compas_bootstrap import generate_bootstrap_dataset
                X_boot, y_boot, _ = await generate_bootstrap_dataset(
                    db=db, max_samples=6000, include_internal=False
                )
                # Append COMPAS data
                for i in range(len(y_boot)):
                    X_rows.append(X_boot[i].tolist())
                    y_labels.append(float(y_boot[i]))
                logger.info(
                    "📊 COMPAS bootstrap added %d samples (total now: %d)",
                    len(y_boot), len(X_rows)
                )
            except Exception as e:
                logger.warning("⚠️ COMPAS bootstrap failed: %s", e)

    if not X_rows:
        raise ValueError(f"No training data found for target '{target}'")

    X = np.array(X_rows, dtype=np.float64)
    y = np.array(y_labels, dtype=np.float64)

    # Replace NaN/Inf with 0
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    logger.info(
        "📊 Training dataset built: %d samples, %d features, %.1f%% positive class",
        len(y), len(feature_names), (y.sum() / len(y) * 100) if len(y) > 0 else 0
    )

    return X, y, feature_names


# ─────────────────────────────────────────────────────────────────────────────
#  Model Training
# ─────────────────────────────────────────────────────────────────────────────

async def train_model(
    db,
    target: str = "lead_quality",
    algorithm: str = "random_forest",
    limit: int = 50000,
) -> Dict[str, Any]:
    """Train an ML model and save to disk.

    Args:
        db: Motor database instance
        target: "lead_quality" or "fta_risk"
        algorithm: "random_forest", "xgboost", or "ensemble"
        limit: Max training samples

    Returns:
        Training result dict with metrics.
    """
    try:
        import joblib
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import (
            accuracy_score, classification_report, f1_score,
            precision_score, recall_score, roc_auc_score,
        )
        from sklearn.model_selection import cross_val_score, train_test_split
    except ImportError as e:
        return {"success": False, "error": f"Missing ML dependency: {e}. Install scikit-learn, joblib."}

    start_time = time.time()

    # ── 1. Build training data ───────────────────────────────────────────────
    try:
        X, y, feature_names = await build_training_dataset(db, target=target, limit=limit)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    if len(y) < 50:
        return {"success": False, "error": f"Insufficient data: {len(y)} samples (need 50+)"}

    # ── 2. Train/test split ──────────────────────────────────────────────────
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y if y.sum() >= 2 else None
    )

    # ── 3. Train model ──────────────────────────────────────────────────────
    if algorithm == "xgboost":
        try:
            from xgboost import XGBClassifier
            model = XGBClassifier(
                n_estimators=200,
                max_depth=6,
                learning_rate=0.1,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=42,
                eval_metric="logloss",
                use_label_encoder=False,
            )
        except ImportError:
            logger.warning("XGBoost not available, falling back to RandomForest")
            algorithm = "random_forest"

    if algorithm == "gradient_boosting":
        from sklearn.ensemble import GradientBoostingClassifier
        model = GradientBoostingClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            subsample=0.8,
            min_samples_split=5,
            min_samples_leaf=2,
            random_state=42,
        )

    if algorithm in ("random_forest", "ensemble"):
        model = RandomForestClassifier(
            n_estimators=200,
            max_depth=12,
            min_samples_split=5,
            min_samples_leaf=2,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )

    model.fit(X_train, y_train)

    # ── 4. Evaluate ──────────────────────────────────────────────────────────
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1] if hasattr(model, "predict_proba") else y_pred

    accuracy = accuracy_score(y_test, y_pred)
    precision = precision_score(y_test, y_pred, zero_division=0)
    recall = recall_score(y_test, y_pred, zero_division=0)
    f1 = f1_score(y_test, y_pred, zero_division=0)

    try:
        auc = roc_auc_score(y_test, y_proba)
    except ValueError:
        auc = 0.0

    # Cross-validation
    try:
        cv_scores = cross_val_score(model, X, y, cv=min(5, len(y) // 10), scoring="f1")
        cv_mean = float(cv_scores.mean())
        cv_std = float(cv_scores.std())
    except Exception:
        cv_mean = f1
        cv_std = 0.0

    # Feature importance
    if hasattr(model, "feature_importances_"):
        importances = model.feature_importances_
        feature_importance = sorted(
            zip(feature_names, importances.tolist()),
            key=lambda x: x[1],
            reverse=True,
        )
    else:
        feature_importance = []

    # ── 5. Confusion matrix + ROC curve data ──────────────────────────────────
    from sklearn.metrics import confusion_matrix
    cm = confusion_matrix(y_test, y_pred)
    cm_data = {
        "tn": int(cm[0][0]) if len(cm) > 1 else int(cm[0][0]),
        "fp": int(cm[0][1]) if len(cm) > 1 else 0,
        "fn": int(cm[1][0]) if len(cm) > 1 else 0,
        "tp": int(cm[1][1]) if len(cm) > 1 else 0,
    }

    # ROC curve points (sampled for compact storage)
    roc_data = None
    try:
        from sklearn.metrics import roc_curve
        fpr, tpr, thresholds = roc_curve(y_test, y_proba)
        # Sample ~50 points for dashboard chart
        step = max(1, len(fpr) // 50)
        roc_data = {
            "fpr": [round(float(x), 4) for x in fpr[::step]],
            "tpr": [round(float(x), 4) for x in tpr[::step]],
        }
    except Exception:
        pass

    # ── 6. Save model ────────────────────────────────────────────────────────
    model_path = MODEL_DIR / f"{target}_{algorithm}.joblib"
    meta_path = MODEL_DIR / f"{target}_{algorithm}_meta.json"

    joblib.dump(model, model_path)

    metadata = {
        "target": target,
        "algorithm": algorithm,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "training_samples": int(len(y)),
        "positive_samples": int(y.sum()),
        "negative_samples": int(len(y) - y.sum()),
        "positive_rate": round(float(y.sum() / len(y) * 100), 2),
        "test_samples": int(len(y_test)),
        "metrics": {
            "accuracy": round(accuracy, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1, 4),
            "auc_roc": round(auc, 4),
            "cv_f1_mean": round(cv_mean, 4),
            "cv_f1_std": round(cv_std, 4),
        },
        "confusion_matrix": cm_data,
        "roc_curve": roc_data,
        "feature_importance": feature_importance[:15],  # Top 15
        "feature_names": feature_names,
        "model_path": str(model_path),
        "training_duration_sec": round(time.time() - start_time, 2),
    }

    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info(
        "✅ Model trained: %s/%s — Accuracy=%.3f, F1=%.3f, AUC=%.3f (%d samples, %.1fs)",
        target, algorithm, accuracy, f1, auc, len(y), time.time() - start_time
    )

    return {"success": True, **metadata}


# ─────────────────────────────────────────────────────────────────────────────
#  Model Loading & Prediction
# ─────────────────────────────────────────────────────────────────────────────

def load_model(target: str = "lead_quality", algorithm: str = "random_forest"):
    """Load a trained model from disk.

    Returns:
        (model, metadata) or (None, None) if not found.
    """
    try:
        import joblib
    except ImportError:
        return None, None

    model_path = MODEL_DIR / f"{target}_{algorithm}.joblib"
    meta_path = MODEL_DIR / f"{target}_{algorithm}_meta.json"

    if not model_path.exists():
        return None, None

    model = joblib.load(model_path)
    metadata = {}
    if meta_path.exists():
        with open(meta_path) as f:
            metadata = json.load(f)

    return model, metadata


def predict(record: Dict[str, Any], target: str = "lead_quality",
            algorithm: str = "random_forest",
            enrichment: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    """Run ML prediction on a single arrest record.

    Returns:
        {
            "ml_score": float (0-100),
            "probability": float (0-1),
            "prediction": str ("high_quality" | "low_quality" | "high_risk" | "low_risk"),
            "confidence": str ("high" | "medium" | "low"),
            "top_factors": list[dict],
            "model_algorithm": str,
            "model_trained_at": str,
        }
    """
    from scoring.feature_engineering import extract_features, get_feature_names

    model, metadata = load_model(target, algorithm)
    if model is None:
        return None

    feature_names = get_feature_names()
    features = extract_features(record, enrichment)
    X = np.array([[features.get(fn, 0.0) for fn in feature_names]], dtype=np.float64)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    # Predict
    proba = model.predict_proba(X)[0] if hasattr(model, "predict_proba") else None
    pred = model.predict(X)[0]

    if proba is not None:
        positive_prob = float(proba[1])
    else:
        positive_prob = float(pred)

    # Convert to 0-100 score
    ml_score = round(positive_prob * 100, 1)

    # Determine confidence level
    if abs(positive_prob - 0.5) > 0.3:
        confidence = "high"
    elif abs(positive_prob - 0.5) > 0.15:
        confidence = "medium"
    else:
        confidence = "low"

    # Top contributing factors (from feature importance × feature values)
    top_factors = _explain_prediction(model, features, feature_names, metadata)

    if target == "lead_quality":
        prediction = "high_quality" if positive_prob >= 0.5 else "low_quality"
    else:
        prediction = "high_risk" if positive_prob >= 0.5 else "low_risk"

    return {
        "ml_score": ml_score,
        "probability": round(positive_prob, 4),
        "prediction": prediction,
        "confidence": confidence,
        "top_factors": top_factors[:8],
        "model_algorithm": algorithm,
        "model_trained_at": metadata.get("trained_at", "unknown"),
        "model_accuracy": metadata.get("metrics", {}).get("accuracy", 0),
        "model_f1": metadata.get("metrics", {}).get("f1_score", 0),
    }


def _explain_prediction(model, features: dict, feature_names: list, metadata: dict) -> List[Dict]:
    """Generate human-readable explanation of prediction factors."""
    importance_map = {}
    if hasattr(model, "feature_importances_"):
        for name, imp in zip(feature_names, model.feature_importances_):
            importance_map[name] = imp
    elif metadata.get("feature_importance"):
        for name, imp in metadata["feature_importance"]:
            importance_map[name] = imp

    FEATURE_LABELS = {
        "bond_amount_raw": "Bond Amount",
        "bond_amount_log": "Bond Amount (log)",
        "bond_tier": "Bond Tier",
        "charge_count": "Number of Charges",
        "charge_severity_max": "Charge Severity",
        "has_violence_charge": "Violence Charge",
        "has_drug_charge": "Drug Charge",
        "has_property_charge": "Property Charge",
        "has_dui_charge": "DUI Charge",
        "has_flight_risk_charge": "Flight Risk Indicator",
        "has_capital_charge": "Capital Offense",
        "bond_type_encoded": "Bond Type",
        "felony_degree": "Felony Degree",
        "in_custody": "In Custody",
        "released": "Released",
        "data_completeness": "Data Completeness",
        "prior_arrest_count": "Prior Arrests",
        "has_active_bond": "Has Active Bond",
        "prior_fta_count": "Prior FTAs",
        "age_at_arrest": "Age at Arrest",
        "is_weekend": "Weekend Arrest",
        "is_night": "Nighttime Arrest",
        "is_swfl": "SWFL Region",
        "premium_estimate": "Premium Estimate",
    }

    factors = []
    for name in feature_names:
        imp = importance_map.get(name, 0)
        val = features.get(name, 0)
        if imp > 0.01:  # Only show meaningful factors
            factors.append({
                "feature": FEATURE_LABELS.get(name, name),
                "value": round(val, 2) if isinstance(val, float) else val,
                "importance": round(imp * 100, 1),
                "direction": "positive" if val > 0 else "neutral",
            })

    factors.sort(key=lambda x: x["importance"], reverse=True)
    return factors


# ─────────────────────────────────────────────────────────────────────────────
#  Model Status
# ─────────────────────────────────────────────────────────────────────────────

def get_all_model_status() -> Dict[str, Any]:
    """Return status of all trained models."""
    models = {}
    for target in ["lead_quality", "fta_risk"]:
        for algo in ["random_forest", "xgboost", "gradient_boosting"]:
            _, meta = load_model(target, algo)
            if meta:
                models[f"{target}_{algo}"] = {
                    "target": target,
                    "algorithm": algo,
                    "trained_at": meta.get("trained_at"),
                    "training_samples": meta.get("training_samples"),
                    "positive_rate": meta.get("positive_rate"),
                    "training_source": meta.get("training_source"),
                    "metrics": meta.get("metrics"),
                    "confusion_matrix": meta.get("confusion_matrix"),
                    "roc_curve": meta.get("roc_curve"),
                    "top_features": [
                        f[0] for f in meta.get("feature_importance", [])[:5]
                    ],
                }

    return {
        "models": models,
        "model_dir": str(MODEL_DIR),
        "models_available": len(models),
    }
