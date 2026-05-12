"""
ShamrockLeads Dashboard — FastAPI Dependency Providers

Drop-in replacements for extensions.py singletons, designed for use with
FastAPI `Depends()`.  Services that call `get_db()` / `get_collection()`
directly continue to work — the signatures are identical.

New routers should use these via DI:

    from dashboard.deps import get_db
    @router.get("/example")
    async def example(db=Depends(get_db)):
        ...
"""
from __future__ import annotations

import os
from functools import lru_cache
from dataclasses import dataclass


# ── Database ──────────────────────────────────────────────────────────────────
# Re-export from extensions to avoid duplicating Motor singleton logic.
# During Phase 5 (full cutover), we can inline the client here and remove
# the extensions.py dependency entirely.

from dashboard.extensions import (          # noqa: F401  — re-export
    get_mongo_client,
    get_db,
    get_collection,
)


# ── Application Settings (replaces current_app.config) ────────────────────────

@dataclass(frozen=True)
class Settings:
    """Typed application settings — replaces current_app.config dict."""
    secret_key: str
    dashboard_public_url: str
    client_brand_url: str
    portal_base_url: str
    dashboard_pin: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings provider — call once, reuse everywhere."""
    _pin = os.getenv("DASHBOARD_PIN", "")
    return Settings(
        secret_key=os.getenv("SECRET_KEY") or (
            "shamrock-" + (_pin or "leads-2245") + "-session-key-v1"
        ),
        dashboard_public_url=os.getenv("DASHBOARD_PUBLIC_URL", "").rstrip("/"),
        client_brand_url=os.getenv(
            "CLIENT_BRAND_URL", "https://www.shamrockbailbonds.biz"
        ).rstrip("/"),
        portal_base_url=os.getenv(
            "PORTAL_BASE_URL", "https://shamrockbailbonds.biz"
        ).rstrip("/"),
        dashboard_pin=_pin,
    )
