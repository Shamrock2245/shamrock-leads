"""
Tuscaloosa County (AL) Arrest Scraper — Tuscaloosa Sheriff Roster.
URL: https://www.tcso.org/inmates
"""
import logging
import time
from typing import List

import requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class TuscaloosaScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Tuscaloosa"

    @property
    def state(self) -> str:
        return "AL"

    def scrape(self) -> List[ArrestRecord]:
        records: List[ArrestRecord] = []
        url = "https://www.tcso.org/api/inmates/roster"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=25)
            if resp.status_code == 200:
                for item in resp.json().get("inmates", []):
                    b_num = str(item.get("booking_number") or item.get("id") or "")
                    name = str(item.get("full_name") or f"{item.get('last_name')}, {item.get('first_name')}").strip()
                    if not b_num or not name:
                        continue
                    records.append(
                        ArrestRecord(
                            booking_number=b_num,
                            county="Tuscaloosa",
                            state="AL",
                            full_name=name,
                            charges=item.get("charges") or [],
                            total_bond_amount=float(item.get("bond_amount") or 0.0),
                            facility="Tuscaloosa County Metro Jail",
                            scraped_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        )
                    )
        except Exception as e:
            logger.info(f"Tuscaloosa AL scrape info: {e}")
        return records
