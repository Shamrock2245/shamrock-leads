"""Live feature flag. Read on each call so tests and deploys can flip it."""

from __future__ import annotations

import os

_ON = {"1", "true", "yes", "on"}


def multi_tenant_enabled() -> bool:
    """True only when SAAS_MULTI_TENANT is explicitly enabled.

    The default is off. Shamrock's queries, cookies, and jobs stay
    single-tenant until an operator turns the flag on after the backfill.
    """
    return os.getenv("SAAS_MULTI_TENANT", "").strip().lower() in _ON
