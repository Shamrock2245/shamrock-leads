"""
FirstAppearanceWatcher — ShamrockLeads
======================================

Automatically re-evaluates "No Bond / Disqualified / Cold" arrest records
for up to 3 days after arrest, catching bond amounts set at first appearance.

Background
----------
Florida law requires a first-appearance hearing within 24 hours of arrest
(Fla. R. Crim. P. 3.130). Defendants with holds or detainers may appear
before a judge on days 2 or 3 as well. Until the judge sets bond, the jail
roster shows bond_amount = 0 / bond_type = "NO BOND", causing our scorer to
mark the record as Disqualified or Cold — even though it may become a Hot
lead within hours.

Strategy
--------
1. Every 30 minutes, query MongoDB for records that are:
   - status = "In Custody" (still jailed)
   - bond_amount = 0 OR bond_type contains NO BOND / HOLD
   - created_at within the last WATCH_WINDOW_DAYS (default: 3)
   - NOT already graduated (lead_status not in Hot/Warm with bond > 0)

2. For each candidate, re-fetch its detail page via detail_url using the
   county's registered scraper (if available) or a generic HTTP fetch.

3. Compare the newly-fetched bond_amount to the stored value:
   - If bond was set (new_bond > 0 and old_bond == 0): re-score, upsert,
     fire a 🔔 BOND SET Slack alert.
   - If bond changed (new_bond != old_bond): re-score, upsert silently.
   - If no change: update last_checked timestamp only.

4. Records older than WATCH_WINDOW_DAYS are automatically dropped from
   the watch list (no further re-checks).

Configuration (env vars)
------------------------
WATCH_WINDOW_DAYS       Days to watch a no-bond record (default: 3)
WATCH_INTERVAL_MINUTES  How often the watcher runs (default: 30)
WATCH_MAX_BATCH         Max records to re-check per run (default: 50)
"""

import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from pymongo import MongoClient, UpdateOne

from config.settings import settings
from core.models import ArrestRecord
from scoring.lead_scorer import LeadScorer

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
WATCH_WINDOW_DAYS   = int(os.getenv("WATCH_WINDOW_DAYS",   "3"))
WATCH_MAX_BATCH     = int(os.getenv("WATCH_MAX_BATCH",     "50"))

# Bond-type strings that indicate "no bond set yet"
_NO_BOND_TYPES = {"NO BOND", "HOLD", "NONE", "DETAINER", "ICE HOLD", "IMMIGRATION"}

# ── County Court Windows (Eastern Time) ───────────────────────────────────────
# First appearance hearing schedules by county.
# Format: { county: { day_range: (start_hour, start_min, end_hour, end_min) } }
# "weekday" = Mon-Fri, "weekend" = Sat-Sun
#
# Lee County: M-F 10:30-11:30 AM ET, Sat-Sun 9:00-10:30 AM ET
_ET = ZoneInfo("America/New_York")

_COURT_WINDOWS: Dict[str, Dict[str, Tuple[int, int, int, int]]] = {
    "Lee": {
        "weekday": (10, 30, 11, 30),   # M-F  10:30 AM - 11:30 AM ET
        "weekend": (9, 0, 10, 30),      # S-S   9:00 AM - 10:30 AM ET
    },
}


def _is_in_court_window(county: str) -> bool:
    """
    Check if the current time (Eastern) falls within the county's
    first-appearance court window. Returns True if:
      - County has no configured window (always eligible), OR
      - Current ET time is within the configured window.
    """
    windows = _COURT_WINDOWS.get(county)
    if not windows:
        return True  # No restriction — always eligible

    now_et = datetime.now(_ET)
    is_weekend = now_et.weekday() >= 5  # 5=Sat, 6=Sun
    key = "weekend" if is_weekend else "weekday"
    window = windows.get(key)
    if not window:
        return True

    start_h, start_m, end_h, end_m = window
    current_minutes = now_et.hour * 60 + now_et.minute
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m

    return start_minutes <= current_minutes <= end_minutes


def _is_no_bond(record_doc: Dict[str, Any]) -> bool:
    """Return True if this record is a no-bond / pending-first-appearance case."""
    bond_amount = float(record_doc.get("bond_amount", 0) or 0)
    bond_type   = str(record_doc.get("bond_type", "") or "").upper()
    lead_status = str(record_doc.get("lead_status", "") or "")

    if bond_amount > 0:
        return False  # Bond already set — not a candidate

    if any(nb in bond_type for nb in _NO_BOND_TYPES):
        return True

    # Also watch Disqualified / Cold records with zero bond even if bond_type is blank
    if lead_status in ("Disqualified", "Cold", "") and bond_amount == 0:
        return True

    return False


def _parse_date(date_str: str) -> Optional[datetime]:
    """Try to parse a date string in common formats. Returns UTC datetime or None."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(date_str.strip()[:10], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class FirstAppearanceWatcher:
    """
    Polls MongoDB for no-bond in-custody records and re-checks them
    against the live jail roster until bond is set or the watch window expires.
    """

    def __init__(
        self,
        writers: list = None,
        scraper_registry: Dict[str, Any] = None,
    ):
        """
        Args:
            writers:          List of writer instances (MongoWriter, etc.)
            scraper_registry: Dict mapping county name → BaseScraper instance.
                              Used to call the county-specific detail fetcher.
        """
        self._writers      = writers or []
        self._scrapers     = scraper_registry or {}
        self._scorer       = LeadScorer()
        self._slack        = None
        self._mongo_client = None
        self._db           = None
        self._arrests      = None
        self._connect()

    # ── MongoDB ───────────────────────────────────────────────────────────────

    def _connect(self):
        if not settings.mongo_configured():
            logger.warning("FirstAppearanceWatcher: MongoDB not configured — watcher disabled")
            return
        try:
            self._mongo_client = MongoClient(settings.MONGODB_URI)
            self._db           = self._mongo_client[settings.MONGODB_DB_NAME]
            self._arrests      = self._db["arrests"]
            logger.info("✅ FirstAppearanceWatcher connected to MongoDB")
        except Exception as e:
            logger.error(f"FirstAppearanceWatcher: MongoDB connection failed: {e}")

    def _get_slack(self):
        """Lazy-load SlackNotifier to avoid circular imports."""
        if self._slack is None:
            try:
                from writers.slack_notifier import SlackNotifier
                self._slack = SlackNotifier()
            except Exception:
                pass
        return self._slack

    # ── Candidate Query ───────────────────────────────────────────────────────

    def _query_candidates(self) -> List[Dict[str, Any]]:
        """
        Query MongoDB for in-custody, no-bond records created within
        the watch window.
        """
        if self._arrests is None:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=WATCH_WINDOW_DAYS)

        query = {
            # Must still be in custody
            "status": {"$regex": "in.custody|incustody", "$options": "i"},
            # Bond must be zero (or missing)
            "$or": [
                {"bond_amount": {"$lte": 0}},
                {"bond_amount": None},
                {"bond_amount_raw": {"$regex": "no.bond|hold|none", "$options": "i"}},
            ],
            # Must have a detail URL to re-fetch
            "detail_url": {"$exists": True, "$ne": ""},
            # Must be within the watch window
            "created_at": {"$gte": cutoff},
            # Skip records already graduated to Hot/Warm with real bond
            "lead_status": {"$nin": []},  # We'll filter in Python for flexibility
        }

        try:
            docs = list(
                self._arrests.find(query)
                .sort("created_at", -1)
                .limit(WATCH_MAX_BATCH)
            )
            # Secondary filter: skip if lead_status is Hot or Warm (already resolved)
            candidates = [
                d for d in docs
                if d.get("lead_status", "") not in ("Hot", "Warm")
                or float(d.get("bond_amount", 0) or 0) == 0
            ]
            logger.info(
                f"🔍 FirstAppearanceWatcher: {len(candidates)} candidates "
                f"(from {len(docs)} raw matches, window={WATCH_WINDOW_DAYS}d)"
            )
            return candidates
        except Exception as e:
            logger.error(f"FirstAppearanceWatcher: query failed: {e}")
            return []

    # ── Detail Re-fetch ───────────────────────────────────────────────────────

    def _refetch_record(self, doc: Dict[str, Any]) -> Optional[ArrestRecord]:
        """
        Re-fetch a single record from the live jail roster.

        Strategy:
        1. If the county has a registered scraper with a _fetch_single_booking()
           method, use it (county-specific, most accurate).
        2. Otherwise, use a generic HTTP GET on the detail_url and parse
           the bond amount from the page HTML.

        Returns an updated ArrestRecord or None on failure.
        """
        county     = doc.get("county", "")
        detail_url = doc.get("detail_url", "")
        booking_id = doc.get("booking_number", "")

        if not detail_url:
            return None

        # ── Strategy 1: County scraper with _fetch_single_booking ────────────
        scraper = self._scrapers.get(county)
        if scraper and hasattr(scraper, "_fetch_single_booking"):
            try:
                record = scraper._fetch_single_booking(booking_id, detail_url)
                if record:
                    record.LastCheckedMode = "UPDATE"
                    record.LastChecked = datetime.now(timezone.utc).isoformat()
                    return record
            except Exception as e:
                logger.warning(
                    f"FirstAppearanceWatcher: county scraper re-fetch failed "
                    f"({county}/{booking_id}): {e}"
                )

        # ── Strategy 2: Generic HTTP bond-amount extraction ──────────────────
        return self._generic_refetch(doc, detail_url)

    def _generic_refetch(
        self, doc: Dict[str, Any], detail_url: str
    ) -> Optional[ArrestRecord]:
        """
        Generic fallback: HTTP GET the detail page and parse bond amount
        from common HTML patterns.
        """
        try:
            import requests
            resp = requests.get(
                detail_url,
                timeout=15,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                },
            )
            if resp.status_code != 200:
                logger.debug(
                    f"FirstAppearanceWatcher: HTTP {resp.status_code} for {detail_url}"
                )
                return None

            html = resp.text

            # Try to extract bond amount from common patterns:
            #   "Bond Amt. 10000.00"  /  "Bond Amount: $10,000"  /  "10000.00"
            bond_val = _extract_bond_from_html(html)

            # Reconstruct an ArrestRecord from the stored doc, updating bond fields
            record = ArrestRecord.from_mongo_doc(doc)
            record.Bond_Amount     = str(bond_val) if bond_val > 0 else record.Bond_Amount
            record.LastCheckedMode = "UPDATE"
            record.LastChecked     = datetime.now(timezone.utc).isoformat()
            return record

        except Exception as e:
            logger.warning(
                f"FirstAppearanceWatcher: generic re-fetch failed "
                f"({doc.get('county')}/{doc.get('booking_number')}): {e}"
            )
            return None

    # ── Core Run Loop ─────────────────────────────────────────────────────────

    def run(self) -> Dict[str, Any]:
        """
        Execute one watcher cycle:
        1. Query candidates
        2. Re-fetch each
        3. Detect bond upgrades
        4. Upsert changed records
        5. Alert on bond-set events

        Returns a summary dict for logging.
        """
        if self._arrests is None:
            return {"status": "skipped", "reason": "no_mongo"}

        now     = datetime.now(timezone.utc)
        stats   = {
            "candidates":    0,
            "rechecked":     0,
            "bond_set":      0,
            "bond_changed":  0,
            "no_change":     0,
            "errors":        0,
            "run_at":        now.isoformat(),
        }

        candidates = self._query_candidates()
        stats["candidates"] = len(candidates)

        if not candidates:
            logger.info("FirstAppearanceWatcher: no candidates — skipping cycle")
            return stats

        operations = []
        bond_set_records: List[ArrestRecord] = []

        # Track consecutive failures per county to avoid hammering rate-limited APIs.
        # After 3 consecutive failures for a county, skip remaining records for that
        # county in this cycle. This prevents the watcher from burning API quota on
        # counties that are currently 429'd (e.g., Lee County Sheriff API).
        county_fail_count: Dict[str, int] = {}
        county_skip_count: Dict[str, int] = {}

        for doc in candidates:
            county     = doc.get("county", "?")
            booking_id = doc.get("booking_number", "?")
            old_bond   = float(doc.get("bond_amount", 0) or 0)

            # ── Court window guard: skip counties outside their hearing window ──
            if not _is_in_court_window(county):
                county_skip_count[county] = county_skip_count.get(county, 0) + 1
                stats["no_change"] += 1
                continue

            # ── Rate-limit guard: skip counties with 3+ consecutive failures ──
            if county_fail_count.get(county, 0) >= 3:
                county_skip_count[county] = county_skip_count.get(county, 0) + 1
                # Still update last_checked so we don't re-query this record next cycle
                operations.append(UpdateOne(
                    {"county": doc["county"], "booking_number": doc["booking_number"]},
                    {"$set": {
                        "last_checked": now.isoformat(),
                        "last_checked_mode": "UPDATE_SKIPPED_RATELIMIT",
                        "updated_at": now,
                    }},
                ))
                stats["no_change"] += 1
                continue

            try:
                updated = self._refetch_record(doc)
                if updated is None:
                    stats["errors"] += 1
                    county_fail_count[county] = county_fail_count.get(county, 0) + 1
                    if county_fail_count[county] == 3:
                        logger.warning(
                            f"⚡ FirstAppearanceWatcher: {county} hit 3 consecutive failures — "
                            f"skipping remaining {county} records this cycle (rate-limited?)"
                        )
                    # Still update last_checked so we don't hammer failed URLs
                    operations.append(UpdateOne(
                        {"county": doc["county"], "booking_number": doc["booking_number"]},
                        {"$set": {
                            "last_checked": now.isoformat(),
                            "last_checked_mode": "UPDATE",
                            "updated_at": now,
                        }},
                    ))
                    continue

                # Reset failure count on success
                county_fail_count[county] = 0
                stats["rechecked"] += 1
                new_bond = updated._parse_bond_numeric()

                # ── Detect bond upgrade ──────────────────────────────────────
                if new_bond > 0 and old_bond == 0:
                    # 🎉 Bond was set at first appearance!
                    self._scorer.score_and_update(updated)
                    mongo_doc = updated.to_mongo_doc()
                    mongo_doc["updated_at"]  = now
                    mongo_doc["bond_set_at"] = now  # Permanent timestamp
                    operations.append(UpdateOne(
                        {"county": updated.County, "booking_number": updated.Booking_Number},
                        {"$set": mongo_doc},
                        upsert=True,
                    ))
                    bond_set_records.append(updated)
                    stats["bond_set"] += 1
                    logger.info(
                        f"🔔 BOND SET: {updated.Full_Name} ({county}/{booking_id}) "
                        f"${new_bond:,.0f} — score={updated.Lead_Score} "
                        f"status={updated.Lead_Status}"
                    )

                elif new_bond != old_bond and new_bond > 0:
                    # Bond amount changed (e.g., reduced at hearing)
                    self._scorer.score_and_update(updated)
                    mongo_doc = updated.to_mongo_doc()
                    mongo_doc["updated_at"] = now
                    operations.append(UpdateOne(
                        {"county": updated.County, "booking_number": updated.Booking_Number},
                        {"$set": mongo_doc},
                        upsert=True,
                    ))
                    stats["bond_changed"] += 1
                    logger.info(
                        f"💰 BOND CHANGED: {updated.Full_Name} ({county}/{booking_id}) "
                        f"${old_bond:,.0f} → ${new_bond:,.0f}"
                    )

                else:
                    # No change — just update the check timestamp
                    operations.append(UpdateOne(
                        {"county": doc["county"], "booking_number": doc["booking_number"]},
                        {"$set": {
                            "last_checked": now.isoformat(),
                            "last_checked_mode": "UPDATE",
                            "updated_at": now,
                        }},
                    ))
                    stats["no_change"] += 1

            except Exception as e:
                logger.warning(
                    f"FirstAppearanceWatcher: error processing "
                    f"{county}/{booking_id}: {e}"
                )
                stats["errors"] += 1
                county_fail_count[county] = county_fail_count.get(county, 0) + 1

        # ── Bulk write to MongoDB ─────────────────────────────────────────────
        if operations:
            try:
                result = self._arrests.bulk_write(operations, ordered=False)
                logger.info(
                    f"FirstAppearanceWatcher: bulk_write complete — "
                    f"modified={result.modified_count} upserted={result.upserted_count}"
                )
            except Exception as e:
                logger.error(f"FirstAppearanceWatcher: bulk_write failed: {e}")

        # ── Slack alerts for bond-set events ─────────────────────────────────
        slack = self._get_slack()
        if slack and bond_set_records:
            for record in bond_set_records:
                try:
                    slack.notify_bond_set(record)
                except Exception as e:
                    logger.warning(f"FirstAppearanceWatcher: Slack alert failed: {e}")

        # Log skipped counties
        if county_skip_count:
            skip_summary = ", ".join(f"{c}: {n} skipped" for c, n in county_skip_count.items())
            logger.info(f"⚡ FirstAppearanceWatcher rate-limit skips: {skip_summary}")

        logger.info(
            f"✅ FirstAppearanceWatcher cycle complete: "
            f"candidates={stats['candidates']} "
            f"rechecked={stats['rechecked']} "
            f"bond_set={stats['bond_set']} "
            f"bond_changed={stats['bond_changed']} "
            f"no_change={stats['no_change']} "
            f"errors={stats['errors']}"
        )
        return stats

    def close(self):
        """Close MongoDB connection."""
        if self._mongo_client:
            try:
                self._mongo_client.close()
            except Exception:
                pass


# ── HTML Bond Extraction Helper ───────────────────────────────────────────────

# Ordered list of regex patterns to extract bond amounts from HTML.
# Tries most-specific first (labeled fields) then falls back to bare numbers.
_BOND_PATTERNS = [
    # "Bond Amt. 10000.00" or "Bond Amount: $10,000.00"
    re.compile(
        r"bond\s*(?:amt|amount)[^$\d]{0,20}[\$]?\s*([\d,]+(?:\.\d{1,2})?)",
        re.IGNORECASE,
    ),
    # "10000.00" in table cell next to "Bond" / "Amt" / "Amount" label
    re.compile(
        r"(?:bond|amt|amount)[^<]{0,50}<\/td>\s*<td[^>]*>\s*[\$]?\s*([\d,]+(?:\.\d{1,2})?)",
        re.IGNORECASE,
    ),
]


def _extract_bond_from_html(html: str) -> float:
    """
    Attempt to extract a bond amount from raw HTML.
    Returns 0.0 if no amount found or amount is clearly invalid.
    """
    for pattern in _BOND_PATTERNS:
        matches = pattern.findall(html)
        for raw in matches:
            try:
                val = float(raw.replace(",", ""))
                # Sanity check: bond amounts are typically $100–$10M
                if 100 <= val <= 10_000_000:
                    return val
            except (ValueError, TypeError):
                continue
    return 0.0
