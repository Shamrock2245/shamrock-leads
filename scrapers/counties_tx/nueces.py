"""
Nueces County (TX) Arrest Scraper — Corpus Christi / Nueces Sheriff Roster.
URL: https://www.nuecesco.com/sheriff/inmates
"""
import logging
import time
from typing import List

import requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class NuecesScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Nueces"

    @property
    def state(self) -> str:
        return "TX"

    def scrape(self) -> List[ArrestRecord]:
        records: List[ArrestRecord] = []
        url = "https://www.nuecesco.com/api/inmates/current"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=25)
            if resp.status_code == 200:
                for item in resp.json():
                    b_num = str(item.get("booking_number") or item.get("id") or "")
                    name = str(item.get("full_name") or f"{item.get('last_name')}, {item.get('first_name')}").strip()
                    if not b_num or not name:
                        continue
                    records.append(
                        ArrestRecord(
                            booking_number=b_num,
                            county="Nueces",
                            state="TX",
                            full_name=name,
                            charges=item.get("charges") or [],
                            total_bond_amount=float(item.get("bond_amount") or 0.0),
                            facility="Nueces County Jail",
                            scraped_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        )
                    )
        except Exception as e:
            logger.info(f"Nueces TX scrape info: {e}")
        return records
