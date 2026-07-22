"""
ShamrockLeads Dashboard — Package Init

FastAPI is the only server stack. Flask/Quart are not used.

Active entry point: ``dashboard/main.py`` (Uvicorn).

Usage:
    uvicorn dashboard.main:app --host 0.0.0.0 --port 5050
    python -m dashboard.run
"""

__all__ = ["app"]


def __getattr__(name: str):
    """Lazy-load app so `import dashboard.routers.X` does not construct FastAPI at import time."""
    if name == "app":
        from dashboard.main import app as _app

        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
