"""
Lubbock County (TX) Arrest Scraper — JailAccess / Sheriff Roster REST.
URL: https://co.lubbock.tx.us/sheriff/inmates
"""
import hashlib
import logging
import time
from typing import List, Optional

import requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class LubbockScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Lubbock"

    @property
    def state(self) -> str:
        return "TX"

    def scrape(self) -> List[ArrestRecord]:
        records: List[ArrestRecord] = []
        url = "https://co.lubbock.tx.us/api/v1/inmates"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("inmates", []):
                    b_num = str(item.get("booking_number") or item.get("id") or "")
                    name = str(item.get("full_name") or f"{item.get('last_name')}, {item.get('first_name')}").strip()
                    if not b_num or not name:
                        continue
                    charges = item.get("charges") or []
                    if isinstance(charges, list):
                        chg_strs = [c.get("description", "") if isinstance(c, dict) else str(c) for c in charges]
                    else:
                        chg_strs = [str(charges)]
                    b_amount = float(item.get("bond_amount") or 0.0)
                    records.append(
                        ArrestRecord(
                            booking_number=b_num,
                            county="Lubbock",
                            state="TX",
                            full_name=name,
                            charges=[c for c in chg_strs if c],
                            total_bond_amount=b_amount,
                            facility="Lubbock County Detention Center",
                            scraped_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        )
                    )
        except Exception as e:
            logger.info(f"Lubbock TX scrape info: {e}")
        return records
