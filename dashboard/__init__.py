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
    # ── Defendant Lifecycle (Notes, DNB/DNC, Contact Log, Bond Finalization) ──
    from dashboard.api.defendant_lifecycle import lifecycle_bp
    app.register_blueprint(lifecycle_bp, url_prefix="/api")

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

    # ── PIN Auth (optional, guarded by DASHBOARD_PIN env var) ──
    pin = os.getenv("DASHBOARD_PIN")
    if pin:
        from dashboard.auth.pin_auth import pin_auth_bp
        app.register_blueprint(pin_auth_bp)

    # ── Static file routes ──
    @app.route("/")
    async def index():
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
