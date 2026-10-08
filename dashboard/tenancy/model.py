"""Tenant and membership documents. Nothing here writes to MongoDB."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dashboard.tenancy.constants import SHAMROCK_TENANT_ID

# Platform operator today. Splitting "Shamrock owner" from "platform super-admin"
# is an owner decision; until then this email is both.
PLATFORM_SUPER_ADMIN_EMAIL = "admin@shamrockbailbonds.biz"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def shamrock_tenant_document(now: str | None = None) -> dict[str, Any]:
    """Tenant #1. Upserted by the backfill script, never by app startup."""
    stamped = now or _now()
    return {
        "tenant_id": SHAMROCK_TENANT_ID,
        "legal_name": "Shamrock Bail Bonds",
        "display_name": "Shamrock Bail Bonds",
        "status": "active",
        "plan": "internal",
        "home_state": "FL",
        "primary_host": "leads.shamrockbailbonds.biz",
        "custom_domains": ["paperwork.shamrockbailbonds.biz"],
        "branding": {
            "logo_url": "",
            "primary_color": "#0b6b3a",
            "display_name": "Shamrock Bail Bonds",
        },
        "settings": {
            "default_surety": "osi",
            "timezone": "America/New_York",
        },
        "created_at": stamped,
        "updated_at": stamped,
        "seed": True,
    }


def shamrock_owner_membership(now: str | None = None) -> dict[str, Any]:
    """Binds the existing super-admin email to tenant #1 as owner."""
    stamped = now or _now()
    return {
        "tenant_id": SHAMROCK_TENANT_ID,
        "email": PLATFORM_SUPER_ADMIN_EMAIL,
        "role": "owner",
        "platform_super_admin": True,
        "status": "active",
        "created_at": stamped,
        "updated_at": stamped,
        "seed": True,
    }
