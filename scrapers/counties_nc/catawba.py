"""
Catawba County (NC) Arrest Scraper — Who's In Jail HTML table.

URL: https://injail.catawbacountync.gov/whosinjail/

Server-renders full roster. Charge-level rows continue under a name row
(empty name cells). We aggregate charges + bond per inmate.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PORTAL_URL = "https://injail.catawbacountync.gov/whosinjail/"


class CatawbaScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Catawba"

    @property
    def state(self) -> str:
        return "NC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        })
        try:
            resp = session.get(PORTAL_URL, timeout=60, verify=False)
            resp.raise_for_status()
        except Exception as e:
            logger.error("Catawba GET failed: %s", e)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        if not table:
            logger.warning("Catawba: no table found")
            return []

        rows = table.find_all("tr")
        if len(rows) < 2:
            return []

        # Header map
        headers = [c.get_text(" ", strip=True).lower() for c in rows[0].find_all(["th", "td"])]
        def idx(*names):
            for n in names:
                for i, h in enumerate(headers):
                    if n in h:
                        return i
            return -1

        i_name = idx("inmate name", "name")
        i_date = idx("date confined", "confined")
        i_addr = idx("address")
        i_age = idx("age")
        i_charge = idx("charge")
        i_bond = idx("bond")
        i_docket = idx("docket", "court docket")
        i_agency = idx("agency")

        inmates: List[dict] = []
        current: Optional[dict] = None

        for tr in rows[1:]:
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 3:
                continue

            def cell(i: int) -> str:
                return cells[i].strip() if 0 <= i < len(cells) else ""

            name = cell(i_name) if i_name >= 0 else ""
            # Skip picture-only / empty
            if name.lower() in ("view", "picture", ""):
                # might still be a continuation if charge present
                name = ""

            charge = cell(i_charge) if i_charge >= 0 else ""
            bond_raw = cell(i_bond) if i_bond >= 0 else ""
            bond = self._parse_bond(bond_raw)
            docket = cell(i_docket) if i_docket >= 0 else ""
            agency = cell(i_agency) if i_agency >= 0 else ""

            if name and len(name) > 2:
                # New inmate
                last, first, middle = self._split_name(name)
                current = {
                    "name": name,
                    "last": last,
                    "first": first,
                    "middle": middle,
                    "admit": cell(i_date) if i_date >= 0 else "",
                    "address": cell(i_addr) if i_addr >= 0 else "",
                    "age": cell(i_age) if i_age >= 0 else "",
                    "charges": [],
                    "bond": 0.0,
                    "dockets": [],
                    "agency": agency,
                }
                inmates.append(current)

            if not current:
                continue

            if charge and charge not in current["charges"]:
                current["charges"].append(charge)
            if bond > 0:
                current["bond"] += bond
            if docket:
                # first line of docket only
                d0 = docket.split("\n")[0].strip()
                if d0 and d0 not in current["dockets"]:
                    current["dockets"].append(d0)
            if agency and not current.get("agency"):
                current["agency"] = agency

        records = []
        for row in inmates:
            booking = ""
            if row["dockets"]:
                booking = re.sub(r"\s+", "", row["dockets"][0])[:40]
            if not booking:
                key = f"{row['name']}|{row['admit']}|{row['age']}".upper()
                booking = "CAT_" + hashlib.md5(key.encode()).hexdigest()[:12]

            records.append(ArrestRecord(
                County=self.county,
                State="NC",
                Full_Name=row["name"],
                First_Name=row["first"],
                Middle_Name=row["middle"],
                Last_Name=row["last"],
                Booking_Number=booking,
                Case_Number=" | ".join(row["dockets"][:5]),
                Booking_Date=row["admit"],
                Age_At_Arrest=str(row["age"] or ""),
                Address=row.get("address") or "",
                Charges=" | ".join(row["charges"]) if row["charges"] else "Unknown",
                Bond_Amount=f"{row['bond']:.2f}" if row["bond"] else "0",
                Status="In Custody",
                Facility="Catawba County Detention",
                Agency=row.get("agency") or "Catawba County Sheriff",
                Detail_URL=PORTAL_URL,
            ))

        logger.info(
            "Catawba: %d inmates in %.1fs",
            len(records),
            time.time() - start,
        )
        return records

    @staticmethod
    def _parse_bond(raw: str) -> float:
        if not raw:
            return 0.0
        cleaned = re.sub(r"[^\d.]", "", raw.replace(",", ""))
        try:
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            return 0.0

    @staticmethod
    def _split_name(name: str):
        name = re.sub(r"\s+", " ", name).strip()
        if "," in name:
            last, rest = name.split(",", 1)
            parts = rest.strip().split()
            first = parts[0] if parts else ""
            middle = " ".join(parts[1:]) if len(parts) > 1 else ""
            return last.strip(), first, middle
        parts = name.split()
        if len(parts) >= 2:
            return parts[-1], parts[0], " ".join(parts[1:-1])
        return name, "", ""
