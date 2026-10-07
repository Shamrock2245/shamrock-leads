"""
Charlotte County Arrest Scraper: Revize CMS roster, residential egress only
===========================================================================
Source: Charlotte County Sheriff's Office (CCSO)
URL: https://inmates.charlottecountyfl.revize.com/bookings
Method: Playwright/Patchright page load of the public roster table from a US
residential exit. No new proxy, CAPTCHA solver or stealth path is added here.

The roster contract (header-mapped columns, source Booking # cross-checked
against its link, all charges per booking, required Released column, bond
unknown = "" never "0", fail-closed paging) lives in
``scrapers/revize_roster.py`` and is the same contract as Manatee (#113).
Detail pages are Cloudflare-blocked, so bond, statute, degree, DOB and address
are not collected.

A Cloudflare challenge/block page, or no usable residential exit, raises
``EgressBlocked`` (``anti_bot`` + ``egress_block``); nothing is written.

Egress (``CHARLOTTE_EGRESS_MODE``):
    auto    (default) existing resolver (env SOCKS -> APE/Warren residential ->
            office/Tailscale SOCKS -> direct only when this host is residential).
    direct  Leads Ops residential egress: no proxy at all; the host exit must be
            verified US residential or the run raises before touching the source.

Live check from the box, 2026-10-07 7:12 PM ET: ``/``, ``/bookings`` and
``/bookings?page=2`` -> 403 ``cf-mitigated: challenge``, ``server: cloudflare``,
title "Just a moment..." (docs/recon/FL_CHARLOTTE_REVIZE_2026-10-07.md).

HISTORY:
- v1-v4: CF bypass attempts (DrissionPage, curl_cffi, Obscura, JailTracker)
- v5: roster table extraction via office SOCKS tunnel
- v6: APE-first residential proxy + SOCKS fallback
- v7: exit-IP preflight + Patchright + sticky Warren session
- v8 (current): shared fail-closed Revize contract, egress blocks fail loud
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper
from scrapers.revize_roster import (
    EXTRACT_JS,
    RevizeRoster,
    check_page_egress,
    egress_mode as _egress_mode,
    resolve_egress as _resolve_egress,
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


def resolve_egress(scraper: Any = None) -> Tuple[Optional[str], str]:
    return _resolve_egress(
        scraper, county="Charlotte", env_var=EGRESS_ENV, sticky_session="fl-charlotte"
    )


class CharlotteCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Charlotte"

    def scrape(self) -> List[ArrestRecord]:
        from scrapers.cf_browser import launch_cf_browser, new_stealth_context, wait_past_cloudflare

        proxy_url, proxy_source = resolve_egress(self)
        logger.info("[Charlotte] egress mode=%s source=%s", egress_mode(), proxy_source)

        pw = browser = None
        t0 = time.time()
        try:
            pw, browser, engine = launch_cf_browser(
                proxy_url,
                label="Charlotte",
                verify_residential=(proxy_source != "direct"),
            )
            context = new_stealth_context(browser)
            page = context.new_page()

            def fetch_page(url: str, pg: int) -> Dict[str, Any]:
                logger.info("[Charlotte] roster page %s (engine=%s)", pg, engine)
                resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                status = getattr(resp, "status", None) if resp is not None else None
                try:
                    headers = dict(resp.headers) if resp is not None else {}
                except Exception:
                    headers = {}
                cleared = wait_past_cloudflare(page, label=f"Charlotte page {pg}", max_wait=45)
                payload = page.evaluate(EXTRACT_JS)
                check_page_egress(
                    county="Charlotte", pg=pg, cleared=cleared, payload=payload,
                    status=status, headers=headers,
                    body=page.content() if not payload.get("has_table") else "",
                    egress_source=proxy_source, env_var=EGRESS_ENV,
                )
                return payload

            records, meta = ROSTER.walk(fetch_page)
            meta["egress_source"] = proxy_source
            self.last_walk_meta = meta
            logger.info(
                "[Charlotte] %s bookings from %s rows over %s pages (published total=%s, egress=%s)",
                meta["bookings"], meta["rows"], meta["pages"], meta["published_total"], proxy_source,
            )
            if records and proxy_source == "ape":
                self.record_proxy_success(proxy_url, (time.time() - t0) * 1000)
            return records

        except Exception as e:
            logger.error("[Charlotte] run failed: %s", e)
            if proxy_source == "ape":
                try:
                    self.record_proxy_failure(proxy_url)
                except Exception:
                    pass
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
