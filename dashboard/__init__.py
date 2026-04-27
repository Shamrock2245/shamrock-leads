from dashboard.api.events import events_bp
from dashboard.api.payments import payments_bp
from dashboard.api.webhooks import webhooks_bp
from dashboard.api.tracking import tracking_bp
from dashboard.api.contacts import contacts_bp
from dashboard.api.court_reminders import court_reminders_bp

def register_blueprints(app):
    app.register_blueprint(events_bp, url_prefix="/api")
    app.register_blueprint(payments_bp, url_prefix="/api")
    app.register_blueprint(webhooks_bp, url_prefix="/api")
    app.register_blueprint(tracking_bp, url_prefix="/api")
    app.register_blueprint(contacts_bp, url_prefix="/api")
    app.register_blueprint(court_reminders_bp, url_prefix="/api")
