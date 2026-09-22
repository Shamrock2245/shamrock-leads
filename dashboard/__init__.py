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


def _sync_scraper_source_states() -> None:
    """Keep extensions.SCRAPER_SOURCE_STATES aligned with the Health label registry.

    ``dashboard/scraper_source_states_data.py`` is the editable source of truth for
    fail_closed / verified_public labels (including FL Broward + JailTracker).
    """
    try:
        from dashboard.extensions import SCRAPER_SOURCE_STATES
        from dashboard.scraper_source_states_data import (
            SCRAPER_SOURCE_STATES as canonical,
        )

        SCRAPER_SOURCE_STATES.clear()
        SCRAPER_SOURCE_STATES.update(canonical)
    except Exception:
        pass


_sync_scraper_source_states()
