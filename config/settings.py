"""
ShamrockLeads — Centralized Configuration
All settings loaded from environment with sensible defaults.
Includes startup validation and masked logging.
"""

import os
import re
import logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env from project root
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _mask(value: str, show_chars: int = 4) -> str:
    """Mask a secret value for safe logging. Shows first N chars + ***."""
    if not value:
        return "(empty)"
    if len(value) <= show_chars:
        return "***"
    return value[:show_chars] + "***"


class Settings:
    """Central configuration singleton with startup validation."""

    # --- MongoDB ---
    MONGODB_URI: str = os.getenv("MONGODB_URI", "")
    MONGODB_DB_NAME: str = os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")

    # --- Google Sheets (legacy/optional) ---
    GOOGLE_SPREADSHEET_ID: str = os.getenv(
        "GOOGLE_SPREADSHEET_ID",
        "121z5R6Hpqur54GNPC8L26ccfDPLHTJc3_LU6G7IV_0E"
    )
    GOOGLE_APPLICATION_CREDENTIALS: str = os.getenv(
        "GOOGLE_APPLICATION_CREDENTIALS", ""
    )

    # --- OpenAI ---
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # --- Slack ---
    SLACK_WEBHOOK_ARRESTS: str = os.getenv("SLACK_WEBHOOK_ARRESTS", "")
    SLACK_WEBHOOK_LEADS: str = os.getenv("SLACK_WEBHOOK_LEADS", "")
    SLACK_WEBHOOK_ERRORS: str = os.getenv("SLACK_WEBHOOK_ERRORS", "")

    # --- Twilio ---
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM_NUMBER: str = os.getenv("TWILIO_FROM_NUMBER", "")

    # --- Scraper ---
    LOG_LEVEL: str = os.getenv("SCRAPER_LOG_LEVEL", "INFO")
    MAX_CONCURRENT: int = int(os.getenv("SCRAPER_MAX_CONCURRENT", "8"))
    DEFAULT_INTERVAL_MINUTES: int = int(
        os.getenv("SCRAPER_DEFAULT_INTERVAL_MINUTES", "60")
    )

    # --- Feature Flags ---
    ENABLE_SHEETS_WRITER: bool = os.getenv("ENABLE_SHEETS_WRITER", "false").lower() == "true"
    ENABLE_MONGO_WRITER: bool = os.getenv("ENABLE_MONGO_WRITER", "true").lower() == "true"
    ENABLE_SLACK_ALERTS: bool = os.getenv("ENABLE_SLACK_ALERTS", "true").lower() == "true"
    ENABLE_LEAD_SCORING: bool = os.getenv("ENABLE_LEAD_SCORING", "true").lower() == "true"

    # --- County-Specific Credentials ---
    HCSO_EMAIL: str = os.getenv("HCSO_EMAIL", "")
    HCSO_PASSWORD: str = os.getenv("HCSO_PASSWORD", "")

    # --- Surety / Insurance Companies ---
    DEFAULT_SURETY: str = os.getenv("DEFAULT_SURETY", "osi")

    # --- GAS Integration ---
    GAS_WEB_APP_URL: str = os.getenv("GAS_WEB_APP_URL", "")

    # --- Gmail OAuth (Court Email Pipeline) ---
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_GMAIL_REFRESH_TOKEN: str = os.getenv("GOOGLE_GMAIL_REFRESH_TOKEN", "")
    GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "admin@shamrockbailbonds.biz")

    # --- BlueBubbles ---
    BLUEBUBBLES_URL_0178: str = os.getenv("BLUEBUBBLES_URL_0178", "")
    BLUEBUBBLES_URL_0314: str = os.getenv("BLUEBUBBLES_URL_0314", "")

    # --- Dashboard ---
    DASHBOARD_PIN: str = os.getenv("DASHBOARD_PIN", "")
    DASHBOARD_PUBLIC_URL: str = os.getenv("DASHBOARD_PUBLIC_URL", "")

    # --- Derived ---
    @classmethod
    def mongo_configured(cls) -> bool:
        return bool(cls.MONGODB_URI)

    @classmethod
    def sheets_configured(cls) -> bool:
        return bool(cls.GOOGLE_SPREADSHEET_ID and cls.GOOGLE_APPLICATION_CREDENTIALS)

    @classmethod
    def slack_configured(cls) -> bool:
        return bool(cls.SLACK_WEBHOOK_ARRESTS)

    @classmethod
    def gmail_configured(cls) -> bool:
        return bool(cls.GOOGLE_CLIENT_ID and cls.GOOGLE_CLIENT_SECRET and cls.GOOGLE_GMAIL_REFRESH_TOKEN)

    @classmethod
    def bluebubbles_configured(cls) -> bool:
        return bool(cls.BLUEBUBBLES_URL_0178 or cls.BLUEBUBBLES_URL_0314)

    # --- Startup Validation ---
    @classmethod
    def validate_and_log(cls):
        """
        Validate critical env vars at startup and log masked values.
        Call this once during app initialization.
        """
        print("=" * 60)
        print("  ShamrockLeads — Configuration Audit")
        print("=" * 60)

        # Critical — fail fast
        critical = {
            "MONGODB_URI": cls.MONGODB_URI,
        }
        for name, value in critical.items():
            if not value:
                print(f"  ❌ {name}: MISSING (CRITICAL)")
                logger.critical("Missing critical env var: %s", name)
            else:
                print(f"  ✅ {name}: {_mask(value)}")

        # Important — warn
        important = {
            "SLACK_WEBHOOK_ARRESTS": cls.SLACK_WEBHOOK_ARRESTS,
            "SLACK_WEBHOOK_ERRORS": cls.SLACK_WEBHOOK_ERRORS,
            "SLACK_WEBHOOK_LEADS": cls.SLACK_WEBHOOK_LEADS,
        }
        for name, value in important.items():
            if not value:
                print(f"  ⚠️  {name}: not set (Slack alerts disabled)")
            else:
                print(f"  ✅ {name}: {_mask(value, 20)}")

        # Optional services
        services = {
            "Gmail OAuth": cls.gmail_configured(),
            "BlueBubbles": cls.bluebubbles_configured(),
            "Google Sheets": cls.sheets_configured(),
            "OpenAI": bool(cls.OPENAI_API_KEY),
        }
        print("\n  Services:")
        for name, configured in services.items():
            status = "✅ configured" if configured else "⬜ not configured"
            print(f"    {name}: {status}")

        print("=" * 60)


settings = Settings()
