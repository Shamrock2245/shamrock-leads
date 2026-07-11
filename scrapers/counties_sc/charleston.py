"""
Charleston County (SC) Arrest Scraper.
Platform: Custom ASP.NET ViewState form
URL: https://inmatesearch.charlestoncounty.gov/
Approach: POST with ViewState + date range for last 24 hours
"""
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PORTAL_URL = "https://inmatesearch.charlestoncounty.gov/"


class CharlestonScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Charleston"

    @property
    def state(self) -> str:
        return "SC"

    def _get_viewstate(self, session: requests.Session) -> dict:
        """Fetch the page and extract ASP.NET hidden form fields."""
        resp = session.get(PORTAL_URL, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        fields = {}
        for name in ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"]:
            tag = soup.find("input", {"name": name})
            if tag:
                fields[name] = tag.get("value", "")
        return fields

    def scrape(self) -> List[ArrestRecord]:
        records = []
        start_time = time.time()
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": PORTAL_URL,
            "Origin": "https://inmatesearch.charlestoncounty.gov",
        })

        try:
            # Step 1: GET to collect ViewState tokens
            vs = self._get_viewstate(session)
            if not vs.get("__VIEWSTATE"):
                logger.error("Charleston: Failed to extract ViewState")
                return []

            # Step 2: POST — search last 24 hours
            now = datetime.now()
            yesterday = now - timedelta(days=1)
            date_fmt = "%m/%d/%Y"

            payload = {
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                "__VIEWSTATE": vs["__VIEWSTATE"],
                "__VIEWSTATEGENERATOR": vs.get("__VIEWSTATEGENERATOR", ""),
                "__EVENTVALIDATION": vs.get("__EVENTVALIDATION", ""),
                "ctl00$MainContent$txtLastName": "",
                "ctl00$MainContent$txtFirstName": "",
                "ctl00$MainContent$txtBookDtFrom": yesterday.strftime(date_fmt),
                "ctl00$MainContent$txtBookDtTo": now.strftime(date_fmt),
                "ctl00$MainContent$txtArrestDtFrom": "",
                "ctl00$MainContent$txtArrestDtTo": "",
                "ctl00$MainContent$chkSoundex": "on",
                "ctl00$MainContent$txtInmateNumber": "",
                "ctl00$MainContent$btnSearch": "Search",
            }

            resp = session.post(PORTAL_URL, data=payload, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Step 3: Parse results table
            table = soup.find("table", id=re.compile(r"GridView|gvResults|grdInmates", re.I))
            if not table:
                tables = soup.find_all("table")
                for t in tables:
                    headers = [th.text.strip().lower() for th in t.find_all("th")]
                    if any(h in headers for h in ["name", "last name", "inmate"]):
                        table = t
                        break

            if not table:
                logger.warning(f"Charleston: No results table found.")
                return []

            rows = table.find_all("tr")[1:]  # Skip header row
            for row in rows:
                cells = [td.text.strip() for td in row.find_all("td")]
                if len(cells) < 3:
                    continue

                try:
                    full_name = f"{cells[0]}, {cells[1]}" if len(cells) > 1 else cells[0]
                    booking_num = cells[2] if len(cells) > 2 else ""
                    booking_date_str = cells[3] if len(cells) > 3 else ""
                    charge = cells[4] if len(cells) > 4 else "Unknown"
                    bond_str = cells[5] if len(cells) > 5 else "0"

                    booking_date = datetime.now(timezone.utc)
                    for fmt in ["%m/%d/%Y", "%m/%d/%Y %I:%M %p", "%Y-%m-%d"]:
                        try:
                            booking_date = datetime.strptime(booking_date_str.strip(), fmt).replace(tzinfo=timezone.utc)
                            break
                        except ValueError:
                            continue

                    bond_amount = 0.0
                    bond_clean = re.sub(r"[^\d.]", "", bond_str)
                    if bond_clean:
                        bond_amount = float(bond_clean)

                    rec = ArrestRecord(
                        state="SC",
                        county=self.county,
                        full_name=full_name.title(),
                        booking_number=booking_num,
                        charges=[charge],
                        bond_amount=bond_amount,
                        booking_date=booking_date,
                        scraped_at=datetime.now(timezone.utc),
                        source_url=PORTAL_URL,
                    )
                    records.append(rec)
                except Exception as row_err:
                    logger.debug(f"Charleston: Row parse error — {row_err}")
                    continue

        except Exception as e:
            logger.error(f"Charleston scrape failed: {e}")

        elapsed = time.time() - start_time
        logger.info(f"Charleston: {len(records)} records in {elapsed:.1f}s")
        return records
