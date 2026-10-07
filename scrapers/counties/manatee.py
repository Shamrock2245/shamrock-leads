"""
Manatee County Arrest Scraper: Revize CMS roster, residential egress only
=========================================================================
Source: Manatee County Sheriff's Office
URL: https://manatee-sheriff.revize.com/bookings
Method: stock Playwright Chromium (no Patchright, no stealth context, no
proxy) loading the public roster table from a host whose own exit is US
residential: Brendan's home relay that Leads Ops runs. There is no proxy,
SOCKS, APE/Warren, CAPTCHA-solver or stealth path in this module.

Source contract (columns mapped by header, never by position):
    Booking # | Last Name | First Name | Middle | Charge | Arrest Date | Released

* ``Booking_Number`` is the source ``Booking #`` cell. When the row links to
  ``/bookings/<id>`` the id must match the cell (column-shift guard). A
  booking number that maps to two different people raises ParseDriftError.
* A booking listed on several rows (one per charge) becomes ONE record whose
  charges are joined with `` | `` and mirrored into
  ``extra_data["charge_details"]`` for one-click hydrate.
* The roster publishes no bond, statute or degree, and detail pages are
  CF-blocked, so ``Bond_Amount`` and ``Bond_Type`` stay ``""`` (unknown; the
  scorer gives no bond points and hydrate leaves the amount blank). Never "0".
* ``Released`` is a required column. Blank / In Custody / No / N/A means in
  custody; Yes / Released or a date means released. Anything else raises
  ParseDriftError, so header or value drift fails closed instead of marking
  everyone In Custody.
* Paging fails closed (ParseDriftError, nothing written) on a missing table,
  missing required column, an empty first page, a repeated page, an empty
  page the pager still offered, a MAX_PAGES stop while a next page is still
  offered, fewer pages walked than the pager advertises, or a walked count
  that disagrees with a published total.
* A Cloudflare challenge / block page, or no usable residential exit, raises
  :class:`EgressBlocked` (classified ``anti_bot`` + ``egress_block``), never
  a silent empty run.

Egress (``MANATEE_EGRESS_MODE``, default and only value ``direct``):
    The host's own exit must be verified US residential (known ISP org,
    country US) or the run raises EgressBlocked before touching the source.
    Proxy environment variables (HTTP(S)_PROXY, ALL_PROXY, SCRAPER_SOCKS_PROXY,
    WARREN_*) are ignored: the exit check runs with ``trust_env=False`` and
    Chromium is launched with ``--no-proxy-server`` and a proxy-free env.
    The old ``auto`` value (APE/Warren + office SOCKS resolver) was removed
    2026-10-07 and now fails loudly as a config error.
See docs/ops/MANATEE_RESIDENTIAL_RUN.md.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper
from scrapers.scraper_resilience import EgressBlocked, ParseDriftError

logger = logging.getLogger(__name__)

BASE_URL = "https://manatee-sheriff.revize.com"
BOOKINGS_URL = f"{BASE_URL}/bookings"
MAX_PAGES = 20
PAGE_DELAY_S = 3.0
EGRESS_MODES = ("direct",)
CF_WAIT_S = 45

# Normalised header text -> logical column
HEADER_ALIASES: Dict[str, Tuple[str, ...]] = {
    "booking": ("booking #", "booking#", "booking no", "booking no.", "booking number", "booking"),
    "last": ("last name", "last"),
    "first": ("first name", "first"),
    "middle": ("middle", "middle name", "mid.", "mid"),
    "charge": ("charge", "charges", "charge description"),
    "arrest_date": ("arrest date", "arrest date/time", "arrested"),
    "released": ("released", "release date", "release"),
}
REQUIRED_COLUMNS = ("booking", "last", "first", "charge", "arrest_date", "released")

_BOOKING_RE = re.compile(r"^(?=.*\d)[A-Z0-9][A-Z0-9-]{3,19}$", re.I)
_DETAIL_ID_RE = re.compile(r"/bookings/([A-Za-z0-9-]+)/?(?:[?#].*)?$")
_IN_CUSTODY_VALUES = {"", "in custody", "no", "n/a", "na", "-"}
_RELEASED_VALUES = {"yes", "released", "y"}
# Only unambiguous "published total" phrasings (a bare "100 entries" can be a
# page-size selector, so it is deliberately not matched).
_TOTAL_RES = (
    re.compile(r"showing\s+\d[\d,]*\s*(?:to|-)\s*\d[\d,]*\s+of\s+(\d[\d,]*)", re.I),
    re.compile(r"\bof\s+(\d[\d,]*)\s+(?:total\s+)?(?:bookings|inmates|results|records|entries)\b", re.I),
    re.compile(r"\btotal\s*(?:bookings|inmates|results|records)?\s*[:=]\s*(\d[\d,]*)", re.I),
)
_CF_TITLE_MARKERS = (
    "just a moment",
    "attention required",
    "security verification",
    "access denied",
    "verify you are human",
)
_CF_BODY_MARKERS = (
    "cf-chl",
    "challenge-platform",
    "cf-error-details",
    "enable javascript and cookies to continue",
    "sorry, you have been blocked",
)


# ── Egress ───────────────────────────────────────────────────────────────────
def egress_mode() -> str:
    """``MANATEE_EGRESS_MODE``: only ``direct``. Anything else fails loudly."""
    mode = (os.getenv("MANATEE_EGRESS_MODE") or "direct").strip().lower()
    if mode not in EGRESS_MODES:
        raise ValueError(
            f"MANATEE_EGRESS_MODE={mode!r} is not supported; only 'direct' (the host's "
            "own residential exit). The APE/office SOCKS 'auto' path was removed."
        )
    return mode


def is_egress_block(
    *,
    status: Optional[int] = None,
    title: str = "",
    body: str = "",
    headers: Optional[Dict[str, str]] = None,
) -> bool:
    """True when the response is a Cloudflare challenge / block page.

    This is the signature of an egress (IP reputation) block, which must not
    be mistaken for an empty roster or a parse problem.
    """
    hdrs = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
    if hdrs.get("cf-mitigated"):
        return True
    t = (title or "").strip().lower()
    if any(m in t for m in _CF_TITLE_MARKERS):
        return True
    b = (body or "")[:20000].lower()
    if any(m in b for m in _CF_BODY_MARKERS):
        return True
    if status in (403, 429, 503) and "cloudflare" in hdrs.get("server", ""):
        return True
    return False


def resolve_egress(scraper: Any = None) -> Tuple[None, str]:
    """Verify this host's own exit is US residential, or raise EgressBlocked.

    Always returns ``(None, "direct")``: there is no proxy to resolve. The
    exit lookup ignores proxy env vars, and an exit whose org/country can't
    be looked up is unverified and refused (#113).
    """
    egress_mode()
    from scrapers.cf_browser import check_exit_ip

    try:
        info = check_exit_ip(None, timeout=15.0, retries=2, trust_env=False)
    except Exception as exc:  # noqa: BLE001 - any lookup failure is unverified
        info = {"error": str(exc)}
    if not info.get("residential_likely"):
        raise EgressBlocked(
            "egress_block: MANATEE_EGRESS_MODE=direct but this host's exit is not verified "
            f"US residential (ip={info.get('ip')} org={info.get('org')!r} "
            f"country={info.get('country')} err={info.get('error')}). Run on the "
            "Leads Ops home relay with VPN off (docs/ops/MANATEE_RESIDENTIAL_RUN.md)."
        )
    logger.info(
        "[Manatee] direct residential egress ip=%s org=%s", info.get("ip"), info.get("org")
    )
    return None, "direct"


def browser_env(environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Copy of the environment without any variable whose name mentions a proxy
    (HTTP(S)_PROXY, ALL_PROXY, NO_PROXY, SCRAPER_SOCKS_PROXY, WARREN_PROXY_*, ...)."""
    src = os.environ if environ is None else environ
    return {k: v for k, v in src.items() if "proxy" not in k.lower()}


def launch_plain_browser():
    """Stock Playwright Chromium, headless, no proxy. Returns ``(pw, browser)``."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-proxy-server"],
            env=browser_env(),
        )
    except Exception:
        pw.stop()
        raise
    return pw, browser


def wait_for_page(page, *, max_wait: float = CF_WAIT_S, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Passively wait for an interstitial to go away. No clicks, no solving.

    Returns False when the page title still looks like a Cloudflare
    challenge/block after ``max_wait`` seconds.
    """
    waited = 0.0
    while True:
        try:
            title = page.title() or ""
        except Exception:  # navigation in flight: keep waiting
            title = None
        if title is not None and not is_egress_block(title=title):
            return True
        if waited >= max_wait:
            return False
        sleep(1.5)
        waited += 1.5


# ── Pure parsing (unit-tested with live-shaped fixtures) ────────────────────
def _norm_header(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower()).rstrip(":").strip()


def map_headers(headers: List[str]) -> Dict[str, int]:
    """Map logical columns to indexes; raise ParseDriftError on a missing column."""
    index: Dict[str, int] = {}
    normed = [_norm_header(h) for h in headers or []]
    for logical, aliases in HEADER_ALIASES.items():
        for i, h in enumerate(normed):
            if h in aliases:
                index[logical] = i
                break
    missing = [c for c in REQUIRED_COLUMNS if c not in index]
    if missing:
        raise ParseDriftError(
            f"Manatee: missing column(s) {missing} in roster headers {headers!r}"
        )
    return index


_DATETIME_FORMATS = (
    "%m/%d/%Y %I:%M %p", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S",
    "%m-%d-%Y %I:%M %p", "%m-%d-%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p",
)
_DATE_FORMATS = (
    "%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%y", "%b %d, %Y", "%B %d, %Y",
)


def parse_date_time(text: str, *, strict: bool = False) -> Tuple[str, str]:
    """``(YYYY-MM-DD, HH:MM)`` from a roster date cell.

    Unparseable non-empty text raises ParseDriftError when ``strict``; otherwise
    the raw text is kept as the date (source-faithful, logged) so an unexpected
    arrest-date format does not drop the whole roster.
    """
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return "", ""
    for fmt in _DATETIME_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
        except ValueError:
            continue
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d"), ""
        except ValueError:
            continue
    if strict:
        raise ParseDriftError(f"Manatee: unparseable date {raw!r}")
    logger.warning("[Manatee] unrecognised date format %r kept as-is", raw)
    return raw, ""


def parse_released(value: str) -> Tuple[str, str]:
    """Return ``(Status, Release_Date)``; unrecognised values raise ParseDriftError."""
    v = re.sub(r"\s+", " ", (value or "").strip())
    low = v.lower()
    if low in _IN_CUSTODY_VALUES:
        return "In Custody", ""
    if low in _RELEASED_VALUES:
        return "Released", ""
    try:
        date, _time = parse_date_time(v, strict=True)
    except ParseDriftError:
        date = ""
    if date:
        return "Released", date
    raise ParseDriftError(f"Manatee: unrecognised Released value {v!r}")


def extract_published_total(text: str) -> Optional[int]:
    for rx in _TOTAL_RES:
        m = rx.search(text or "")
        if m:
            return int(m.group(1).replace(",", ""))
    return None


def parse_roster_page(payload: Dict[str, Any], *, page_no: int = 1) -> List[Dict[str, Any]]:
    """Turn one page payload (from the browser) into validated row dicts.

    ``payload`` keys: ``has_table`` (bool), ``headers`` (list[str]), ``rows``
    (list of ``{"cells": [...], "href": str, "img": str}``).
    """
    if not payload.get("has_table"):
        raise ParseDriftError(f"Manatee: no roster table on page {page_no}")
    idx = map_headers(payload.get("headers") or [])
    out: List[Dict[str, Any]] = []
    malformed = 0
    raw_rows = payload.get("rows") or []
    for row in raw_rows:
        cells = [re.sub(r"\s+", " ", str(c or "")).strip() for c in (row.get("cells") or [])]
        if len(cells) <= max(idx.values()):
            malformed += 1
            continue
        booking = cells[idx["booking"]]
        if not _BOOKING_RE.match(booking):
            malformed += 1
            continue
        href = str(row.get("href") or "").strip()
        m = _DETAIL_ID_RE.search(href) if href else None
        if m and m.group(1) != booking:
            raise ParseDriftError(
                f"Manatee: Booking # cell {booking!r} != detail link id {m.group(1)!r} "
                f"on page {page_no} (column shift)"
            )
        status, release_date = parse_released(cells[idx["released"]])
        arrest_date, arrest_time = parse_date_time(cells[idx["arrest_date"]])
        img = str(row.get("img") or "").strip()
        out.append({
            "booking": booking,
            "last": cells[idx["last"]],
            "first": cells[idx["first"]],
            "middle": cells[idx["middle"]] if "middle" in idx else "",
            "charge": cells[idx["charge"]],
            "arrest_date": arrest_date,
            "arrest_time": arrest_time,
            "status": status,
            "release_date": release_date,
            "detail_url": urljoin(BASE_URL + "/", href) if m else "",
            "mugshot_url": urljoin(BASE_URL + "/", img) if img else "",
        })
    if raw_rows and not out:
        raise ParseDriftError(
            f"Manatee: page {page_no} had {len(raw_rows)} rows but no source Booking #"
        )
    if malformed:
        logger.warning("[Manatee] page %s: dropped %s malformed rows", page_no, malformed)
    return out


def build_records(rows: List[Dict[str, Any]]) -> List[ArrestRecord]:
    """Group charge rows by source Booking # into one ArrestRecord each."""
    grouped: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for r in rows:
        key = r["booking"]
        person = (r["last"].upper(), r["first"].upper())
        g = grouped.get(key)
        if g is None:
            grouped[key] = g = {**r, "person": person, "charges": [], "statuses": [], "release_dates": []}
            order.append(key)
        elif g["person"] != person:
            raise ParseDriftError(
                f"Manatee: Booking # {key} maps to two people {g['person']} / {person} (key collision)"
            )
        # Keep every charge row (two counts of the same charge are two bonds).
        if r["charge"]:
            g["charges"].append(r["charge"])
        g["statuses"].append(r["status"])
        if r["release_date"]:
            g["release_dates"].append(r["release_date"])
        for k in ("arrest_date", "arrest_time", "middle", "detail_url", "mugshot_url"):
            if not g.get(k) and r.get(k):
                g[k] = r[k]

    records: List[ArrestRecord] = []
    for key in order:
        g = grouped[key]
        released = all(s == "Released" for s in g["statuses"])
        full_name = f"{g['last']}, {g['first']}" + (f" {g['middle']}" if g["middle"] else "")
        charges = g["charges"]
        records.append(ArrestRecord(
            County="Manatee",
            State="FL",
            Booking_Number=key,
            Full_Name=full_name,
            First_Name=g["first"],
            Middle_Name=g["middle"],
            Last_Name=g["last"],
            Arrest_Date=g["arrest_date"],
            Arrest_Time=g["arrest_time"],
            # Roster has no separate booking timestamp; Arrest Date is the
            # closest source field (recorded in extra_data below).
            Booking_Date=g["arrest_date"],
            Booking_Time=g["arrest_time"],
            Charges=" | ".join(charges),
            # Not published on the roster: unknown, never "0".
            Bond_Amount="",
            Bond_Type="",
            Facility="Manatee County Jail",
            Status="Released" if released else "In Custody",
            Release_Date=max(g["release_dates"]) if released and g["release_dates"] else "",
            Mugshot_URL=g["mugshot_url"],
            Detail_URL=g["detail_url"] or f"{BASE_URL}/bookings/{key}",
            extra_data={
                "booking_key_origin": "source-issued Revize Booking #",
                "booking_date_origin": "roster Arrest Date",
                "bond_published": False,
                "charge_details": [{"charge": c, "description": c} for c in charges],
            },
        ))
    return records


def walk_roster(
    fetch_page: Callable[[str, int], Dict[str, Any]],
    *,
    max_pages: int = MAX_PAGES,
    sleep: Callable[[float], None] = time.sleep,
) -> Tuple[List[ArrestRecord], Dict[str, Any]]:
    """Walk every roster page and fail closed on any completeness drift.

    ``fetch_page(url, page_no)`` returns the browser payload, which may also
    carry ``text`` (page text for a published total), ``next_href`` and
    ``max_page`` (highest page number the pager links to).
    """
    url = BOOKINGS_URL
    all_rows: List[Dict[str, Any]] = []
    seen_urls = set()
    seen_sigs: Dict[Tuple[str, ...], int] = {}
    published_total: Optional[int] = None
    advertised_max = 1
    pages_walked = 0

    for pg in range(1, max_pages + 1):
        if url in seen_urls:
            raise ParseDriftError(f"Manatee: pager looped back to {url} at page {pg}")
        seen_urls.add(url)
        payload = fetch_page(url, pg)
        rows = parse_roster_page(payload, page_no=pg)
        pages_walked = pg
        if not rows:
            if pg == 1:
                raise ParseDriftError("Manatee: roster table is empty on page 1")
            raise ParseDriftError(f"Manatee: page {pg} is empty but the pager offered it")
        sig = tuple(r["booking"] for r in rows)
        if sig in seen_sigs:
            raise ParseDriftError(
                f"Manatee: page {pg} repeats page {seen_sigs[sig]} (paging ignored / looped)"
            )
        seen_sigs[sig] = pg
        all_rows.extend(rows)

        total = extract_published_total(payload.get("text") or "")
        if total is not None:
            published_total = total
        try:
            advertised_max = max(advertised_max, int(payload.get("max_page") or 1))
        except (TypeError, ValueError):
            pass

        next_href = str(payload.get("next_href") or "").strip()
        if next_href:
            next_url = urljoin(url, next_href)
        elif pg < advertised_max:
            next_url = f"{BOOKINGS_URL}?page={pg + 1}"
        else:
            next_url = ""
        if not next_url:
            break
        if pg == max_pages:
            raise ParseDriftError(
                f"Manatee: hit MAX_PAGES={max_pages} with a next page still offered; walk incomplete"
            )
        url = next_url
        sleep(PAGE_DELAY_S)

    records = build_records(all_rows)
    if published_total is not None and published_total not in (len(all_rows), len(records)):
        raise ParseDriftError(
            f"Manatee: walked {len(all_rows)} rows / {len(records)} bookings but the page "
            f"publishes a total of {published_total}; walk incomplete"
        )
    meta = {
        "pages": pages_walked,
        "rows": len(all_rows),
        "bookings": len(records),
        "published_total": published_total,
    }
    return records, meta


# Browser-side extraction: headers, rows (cells + detail link + image),
# page text, pager next link and highest advertised page number.
_EXTRACT_JS = r"""() => {
    const table = document.querySelector('table');
    const out = {has_table: !!table, headers: [], rows: [], text: '', next_href: '', max_page: 1,
                 title: document.title || ''};
    out.text = (document.body && document.body.innerText || '').slice(0, 20000);
    if (table) {
        let ths = Array.from(table.querySelectorAll('thead th'));
        if (!ths.length) {
            const first = table.querySelector('tr');
            ths = first ? Array.from(first.querySelectorAll('th')) : [];
        }
        out.headers = ths.map(th => th.textContent.trim());
        const body = table.querySelector('tbody') || table;
        out.rows = Array.from(body.querySelectorAll('tr'))
            .filter(r => r.querySelectorAll('td').length)
            .map(r => {
                const a = r.querySelector('a[href*="/bookings/"]');
                const img = r.querySelector('img');
                return {
                    cells: Array.from(r.querySelectorAll('td')).map(c => c.textContent.trim()),
                    href: a ? a.getAttribute('href') : '',
                    img: img ? (img.getAttribute('src') || '') : '',
                };
            });
    }
    const links = Array.from(document.querySelectorAll('a[href*="page="]'));
    for (const a of links) {
        const m = (a.getAttribute('href') || '').match(/[?&]page=(\d+)/);
        if (m) out.max_page = Math.max(out.max_page, parseInt(m[1], 10));
        const li = a.closest('li');
        const disabled = li && /disabled/.test(li.className || '');
        const label = (a.getAttribute('rel') || '') + ' ' + (a.getAttribute('aria-label') || '') + ' ' + a.textContent;
        if (!disabled && /next|›|»/i.test(label)) out.next_href = a.getAttribute('href');
    }
    return out;
}"""


class ManateeCountyScraper(BaseScraper):

    @property
    def county(self) -> str:
        return "Manatee"

    def scrape(self) -> List[ArrestRecord]:
        _, egress_source = resolve_egress(self)
        logger.info("[Manatee] egress mode=%s source=%s", egress_mode(), egress_source)

        pw = browser = None
        try:
            pw, browser = launch_plain_browser()
            page = browser.new_context().new_page()

            def fetch_page(url: str, pg: int) -> Dict[str, Any]:
                logger.info("[Manatee] roster page %s", pg)
                resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                status = getattr(resp, "status", None) if resp is not None else None
                try:
                    headers = dict(resp.headers) if resp is not None else {}
                except Exception:
                    headers = {}
                cleared = wait_for_page(page)
                payload = page.evaluate(_EXTRACT_JS)
                blocked = is_egress_block(
                    title=payload.get("title") or "",
                    body=page.content() if not payload.get("has_table") else "",
                )
                if not cleared or blocked or (
                    not payload.get("has_table")
                    and is_egress_block(status=status, headers=headers)
                ):
                    raise EgressBlocked(
                        f"egress_block: Manatee page {pg} stuck on a Cloudflare challenge/block "
                        f"(HTTP {status}) via {egress_source} exit. Nothing written. Run on "
                        "the Leads Ops home relay with MANATEE_EGRESS_MODE=direct "
                        "(docs/ops/MANATEE_RESIDENTIAL_RUN.md)."
                    )
                return payload

            records, meta = walk_roster(fetch_page)
            meta["egress_source"] = egress_source
            self.last_walk_meta = meta
            logger.info(
                "[Manatee] %s bookings from %s rows over %s pages (published total=%s, egress=%s)",
                meta["bookings"], meta["rows"], meta["pages"], meta["published_total"], egress_source,
            )
            return records

        except Exception as e:
            logger.error("[Manatee] run failed: %s", e)
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
