#!/usr/bin/env python3
"""
Brendan-approved $0.01 SwipeSimple Share Invoice smoke (PR #51).

Creates a draft invoice at $0.01 with a non-customer reference_id
(SMOKE-YYYYMMDD-HHMM) and fetches copy_link. Does NOT:
  - invent or write BondCase / Mongo
  - dispatch BlueBubbles / email
  - print cookies, CSRF tokens, or passwords

Requires (human / Leads Ops):
  SWIPESIMPLE_LIVE=1
  SWIPESIMPLE_SESSION or SWIPESIMPLE_COOKIE_JAR
Optional:
  SWIPESIMPLE_CSRF_TOKEN
  SWIPESIMPLE_BASE_URL / MERCHANT_ID / CATALOG_* / NEW_INVOICE_PATH

Usage (from repo root, after SESSION is in the env):
  python scripts/swipesimple_smoke_create.py --check-only   # gates only, no HTTP
  python scripts/swipesimple_smoke_create.py                # live $0.01 create
  python scripts/swipesimple_smoke_create.py --reference-id SMOKE-20260924-1315

Keep SWIPESIMPLE_DISPATCH_LIVE unset during smoke.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("swipesimple_smoke")

# Never dump these even if someone passes --verbose dumps of os.environ.
_SECRET_ENV_NAMES = frozenset(
    {
        "SWIPESIMPLE_SESSION",
        "SWIPESIMPLE_COOKIE_JAR",
        "SWIPESIMPLE_CSRF_TOKEN",
        "SWIPESIMPLE_PASSWORD",
        "SWIPESIMPLE_USERNAME",
    }
)


def _safe_env_presence() -> dict:
    return {name: bool((os.getenv(name) or "").strip()) for name in sorted(_SECRET_ENV_NAMES)}


async def _run(args: argparse.Namespace) -> int:
    from dashboard.services.swipesimple_invoice_service import (
        SwipeSimpleInvoiceError,
        SwipeSimpleLiveDisabled,
        default_smoke_reference_id,
        smoke_create_one_cent_draft,
    )

    ref = (args.reference_id or "").strip() or None
    logger.info(
        "smoke start check_only=%s reference_id=%s secret_env_present=%s",
        args.check_only,
        ref or default_smoke_reference_id(),
        _safe_env_presence(),
    )

    try:
        result = await smoke_create_one_cent_draft(
            reference_id=ref,
            customer_name=args.customer_name,
            customer_id=args.customer_id,
            check_only=args.check_only,
        )
    except SwipeSimpleLiveDisabled as exc:
        logger.error("LIVE gate blocked smoke: %s", exc)
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "SWIPESIMPLE_LIVE_disabled",
                    "hint": "Set SWIPESIMPLE_LIVE=1 only for Brendan-approved smoke, then unset.",
                    "secret_env_present": _safe_env_presence(),
                },
                indent=2,
            )
        )
        return 2
    except SwipeSimpleInvoiceError as exc:
        logger.error("smoke failed (no secrets logged): %s", exc)
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "secret_env_present": _safe_env_presence(),
                },
                indent=2,
            )
        )
        return 1

    # Result already excludes secrets; still strip any accidental keys.
    safe = {
        k: v
        for k, v in result.items()
        if k not in {"_session", "_cookie_jar", "_csrf", "cookie", "authenticity_token"}
    }
    print(json.dumps(safe, indent=2, default=str))
    if args.check_only:
        return 0 if safe.get("ready") else 3
    return 0 if safe.get("ok") else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "SwipeSimple $0.01 draft smoke (create + copy_link). "
            "No BondCase, no dispatch, never prints secrets."
        )
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Print LIVE/session gate readiness only — no HTTP to swipesimple.com",
    )
    parser.add_argument(
        "--reference-id",
        default="",
        help="Override SMOKE-YYYYMMDD-HHMM (must start with SMOKE)",
    )
    parser.add_argument(
        "--customer-id",
        default="",
        help="Existing SwipeSimple customer id (cus_*) for the smoke; blank = new customer",
    )
    parser.add_argument(
        "--customer-name",
        default="SMOKE TEST DO NOT PAY",
        help="Synthetic customer display name (not written to BondCase)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
