"""ShamrockLeads — Agent Performance Analytics API
==================================================
Measures the operational output of every AI agent in the digital workforce.

Endpoints:
  GET /api/analytics/agent-performance   — Full workforce scorecard
  GET /api/analytics/agent/:agent_id     — Individual agent deep-dive
  GET /api/analytics/scraper-accuracy    — Scraper fleet accuracy metrics
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from quart import Blueprint, jsonify, request

from dashboard.extensions import get_db

logger = logging.getLogger(__name__)

agent_analytics_bp = Blueprint("agent_analytics", __name__)


def _utc_now():
    return datetime.now(timezone.utc)


def _period_range(days: int = 30):
    """Return (start, end) for the given lookback period."""
    end = _utc_now()
    start = end - timedelta(days=days)
    return start, end


# ── Agent definitions with collection mappings ────────────────────────────
AGENT_PROFILES = {
    "the_clerk": {
        "name": "The Clerk",
        "role": "Jail Roster Parsing & Data Ingestion",
        "icon": "📋",
        "metrics_source": "arrests",
    },
    "the_analyst": {
        "name": "The Analyst",
        "role": "Lead Scoring & Risk Classification",
        "icon": "🎯",
        "metrics_source": "arrests",
    },
    "the_matcher": {
        "name": "The Matcher",
        "role": "Defendant-Indemnitor Linking",
        "icon": "🔗",
        "metrics_source": "matches",
    },
    "the_closer": {
        "name": "The Closer",
        "role": "iMessage Outreach & Lead Recovery",
        "icon": "💬",
        "metrics_source": "outreach_sequences",
    },
    "the_finder": {
        "name": "The Finder",
        "role": "OSINT Contact Discovery",
        "icon": "🔍",
        "metrics_source": "defendants",
    },
    "the_paperwork_agent": {
        "name": "The Paperwork Agent",
        "role": "SignNow Packet Generation",
        "icon": "📝",
        "metrics_source": "paperwork_packets",
    },
    "the_signature_agent": {
        "name": "The Signature Agent",
        "role": "E-Signature Orchestration",
        "icon": "✍️",
        "metrics_source": "paperwork_packets",
    },
    "the_payment_agent": {
        "name": "The Payment Agent",
        "role": "Premium Collection Tracking",
        "icon": "💳",
        "metrics_source": "payments",
    },
    "shannon": {
        "name": "Shannon",
        "role": "AI iMessage Auto-Reply Agent",
        "icon": "🤖",
        "metrics_source": "imessage_conversations",
    },
    "rearrest_detector": {
        "name": "Re-Arrest Detector",
        "role": "Active Bond Cross-Reference",
        "icon": "🚨",
        "metrics_source": "rearrest_alerts",
    },
    "the_court_clerk": {
        "name": "The Court Clerk",
        "role": "Court Date Tracking & Reminders",
        "icon": "⚖️",
        "metrics_source": "court_reminders",
    },
    "discharge_monitor": {
        "name": "Discharge Monitor",
        "role": "Gmail Exoneration Scanner",
        "icon": "📧",
        "metrics_source": "active_bonds",
    },
}


@agent_analytics_bp.route("/analytics/agent-performance")
async def agent_performance():
    """Full digital workforce scorecard."""
    try:
        db = get_db()
        days = int(request.args.get("days", "30"))
        start, end = _period_range(days)
        start_iso = start.isoformat()

        results = []

        # ── The Clerk (Scraper) ──────────────────────────────────────────
        arrests_col = db["arrests"]
        total_scraped = await arrests_col.count_documents(
            {"scraped_at": {"$gte": start_iso}}
        )
        counties = await arrests_col.distinct("county", {"scraped_at": {"$gte": start_iso}})
        results.append({
            **AGENT_PROFILES["the_clerk"],
            "agent_id": "the_clerk",
            "period_days": days,
            "kpis": {
                "records_processed": total_scraped,
                "counties_active": len(counties),
                "daily_avg": round(total_scraped / max(days, 1), 1),
            },
            "status": "active" if total_scraped > 0 else "idle",
        })

        # ── The Analyst (Lead Scoring) ───────────────────────────────────
        scored = await arrests_col.count_documents(
            {"lead_score": {"$exists": True, "$ne": None}, "scraped_at": {"$gte": start_iso}}
        )
        hot = await arrests_col.count_documents(
            {"lead_status": "Hot", "scraped_at": {"$gte": start_iso}}
        )
        warm = await arrests_col.count_documents(
            {"lead_status": "Warm", "scraped_at": {"$gte": start_iso}}
        )
        cold = await arrests_col.count_documents(
            {"lead_status": "Cold", "scraped_at": {"$gte": start_iso}}
        )
        disq = await arrests_col.count_documents(
            {"lead_status": "Disqualified", "scraped_at": {"$gte": start_iso}}
        )
        scoring_rate = round((scored / max(total_scraped, 1)) * 100, 1)
        results.append({
            **AGENT_PROFILES["the_analyst"],
            "agent_id": "the_analyst",
            "period_days": days,
            "kpis": {
                "records_scored": scored,
                "scoring_rate_pct": scoring_rate,
                "hot_leads": hot,
                "warm_leads": warm,
                "cold_leads": cold,
                "disqualified": disq,
                "hot_rate_pct": round((hot / max(scored, 1)) * 100, 1),
            },
            "status": "active" if scored > 0 else "idle",
        })

        # ── The Matcher ──────────────────────────────────────────────────
        matches_col = db["matches"]
        total_matches = await matches_col.count_documents(
            {"created_at": {"$gte": start_iso}}
        )
        validated = await matches_col.count_documents(
            {"status": "validated", "created_at": {"$gte": start_iso}}
        )
        rejected = await matches_col.count_documents(
            {"status": "rejected", "created_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["the_matcher"],
            "agent_id": "the_matcher",
            "period_days": days,
            "kpis": {
                "total_matches": total_matches,
                "validated": validated,
                "rejected": rejected,
                "accuracy_pct": round((validated / max(total_matches, 1)) * 100, 1),
            },
            "status": "active" if total_matches > 0 else "idle",
        })

        # ── The Closer (Outreach) ────────────────────────────────────────
        outreach_col = db["outreach_sequences"]
        total_seqs = await outreach_col.count_documents(
            {"created_at": {"$gte": start_iso}}
        )
        replied = await outreach_col.count_documents(
            {"status": "replied", "created_at": {"$gte": start_iso}}
        )
        converted = await outreach_col.count_documents(
            {"status": "converted", "created_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["the_closer"],
            "agent_id": "the_closer",
            "period_days": days,
            "kpis": {
                "sequences_started": total_seqs,
                "replies_received": replied,
                "conversions": converted,
                "reply_rate_pct": round((replied / max(total_seqs, 1)) * 100, 1),
                "conversion_rate_pct": round((converted / max(total_seqs, 1)) * 100, 1),
            },
            "status": "active" if total_seqs > 0 else "idle",
        })

        # ── The Finder (Contact Discovery) ───────────────────────────────
        defendants_col = db["defendants"]
        enriched = await defendants_col.count_documents(
            {"contacts_discovered": {"$exists": True, "$ne": []},
             "updated_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["the_finder"],
            "agent_id": "the_finder",
            "period_days": days,
            "kpis": {"defendants_enriched": enriched},
            "status": "active" if enriched > 0 else "idle",
        })

        # ── Paperwork & Signature ────────────────────────────────────────
        packets_col = db["paperwork_packets"]
        packets_gen = await packets_col.count_documents(
            {"created_at": {"$gte": start_iso}}
        )
        packets_signed = await packets_col.count_documents(
            {"status": "completed", "created_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["the_paperwork_agent"],
            "agent_id": "the_paperwork_agent",
            "period_days": days,
            "kpis": {
                "packets_generated": packets_gen,
                "packets_signed": packets_signed,
                "completion_rate_pct": round((packets_signed / max(packets_gen, 1)) * 100, 1),
            },
            "status": "active" if packets_gen > 0 else "idle",
        })

        # ── Payments ─────────────────────────────────────────────────────
        pay_col = db["payments"]
        payments = await pay_col.count_documents(
            {"created_at": {"$gte": start_iso}}
        )
        pay_pipe = [
            {"$match": {"created_at": {"$gte": start_iso}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
        pay_agg = await pay_col.aggregate(pay_pipe).to_list(1)
        total_collected = pay_agg[0]["total"] if pay_agg else 0
        results.append({
            **AGENT_PROFILES["the_payment_agent"],
            "agent_id": "the_payment_agent",
            "period_days": days,
            "kpis": {
                "payments_recorded": payments,
                "total_collected": round(total_collected, 2),
            },
            "status": "active" if payments > 0 else "idle",
        })

        # ── Shannon (AI auto-reply) ──────────────────────────────────────
        imsg_col = db["imessage_conversations"]
        ai_replies = await imsg_col.count_documents(
            {"ai_replied": True, "updated_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["shannon"],
            "agent_id": "shannon",
            "period_days": days,
            "kpis": {"ai_replies_sent": ai_replies},
            "status": "active" if ai_replies > 0 else "idle",
        })

        # ── Re-Arrest Detector ───────────────────────────────────────────
        rearrest_col = db["rearrest_alerts"]
        alerts = await rearrest_col.count_documents(
            {"detected_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["rearrest_detector"],
            "agent_id": "rearrest_detector",
            "period_days": days,
            "kpis": {"alerts_fired": alerts},
            "status": "active" if alerts > 0 else "idle",
        })

        # ── Court Clerk ──────────────────────────────────────────────────
        court_col = db["court_reminders"]
        reminders = await court_col.count_documents(
            {"created_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["the_court_clerk"],
            "agent_id": "the_court_clerk",
            "period_days": days,
            "kpis": {"reminders_sent": reminders},
            "status": "active" if reminders > 0 else "idle",
        })

        # ── Discharge Monitor ────────────────────────────────────────────
        bonds_col = db["active_bonds"]
        discharged = await bonds_col.count_documents(
            {"status": "exonerated", "updated_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["discharge_monitor"],
            "agent_id": "discharge_monitor",
            "period_days": days,
            "kpis": {"bonds_discharged": discharged},
            "status": "active" if discharged > 0 else "idle",
        })

        # Workforce health summary
        active_count = sum(1 for r in results if r["status"] == "active")
        idle_count = sum(1 for r in results if r["status"] == "idle")

        return jsonify({
            "success": True,
            "report_type": "Digital Workforce Scorecard",
            "period_days": days,
            "generated_at": _utc_now().isoformat(),
            "workforce_health": {
                "total_agents": len(results),
                "active": active_count,
                "idle": idle_count,
                "utilization_pct": round((active_count / max(len(results), 1)) * 100, 1),
            },
            "agents": results,
        })
    except Exception as exc:
        logger.exception("analytics/agent-performance error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


@agent_analytics_bp.route("/analytics/scraper-accuracy")
async def scraper_accuracy():
    """Per-county scraper accuracy and performance metrics."""
    try:
        db = get_db()
        days = int(request.args.get("days", "7"))
        start, _ = _period_range(days)
        start_iso = start.isoformat()

        col = db["arrests"]
        pipe = [
            {"$match": {"scraped_at": {"$gte": start_iso}}},
            {"$group": {
                "_id": "$county",
                "total": {"$sum": 1},
                "with_score": {"$sum": {"$cond": [{"$gt": ["$lead_score", None]}, 1, 0]}},
                "hot": {"$sum": {"$cond": [{"$eq": ["$lead_status", "Hot"]}, 1, 0]}},
                "avg_score": {"$avg": "$lead_score"},
                "with_bond": {"$sum": {"$cond": [{"$gt": ["$bond_amount", 0]}, 1, 0]}},
            }},
            {"$sort": {"total": -1}},
        ]
        data = await col.aggregate(pipe).to_list(None)

        counties = []
        for d in data:
            total = d["total"]
            counties.append({
                "county": d["_id"],
                "total_records": total,
                "scored_pct": round((d["with_score"] / max(total, 1)) * 100, 1),
                "hot_leads": d["hot"],
                "avg_score": round(d["avg_score"] or 0, 1),
                "bond_data_pct": round((d["with_bond"] / max(total, 1)) * 100, 1),
            })

        return jsonify({
            "success": True,
            "report_type": "Scraper Accuracy by County",
            "period_days": days,
            "generated_at": _utc_now().isoformat(),
            "counties": counties,
            "total_counties": len(counties),
            "total_records": sum(c["total_records"] for c in counties),
        })
    except Exception as exc:
        logger.exception("analytics/scraper-accuracy error: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500
