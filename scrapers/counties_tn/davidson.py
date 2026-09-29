"""
Davidson County (TN) Arrest Scraper — Nashville DCSO Active Inmate Search.

Portal: https://dcso.nashville.gov
  - /Search/RecentBookings  — last 48h bookings (primary high-intent bail feed)
  - /Search/Details/{jms}   — charges, warrants, bond amounts, custody status

Davidson (Nashville) is TN's 2nd-largest county. Powered by Justice Integration Services.
Official source identifiers: DCSO JMS Number and Control Number.
"""
from __future__ import annotations

import logging
import re
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
DETAIL_PATH = "/Search/Details/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE_URL,
}

MAX_DETAIL_FETCHES = 250
REQUEST_PAUSE = 0.15


class DavidsonScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = ""

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
        session.verify = True

        records: List[ArrestRecord] = []
        seen: Set[str] = set()

        # 1) Recent bookings (last 48h) — primary bail-intent feed
        try:
            recent = self._scrape_recent(session)
            for rec in recent:
                key = rec.Booking_Number
                if not key or key in seen:
                    continue
                seen.add(key)
                records.append(rec)
        except Exception as e:
            logger.error(f"Davidson recent bookings failed: {e}")

        # 2) Enrich charges and bond from detail pages
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

            # Detail JMS id from button onclick
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

            # Require official source booking identifier (JMS ID or Control Number)
            booking = jms_id or control
            if not booking:
                continue

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
                    Person_ID=str(control or jms_id or ""),
                    DOB=dob,
                    Race=race,
                    Sex=sex,
                    Booking_Date=admitted,
                    Arrest_Date=admitted,
                    Release_Date=release,
                    Status=status,
                    Facility=facility or "Downtown Detention Center",
                    Charges="Unknown",
                    Bond_Amount="0",
                    Detail_URL=detail_url,
                    Agency="Davidson County Sheriff's Office",
                    extra_data={"source": source, "jms_id": jms_id or "", "control_number": control or ""},
                )
            )

        return records

    # ── Detail enrichment ────────────────────────────────────────────────────

    def _enrich_details(
        self, session: requests.Session, records: List[ArrestRecord]
    ) -> None:
        """Fetch charge/bond from detail pages for records."""
        fetched = 0
        for rec in records:
            if fetched >= MAX_DETAIL_FETCHES:
                break
            jms = (rec.extra_data or {}).get("jms_id") or ""
            if not jms:
                m = re.search(r"/Details/(\d+)", rec.Detail_URL or "")
                jms = m.group(1) if m else ""
            if not jms:
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

        out: Dict[str, str] = {}

        # Parse inmate info details list
        info_div = soup.find("div", id="processing-inmate-information")
        if info_div:
            for li in info_div.find_all("li"):
                lbl = li.find("label")
                if not lbl:
                    continue
                lbl_txt = lbl.get_text(strip=True).lower()
                val = li.get_text(strip=True).replace(lbl.get_text(strip=True), "").strip()
                if "facility" in lbl_txt and val:
                    out["facility"] = val
                elif ("birth" in lbl_txt or "dob" in lbl_txt) and val:
                    out["dob"] = self._clean_dob(val)
                elif "arrest booking date" in lbl_txt or "admitted date" in lbl_txt:
                    if val and "booking_date" not in out:
                        out["booking_date"] = val
                elif "release date" in lbl_txt and val:
                    out["release"] = val

        # Parse active charges from details-list
        charges: List[str] = []
        total_bond = 0.0
        for ul in soup.find_all("ul", class_="details-list"):
            chg_val = ""
            bond_val = ""
            warrant_val = ""
            for li in ul.find_all("li"):
                lbl = li.find("label")
                if not lbl:
                    continue
                lbl_txt = lbl.get_text(strip=True).lower()
                val = li.get_text(" ", strip=True).replace(lbl.get_text(" ", strip=True), "").strip()
                if "arrested charge" in lbl_txt and val:
                    chg_val = val
                elif "warrant" in lbl_txt and val:
                    warrant_val = val
                elif "bond" in lbl_txt and val:
                    bond_val = val

            if chg_val:
                label = chg_val
                if warrant_val:
                    label = f"{label} (Warrant: {warrant_val})"
                charges.append(label)
                if bond_val and "$" in bond_val:
                    m_b = re.search(r"[$]\s*([\d,]+(?:\.\d{2})?)", bond_val)
                    if m_b:
                        try:
                            total_bond += float(m_b.group(1).replace(",", ""))
                        except ValueError:
                            pass

        if charges:
            out["charges"] = " | ".join(charges)
        if total_bond > 0:
            out["bond"] = str(int(total_bond) if total_bond == int(total_bond) else f"{total_bond:.2f}")

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
        cleaned = re.sub(r"[\xa0\s]+", " ", raw).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned

    @staticmethod
    def _split_name(full_name: str) -> tuple[str, str]:
        if not full_name:
            return ("", "")
        if "," in full_name:
            parts = [p.strip() for p in full_name.split(",", 1)]
            return (parts[1] if len(parts) > 1 else "", parts[0])
        parts = full_name.split()
        if len(parts) == 1:
            return (parts[0], "")
        return (" ".join(parts[:-1]), parts[-1])

    @staticmethod
    def _clean_dob(raw: str) -> str:
        if not raw:
            return ""
        raw = re.sub(r"\s*\(\d+\)\s*", "", raw).strip()
        m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", raw)
        if m:
            return m.group(1)
        m = re.search(r"([A-Za-z]{3}\s+\d{1,2},\s*\d{4})", raw)
        if m:
            return m.group(1)
        return raw.split()[0] if raw else ""
