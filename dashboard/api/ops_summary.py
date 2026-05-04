"""
ShamrockLeads — Daily Ops Summary API
Auto-generated operational intelligence report.

Endpoints:
  GET /ops/daily-summary  — Full daily operations snapshot
  GET /ops/health         — System-wide health check
"""

from quart import Blueprint, jsonify
from datetime import datetime, timezone, timedelta

from dashboard.extensions import get_collection, BB_SERVERS, REGISTERED_COUNTIES

ops_summary_bp = Blueprint('ops_summary', __name__)


@ops_summary_bp.route('/ops/daily-summary', methods=['GET'])
async def daily_summary():
    """Generate a comprehensive daily operations summary."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    arrests_col = get_collection("arrests")
    bonds_col = get_collection("active_bonds")
    plans_col = get_collection("payment_plans")
    payments_col = get_collection("payments")
    intake_col = get_collection("intake_queue")
    reminders_col = get_collection("court_reminders")
    sequences_col = get_collection("outreach_sequences")

    # ── Arrests Intelligence ──
    total_arrests = await arrests_col.estimated_document_count()
    today_new = await arrests_col.count_documents({
        "scraped_at": {"$gte": today_start.isoformat()}
    })
    yesterday_new = await arrests_col.count_documents({
        "scraped_at": {
            "$gte": yesterday_start.isoformat(),
            "$lt": today_start.isoformat()
        }
    })
    hot_leads_24h = await arrests_col.count_documents({
        "lead_score": {"$gte": 70},
        "scraped_at": {"$gte": (now - timedelta(hours=24)).isoformat()}
    })

    # Active counties with data in last 24h
    pipeline_counties = [
        {"$match": {"scraped_at": {"$gte": (now - timedelta(hours=24)).isoformat()}}},
        {"$group": {"_id": "$county"}},
    ]
    active_counties = []
    async for doc in arrests_col.aggregate(pipeline_counties):
        active_counties.append(doc["_id"])

    # Stale counties (no data in 6+ hours)
    pipeline_stale = [
        {"$group": {"_id": "$county", "latest": {"$max": "$scraped_at"}}},
        {"$match": {"latest": {"$lt": (now - timedelta(hours=6)).isoformat()}}},
    ]
    stale_counties = []
    async for doc in arrests_col.aggregate(pipeline_stale):
        stale_counties.append(doc["_id"])

    # ── Bond Portfolio ──
    active_bond_count = await bonds_col.count_documents({"status": "active"})
    pipeline_liability = [
        {"$match": {"status": "active"}},
        {"$group": {"_id": None, "total": {"$sum": "$bond_amount"}}},
    ]
    total_liability = 0
    async for doc in bonds_col.aggregate(pipeline_liability):
        total_liability = doc["total"]

    # ── Payment Health ──
    active_plans = await plans_col.count_documents({"status": "active"})
    cutoff_30 = (now - timedelta(days=30)).isoformat()
    delinquent_plans = await plans_col.count_documents({
        "status": "active",
        "next_due_date": {"$lt": cutoff_30},
    })

    # Today's collections
    pipeline_today_rev = [
        {"$match": {"status": "completed", "timestamp": {"$gte": today_start.isoformat()}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ]
    today_revenue = 0
    today_payment_count = 0
    async for doc in payments_col.aggregate(pipeline_today_rev):
        today_revenue = doc["total"]
        today_payment_count = doc["count"]

    # ── Intake Pipeline ──
    pending_intakes = await intake_col.count_documents({"status": "pending"})
    matched_intakes = await intake_col.count_documents({"status": "matched"})

    # ── Court Reminders ──
    upcoming_court = await reminders_col.count_documents({
        "status": "pending",
        "send_at": {"$lte": (now + timedelta(days=7)).isoformat()},
    })
    sent_today = await reminders_col.count_documents({
        "status": "sent",
        "sent_at": {"$gte": today_start.isoformat()},
    })

    # ── Outreach ──
    active_sequences = await sequences_col.count_documents({"status": "active"})
    completed_sequences = await sequences_col.count_documents({"status": "completed"})

    # ── BlueBubbles Status ──
    bb_status = {
        "servers_configured": len(BB_SERVERS),
        "servers": {k: {"label": v["label"], "url_set": bool(v.get("url"))} for k, v in BB_SERVERS.items()},
    }

    return jsonify({
        "generated_at": now.isoformat(),
        "period": "daily",
        "arrests": {
            "total_database": total_arrests,
            "new_today": today_new,
            "new_yesterday": yesterday_new,
            "trend": "up" if today_new > yesterday_new else "down" if today_new < yesterday_new else "flat",
            "hot_leads_24h": hot_leads_24h,
            "active_counties": sorted(active_counties),
            "stale_counties": sorted(stale_counties),
            "total_registered": len(REGISTERED_COUNTIES),
        },
        "bonds": {
            "active_count": active_bond_count,
            "total_liability": round(total_liability, 2),
        },
        "payments": {
            "active_plans": active_plans,
            "delinquent_plans": delinquent_plans,
            "today_revenue": round(today_revenue, 2),
            "today_payments": today_payment_count,
        },
        "intake": {
            "pending": pending_intakes,
            "matched": matched_intakes,
        },
        "court": {
            "reminders_due_7d": upcoming_court,
            "sent_today": sent_today,
        },
        "outreach": {
            "active_sequences": active_sequences,
            "completed_sequences": completed_sequences,
        },
        "infrastructure": {
            "bluebubbles": bb_status,
            "scraper_fleet_size": len(REGISTERED_COUNTIES),
        },
    })


@ops_summary_bp.route('/ops/health', methods=['GET'])
async def system_health():
    """Quick system-wide health check across all subsystems."""
    now = datetime.now(timezone.utc)
    health = {"status": "ok", "checks": {}, "timestamp": now.isoformat()}

    # MongoDB
    try:
        arrests_col = get_collection("arrests")
        count = await arrests_col.estimated_document_count()
        health["checks"]["mongodb"] = {"status": "ok", "total_arrests": count}
    except Exception as e:
        health["checks"]["mongodb"] = {"status": "error", "error": str(e)}
        health["status"] = "degraded"

    # BlueBubbles
    bb_ok = len(BB_SERVERS) > 0
    health["checks"]["bluebubbles"] = {
        "status": "ok" if bb_ok else "warning",
        "servers": len(BB_SERVERS),
    }
    if not bb_ok:
        health["status"] = "degraded"

    # Scraper freshness (any data in last 2 hours?)
    try:
        two_hours_ago = (now - timedelta(hours=2)).isoformat()
        recent = await arrests_col.count_documents({"scraped_at": {"$gte": two_hours_ago}})
        health["checks"]["scrapers"] = {
            "status": "ok" if recent > 0 else "stale",
            "records_2h": recent,
        }
        if recent == 0:
            health["status"] = "degraded"
    except Exception as e:
        health["checks"]["scrapers"] = {"status": "error", "error": str(e)}

    return jsonify(health), 200 if health["status"] == "ok" else 503
