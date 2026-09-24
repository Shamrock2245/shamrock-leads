"""
ShamrockLeads — SwipeSimple Playwright session refresh / bootstrap
==================================================================
PURPOSE: session refresh + cookie/CSRF capture ONLY.

This module is NOT the primary invoice-create path.
Production create = Option 2 captured Share Invoice HTTP replay in
`swipesimple_invoice_service.py`.

Use this stub to:
  - log into SwipeSimple (headful/headless as ops policy allows)
  - navigate toward Share Invoice (Web Link) UI solely to refresh session
  - write refreshed cookies / session material into the env/secret-store
    shape expected by `load_swipesimple_session_config()`:
      SWIPESIMPLE_SESSION or SWIPESIMPLE_COOKIE_JAR
      optional SWIPESIMPLE_CSRF_TOKEN, SWIPESIMPLE_MERCHANT_ID

MUST NOT create customer invoices from this stub.
MUST NOT invent payment links.
MUST NOT log or echo secret values.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://app.swipesimple.com"


class SwipeSimpleBootstrapError(Exception):
    """Session bootstrap / refresh failures."""


class SwipeSimpleBootstrapNotWired(NotImplementedError):
    """Raised until Playwright login + capture steps are implemented."""


def _base_url() -> str:
    return (os.getenv("SWIPESIMPLE_BASE_URL") or _DEFAULT_BASE_URL).strip().rstrip("/")


def _credentials_present() -> Dict[str, bool]:
    """Presence flags only — never return or log credential values."""
    return {
        "has_username": bool((os.getenv("SWIPESIMPLE_USERNAME") or "").strip()),
        "has_password": bool((os.getenv("SWIPESIMPLE_PASSWORD") or "").strip()),
        "has_existing_session": bool(
            (os.getenv("SWIPESIMPLE_SESSION") or os.getenv("SWIPESIMPLE_COOKIE_JAR") or "").strip()
        ),
    }


def secret_store_shape() -> Dict[str, str]:
    """
    Document the env/secret keys this bootstrap should refresh into.
    Values are key NAMES only.
    """
    return {
        "session": "SWIPESIMPLE_SESSION",
        "cookie_jar": "SWIPESIMPLE_COOKIE_JAR",
        "csrf": "SWIPESIMPLE_CSRF_TOKEN",
        "merchant_id": "SWIPESIMPLE_MERCHANT_ID",
        "base_url": "SWIPESIMPLE_BASE_URL",
    }


async def refresh_swipesimple_session(
    *,
    headless: bool = True,
    navigate_share_invoice_ui: bool = True,
) -> Dict[str, Any]:
    """
    STUB: Playwright login + optional navigate to Share Invoice (Web Link)
    to refresh cookies/session into secret-store shape.

    Does NOT create invoices. Does NOT click final Share/Create that would
    bill a customer. Raises until wired.
    """
    flags = _credentials_present()
    logger.info(
        "[ss_bootstrap] session refresh STUB headless=%s navigate_share_ui=%s "
        "has_username=%s has_password=%s has_existing_session=%s base_url=%s",
        headless,
        navigate_share_invoice_ui,
        flags["has_username"],
        flags["has_password"],
        flags["has_existing_session"],
        _base_url(),
    )

    if not (flags["has_username"] and flags["has_password"]) and not flags["has_existing_session"]:
        raise SwipeSimpleBootstrapError("swipesimple_credentials_or_session_required")

    # TODO(ops): implement with Playwright:
    #   1. browser.new_context / launch
    #   2. login page → fill username/password (from env; never log)
    #   3. optional: open Share Invoice (Web Link) screen for CSRF/cookie refresh
    #   4. export cookies → SWIPESIMPLE_SESSION / SWIPESIMPLE_COOKIE_JAR shape
    #   5. optionally capture CSRF header/meta → SWIPESIMPLE_CSRF_TOKEN
    #   6. close browser — STOP before any invoice create/submit
    raise SwipeSimpleBootstrapNotWired(
        "Playwright SwipeSimple session refresh not wired. "
        "This path is bootstrap/capture only — primary create remains HTTP replay "
        "in swipesimple_invoice_service.create_locked_invoice."
    )


async def capture_share_invoice_curl_hints() -> Dict[str, Any]:
    """
    STUB helper for ops: after a manual DevTools capture, remind which contract
    fields belong in SWIPESIMPLE_INVOICE_CONTRACT.md. Does not perform network I/O.
    """
    return {
        "ok": True,
        "stub": True,
        "contract_path": "dashboard/services/SWIPESIMPLE_INVOICE_CONTRACT.md",
        "secret_keys": secret_store_shape(),
        "note": (
            "Paste Brendan DevTools cURL into the contract (URL/method/header names/"
            "JSON keys only in git; secrets stay in env)."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    """CLI entry for ops: python -m dashboard.services.swipesimple_playwright_bootstrap"""
    import asyncio

    async def _run() -> None:
        try:
            await refresh_swipesimple_session()
        except SwipeSimpleBootstrapNotWired as exc:
            logger.warning("[ss_bootstrap] %s", exc)
        except SwipeSimpleBootstrapError as exc:
            logger.error("[ss_bootstrap] %s", exc)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
