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

__all__ = ["app"]
