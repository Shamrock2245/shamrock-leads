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
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional, List

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
    from dashboard.api.automation_control import register_trigger
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
        except Exception:
            pass
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
        except Exception:
            pass
    if enriched:
        logger.info("[NLP-Enrich] ✅ %d enriched", enriched)

async def _run_bb_health():
    from dashboard.api.bb_health_monitor import run_health_check_all
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
    from dashboard.api.notifications import create_notification
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
    from dashboard.api.rearrest_detector import scan_for_rearrests
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

async def _run_retention():
    from dashboard.api.data_retention import _execute_purge
    result = await _execute_purge(dry_run=False)
    total = result.get("total_purged", 0)
    if total:
        logger.info("[DataRetention] purged %s records", total)

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
        db = c[os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")] if c else None
        r = BlogScheduler(db=db).run()
        if c:
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
    from dashboard.services.geo_intelligence import GeoIntelligenceService
    from dashboard.services.traccar_client import TraccarClient
    from dashboard.extensions import get_db
    svc = GeoIntelligenceService(db=get_db())
    devices = await svc.list_devices()
    active = [d for d in devices if d.get("status") == "active"]
    for dev in active:
        try:
            tid = dev.get("traccar_device_id")
            if not tid:
                continue
            pos = await TraccarClient().get_latest_position(tid)
            if pos:
                await svc.sync_position(device_id=str(dev["_id"]), lat=pos["latitude"],
                    lng=pos["longitude"], accuracy=pos.get("accuracy"), source="geo_cron")
        except Exception:
            pass

async def _run_findmy():
    from dashboard.extensions import get_db
    from dashboard.services.automation_config import get_automation_config
    cfg = await get_automation_config(get_db())
    fm = cfg.get("findmy_geofence", {})
    bb_url = os.getenv("BB_SERVER_URL")
    if not bb_url:
        return
    from dashboard.api.bb_private_api import BlueBubblesPrivateClient
    from dashboard.api.geo_geofence_patch import haversine_distance
    from datetime import datetime, timezone
    client = BlueBubblesPrivateClient(base_url=bb_url, password=os.getenv("BB_SERVER_PASSWORD"))
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
        {"type": "auto_reply"}, {"$set": {"enabled": new_state}}, upsert=False)

async def _run_speed_to_contact():
    from dashboard.services.outreach_sequencer import OutreachSequencer
    from dashboard.services.automation_config import get_automation_config
    from dashboard.extensions import get_db
    from datetime import datetime, timezone
    db = get_db()
    cfg = await get_automation_config(db)
    stc = cfg.get("speed_to_contact", {})
    seq = OutreachSequencer(db)
    result = await seq.batch_start_new_arrests(hours_back=stc.get("hours_back", 1), limit=stc.get("max_per_cycle", 20))
    await db["automation_run_log"].insert_one({"automation": "speed_to_contact", "run_at": datetime.now(timezone.utc), "result": result})
    if result.get("started", 0):
        logger.info("[SpeedToContact] started=%s", result["started"])

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


# ═══════════════════════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════════════════════

CRON_REGISTRY: List[CronDef] = [
    CronDef("alpha_engine",       "AlphaEngine",       14400, 180, _run_alpha_engine),
    CronDef("docket_monitor",     "DocketMonitor",     14400, 120, _run_docket_monitor),
    CronDef("court_intel",        "CourtIntel",        21600,  60, _run_court_intel),
    CronDef("nlp_enrichment",     "NLP-Enrich",         7200,  90, _run_nlp_enrichment),
    CronDef("bb_health",          "BB-Health",           600,  30, _run_bb_health),
    CronDef("court_reminders",    "CourtReminder",      3600,  60, _run_court_reminders),
    CronDef("delinquency_scanner","Delinquency",       14400, 120, _run_delinquency),
    CronDef("rearrest_detection", "ReArrest",           7200, 180, _run_rearrest),
    CronDef("fta_alert",          "FTAAlert",          14400, 120, _run_fta_alert),
    CronDef("data_retention",     "DataRetention",    604800, 300, _run_retention),
    CronDef("court_email",        "CourtEmail",          900,  90, _run_court_email),
    CronDef("blog_publisher",     "Blog",              21600, 300, _run_blog),
    CronDef("wix_sync",           "WixSync",           14400, 600, _run_wix_sync),
    CronDef("geo_intelligence",   "GeoIntel",            300, 120, _run_geo_intel, default_enabled=False),
    CronDef("findmy_geofence",    "FindMyGeofence",      900, 180, _run_findmy, default_enabled=False),
    CronDef("auto_reply",         "AutoReplyBridge",      60,  30, _run_auto_reply_bridge),
    CronDef("speed_to_contact",   "SpeedToContact",     1800,  90, _run_speed_to_contact, default_enabled=False),
    CronDef("paperwork_chase",    "PaperworkChase",     3600, 150, _run_paperwork_chase, default_enabled=False),
    CronDef("intake_recovery",    "IntakeRecovery",     3600, 200, _run_intake_recovery, default_enabled=False),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Startup indexes (extracted from __init__.py @before_serving)
# ═══════════════════════════════════════════════════════════════════════════════

async def _ensure_all_indexes():
    """Create all MongoDB indexes on startup."""
    from dashboard.extensions import get_db
    db = get_db()
    try:
        # Defendants
        await db["defendants"].create_index([("identity_key", 1)], unique=True, name="idx_identity_key_unique", background=True)
        await db["defendants"].create_index([("defendant_id", 1)], unique=True, name="idx_defendant_id_unique", background=True)
        await db["defendants"].create_index([("dob", 1)], name="idx_dob", background=True)
        await db["defendants"].create_index([("counties", 1)], name="idx_counties", background=True)
        await db["defendants"].create_index([("total_arrests", -1)], name="idx_total_arrests", background=True)
        await db["defendants"].create_index([("active", 1)], name="idx_active", background=True)
        await db["arrests"].create_index([("defendant_id", 1)], name="idx_defendant_id", background=True, sparse=True)
        # Audit
        await db["audit_events"].create_index([("entity_id", 1), ("timestamp", -1)], name="idx_entity_id_timestamp", background=True)
        await db["audit_events"].create_index([("event_type", 1)], name="idx_event_type", background=True)
        await db["audit_events"].create_index([("timestamp", 1)], name="idx_audit_ttl_90d", background=True, expireAfterSeconds=7776000)
        await db["audit_events"].create_index([("entity_type", 1), ("entity_id", 1), ("timestamp", -1)], name="idx_audit_entity_lookup", background=True)
        # Matching
        await db["intake_queue"].create_index([("matched_booking_number", 1)], name="idx_matched_booking", background=True, sparse=True)
        await db["intake_queue"].create_index([("match_confidence", -1)], name="idx_match_confidence", background=True, sparse=True)
        await db["intake_queue"].create_index([("status", 1), ("created_at", -1)], name="idx_status_created", background=True)
        # Outreach + Paperwork
        await db["outreach_sequences"].create_index([("booking_number", 1), ("county", 1)], name="idx_booking_county", background=True)
        await db["outreach_sequences"].create_index([("status", 1)], name="idx_seq_status", background=True)
        await db["outreach_sequences"].create_index([("phone", 1), ("status", 1)], name="idx_phone_status", background=True)
        await db["outreach_messages"].create_index([("sequence_id", 1)], name="idx_msg_sequence_id", background=True)
        await db["paperwork_packets"].create_index([("intake_id", 1)], name="idx_pkt_intake_id", background=True)
        await db["paperwork_packets"].create_index([("packet_id", 1)], unique=True, name="idx_pkt_packet_id", background=True)
        await db["paperwork_packets"].create_index([("signnow_document_id", 1)], name="idx_pkt_signnow_doc_id", background=True, sparse=True)
        await db["paperwork_packets"].create_index([("signnow_invite_id", 1)], name="idx_pkt_signnow_invite_id", background=True, sparse=True)
        await db["paperwork_packets"].create_index([("bond_case_id", 1)], name="idx_pkt_bond_case_id", background=True, sparse=True)
        await db["paperwork_packets"].create_index([("voided", 1), ("status", 1)], name="idx_pkt_voided_status", background=True)
        # Court / Discharge / Calendar
        await db["court_reminders"].create_index([("booking_number", 1), ("reminder_type", 1), ("status", 1)], name="idx_cr_booking_type_status", background=True)
        await db["court_reminders"].create_index([("status", 1), ("send_at", 1)], name="idx_cr_status_sendat", background=True)
        await db["discharge_queue"].create_index([("status", 1)], name="idx_dq_status", background=True)
        await db["gcal_sync"].create_index([("dedup_key", 1)], unique=True, name="idx_gcal_dedup", background=True)
        await db["active_bonds"].create_index([("court_date", 1), ("status", 1)], name="idx_ab_court_status", background=True)
        await db["active_bonds"].create_index([("bond_date", 1), ("surety", 1)], name="idx_bonds_date_surety", background=True)
        await db["active_bonds"].create_index([("status", 1), ("updated_at", -1)], name="idx_bonds_status_updated", background=True)
        await db["active_bonds"].create_index([("agent_name", 1), ("bond_date", 1)], name="idx_bonds_agent_date", background=True)
        # Payments + Notifications
        await db["payment_plans"].create_index([("plan_id", 1)], unique=True, name="idx_plan_id", background=True)
        await db["payment_plans"].create_index([("booking_number", 1)], name="idx_pp_booking", background=True)
        await db["payment_plans"].create_index([("status", 1), ("next_due_date", 1)], name="idx_pp_status_due", background=True)
        await db["payments"].create_index([("booking_number", 1), ("timestamp", -1)], name="idx_pay_booking_ts", background=True)
        await db["payments"].create_index([("plan_id", 1)], name="idx_pay_plan", background=True)
        await db["notifications"].create_index([("notification_id", 1)], unique=True, name="idx_notif_id", background=True)
        await db["notifications"].create_index([("read", 1), ("dismissed", 1), ("created_at", -1)], name="idx_notif_unread", background=True)
        await db["notifications"].create_index([("type", 1), ("created_at", -1)], name="idx_notif_type", background=True)
        await db["notifications"].create_index([("created_at_dt", 1)], name="idx_notif_ttl_60d", background=True, expireAfterSeconds=5184000)
        # Portal
        await db["portal_tokens"].create_index([("token", 1)], unique=True, name="idx_token_unique", background=True)
        await db["portal_tokens"].create_index([("booking_number", 1), ("active", 1)], name="idx_portal_booking_active", background=True)
        await db["portal_tokens"].create_index([("expires_at", 1)], expireAfterSeconds=604800, name="idx_portal_ttl", background=True)
        await db["bond_checkins"].create_index([("booking_number", 1), ("checkin_at", -1)], name="idx_checkin_booking", background=True)
        # Geo
        await db["geo_devices"].create_index([("traccar_device_id", 1)], name="idx_geo_traccar_id", background=True)
        await db["geo_devices"].create_index([("booking_number", 1), ("status", 1)], name="idx_geo_booking_status", background=True)
        await db["geo_zones"].create_index([("booking_number", 1), ("active", 1)], name="idx_zone_booking_active", background=True)
        await db["geo_events"].create_index([("event_type", 1), ("created_at", -1)], name="idx_geo_evt_type_created", background=True)
        await db["geo_events"].create_index([("booking_number", 1), ("acknowledged", 1)], name="idx_geo_evt_booking_ack", background=True)
        await db["geo_vehicle_watch"].create_index([("status", 1), ("created_at", -1)], name="idx_vwatch_status", background=True)
        await db["geo_events"].create_index([("created_at", 1)], name="idx_geo_evt_ttl_90d", background=True, expireAfterSeconds=7776000)
        # Automation
        await db["automation_config"].create_index([("type", 1)], unique=True, name="idx_config_type", background=True)
        await db["automation_run_log"].create_index([("automation", 1), ("run_at", -1)], name="idx_auto_run", background=True)
        await db["automation_run_log"].create_index([("run_at", 1)], name="idx_auto_run_ttl", background=True, expireAfterSeconds=2592000)
        await db["paperwork_chase_log"].create_index([("packet_id", 1), ("chase_type", 1)], name="idx_chase_dedup", background=True)
        await db["intake_recovery_log"].create_index([("intake_id", 1), ("created_at", -1)], name="idx_recovery_dedup", background=True)
        # TTL
        await db["error_log"].create_index([("timestamp", 1)], name="idx_error_ttl_30d", background=True, expireAfterSeconds=2592000)
        await db["rearrest_alerts"].create_index([("detected_at", 1)], name="idx_rearrest_ttl_60d", background=True, expireAfterSeconds=5184000)
        logger.info("☘️  All MongoDB indexes ensured")
    except Exception as e:
        logger.warning("Index setup warning: %s", e)


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
            from dashboard.api.bb_private_api import BlueBubblesClient
            from dashboard.extensions import BB_SERVERS
            from dashboard.api.bb_webhook_receiver import BB_WEBHOOK_EVENTS
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
        from dashboard.api.bb_firebase_sync import poll_firebase_for_bb_url
        asyncio.ensure_future(poll_firebase_for_bb_url())
    except Exception:
        pass

    # Inbox poller
    try:
        from dashboard.api.imessage_automation import start_inbox_poller
        asyncio.ensure_future(start_inbox_poller(None))
    except Exception:
        pass


async def start_all_crons() -> List[asyncio.Task]:
    """Launch all background crons + one-time startup tasks. Returns task handles."""
    await _startup_tasks()
    tasks = []
    for cron in CRON_REGISTRY:
        task = asyncio.create_task(_cron_runner(cron), name=f"cron_{cron.name}")
        tasks.append(task)
        logger.info("☘️  Scheduled: %s (every %ds, first in %ds)", cron.label, cron.interval, cron.initial_delay)
    return tasks
