"""
ShamrockLeads — Centralized Configuration
All settings loaded from environment with sensible defaults.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")


class Settings:
    """Central configuration singleton."""

    # --- MongoDB ---
    MONGODB_URI: str = os.getenv("MONGODB_URI", "")
    MONGODB_DB_NAME: str = os.getenv("MONGODB_DB_NAME", "shamrock_leads")

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
    MAX_CONCURRENT: int = int(os.getenv("SCRAPER_MAX_CONCURRENT", "3"))
    DEFAULT_INTERVAL_MINUTES: int = int(
        os.getenv("SCRAPER_DEFAULT_INTERVAL_MINUTES", "60")
    )

    # --- Feature Flags ---
    ENABLE_SHEETS_WRITER: bool = os.getenv("ENABLE_SHEETS_WRITER", "true").lower() == "true"
    ENABLE_MONGO_WRITER: bool = os.getenv("ENABLE_MONGO_WRITER", "true").lower() == "true"
    ENABLE_SLACK_ALERTS: bool = os.getenv("ENABLE_SLACK_ALERTS", "true").lower() == "true"
    ENABLE_LEAD_SCORING: bool = os.getenv("ENABLE_LEAD_SCORING", "true").lower() == "true"

    # --- County-Specific Credentials ---
    HCSO_EMAIL: str = os.getenv("HCSO_EMAIL", "")
    HCSO_PASSWORD: str = os.getenv("HCSO_PASSWORD", "")

    # --- GAS Integration ---
    GAS_WEB_APP_URL: str = os.getenv("GAS_WEB_APP_URL", "")

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


settings = Settings()
