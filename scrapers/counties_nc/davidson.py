"""
Davidson County (NC) Arrest Scraper.

dhtmlxGrid loads: handler/inmate_data.ashx?dynamic=100
Columns: Last, First, Middle, Full Name, Street, City, State, Sex, Age, Height, Weight
"""
from __future__ import annotations

import logging
import re
import time
from typing import List
from xml.etree import ElementTree as ET

import requests

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
DATA_URL = "http://www2.co.davidson.nc.us/DCInmates/handler/inmate_data.ashx?dynamic=100"
PORTAL_URL = "http://www2.co.davidson.nc.us/DCInmates/"


class DavidsonScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Davidson"

    @property
    def state(self) -> str:
        return "NC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        try:
            resp = requests.get(
                DATA_URL,
                timeout=40,
                headers={"User-Agent": "Mozilla/5.0", "Referer": PORTAL_URL},
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for row in root.findall("row"):
                cells = []
                for cell in row.findall("cell"):
                    text = (cell.text or "").strip()
                    # strip HTML anchors from full-name cell
                    text = re.sub(r"<[^>]+>", "", text).strip()
                    cells.append(text)
                if len(cells) < 4:
                    continue
                last, first, middle, full = cells[0], cells[1], cells[2], cells[3]
                name = full or f"{last}, {first} {middle}".strip()
                sex = cells[7] if len(cells) > 7 else ""
                age = cells[8] if len(cells) > 8 else ""
                height = cells[9] if len(cells) > 9 else ""
                weight = cells[10] if len(cells) > 10 else ""
                # cells[11] = booking (e.g. 26-002697); cells[12] = internal id
                booking = cells[11].strip() if len(cells) > 11 else ""
                if not booking:
                    m = re.search(r"(\d{2}-\d{6})", cells[3] if len(cells) > 3 else "")
                    booking = m.group(1) if m else (
                        f"DAV_{re.sub(r'[^A-Za-z0-9]', '', last)[:10]}_{age or '0'}"
                    )
                records.append(ArrestRecord(
                    County=self.county,
                    State="NC",
                    Full_Name=name,
                    First_Name=first,
                    Middle_Name=middle,
                    Last_Name=last,
                    Booking_Number=str(booking),
                    Sex=(sex or "")[:1].upper(),
                    Age_At_Arrest=age,
                    Height=height,
                    Weight=weight,
                    Charges="Unknown",
                    Bond_Amount="0",
                    Status="In Custody",
                    Detail_URL=PORTAL_URL,
                    Facility="Davidson County Detention",
                    City=cells[5] if len(cells) > 5 else "",
                ))
        except Exception as e:
            logger.error(f"Davidson scrape failed: {e}")
        logger.info(f"Davidson: {len(records)} records in {time.time()-start:.1f}s")
        return records
