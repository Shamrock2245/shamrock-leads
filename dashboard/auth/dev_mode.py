"""When an unconfigured credential may be tolerated: only in explicit development.

Every PIN, admin-key, webhook-secret and session-secret guard in the dashboard
fails closed when its credential is missing. The single exception is a process
whose ``ENV`` (or, if ``ENV`` is unset, ``ENVIRONMENT``) is exactly
``development``. ``ENV`` unset, ``production``, ``prod``, ``staging``, ``test``,
``dev`` and typos are all closed. A ``REQUIRE_*`` flag closes development too.

No credential values are read or logged here.
"""
from __future__ import annotations

import os

_TRUTHY = ("1", "true", "yes")


def explicit_development() -> bool:
    """True only when ENV/ENVIRONMENT is exactly ``development``."""
    env = (os.getenv("ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
    return env == "development"


def _required(flag: str) -> bool:
    return os.getenv(flag, "").strip().lower() in _TRUTHY


def no_pin_passthrough_allowed() -> bool:
    """No DASHBOARD_PIN / admin key may be tolerated (explicit development, and
    ``REQUIRE_DASHBOARD_PIN`` not set)."""
    return explicit_development() and not _required("REQUIRE_DASHBOARD_PIN")


def unconfigured_secret_allowed(require_flag: str | None = None) -> bool:
    """A missing webhook secret / admin key may be tolerated (explicit
    development, and neither ``REQUIRE_DASHBOARD_PIN`` nor ``require_flag`` set)."""
    if require_flag and _required(require_flag):
        return False
    return no_pin_passthrough_allowed()


def session_secret_fallback_allowed() -> bool:
    """A missing SECRET_KEY may use the development-only signing key (explicit
    development, and ``REQUIRE_SECRET_KEY`` not set)."""
    return explicit_development() and not _required("REQUIRE_SECRET_KEY")
