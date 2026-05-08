"""
ShamrockLeads — Revenue Forecasting Engine
============================================
Replaces linear extrapolation with real time-series forecasting.

Methods:
  1. Exponential Smoothing (Holt-Winters) — Best for monthly revenue with seasonality
  2. ARIMA-style decomposition — Trend + seasonal + residual
  3. Monte Carlo simulation — Probabilistic confidence intervals

All methods work without Prophet (heavy dep) by using numpy/scipy only.
Falls back gracefully if insufficient data.

Endpoints are served via analytics_bp (analytics.py).
This module provides the computation layer.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Exponential Smoothing (Double/Triple)
# ─────────────────────────────────────────────────────────────────────────────

class ExponentialSmoother:
    """Holt-Winters exponential smoothing for revenue forecasting.

    Supports:
      - Single (level only) — flat/stable series
      - Double (level + trend) — trending series
      - Triple (level + trend + seasonal) — seasonal series

    Automatically selects the best method based on data characteristics.
    """

    def __init__(self, alpha: float = 0.3, beta: float = 0.1, gamma: float = 0.2,
                 seasonal_period: int = 7):
        self.alpha = alpha  # Level smoothing
        self.beta = beta    # Trend smoothing
        self.gamma = gamma  # Seasonal smoothing
        self.seasonal_period = seasonal_period

    def forecast(self, data: List[float], horizon: int = 30) -> Dict[str, Any]:
        """Generate forecast with confidence intervals.

        Args:
            data: Historical daily revenue values (most recent last)
            horizon: Number of days to forecast

        Returns:
            {
                "forecast": [daily predictions],
                "upper_bound": [95% CI upper],
                "lower_bound": [95% CI lower],
                "trend": "up" | "down" | "flat",
                "trend_strength": float (0-1),
                "seasonal_pattern": [day-of-week multipliers],
                "method": "single" | "double" | "triple",
            }
        """
        if len(data) < 3:
            return self._empty_forecast(horizon)

        arr = np.array(data, dtype=np.float64)
        arr = np.nan_to_num(arr, nan=0.0)

        # Select method based on data characteristics
        if len(arr) >= self.seasonal_period * 2:
            method = "triple"
            predictions = self._triple_exponential(arr, horizon)
        elif len(arr) >= 7:
            method = "double"
            predictions = self._double_exponential(arr, horizon)
        else:
            method = "single"
            predictions = self._single_exponential(arr, horizon)

        # Compute confidence intervals via residual analysis
        residuals = self._compute_residuals(arr, method)
        std_err = np.std(residuals) if len(residuals) > 0 else np.std(arr) * 0.3

        upper = [max(0, p + 1.96 * std_err * math.sqrt(i + 1)) for i, p in enumerate(predictions)]
        lower = [max(0, p - 1.96 * std_err * math.sqrt(i + 1)) for i, p in enumerate(predictions)]

        # Trend analysis
        trend_direction, trend_strength = self._analyze_trend(arr)

        # Seasonal pattern (day-of-week)
        seasonal = self._extract_seasonal(arr)

        return {
            "forecast": [round(p, 2) for p in predictions],
            "upper_bound": [round(u, 2) for u in upper],
            "lower_bound": [round(l, 2) for l in lower],
            "trend": trend_direction,
            "trend_strength": round(trend_strength, 3),
            "seasonal_pattern": seasonal,
            "method": method,
            "data_points": len(data),
            "forecast_horizon": horizon,
        }

    def _single_exponential(self, data: np.ndarray, horizon: int) -> List[float]:
        """Simple exponential smoothing (level only)."""
        level = data[0]
        for val in data[1:]:
            level = self.alpha * val + (1 - self.alpha) * level
        return [float(level)] * horizon

    def _double_exponential(self, data: np.ndarray, horizon: int) -> List[float]:
        """Double exponential smoothing (level + trend)."""
        level = data[0]
        trend = (data[1] - data[0]) if len(data) > 1 else 0

        for val in data[1:]:
            prev_level = level
            level = self.alpha * val + (1 - self.alpha) * (level + trend)
            trend = self.beta * (level - prev_level) + (1 - self.beta) * trend

        predictions = []
        for i in range(1, horizon + 1):
            predictions.append(float(max(0, level + i * trend)))
        return predictions

    def _triple_exponential(self, data: np.ndarray, horizon: int) -> List[float]:
        """Triple exponential smoothing (Holt-Winters with additive seasonality)."""
        period = self.seasonal_period
        n = len(data)

        # Initialize seasonal components
        seasonal = np.zeros(period)
        for i in range(period):
            seasonal[i] = np.mean(data[i::period]) - np.mean(data[:period])

        level = np.mean(data[:period])
        trend = (np.mean(data[period:2*period]) - np.mean(data[:period])) / period if n >= 2 * period else 0

        for t in range(n):
            val = data[t]
            prev_level = level
            season_idx = t % period

            level = self.alpha * (val - seasonal[season_idx]) + (1 - self.alpha) * (level + trend)
            trend = self.beta * (level - prev_level) + (1 - self.beta) * trend
            seasonal[season_idx] = self.gamma * (val - level) + (1 - self.gamma) * seasonal[season_idx]

        predictions = []
        for i in range(1, horizon + 1):
            season_idx = (n + i) % period
            predictions.append(float(max(0, level + i * trend + seasonal[season_idx])))
        return predictions

    def _compute_residuals(self, data: np.ndarray, method: str) -> np.ndarray:
        """Compute in-sample residuals for CI estimation."""
        if len(data) < 3:
            return np.array([])

        fitted = np.zeros_like(data)
        level = data[0]
        trend = 0
        fitted[0] = level

        if method == "single":
            for t in range(1, len(data)):
                level = self.alpha * data[t] + (1 - self.alpha) * level
                fitted[t] = level
        else:
            trend = (data[1] - data[0]) if len(data) > 1 else 0
            for t in range(1, len(data)):
                prev_level = level
                level = self.alpha * data[t] + (1 - self.alpha) * (level + trend)
                trend = self.beta * (level - prev_level) + (1 - self.beta) * trend
                fitted[t] = level + trend

        return data[1:] - fitted[1:]

    def _analyze_trend(self, data: np.ndarray) -> Tuple[str, float]:
        """Analyze trend direction and strength."""
        if len(data) < 5:
            return "flat", 0.0

        # Linear regression slope
        x = np.arange(len(data))
        coeffs = np.polyfit(x, data, 1)
        slope = coeffs[0]

        # Normalize slope by mean
        mean_val = np.mean(data)
        if mean_val == 0:
            return "flat", 0.0

        normalized_slope = slope / mean_val

        if normalized_slope > 0.01:
            return "up", min(abs(normalized_slope) * 10, 1.0)
        elif normalized_slope < -0.01:
            return "down", min(abs(normalized_slope) * 10, 1.0)
        return "flat", 0.0

    def _extract_seasonal(self, data: np.ndarray) -> List[float]:
        """Extract day-of-week seasonal multipliers."""
        if len(data) < self.seasonal_period:
            return [1.0] * self.seasonal_period

        period = self.seasonal_period
        means = []
        overall_mean = np.mean(data) if np.mean(data) > 0 else 1.0

        for i in range(period):
            day_values = data[i::period]
            day_mean = np.mean(day_values)
            means.append(round(day_mean / overall_mean, 2))

        return means

    def _empty_forecast(self, horizon: int) -> Dict[str, Any]:
        return {
            "forecast": [0.0] * horizon,
            "upper_bound": [0.0] * horizon,
            "lower_bound": [0.0] * horizon,
            "trend": "flat",
            "trend_strength": 0.0,
            "seasonal_pattern": [1.0] * 7,
            "method": "insufficient_data",
            "data_points": 0,
            "forecast_horizon": horizon,
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Monte Carlo Revenue Simulation
# ─────────────────────────────────────────────────────────────────────────────

def monte_carlo_forecast(
    daily_revenues: List[float],
    horizon: int = 30,
    simulations: int = 1000,
) -> Dict[str, Any]:
    """Monte Carlo simulation for revenue forecasting.

    Generates multiple possible future paths based on historical volatility.
    Returns percentile-based confidence intervals.
    """
    if len(daily_revenues) < 7:
        return {
            "median_total": 0, "p10_total": 0, "p90_total": 0,
            "mean_daily": 0, "simulations": 0,
        }

    arr = np.array(daily_revenues, dtype=np.float64)
    arr = arr[arr > 0]  # Remove zero days for better distribution

    if len(arr) < 3:
        mean_daily = np.mean(daily_revenues)
        return {
            "median_total": round(mean_daily * horizon, 2),
            "p10_total": round(mean_daily * horizon * 0.6, 2),
            "p90_total": round(mean_daily * horizon * 1.4, 2),
            "mean_daily": round(mean_daily, 2),
            "simulations": 0,
        }

    # Fit log-normal distribution (revenue is typically right-skewed)
    log_arr = np.log1p(arr)
    mu = np.mean(log_arr)
    sigma = np.std(log_arr)

    # Simulate
    np.random.seed(42)
    sim_totals = []
    for _ in range(simulations):
        sim_days = np.expm1(np.random.normal(mu, sigma, horizon))
        sim_days = np.maximum(sim_days, 0)
        sim_totals.append(float(np.sum(sim_days)))

    sim_totals = np.array(sim_totals)

    return {
        "median_total": round(float(np.median(sim_totals)), 2),
        "mean_total": round(float(np.mean(sim_totals)), 2),
        "p10_total": round(float(np.percentile(sim_totals, 10)), 2),
        "p25_total": round(float(np.percentile(sim_totals, 25)), 2),
        "p75_total": round(float(np.percentile(sim_totals, 75)), 2),
        "p90_total": round(float(np.percentile(sim_totals, 90)), 2),
        "mean_daily": round(float(np.mean(arr)), 2),
        "std_daily": round(float(np.std(arr)), 2),
        "simulations": simulations,
        "horizon_days": horizon,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Async MongoDB Data Fetcher
# ─────────────────────────────────────────────────────────────────────────────

async def get_daily_revenue_series(db, days: int = 90) -> List[Dict[str, Any]]:
    """Fetch daily revenue time series from MongoDB payments collection.

    Returns list of {date: "YYYY-MM-DD", amount: float, count: int}
    with zero-fill for missing days.
    """
    from datetime import timedelta

    payments_col = db["payments"]
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    pipe = await payments_col.aggregate([
        {"$match": {
            "status": {"$in": ["completed", "paid", "success"]},
            "timestamp": {"$gte": cutoff}
        }},
        {"$group": {
            "_id": {
                "y": {"$year": "$timestamp"},
                "m": {"$month": "$timestamp"},
                "d": {"$dayOfMonth": "$timestamp"},
            },
            "amount": {"$sum": "$amount"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"_id.y": 1, "_id.m": 1, "_id.d": 1}},
    ]).to_list(None)

    # Build filled series
    filled = {}
    for i in range(days):
        day = (now - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        filled[day] = {"date": day, "amount": 0.0, "count": 0}

    for row in pipe:
        d = row["_id"]
        key = f"{d['y']}-{d['m']:02d}-{d['d']:02d}"
        filled[key] = {"date": key, "amount": round(row["amount"], 2), "count": row["count"]}

    return sorted(filled.values(), key=lambda x: x["date"])


async def generate_full_forecast(db, days_history: int = 90, horizon: int = 30) -> Dict[str, Any]:
    """Generate complete revenue forecast with multiple methods.

    Returns combined forecast using exponential smoothing + Monte Carlo.
    """
    series = await get_daily_revenue_series(db, days=days_history)
    daily_amounts = [r["amount"] for r in series]

    # Exponential Smoothing
    smoother = ExponentialSmoother(alpha=0.3, beta=0.1, gamma=0.2, seasonal_period=7)
    es_forecast = smoother.forecast(daily_amounts, horizon=horizon)

    # Monte Carlo
    mc_forecast = monte_carlo_forecast(daily_amounts, horizon=horizon)

    # Monthly projection (combine methods)
    es_total = sum(es_forecast["forecast"])
    mc_median = mc_forecast["median_total"]
    blended_forecast = round((es_total * 0.6 + mc_median * 0.4), 2) if mc_median > 0 else es_total

    # Current month MTD
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days_elapsed = (now - month_start).days + 1
    mtd = sum(r["amount"] for r in series if r["date"] >= month_start.strftime("%Y-%m-%d"))
    days_remaining = max(1, 30 - days_elapsed)

    return {
        "historical": series[-30:],  # Last 30 days for charting
        "exponential_smoothing": es_forecast,
        "monte_carlo": mc_forecast,
        "summary": {
            "mtd_revenue": round(mtd, 2),
            "mtd_daily_avg": round(mtd / days_elapsed, 2) if days_elapsed > 0 else 0,
            "forecast_next_30d": round(blended_forecast, 2),
            "forecast_remaining_month": round(blended_forecast * (days_remaining / horizon), 2),
            "projected_month_total": round(mtd + blended_forecast * (days_remaining / horizon), 2),
            "trend": es_forecast["trend"],
            "trend_strength": es_forecast["trend_strength"],
            "confidence_band": {
                "low": round(mc_forecast["p10_total"], 2),
                "high": round(mc_forecast["p90_total"], 2),
            },
            "days_elapsed": days_elapsed,
            "days_remaining": days_remaining,
        },
    }
