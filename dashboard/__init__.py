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

    # ── Legacy compatibility blueprint (iMessage, cleanup, custody, db-health) ──
    from dashboard.api.legacy import legacy_bp
    app.register_blueprint(legacy_bp, url_prefix="/api")

    # ── iMessage Automation (auto-reply, inbox polling, Private API proxy) ──
    from dashboard.api.imessage_automation import imessage_auto_bp, start_inbox_poller
    app.register_blueprint(imessage_auto_bp, url_prefix="/api")

    # Start background inbox poller
    @app.before_serving
    async def _start_inbox_poller():
        import asyncio
        asyncio.ensure_future(start_inbox_poller(app))

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
