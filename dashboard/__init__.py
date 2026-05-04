"""ShamrockLeads Dashboard — Quart Application Factory

Production entry point for the async dashboard API.
All blueprints use Motor (async MongoDB) via extensions.get_collection().
"""
from quart import Quart, send_from_directory
from quart_cors import cors
import os
import logging

logger = logging.getLogger(__name__)


def create_app():
    app = Quart(__name__, static_folder=os.path.dirname(__file__), static_url_path="")

    # ── CORS — allow dashboard frontend and external callers ──
    app = cors(app, allow_origin="*")

    # ── Initialize extensions (Motor, BlueBubbles, POA seed, secret key) ──
    from dashboard.extensions import init_app
    init_app(app)

    # ── Register ALL API Blueprints ──
    from dashboard.api.stats import stats_bp
    from dashboard.api.bonds import bonds_bp
    from dashboard.api.poa import poa_bp
    from dashboard.api.leads import leads_bp
    from dashboard.api.arrests import arrests_bp
    from dashboard.api.defendants import defendants_bp
    from dashboard.api.events import events_bp
    from dashboard.api.payments import payments_bp
    from dashboard.api.webhooks import webhooks_bp
    from dashboard.api.tracking import tracking_bp
    from dashboard.api.court_reminders import court_reminders_bp
    from dashboard.api.contacts import contacts_bp
    from dashboard.api.bond_lifecycle import bond_lifecycle_bp
    from dashboard.api.intake import intake_bp          # ← Indemnitor Intake Queue
    from dashboard.api.scraper_control import scraper_control_bp  # ← Run-Now Control
    from dashboard.api.prospective_bonds import prospective_bonds_bp  # ← In Progress Pipeline
    from dashboard.api.geo import geo_bp  # ← Silent Geo-Link Capture

    app.register_blueprint(stats_bp, url_prefix="/api")
    app.register_blueprint(bonds_bp, url_prefix="/api")
    app.register_blueprint(poa_bp, url_prefix="/api")
    app.register_blueprint(leads_bp, url_prefix="/api")
    app.register_blueprint(arrests_bp, url_prefix="/api")
    app.register_blueprint(defendants_bp, url_prefix="/api")
    app.register_blueprint(events_bp, url_prefix="/api")
    app.register_blueprint(payments_bp, url_prefix="/api")
    app.register_blueprint(webhooks_bp, url_prefix="/api")
    app.register_blueprint(tracking_bp, url_prefix="/api")
    app.register_blueprint(court_reminders_bp, url_prefix="/api")
    app.register_blueprint(contacts_bp, url_prefix="/api")
    app.register_blueprint(bond_lifecycle_bp, url_prefix="/api/bond-lifecycle")
    app.register_blueprint(intake_bp, url_prefix="/api")  # ← Indemnitor Intake Queue
    app.register_blueprint(scraper_control_bp, url_prefix="/api")  # ← Run-Now Control
    app.register_blueprint(prospective_bonds_bp, url_prefix="/api")  # ← In Progress Pipeline
    app.register_blueprint(geo_bp)  # ← Geo routes: /api/geo/* and /g/<token>

    # ── AI Agent Brain API (Pipeline AI feature bar) ──────────────────────────
    from dashboard.api.agent_brain_api import agent_brain_api_bp
    app.register_blueprint(agent_brain_api_bp, url_prefix="/api")  # ← AI Outreach Endpoints
    # ── Revenue Analytics ──────────────────────────────────────────────────────
    from dashboard.api.analytics import analytics_bp
    app.register_blueprint(analytics_bp, url_prefix="/api")

    # ── Agency Reports (Discharged, Liability, Forfeitures, Agent Production) ─
    from dashboard.api.reports import reports_bp
    app.register_blueprint(reports_bp, url_prefix="/api")

    # ── Lead Intelligence (AI scoring explanations, trends, charge severity) ─────
    from dashboard.api.lead_intelligence import lead_intel_bp
    app.register_blueprint(lead_intel_bp, url_prefix="/api")

    # ── Court Calendar ─────────────────────────────────────────────────────────
    from dashboard.api.calendar import calendar_bp
    app.register_blueprint(calendar_bp, url_prefix="/api")

    # ── Phase 4: Matching Engine ─────────────────────────────────────────────
    from dashboard.api.matching import matching_bp
    app.register_blueprint(matching_bp, url_prefix="/api")

    # ── Phase 6: Paperwork Generation ────────────────────────────────────────
    from dashboard.api.paperwork import paperwork_bp
    app.register_blueprint(paperwork_bp, url_prefix="/api")

    # ── Phase 10: Outreach Sequencing ─────────────────────────────────────────
    from dashboard.api.outreach import outreach_bp
    app.register_blueprint(outreach_bp, url_prefix="/api")

    # ── Defendant Lifecycle (Notes, DNB/DNC, Contact Log, Bond Finalization) ──
    from dashboard.api.defendant_lifecycle import lifecycle_bp
    app.register_blueprint(lifecycle_bp, url_prefix="/api")

    # ── Bond Lifecycle Timeline (unified cross-collection timeline) ──────────
    from dashboard.api.lifecycle_timeline import lifecycle_timeline_bp
    app.register_blueprint(lifecycle_timeline_bp, url_prefix="/api")

    # ── Feature J: Discharge Monitor (Gmail → auto-exonerate) ──
    from dashboard.api.discharge_monitor import discharge_monitor_bp
    app.register_blueprint(discharge_monitor_bp, url_prefix="/api")

    # ── Payment Plans (Phase 8: The Payment Agent) ─────────────────────────────
    from dashboard.api.payment_plans import payment_plans_bp
    app.register_blueprint(payment_plans_bp, url_prefix="/api")

    # ── Ops Summary (Daily Intelligence Report) ──────────────────────────────
    from dashboard.api.ops_summary import ops_summary_bp
    app.register_blueprint(ops_summary_bp, url_prefix="/api")

    # ── Notification Center (Centralized Alerts) ─────────────────────────────
    from dashboard.api.notifications import notifications_bp
    app.register_blueprint(notifications_bp, url_prefix="/api")

    # ── Re-Arrest Detector (Cross-references arrests vs active bonds) ───────
    from dashboard.api.rearrest_detector import rearrest_bp
    app.register_blueprint(rearrest_bp, url_prefix="/api")

    # ── Data Retention (Auto-purge policy for MongoDB M0 512MB ceiling) ──────
    from dashboard.api.data_retention import retention_bp
    app.register_blueprint(retention_bp, url_prefix="/api")

    # ── Legacy compatibility blueprint (iMessage, cleanup, custody, db-health) ──
    from dashboard.api.legacy import legacy_bp
    app.register_blueprint(legacy_bp, url_prefix="/api")

    # ── iMessage Automation (auto-reply, inbox polling, Private API proxy) ──
    from dashboard.api.imessage_automation import imessage_auto_bp, start_inbox_poller
    app.register_blueprint(imessage_auto_bp, url_prefix="/api")

    # ── BlueBubbles Enhancement Suite (Phase 2) ──────────────────────────────
    from dashboard.api.bb_webhook_receiver import bb_webhook_bp
    from dashboard.api.rearrest_notifier import rearrest_bp
    from dashboard.api.bb_prospecting import bb_prospecting_bp
    from dashboard.api.bb_scheduled_messages import bb_schedule_bp
    from dashboard.api.bb_document_delivery import bb_docs_bp
    from dashboard.api.bb_contact_sync import bb_contacts_bp
    from dashboard.api.bb_health_monitor import bb_health_bp
    app.register_blueprint(bb_webhook_bp, url_prefix="/api")
    app.register_blueprint(rearrest_bp, url_prefix="/api")
    app.register_blueprint(bb_prospecting_bp, url_prefix="/api")
    app.register_blueprint(bb_schedule_bp, url_prefix="/api")
    app.register_blueprint(bb_docs_bp, url_prefix="/api")
    app.register_blueprint(bb_contacts_bp, url_prefix="/api")
    app.register_blueprint(bb_health_bp, url_prefix="/api")
    # Start background inbox poller (fallback — webhook is primary)
    @app.before_serving
    async def _start_inbox_poller():
        import asyncio
        asyncio.ensure_future(start_inbox_poller(app))

    # Auto-register BB webhooks with both iMac servers on startup
    @app.before_serving
    async def _register_bb_webhooks():
        """Register our VPS URL as a webhook on all BlueBubbles servers."""
        import asyncio
        vps_url = os.getenv("BB_WEBHOOK_PUBLIC_URL", "").rstrip("/")
        if not vps_url:
            logger.warning("BB_WEBHOOK_PUBLIC_URL not set — skipping auto webhook registration")
            return
        from dashboard.api.bb_private_api import BlueBubblesClient
        from dashboard.extensions import BB_SERVERS
        from dashboard.api.bb_webhook_receiver import BB_WEBHOOK_EVENTS
        webhook_url = f"{vps_url}/api/webhooks/bluebubbles"
        for suffix, server in BB_SERVERS.items():
            try:
                client = BlueBubblesClient(server["url"], server["password"])
                result = await client.ensure_webhook(webhook_url, BB_WEBHOOK_EVENTS)
                logger.info(
                    "BB webhook auto-registered for %s: success=%s already_existed=%s",
                    server["label"], result.get("success"), result.get("already_existed")
                )
            except Exception as e:
                logger.warning("BB webhook auto-registration failed for %s: %s", server["label"], e)

    # ── Phase 2: Defendant Normalization — ensure indexes on startup ──────────
    @app.before_serving
    async def _ensure_defendant_indexes():
        """Create MongoDB indexes for defendants + audit_events collections."""
        try:
            from dashboard.extensions import get_db
            db = get_db()
            defendants_col = db["defendants"]
            arrests_col = db["arrests"]
            audit_col = db["audit_events"]

            # defendants indexes
            await defendants_col.create_index(
                [("identity_key", 1)], unique=True, name="idx_identity_key_unique", background=True
            )
            await defendants_col.create_index(
                [("defendant_id", 1)], unique=True, name="idx_defendant_id_unique", background=True
            )
            await defendants_col.create_index(
                [("dob", 1)], name="idx_dob", background=True
            )
            await defendants_col.create_index(
                [("counties", 1)], name="idx_counties", background=True
            )
            await defendants_col.create_index(
                [("total_arrests", -1)], name="idx_total_arrests", background=True
            )
            await defendants_col.create_index(
                [("active", 1)], name="idx_active", background=True
            )
            # arrests: sparse index for defendant_id back-reference
            await arrests_col.create_index(
                [("defendant_id", 1)], name="idx_defendant_id", background=True, sparse=True
            )
            # audit_events indexes
            await audit_col.create_index(
                [("entity_id", 1), ("timestamp", -1)],
                name="idx_entity_id_timestamp", background=True
            )
            await audit_col.create_index(
                [("event_type", 1)], name="idx_event_type", background=True
            )
            logger.info("☘️  Phase 2: defendant indexes ensured")
        except Exception as exc:
            logger.warning("Phase 2 index setup warning: %s", exc)

    # ── Phase 4: Matching Engine — ensure indexes on startup ─────────────────
    @app.before_serving
    async def _ensure_matching_indexes():
        """Create MongoDB indexes for intake_queue matching fields."""
        try:
            from dashboard.extensions import get_db
            db = get_db()
            intake_col = db["intake_queue"]
            await intake_col.create_index(
                [("matched_booking_number", 1)], name="idx_matched_booking", background=True, sparse=True
            )
            await intake_col.create_index(
                [("match_confidence", -1)], name="idx_match_confidence", background=True, sparse=True
            )
            await intake_col.create_index(
                [("status", 1), ("created_at", -1)], name="idx_status_created", background=True
            )
            logger.info("☘️  Phase 4: matching engine indexes ensured")
        except Exception as exc:
            logger.warning("Phase 4 index setup warning: %s", exc)

    # ── Phase 10: Outreach Sequencing — ensure indexes on startup ─────────────
    @app.before_serving
    async def _ensure_outreach_indexes():
        """Create MongoDB indexes for outreach_sequences and outreach_messages."""
        try:
            from dashboard.extensions import get_db
            db = get_db()
            seqs_col = db["outreach_sequences"]
            msgs_col = db["outreach_messages"]
            pkts_col = db["paperwork_packets"]
            await seqs_col.create_index(
                [("booking_number", 1), ("county", 1)], name="idx_booking_county", background=True
            )
            await seqs_col.create_index(
                [("status", 1)], name="idx_seq_status", background=True
            )
            await seqs_col.create_index(
                [("phone", 1), ("status", 1)], name="idx_phone_status", background=True
            )
            await msgs_col.create_index(
                [("sequence_id", 1)], name="idx_msg_sequence_id", background=True
            )
            await pkts_col.create_index(
                [("intake_id", 1)], name="idx_pkt_intake_id", background=True
            )
            await pkts_col.create_index(
                [("packet_id", 1)], unique=True, name="idx_pkt_packet_id", background=True
            )
            logger.info("☘️  Phase 10: outreach + paperwork indexes ensured")
        except Exception as exc:
            logger.warning("Phase 10 index setup warning: %s", exc)

    # ── Tier 2-3: Court reminders, discharge, GCal indexes ───────────────────
    @app.before_serving
    async def _ensure_tier23_indexes():
        try:
            from dashboard.extensions import get_db
            db = get_db()
            # court_reminders: used by auto-scan dedup + process_due
            await db["court_reminders"].create_index(
                [("booking_number", 1), ("reminder_type", 1), ("status", 1)],
                name="idx_cr_booking_type_status", background=True,
            )
            await db["court_reminders"].create_index(
                [("status", 1), ("send_at", 1)],
                name="idx_cr_status_sendat", background=True,
            )
            # discharge_queue: used by process endpoint
            await db["discharge_queue"].create_index(
                [("status", 1)], name="idx_dq_status", background=True,
            )
            # gcal_sync: dedup key
            await db["gcal_sync"].create_index(
                [("dedup_key", 1)], unique=True, name="idx_gcal_dedup", background=True,
            )
            # active_bonds: court date scan performance
            await db["active_bonds"].create_index(
                [("court_date", 1), ("status", 1)],
                name="idx_ab_court_status", background=True,
            )
            logger.info("☘️  Tier 2-3: court/discharge/gcal indexes ensured")
        except Exception as exc:
            logger.warning("Tier 2-3 index setup warning: %s", exc)

    # ── Phase 8: Payment Plans + Notification Center indexes ─────────────────
    @app.before_serving
    async def _ensure_phase8_indexes():
        try:
            from dashboard.extensions import get_db
            db = get_db()
            # payment_plans
            await db["payment_plans"].create_index(
                [("plan_id", 1)], unique=True, name="idx_plan_id", background=True,
            )
            await db["payment_plans"].create_index(
                [("booking_number", 1)], name="idx_pp_booking", background=True,
            )
            await db["payment_plans"].create_index(
                [("status", 1), ("next_due_date", 1)], name="idx_pp_status_due", background=True,
            )
            # payments
            await db["payments"].create_index(
                [("booking_number", 1), ("timestamp", -1)], name="idx_pay_booking_ts", background=True,
            )
            await db["payments"].create_index(
                [("plan_id", 1)], name="idx_pay_plan", background=True,
            )
            # notifications
            await db["notifications"].create_index(
                [("notification_id", 1)], unique=True, name="idx_notif_id", background=True,
            )
            await db["notifications"].create_index(
                [("read", 1), ("dismissed", 1), ("created_at", -1)],
                name="idx_notif_unread", background=True,
            )
            await db["notifications"].create_index(
                [("type", 1), ("created_at", -1)], name="idx_notif_type", background=True,
            )
            logger.info("☘️  Phase 8: payment + notification indexes ensured")
        except Exception as exc:
            logger.warning("Phase 8 index setup warning: %s", exc)

    # BB health monitor loop — checks every 10 minutes, alerts Slack on issues
    @app.before_serving
    async def _start_bb_health_monitor():
        import asyncio
        from dashboard.api.bb_health_monitor import run_health_check_all
        async def _health_loop():
            await asyncio.sleep(30)  # Initial delay — let servers start first
            while True:
                try:
                    await run_health_check_all()
                except Exception as e:
                    logger.warning("BB health check error: %s", e)
                await asyncio.sleep(600)  # Every 10 minutes
        asyncio.ensure_future(_health_loop())

    # ── Feature I: Court Reminder Auto-Scan (hourly cron) ───────────────────
    @app.before_serving
    async def _start_court_reminder_cron():
        """Hourly background loop: scan active bonds for upcoming court dates,
        auto-schedule 4-touch reminders, and send any that are due."""
        import asyncio
        async def _reminder_loop():
            await asyncio.sleep(60)  # Initial delay — let DB connect first
            _cycle = 0
            while True:
                _cycle += 1
                try:
                    from dashboard.services.court_reminder_service import CourtReminderService
                    service = CourtReminderService(app.db)
                    scan = await service.auto_scan_and_schedule()
                    send = await service.process_due_reminders()
                    logger.info(
                        "☘️  Court reminder cron [cycle %s]: scanned=%s scheduled=%s sent=%s failed=%s",
                        _cycle, scan.get("bonds_scanned", 0), scan.get("scheduled", 0),
                        send.get("sent", 0), send.get("failed", 0),
                    )
                    # Heartbeat every 6th cycle (~6 hours) to confirm loop is alive
                    if _cycle % 6 == 0:
                        logger.info("☘️  Court reminder cron heartbeat: cycle=%s still running", _cycle)
                except Exception as e:
                    logger.warning("Court reminder cron error [cycle %s]: %s", _cycle, e)
                await asyncio.sleep(3600)  # Every hour
        asyncio.ensure_future(_reminder_loop())

    # ── Phase 8: Payment Delinquency Scanner (every 4 hours) ─────────────────
    @app.before_serving
    async def _start_delinquency_scanner():
        """Check for overdue payment plans and create notifications."""
        import asyncio
        async def _delinquency_loop():
            await asyncio.sleep(120)  # Initial delay
            while True:
                try:
                    from dashboard.extensions import get_db
                    from dashboard.api.notifications import create_notification
                    from datetime import datetime, timezone, timedelta
                    db = get_db()
                    plans_col = db["payment_plans"]
                    now = datetime.now(timezone.utc)
                    cutoff = (now - timedelta(days=30)).isoformat()

                    cursor = plans_col.find({
                        "status": "active",
                        "next_due_date": {"$lt": cutoff},
                        "delinquent": {"$ne": True},
                    })
                    flagged = 0
                    async for plan in cursor:
                        # Flag as delinquent
                        await plans_col.update_one(
                            {"plan_id": plan["plan_id"]},
                            {"$set": {"delinquent": True, "updated_at": now.isoformat()}}
                        )
                        # Create notification
                        await create_notification(
                            notification_type="delinquent",
                            title=f"Delinquent: {plan.get('defendant_name', plan['booking_number'])}",
                            message=f"Payment plan overdue. Balance: ${plan.get('balance_remaining', 0):,.2f}",
                            entity_id=plan["booking_number"],
                            entity_type="payment_plan",
                            metadata={"plan_id": plan["plan_id"], "balance": plan.get("balance_remaining", 0)},
                        )
                        flagged += 1

                    if flagged > 0:
                        logger.info("☘️  Delinquency scanner: flagged %s plans", flagged)
                except Exception as e:
                    logger.warning("Delinquency scanner error: %s", e)
                await asyncio.sleep(14400)  # Every 4 hours
        asyncio.ensure_future(_delinquency_loop())

    # ── Re-Arrest Detection Cron (every 2 hours) ────────────────────────────
    @app.before_serving
    async def _start_rearrest_cron():
        """Scan recent arrests against active bonds to detect re-arrests."""
        import asyncio
        async def _rearrest_loop():
            await asyncio.sleep(180)  # Initial delay
            while True:
                try:
                    from dashboard.api.rearrest_detector import scan_for_rearrests
                    result = await scan_for_rearrests(hours=3)
                    if result.get("detected", 0) > 0:
                        logger.warning(
                            "🔄 RE-ARREST DETECTED: %s match(es) found!",
                            result["detected"]
                        )
                    else:
                        logger.debug("Re-arrest scan: clean (%s arrests checked)", result.get("scanned_arrests", 0))
                except Exception as e:
                    logger.warning("Re-arrest scan error: %s", e)
                await asyncio.sleep(7200)  # Every 2 hours
        asyncio.ensure_future(_rearrest_loop())

    # ── Data Retention Cron (weekly purge) ──────────────────────────────────
    @app.before_serving
    async def _start_retention_cron():
        """Weekly auto-purge of old low-value records to stay under MongoDB M0 512MB."""
        import asyncio
        async def _retention_loop():
            await asyncio.sleep(300)  # Initial delay
            while True:
                try:
                    from dashboard.api.data_retention import _execute_purge
                    result = await _execute_purge(dry_run=False)
                    total = result.get("total_purged", 0)
                    if total > 0:
                        logger.info("☘️  Data retention: purged %s records", total)
                except Exception as e:
                    logger.warning("Data retention error: %s", e)
                await asyncio.sleep(604800)  # Every 7 days
        asyncio.ensure_future(_retention_loop())

    # ── PIN Auth (optional, guarded by DASHBOARD_PIN env var) ──
    pin = os.getenv("DASHBOARD_PIN")
    if pin:
        from dashboard.auth.pin_auth import pin_auth_bp
        app.register_blueprint(pin_auth_bp)

    # ── Static file routes ──
    @app.route("/")
    async def index():
        return await send_from_directory(app.static_folder, "index.html")

    @app.route("/<path:filename>")
    async def serve_static(filename):
        """Serve CSS, JS, images, and other static assets from the dashboard directory.
        Falls back to index.html for SPA routing (non-API, non-file paths).
        """
        import os as _os
        static_path = _os.path.join(app.static_folder, filename)
        if _os.path.isfile(static_path):
            return await send_from_directory(app.static_folder, filename)
        # SPA fallback — return index.html for any unmatched non-API path
        return await send_from_directory(app.static_folder, "index.html")

    @app.route("/health")
    async def health():
        from dashboard.extensions import get_collection
        try:
            arrests = get_collection("arrests")
            total = await arrests.estimated_document_count()
            return {"status": "ok", "total_arrests": total}
        except Exception:
            return {"status": "degraded"}, 503

    logger.info("☘️  Quart app factory initialized — all blueprints registered")
    return app
