"""
Horry County (SC) Arrest Scraper.
Platform: Custom HTML — horrycountysc.gov/apps/bookings
"""
import logging, re, time, json
from datetime import datetime, timezone
from typing import List
import requests
from bs4 import BeautifulSoup
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
PORTAL_URL = "https://www.horrycountysc.gov/apps/bookings"

class HorryScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Horry"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        records = []
        start = time.time()
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/html, */*",
        })
        try:
            resp = session.get(PORTAL_URL, timeout=15)
            resp.raise_for_status()
            # Try JSON first
            try:
                data = resp.json()
                bookings = data if isinstance(data, list) else data.get("bookings", data.get("data", []))
                for b in bookings:
                    name = b.get("name") or b.get("fullName") or b.get("inmateName") or "Unknown"
                    charge = b.get("charge") or b.get("charges") or b.get("offense") or "Unknown"
                    if isinstance(charge, list):
                        charge = "; ".join(str(c) for c in charge)
                    bond_str = str(b.get("bond") or b.get("bondAmount") or "0")
                    booking_date_str = b.get("bookingDate") or b.get("arrestDate") or ""
                    booking_date = datetime.now(timezone.utc)
                    for fmt in ["%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%Y-%m-%d"]:
                        try:
                            booking_date = datetime.strptime(booking_date_str[:19], fmt).replace(tzinfo=timezone.utc)
                            break
                        except (ValueError, TypeError):
                            continue
                    bond_amount = float(re.sub(r"[^\d.]", "", bond_str) or "0")
                    records.append(ArrestRecord(
                        state="SC", county=self.county, full_name=name.title(),
                        charges=[str(charge)], bond_amount=bond_amount,
                        booking_date=booking_date, scraped_at=datetime.now(timezone.utc),
                        source_url=PORTAL_URL,
                    ))
            except (json.JSONDecodeError, AttributeError):
                # Fall back to HTML parsing
                soup = BeautifulSoup(resp.text, "html.parser")
                table = None
                for t in soup.find_all("table"):
                    rows = t.find_all("tr")
                    if len(rows) > 2 and len(rows[1].find_all("td")) >= 3:
                        table = t
                        break
                if table:
                    for row in table.find_all("tr")[1:]:
                        cells = [td.text.strip() for td in row.find_all("td")]
                        if len(cells) < 2:
                            continue
                        records.append(ArrestRecord(
                            state="SC", county=self.county, full_name=cells[0].title(),
                            charges=[cells[1] if len(cells) > 1 else "Unknown"],
                            booking_date=datetime.now(timezone.utc),
                            scraped_at=datetime.now(timezone.utc),
                            source_url=PORTAL_URL,
                        ))
        except Exception as e:
            logger.error(f"Horry scrape failed: {e}")
        logger.info(f"Horry: {len(records)} records in {time.time()-start:.1f}s")
        return records
