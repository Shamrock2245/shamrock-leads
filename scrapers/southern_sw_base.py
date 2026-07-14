"""
Base scraper for Southern Software Citizen Connect.

Current Citizen Connect flow (2026):
  1. GET  /bookingsearch/index.php?AgencyID={agency_id}
  2. Read hidden JMSAgencyID from #formcurrentconfinementsonload
  3. POST /bookingsearch/fetchesforajax/fetch_current_confinements.php
     with JMSAgencyID (+ search/agency/sort)

Returns HTML booking-card fragments with name, booked date, bond, charges.
"""

from __future__ import annotations

import logging
import re
import time
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

CC_BASE = "https://cc.southernsoftware.com/bookingsearch"


class SouthernSWBaseScraper(BaseScraper):
    """Subclasses provide ``county`` and ``agency_id`` (Citizen Connect AgencyID)."""

    @property
    def county(self) -> str:
        raise NotImplementedError("Subclasses must define county name")

    @property
    def agency_id(self) -> str:
        raise NotImplementedError("Subclasses must define AgencyID (e.g., 'HarnettCoNC')")

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        agency = self.agency_id
        index_url = f"{CC_BASE}/index.php?AgencyID={agency}"
        logger.info(f"📥 Southern Software roster for {self.county} ({agency})")

        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        })

        try:
            resp = session.get(index_url, timeout=25)
            if resp.status_code != 200:
                logger.error(f"{self.county}: index HTTP {resp.status_code}")
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            jms = self._extract_jms_agency_id(soup)
            if not jms:
                logger.warning(f"{self.county}: no JMSAgencyID on index; trying agency_id alone")
                jms = agency

            # Paginate current confinements via IDX (20 cards/page)
            records: List[ArrestRecord] = []
            seen: set = set()
            for idx in range(1, 80):  # hard cap ~1600 inmates
                html = self._fetch_endpoint(
                    session,
                    "fetch_current_confinements.php",
                    jms,
                    index_url,
                    idx=idx,
                )
                if not html:
                    break
                page = self._parse_booking_cards(html)
                if not page:
                    break
                new = 0
                for rec in page:
                    key = f"{rec.Full_Name}|{rec.Booking_Date}|{rec.Booking_Number}"
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(rec)
                    new += 1
                if new == 0:
                    break
                time.sleep(0.2)

            if not records:
                # Fallback: last 7 days admits (single page)
                html = self._fetch_endpoint(
                    session, "fetch_last7days.php", jms, index_url, idx=1
                )
                records = self._parse_booking_cards(html) if html else []

            logger.info(
                f"✅ {self.county}: Southern SW {len(records)} records "
                f"in {time.time() - start:.1f}s"
            )
            return records
        except Exception as e:
            logger.error(f"Error scraping Southern SW {self.county}: {e}")
            return []

    @staticmethod
    def _extract_jms_agency_id(soup: BeautifulSoup) -> str:
        tag = soup.find("input", {"name": "JMSAgencyID"}) or soup.find(
            "input", {"id": "JMSAgencyID"}
        )
        if tag and tag.get("value"):
            return tag["value"].strip()
        # Fallback: any NC/SC/GA ORI pattern in page
        m = re.search(r'name="JMSAgencyID"[^>]*value="([^"]+)"', str(soup))
        return m.group(1) if m else ""

    def _fetch_endpoint(
        self,
        session: requests.Session,
        endpoint: str,
        jms: str,
        referer: str,
        idx: int = 1,
    ) -> str:
        url = f"{CC_BASE}/fetchesforajax/{endpoint}"
        data = {
            "JMSAgencyID": jms,
            "search": "",
            "agency": "",
            "sort": "name",
            "IDX": str(idx),
        }
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
            "Origin": "https://cc.southernsoftware.com",
            "Accept": "text/html, */*; q=0.01",
        }
        try:
            resp = session.post(url, data=data, headers=headers, timeout=45)
            if resp.status_code != 200:
                logger.warning(f"{self.county}: {endpoint} HTTP {resp.status_code}")
                return ""
            return resp.text or ""
        except Exception as e:
            logger.warning(f"{self.county}: {endpoint} failed: {e}")
            return ""

    def _parse_booking_cards(self, html: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("div", class_=re.compile(r"booking-card", re.I))
        if not cards:
            # Broader card fallback
            cards = [
                c
                for c in soup.find_all("div", class_=re.compile(r"\bcard\b", re.I))
                if c.find(["h4", "h5"]) and (
                    "Booked" in c.get_text() or "Bond" in c.get_text() or "Charge" in c.get_text()
                )
            ]

        records: List[ArrestRecord] = []
        for card in cards:
            try:
                rec = self._parse_one_card(card)
                if rec:
                    records.append(rec)
            except Exception as e:
                logger.debug(f"{self.county}: card parse error: {e}")
        return records

    def _parse_one_card(self, card) -> Optional[ArrestRecord]:
        # Name is typically first h5 in booking-header
        name_el = card.find(["h5", "h4", "h3"])
        if not name_el:
            return None
        name = name_el.get_text(" ", strip=True)
        if not name or name.lower().startswith("total inmates"):
            return None

        text = card.get_text("\n", strip=True)

        def field(pattern: str) -> str:
            m = re.search(pattern, text, re.I | re.M)
            return m.group(1).strip() if m else ""

        booked = field(r"Booked:\s*([0-9/.\-]+)")
        arrest_dt = field(r"Arrest Date/Time:\s*([^\n]+)")
        agency = field(r"Arresting Agency:\s*([^\n]+)")
        bond_total = field(r"Bond Total:\s*\$?\s*([\d,]+\.?\d*)")
        demo = field(r"Demographics:\s*([^\n]+)")

        # Charges: lines under Charges section / charge-item blocks
        charges: List[str] = []
        for item in card.find_all(class_=re.compile(r"charge-item|charge-details", re.I)):
            t = item.get_text(" ", strip=True)
            # strip leading index numbers
            t = re.sub(r"^\d+\s*", "", t).strip()
            if t and len(t) > 2 and not t.lower().startswith("docket"):
                # Prefer description before bond/docket noise
                t = re.split(r"\bBond:|\bDocket:", t)[0].strip()
                if t:
                    charges.append(t)
        if not charges:
            # Heuristic: lines that look like offense descriptions
            for line in text.splitlines():
                line = line.strip()
                if not line or len(line) < 4:
                    continue
                if any(
                    k in line.lower()
                    for k in (
                        "booked",
                        "demographics",
                        "arresting",
                        "bond total",
                        "view full",
                        "years /",
                        "docket",
                        "charges",
                    )
                ):
                    continue
                if re.match(r"^\$?[\d,]+", line):
                    continue
                if line.isdigit():
                    continue
                if line == name:
                    continue
                if re.search(r"[A-Za-z]{3,}", line):
                    charges.append(line)
                    if len(charges) >= 8:
                        break

        age = race = sex = ""
        if demo:
            # e.g. "47 years / W / F"
            m = re.match(
                r"(\d+)\s*years?\s*/\s*([A-Za-z]+)\s*/\s*([A-Za-z]+)", demo
            )
            if m:
                age, race, sex = m.group(1), m.group(2), m.group(3)

        first = last = middle = ""
        if "," in name:
            last, rest = [p.strip() for p in name.split(",", 1)]
            parts = rest.split()
            first = parts[0] if parts else ""
            middle = " ".join(parts[1:]) if len(parts) > 1 else ""
        else:
            parts = name.split()
            if len(parts) >= 2:
                first = parts[0]
                last = parts[-1]
                middle = " ".join(parts[1:-1]) if len(parts) > 2 else ""
            else:
                last = name

        booking_num = (
            f"SSW_{re.sub(r'[^A-Za-z0-9]', '', last)[:12]}_"
            f"{re.sub(r'[^0-9]', '', booked)[:8]}_"
            f"{age or '0'}"
        )
        bond = re.sub(r"[^\d.]", "", bond_total) or "0"
        if bond == "0":
            # sum individual bond lines
            bonds = re.findall(r"Bond:\s*\$?\s*([\d,]+\.?\d*)", text, re.I)
            total = 0.0
            for b in bonds:
                try:
                    total += float(b.replace(",", ""))
                except ValueError:
                    pass
            if total:
                bond = str(int(total) if total == int(total) else total)

        return ArrestRecord(
            County=self.county,
            State=self.state or "FL",
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=booking_num,
            Booking_Date=booked or (arrest_dt[:10] if arrest_dt else ""),
            Arrest_Date=arrest_dt.split()[0] if arrest_dt else booked,
            Age_At_Arrest=age,
            Race=race,
            Sex=(sex or "")[:1].upper(),
            Agency=agency,
            Charges=" | ".join(charges) if charges else "Unknown",
            Bond_Amount=str(bond),
            Status="In Custody",
            Detail_URL=f"{CC_BASE}/index.php?AgencyID={self.agency_id}",
        )


# Alias used by some county modules
SouthernSoftwareBaseScraper = SouthernSWBaseScraper
