"""
Broward County Arrest Scraper — BSO Arrest Search (Turnstile + paged roster).
Source: Broward Sheriff's Office
URL: https://apps.sheriff.org/ArrestSearch
Method: SolveCaptcha Turnstile → StartArrestSearch → GetArrestSearchPage pages
        → optional InmateDetailJ enrich for charges/bond.

HISTORY:
- v1: Sequential InmateDetail/{JMS_ID} probing (TLS verify off, assumed custody).
- v2: Fail-closed stub — Turnstile on search; ID URLs now 404; contract forbade
      person-level probes without a booking-safe bulk roster.
- v3 (current): Official Angular app exposes a real bulk grid after Turnstile.
      UI requires ≥2 letters of first or last name; we sweep last-name prefixes
      (single-letter first — server may accept — then digraphs).
      Mac write smoke 2026-09-23: prefix SM → 30 scraped / 30 new / status=ok
      after passing Turnstile action=arrest_search to SolveCaptcha.
      Requires env SOLVECAPTCHA_KEY.
"""
from __future__ import annotations

import logging
import os
import re
import string
import time
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Set

from curl_cffi import requests as cffi_requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper
from scrapers.solvecaptcha import solve_turnstile

logger = logging.getLogger(__name__)

SEARCH_PAGE_URL = "https://apps.sheriff.org/ArrestSearch"
START_URL = f"{SEARCH_PAGE_URL}/StartArrestSearch"
PAGE_URL = f"{SEARCH_PAGE_URL}/GetArrestSearchPage"
DETAIL_URL = f"{SEARCH_PAGE_URL}/InmateDetailJ"
TURNSTILE_SITEKEY = "0x4AAAAAAD87BAnKQzZI2ajp"
IMPERSONATE = "chrome131"
FACILITY = "Broward County Jail"
# UI asks for ≥2 letters; try single letters on the server first (cheaper).
DEFAULT_PREFIX_MODE = os.getenv("BROWARD_PREFIX_MODE", "single").strip().lower()
MAX_PAGES_PER_PREFIX = int(os.getenv("BROWARD_MAX_PAGES", "40"))
PAGE_SIZE = int(os.getenv("BROWARD_PAGE_SIZE", "50"))
ENRICH_DETAILS = os.getenv("BROWARD_ENRICH_DETAILS", "1").strip() not in (
    "0",
    "false",
    "False",
)
MAX_DETAIL_FETCHES = int(os.getenv("BROWARD_MAX_DETAILS", "120"))
PREFIX_PAUSE_SEC = float(os.getenv("BROWARD_PREFIX_PAUSE", "0.4"))


class BrowardCountyScraper(BaseScraper):
    """Broward (FL) — Turnstile-gated Arrest Search bulk grid."""

    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "Broward Arrest Search bulk grid after Turnstile (action=arrest_search); "
        "Mac write smoke 2026-09-23: 30 new via prefix SM."
    )

    OFFICIAL_ARREST_SEARCH_URL = SEARCH_PAGE_URL

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger = logger

    @property
    def county(self) -> str:
        return "Broward"

    @property
    def roster_url(self) -> str:
        return self.OFFICIAL_ARREST_SEARCH_URL

    def scrape(self) -> List[ArrestRecord]:
        api_key = os.getenv("SOLVECAPTCHA_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "Broward requires SOLVECAPTCHA_KEY to solve Turnstile on "
                f"{SEARCH_PAGE_URL}"
            )

        session = cffi_requests.Session()
        session.get(
            SEARCH_PAGE_URL,
            impersonate=IMPERSONATE,
            timeout=45,
            verify=False,
        )

        seen: Set[str] = set()
        records: List[ArrestRecord] = []
        details_fetched = 0

        for prefix in self._iter_prefixes():
            token = self._solve_turnstile(api_key)
            if not token:
                logger.error("[Broward] Turnstile solve failed for prefix=%r", prefix)
                continue

            session_id = self._start_search(session, last_name=prefix, token=token)
            if not session_id:
                logger.warning("[Broward] StartArrestSearch failed for prefix=%r", prefix)
                continue

            rows = self._paginate_session(session, session_id)
            logger.info(
                "[Broward] prefix=%r session rows=%d", prefix, len(rows)
            )

            for row in rows:
                jms = str(
                    row.get("JMS_NUMBER")
                    or row.get("jms_number")
                    or row.get("Jms_Number")
                    or ""
                ).strip()
                if not jms or jms in seen:
                    continue
                seen.add(jms)

                rec = self._row_to_record(row, jms)
                detail_token = (
                    row.get("DETAIL_TOKEN")
                    or row.get("detailToken")
                    or row.get("DetailToken")
                )
                if (
                    ENRICH_DETAILS
                    and detail_token
                    and details_fetched < MAX_DETAIL_FETCHES
                ):
                    try:
                        self._enrich_detail(session, rec, str(detail_token))
                        details_fetched += 1
                    except Exception as e:
                        logger.debug("[Broward] detail enrich failed for %s: %s", jms, e)

                records.append(rec)

            time.sleep(PREFIX_PAUSE_SEC)

        logger.info(
            "[Broward] Scraped %d records (details_enriched=%d)",
            len(records),
            details_fetched,
        )
        return records

    # ── Prefix strategy ────────────────────────────────────────────────────

    def _iter_prefixes(self) -> Iterable[str]:
        """Yield last-name search prefixes.

        ``single``: A–Z (26 Turnstile solves) — server may accept despite UI.
        ``digraph``: aa–zz (676) — expensive; use only if single is rejected.
        ``env``: comma list in BROWARD_NAME_PREFIXES.
        """
        custom = os.getenv("BROWARD_NAME_PREFIXES", "").strip()
        if custom:
            for part in custom.split(","):
                p = part.strip()
                if p:
                    yield p
            return

        mode = DEFAULT_PREFIX_MODE
        if mode == "digraph":
            for a in string.ascii_lowercase:
                for b in string.ascii_lowercase:
                    yield f"{a}{b}"
            return

        # default: single letters
        for a in string.ascii_uppercase:
            yield a

    # ── Turnstile ──────────────────────────────────────────────────────────

    def _solve_turnstile(self, api_key: str) -> Optional[str]:
        # Widget declares data-action="arrest_search"; token is rejected
        # (CAPTCHA_FAILED) without matching action. Shared helper: scrapers/solvecaptcha.py
        return solve_turnstile(
            api_key,
            sitekey=TURNSTILE_SITEKEY,
            pageurl=SEARCH_PAGE_URL,
            action="arrest_search",
            http=cffi_requests,
            log_prefix="[Broward]",
        )

    # ── Search session ─────────────────────────────────────────────────────

    def _start_search(
        self, session, *, last_name: str, token: str, first_name: str = ""
    ) -> Optional[str]:
        try:
            resp = session.post(
                START_URL,
                data={"fn": first_name or "", "ln": last_name or "", "d": token},
                headers={
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Referer": SEARCH_PAGE_URL,
                    "Origin": "https://apps.sheriff.org",
                },
                impersonate=IMPERSONATE,
                timeout=60,
                verify=False,
            )
            if resp.status_code != 200:
                logger.warning(
                    "[Broward] StartArrestSearch HTTP %s: %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return None
            data = resp.json()
            sid = (
                data.get("searchSessionId")
                or data.get("SearchSessionId")
                or data.get("sessionId")
            )
            if not sid:
                logger.warning("[Broward] StartArrestSearch no session: %s", str(data)[:300])
            return sid
        except Exception as e:
            logger.warning("[Broward] StartArrestSearch error: %s", e)
            return None

    def _paginate_session(self, session, search_session_id: str) -> List[dict]:
        rows: List[dict] = []
        for page in range(1, MAX_PAGES_PER_PREFIX + 1):
            try:
                resp = session.post(
                    PAGE_URL,
                    data={
                        "searchSessionId": search_session_id,
                        "pageNumber": str(page),
                        "pageSize": str(PAGE_SIZE),
                        "sortColumn": "JMS_NUMBER",
                        "sortDescending": "true",
                    },
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "Referer": SEARCH_PAGE_URL,
                    },
                    impersonate=IMPERSONATE,
                    timeout=60,
                    verify=False,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "[Broward] GetArrestSearchPage HTTP %s page=%d",
                        resp.status_code,
                        page,
                    )
                    break
                data = resp.json()
                batch = data.get("records") or data.get("Records") or []
                total = int(data.get("totalRecords") or data.get("TotalRecords") or 0)
                if not batch:
                    break
                rows.extend(batch)
                if len(rows) >= total:
                    break
                time.sleep(0.25)
            except Exception as e:
                logger.warning("[Broward] page %d error: %s", page, e)
                break
        return rows

    # ── Record mapping ─────────────────────────────────────────────────────

    def _row_to_record(self, row: dict, jms: str) -> ArrestRecord:
        last = str(row.get("LAST_NAME") or row.get("Last_Name") or "").strip()
        first = str(row.get("FIRST_NAME") or row.get("First_Name") or "").strip()
        middle = str(row.get("MIDDLE_NAME") or row.get("Middle_Name") or "").strip()
        full = f"{last}, {first}" + (f" {middle}" if middle else "")
        sex = str(row.get("SEX") or row.get("Sex") or "").strip()
        location = str(row.get("LOCATION") or row.get("Location") or "").strip()

        return ArrestRecord(
            County=self.county,
            State="FL",
            Booking_Number=jms,
            Full_Name=full,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Sex=sex[:1].upper() if sex else "",
            Facility=location or FACILITY,
            Status="In Custody",
            Detail_URL=f"{SEARCH_PAGE_URL}/InmateDetail#jms={jms}",
            LastCheckedMode="INITIAL",
            Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def _enrich_detail(self, session, rec: ArrestRecord, detail_token: str) -> None:
        resp = session.post(
            DETAIL_URL,
            data={"token": detail_token},
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": SEARCH_PAGE_URL,
            },
            impersonate=IMPERSONATE,
            timeout=45,
            verify=False,
        )
        if resp.status_code != 200:
            return
        # Response is HTML fragment
        html = resp.text
        if isinstance(resp.json() if False else None, dict):
            pass
        try:
            # Some deployments wrap HTML in JSON {data: "..."}
            js = resp.json()
            if isinstance(js, dict) and ("data" in js or "Data" in js):
                html = js.get("data") or js.get("Data") or html
            elif isinstance(js, str):
                html = js
        except Exception:
            html = resp.text

        booking_date = self._label_from_html(html, r"Booking\s*Date")
        if booking_date:
            rec.Booking_Date = booking_date
            rec.Arrest_Date = booking_date
        dob = self._label_from_html(html, r"DOB|Date\s*of\s*Birth")
        if dob:
            rec.DOB = dob
        race = self._label_from_html(html, r"Race")
        if race:
            rec.Race = race
        agency = self._label_from_html(html, r"Arresting\s*Agency|Agency")
        if agency:
            rec.Agency = agency
        bond = self._label_from_html(html, r"Total\s*Bond|Bond\s*Amount|Bond")
        if bond:
            rec.Bond_Amount = str(self._parse_bond(bond))
        charges = self._charges_from_html(html)
        if charges:
            rec.Charges = charges

    @staticmethod
    def _label_from_html(html: str, label_re: str) -> str:
        m = re.search(
            rf"(?:{label_re})\s*[:<\s]+([^<\n\r]{{2,60}})",
            html,
            re.I,
        )
        if not m:
            return ""
        return re.sub(r"\s+", " ", m.group(1)).strip(" :")

    @staticmethod
    def _charges_from_html(html: str) -> str:
        parts = re.findall(
            r"(?:Charge|Statute|Offense)[^<]*</[^>]+>\s*<[^>]+>([^<]{3,120})",
            html,
            re.I,
        )
        cleaned = [re.sub(r"\s+", " ", p).strip() for p in parts if p.strip()]
        # de-dupe preserve order
        out: List[str] = []
        seen: Set[str] = set()
        for c in cleaned:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return " | ".join(out[:12])

    @staticmethod
    def _parse_bond(bond_str: str) -> float:
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", str(bond_str).strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
