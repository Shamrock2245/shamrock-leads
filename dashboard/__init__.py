"""
ShamrockLeads Dashboard — Package Init

Migration from Quart to FastAPI is COMPLETE (2026-05-19).
The active entry point is `dashboard/main.py` (FastAPI + Uvicorn).

Usage:
    uvicorn dashboard.main:app --host 0.0.0.0 --port 5050 --workers 1
"""

__all__ = ["app"]


def __getattr__(name: str):
    """Lazy-load app so `import dashboard.routers.X` does not construct FastAPI at import time."""
    if name == "app":
        from dashboard.main import app as _app

        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
