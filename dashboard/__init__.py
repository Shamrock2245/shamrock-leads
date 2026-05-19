"""
ShamrockLeads Dashboard — Package Init

Migration from Quart to FastAPI is COMPLETE (2026-05-19).
The active entry point is `dashboard/main.py` (FastAPI + Uvicorn).

The old Quart `create_app()` factory has been retired.
All 63 routers live in `dashboard/routers/`.
Dependency injection lives in `dashboard/deps.py`.
Background crons live in `dashboard/cron.py`.

Usage:
    uvicorn dashboard.main:app --host 0.0.0.0 --port 5050 --workers 1
"""
# Re-export the FastAPI app for any callers that do `from dashboard import app`
from dashboard.main import app  # noqa: F401

__all__ = ["app"]
