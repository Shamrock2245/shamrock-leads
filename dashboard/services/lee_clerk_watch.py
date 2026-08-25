"""Lee Clerk / jail follow-up after a bond is written.

Lee Clerk matrix records typically take about a day to catch up after a
bond is posted. This watcher:

  * does **not** hammer clerk search during that first window
  * after ~20 hours, probes clerk once the case/POA should exist
  * refreshes the Lee Sheriff booking page on a short interval so later
    bond-amount / case-number changes land in Mongo near-instantly

Staff can still mark clerk-posted by hand. Weak HTML (SPA shells) never
locks a POA.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from dashboard.deps import get_collection
from dashboard.services.packet_builder_service import lee_clerk_search_url
from dashboard.services.place_identity import is_lee_florida, mongo_place_clause

logger = logging.getLogger(__name__)

FIRST_CLERK_CHECK_HOURS = 20
EARLY_JAIL_REFRESH_MINUTES = 45
FAST_JAIL_REFRESH_MINUTES = 8
CLERK_PROBE_TIMEOUT_S = 8.0
MIN_CLERK_HTML_BYTES = 800


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def hours_since(anchor: Any, now: datetime) -> Optional[float]:
    start = _as_dt(anchor)
    if not start:
        return None
    return (now - start).total_seconds() / 3600.0


def clerk_check_due(
    *,
    written_at: Any,
    now: datetime,
    already_posted: bool,
    first_check_hours: float = FIRST_CLERK_CHECK_HOURS,
) -> bool:
    if already_posted:
        return False
    elapsed = hours_since(written_at, now)
    if elapsed is None:
        return False
    return elapsed >= first_check_hours


def jail_refresh_due(
    *,
    last_refresh_at: Any,
    now: datetime,
    hours_since_write: Optional[float],
) -> bool:
    last = _as_dt(last_refresh_at)
    interval_min = (
        FAST_JAIL_REFRESH_MINUTES
        if hours_since_write is not None and hours_since_write >= FIRST_CLERK_CHECK_HOURS
        else EARLY_JAIL_REFRESH_MINUTES
    )
    if last is None:
        return True
    return now - last >= timedelta(minutes=interval_min)


def clerk_page_indicates_posted(html: str, query: str) -> bool:
    """Fail closed: SPA shells and a bare 'posted' never lock a POA."""
    body = html or ""
    if len(body) < MIN_CLERK_HTML_BYTES:
        return False
    needle = (query or "").strip()
    if not needle or needle.lower() not in body.lower():
        return False
    return bool(
        re.search(
            r"\b(surety|appearance\s+bond|power of attorney|bond posted|posted bond)\b",
            body,
            re.I,
        )
    )


async def probe_lee_clerk(query: str, *, client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    q = (query or "").strip()
    url = lee_clerk_search_url(q, "")
    if not q:
        return {"success": False, "posted": False, "url": url, "reason": "empty_query"}
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=CLERK_PROBE_TIMEOUT_S, follow_redirects=True)
    try:
        resp = await http.get(
            url,
            headers={"User-Agent": "ShamrockBailBonds/lee-clerk-watch"},
        )
        html = resp.text or ""
        posted = resp.status_code == 200 and clerk_page_indicates_posted(html, q)
        return {
            "success": resp.status_code == 200,
            "posted": posted,
            "url": url,
            "status_code": resp.status_code,
            "bytes": len(html),
            "reason": "posted" if posted else "inconclusive",
        }
    except Exception as exc:
        logger.debug("lee clerk probe failed: %s", type(exc).__name__)
        return {"success": False, "posted": False, "url": url, "reason": type(exc).__name__}
    finally:
        if own_client:
            await http.aclose()


async def apply_clerk_posted(booking_number: str, *, source: str = "lee_clerk_watch") -> Dict[str, int]:
    booking = (booking_number or "").strip()
    if not booking:
        return {}
    now = datetime.now(timezone.utc).isoformat()
    patch = {
        "clerk_bond_posted": True,
        "clerk_bond_posted_at": now,
        "clerk_bond_posted_county": "Lee",
        "clerk_bond_posted_source": source,
        "poa_locked": True,
        "updated_at": now,
    }
    lookup = {"$or": [{"booking_number": booking}, {"Booking_Number": booking}]}
    updated = {}
    for name in ("bond_cases", "active_bonds", "arrests", "defendants", "paperwork_packets"):
        try:
            result = await get_collection(name).update_many(lookup, {"$set": patch})
            updated[name] = int(getattr(result, "matched_count", 0) or 0)
        except Exception:
            updated[name] = 0
    return updated


async def _refresh_jail_source(arrest: Dict[str, Any]) -> Dict[str, Any]:
    booking = str(arrest.get("booking_number") or "").strip()
    detail_url = str(arrest.get("detail_url") or arrest.get("source_url") or "").strip()
    if not detail_url and booking:
        detail_url = f"https://www.sheriffleefl.org/booking/?id={booking}"
    if not detail_url:
        return {"attempted": False, "updated": False}
    try:
        from dashboard.services.url_ingest_service import ingest_url
        res = await ingest_url(detail_url)
    except Exception as exc:
        logger.debug("lee jail refresh skipped: %s", type(exc).__name__)
        return {"attempted": True, "updated": False, "error": type(exc).__name__}
    data = res.get("data") if isinstance(res, dict) else None
    if not res.get("success") or not isinstance(data, dict):
        return {"attempted": True, "updated": False}
    now_iso = datetime.now(timezone.utc).isoformat()
    fields = {
        "last_source_refresh_at": now_iso,
        "last_source_refresh_by": "lee_clerk_watch",
        "last_source_refresh_url": detail_url,
        "updated_at": now_iso,
    }
    for key in (
        "full_name", "charges", "bond_amount", "bond_type", "case_number",
        "court_date", "court_time", "court_location", "status", "dob",
    ):
        if data.get(key) not in (None, ""):
            fields[key] = data[key]
    if data.get("bond_amount") is not None:
        fields["total_bond_amount"] = data.get("bond_amount")
    await get_collection("arrests").update_one(
        {"booking_number": booking},
        {"$set": fields},
    )
    return {"attempted": True, "updated": True, "bond_amount": data.get("bond_amount")}


def _written_anchor(doc: Dict[str, Any]) -> Any:
    """Bond/packet timestamps only — never jail scrape time."""
    return (
        doc.get("packet_sent_at")
        or doc.get("bond_date")
        or doc.get("written_at")
        or doc.get("posted_date")
        or doc.get("created_at")
        or doc.get("match_timestamp")
    )


def _lee_county_clause() -> Dict[str, Any]:
    return mongo_place_clause("Lee", "FL")


async def _collect_inflight_bookings(limit: int) -> List[str]:
    """Only Shamrock work-in-progress — not the public Lee jail roster."""
    seen: List[str] = []
    found: set[str] = set()

    def _add(raw: Any) -> None:
        booking = str(raw or "").strip()
        if booking and booking not in found:
            found.add(booking)
            seen.append(booking)

    queries = [
        ("active_bonds", {**_lee_county_clause(), "clerk_bond_posted": {"$ne": True}}),
        ("bond_cases", {**_lee_county_clause(), "clerk_bond_posted": {"$ne": True}}),
        ("paperwork_packets", {
            **_lee_county_clause(),
            "clerk_bond_posted": {"$ne": True},
            "voided": {"$ne": True},
        }),
        ("prospective_bonds", _lee_county_clause()),
        ("intake_queue", {
            "matched_booking_number": {"$exists": True, "$nin": [None, ""]},
            **mongo_place_clause(
                "Lee",
                "FL",
                county_fields=("matched_county", "defendant_county", "county"),
                state_fields=("matched_state", "defendant_state", "state"),
            ),
        }),
    ]
    for name, filt in queries:
        try:
            col = get_collection(name)
            cursor = col.find(filt, {"booking_number": 1, "Booking_Number": 1, "matched_booking_number": 1}).limit(limit)
            async for doc in cursor:
                _add(doc.get("booking_number") or doc.get("Booking_Number") or doc.get("matched_booking_number"))
                if len(seen) >= limit:
                    return seen
        except Exception:
            logger.debug("lee clerk watch skipped collection %s", name, exc_info=True)
    return seen


async def run_lee_clerk_watch(
    *,
    now: Optional[datetime] = None,
    limit: int = 25,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """Follow up only on Shamrock Lee cases that are already being written."""
    now = now or datetime.now(timezone.utc)
    bookings = await _collect_inflight_bookings(limit)
    arrests = get_collection("arrests")
    stats = {
        "scanned": 0,
        "clerk_probed": 0,
        "clerk_posted": 0,
        "jail_refreshed": 0,
        "skipped_early": 0,
        "inconclusive": 0,
        "inflight": len(bookings),
    }
    for booking in bookings:
        doc = await arrests.find_one({"booking_number": booking}) or {"booking_number": booking}
        for coll_name in ("active_bonds", "paperwork_packets"):
            try:
                extra = await get_collection(coll_name).find_one(
                    {"$or": [{"booking_number": booking}, {"Booking_Number": booking}]},
                    {
                        "packet_sent_at": 1, "bond_date": 1, "written_at": 1,
                        "posted_date": 1, "created_at": 1, "case_number": 1,
                        "poa_number": 1, "clerk_bond_posted": 1, "county": 1, "state": 1,
                    },
                ) or {}
            except Exception:
                extra = {}
            for key, val in extra.items():
                if key == "_id" or val in (None, ""):
                    continue
                if not doc.get(key):
                    doc[key] = val
        if doc.get("clerk_bond_posted"):
            continue
        county = doc.get("county") or doc.get("County") or "Lee"
        state = doc.get("state") or doc.get("State") or "FL"
        if not is_lee_florida(county, state):
            continue
        stats["scanned"] += 1
        elapsed = hours_since(_written_anchor(doc), now)
        if jail_refresh_due(
            last_refresh_at=doc.get("last_source_refresh_at"),
            now=now,
            hours_since_write=elapsed,
        ):
            refresh = await _refresh_jail_source(doc)
            if refresh.get("updated"):
                stats["jail_refreshed"] += 1
                extra = await arrests.find_one({"booking_number": booking}) or {}
                doc.update(extra)

        query_text = str(doc.get("case_number") or doc.get("poa_number") or "").strip()
        if not clerk_check_due(
            written_at=_written_anchor(doc),
            now=now,
            already_posted=bool(doc.get("clerk_bond_posted")),
        ):
            stats["skipped_early"] += 1
            continue
        if not query_text:
            stats["inconclusive"] += 1
            continue

        probe = await probe_lee_clerk(query_text, client=http_client)
        stats["clerk_probed"] += 1
        if probe.get("posted"):
            await apply_clerk_posted(booking, source="lee_clerk_watch")
            stats["clerk_posted"] += 1
        else:
            stats["inconclusive"] += 1

    return stats
