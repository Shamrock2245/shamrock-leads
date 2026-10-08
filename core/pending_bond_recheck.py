"""Pending-bond re-check worker.

Brendan (2026-10-08): a $0.00, "not set" or "no bond" bond usually means the
bond is still pending (unless the person is being sentenced). Re-check those
bookings daily on days 1-3 after booking, then weekly while the person is in
custody, until a real bond is published, the person is released, or a
sentencing status shows up.

How it works
------------
* Targets are stored ``arrests`` docs whose bond is blank, "0" (unless the
  parser marked that 0 as a real published value via ``extra.bond_published``)
  or a no-bond / not-set text, whose status is in custody, that carry a source
  booking key, and that have no staff bond edit.
* Only counties whose scraper is live are eligible: registered, not
  ``fail_closed`` in Health, ``SOURCE_CONTRACT_VALIDATED``, not auto-disabled,
  not relay-only (unless this runs on the relay with ``include_relay_only``),
  and with an opted-in ``fetch_bond_recheck(booking_id, detail_url)`` method.
  That method re-reads one booking through the county's existing detail path
  (no new endpoints) with plain requests. Scrapers whose single-booking path
  uses stealth, TLS impersonation or a proxy (Lee, Collier today) are not
  opted in.
* The refreshed record keeps every stored non-bond field the source did not
  return, and is written only through ``MongoWriter.write_records``, so the
  charges/bond pair rule and the staff-edit protections apply. A fetch that
  returns nothing (not found, failed, drifted page) writes nothing and never
  counts as released.
* Per-booking state (``last_checked_at``, ``check_count``, ``stop_reason``)
  lives in its own ``bond_rechecks`` collection, so arrests docs and staff
  edits are never touched by the bookkeeping.
* Each run is capped globally (``PENDING_BOND_RECHECK_MAX_PER_RUN``) and per
  county (the smaller of ``PENDING_BOND_RECHECK_PER_COUNTY`` and the county
  module's ``MAX_DETAILS_PER_RUN`` / ``MAX_DETAILS``). Due bookings over a cap
  are skipped and stay due for the next run. Pacing uses the county module's
  ``REQUEST_PAUSE_S`` or the worker default, whichever is slower.
* Logs carry county labels, booking keys, field names and counts only, never
  person data.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from dataclasses import fields as dc_fields
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from core.models import ArrestRecord
from core.staff_edits import MANUAL_CHARGE_MODE, PROVENANCE_PROJECTION, staff_provenance

logger = logging.getLogger(__name__)

STATE_COLLECTION = "bond_rechecks"
RECHECK_MODE = "BOND_RECHECK"
_ET = ZoneInfo("America/New_York")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def recheck_enabled() -> bool:
    """Kill switch: PENDING_BOND_RECHECK_ENABLED=0 turns the job off."""
    return os.getenv("PENDING_BOND_RECHECK_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


INTERVAL_HOURS = _env_int("PENDING_BOND_RECHECK_INTERVAL_HOURS", 6)
MAX_PER_RUN = _env_int("PENDING_BOND_RECHECK_MAX_PER_RUN", 60)
PER_COUNTY_CAP = _env_int("PENDING_BOND_RECHECK_PER_COUNTY", 25)
DEFAULT_PAUSE_S = _env_float("PENDING_BOND_RECHECK_PAUSE_S", 1.0)
SCAN_LIMIT_PER_COUNTY = _env_int("PENDING_BOND_RECHECK_SCAN_LIMIT", 2000)

DAILY_DAYS = 3                          # days 1..3 after booking: daily
DAILY_MIN_GAP = timedelta(hours=20)     # "daily" with slack for a 6h job
WEEKLY_MIN_GAP = timedelta(days=7)      # after day 3: weekly
MAX_NOT_FOUND_STREAK = 3                # source stopped listing it: stop asking

# Stop reasons
STOP_BOND_PUBLISHED = "bond_published"
STOP_BOND_FINAL_ZERO = "bond_final_zero"
STOP_RELEASED = "released"
STOP_SENTENCED = "sentenced"
STOP_NOT_FOUND = "not_found_on_source"

_MONEY_RE = re.compile(r"^\$?\s*([0-9][0-9,]*(?:\.\d+)?)$")
_SENTENCED_RE = re.compile(r"\bsentenc", re.I)
_RELEASED_RE = re.compile(r"\breleas|\bdischarg|\bbonded out\b|\bout of custody\b|\bnot in custody\b", re.I)
_CUSTODY_RE = re.compile(r"\bin custody\b|\bincarcerated\b|\bconfined\b|\bin jail\b|\bbooked\b", re.I)
_PENDING_TYPE_RE = re.compile(r"no\s*bond|not\s*set|pending|tbd|to be (?:set|determined)", re.I)

# Fields the source owns as a pair; mongo_writer's pair rule decides them.
_PAIR_FIELDS = {"Charges", "Bond_Amount", "Bond_Type"}
# Fields the worker sets itself.
_OWN_FIELDS = {"County", "State", "Booking_Number", "LastChecked", "LastCheckedMode", "Scrape_Timestamp"}


# ── Pure helpers ────────────────────────────────────────────────────────────

def parse_bond(value: Any) -> Optional[float]:
    """Numeric bond for a published amount, else None (blank or text)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _MONEY_RE.match(str(value).strip())
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _zero_marked_final(extra: Any) -> bool:
    return isinstance(extra, dict) and extra.get("bond_published") is True


def is_sentenced(status: Any, bond_type: Any = "", charges: Any = "") -> bool:
    return any(_SENTENCED_RE.search(str(v or "")) for v in (status, bond_type, charges))


def is_released(status: Any, release_date: Any = "") -> bool:
    s = str(status or "")
    if _RELEASED_RE.search(s):
        return True
    return bool(str(release_date or "").strip()) and not _CUSTODY_RE.search(s)


def is_in_custody(status: Any) -> bool:
    s = str(status or "")
    return bool(_CUSTODY_RE.search(s)) and not _RELEASED_RE.search(s) and not _SENTENCED_RE.search(s)


def is_pending_bond(doc: Dict[str, Any]) -> bool:
    """Stored bond is blank, a not-final 0, or a no-bond / not-set text."""
    raw = doc.get("bond_amount_raw")
    if raw is None:
        raw = doc.get("bond_amount")
    amount = parse_bond(raw)
    if amount is None:
        # Blank, a no-bond / not-set text, or any other unparseable text
        # (e.g. "HOLD"): no amount has been published, so it is pending.
        return True
    if amount > 0:
        return False
    # Zero: pending unless the parser marked it as a real published 0.
    return not _zero_marked_final(doc.get("extra"))


def booking_day(doc: Dict[str, Any], today: date) -> Optional[int]:
    """Days since booking (0 = booked today, ET). None if no usable date."""
    d = _parse_booking_date(doc.get("booking_date"))
    if d is None:
        created = doc.get("created_at") or doc.get("first_seen_at")
        if isinstance(created, datetime):
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            d = created.astimezone(_ET).date()
    if d is None:
        return None
    return (today - d).days


def _parse_booking_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s.split()[0], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _aware(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_due(day: Optional[int], last_checked: Optional[datetime], now: datetime) -> bool:
    """Cadence: daily on days 1-3 after booking, weekly after that.

    Day 0 (booked today) is left to the regular scrape and the first-appearance
    watcher. A booking without a usable date is never due (no guessing)."""
    if day is None or day < 1:
        return False
    last = _aware(last_checked)
    if last is None:
        return True
    gap = now - last
    return gap >= (DAILY_MIN_GAP if day <= DAILY_DAYS else WEEKLY_MIN_GAP)


def stop_reason_for(record: ArrestRecord) -> Optional[str]:
    """Why re-checking this booking should stop, from a freshly fetched record."""
    if is_sentenced(record.Status, record.Bond_Type, record.Charges):
        return STOP_SENTENCED
    if is_released(record.Status, record.Release_Date):
        return STOP_RELEASED
    amount = parse_bond(record.Bond_Amount)
    if amount is not None and amount > 0:
        return STOP_BOND_PUBLISHED
    if amount == 0 and _zero_marked_final(record.extra_data):
        return STOP_BOND_FINAL_ZERO
    return None  # still pending (a 0 the parser can't vouch for stays pending)


def merge_with_stored(fetched: ArrestRecord, stored_doc: Dict[str, Any], now: datetime) -> ArrestRecord:
    """Fill fields the refresh did not return from the stored doc.

    ``mongo_writer`` ``$set``s the whole doc, so a blank name/date/etc. in a
    detail-only refresh would overwrite stored values. Charges and bond are left
    as fetched: the writer's pair rule decides them. Identity comes from the
    stored doc so the upsert hits the same (state, county, booking_number)."""
    stored = ArrestRecord.from_mongo_doc(stored_doc)
    for f in dc_fields(ArrestRecord):
        name = f.name
        if name in _PAIR_FIELDS or name in _OWN_FIELDS or name == "extra_data":
            continue
        val = getattr(fetched, name)
        blank = val is None or (isinstance(val, str) and not val.strip())
        if name == "Lead_Score":
            blank = not val
        if name == "Bond_Paid":
            blank = blank or val == "NO"  # model default, not a source value
        if blank:
            setattr(fetched, name, getattr(stored, name))
    fetched.County = stored_doc.get("county") or fetched.County
    fetched.State = stored_doc.get("state") or fetched.State
    fetched.Booking_Number = stored_doc.get("booking_number") or fetched.Booking_Number
    merged_extra = dict(stored_doc.get("extra") or {}) if isinstance(stored_doc.get("extra"), dict) else {}
    merged_extra.update(fetched.extra_data or {})
    fetched.extra_data = merged_extra
    fetched.LastChecked = now.isoformat()
    stored_mode = str(stored_doc.get("last_checked_mode") or "")
    fetched.LastCheckedMode = stored_mode if stored_mode == MANUAL_CHARGE_MODE else RECHECK_MODE
    return fetched


def changed_fields(stored_doc: Dict[str, Any], record: ArrestRecord) -> List[str]:
    new = record.to_mongo_doc()
    watch = ("status", "release_date", "bond_amount_raw", "bond_type", "charges", "case_number")
    return [k for k in watch if str(stored_doc.get(k) or "") != str(new.get(k) or "")]


# ── Eligibility ─────────────────────────────────────────────────────────────

def _label(scraper: Any) -> str:
    from config.relay_only import label_for

    return label_for(scraper)


def _source_state(label: str) -> str:
    try:
        from dashboard.extensions import scraper_source_state

        return scraper_source_state(label)
    except Exception:
        return "unverified"


def county_exclusion(scraper: Any, *, include_relay_only: bool, writer: Any = None) -> Optional[str]:
    """None when the county may be re-checked, else a short reason."""
    from config.relay_only import is_relay_only

    if not callable(getattr(scraper, "fetch_bond_recheck", None)):
        return "no_recheck_path"
    if _source_state(_label(scraper)) == "fail_closed":
        return "fail_closed"
    if not bool(getattr(scraper, "SOURCE_CONTRACT_VALIDATED", True)):
        return "source_contract_unvalidated"
    if is_relay_only(scraper) and not include_relay_only:
        return "relay_only"
    loader = getattr(scraper, "_load_resilience_state", None)
    if callable(loader) and writer is not None:
        try:
            if getattr(loader(writer), "auto_disabled", False):
                return "auto_disabled"
        except Exception:
            pass
    return None


def _module_attr(scraper: Any, *names: str) -> Optional[float]:
    mod = sys.modules.get(type(scraper).__module__)
    for n in names:
        v = getattr(scraper, n, None)
        if v is None and mod is not None:
            v = getattr(mod, n, None)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
    return None


def county_cap(scraper: Any, default: int) -> int:
    mod_cap = _module_attr(scraper, "MAX_DETAILS_PER_RUN", "MAX_DETAILS")
    return max(0, min(default, int(mod_cap))) if mod_cap is not None else max(0, default)


def county_pause(scraper: Any, default: float) -> float:
    mod_pause = _module_attr(scraper, "REQUEST_PAUSE_S", "REQUEST_DELAY_S")
    return max(default, float(mod_pause or 0.0))


def candidate_query(state: str, county: str) -> Dict[str, Any]:
    """Coarse Mongo filter; ``is_target`` makes the final call in Python."""
    return {
        "state": state,
        "county": county,
        "status": {"$regex": "custody|incarcerated|confined|in jail|booked", "$options": "i"},
        "booking_number": {"$nin": ["", None]},
        "$or": [
            {"bond_amount_raw": {"$in": ["", None, "0", "0.0", "0.00", "$0", "$0.00"]}},
            {"bond_amount_raw": {"$exists": False}},
            {"bond_amount": {"$in": [0, None]}},
            {"bond_type": {"$regex": _PENDING_TYPE_RE.pattern, "$options": "i"}},
        ],
    }


CANDIDATE_PROJECTION = {
    **{k: v for k, v in PROVENANCE_PROJECTION.items()},
    "status": 1, "release_date": 1, "booking_date": 1, "created_at": 1,
    "bond_amount_raw": 1, "extra.bond_published": 1, "detail_url": 1,
}


def is_target(doc: Dict[str, Any]) -> Optional[str]:
    """None if the stored doc is a re-check target, else a skip reason."""
    bk = str(doc.get("booking_number") or "").strip()
    if not bk:
        return "no_booking_key"
    if is_sentenced(doc.get("status"), doc.get("bond_type"), doc.get("charges")):
        return "sentenced"
    if not is_in_custody(doc.get("status")):
        return "not_in_custody"
    if not is_pending_bond(doc):
        return "bond_known"
    if staff_provenance(doc).bond:
        return "staff_bond"
    return None


# ── Worker ──────────────────────────────────────────────────────────────────

class PendingBondRecheck:
    def __init__(
        self,
        scrapers: Iterable[Any],
        writers: List[Any],
        *,
        include_relay_only: bool = False,
        max_per_run: Optional[int] = None,
        per_county_cap: Optional[int] = None,
        default_pause_s: Optional[float] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        scorer: Any = None,
    ):
        self.scrapers = list(scrapers)
        self.mongo_writers = [w for w in writers if hasattr(w, "write_records") and hasattr(w, "arrests")]
        self.include_relay_only = include_relay_only
        self.max_per_run = MAX_PER_RUN if max_per_run is None else max_per_run
        self.per_county_cap = PER_COUNTY_CAP if per_county_cap is None else per_county_cap
        self.default_pause_s = DEFAULT_PAUSE_S if default_pause_s is None else default_pause_s
        self._sleep = sleep
        self._clock = clock
        self._scorer = scorer

    # Mongo handles come from the MongoWriter: no separate connection.
    @property
    def _writer(self):
        return self.mongo_writers[0] if self.mongo_writers else None

    def _state_coll(self):
        return self._writer.db[STATE_COLLECTION]

    def _load_states(self, state: str, county: str, bookings: List[str]) -> Dict[str, Dict[str, Any]]:
        if not bookings:
            return {}
        out = {}
        for d in self._state_coll().find(
            {"state": state, "county": county, "booking_number": {"$in": bookings}},
            {"_id": 0},
        ):
            out[d.get("booking_number")] = d
        return out

    def _save_state(self, state: str, county: str, booking: str, now: datetime, outcome: str,
                    stop: Optional[str], *, found: bool, fields_changed: List[str]) -> None:
        set_doc: Dict[str, Any] = {
            "last_checked_at": now,
            "last_outcome": outcome,
            "updated_at": now,
        }
        if fields_changed:
            set_doc["last_changed_fields"] = fields_changed
        if stop:
            set_doc["stop_reason"] = stop
            set_doc["stopped_at"] = now
        update: Dict[str, Any] = {
            "$set": set_doc,
            "$inc": {"check_count": 1},
            "$setOnInsert": {"state": state, "county": county, "booking_number": booking, "first_checked_at": now},
        }
        if found:
            set_doc["not_found_streak"] = 0
        else:
            update["$inc"]["not_found_streak"] = 1
        self._state_coll().update_one(
            {"state": state, "county": county, "booking_number": booking}, update, upsert=True
        )

    def _score(self, record: ArrestRecord) -> None:
        try:
            if self._scorer is None:
                from scoring.lead_scorer import LeadScorer

                self._scorer = LeadScorer()
            self._scorer.score_and_update(record)
        except Exception as exc:  # keep the stored score
            logger.warning("bond-recheck: scoring skipped (%s)", type(exc).__name__)

    def select_due(self, scraper: Any, now: datetime) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """Due target docs for one county, oldest-checked first, plus skip counts."""
        state = (getattr(scraper, "state", None) or "FL").upper()
        county = scraper.county
        skips: Dict[str, int] = {}
        today = now.astimezone(_ET).date()
        cursor = self._writer.arrests.find(candidate_query(state, county), CANDIDATE_PROJECTION)
        if hasattr(cursor, "limit"):
            cursor = cursor.limit(SCAN_LIMIT_PER_COUNTY)
        docs = list(cursor)[:SCAN_LIMIT_PER_COUNTY]
        targets = []
        for d in docs:
            reason = is_target(d)
            if reason:
                skips[reason] = skips.get(reason, 0) + 1
                continue
            targets.append(d)
        states = self._load_states(state, county, [str(d["booking_number"]) for d in targets])
        due = []
        for d in targets:
            st = states.get(str(d["booking_number"])) or {}
            if st.get("stop_reason"):
                skips["stopped"] = skips.get("stopped", 0) + 1
                continue
            day = booking_day(d, today)
            if day is None:
                skips["no_booking_date"] = skips.get("no_booking_date", 0) + 1
                continue
            if not is_due(day, st.get("last_checked_at"), now):
                skips["not_due"] = skips.get("not_due", 0) + 1
                continue
            d["_day"] = day
            d["_last"] = _aware(st.get("last_checked_at"))
            due.append(d)
        # Never-checked first, then longest since the last check; newer bookings break ties.
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        due.sort(key=lambda d: (d["_last"] is not None, d["_last"] or epoch, d["_day"]))
        return due, skips

    def run(self) -> Dict[str, Any]:
        stats: Dict[str, Any] = {
            "checked": 0, "written": 0, "not_found": 0, "stopped": {}, "skipped_over_cap": 0,
            "counties": {}, "excluded": {},
        }
        if not recheck_enabled():
            logger.info("bond-recheck: disabled (PENDING_BOND_RECHECK_ENABLED=0)")
            stats["disabled"] = True
            return stats
        if self._writer is None:
            logger.warning("bond-recheck: no MongoWriter configured; nothing to do")
            return stats
        now = self._clock()
        budget = max(0, self.max_per_run)
        for scraper in self.scrapers:
            label = _label(scraper)
            reason = county_exclusion(scraper, include_relay_only=self.include_relay_only, writer=self._writer)
            if reason:
                stats["excluded"][label] = reason
                continue
            try:
                due, skips = self.select_due(scraper, now)
            except Exception as exc:
                logger.warning("bond-recheck: %s target read failed (%s); county skipped", label, type(exc).__name__)
                stats["counties"][label] = {"error": type(exc).__name__}
                continue
            cap = min(county_cap(scraper, self.per_county_cap), budget)
            batch, over = due[:cap], len(due) - min(cap, len(due))
            stats["skipped_over_cap"] += over
            if over:
                logger.info("bond-recheck: %s %d due over the cap, left for a later run", label, over)
            pause = county_pause(scraper, self.default_pause_s)
            c_stats = {"due": len(due), "checked": 0, "written": 0, "not_found": 0, "skips": skips, "over_cap": over}
            for i, doc in enumerate(batch):
                if i:
                    self._sleep(pause)
                self._recheck_one(scraper, label, doc, now, stats, c_stats)
            budget -= len(batch)
            stats["counties"][label] = c_stats
        logger.info(
            "bond-recheck: checked=%d written=%d not_found=%d stopped=%s over_cap=%d excluded=%d",
            stats["checked"], stats["written"], stats["not_found"], stats["stopped"],
            stats["skipped_over_cap"], len(stats["excluded"]),
        )
        return stats

    def _recheck_one(self, scraper, label, doc, now, stats, c_stats) -> None:
        state, county, bk = doc["state"], doc["county"], str(doc["booking_number"])
        stats["checked"] += 1
        c_stats["checked"] += 1
        try:
            fetched = scraper.fetch_bond_recheck(bk, doc.get("detail_url") or "")
        except Exception as exc:
            logger.warning("bond-recheck: %s %s fetch error (%s); nothing written", label, bk, type(exc).__name__)
            fetched = None
        if fetched is None or str(fetched.Booking_Number or "").strip() != bk:
            stats["not_found"] += 1
            c_stats["not_found"] += 1
            prev = 0
            try:
                prev = int((self._load_states(state, county, [bk]).get(bk) or {}).get("not_found_streak") or 0)
            except Exception:
                pass
            stop = STOP_NOT_FOUND if prev + 1 >= MAX_NOT_FOUND_STREAK else None
            logger.info("bond-recheck: %s %s not found on source; nothing written%s", label, bk,
                        f" (stop={stop})" if stop else "")
            self._save_state(state, county, bk, now, "not_found", stop, found=False, fields_changed=[])
            if stop:
                stats["stopped"][stop] = stats["stopped"].get(stop, 0) + 1
            return
        full = self._writer.arrests.find_one({"state": state, "county": county, "booking_number": bk})
        if not full:
            logger.info("bond-recheck: %s %s stored doc vanished; nothing written", label, bk)
            return
        record = merge_with_stored(fetched, full, now)
        self._score(record)
        changed = changed_fields(full, record)
        stop = stop_reason_for(record)
        for w in self.mongo_writers:
            w.write_records([record], county)
        stats["written"] += 1
        c_stats["written"] += 1
        if stop:
            stats["stopped"][stop] = stats["stopped"].get(stop, 0) + 1
        logger.info(
            "bond-recheck: %s %s refreshed; changed=%s stop=%s",
            label, bk, ",".join(changed) or "-", stop or "-",
        )
        self._save_state(state, county, bk, now, "refreshed", stop, found=True, fields_changed=changed)


def run_pending_bond_recheck(scheduler: Any, *, include_relay_only: bool = False) -> Dict[str, Any]:
    """Scheduler entry point: every registered scraper, the scheduler's writers."""
    from dashboard.tenancy.context import bind_platform_job

    worker = PendingBondRecheck(
        list(getattr(scheduler, "_scrapers", {}).values()),
        list(getattr(scheduler, "_writers", []) or []),
        include_relay_only=include_relay_only,
    )
    with bind_platform_job(job_name="pending_bond_recheck"):
        return worker.run()
