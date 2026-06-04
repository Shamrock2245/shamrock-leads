"""
ShamrockLeads — Background Cron Task Registry

Extracted from __init__.py (1,749 lines) into a clean, declarative registry.
Each cron is defined as a CronDef and launched via a generic runner.

Usage (from main.py lifespan):
    from dashboard.cron import start_all_crons
    tasks = await start_all_crons()
"""
from __future__ import annotations
import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Callable, Awaitable, List

logger = logging.getLogger(__name__)


@dataclass
class CronDef:
    """Declarative cron job definition."""
    name: str                          # automation_config key + trigger name
    label: str                         # Human-readable log prefix
    interval: float                    # Seconds between runs
    initial_delay: float               # Seconds before first run
    run: Callable[[], Awaitable[None]] # The actual work
    default_enabled: bool = True       # Default if missing from automation_config
    emoji: str = "☘️"


async def _cron_runner(cron: CronDef):
    """Generic cron loop with automation_config gating + manual trigger support."""
    from dashboard.routers.automation_control import register_trigger
    trigger = asyncio.Event()
    register_trigger(cron.name, trigger)
    await asyncio.sleep(cron.initial_delay)
    cycle = 0
    while True:
        cycle += 1
        try:
            from dashboard.services.automation_config import should_run
            from dashboard.extensions import get_db
            if not await should_run(get_db(), cron.name, default=cron.default_enabled):
                logger.debug("[%s] Disabled — skipping", cron.label)
                trigger.clear()
                try:
                    await asyncio.wait_for(trigger.wait(), timeout=cron.interval)
                except asyncio.TimeoutError:
                    pass
                continue
        except Exception as e:
            logger.debug("[%s] automation_config check failed: %s", cron.label, e)
        try:
            await cron.run()
        except Exception as e:
            logger.warning("[%s] Error (cycle %s): %s", cron.label, cycle, e)
        trigger.clear()
        try:
            await asyncio.wait_for(trigger.wait(), timeout=cron.interval)
            logger.info("[%s] ▶ Manual trigger — running now", cron.label)
        except asyncio.TimeoutError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Cron Implementations (one async function each)
# ═══════════════════════════════════════════════════════════════════════════════

async def _run_alpha_engine():
    from dashboard.services.source_performance_tracker import SourcePerformanceTracker
    from dashboard.extensions import get_db
    tracker = SourcePerformanceTracker(get_db())
    result = await tracker.run_scoring_cycle()
    logger.info("[AlphaEngine] ✅ %d counties scored", result.get("counties_scored", 0))

async def _run_docket_monitor():
    from dashboard.services.docket_monitor import run_docket_scan
    from dashboard.extensions import get_db
    result = await run_docket_scan(get_db(), limit=100)
    logger.info("[DocketMonitor] ✅ %d bonds, %d events", result.get("bonds_scanned", 0), result.get("events_found", 0))

async def _run_court_intel():
    from dashboard.services.court_data_ingestor import run_ingestion
    from dashboard.extensions import get_db
    result = await run_ingestion(get_db(), days_back=30)
    logger.info("[CourtIntel] ✅ %d new, %d dupes", result.get("ingested", 0), result.get("duplicates", 0))

async def _run_nlp_enrichment():
    from dashboard.extensions import get_db
    from dashboard.services.legal_nlp_service import analyze_charges, extract_citations
    from datetime import datetime, timezone
    db = get_db()
    cursor = db["arrests"].find(
        {"nlp_enriched_at": {"$exists": False}, "charges": {"$exists": True, "$ne": ""}},
        {"_id": 1, "charges": 1, "booking_number": 1}
    ).limit(200)
    enriched = 0
    async for doc in cursor:
        try:
            text = doc.get("charges", "")
            if not text:
                continue
            a = analyze_charges(text)
            c = extract_citations(text)
            await db["arrests"].update_one({"_id": doc["_id"]}, {"$set": {
                "nlp_severity": a["max_severity"], "nlp_severity_level": a["severity_level"],
                "nlp_fta_risk": a["fta_risk_score"], "nlp_statutes": a["statutes"],
                "nlp_citations": c, "nlp_risk_factors": a["risk_factors"],
                "nlp_charge_count": a.get("charge_count", 0),
                "nlp_enriched_at": datetime.now(timezone.utc).isoformat() + "Z",
            }})
            enriched += 1
        except Exception as e:
            logger.debug("[NLP-Enrich] Record %s failed: %s", doc.get("booking_number", "?"), e)
    if enriched:
        logger.info("[NLP-Enrich] ✅ %d enriched", enriched)

async def _run_bb_health():
    from dashboard.routers.bb_health_monitor import run_health_check_all
    await run_health_check_all()

async def _run_court_reminders():
    from dashboard.services.court_reminder_service import CourtReminderService
    from dashboard.extensions import get_db
    svc = CourtReminderService(get_db())
    scan = await svc.auto_scan_and_schedule()
    send = await svc.process_due_reminders()
    logger.info("[CourtReminder] scanned=%s scheduled=%s sent=%s",
                scan.get("bonds_scanned", 0), scan.get("scheduled", 0), send.get("sent", 0))

async def _run_delinquency():
    from dashboard.extensions import get_db
    from dashboard.routers.notifications import create_notification
    from datetime import datetime, timezone, timedelta
    db = get_db()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=30)).isoformat()
    cursor = db["payment_plans"].find({"status": "active", "next_due_date": {"$lt": cutoff}, "delinquent": {"$ne": True}})
    flagged = 0
    async for plan in cursor:
        await db["payment_plans"].update_one({"plan_id": plan["plan_id"]}, {"$set": {"delinquent": True, "updated_at": now.isoformat()}})
        await create_notification(notification_type="delinquent",
            title=f"Delinquent: {plan.get('defendant_name', plan['booking_number'])}",
            message=f"Payment overdue. Balance: ${plan.get('balance_remaining', 0):,.2f}",
            entity_id=plan["booking_number"], entity_type="payment_plan",
            metadata={"plan_id": plan["plan_id"], "balance": plan.get("balance_remaining", 0)})
        flagged += 1
    if flagged:
        logger.info("[Delinquency] flagged %s plans", flagged)

async def _run_rearrest():
    from dashboard.routers.rearrest_detector import scan_for_rearrests
    result = await scan_for_rearrests(hours=3)
    if result.get("detected", 0) > 0:
        logger.warning("🔄 RE-ARREST: %s match(es)!", result["detected"])

async def _run_fta_alert():
    """Every 4h: scan active bonds for missed court dates, fire BB alert pipeline."""
    from dashboard.extensions import get_db
    from dashboard.services.fta_alert_service import FTAAlertService
    db = get_db()
    svc = FTAAlertService(db)
    result = await svc.scan_and_alert()
    if result.get("fta_detected", 0) > 0:
        logger.warning(
            "[FTAAlert] 🚨 %d new FTA(s) detected | alerts_sent=%d | escalated=%d",
            result["fta_detected"], result["alerts_sent"], result["escalated"],
        )
    else:
        logger.info("[FTAAlert] scanned=%d — no new FTAs", result.get("scanned", 0))

async def _run_missed_payment():
    """Every 12h: scan payment plans for overdue installments, fire BB alert + geo link."""
    from dashboard.services.missed_payment_alert_service import MissedPaymentAlertService
    svc = MissedPaymentAlertService()
    result = await svc.scan_and_alert()
    if result.get("alerted", 0) > 0 or result.get("escalated", 0) > 0:
        logger.warning(
            "[MissedPayment] 💸 alerted=%d escalated=%d scanned=%d errors=%d",
            result["alerted"], result["escalated"], result["scanned"], result["errors"],
        )
    else:
        logger.info("[MissedPayment] scanned=%d — no overdue payments", result.get("scanned", 0))


async def _run_retention():
    from dashboard.routers.data_retention import _execute_purge, _get_db_stats, _maybe_alert_slack
    result = await _execute_purge(dry_run=False)
    total = result.get("total_purged", 0)
    if total:
        logger.info(
            "[DataRetention] Purged %d records (protected=%d)",
            total, result.get("protected_booking_numbers", 0),
        )
    # Check DB size and alert Slack if at risk
    db_stats = await _get_db_stats()
    await _maybe_alert_slack(db_stats)
    logger.info(
        "[DataRetention] DB usage: %.1f%% (%s MB / %s MB)",
        db_stats["usage_pct"], db_stats["total_size_mb"], db_stats["limit_mb"],
    )

async def _run_court_email():
    def _sync():
        from pymongo import MongoClient
        from dashboard.services.court_email_scheduler import CourtEmailScheduler
        uri = os.getenv("MONGODB_URI", "")
        if not uri:
            return {}
        c = MongoClient(uri, serverSelectionTimeoutMS=10000)
        sched = CourtEmailScheduler(db=c[os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")])
        r = sched.process_all()
        c.close()
        return r
    result = await asyncio.to_thread(_sync)
    if result.get("processed", 0):
        logger.info("[CourtEmail] processed=%s", result["processed"])

async def _run_blog():
    def _sync():
        from blog.scheduler import BlogScheduler
        from pymongo import MongoClient
        uri = os.getenv("MONGODB_URI", "")
        c = MongoClient(uri, serverSelectionTimeoutMS=10000) if uri else None
        db = c[os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")] if c is not None else None
        r = BlogScheduler(db=db).run()
        if c is not None:
            c.close()
        return r
    result = await asyncio.to_thread(_sync)
    if result.get("published", 0):
        logger.info("[Blog] published %s", result["published"])

async def _run_wix_sync():
    from wix.sync import WixSyncEngine
    from dashboard.extensions import get_db
    engine = WixSyncEngine(db=get_db())
    if engine.is_configured:
        result = await engine.run_full_sync()
        r = result.get("results", {})
        logger.info("[WixSync] intakes=%s cases=%s", r.get("intakes", {}).get("synced", 0), r.get("cases", {}).get("synced", 0))

async def _run_geo_intel():
    """Geo intelligence cron — requires Traccar (skip if not configured)."""
    if not os.getenv("TRACCAR_URL") and not os.getenv("TRACCAR_TOKEN"):
        return  # Traccar not configured — skip silently
    from dashboard.services.geo_intelligence import GeoIntelligenceService
    from dashboard.services.traccar_client import TraccarClient
    from dashboard.extensions import get_db
    svc = GeoIntelligenceService(db=get_db())
    devices = await svc.list_devices()
    active = [d for d in devices if d.get("status") == "active"]
    tc = TraccarClient()
    for dev in active:
        try:
            tid = dev.get("traccar_device_id")
            if not tid:
                continue
            positions = await tc.get_positions(device_id=tid)
            if positions:
                pos = positions[0]
                await svc.sync_position(device_id=str(dev["_id"]), lat=pos["latitude"],
                    lng=pos["longitude"], accuracy=pos.get("accuracy"), source="geo_cron")
        except Exception as e:
            logger.debug("[GeoIntel] Device %s sync failed: %s", dev.get("_id", "?"), e)
    await tc.close()

async def _run_findmy():
    """FindMy geofence cron — uses BlueBubbles Private API."""
    from dashboard.extensions import get_db, BB_SERVERS
    from dashboard.services.automation_config import get_automation_config
    cfg = await get_automation_config(get_db())
    fm = cfg.get("findmy_geofence", {})
    if not BB_SERVERS:
        return  # No BlueBubbles servers configured
    # Use the first available server (office iMac)
    server = next(iter(BB_SERVERS.values()))
    from dashboard.routers.bb_private_api import BlueBubblesClient
    from dashboard.routers.geo import haversine_distance
    from datetime import datetime, timezone
    client = BlueBubblesClient(base_url=server["url"], password=server["password"])
    result = await client.findmy_devices()
    devices = result.get("data", {}).get("devices", [])
    center_lat, center_lng = fm.get("center_lat", 26.5629), fm.get("center_lng", -81.8723)
    radius = fm.get("geofence_miles", 25)
    for dev in devices:
        lat, lng = dev.get("location", {}).get("latitude"), dev.get("location", {}).get("longitude")
        if lat is None or lng is None:
            continue
        dist = haversine_distance(center_lat, center_lng, lat, lng)
        if dist > radius:
            await get_db()["geo_events"].insert_one({
                "event_type": "findmy_boundary_breach", "source": "findmy_cron",
                "device_name": dev.get("name", "Unknown"), "distance_miles": round(dist, 2),
                "lat": lat, "lng": lng, "created_at": datetime.now(timezone.utc), "acknowledged": False,
            })

async def _run_auto_reply_bridge():
    from dashboard.services.automation_config import get_automation_config
    from dashboard.extensions import get_db, get_collection
    cfg = await get_automation_config(get_db())
    new_state = cfg.get("auto_reply", {}).get("enabled", False)
    await get_collection("outreach_config").update_one(
        {"type": "auto_reply"}, {"$set": {"enabled": new_state}}, upsert=True)

async def _run_speed_to_contact():
    from dashboard.services.outreach_sequencer import OutreachSequencer
    from dashboard.services.automation_config import get_automation_config
    from dashboard.extensions import get_db
    from datetime import datetime, timezone
    db = get_db()
    cfg = await get_automation_config(db)
    stc = cfg.get("speed_to_contact", {})
    mode = stc.get("mode", "review")
    if mode == "off":
        return  # Mode explicitly set to off
    seq = OutreachSequencer(db)
    result = await seq.batch_start_new_arrests(
        hours_back=stc.get("hours_back", 1),
        limit=stc.get("max_per_cycle", 20),
        mode=mode,
        min_lead_score=stc.get("min_lead_score", 70),
    )
    await db["automation_run_log"].insert_one({"automation": "speed_to_contact", "run_at": datetime.now(timezone.utc), "result": result})
    if result.get("started", 0) or result.get("queued", 0):
        logger.info("[SpeedToContact] mode=%s started=%s queued=%s no_phone=%s",
                    mode, result.get("started", 0), result.get("queued", 0), result.get("no_phone", 0))

async def _run_paperwork_chase():
    from dashboard.services.paperwork_chase_service import PaperworkChaseService
    from dashboard.services.automation_config import get_automation_config
    from dashboard.extensions import get_db
    from datetime import datetime, timezone
    db = get_db()
    cfg = await get_automation_config(db)
    chase = cfg.get("paperwork_chase", {})
    svc = PaperworkChaseService(db)
    result = await svc.scan_and_chase(config=chase)
    await db["automation_run_log"].insert_one({"automation": "paperwork_chase", "run_at": datetime.now(timezone.utc), "result": result})

async def _run_intake_recovery():
    from dashboard.services.intake_recovery_service import IntakeRecoveryService
    from dashboard.services.automation_config import get_automation_config
    from dashboard.extensions import get_db
    from datetime import datetime, timezone
    db = get_db()
    cfg = await get_automation_config(db)
    recovery = cfg.get("intake_recovery", {})
    svc = IntakeRecoveryService(db)
    result = await svc.scan_and_recover(config=recovery)
    await db["automation_run_log"].insert_one({"automation": "intake_recovery", "run_at": datetime.now(timezone.utc), "result": result})


async def _run_outreach_queue():
    from dashboard.services.outreach_queue import process_outreach_queue
    from dashboard.extensions import get_db
    result = await process_outreach_queue(get_db())
    if result.get("processed", 0) > 0:
        logger.info(
            "[OutreachQueue] processed=%d sent=%d retried=%d failed=%d",
            result["processed"], result["sent"], result["retried"], result["failed"]
        )


async def _run_drip_scanner():
    """Scan for leads matching drip trigger conditions and queue messages for approval."""
    from dashboard.services.drip_sequences import DripSequenceRunner
    from dashboard.extensions import get_db
    db = get_db()
    runner = DripSequenceRunner(db)
    result = await runner.scan_and_queue()
    if result.get("queued", 0) > 0:
        logger.info("[DripScanner] Queued %d messages for approval: %s", result["queued"], result["by_sequence"])


async def _run_overdue_tasks():
    """Flag pending tasks whose due_date has passed as overdue.

    Uses update_many for efficiency.  Compares against both ISO-string
    and native datetime due_date formats to handle legacy records.
    """
    from dashboard.extensions import get_db
    from datetime import datetime, timezone
    db = get_db()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    # Match both ISO-string due_dates (legacy) and native datetime due_dates
    result = await db["tasks"].update_many(
        {
            "status": "pending",
            "$or": [
                {"due_date": {"$lt": now_iso}},   # ISO string comparison
                {"due_date": {"$lt": now}},        # native datetime comparison
            ],
        },
        {"$set": {"status": "overdue", "overdue_at": now_iso}},
    )
    flagged = result.modified_count
    if flagged > 0:
        logger.warning("[Tasks] Flagged %d tasks as overdue", flagged)


# ═══════════════════════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════════════════════

CRON_REGISTRY: List[CronDef] = [
    CronDef("outreach_queue",     "OutreachQueue",       30,  10, _run_outreach_queue),
    CronDef("alpha_engine",       "AlphaEngine",       14400, 180, _run_alpha_engine),
    CronDef("docket_monitor",     "DocketMonitor",     14400, 120, _run_docket_monitor),
    CronDef("court_intel",        "CourtIntel",        21600,  60, _run_court_intel),
    CronDef("nlp_enrichment",     "NLP-Enrich",         7200,  90, _run_nlp_enrichment),
    CronDef("bb_health",          "BB-Health",           600,  30, _run_bb_health),
    CronDef("court_reminders",    "CourtReminder",      3600,  60, _run_court_reminders),
    CronDef("delinquency_scanner","Delinquency",       14400, 120, _run_delinquency),
    CronDef("rearrest_detection", "ReArrest",           7200, 180, _run_rearrest),
    CronDef("fta_alert",          "FTAAlert",          14400, 120, _run_fta_alert),
    CronDef("missed_payment",     "MissedPayment",     43200, 240, _run_missed_payment),
    CronDef("data_retention",     "DataRetention",     86400, 300, _run_retention),
    CronDef("court_email",        "CourtEmail",          900,  90, _run_court_email),
    CronDef("blog_publisher",     "Blog",              21600, 300, _run_blog),
    CronDef("wix_sync",           "WixSync",           14400, 600, _run_wix_sync),
    CronDef("geo_intelligence",   "GeoIntel",            300, 120, _run_geo_intel, default_enabled=False),
    CronDef("findmy_geofence",    "FindMyGeofence",      900, 180, _run_findmy, default_enabled=False),
    CronDef("auto_reply",         "AutoReplyBridge",      60,  30, _run_auto_reply_bridge),
    CronDef("speed_to_contact",   "SpeedToContact",     1800,  90, _run_speed_to_contact, default_enabled=False),
    CronDef("paperwork_chase",    "PaperworkChase",     3600, 150, _run_paperwork_chase, default_enabled=False),
    CronDef("intake_recovery",    "IntakeRecovery",     3600, 200, _run_intake_recovery, default_enabled=False),
    CronDef("overdue_tasks",      "OverdueTasks",       3600, 180, _run_overdue_tasks),
    CronDef("drip_scanner",       "DripScanner",        1800,  60, _run_drip_scanner),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Startup indexes (extracted from __init__.py @before_serving)
# ═══════════════════════════════════════════════════════════════════════════════

async def _ensure_all_indexes():
    """Create all MongoDB indexes on startup."""
    from dashboard.extensions import get_db
    db = get_db()
    warnings = []

    async def _idx(collection_name: str, keys, **kwargs):
        """Create one index, swallowing 'already exists' conflicts."""
        try:
            await db[collection_name].create_index(keys, **kwargs)
        except Exception as e:
            err_str = str(e)
            # IndexOptionsConflict (code 85) or "already exists" → harmless
            if "already exists" in err_str or "IndexOptionsConflict" in err_str:
                pass  # Index exists with same or compatible definition — fine
            else:
                idx_name = kwargs.get("name", str(keys))
                warnings.append(f"{collection_name}.{idx_name}: {e}")

    try:
        # Defendants
        await _idx("defendants", [("identity_key", 1)], unique=True, name="idx_identity_key_unique", background=True)
        await _idx("defendants", [("defendant_id", 1)], unique=True, name="idx_defendant_id_unique", background=True)
        await _idx("defendants", [("dob", 1)], name="idx_dob", background=True)
        await _idx("defendants", [("counties", 1)], name="idx_counties", background=True)
        await _idx("defendants", [("total_arrests", -1)], name="idx_total_arrests", background=True)
        await _idx("defendants", [("active", 1)], name="idx_active", background=True)
        await _idx("arrests", [("defendant_id", 1)], name="idx_defendant_id", background=True, sparse=True)
        # Tasks & Ledger
        await _idx("tasks", [("status", 1), ("due_date", 1)], name="idx_tasks_status_due", background=True)
        await _idx("tasks", [("booking_number", 1), ("status", 1)], name="idx_tasks_booking_status", background=True)
        await _idx("financial_ledger", [("booking_number", 1), ("timestamp", -1)], name="idx_ledger_booking_ts", background=True)
        await _idx("financial_ledger", [("stripe_swipe_ref", 1)], sparse=True, name="idx_ledger_swipe_ref", background=True)
        # Audit
        await _idx("audit_events", [("entity_id", 1), ("timestamp", -1)], name="idx_entity_id_timestamp", background=True)
        await _idx("audit_events", [("event_type", 1)], name="idx_event_type", background=True)
        await _idx("audit_events", [("timestamp", 1)], name="idx_audit_ttl_90d", background=True, expireAfterSeconds=7776000)
        await _idx("audit_events", [("entity_type", 1), ("entity_id", 1), ("timestamp", -1)], name="idx_audit_entity_lookup", background=True)
        # Matching
        await _idx("intake_queue", [("matched_booking_number", 1)], name="idx_matched_booking", background=True, sparse=True)
        await _idx("intake_queue", [("match_confidence", -1)], name="idx_match_confidence", background=True, sparse=True)
        await _idx("intake_queue", [("status", 1), ("created_at", -1)], name="idx_status_created", background=True)
        # Outreach + Paperwork
        await _idx("outreach_sequences", [("booking_number", 1), ("county", 1)], name="idx_booking_county", background=True)
        await _idx("outreach_sequences", [("status", 1)], name="idx_seq_status", background=True)
        await _idx("outreach_sequences", [("phone", 1), ("status", 1)], name="idx_phone_status", background=True)
        await _idx("outreach_messages", [("sequence_id", 1)], name="idx_msg_sequence_id", background=True)
        # Outreach Queue
        await _idx("outreach_queue", [("status", 1), ("next_attempt", 1)], name="idx_outreach_status_attempt", background=True)
        await _idx("outreach_queue", [("created_at", 1)], name="idx_outreach_ttl_30d", background=True, expireAfterSeconds=2592000)
        await _idx("paperwork_packets", [("intake_id", 1)], name="idx_pkt_intake_id", background=True)
        await _idx("paperwork_packets", [("packet_id", 1)], unique=True, name="idx_pkt_packet_id", background=True)
        await _idx("paperwork_packets", [("signnow_document_id", 1)], name="idx_pkt_signnow_doc_id", background=True, sparse=True)
        await _idx("paperwork_packets", [("signnow_invite_id", 1)], name="idx_pkt_signnow_invite_id", background=True, sparse=True)
        await _idx("paperwork_packets", [("bond_case_id", 1)], name="idx_pkt_bond_case_id", background=True, sparse=True)
        await _idx("paperwork_packets", [("voided", 1), ("status", 1)], name="idx_pkt_voided_status", background=True)
        # Court / Discharge / Calendar
        await _idx("court_reminders", [("booking_number", 1), ("reminder_type", 1), ("status", 1)], name="idx_cr_booking_type_status", background=True)
        await _idx("court_reminders", [("status", 1), ("send_at", 1)], name="idx_cr_status_sendat", background=True)
        await _idx("discharge_queue", [("status", 1)], name="idx_dq_status", background=True)
        await _idx("gcal_sync", [("dedup_key", 1)], unique=True, name="idx_gcal_dedup", background=True)
        await _idx("active_bonds", [("court_date", 1), ("status", 1)], name="idx_ab_court_status", background=True)
        await _idx("active_bonds", [("bond_date", 1), ("surety", 1)], name="idx_bonds_date_surety", background=True)
        await _idx("active_bonds", [("status", 1), ("updated_at", -1)], name="idx_bonds_status_updated", background=True)
        await _idx("active_bonds", [("agent_name", 1), ("bond_date", 1)], name="idx_bonds_agent_date", background=True)
        # Payments + Notifications
        await _idx("payment_plans", [("plan_id", 1)], unique=True, name="idx_plan_id", background=True)
        await _idx("payment_plans", [("booking_number", 1)], name="idx_pp_booking", background=True)
        await _idx("payment_plans", [("status", 1), ("next_due_date", 1)], name="idx_pp_status_due", background=True)
        await _idx("payments", [("booking_number", 1), ("timestamp", -1)], name="idx_pay_booking_ts", background=True)
        await _idx("payments", [("plan_id", 1)], name="idx_pay_plan", background=True)
        await _idx("notifications", [("notification_id", 1)], unique=True, name="idx_notif_id", background=True)
        await _idx("notifications", [("read", 1), ("dismissed", 1), ("created_at", -1)], name="idx_notif_unread", background=True)
        await _idx("notifications", [("type", 1), ("created_at", -1)], name="idx_notif_type", background=True)
        await _idx("notifications", [("created_at_dt", 1)], name="idx_notif_ttl_60d", background=True, expireAfterSeconds=5184000)
        # Portal
        await _idx("portal_tokens", [("token", 1)], unique=True, name="idx_token_unique", background=True)
        await _idx("portal_tokens", [("booking_number", 1), ("active", 1)], name="idx_portal_booking_active", background=True)
        await _idx("portal_tokens", [("expires_at", 1)], expireAfterSeconds=604800, name="idx_portal_ttl", background=True)
        await _idx("bond_checkins", [("booking_number", 1), ("checkin_at", -1)], name="idx_checkin_booking", background=True)
        # Geo
        await _idx("geo_devices", [("traccar_device_id", 1)], name="idx_geo_traccar_id", background=True)
        await _idx("geo_devices", [("booking_number", 1), ("status", 1)], name="idx_geo_booking_status", background=True)
        await _idx("geo_zones", [("booking_number", 1), ("active", 1)], name="idx_zone_booking_active", background=True)
        await _idx("geo_events", [("event_type", 1), ("created_at", -1)], name="idx_geo_evt_type_created", background=True)
        await _idx("geo_events", [("booking_number", 1), ("acknowledged", 1)], name="idx_geo_evt_booking_ack", background=True)
        await _idx("geo_vehicle_watch", [("status", 1), ("created_at", -1)], name="idx_vwatch_status", background=True)
        await _idx("geo_events", [("created_at", 1)], name="idx_geo_evt_ttl_90d", background=True, expireAfterSeconds=7776000)
        # Automation
        await _idx("automation_config", [("type", 1)], unique=True, name="idx_config_type", background=True)
        await _idx("automation_run_log", [("automation", 1), ("run_at", -1)], name="idx_auto_run", background=True)
        await _idx("automation_run_log", [("run_at", 1)], name="idx_auto_run_ttl", background=True, expireAfterSeconds=2592000)
        await _idx("paperwork_chase_log", [("packet_id", 1), ("chase_type", 1)], name="idx_chase_dedup", background=True)
        await _idx("intake_recovery_log", [("intake_id", 1), ("created_at", -1)], name="idx_recovery_dedup", background=True)
        # TTL
        await _idx("error_log", [("timestamp", 1)], name="idx_ttl_30d", background=True, expireAfterSeconds=2592000)
        await _idx("rearrest_alerts", [("detected_at", 1)], name="idx_rearrest_ttl_60d", background=True, expireAfterSeconds=5184000)
        # Social OAuth
        await _idx("social_accounts", [("platform", 1), ("account_id", 1)], unique=True, name="idx_social_platform_account", background=True)
        await _idx("social_accounts", [("status", 1), ("token_expires_at", 1)], name="idx_social_status_expiry", background=True)

        if warnings:
            logger.warning("Index setup had %d issue(s): %s", len(warnings), "; ".join(warnings))
        else:
            logger.info("☘️  All MongoDB indexes ensured")
    except Exception as e:
        logger.warning("Index setup fatal error: %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
# Startup orchestration
# ═══════════════════════════════════════════════════════════════════════════════

async def _startup_tasks():
    """One-time startup tasks (webhooks, inbox poller, indexes)."""
    await _ensure_all_indexes()

    # BB webhook auto-registration
    vps_url = os.getenv("BB_WEBHOOK_PUBLIC_URL", "").rstrip("/")
    if vps_url:
        try:
            from dashboard.routers.bb_private_api import BlueBubblesClient
            from dashboard.extensions import BB_SERVERS
            from dashboard.routers.bb_webhook_receiver import BB_WEBHOOK_EVENTS
            webhook_url = f"{vps_url}/api/webhooks/bluebubbles"
            for suffix, server in BB_SERVERS.items():
                try:
                    client = BlueBubblesClient(server["url"], server["password"])
                    await client.ensure_webhook(webhook_url, BB_WEBHOOK_EVENTS)
                    logger.info("BB webhook registered for %s", server["label"])
                except Exception as e:
                    logger.warning("BB webhook failed for %s: %s", server["label"], e)
        except Exception as e:
            logger.warning("BB webhook setup error: %s", e)

    # Firebase BB URL sync
    try:
        from dashboard.routers.bb_firebase_sync import poll_firebase_for_bb_url
        asyncio.ensure_future(poll_firebase_for_bb_url())
    except Exception as e:
        logger.debug("Firebase BB URL sync skipped: %s", e)

    # Inbox poller
    try:
        from dashboard.routers.imessage_automation import start_inbox_poller
        asyncio.ensure_future(start_inbox_poller(None))
    except Exception as e:
        logger.debug("Inbox poller start skipped: %s", e)


async def start_all_crons() -> List[asyncio.Task]:
    """Launch all background crons + one-time startup tasks. Returns task handles."""
    await _startup_tasks()
    tasks = []
    for cron in CRON_REGISTRY:
        task = asyncio.create_task(_cron_runner(cron), name=f"cron_{cron.name}")
        tasks.append(task)
        logger.info("☘️  Scheduled: %s (every %ds, first in %ds)", cron.label, cron.interval, cron.initial_delay)
    return tasks
