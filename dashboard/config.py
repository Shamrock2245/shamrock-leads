"""
ShamrockLeads Dashboard — Configuration
Environment-based config classes for Dev, Prod, and Test.
"""

import os
from pathlib import Path

# Base directory for the dashboard
BASE_DIR = Path(__file__).resolve().parent


class Config:
    """Base configuration — shared across all environments."""

    # ── MongoDB ──
    MONGODB_URI = os.getenv("MONGODB_URI", "")
    MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")

    # ── Redis ──
    REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

    # ── Dashboard Auth ──
    DASHBOARD_PIN = os.getenv("DASHBOARD_PIN", "")
    SECRET_KEY = os.getenv("SECRET_KEY", os.urandom(32).hex())
    JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", "24"))

    # ── GAS Integration (legacy bridge) ──
    GAS_WEB_APP_URL = os.getenv("GAS_WEB_APP_URL", "")

    # ── SignNow (Direct API — Phase E) ──
    SIGNNOW_API_TOKEN = os.getenv("SIGNNOW_API_TOKEN", "")
    SIGNNOW_API_BASE_URL = os.getenv("SIGNNOW_API_BASE_URL", "https://api.signnow.com")
    SIGNNOW_WEBHOOK_SECRET = os.getenv("SIGNNOW_WEBHOOK_SECRET", "")

    # ── Twilio ──
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
    TWILIO_MESSAGING_SERVICE_SID = os.getenv("TWILIO_MESSAGING_SERVICE_SID", "")

    # ── SwipeSimple ──
    SWIPESIMPLE_PAYMENT_LINK = os.getenv(
        "SWIPESIMPLE_PAYMENT_LINK",
        "https://swipesimple.com/links/lnk_b6bf996f4c57bb340a150e297e769abd",
    )

    # ── BlueBubbles iMessage (Multi-Server) ──
    # Dynamically loaded from env in extensions.py

    # ── Feature Flags ──
    ENABLE_SHEETS_WRITER = os.getenv("ENABLE_SHEETS_WRITER", "true").lower() == "true"
    ENABLE_MONGO_WRITER = os.getenv("ENABLE_MONGO_WRITER", "true").lower() == "true"
    ENABLE_SLACK_ALERTS = os.getenv("ENABLE_SLACK_ALERTS", "true").lower() == "true"
    ENABLE_LEAD_SCORING = os.getenv("ENABLE_LEAD_SCORING", "true").lower() == "true"

    # ── Surety ──
    DEFAULT_SURETY = os.getenv("DEFAULT_SURETY", "osi")


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


class ProductionConfig(Config):
    """Production configuration (Hetzner VPS)."""
    DEBUG = False


class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    MONGODB_DB_NAME = "ShamrockBailDB_test"
    REDIS_URL = "redis://localhost:6379/1"


# Config selector — keyed by FLASK_ENV / QUART_ENV
config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(env_name=None):
    """Return config class for the given environment name."""
    env_name = env_name or os.getenv("FLASK_ENV", "production")
    return config_map.get(env_name, ProductionConfig)

