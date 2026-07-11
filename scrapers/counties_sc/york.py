"""
York County (SC) Arrest Scraper.
Platform: Custom ASP.NET — inmatesinjail.yorkcountygov.com
"""
import logging, re, time
from datetime import datetime, timezone
from typing import List
import requests
from bs4 import BeautifulSoup
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
PORTAL_URL = "https://inmatesinjail.yorkcountygov.com/detentioncenter/inmatesinjail.aspx"

class YorkScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "York"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        records = []
        start = time.time()
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        try:
            resp = session.get(PORTAL_URL, timeout=15, verify=False)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            table = None
            for t in soup.find_all("table"):
                rows = t.find_all("tr")
                if len(rows) > 2 and len(rows[1].find_all("td")) >= 3:
                    table = t
                    break
            if not table:
                logger.warning("York: No inmate table found")
                return []
            for row in table.find_all("tr")[1:]:
                cells = [td.text.strip() for td in row.find_all("td")]
                if len(cells) < 2:
                    continue
                try:
                    full_name = cells[0] or "Unknown"
                    charge = cells[1] if len(cells) > 1 else "Unknown"
                    booking_date_str = cells[2] if len(cells) > 2 else ""
                    bond_str = cells[3] if len(cells) > 3 else "0"
                    booking_date = datetime.now(timezone.utc)
                    for fmt in ["%m/%d/%Y", "%Y-%m-%d"]:
                        try:
                            booking_date = datetime.strptime(booking_date_str.strip(), fmt).replace(tzinfo=timezone.utc)
                            break
                        except ValueError:
                            continue
                    bond_amount = float(re.sub(r"[^\d.]", "", bond_str) or "0")
                    records.append(ArrestRecord(
                        state="SC", county=self.county, full_name=full_name.title(),
                        charges=[charge], bond_amount=bond_amount,
                        booking_date=booking_date, scraped_at=datetime.now(timezone.utc),
                        source_url=PORTAL_URL,
                    ))
                except Exception as e:
                    logger.debug(f"York row error: {e}")
        except Exception as e:
            logger.error(f"York scrape failed: {e}")
        logger.info(f"York: {len(records)} records in {time.time()-start:.1f}s")
        return records
