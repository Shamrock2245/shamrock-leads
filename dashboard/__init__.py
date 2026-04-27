"""ShamrockLeads Dashboard — Quart Application Factory"""

from quart import Quart, send_from_directory
import motor.motor_asyncio
import os

def create_app():
    app = Quart(__name__, static_folder=os.path.dirname(__file__), static_url_path="")

    # MongoDB (async via Motor)
    mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    db_name = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_uri)
    app.db = client[db_name]

    # --- Register ALL API Blueprints ---
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

    # --- PIN Auth (optional, guarded by DASHBOARD_PIN env var) ---
    pin = os.getenv("DASHBOARD_PIN")
    if pin:
        from dashboard.auth.pin_auth import pin_auth_bp
        app.register_blueprint(pin_auth_bp)

    # --- Static file routes ---
    @app.route("/")
    async def index():
        return await send_from_directory(app.static_folder, "index.html")

    @app.route("/health")
    async def health():
        return {"status": "ok"}

    return app
