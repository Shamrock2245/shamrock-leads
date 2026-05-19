# ── AUTO-MIGRATED: Quart Blueprint → FastAPI APIRouter (v3) ──
# _qp = dict(request.query_params) injected into fns that read query params.
# Review each endpoint and move _qp.get() calls to typed fn signatures.

"""
ShamrockLeads — ML Intelligence API Blueprint
===============================================
REST endpoints for the ML prediction engine.

Endpoints:
  POST  /api/ml/train                      — Trigger model training
  GET   /api/ml/predict/<booking_number>    — Get ML prediction for a lead
  GET   /api/ml/model-status               — Status of all trained models
  POST  /api/ml/batch-predict               — Batch predictions for multiple leads
  GET   /api/ml/feature-importance          — Feature importance analysis
  GET   /api/ml/predictions/compare/<bn>    — Rule-based vs ML score comparison

All routes are async (Quart) with Motor (async MongoDB).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.extensions import get_db

logger = logging.getLogger(__name__)

ml_bp = APIRouter(prefix="/api", tags=["ml_intelligence"])
# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/ml/train — Trigger model training
# ─────────────────────────────────────────────────────────────────────────────
@ml_bp.post("/ml/train")
async def api_train_model(request: Request):
    """Train an ML model on historical data.

    Body (optional):
        {
            "target": "lead_quality" | "fta_risk",
            "algorithm": "random_forest" | "xgboost",
            "limit": 50000
        }
    """
    try:
        from scoring.model_trainer import train_model

        data = await request.json() or {}
        target = data.get("target", "lead_quality")
        algorithm = data.get("algorithm", "random_forest")
        limit = int(data.get("limit", 50000))

        if target not in ("lead_quality", "fta_risk"):
            return JSONResponse({"success": False, "error": "target must be 'lead_quality' or 'fta_risk'"}, status_code=400)
        if algorithm not in ("random_forest", "xgboost", "gradient_boosting", "ensemble"):
            return JSONResponse({"success": False, "error": "algorithm must be 'random_forest', 'xgboost', 'gradient_boosting', or 'ensemble'"}, status_code=400)

        db = get_db()
        result = await train_model(db, target=target, algorithm=algorithm, limit=limit)

        if result.get("success"):
            logger.info("🧠 ML model trained: %s/%s — F1=%.3f",
                       target, algorithm, result.get("metrics", {}).get("f1_score", 0))
        else:
            logger.warning("⚠️ ML training failed: %s", result.get("error"))

        return result

    except Exception as e:
        logger.exception("ML training error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/ml/bootstrap-fta — COMPAS Bootstrap FTA Risk Training
# ─────────────────────────────────────────────────────────────────────────────
@ml_bp.post("/ml/bootstrap-fta")
async def api_bootstrap_fta(request: Request):
    """Bootstrap an FTA risk model using ProPublica's COMPAS dataset.

    This solves the cold-start problem when we have insufficient bonded case
    outcome data to train a reliable FTA predictor. Downloads ~6,000 labeled
    pretrial records from Broward County and maps them to our feature schema.

    Body (optional):
        {
            "algorithm": "random_forest" | "gradient_boosting",
            "max_samples": 6000,
            "include_internal": true
        }
    """
    try:
        import time as _time
        start = _time.time()

        data = await request.json() or {}
        algorithm = data.get("algorithm", "random_forest")
        max_samples = int(data.get("max_samples", 6000))
        include_internal = data.get("include_internal", True)

        db = get_db()

        # ── 1. Generate bootstrap dataset ──────────────────────────────────
        from scoring.compas_bootstrap import (
            generate_bootstrap_dataset,
            fetch_compas_csv,
            _filter_compas_rows,
            get_compas_stats,
        )

        raw_rows = await fetch_compas_csv()
        filtered_rows = _filter_compas_rows(raw_rows)
        compas_stats = get_compas_stats(filtered_rows)

        X, y, feature_names = await generate_bootstrap_dataset(
            db=db, max_samples=max_samples, include_internal=include_internal
        )

        # ── 2. Train model on bootstrap data ───────────────────────────────
        from scoring.model_trainer import MODEL_DIR
        import numpy as np
        import json
        from datetime import datetime, timezone

        try:
            import joblib
            from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
            from sklearn.model_selection import train_test_split, cross_val_score
            from sklearn.metrics import (
                accuracy_score, precision_score, recall_score,
                f1_score, roc_auc_score, confusion_matrix, roc_curve,
            )
        except ImportError as e:
            return JSONResponse({"success": False, "error": f"Missing dependency: {e}"}, status_code=500)

        # Train/test split
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42,
            stratify=y if y.sum() >= 2 else None
        )

        # Model selection
        if algorithm == "gradient_boosting":
            model = GradientBoostingClassifier(
                n_estimators=200, max_depth=6, learning_rate=0.1,
                subsample=0.8, min_samples_split=5, random_state=42,
            )
        else:
            model = RandomForestClassifier(
                n_estimators=200, max_depth=12, min_samples_split=5,
                min_samples_leaf=2, class_weight="balanced",
                random_state=42, n_jobs=-1,
            )

        model.fit(X_train, y_train)

        # Evaluate
        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]

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
            cv = cross_val_score(model, X, y, cv=5, scoring="f1")
            cv_mean, cv_std = float(cv.mean()), float(cv.std())
        except Exception:
            cv_mean, cv_std = f1, 0.0

        # Feature importance
        importances = model.feature_importances_
        feature_importance = sorted(
            zip(feature_names, importances.tolist()),
            key=lambda x: x[1], reverse=True,
        )

        # Confusion matrix
        cm = confusion_matrix(y_test, y_pred)
        cm_data = {
            "tn": int(cm[0][0]), "fp": int(cm[0][1]) if cm.shape[1] > 1 else 0,
            "fn": int(cm[1][0]) if cm.shape[0] > 1 else 0,
            "tp": int(cm[1][1]) if cm.shape[0] > 1 and cm.shape[1] > 1 else 0,
        }

        # ROC curve
        roc_data = None
        try:
            fpr, tpr, _ = roc_curve(y_test, y_proba)
            step = max(1, len(fpr) // 50)
            roc_data = {
                "fpr": [round(float(x), 4) for x in fpr[::step]],
                "tpr": [round(float(x), 4) for x in tpr[::step]],
            }
        except Exception:
            pass

        # ── 3. Save model ──────────────────────────────────────────────────
        model_path = MODEL_DIR / f"fta_risk_{algorithm}.joblib"
        meta_path = MODEL_DIR / f"fta_risk_{algorithm}_meta.json"
        joblib.dump(model, model_path)

        metadata = {
            "target": "fta_risk",
            "algorithm": algorithm,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "training_source": "compas_bootstrap",
            "compas_stats": compas_stats,
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
            "feature_importance": feature_importance[:15],
            "feature_names": feature_names,
            "model_path": str(model_path),
            "training_duration_sec": round(_time.time() - start, 2),
        }

        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info(
            "🧠 FTA COMPAS Bootstrap model trained: %s — Acc=%.3f, F1=%.3f, AUC=%.3f (%d samples, %.1fs)",
            algorithm, accuracy, f1, auc, len(y), _time.time() - start
        )

        return {"success": True, **metadata}

    except Exception as e:
        logger.exception("COMPAS bootstrap error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/ml/predict/<booking_number> — ML prediction for a lead
# ─────────────────────────────────────────────────────────────────────────────
@ml_bp.get("/ml/predict/<booking_number>")
async def api_predict(request: Request, booking_number: str):
    """Get ML prediction for a specific arrest record.

    _qp = dict(request.query_params)
    Query params:
        target: "lead_quality" (default) | "fta_risk"
        algorithm: "random_forest" (default) | "xgboost"
    """
    try:
        from scoring.model_trainer import predict

        target = _qp.get("target", "lead_quality")
        algorithm = _qp.get("algorithm", "random_forest")

        db = get_db()
        arrest = await db["arrests"].find_one({"booking_number": booking_number})
        if not arrest:
            return JSONResponse({"success": False, "error": "Arrest not found"}, status_code=404)

        # Build enrichment from DB
        enrichment = await _build_enrichment(db, arrest)

        result = predict(arrest, target=target, algorithm=algorithm, enrichment=enrichment)
        if result is None:
            return {
                "success": False,
                "error": f"No trained model found for {target}/{algorithm}. POST /api/ml/train first."
            }, 404

        return {
            "success": True,
            "booking_number": booking_number,
            "defendant_name": arrest.get("full_name", ""),
            "county": arrest.get("county", ""),
            "target": target,
            "prediction": result,
            # Include the rule-based score for comparison
            "rule_based_score": arrest.get("lead_score"),
            "rule_based_status": arrest.get("lead_status"),
        }

    except Exception as e:
        logger.exception("ML predict error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/ml/batch-predict — Batch predictions
# ─────────────────────────────────────────────────────────────────────────────
@ml_bp.post("/ml/batch-predict")
async def api_batch_predict(request: Request):
    """Batch ML predictions for multiple arrest records.

    Body:
        {
            "booking_numbers": ["2024-001", "2024-002", ...],
            "target": "lead_quality",
            "algorithm": "random_forest"
        }
    """
    try:
        from scoring.model_trainer import predict

        data = await request.json() or {}
        booking_numbers = data.get("booking_numbers", [])
        target = data.get("target", "lead_quality")
        algorithm = data.get("algorithm", "random_forest")

        if not booking_numbers:
            return JSONResponse({"success": False, "error": "booking_numbers required"}, status_code=400)

        db = get_db()
        results = []

        for bn in booking_numbers[:100]:  # Cap at 100
            arrest = await db["arrests"].find_one({"booking_number": bn})
            if not arrest:
                results.append({"booking_number": bn, "error": "not_found"})
                continue

            enrichment = await _build_enrichment(db, arrest)
            result = predict(arrest, target=target, algorithm=algorithm, enrichment=enrichment)

            if result:
                results.append({
                    "booking_number": bn,
                    "defendant_name": arrest.get("full_name", ""),
                    "county": arrest.get("county", ""),
                    "ml_score": result["ml_score"],
                    "prediction": result["prediction"],
                    "confidence": result["confidence"],
                    "rule_based_score": arrest.get("lead_score"),
                })
            else:
                results.append({"booking_number": bn, "error": "no_model"})

        return {
            "success": True,
            "count": len(results),
            "target": target,
            "predictions": results,
        }

    except Exception as e:
        logger.exception("Batch predict error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/ml/model-status — All model status
# ─────────────────────────────────────────────────────────────────────────────
@ml_bp.get("/ml/model-status")
async def api_model_status():
    """Get status and metrics of all trained ML models."""
    try:
        from scoring.model_trainer import get_all_model_status
        status = get_all_model_status()
        return {"success": True, **status}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/ml/feature-importance — Feature importance analysis
# ─────────────────────────────────────────────────────────────────────────────
@ml_bp.get("/ml/feature-importance")
async def api_feature_importance(request: Request):
    """Get feature importance rankings for a trained model.

    _qp = dict(request.query_params)
    Query params:
        target: "lead_quality" (default) | "fta_risk"
        algorithm: "random_forest" (default) | "xgboost"
    """
    try:
        from scoring.model_trainer import load_model

        target = _qp.get("target", "lead_quality")
        algorithm = _qp.get("algorithm", "random_forest")

        _, metadata = load_model(target, algorithm)
        if not metadata:
            return {
                "success": False,
                "error": f"No model found for {target}/{algorithm}"
            }, 404

        importance = metadata.get("feature_importance", [])
        return {
            "success": True,
            "target": target,
            "algorithm": algorithm,
            "trained_at": metadata.get("trained_at"),
            "training_samples": metadata.get("training_samples"),
            "feature_importance": [
                {"feature": name, "importance": round(imp * 100, 2)}
                for name, imp in importance
            ],
        }

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/ml/predictions/compare/<bn> — Rule-based vs ML comparison
# ─────────────────────────────────────────────────────────────────────────────
@ml_bp.get("/ml/predictions/compare/<booking_number>")
async def api_compare_predictions(booking_number: str):
    """Compare rule-based and ML predictions side-by-side.

    Shows exactly where the models agree and disagree, helping calibrate
    the ML model against the existing rule-based system.
    """
    try:
        from scoring.model_trainer import predict
        from dashboard.api.lead_intelligence import build_score_explanation, classify_charge

        db = get_db()
        arrest = await db["arrests"].find_one({"booking_number": booking_number})
        if not arrest:
            return JSONResponse({"success": False, "error": "Not found"}, status_code=404)

        enrichment = await _build_enrichment(db, arrest)

        # Rule-based
        rule_factors = build_score_explanation(arrest)
        rule_score = sum(f["points"] for f in rule_factors)
        rule_status = arrest.get("lead_status", "Unknown")

        # ML predictions
        ml_lead = predict(arrest, target="lead_quality", enrichment=enrichment)
        ml_fta = predict(arrest, target="fta_risk", enrichment=enrichment)

        # Compute agreement
        agreement = "N/A"
        if ml_lead:
            rule_positive = rule_score >= 40  # Warm+Hot
            ml_positive = ml_lead["probability"] >= 0.5
            agreement = "agree" if rule_positive == ml_positive else "disagree"

        return {
            "success": True,
            "booking_number": booking_number,
            "defendant_name": arrest.get("full_name", ""),
            "comparison": {
                "rule_based": {
                    "score": rule_score,
                    "status": rule_status,
                    "factors": rule_factors,
                },
                "ml_lead_quality": ml_lead,
                "ml_fta_risk": ml_fta,
                "agreement": agreement,
            },
        }

    except Exception as e:
        logger.exception("Compare predictions error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
#  Enrichment Builder (shared)
# ─────────────────────────────────────────────────────────────────────────────

async def _build_enrichment(db, arrest: dict) -> dict:
    """Build enrichment features from MongoDB for a single arrest."""
    full_name = (arrest.get("full_name") or "").lower().strip()
    booking_number = arrest.get("booking_number", "")

    enrichment = {
        "prior_arrest_count": 0,
        "has_active_bond": False,
        "prior_fta_count": 0,
        "days_since_last_arrest": 9999,
        "prior_bond_total": 0,
    }

    if not full_name:
        return enrichment

    try:
        # Prior arrests for same name
        prior_count = await db["arrests"].count_documents({
            "full_name": {"$regex": f"^{full_name}$", "$options": "i"},
            "booking_number": {"$ne": booking_number},
        })
        enrichment["prior_arrest_count"] = prior_count

        # Active bond check
        active_bond = await db["active_bonds"].find_one({
            "defendant_name": {"$regex": full_name, "$options": "i"},
            "status": {"$in": ["active", "monitoring"]},
        })
        enrichment["has_active_bond"] = active_bond is not None

        # Prior bond total
        if active_bond:
            enrichment["prior_bond_total"] = float(active_bond.get("bond_amount", 0) or 0)

        # Re-arrest history
        rearrest_count = await db["rearrest_alerts"].count_documents({
            "defendant_name": {"$regex": full_name, "$options": "i"},
        })
        enrichment["prior_fta_count"] = rearrest_count

    except Exception as e:
        logger.debug("Enrichment lookup error: %s", e)

    return enrichment
