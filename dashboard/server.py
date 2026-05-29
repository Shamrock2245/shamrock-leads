"""
ShamrockLeads Dashboard — Server Entry Point (DEPRECATED)

Static file serving and API have been consolidated into the FastAPI app.
This file is retained for backwards compatibility only.

Active server: dashboard/main.py (FastAPI + Uvicorn on port 5050)
Public URL: https://leads.shamrockbailbonds.biz (Nginx → :8088 → :5050)

DO NOT add new code here. Use dashboard/main.py instead.
"""
# Expose the FastAPI app for any legacy callers
from dashboard.main import app  # noqa: F401


def start_dashboard_server():
    """Legacy stub — dashboard now starts via uvicorn dashboard.main:app."""
    pass


def update_scraper_status(**kwargs):
    """Legacy stub — scraper status now persisted via MongoWriter.upsert_scraper_status()."""
    pass


__all__ = ["app", "start_dashboard_server", "update_scraper_status"]
