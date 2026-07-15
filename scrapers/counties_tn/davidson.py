"""
Davidson County (TN) Arrest Scraper — Nashville DCSO Active Inmate Search.

Portal: https://dcso.nashville.gov
  - /Search/RecentBookings  — last 48h bookings (high value for bail)
  - /Search/Person          — active inmate letter search
  - /Search/Details/{jms}   — charges + bond

Davidson (Nashville) is TN's 2nd-largest county. Powered by Justice Integration Services.
"""
from __future__ import annotations

import hashlib
import logging
import re
import string
import time
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://dcso.nashville.gov"
RECENT_URL = f"{BASE_URL}/Search/RecentBookings"
PERSON_URL = f"{BASE_URL}/Search/Person"
DETAIL_PATH = "/Search/Details/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE_URL,
}

# Cap detail enrichment so one-shot stays within a reasonable window.
MAX_DETAIL_FETCHES = 200
REQUEST_PAUSE = 0.15


class DavidsonScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Davidson"

    @property
    def state(self) -> str:
        return "TN"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(HEADERS)
        session.verify = False

        records: List[ArrestRecord] = []
        seen: Set[str] = set()

        # 1) Recent bookings (last 48h) — primary bail-intent feed
        try:
            recent = self._scrape_recent(session)
            for rec in recent:
                key = rec.Booking_Number or rec.Full_Name
                if key in seen:
                    continue
                seen.add(key)
                records.append(rec)
        except Exception as e:
            logger.error(f"Davidson recent bookings failed: {e}")

        # 2) Active roster via last-name letter walk (list-level fields)
        try:
            active = self._scrape_active_letters(session, seen)
            for rec in active:
                key = rec.Booking_Number or rec.Full_Name
                if key in seen:
                    continue
                seen.add(key)
                records.append(rec)
        except Exception as e:
            logger.error(f"Davidson active letter walk failed: {e}")

        # 3) Enrich missing charges/bond from detail pages (recent first)
        try:
            self._enrich_details(session, records)
        except Exception as e:
            logger.debug(f"Davidson detail enrichment partial failure: {e}")

        logger.info(
            f"✅ Davidson (TN): {len(records)} records in {time.time() - start:.1f}s"
        )
        return records

    # ── Recent bookings ──────────────────────────────────────────────────────

    def _scrape_recent(self, session: requests.Session) -> List[ArrestRecord]:
        resp = session.get(RECENT_URL, timeout=30)
        resp.raise_for_status()
        return self._parse_results_table(resp.text, source="recent")

    # ── Active letter walk ───────────────────────────────────────────────────

    def _scrape_active_letters(
        self, session: requests.Session, seen: Set[str]
    ) -> List[ArrestRecord]:
        out: List[ArrestRecord] = []
        for letter in string.ascii_uppercase:
            try:
                batch = self._search_person(session, last_name=letter, first_name="")
            except Exception as e:
                logger.debug(f"Davidson letter {letter}: {e}")
                continue
            for rec in batch:
                key = rec.Booking_Number or rec.Full_Name
                if key in seen:
                    continue
                out.append(rec)
            time.sleep(REQUEST_PAUSE)
        return out

    def _search_person(
        self, session: requests.Session, last_name: str, first_name: str = ""
    ) -> List[ArrestRecord]:
        # Seed session / any cookies
        session.get(PERSON_URL, timeout=25)
        data = {
            "firstName": first_name,
            "lastName": last_name,
        }
        resp = session.post(PERSON_URL, data=data, timeout=30)
        resp.raise_for_status()
        return self._parse_results_table(resp.text, source="active")

    # ── Table parsing ────────────────────────────────────────────────────────

    def _parse_results_table(self, html: str, source: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if not table:
            return []

        rows = table.find_all("tr")
        if len(rows) < 2:
            return []

        headers = [
            th.get_text(" ", strip=True).lower()
            for th in rows[0].find_all(["th", "td"])
        ]
        records: List[ArrestRecord] = []

        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            # Detail JMS id from onclick
            jms_id = self._extract_jms_id(row)
            cell_text = [c.get_text(" ", strip=True) for c in cells]

            # Name cell usually index 1 (after View Details)
            name_raw = ""
            for i, h in enumerate(headers):
                if i < len(cell_text) and "name" in h:
                    name_raw = cell_text[i]
                    break
            if not name_raw and len(cell_text) > 1:
                name_raw = cell_text[1]
            name = self._clean_name(name_raw)
            if not name or len(name) < 2:
                continue

            control = ""
            dob = ""
            race = ""
            sex = ""
            facility = ""
            admitted = ""
            release = ""

            for i, h in enumerate(headers):
                if i >= len(cell_text):
                    break
                val = cell_text[i]
                if "control" in h:
                    control = val
                elif "birth" in h or h == "dob":
                    dob = self._clean_dob(val)
                elif h == "race":
                    race = val
                elif h == "sex":
                    sex = val[:1].upper() if val else ""
                elif "facility" in h:
                    facility = val
                elif "admitted" in h:
                    admitted = val
                elif "release" in h:
                    release = val

            booking = control or jms_id or self._fallback_booking(name)
            status = "Released" if release else "In Custody"
            detail_url = (
                urljoin(BASE_URL, f"{DETAIL_PATH}{jms_id}") if jms_id else RECENT_URL
            )

            first, last = self._split_name(name)
            records.append(
                ArrestRecord(
                    County=self.county,
                    State="TN",
                    Full_Name=name,
                    First_Name=first,
                    Last_Name=last,
                    Booking_Number=str(booking),
                    Person_ID=str(jms_id or control or ""),
                    DOB=dob,
                    Race=race,
                    Sex=sex,
                    Booking_Date=admitted,
                    Arrest_Date=admitted,
                    Release_Date=release,
                    Status=status,
                    Facility=facility or "Davidson County Correctional Center",
                    Charges="Unknown",
                    Bond_Amount="0",
                    Detail_URL=detail_url,
                    Agency="Davidson County Sheriff's Office",
                    extra_data={"source": source, "jms_id": jms_id or ""},
                )
            )

        return records

    # ── Detail enrichment ────────────────────────────────────────────────────

    def _enrich_details(
        self, session: requests.Session, records: List[ArrestRecord]
    ) -> None:
        """Fetch charge/bond from detail pages for records missing charges."""
        fetched = 0
        for rec in records:
            if fetched >= MAX_DETAIL_FETCHES:
                break
            jms = (rec.extra_data or {}).get("jms_id") or ""
            if not jms:
                # Try extract from Detail_URL
                m = re.search(r"/Details/(\d+)", rec.Detail_URL or "")
                jms = m.group(1) if m else ""
            if not jms:
                continue
            # Skip if already enriched
            if rec.Charges and rec.Charges != "Unknown" and rec.Bond_Amount not in ("", "0"):
                continue

            try:
                detail = self._fetch_detail(session, jms)
                if not detail:
                    continue
                if detail.get("charges"):
                    rec.Charges = detail["charges"]
                if detail.get("bond"):
                    rec.Bond_Amount = detail["bond"]
                if detail.get("facility") and not rec.Facility:
                    rec.Facility = detail["facility"]
                if detail.get("dob") and not rec.DOB:
                    rec.DOB = detail["dob"]
                if detail.get("booking_date") and not rec.Booking_Date:
                    rec.Booking_Date = detail["booking_date"]
                    rec.Arrest_Date = detail["booking_date"]
                if detail.get("release"):
                    rec.Release_Date = detail["release"]
                    rec.Status = "Released"
                fetched += 1
                time.sleep(REQUEST_PAUSE)
            except Exception as e:
                logger.debug(f"Davidson detail {jms}: {e}")

    def _fetch_detail(self, session: requests.Session, jms_id: str) -> Optional[Dict]:
        url = urljoin(BASE_URL, f"{DETAIL_PATH}{jms_id}")
        resp = session.get(url, timeout=25)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text("\n", strip=True)

        out: Dict[str, str] = {}

        # Label/value pairs from page text blocks
        def _after(label: str) -> str:
            m = re.search(
                rf"{re.escape(label)}\s*\n\s*(.+)",
                text,
                re.IGNORECASE,
            )
            return m.group(1).strip() if m else ""

        out["facility"] = _after("Facility")
        dob_raw = _after("Date of Birth")
        if dob_raw:
            out["dob"] = self._clean_dob(dob_raw)
        out["booking_date"] = _after("Arrest Booking Date") or _after("Admitted Date")
        out["release"] = _after("Release Date")

        # Charges: collect Arrested Charge lines
        charges = re.findall(
            r"Arrested Charge\s*\n\s*(.+)",
            text,
            re.IGNORECASE,
        )
        if charges:
            out["charges"] = " | ".join(c.strip() for c in charges if c.strip())

        # Bonds: sum all Bond $ amounts
        bonds = re.findall(r"Bond\s*\n\s*\$?\s*([\d,]+\.?\d*)", text, re.IGNORECASE)
        if not bonds:
            bonds = re.findall(r"\$\s*([\d,]+\.\d{2})", text)
        if bonds:
            total = 0.0
            for b in bonds:
                try:
                    total += float(b.replace(",", ""))
                except ValueError:
                    continue
            out["bond"] = str(int(total)) if total == int(total) else f"{total:.2f}"

        return out if out else None

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_jms_id(row) -> str:
        for attr in (row.get("onclick") or "",):
            m = re.search(r"/Details/(\d+)", attr)
            if m:
                return m.group(1)
        for el in row.find_all(["a", "button"]):
            blob = (el.get("onclick") or "") + (el.get("href") or "")
            m = re.search(r"/Details/(\d+)", blob)
            if m:
                return m.group(1)
        return ""

    @staticmethod
    def _clean_name(raw: str) -> str:
        if not raw:
            return ""
        # Results often append AKA variants after the primary name
        primary = raw.split("  ")[0].strip()
        primary = re.sub(r"\s+", " ", primary)
        # Prefer LAST, FIRST form — take first comma-separated name unit
        if "," in primary:
            parts = primary.split(",")
            last = parts[0].strip()
            first = parts[1].strip().split("  ")[0].strip()
            # Truncate first if AKA pollution
            first = re.split(r"\s{2,}|\s(?=[A-Z]{2,},\s)", first)[0].strip()
            return f"{last}, {first}".strip(", ")
        return primary.title() if primary.isupper() else primary

    @staticmethod
    def _split_name(name: str) -> tuple:
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip().title()
            first = parts[1].strip().title()
            return first, last
        bits = name.split()
        if len(bits) >= 2:
            return bits[0].title(), bits[-1].title()
        return name.title(), ""

    @staticmethod
    def _clean_dob(raw: str) -> str:
        if not raw:
            return ""
        # "Dec 09, 1995 (30)" or "9/1/1990 12:00:00 AM  (35)"
        raw = re.sub(r"\s*\(\d+\)\s*$", "", raw).strip()
        raw = re.sub(r"\s+\d{1,2}:\d{2}:\d{2}\s*[AP]M\s*$", "", raw, flags=re.I)
        return raw.strip()

    @staticmethod
    def _fallback_booking(name: str) -> str:
        return f"DAV_{hashlib.md5(f'{name}|DAVIDSON_TN'.encode()).hexdigest()[:10]}"
