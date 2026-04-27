"""
ShamrockLeads Dashboard — Application Factory
Creates and configures the Quart application with all blueprints registered.
"""

from quart import Quart, send_from_directory
from dashboard.config import get_config
from dashboard.extensions import init_app as init_extensions


def create_app(config_name=None):
    """Application factory — creates a fully configured Quart app."""
    import os
    config_name = config_name or os.getenv("FLASK_ENV", "production")

    app = Quart(__name__, static_folder=None)
    cfg = get_config(config_name)
    app.config.from_object(cfg)

    # ── Initialize extensions (Motor, Redis, POA seeding) ──
    init_extensions(app)

    # ── Register all API Blueprints (url_prefix="/api") ──
    from dashboard.api.stats import stats_bp
    from dashboard.api.bonds import bonds_bp
    from dashboard.api.poa import poa_bp
    from dashboard.api.leads import leads_bp
    from dashboard.api.arrests import arrests_bp
    from dashboard.api.defendants import defendants_bp
    from dashboard.api.events import events_bp
    from dashboard.api.payments import payments_bp
    from dashboard.api.webhooks import webhooks_bp

    app.register_blueprint(stats_bp, url_prefix="/api")
    app.register_blueprint(bonds_bp, url_prefix="/api")
    app.register_blueprint(poa_bp, url_prefix="/api")
    app.register_blueprint(leads_bp, url_prefix="/api")
    app.register_blueprint(arrests_bp, url_prefix="/api")
    app.register_blueprint(defendants_bp, url_prefix="/api")
    app.register_blueprint(events_bp, url_prefix="/api")
    app.register_blueprint(payments_bp, url_prefix="/api")
    app.register_blueprint(webhooks_bp, url_prefix="/api")

    # ── Static file serving ──
    @app.route("/")
    async def index():
        return await send_from_directory(".", "index.html")

    @app.route("/<path:filename>")
    async def serve_file(filename):
        if filename.endswith((".css", ".js", ".png", ".ico", ".svg", ".pdf", ".map")):
            return await send_from_directory(".", filename)
        return await send_from_directory(".", "index.html")

    # ── Health endpoint ──
    @app.route("/health")
    async def health():
        from dashboard.extensions import get_db
        try:
            db = get_db()
            await db.command("ping")
            return {"status": "ok", "db": "connected"}, 200
        except Exception as e:
            return {"status": "degraded", "db": str(e)}, 503

    # ── PIN Auth (if enabled) ──
    pin = app.config.get("DASHBOARD_PIN")
    if pin:
        from dashboard.auth.pin_auth import pin_auth_bp
        app.register_blueprint(pin_auth_bp)

    return app
