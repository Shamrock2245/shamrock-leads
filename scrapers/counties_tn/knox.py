"""
Knox County (TN) Arrest Scraper — Sheriff 24-Hour Arrests & Inmate Population.

Portal:
  - https://sheriff.knoxcountytn.gov/index.php   (24-hour arrests feed)
  - https://sheriff.knoxcountytn.gov/inmate.php  (Current inmate population)

Official source identifier: Inmate IDN# (e.g., 1720300).
Contract: Complete displayed name, DOB, IDN#, booked date, charges, bond type/amount, court date.
"""
from __future__ import annotations

import logging
import re
import time
from typing import List, Optional, Set

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

ARREST_24H_URL = "https://sheriff.knoxcountytn.gov/index.php"
INMATE_POP_URL = "https://sheriff.knoxcountytn.gov/inmate.php"
FACILITY = "Knox County Detention Facility"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class KnoxScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = ""

    @property
    def county(self) -> str:
        return "Knox"

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

        # 1) Scrape 24-hour arrests feed (highest priority for bail bonds)
        try:
            r24 = session.get(ARREST_24H_URL, timeout=30)
            if r24.status_code == 200:
                batch_24 = self._parse_knox_html(r24.text, source_url=ARREST_24H_URL)
                for rec in batch_24:
                    if rec.Booking_Number and rec.Booking_Number not in seen:
                        seen.add(rec.Booking_Number)
                        records.append(rec)
                logger.info(f"Knox (TN): 24h arrests parsed {len(batch_24)} records")
        except Exception as e:
            logger.error(f"Knox 24h arrest scrape failed: {e}")

        # 2) Scrape active inmate population default listing for broader coverage
        try:
            r_pop = session.get(INMATE_POP_URL, timeout=30)
            if r_pop.status_code == 200:
                batch_pop = self._parse_knox_html(r_pop.text, source_url=INMATE_POP_URL)
                for rec in batch_pop:
                    if rec.Booking_Number and rec.Booking_Number not in seen:
                        seen.add(rec.Booking_Number)
                        records.append(rec)
                logger.info(f"Knox (TN): inmate pop parsed {len(batch_pop)} records")
        except Exception as e:
            logger.debug(f"Knox inmate pop scrape failed: {e}")

        logger.info(
            f"✅ Knox (TN): {len(records)} records in {time.time() - start:.1f}s"
        )
        return records

    def _parse_knox_html(self, html: str, source_url: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        records: List[ArrestRecord] = []

        # Knox renders triplets of tables per inmate:
        # Table i: Inmate demographic info (Name, DOB, IDN#)
        # Table i+1: Charges and bond info
        # Table i+2: Court dates and hearings
        for i in range(0, len(tables), 3):
            t_info = tables[i]
            rows_info = t_info.find_all("tr")
            if not rows_info:
                continue

            # Header row: Name and DOB
            header_th = [th.get_text(strip=True) for th in rows_info[0].find_all(["th", "td"])]
            name_raw = header_th[0] if len(header_th) > 0 else ""
            dob = ""
            if len(header_th) > 1 and "D.O.B." in header_th[1]:
                dob = header_th[1].replace("D.O.B.", "").strip()

            idn = ""
            for r in rows_info:
                for td in r.find_all("td"):
                    txt = td.get_text(strip=True)
                    if "IDN#:" in txt:
                        idn = txt.replace("IDN#:", "").strip()
                        break
                if idn:
                    break

            if not name_raw or not idn:
                continue

            charges: List[str] = []
            booked_date = ""
            total_bond = 0.0

            if i + 1 < len(tables):
                t_chg = tables[i + 1]
                for r in t_chg.find_all("tr")[1:]:
                    tds = [td.get_text(strip=True) for td in r.find_all("td")]
                    if len(tds) >= 3:
                        b_date = tds[1]
                        chg_desc = tds[2]
                        if chg_desc and chg_desc not in charges:
                            charges.append(chg_desc)
                        if not booked_date and b_date:
                            booked_date = b_date
                    for td in tds:
                        m_b = re.search(r"Bond Amount:\s*[$]?([\d,]+)", td)
                        if m_b:
                            try:
                                total_bond += float(m_b.group(1).replace(",", ""))
                            except ValueError:
                                pass

            court_date = ""
            court_division = ""
            if i + 2 < len(tables):
                t_court = tables[i + 2]
                for r in t_court.find_all("tr")[1:]:
                    tds = [td.get_text(strip=True) for td in r.find_all("td")]
                    if len(tds) >= 2 and tds[0]:
                        court_date = tds[0]
                        court_division = tds[1]
                        break

            name = self._clean_name(name_raw)
            first, last = self._split_name(name)
            charge_str = "; ".join(charges) if charges else "Unknown"
            bond_str = str(int(total_bond) if total_bond == int(total_bond) else f"{total_bond:.2f}")

            rec = ArrestRecord(
                County=self.county,
                State="TN",
                Full_Name=name,
                First_Name=first,
                Last_Name=last,
                Booking_Number=str(idn),
                Person_ID=str(idn),
                DOB=dob,
                Booking_Date=booked_date,
                Arrest_Date=booked_date,
                Charges=charge_str,
                Bond_Amount=bond_str,
                Status="In Custody",
                Facility=FACILITY,
                Court_Date=court_date,
                Detail_URL=source_url,
                Agency="Knox County Sheriff's Office",
                extra_data={
                    "court_division": court_division,
                    "idn": idn,
                },
            )
            records.append(rec)

        return records

    @staticmethod
    def _clean_name(raw: str) -> str:
        if not raw:
            return ""
        cleaned = re.sub(r"[\xa0\s]+", " ", raw).strip()
        return re.sub(r"\s+", " ", cleaned)

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
