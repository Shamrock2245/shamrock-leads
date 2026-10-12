"""
Charlotte County Arrest Scraper: Revize CMS roster, residential egress only
===========================================================================
Source: Charlotte County Sheriff's Office (CCSO)
URL: https://inmates.charlottecountyfl.revize.com/bookings
Method: stock Playwright Chromium (no Patchright, no stealth context, no proxy)
loading the public roster table from a host whose own exit is US residential:
the Leads Ops home relay. There is no proxy, SOCKS, APE/Warren, CAPTCHA-solver
or stealth path in this module (same cleanup as Manatee #121).

The roster contract (header-mapped columns, source Booking # cross-checked
against its link, all charges per booking, required Released column, bond
unknown = "" never "0", fail-closed paging) lives in
``scrapers/revize_roster.py`` and is the same contract as Manatee (#113).
Detail pages are Cloudflare-blocked, so bond, statute, degree, DOB and address
are not collected.

A Cloudflare challenge/block page, or no verified residential exit, raises
``EgressBlocked`` (``anti_bot`` + ``egress_block``); nothing is written.

Egress (``CHARLOTTE_EGRESS_MODE``, default and only value ``direct``):
    The host's own exit must be verified US residential (known ISP org,
    country US) or the run raises EgressBlocked before touching the source.
    Proxy environment variables are ignored: the exit check runs with
    ``trust_env=False`` and Chromium is launched with ``--no-proxy-server``
    and a proxy-free env. The old ``auto`` value (APE/Warren + office SOCKS
    resolver) was removed 2026-10-07 and now fails loudly as a config error.

Scheduling: relay-only (``config/relay_only.py``). The VPS scheduler never
runs Charlotte; Leads Ops runs it with ``python main.py --relay-only`` or
``python main.py Charlotte`` (docs/ops/REVIZE_RELAY_RUN.md).

Live check from the box, 2026-10-07 7:12 PM ET: ``/``, ``/bookings`` and
``/bookings?page=2`` -> 403 ``cf-mitigated: challenge``, ``server: cloudflare``,
title "Just a moment..." (docs/recon/FL_CHARLOTTE_REVIZE_2026-10-07.md).

HISTORY:
- v1-v4: CF bypass attempts (DrissionPage, curl_cffi, Obscura, JailTracker)
- v5: roster table extraction via office SOCKS tunnel
- v6: APE-first residential proxy + SOCKS fallback
- v7: exit-IP preflight + Patchright + sticky Warren session
- v8: shared fail-closed Revize contract, egress blocks fail loud
- v9 (current): proxy/stealth removed; relay-only, direct residential egress
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper
from scrapers.revize_roster import (
    EXTRACT_JS,
    RevizeRoster,
    check_page_egress,
    egress_mode as _egress_mode,
    launch_plain_browser,
    resolve_egress as _resolve_egress,
    wait_for_page,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://inmates.charlottecountyfl.revize.com"
BOOKINGS_URL = f"{BASE_URL}/bookings"
MAX_PAGES = 50
EGRESS_ENV = "CHARLOTTE_EGRESS_MODE"

ROSTER = RevizeRoster(
    county="Charlotte",
    base_url=BASE_URL,
    facility="Charlotte County Jail",
    max_pages=MAX_PAGES,
)


def egress_mode() -> str:
    return _egress_mode(EGRESS_ENV)


def resolve_egress(scraper: Any = None) -> Tuple[None, str]:
    return _resolve_egress(scraper, county="Charlotte", env_var=EGRESS_ENV)


class CharlotteCountyScraper(BaseScraper):

    # FAIL CLOSED 2026-10-09 (docs/recon/FL_CHARLOTTE_SOURCE_RECON_2026-10-09.md). The Revize roster
    # answers a Cloudflare challenge (HTTP 403 ``cf-mitigated: challenge``) on
    # page 1 from the box, T-Mobile AS21928 and Comcast AS7922 alike, and no
    # other official plain-HTTP source publishes a booking roster with a real
    # source booking number. The parser below is kept unchanged: reopening is
    # flipping this flag once a contract is proven (relay read + write smoke).
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "Revize roster (inmates.charlottecountyfl.revize.com, also the iframe on ccso.org) returns a Cloudflare challenge on every path from every tested exit (2026-10-09); the Clerk's Benchmark case search is behind reCAPTCHA. No public roster with a source booking number is reachable without passing a challenge."
    )

    @property
    def county(self) -> str:
        return "Charlotte"

    def scrape(self) -> List[ArrestRecord]:
        if not getattr(self, "SOURCE_CONTRACT_VALIDATED", True):
            # No egress check, no browser, no source request.
            logger.warning("[Charlotte] fail_closed: %s", self.SOURCE_CONTRACT_REASON)
            return []
        _, egress_source = resolve_egress(self)
        logger.info("[Charlotte] egress mode=%s source=%s", egress_mode(), egress_source)

        pw = browser = None
        try:
            pw, browser = launch_plain_browser()
            page = browser.new_context().new_page()

            def fetch_page(url: str, pg: int) -> Dict[str, Any]:
                logger.info("[Charlotte] roster page %s", pg)
                resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                status = getattr(resp, "status", None) if resp is not None else None
                try:
                    headers = dict(resp.headers) if resp is not None else {}
                except Exception:
                    headers = {}
                cleared = wait_for_page(page)
                payload = page.evaluate(EXTRACT_JS)
                check_page_egress(
                    county="Charlotte", pg=pg, cleared=cleared, payload=payload,
                    status=status, headers=headers,
                    body=page.content() if not payload.get("has_table") else "",
                    egress_source=egress_source, env_var=EGRESS_ENV,
                )
                return payload

            records, meta = ROSTER.walk(fetch_page)
            meta["egress_source"] = egress_source
            self.last_walk_meta = meta
            logger.info(
                "[Charlotte] %s bookings from %s rows over %s pages (published total=%s, egress=%s)",
                meta["bookings"], meta["rows"], meta["pages"], meta["published_total"], egress_source,
            )
            return records

        except Exception as e:
            logger.error("[Charlotte] run failed: %s", e)
            raise
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass
