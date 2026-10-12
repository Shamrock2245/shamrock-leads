"""
Pinellas County Arrest Scraper — Who's In Jail (Blazor Server).
Source: Pinellas County Sheriff's Office
URL: https://whosinjail.pinellassheriff.gov/
Method: stock Playwright Chromium on the Leads Ops home relay only —
        booking-date search + Next pagination.

RELAY-ONLY (owner exception, Brendan 2026-10-08 1:38 PM ET; CoS agreed):
"we will connect at home, with residential egress." Who's In Jail is a Blazor
Server app; a plain-requests read gets only the JS shell (no form, table or
booking numbers; docs/recon/FL_PINELLAS_RELAY_ONLY_2026-10-08.md), so the rows
need a browser. The exception allows a NON-stealth browser on the residential
relay only:
- stock Playwright Chromium (bundled, not ``channel="chrome"``), headless,
  ``--no-proxy-server``, proxy env vars stripped, and an honest User-Agent that
  names the bot (``USER_AGENT``); no patchright, stealth plugins, proxy,
  impersonation or challenge solving;
- ``config/relay_only.py`` lists Pinellas, so the VPS/Hetzner scheduler never
  gives it an interval job and a dashboard trigger is not run there;
- ``scrape()`` first verifies this host's own exit is US residential
  (``PINELLAS_EGRESS_MODE=direct``, the only value) and raises
  ``EgressBlocked`` before any browser start or source request otherwise.
Health stays ``unverified`` until a Leads Ops write smoke through the relay.

HISTORY:
- v1: ASP.NET InmateBooking at pinellassheriff.gov/InmateBooking/ (ViewState POST)
- v2: Old app pool returns HTTP 503. Site now points to Who's In Jail
  Blazor Server (SignalR; no public REST). Patchright Chrome booking-date search.
- v3 (2026-10-08): patchright removed; stock Playwright Chromium,
  honest User-Agent, relay-only with direct residential egress.
- v3.1 (2026-10-09): relay smoke 0/10 modals. The row-click script had a raw
  newline inside a JS regex literal (``split(/\n/)`` in a non-raw Python
  string), so every ``page.evaluate`` threw a SyntaxError that was logged only
  at DEBUG. Fixed; modal open now uses a trusted click, waits on the modal's
  own content (``PINELLAS_MODAL_TIMEOUT_MS``, default 20000, cap 45000),
  retries once, closes before the next booking, and logs a names-free reason
  per failure. ``PINELLAS_MODAL_DEBUG=1`` writes per-modal JSONL to ``logs/``.

Public roster covers current inmates + releases within ~30 days.

Bond / charges for hydrate:
- Roster rows include abbreviated charge text under the name when
  "Include Charge Information" is checked.
- Per-charge **Bond Assessed** and full **Offense Description** live only in
  the Subject Charge Report modal (name click). The total is the sum of
  Bond Assessed only when every charge publishes an amount; the jail does
  publish real $0.00 values (33 of 82 cells on 2026-10-06/07), which are kept
  as "0". A charge with a blank or non-numeric Bond Assessed (it can be a
  hold), or a booking whose modal did not render, leaves the total "" —
  unknown, never $0.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord
from scrapers.revize_roster import (
    egress_mode as _egress_mode,
    launch_plain_browser,
    resolve_egress as _resolve_egress,
)

logger = logging.getLogger(__name__)

SEARCH_URL = "https://whosinjail.pinellassheriff.gov/"
DAYS_BACK = 3  # Relay runs on a launchd/cron cadence; 3 days covers plenty of ground
MAX_PAGES_PER_DAY = 40
FACILITY = "Pinellas County Jail"
EGRESS_ENV = "PINELLAS_EGRESS_MODE"
# Honest User-Agent: names the bot and its operator; no Chrome spoofing.
USER_AGENT = (
    "ShamrockLeadsBot/1.0 (+https://shamrockbailbonds.biz; "
    "Pinellas public jail roster via residential relay; stock Playwright Chromium)"
)


# ── Subject Charge Report modal: waits, retry and diagnostics ───────────────
# Relay smoke 2026-10-09 (main 6c9303d): 10/10 modals "did not render" and the
# old code logged no cause. The modal is now opened with a trusted click on
# the row's own name link, waited on by its own content (visible container,
# charge fields present, text stable across two polls), retried once, and
# closed before the next one. Every failure logs a names-free reason.
MODAL_CONTAINER_SELECTOR = (
    ".modal, [role=dialog], dialog[open], .modal-dialog, .blazored-modal, .mud-dialog"
)
MODAL_TIMEOUT_ENV = "PINELLAS_MODAL_TIMEOUT_MS"
MODAL_TIMEOUT_MS_DEFAULT = 20000  # was a fixed 8s
MODAL_TIMEOUT_MS_MIN = 2000
MODAL_TIMEOUT_MS_CAP = 45000
MODAL_POLL_S = 0.25
MODAL_CLOSE_WAIT_S = 3.0
MODAL_RETRY_PAUSE_S = 1.0
MODAL_PAUSE_S = 0.5  # between bookings
MODAL_ABORT_AFTER = 5  # consecutive bookings failing twice → stop opening modals this run
MODAL_SHAPE_LOG_LIMIT = 3  # DOM-shape snippets in normal logs per run
MODAL_DEBUG_ENV = "PINELLAS_MODAL_DEBUG"
DEBUG_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"

REASON_OK = "ok"
REASON_CLICK_TARGET = "click_target_not_found"
REASON_SELECTOR = "selector_not_found"
REASON_EMPTY = "empty_content"
REASON_NO_FIELDS = "no_charge_fields_by_deadline"
REASON_CIRCUIT = "circuit_disconnected"
REASON_EXCEPTION = "exception"
REASON_ABORTED = "skipped_after_consecutive_failures"

_TARGET_ATTR = "data-shamrock-modal-target"

# Clock/sleep indirection so unit tests drive the wait loop with a fake clock.
_now = time.monotonic
_sleep = time.sleep


def modal_timeout_ms() -> int:
    """Per-attempt modal wait (``PINELLAS_MODAL_TIMEOUT_MS``), clamped to
    [2000, 45000]; default 20000."""
    try:
        value = int(os.getenv(MODAL_TIMEOUT_ENV, str(MODAL_TIMEOUT_MS_DEFAULT)))
    except (TypeError, ValueError):
        value = MODAL_TIMEOUT_MS_DEFAULT
    return max(MODAL_TIMEOUT_MS_MIN, min(MODAL_TIMEOUT_MS_CAP, value))


def modal_debug_enabled() -> bool:
    return (os.getenv(MODAL_DEBUG_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


_NAMEISH_RE = re.compile(r"\b[A-Z][A-Za-z'\-]+,\s*[A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+)*")


def _redact(text: str, names=()) -> str:
    """Remove roster names (exact and ``LAST, FIRST`` shapes) from diagnostics."""
    out = str(text or "")
    for name in sorted((n for n in names if n), key=len, reverse=True):
        out = re.sub(re.escape(name), "[name]", out, flags=re.I)
        for token in re.split(r"[\s,]+", name):
            if len(token) >= 3:
                out = re.sub(rf"\b{re.escape(token)}\b", "[name]", out, flags=re.I)
    return _NAMEISH_RE.sub("[name]", out)


def _strip_query(url: str) -> str:
    """Scheme/host/path only: SignalR puts connection tokens in the query."""
    return str(url or "").split("?", 1)[0].split("#", 1)[0][:200]


class ModalDebugRecorder:
    """``PINELLAS_MODAL_DEBUG=1`` only: per-modal JSONL under ``logs/`` on the
    relay. Records selectors, wait condition, timeouts, exception type/text,
    page console errors, page errors, failed requests and websocket
    (SignalR) open/close/error events, plus a names-free modal structure
    (tags/classes and field labels only; values become ``#text(len)``).
    Everything passes through :func:`_redact`."""

    def __init__(self, scraper: Any, log_dir: Path = DEBUG_LOG_DIR):
        self.scraper = scraper
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = log_dir / f"pinellas-modal-debug-{stamp}.jsonl"
        self._events: List[dict] = []

    def _names(self):
        return getattr(self.scraper, "_known_names", ()) or ()

    def _event(self, kind: str, **fields) -> None:
        fields = {k: (_redact(v, self._names())[:300] if isinstance(v, str) else v)
                  for k, v in fields.items()}
        self._events.append({"t": round(_now(), 3), "kind": kind, **fields})

    def attach(self, page) -> None:
        def on_console(msg):
            try:
                if msg.type in ("error", "warning"):
                    self._event("console_" + msg.type, text=msg.text)
            except Exception:  # noqa: BLE001
                pass

        def on_websocket(ws):
            url = _strip_query(getattr(ws, "url", ""))
            self._event("ws_open", url=url)
            try:
                ws.on("close", lambda *_: self._event("ws_close", url=url))
                ws.on("socketerror", lambda err=None: self._event("ws_error", url=url, error=str(err)))
            except Exception:  # noqa: BLE001
                pass

        page.on("console", on_console)
        page.on("pageerror", lambda err: self._event("pageerror", error=str(err)))
        page.on("websocket", on_websocket)
        page.on("requestfailed", lambda req: self._event(
            "request_failed", url=_strip_query(getattr(req, "url", "")),
            failure=str(getattr(req, "failure", "") or "")))
        self.write({"kind": "start", "selector": MODAL_CONTAINER_SELECTOR,
                    "timeout_ms": modal_timeout_ms(), "cap_ms": MODAL_TIMEOUT_MS_CAP})

    def write(self, record: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")

    def record_modal(self, booking_num: str, attempt: int, reason: str, diag: dict) -> None:
        events, self._events = self._events, []
        self.write({
            "kind": "modal", "booking_number": booking_num, "attempt": attempt,
            "reason": reason, **{k: v for k, v in diag.items()}, "events": events,
        })

    def finish(self, summary: dict) -> None:
        events, self._events = self._events, []
        self.write({"kind": "summary", **summary, "events": events})


# Mark the name link of the row whose last cell starts with the booking number.
_JS_MARK_ROW_LINK = """(bn) => {
  document.querySelectorAll('[%(attr)s]').forEach(e => e.removeAttribute('%(attr)s'));
  for (const r of document.querySelectorAll('table tbody tr')) {
    const tds = r.querySelectorAll('td');
    if (!tds.length) continue;
    const booking = ((tds[tds.length - 1].innerText || '').trim().split(/\\n/)[0] || '').trim();
    if (booking !== bn) continue;
    const a = r.querySelector('.td-name a');
    if (a) { a.setAttribute('%(attr)s', '1'); return true; }
  }
  return false;
}""" % {"attr": _TARGET_ATTR}

_JS_CLICK_MARKED = """() => {
  const a = document.querySelector('[%(attr)s]');
  if (!a) return false;
  a.click();
  return true;
}""" % {"attr": _TARGET_ATTR}

# Visible modal containers (Blazor's reconnect overlay excluded), their text
# size and whether the charge fields are present. Text is returned only when
# the charge fields are there (it is parsed, never logged).
_JS_PROBE_MODAL = """(sel) => {
  const visible = (el) => {
    const cs = window.getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const rc = document.getElementById('components-reconnect-modal');
  const reconnect = !!(rc && visible(rc) && /reconnect-(show|failed|rejected)/.test(rc.className || ''));
  let containers = 0, textLen = 0, text = '', matched = '';
  for (const m of document.querySelectorAll(sel)) {
    if (m.id === 'components-reconnect-modal' || (rc && rc.contains(m))) continue;
    if (!visible(m)) continue;
    containers += 1;
    const t = (m.innerText || '').trim();
    textLen = Math.max(textLen, t.length);
    if (!text && /Bond Assessed|Offense Description/i.test(t)) {
      text = t;
      matched = m.tagName.toLowerCase() + (m.className ? '.' + String(m.className).trim().split(/\\s+/).join('.') : '');
    }
  }
  return {containers, text_len: textLen, has_marker: !!text, text, matched: matched.slice(0, 120),
          reconnect, blazor: typeof window.Blazor !== 'undefined'};
}"""

# Names-free DOM shape: tag/class counts and modal-candidate summaries.
_JS_DOM_SHAPE = """() => {
  const top = (obj, n) => Object.fromEntries(Object.entries(obj).sort((a, b) => b[1] - a[1]).slice(0, n));
  const tags = {}, classes = {};
  for (const el of document.body ? document.body.querySelectorAll('*') : []) {
    const t = el.tagName.toLowerCase();
    tags[t] = (tags[t] || 0) + 1;
    for (const c of el.classList) classes[c] = (classes[c] || 0) + 1;
  }
  const cand = Array.from(document.querySelectorAll(
    '.modal, [role=dialog], dialog, .modal-dialog, .modal-backdrop, [class*=modal], [class*=dialog]'
  )).slice(0, 12).map(el => {
    const cs = window.getComputedStyle(el);
    return {tag: el.tagName.toLowerCase(), id: (el.id || '').slice(0, 60),
            cls: String(el.className || '').slice(0, 120), role: el.getAttribute('role') || '',
            display: cs.display, visibility: cs.visibility, children: el.children.length,
            text_len: (el.innerText || '').trim().length};
  });
  const rc = document.getElementById('components-reconnect-modal');
  return {tag_counts: top(tags, 15), class_counts: top(classes, 25), modal_candidates: cand,
          reconnect_class: rc ? String(rc.className || '').slice(0, 120) : null,
          rows: document.querySelectorAll('table tbody tr').length,
          blazor: typeof window.Blazor !== 'undefined'};
}"""

# Names-free structure of modal-like containers: tags/classes; text nodes
# kept only when they look like a field label ("Bond Assessed:"), else
# replaced by "#text(<len>)".
_JS_MODAL_STRUCTURE = """(sel) => {
  const label = /^[A-Za-z #\\/()\\-]{2,40}:?$/;
  const walk = (el, depth) => {
    if (depth > 8) return '…';
    const parts = [];
    for (const n of el.childNodes) {
      if (n.nodeType === 3) {
        const t = n.textContent.trim();
        if (t) parts.push(label.test(t) && /:$/.test(t) ? t : '#text(' + t.length + ')');
      } else if (n.nodeType === 1) {
        const cls = n.classList.length ? '.' + Array.from(n.classList).join('.') : '';
        parts.push('<' + n.tagName.toLowerCase() + cls + '>' + walk(n, depth + 1));
      }
    }
    return parts.join(' ').slice(0, 4000);
  };
  return Array.from(document.querySelectorAll(sel + ', [class*=modal]')).slice(0, 4).map(el => ({
    root: el.tagName.toLowerCase() + (el.classList.length ? '.' + Array.from(el.classList).join('.') : ''),
    structure: walk(el, 0),
  }));
}"""


def egress_mode() -> str:
    return _egress_mode(EGRESS_ENV)


def resolve_egress(scraper: Any = None) -> Tuple[None, str]:
    """Verify this host's own exit is US residential or raise ``EgressBlocked``
    (before any browser start or request to the source)."""
    return _resolve_egress(scraper, county="Pinellas", env_var=EGRESS_ENV)


class PinellasCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Pinellas"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        # Relay gate first: off the residential relay this raises EgressBlocked
        # and no browser is started and no source request is made.
        _, egress_source = resolve_egress(self)
        logger.info("[Pinellas] egress mode=%s source=%s", egress_mode(), egress_source)

        all_records: List[ArrestRecord] = []
        seen: Set[str] = set()
        self._modal_attempts = self._modal_failures = 0
        self._modal_failure_reasons = Counter()
        self._modal_fail_streak = 0
        self._modal_shape_logs = 0
        self._known_names = set()
        self._debug = None
        self.debug_log_path = None

        pw, browser = launch_plain_browser()
        try:
            try:
                # Stock context with an honest User-Agent; no init scripts.
                page = browser.new_context(user_agent=USER_AGENT).new_page()
                if modal_debug_enabled():
                    try:
                        self._debug = ModalDebugRecorder(self)
                        self._debug.attach(page)
                        self.debug_log_path = str(self._debug.path)
                        logger.info("[Pinellas] modal debug log: %s", self.debug_log_path)
                    except Exception as exc:  # noqa: BLE001 - debug must never break a run
                        logger.warning("[Pinellas] modal debug disabled: %s", type(exc).__name__)
                        self._debug = None
                try:
                    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_selector("#booking-date", timeout=60000)
                    time.sleep(1)

                    date_errors = 0
                    for days_ago in range(DAYS_BACK):
                        target = datetime.now() - timedelta(days=days_ago)
                        date_iso = target.strftime("%Y-%m-%d")
                        try:
                            daily = self._scrape_date(page, date_iso, seen)
                            all_records.extend(daily)
                            logger.info(
                                "[Pinellas] %s: %d records", date_iso, len(daily)
                            )
                        except Exception as e:
                            date_errors += 1
                            logger.warning("[Pinellas] %s error: %s", date_iso, e)
                        time.sleep(1)
                    if date_errors == DAYS_BACK:
                        raise RuntimeError(f"Pinellas: all {DAYS_BACK} date searches failed")
                finally:
                    if self._debug is not None:
                        try:
                            self._debug.finish({
                                "modal_attempts": self._modal_attempts,
                                "modal_failures": self._modal_failures,
                                "reasons": dict(self._modal_failure_reasons),
                            })
                        except Exception:  # noqa: BLE001 - debug only
                            pass
                    try:
                        page.close()
                    except Exception:
                        pass
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
        finally:
            try:
                pw.stop()
            except Exception:
                pass

        self._check_modal_failures()
        logger.info("[Pinellas] Scraped %d total records", len(all_records))
        return all_records

    def _scrape_date(self, page, date_iso: str, seen: Set[str]) -> List[ArrestRecord]:
        """Search one booking date (HTML date input expects YYYY-MM-DD)."""
        page.fill("#booking-date", date_iso)
        # "Include Charge Information" — first checkbox on the form.
        try:
            page.locator("input[type=checkbox]").first.check(force=True)
        except Exception:
            pass

        page.get_by_role("button", name=re.compile(r"search", re.I)).click()
        # Empty days still render the shell; wait briefly for rows or settle.
        try:
            page.wait_for_selector("table tbody tr", timeout=20000)
        except Exception:
            logger.info("[Pinellas] %s: no result rows", date_iso)
            return []

        time.sleep(1.2)
        records: List[ArrestRecord] = []
        for page_num in range(1, MAX_PAGES_PER_DAY + 1):
            batch = self._extract_rows(page)
            self._remember_names(batch)
            new_count = 0
            for raw in batch:
                booking_num = (raw.get("booking_num") or "").strip()
                if not booking_num:
                    continue
                if booking_num in seen:
                    continue
                seen.add(booking_num)
                self._modal_attempts += 1
                detail = self._read_detail_modal(page, booking_num)
                if not detail:
                    # Skip: a roster-only record would $set a blank bond and
                    # abbreviated charges over the values stored for it.
                    self._modal_failures += 1
                    continue
                if detail.get("charges"):
                    raw["charge"] = detail["charges"]
                raw["bond_amount"] = detail.get("bond_amount", "")
                if detail.get("case_numbers"):
                    raw["case_number"] = detail["case_numbers"]
                rec = self._row_to_record(raw)
                if rec:
                    records.append(rec)
                    new_count += 1

            nxt = page.get_by_role("button", name=re.compile(r"^Next$", re.I))
            disabled = True
            if nxt.count():
                try:
                    disabled = nxt.is_disabled()
                except Exception:
                    disabled = True

            logger.debug(
                "[Pinellas] %s page %d: batch=%d new=%d next_disabled=%s",
                date_iso,
                page_num,
                len(batch),
                new_count,
                disabled,
            )
            if disabled or new_count == 0:
                break
            nxt.click()
            time.sleep(1.2)

        return records

    @staticmethod
    def _extract_rows(page) -> List[dict]:
        return page.evaluate(
            """() => {
              const rows = Array.from(document.querySelectorAll('table tbody tr'));
              return rows.map(r => {
                const nameTd = r.querySelector('.td-name');
                const nameA = nameTd ? nameTd.querySelector('a') : null;
                const chargeSpan = nameTd ? nameTd.querySelector('span') : null;
                const race = r.querySelector('.td-race')?.innerText.trim() || '';
                const sex = r.querySelector('.td-sex')?.innerText.trim() || '';
                const dob = r.querySelector('.td-dob')?.innerText.trim() || '';
                const bookingTd = r.querySelector('.td-booking');
                const bookingParts = bookingTd
                  ? bookingTd.innerText.trim().split(/\\n+/).map(s => s.trim()).filter(Boolean)
                  : [];
                const tds = Array.from(r.querySelectorAll('td'));
                const last = tds[tds.length - 1];
                const ids = last
                  ? last.innerText.trim().split(/\\n+/).map(s => s.trim()).filter(Boolean)
                  : [];
                let name = '';
                if (nameA) {
                  name = nameA.innerText.trim();
                } else if (nameTd) {
                  name = nameTd.innerText.split('\\n')[0].trim();
                }
                return {
                  name,
                  charge: chargeSpan ? chargeSpan.innerText.trim() : '',
                  race, sex, dob,
                  booking_dt: bookingParts[0] || '',
                  custody: bookingParts[1] || '',
                  booking_num: ids[0] || '',
                  inmate_num: ids[1] || '',
                };
              });
            }"""
        )

    def _row_to_record(self, raw: dict) -> Optional[ArrestRecord]:
        name = self._clean(raw.get("name") or "")
        booking_num = self._clean(raw.get("booking_num") or "")
        if not name or not booking_num:
            return None

        first, middle, last = self._parse_name(name)
        booking_date, booking_time = self._parse_booking_dt(raw.get("booking_dt") or "")
        custody_raw = (raw.get("custody") or "").strip().lower()
        if "released" in custody_raw:
            status = "Released"
        elif custody_raw:
            status = "In Custody"
        else:
            status = "In Custody"

        sex_raw = (raw.get("sex") or "").strip().upper()
        sex = sex_raw[:1] if sex_raw else ""

        dob = self._normalize_dob(raw.get("dob") or "")
        inmate_num = self._clean(raw.get("inmate_num") or "")

        return ArrestRecord(
            County=self.county,
            State="FL",
            Booking_Number=booking_num,
            Person_ID=inmate_num,
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            DOB=dob,
            Arrest_Date=booking_date,
            Booking_Date=booking_date,
            Booking_Time=booking_time,
            Status=status,
            Facility=FACILITY,
            Race=self._clean(raw.get("race") or ""),
            Sex=sex,
            Charges=self._clean(raw.get("charge") or ""),
            Bond_Amount=self._format_bond_amount(raw.get("bond_amount")),
            Case_Number=self._clean(raw.get("case_number") or ""),
            Detail_URL=SEARCH_URL,
            LastCheckedMode="INITIAL",
        )


    _modal_attempts = 0
    _modal_failures = 0

    def _check_modal_failures(self) -> None:
        """Bookings whose modal did not render are skipped (never written with
        blanks). If every modal failed, the run fails loud. The per-reason
        counts (names-free) ride along in the warning and the error text."""
        reasons = getattr(self, "_modal_failure_reasons", None) or Counter()
        summary = ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())) or "none recorded"
        if self._modal_failures:
            logger.warning(
                "[Pinellas] %d/%d charge-report modals did not render; those bookings were skipped "
                "(reasons: %s)",
                self._modal_failures, self._modal_attempts, summary,
            )
        if self._modal_attempts and self._modal_failures == self._modal_attempts:
            raise RuntimeError(
                "Pinellas: every Subject Charge Report modal failed to render "
                f"(reasons: {summary})"
            )

    # ── Subject Charge Report modal ──────────────────────────────────────────
    def _read_detail_modal(self, page, booking_num: str) -> Optional[dict]:
        """Open Subject Charge Report for one roster row; parse bond + charges.

        Bond Assessed / Offense Description are not on the roster table — only
        in the name-click modal. Returns None if the modal does not render
        (after one retry); the names-free reason is logged and counted.
        """
        if not hasattr(self, "_modal_failure_reasons"):
            self._modal_failure_reasons = Counter()
        streak = getattr(self, "_modal_fail_streak", 0)
        if streak >= MODAL_ABORT_AFTER:
            # The last N bookings all failed twice: the modal path is broken
            # for this run. Stop spending up to 2x timeout per booking.
            self._modal_failure_reasons[REASON_ABORTED] += 1
            return None

        timeout_ms = modal_timeout_ms()
        result = None
        reason = ""
        for attempt in (1, 2):
            text, reason, diag = self._open_modal_once(page, booking_num, timeout_ms)
            self._log_modal_attempt(booking_num, attempt, reason, diag)
            if text is not None:
                result = self.parse_charge_report_text(text)
                break
            if reason == REASON_CLICK_TARGET:
                break  # the row link is not on the page; a retry cannot help
            _sleep(MODAL_RETRY_PAUSE_S)

        if result is None:
            self._modal_failure_reasons[reason or REASON_EXCEPTION] += 1
            self._modal_fail_streak = streak + 1
        else:
            self._modal_fail_streak = 0
        _sleep(MODAL_PAUSE_S)
        return result

    def _open_modal_once(self, page, booking_num: str, timeout_ms: int) -> Tuple[Optional[str], str, dict]:
        """One click → wait → close cycle. Returns ``(text|None, reason, diag)``."""
        diag: dict = {"selector": MODAL_CONTAINER_SELECTOR, "timeout_ms": timeout_ms}
        started = _now()
        text: Optional[str] = None
        try:
            leftover = self._close_modal(page)
            if leftover:
                diag["leftover_modal_open"] = True
            diag["wait"] = "row link"
            click_mode = self._click_row_link(page, booking_num, diag)
            diag["click"] = click_mode
            if not click_mode:
                reason = REASON_CLICK_TARGET
            else:
                text, reason, probe = self._wait_for_modal(page, timeout_ms, diag)
                diag["probe"] = {k: v for k, v in (probe or {}).items() if k != "text"}
        except Exception as exc:  # noqa: BLE001 - classified and logged below
            reason = REASON_EXCEPTION
            diag["exc_type"] = type(exc).__name__
            diag["exc"] = _redact(str(exc), self._known_names)[:300]
        diag["elapsed_ms"] = int((_now() - started) * 1000)
        if text is None:
            try:
                diag["dom_shape"] = page.evaluate(_JS_DOM_SHAPE)
            except Exception as exc:  # noqa: BLE001
                diag["dom_shape"] = {"error": type(exc).__name__}
            if modal_debug_enabled():
                try:
                    diag["modal_structure"] = page.evaluate(_JS_MODAL_STRUCTURE, MODAL_CONTAINER_SELECTOR)
                except Exception as exc:  # noqa: BLE001
                    diag["modal_structure"] = {"error": type(exc).__name__}
        try:
            self._close_modal(page)
        except Exception:  # noqa: BLE001
            pass
        return text, reason, diag

    def _click_row_link(self, page, booking_num: str, diag: dict) -> str:
        """Click the name link of the row whose booking number matches.

        Marks the exact anchor in the DOM, then uses a real (trusted) Playwright
        click so Blazor's event delegation sees a normal user click. Falls back
        to a DOM ``click()`` if the trusted click fails. Returns ``"trusted"``,
        ``"js"`` or ``""`` (row link not found)."""
        found = page.evaluate(_JS_MARK_ROW_LINK, booking_num)
        if not found:
            return ""
        try:
            link = page.locator(f"a[{_TARGET_ATTR}]").first
            link.scroll_into_view_if_needed(timeout=5000)
            link.click(timeout=5000)
            return "trusted"
        except Exception as exc:  # noqa: BLE001
            diag["trusted_click_exc"] = type(exc).__name__
        return "js" if page.evaluate(_JS_CLICK_MARKED) else ""

    def _wait_for_modal(self, page, timeout_ms: int, diag: dict) -> Tuple[Optional[str], str, dict]:
        """Poll the modal's own content until it carries the charge fields and
        is stable across two polls, or until ``timeout_ms``.

        Reasons: ``selector_not_found`` (no visible modal container ever),
        ``empty_content`` (container visible, no text), ``no_charge_fields_by_deadline``
        (text but no Offense Description / Bond Assessed), ``circuit_disconnected``
        (Blazor reconnect UI showing)."""
        deadline = _now() + timeout_ms / 1000.0
        diag["wait"] = "modal container visible + charge fields + stable text"
        probe: dict = {}
        seen_text: Optional[str] = None
        while True:
            probe = page.evaluate(_JS_PROBE_MODAL, MODAL_CONTAINER_SELECTOR) or {}
            if probe.get("reconnect"):
                return None, REASON_CIRCUIT, probe
            if probe.get("has_marker"):
                text = probe.get("text") or ""
                if seen_text is not None and text == seen_text:
                    return text, REASON_OK, probe
                seen_text = text
            if _now() >= deadline:
                break
            _sleep(MODAL_POLL_S)
        if seen_text:
            return seen_text, REASON_OK, probe
        if not probe.get("containers"):
            return None, REASON_SELECTOR, probe
        if not probe.get("text_len"):
            return None, REASON_EMPTY, probe
        return None, REASON_NO_FIELDS, probe

    def _close_modal(self, page) -> bool:
        """Escape + the modal's own Close button; wait (≤3s) for it to go.
        Returns True when a modal was open on entry."""
        probe = page.evaluate(_JS_PROBE_MODAL, MODAL_CONTAINER_SELECTOR) or {}
        if not probe.get("containers"):
            return False
        try:
            page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass
        try:
            closer = page.locator(MODAL_CONTAINER_SELECTOR).get_by_role(
                "button", name=re.compile(r"close|×", re.I)
            )
            if closer.count():
                closer.first.click(force=True, timeout=2000)
        except Exception:  # noqa: BLE001
            pass
        deadline = _now() + MODAL_CLOSE_WAIT_S
        while _now() < deadline:
            if not (page.evaluate(_JS_PROBE_MODAL, MODAL_CONTAINER_SELECTOR) or {}).get("containers"):
                break
            _sleep(MODAL_POLL_S)
        return True

    # ── Diagnostics (names-free) ─────────────────────────────────────────────
    _known_names: frozenset = frozenset()

    def _log_modal_attempt(self, booking_num: str, attempt: int, reason: str, diag: dict) -> None:
        if reason != REASON_OK:
            shown = getattr(self, "_modal_shape_logs", 0)
            logger.warning(
                "[Pinellas] modal booking=%s attempt=%d/2 reason=%s wait=%r selector=%r "
                "timeout_ms=%s elapsed_ms=%s click=%s exc=%s%s",
                booking_num, attempt, reason, diag.get("wait"), diag.get("selector"),
                diag.get("timeout_ms"), diag.get("elapsed_ms"), diag.get("click"),
                (f"{diag.get('exc_type')}: {diag.get('exc')}" if diag.get("exc_type") else "-"),
                (f" dom_shape={json.dumps(diag.get('dom_shape'), sort_keys=True)[:1500]}"
                 if shown < MODAL_SHAPE_LOG_LIMIT else ""),
            )
            self._modal_shape_logs = shown + 1
        dbg = getattr(self, "_debug", None)
        if dbg is not None:
            dbg.record_modal(booking_num, attempt, reason, diag)

    def _remember_names(self, batch: List[dict]) -> None:
        """Names seen on this page, used only to redact diagnostics."""
        names = set(self._known_names) if self._known_names else set()
        for raw in batch:
            name = (raw.get("name") or "").strip()
            if name:
                names.add(name)
        self._known_names = names


    @staticmethod
    def parse_charge_report_text(text: str) -> dict:
        """Parse Subject Charge Report modal text (source-faithful, no invention).

        The total is the sum of every charge's **Bond Assessed** only when each
        charge publishes an amount (a published $0.00 counts). A blank or
        non-numeric Bond Assessed (it can be a hold) makes the total "".
        Offense Description lines give Charges; Court Case Numbers are joined.
        """
        if not text:
            return {"charges": "", "bond_amount": "", "case_numbers": ""}

        offenses = re.findall(r"Offense Description:\s*([^\n]+)", text, flags=re.I)
        cases = re.findall(r"Court Case Number:\s*([^\n]+)", text, flags=re.I)

        # One block per charge (each starts at its Offense Description).
        blocks = re.split(r"Offense Description:", text, flags=re.I)[1:] or [text]
        amounts = []
        any_unpublished = False
        for block in blocks:
            m = re.search(r"Bond Assessed:[ \t]*\n?[ \t]*([^\n]*)", block, flags=re.I)
            amount = PinellasCountyScraper._parse_bond_number(m.group(1)) if m else None
            if amount is None:
                any_unpublished = True
            else:
                amounts.append(amount)

        charges = " | ".join(
            PinellasCountyScraper._clean(o) for o in offenses if o and o.strip()
        )
        case_numbers = " | ".join(
            PinellasCountyScraper._clean(c) for c in cases if c and c.strip()
        )
        if amounts and not any_unpublished:
            bond_amount = PinellasCountyScraper._format_bond_amount(sum(amounts))
        else:
            bond_amount = ""

        return {
            "charges": charges,
            "bond_amount": bond_amount,
            "case_numbers": case_numbers,
        }

    @staticmethod
    def _parse_bond_number(bond_str) -> Optional[float]:
        """A published dollar amount (``$0.00`` included), else None (unknown)."""
        if bond_str is None:
            return None
        cleaned = re.sub(r"[$,\s]", "", str(bond_str).strip())
        if not re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned):
            return None
        return float(cleaned)

    @staticmethod
    def _format_bond_amount(value) -> str:
        """Canonical Bond_Amount string. Unknown/blank/non-numeric → "" (never
        $0); a published zero → "0"."""
        if value is None or value == "":
            return ""
        if isinstance(value, (int, float)):
            amount = float(value)
        else:
            amount = PinellasCountyScraper._parse_bond_number(str(value))
            if amount is None:
                return ""
        if amount <= 0:
            return "0"
        if amount.is_integer():
            return str(int(amount))
        return f"{amount:.2f}"

    @staticmethod
    def _clean(text: str) -> str:
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    @staticmethod
    def _parse_name(name: str):
        """Parse 'LAST, FIRST MIDDLE' into components."""
        if not name:
            return "", "", ""
        name = " ".join(name.strip().split())
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip()
            remainder = parts[1].strip().split()
            first = remainder[0] if remainder else ""
            middle = " ".join(remainder[1:]) if len(remainder) > 1 else ""
            return first, middle, last
        parts = name.split()
        if len(parts) >= 3:
            return parts[0], " ".join(parts[1:-1]), parts[-1]
        if len(parts) == 2:
            return parts[0], "", parts[1]
        return name, "", ""

    @staticmethod
    def _parse_booking_dt(text: str):
        """Parse '9/22/2026 2:42:48 AM' → (YYYY-MM-DD, HH:MM:SS)."""
        text = (text or "").strip()
        if not text:
            return "", ""
        for fmt in (
            "%m/%d/%Y %I:%M:%S %p",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y %I:%M %p",
            "%m/%d/%Y",
        ):
            try:
                dt = datetime.strptime(text, fmt)
                return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
            except ValueError:
                continue
        # Fallback: date-only first token
        token = text.split()[0] if text else ""
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(token, fmt)
                return dt.strftime("%Y-%m-%d"), ""
            except ValueError:
                continue
        return text, ""

    @staticmethod
    def _normalize_dob(text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                return datetime.strptime(text, fmt).strftime("%m/%d/%Y")
            except ValueError:
                continue
        return text
