"""
Hamilton County (TN) Arrest Scraper — Chattanooga jail inmate roster.

Portal:  https://www.hcsheriff.gov/Corrections/Inmates-app
APIs:
  GET  /Corrections/Inmates-app/Full-List/api  → JSON dict keyed A-Z
       Each entry: {first_name, middle_name, last_name, dob, spn, category}
  POST /Corrections/Inmates-app/api
       Body: {"type": "data", "info": "<spn>"}
       Returns: {first_name, middle_name, last_name, dob, bond_amount,
                 judge_name, division, court_date}

No CAPTCHA, no Cloudflare. Direct requests with Chrome UA suffice.
Detail fetch is rate-limited to avoid hammering the Next.js API.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

import requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

ROSTER_API = "https://www.hcsheriff.gov/Corrections/Inmates-app/Full-List/api"
DETAIL_API = "https://www.hcsheriff.gov/Corrections/Inmates-app/api"
PORTAL_URL = "https://www.hcsheriff.gov/Corrections/Inmates-app"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.hcsheriff.gov/Corrections/Inmates-app/Full-List",
}

# Rate limiting: pause between detail fetches
DETAIL_PAUSE_S = 0.4
# Maximum detail fetches per run (avoid excessive API load)
MAX_DETAIL_FETCHES = 200


class HamiltonScraper(BaseScraper):
    """Hamilton County (TN) — Chattanooga jail roster via JSON API."""

    @property
    def county(self) -> str:
        return "Hamilton"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def scraper_id(self) -> str:
        return "scraper_tn_hamilton"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(HEADERS)
        session.verify = True

        records: List[ArrestRecord] = []
        seen: set = set()

        # ── Phase 1: Fetch full roster (all letters) ──
        roster = self._fetch_roster(session)
        if not roster:
            logger.error("Hamilton (TN): roster API returned empty")
            return []

        # ── Phase 2: Enrich with detail data (bond, court date) ──
        detail_count = 0
        for letter, inmates in roster.items():
            if not isinstance(inmates, list):
                continue
            for inmate in inmates:
                try:
                    spn = str(inmate.get("spn", ""))
                    if not spn or spn in seen:
                        continue
                    seen.add(spn)

                    first = str(inmate.get("first_name", "")).strip()
                    middle = str(inmate.get("middle_name", "")).strip()
                    last = str(inmate.get("last_name", "")).strip()
                    full_name = f"{last}, {first}"
                    if middle:
                        full_name = f"{last}, {first} {middle}"

                    dob_raw = str(inmate.get("dob", ""))
                    dob = self._parse_iso_date(dob_raw)

                    # Default record without detail
                    rec = ArrestRecord(
                        County=self.county,
                        State="TN",
                        Full_Name=full_name.title(),
                        First_Name=first.title(),
                        Middle_Name=middle.title(),
                        Last_Name=last.title(),
                        DOB=dob,
                        Booking_Number=f"HAM_{spn}",
                        Person_ID=spn,
                        Charges="Unknown",
                        Bond_Amount="0",
                        Status="In Custody",
                        Detail_URL=f"{PORTAL_URL}/{spn}",
                        Facility="Hamilton County Jail & Detention Center",
                    )

                    # Fetch detail if under limit
                    if detail_count < MAX_DETAIL_FETCHES:
                        detail = self._fetch_detail(session, spn)
                        if detail:
                            rec.Bond_Amount = detail.get("bond", "0")
                            rec.Court_Date = detail.get("court_date", "")
                            if detail.get("judge"):
                                rec.extra_data["judge"] = detail["judge"]
                            if detail.get("division"):
                                rec.extra_data["division"] = detail["division"]
                        detail_count += 1
                        time.sleep(DETAIL_PAUSE_S)

                    records.append(rec)

                except Exception as e:
                    logger.debug(f"Hamilton inmate parse error: {e}")
                    continue

        elapsed = time.time() - start
        logger.info(
            f"✅ Hamilton (TN): {len(records)} records "
            f"({detail_count} details fetched) in {elapsed:.1f}s"
        )
        return records

    # ── API Methods ──────────────────────────────────────────────────────────

    def _fetch_roster(self, session: requests.Session) -> Optional[Dict[str, Any]]:
        """Fetch the full A-Z inmate roster from the JSON API with retry."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = session.get(ROSTER_API, timeout=30, allow_redirects=True)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        return data
                    logger.warning("Hamilton roster API: unexpected response type")
                    return None
                elif resp.status_code in (500, 502, 503):
                    logger.warning(
                        f"Hamilton roster API: HTTP {resp.status_code} "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(2 * (attempt + 1))  # Backoff
                    continue
                else:
                    logger.error(f"Hamilton roster API: HTTP {resp.status_code}")
                    return None
            except Exception as e:
                logger.error(f"Hamilton roster API attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                return None
        logger.error("Hamilton roster API: all retries exhausted")
        return None

    def _fetch_detail(self, session: requests.Session, spn: str) -> Optional[Dict[str, str]]:
        """Fetch bond/court detail for a single inmate by SPN."""
        try:
            resp = session.post(
                DETAIL_API,
                json={"type": "data", "info": spn},
                timeout=15,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None

            out: Dict[str, str] = {}

            # Bond amount (comes as "$4,000.00" or similar)
            bond_raw = str(data.get("bond_amount", "0"))
            bond_clean = re.sub(r"[^\d.]", "", bond_raw) or "0"
            out["bond"] = bond_clean

            # Court date
            court_raw = str(data.get("court_date", ""))
            if court_raw and court_raw != "None":
                out["court_date"] = self._parse_iso_date(court_raw)

            # Judge
            judge = str(data.get("judge_name", "")).strip()
            if judge:
                out["judge"] = judge

            # Division
            division = data.get("division")
            if division:
                out["division"] = str(division)

            return out

        except Exception as e:
            logger.debug(f"Hamilton detail {spn}: {e}")
            return None

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_iso_date(raw: str) -> str:
        """Parse ISO date string to MM/DD/YYYY format."""
        if not raw or raw == "None":
            return ""
        # Handle "1992-11-23T00:00:00.000Z" format
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
        if match:
            y, m, d = match.groups()
            return f"{m}/{d}/{y}"
        return raw.strip()
