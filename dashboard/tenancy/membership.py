"""Active membership checks for host-selected tenants.

The lookup uses the raw database. ``tenant_memberships`` is a platform
collection, and the request tenant is not bound yet when this runs.
"""

from __future__ import annotations

import logging

from dashboard.tenancy.constants import TENANT_FIELD
from dashboard.tenancy.context import normalize_slug

logger = logging.getLogger(__name__)


async def has_active_membership(email: str | None, tenant_id: str | None) -> bool:
    """True when ``email`` has an active membership in ``tenant_id``.

    Missing email, an unknown slug, and a lookup error all fail closed.
    The email is not written to the log.
    """
    from dashboard.auth.super_admin import normalize_email

    normalized = normalize_email(email)
    slug = normalize_slug(tenant_id)
    if not normalized or not slug:
        return False
    try:
        from dashboard.extensions import get_raw_db

        db = get_raw_db()
        doc = await db["tenant_memberships"].find_one(
            {
                TENANT_FIELD: slug,
                "email": normalized,
                "status": "active",
            }
        )
    except Exception:
        logger.warning("tenant membership lookup failed tenant=%s", slug)
        return False
    return bool(doc)
