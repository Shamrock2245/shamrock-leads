"""
Shelby County (TN) Arrest Scraper — Memphis Jail Inmate Lookup (IML).

Portal: https://imljail.shelbycountytn.gov/IML (201 Poplar Jail)
Official source identifier: 8-digit Booking Number (e.g., 26115215).
Contract: Complete displayed name, DOB, Booking Number, Permanent ID, commitment date,
charges with codes/grades, bond amounts, bond types, court dates.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional, Set

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

IML_URL = "https://imljail.shelbycountytn.gov/IML"
FACILITY = "Shelby County Jail (201 Poplar)"
AGENCY = "Shelby County Sheriff's Office"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MAX_SEARCH_PAGES = 5
MAX_DETAIL_FETCHES = 100
REQUEST_PAUSE = 0.15


class ShelbyScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = ""

    @property
    def county(self) -> str:
        return "Shelby"

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

        try:
            # Step 1: Initial GET to establish session cookies
            session.get(IML_URL, timeout=20)

            # Step 2: Blank name search to retrieve active in-custody roster
            search_data = {
                "flow_action": "searchbyname",
                "systemUser_lastName": "",
                "systemUser_firstName": "",
                "systemUser_dateOfBirth": "",
                "systemUser_includereleasedinmate": "N",
                "systemUser_includereleasedinmate2": "N",
            }
            resp = session.post(IML_URL, data=search_data, timeout=30)
            if resp.status_code != 200:
                logger.error(f"Shelby IML search returned HTTP {resp.status_code}")
                return []

            # Parse page 1
            page_inmates = self._parse_listing_page(resp.text)
            all_inmates = list(page_inmates)

            # Step 3: Fetch subsequent pages
            current_start = 31
            for page in range(2, MAX_SEARCH_PAGES + 1):
                try:
                    time.sleep(REQUEST_PAUSE)
                    next_data = {
                        "flow_action": "next",
                        "currentStart": str(current_start),
                    }
                    resp_next = session.post(IML_URL, data=next_data, timeout=30)
                    if resp_next.status_code != 200:
                        break
                    next_inmates = self._parse_listing_page(resp_next.text)
                    if not next_inmates:
                        break
                    all_inmates.extend(next_inmates)
                    current_start += 30
                except Exception as e:
                    logger.warning(f"Shelby IML pagination error page {page}: {e}")
                    break

            logger.info(f"Shelby IML found {len(all_inmates)} listing rows across {MAX_SEARCH_PAGES} pages")

            # Step 4: Build records and enrich with detail
            detail_count = 0
            for item in all_inmates:
                b_num = item["booking_number"]
                if not b_num or b_num in seen:
                    continue
                seen.add(b_num)

                name = item["name"]
                first, last = self._split_name(name)
                sys_id = item["sys_id"]

                charges = "Unknown"
                bond_amt = "0"
                court_date = ""
                commit_date = ""

                if sys_id and detail_count < MAX_DETAIL_FETCHES:
                    try:
                        time.sleep(REQUEST_PAUSE)
                        detail = self._fetch_inmate_detail(session, sys_id)
                        if detail:
                            if detail.get("charges"):
                                charges = detail["charges"]
                            if detail.get("bond"):
                                bond_amt = detail["bond"]
                            if detail.get("court_date"):
                                court_date = detail["court_date"]
                            if detail.get("commitment_date"):
                                commit_date = detail["commitment_date"]
                        detail_count += 1
                    except Exception as de:
                        logger.debug(f"Shelby detail {sys_id}: {de}")

                rec = ArrestRecord(
                    County=self.county,
                    State="TN",
                    Full_Name=name,
                    First_Name=first,
                    Last_Name=last,
                    Booking_Number=str(b_num),
                    Person_ID=str(item.get("perm_id") or b_num),
                    DOB=item.get("dob") or "",
                    Booking_Date=commit_date,
                    Arrest_Date=commit_date,
                    Charges=charges,
                    Bond_Amount=bond_amt,
                    Status="In Custody",
                    Facility=FACILITY,
                    Agency=AGENCY,
                    Court_Date=court_date,
                    Detail_URL=IML_URL,
                    extra_data={
                        "sys_id": sys_id,
                        "perm_id": item.get("perm_id", ""),
                    },
                )
                records.append(rec)

        except Exception as e:
            logger.error(f"Shelby IML scrape failed: {e}")

        logger.info(f"✅ Shelby (TN): {len(records)} records in {time.time() - start:.1f}s")
        return records

    def _parse_listing_page(self, html: str) -> List[Dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        inmates: List[Dict[str, str]] = []

        for tr in soup.find_all("tr"):
            btn = tr.find("a", href=re.compile(r"submitInmate"))
            if not btn:
                continue

            m = re.search(r"submitInmate\('(\d+)'", btn["href"])
            sys_id = m.group(1) if m else ""

            tds = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(tds) < 4:
                continue

            name_raw = tds[0]
            b_num = tds[1]
            perm_id = tds[2] if len(tds) > 2 else ""
            dob = tds[3] if len(tds) > 3 else ""

            # Ensure valid source booking number
            if not b_num or not re.match(r"^\d{6,10}$", b_num):
                continue

            name = self._clean_name(name_raw)
            if not name or len(name) < 2:
                continue

            inmates.append({
                "sys_id": sys_id,
                "name": name,
                "booking_number": b_num,
                "perm_id": perm_id,
                "dob": dob,
            })

        return inmates

    def _fetch_inmate_detail(self, session: requests.Session, sys_id: str) -> Optional[Dict[str, str]]:
        detail_data = {
            "flow_action": "edit",
            "sysID": sys_id,
            "imgSysID": "0",
        }
        resp = session.post(IML_URL, data=detail_data, timeout=25)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        out: Dict[str, str] = {}

        # Parse Commitment Date
        text = soup.get_text()
        m_commit = re.search(r"Commitment Date:\s*([0-9/]+)", text)
        if m_commit:
            out["commitment_date"] = m_commit.group(1).strip()

        # Parse Next Court Date
        m_court = re.search(r"Next Court Date:\s*([0-9/]+\s*[0-9:]*)", text)
        if m_court:
            out["court_date"] = m_court.group(1).strip()

        # Parse Bond Amount (Grand Total preferred, then Total, then Amount)
        m_bond = re.search(r"Grand\s*Total:\s*([\d,]+(?:\.\d{2})?)", text, re.I)
        if not m_bond:
            m_bond = re.search(r"Total:\s*([\d,]+(?:\.\d{2})?)", text, re.I)
        if not m_bond:
            m_bond = re.search(r"Amount:\s*([\d,]+(?:\.\d{2})?)", text, re.I)

        if m_bond:
            out["bond"] = m_bond.group(1).replace(",", "")

        # Parse Charges: look for rows with code and description
        charges: List[str] = []
        for row in soup.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cells) >= 5 and re.match(r"^\d{4,6}$", cells[2]):
                code = cells[2]
                desc = cells[3]
                grade = cells[4]
                c_str = f"{code} {desc}".strip()
                if grade:
                    c_str = f"{c_str} ({grade})"
                if c_str and c_str not in charges:
                    charges.append(c_str)

        if charges:
            out["charges"] = "; ".join(charges)

        return out if out else None

    @staticmethod
    def _clean_name(raw: str) -> str:
        if not raw:
            return ""
        # Strip trailing numbers or weird prefixes like '0, AUSTIN'
        cleaned = re.sub(r"[\xa0\s]+", " ", raw).strip()
        cleaned = re.sub(r"^\d+\s*,\s*", "", cleaned)
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
