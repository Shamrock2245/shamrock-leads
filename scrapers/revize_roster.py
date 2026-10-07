"""Shared Revize CMS ``/bookings`` roster contract (pure, county-parameterised).

Used by Charlotte FL. Manatee FL (#113) carries the same contract in
``scrapers/counties/manatee.py``; it can move onto this module in a later PR.
No network here: the county scraper loads pages from residential egress and
hands this module the browser payload.

Contract (columns mapped by header, never by position):
    Booking # | Last Name | First Name | Middle | Charge | Arrest Date | Released

* ``Booking_Number`` is the source ``Booking #`` cell. When the row links to
  ``/bookings/<id>`` the id must match the cell (column-shift guard). One
  booking number naming two different people raises ParseDriftError.
* A booking listed on several rows (one per charge) becomes ONE record; every
  charge is kept, joined with `` | `` and mirrored into
  ``extra_data["charge_details"]`` for one-click hydrate.
* The roster publishes no bond, so ``Bond_Amount``/``Bond_Type`` are ``""``
  (unknown), never ``"0"``.
* ``Released`` is required. Blank / In Custody / No / N/A -> in custody;
  Yes / Released / a date -> released; anything else raises ParseDriftError.
* Paging fails closed on: no table, missing column, empty first page, empty
  or repeated page, pager loop, MAX_PAGES with a next page still offered,
  or a walked count that disagrees with a published total.
* Cloudflare challenge/block pages are egress blocks (``EgressBlocked``),
  never an empty roster.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from core.models import ArrestRecord
from scrapers.scraper_resilience import EgressBlocked, ParseDriftError

# #113's Cloudflare challenge/block classifier, shared rather than copied.
from scrapers.counties.manatee import is_egress_block  # noqa: F401  (re-exported)

logger = logging.getLogger(__name__)

EGRESS_MODES = ("auto", "direct")

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
# Only unambiguous "published total" phrasings; a bare "100 entries" can be a
# page-size selector and is deliberately not matched.
_TOTAL_RES = (
    re.compile(r"showing\s+\d[\d,]*\s*(?:to|-)\s*\d[\d,]*\s+of\s+(\d[\d,]*)", re.I),
    re.compile(r"\bof\s+(\d[\d,]*)\s+(?:total\s+)?(?:bookings|inmates|results|records|entries)\b", re.I),
    re.compile(r"\btotal\s*(?:bookings|inmates|results|records)?\s*[:=]\s*(\d[\d,]*)", re.I),
)
_DATETIME_FORMATS = (
    "%m/%d/%Y %I:%M %p", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S",
    "%m-%d-%Y %I:%M %p", "%m-%d-%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p",
)
_DATE_FORMATS = (
    "%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%y", "%b %d, %Y", "%B %d, %Y",
)


@dataclass(frozen=True)
class RevizeRoster:
    """One county's Revize roster: label, base URL, facility and page cap."""

    county: str
    base_url: str
    facility: str
    max_pages: int = 20
    page_delay_s: float = 3.0

    @property
    def bookings_url(self) -> str:
        return f"{self.base_url}/bookings"

    # ── Pure parsing ────────────────────────────────────────────────────────
    def map_headers(self, headers: List[str]) -> Dict[str, int]:
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
                f"{self.county}: missing column(s) {missing} in roster headers {headers!r}"
            )
        return index

    def parse_date_time(self, text: str, *, strict: bool = False) -> Tuple[str, str]:
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
            raise ParseDriftError(f"{self.county}: unparseable date {raw!r}")
        logger.warning("[%s] unrecognised date format %r kept as-is", self.county, raw)
        return raw, ""

    def parse_released(self, value: str) -> Tuple[str, str]:
        v = re.sub(r"\s+", " ", (value or "").strip())
        low = v.lower()
        if low in _IN_CUSTODY_VALUES:
            return "In Custody", ""
        if low in _RELEASED_VALUES:
            return "Released", ""
        try:
            date, _ = self.parse_date_time(v, strict=True)
        except ParseDriftError:
            date = ""
        if date:
            return "Released", date
        raise ParseDriftError(f"{self.county}: unrecognised Released value {v!r}")

    def parse_roster_page(self, payload: Dict[str, Any], *, page_no: int = 1) -> List[Dict[str, Any]]:
        """Validated row dicts from one browser payload.

        ``payload``: ``has_table``, ``headers`` and ``rows``
        (``[{"cells": [...], "href": str, "img": str}]``).
        """
        if not payload.get("has_table"):
            raise ParseDriftError(f"{self.county}: no roster table on page {page_no}")
        idx = self.map_headers(payload.get("headers") or [])
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
                    f"{self.county}: Booking # cell {booking!r} != detail link id "
                    f"{m.group(1)!r} on page {page_no} (column shift)"
                )
            status, release_date = self.parse_released(cells[idx["released"]])
            arrest_date, arrest_time = self.parse_date_time(cells[idx["arrest_date"]])
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
                "detail_url": urljoin(self.base_url + "/", href) if m else "",
                "mugshot_url": urljoin(self.base_url + "/", img) if img else "",
            })
        if raw_rows and not out:
            raise ParseDriftError(
                f"{self.county}: page {page_no} had {len(raw_rows)} rows but no source Booking #"
            )
        if malformed:
            logger.warning("[%s] page %s: dropped %s malformed rows", self.county, page_no, malformed)
        return out

    def build_records(self, rows: List[Dict[str, Any]]) -> List[ArrestRecord]:
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
                    f"{self.county}: Booking # {key} maps to two people "
                    f"{g['person']} / {person} (key collision)"
                )
            if r["charge"]:
                g["charges"].append(r["charge"])  # two counts = two bonds; keep both
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
                County=self.county,
                State="FL",
                Booking_Number=key,
                Full_Name=full_name,
                First_Name=g["first"],
                Middle_Name=g["middle"],
                Last_Name=g["last"],
                Arrest_Date=g["arrest_date"],
                Arrest_Time=g["arrest_time"],
                # No separate booking timestamp on the roster; Arrest Date is the
                # closest source field (recorded in extra_data).
                Booking_Date=g["arrest_date"],
                Booking_Time=g["arrest_time"],
                Charges=" | ".join(charges),
                Bond_Amount="",  # not published on the roster: unknown, never "0"
                Bond_Type="",
                Facility=self.facility,
                Status="Released" if released else "In Custody",
                Release_Date=max(g["release_dates"]) if released and g["release_dates"] else "",
                Mugshot_URL=g["mugshot_url"],
                Detail_URL=g["detail_url"] or f"{self.base_url}/bookings/{key}",
                extra_data={
                    "booking_key_origin": "source-issued Revize Booking #",
                    "booking_date_origin": "roster Arrest Date",
                    "bond_published": False,
                    "charge_details": [{"charge": c, "description": c} for c in charges],
                },
            ))
        return records

    def walk(
        self,
        fetch_page: Callable[[str, int], Dict[str, Any]],
        *,
        max_pages: Optional[int] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Tuple[List[ArrestRecord], Dict[str, Any]]:
        """Walk every roster page; fail closed on any completeness drift."""
        max_pages = max_pages or self.max_pages
        url = self.bookings_url
        all_rows: List[Dict[str, Any]] = []
        seen_urls = set()
        seen_sigs: Dict[Tuple[str, ...], int] = {}
        published_total: Optional[int] = None
        advertised_max = 1
        pages_walked = 0

        for pg in range(1, max_pages + 1):
            if url in seen_urls:
                raise ParseDriftError(f"{self.county}: pager looped back to {url} at page {pg}")
            seen_urls.add(url)
            payload = fetch_page(url, pg)
            rows = self.parse_roster_page(payload, page_no=pg)
            pages_walked = pg
            if not rows:
                if pg == 1:
                    raise ParseDriftError(f"{self.county}: roster table is empty on page 1")
                raise ParseDriftError(f"{self.county}: page {pg} is empty but the pager offered it")
            sig = tuple(r["booking"] for r in rows)
            if sig in seen_sigs:
                raise ParseDriftError(
                    f"{self.county}: page {pg} repeats page {seen_sigs[sig]} (paging ignored / looped)"
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
                next_url = f"{self.bookings_url}?page={pg + 1}"
            else:
                next_url = ""
            if not next_url:
                break
            if pg == max_pages:
                raise ParseDriftError(
                    f"{self.county}: hit MAX_PAGES={max_pages} with a next page still offered; "
                    "walk incomplete"
                )
            url = next_url
            sleep(self.page_delay_s)

        records = self.build_records(all_rows)
        if published_total is not None and published_total not in (len(all_rows), len(records)):
            raise ParseDriftError(
                f"{self.county}: walked {len(all_rows)} rows / {len(records)} bookings but the "
                f"page publishes a total of {published_total}; walk incomplete"
            )
        return records, {
            "pages": pages_walked,
            "rows": len(all_rows),
            "bookings": len(records),
            "published_total": published_total,
        }


def _norm_header(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower()).rstrip(":").strip()


def extract_published_total(text: str) -> Optional[int]:
    for rx in _TOTAL_RES:
        m = rx.search(text or "")
        if m:
            return int(m.group(1).replace(",", ""))
    return None


# ── Egress ───────────────────────────────────────────────────────────────────
def egress_mode(env_var: str) -> str:
    """``<COUNTY>_EGRESS_MODE`` (auto|direct). Unknown values fail loudly."""
    mode = (os.getenv(env_var) or "auto").strip().lower()
    if mode not in EGRESS_MODES:
        raise ValueError(f"{env_var}={mode!r} is not one of {EGRESS_MODES}")
    return mode


def resolve_egress(
    scraper: Any, *, county: str, env_var: str, sticky_session: str
) -> Tuple[Optional[str], str]:
    """``(proxy_url_or_None, source)`` for this run, or raise ``EgressBlocked``.

    ``direct`` never resolves a proxy: the host itself must be a verified US
    residential exit (an exit whose org/country can't be looked up is
    unverified and refused, per #113). ``auto`` keeps the existing resolver.
    """
    mode = egress_mode(env_var)
    if mode == "direct":
        from scrapers.socks_proxy import validate_residential_proxy

        ok, info = validate_residential_proxy(None, require_residential_exit=True)
        if not ok:
            raise EgressBlocked(
                f"egress_block: {env_var}=direct but this host's exit is not verified US "
                f"residential (ip={info.get('ip')} org={info.get('org')!r} "
                f"country={info.get('country')} err={info.get('error')}). Run {county} from "
                "Leads Ops residential egress with VPN off."
            )
        logger.info("[%s] direct residential egress ip=%s org=%s", county, info.get("ip"), info.get("org"))
        return None, "direct"

    from scrapers.socks_proxy import resolve_residential_proxy

    try:
        return resolve_residential_proxy(
            scraper, sticky_session=sticky_session, require=True, max_ape_attempts=5
        )
    except RuntimeError as exc:
        raise EgressBlocked(
            f"egress_block: no residential exit for {county} Revize ({exc}). "
            f"Run from Leads Ops residential egress with {env_var}=direct."
        ) from exc


def check_page_egress(
    *, county: str, pg: int, cleared: bool, payload: Dict[str, Any], status: Optional[int],
    headers: Dict[str, str], body: str, egress_source: str, env_var: str,
) -> None:
    """Raise ``EgressBlocked`` when a loaded page is a Cloudflare challenge/block."""
    blocked = is_egress_block(title=payload.get("title") or "", body=body)
    if not cleared or blocked or (
        not payload.get("has_table") and is_egress_block(status=status, headers=headers)
    ):
        raise EgressBlocked(
            f"egress_block: {county} page {pg} stuck on a Cloudflare challenge/block "
            f"(HTTP {status}) via {egress_source} exit. Nothing written. Run from residential "
            f"egress with {env_var}=direct."
        )


# Browser-side extraction: headers, rows (cells + detail link + image), page
# text, pager next link, highest advertised page number and title.
EXTRACT_JS = r"""() => {
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
