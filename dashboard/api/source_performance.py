"""
ShamrockLeads — Source Performance API (Alpha Engine)
======================================================
Exposes the self-replicating pipeline intelligence to the dashboard.

Endpoints:
  GET  /api/alpha/leaderboard           — County source rankings (full leaderboard)
  GET  /api/alpha/county/<county>       — Single county deep-dive
  GET  /api/alpha/tiers                 — Tier distribution summary
  GET  /api/alpha/recommendations       — Top actions across all counties
  GET  /api/alpha/stats                 — High-level KPIs for dashboard header
  POST /api/alpha/recalculate           — Trigger a scoring cycle manually
  POST /api/alpha/record-conversion     — Record a bond conversion feedback signal
  GET  /api/alpha/trend/<county>        — Score history for trend charting
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from quart import Blueprint, jsonify, request

from dashboard.extensions import get_db
from dashboard.services.source_performance_tracker import SourcePerformanceTracker

logger = logging.getLogger(__name__)

source_performance_bp = Blueprint("source_performance", __name__)


def _tracker():
    return SourcePerformanceTracker(get_db())


# ── Leaderboard ──────────────────────────────────────────────────────────
@source_performance_bp.route("/alpha/leaderboard")
async def leaderboard():
    """Full county source performance leaderboard."""
    try:
        limit = int(request.args.get("limit", "50"))
        tracker = _tracker()
        data = await tracker.get_leaderboard(limit=limit)
        stats = await tracker.get_system_stats()
        return jsonify({
            "success": True,
            "leaderboard": data,
            "system_stats": stats,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        logger.exception("alpha/leaderboard error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ── County Deep-Dive ─────────────────────────────────────────────────────
@source_performance_bp.route("/alpha/county/<county>")
async def county_detail(county: str):
    """Detailed score breakdown for a single county."""
    try:
        tracker = _tracker()
        data = await tracker.get_county_detail(county)
        if not data:
            return jsonify({"success": False, "error": f"No data for county: {county}"}), 404
        return jsonify({"success": True, "county": data})
    except Exception as exc:
        logger.exception("alpha/county/%s error: %s", county, exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ── Tier Summary ─────────────────────────────────────────────────────────
@source_performance_bp.route("/alpha/tiers")
async def tiers():
    """County distribution across performance tiers."""
    try:
        tracker = _tracker()
        data = await tracker.get_tier_summary()
        return jsonify({"success": True, "tiers": data})
    except Exception as exc:
        logger.exception("alpha/tiers error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ── Recommendations ──────────────────────────────────────────────────────
@source_performance_bp.route("/alpha/recommendations")
async def recommendations():
    """
    Top actionable recommendations across all counties.
    Limit defaults to 20.
    """
    try:
        limit = int(request.args.get("limit", "20"))
        tracker = _tracker()
        data = await tracker.get_leaderboard(limit=100)

        all_actions = []
        for county_data in data:
            county = county_data.get("county", "Unknown")
            tier = county_data.get("tier", "dormant")
            score = county_data.get("score", 0)
            actions = county_data.get("actions", [])
            for action in actions:
                all_actions.append({
                    "county": county,
                    "tier": tier,
                    "score": score,
                    "action": action,
                })

        # Sort by score descending (prioritize alpha county actions)
        all_actions.sort(key=lambda a: a["score"], reverse=True)
        all_actions = all_actions[:limit]

        return jsonify({
            "success": True,
            "recommendations": all_actions,
            "total_actions": len(all_actions),
        })
    except Exception as exc:
        logger.exception("alpha/recommendations error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ── System Stats ─────────────────────────────────────────────────────────
@source_performance_bp.route("/alpha/stats")
async def stats():
    """High-level Alpha Engine KPIs for the dashboard header."""
    try:
        tracker = _tracker()
        data = await tracker.get_system_stats()
        return jsonify({"success": True, **data})
    except Exception as exc:
        logger.exception("alpha/stats error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ── Recalculate ──────────────────────────────────────────────────────────
@source_performance_bp.route("/alpha/recalculate", methods=["POST"])
async def recalculate():
    """
    Trigger a full scoring cycle manually.
    Normally this runs on a scheduler (APScheduler), but can be
    kicked off from the dashboard.
    """
    try:
        tracker = _tracker()
        result = await tracker.run_scoring_cycle()
        return jsonify(result)
    except Exception as exc:
        logger.exception("alpha/recalculate error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ── Record Conversion ────────────────────────────────────────────────────
@source_performance_bp.route("/alpha/record-conversion", methods=["POST"])
async def record_conversion():
    """
    Record a bond conversion feedback signal.
    Called when a bond is written and moves to 'active' status.

    Body:
      county: str (required)
      booking_number: str (required)
      bond_amount: float (required)
      premium: float (required)
      channel: str (optional, default "scraper")
    """
    try:
        body = await request.get_json(silent=True) or {}
        county = body.get("county", "")
        booking_number = body.get("booking_number", "")
        bond_amount = float(body.get("bond_amount", 0))
        premium = float(body.get("premium", 0))
        channel = body.get("channel", "scraper")

        if not county or not booking_number:
            return jsonify({
                "success": False,
                "error": "county and booking_number are required",
            }), 400

        tracker = _tracker()
        await tracker.record_conversion(
            county=county,
            booking_number=booking_number,
            bond_amount=bond_amount,
            premium=premium,
            channel=channel,
        )
        return jsonify({"success": True, "message": "Conversion recorded"})
    except Exception as exc:
        logger.exception("alpha/record-conversion error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


# ── Score Trend History ──────────────────────────────────────────────────
@source_performance_bp.route("/alpha/trend/<county>")
async def trend(county: str):
    """
    Score history for a county (from conversion_events + source_performance
    snapshots). Returns recent conversion events + current score.
    """
    try:
        db = get_db()
        # Get recent conversions for this county
        cursor = db["conversion_events"].find(
            {"county": {"$regex": f"^{county}$", "$options": "i"}},
            {"_id": 0},
        ).sort("recorded_at", -1).limit(50)
        events = await cursor.to_list(50)

        # Get current score
        tracker = _tracker()
        current = await tracker.get_county_detail(county)

        return jsonify({
            "success": True,
            "county": county,
            "current_score": current.get("score", 0) if current else 0,
            "current_tier": current.get("tier", "unknown") if current else "unknown",
            "trend_vs_prior": current.get("trend_vs_prior", 0) if current else 0,
            "recent_conversions": events,
        })
    except Exception as exc:
        logger.exception("alpha/trend/%s error: %s", county, exc)
        return jsonify({"success": False, "error": str(exc)}), 500
