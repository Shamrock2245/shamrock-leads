"""
Horry County (SC) Arrest Scraper.
"""
import hashlib
import logging
import re
import time
from datetime import timezone
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
        records: List[ArrestRecord] = []
        start = time.time()
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/html, */*",
        })
        try:
            resp = session.get(PORTAL_URL, timeout=20, verify=False)
            resp.raise_for_status()

            # JSON path (Horry sometimes serves JSON)
            try:
                data = resp.json()
                bookings = data if isinstance(data, list) else data.get("bookings", data.get("data", []))
                if isinstance(bookings, list) and bookings and isinstance(bookings[0], dict):
                    for b in bookings:
                        name = b.get("name") or b.get("fullName") or b.get("inmateName") or "Unknown"
                        charge = b.get("charge") or b.get("charges") or b.get("offense") or "Unknown"
                        if isinstance(charge, list):
                            charge = "; ".join(str(c) for c in charge)
                        bond_str = str(b.get("bond") or b.get("bondAmount") or "0")
                        booking_num = str(b.get("bookingNumber") or b.get("id") or "")
                        if not booking_num:
                            booking_num = f"SC_{re.sub(r'[^A-Za-z0-9]', '', name)[:16]}_{int(time.time()) % 100000}"
                        bond = re.sub(r"[^\d.]", "", bond_str) or "0"
                        booking_date = str(b.get("bookingDate") or b.get("arrestDate") or "")
                        records.append(ArrestRecord(
                            County=self.county,
                            State="SC",
                            Full_Name=str(name).title(),
                            Booking_Number=booking_num,
                            Booking_Date=booking_date[:19] if booking_date else "",
                            Charges=str(charge),
                            Bond_Amount=bond,
                            Status="In Custody",
                            Detail_URL=PORTAL_URL,
                        ))
                    logger.info(f"Horry: {len(records)} JSON records in {time.time()-start:.1f}s")
                    return records
            except Exception:
                pass

            soup = BeautifulSoup(resp.text, "html.parser")
            table = None
            for t in soup.find_all("table"):
                rows = t.find_all("tr")
                if len(rows) > 2 and len(rows[1].find_all("td")) >= 2:
                    table = t
                    break
            if not table:
                logger.warning("Horry: No inmate table found")
                return []
            for row in table.find_all("tr")[1:]:
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue
                full_name = cells[0] or "Unknown"
                charge = cells[1] if len(cells) > 1 else "Unknown"
                booking_date_str = cells[2] if len(cells) > 2 else ""
                bond_str = cells[3] if len(cells) > 3 else "0"
                bond = re.sub(r"[^\d.]", "", bond_str) or "0"
                # Deterministic fallback — stable across Python processes (hash() is not)
                booking_num = f"SC_{hashlib.md5(f'{full_name}|{booking_date_str}|HORRY'.encode()).hexdigest()[:10]}"
                records.append(ArrestRecord(
                    County=self.county,
                    State="SC",
                    Full_Name=full_name.title(),
                    Booking_Number=booking_num,
                    Booking_Date=booking_date_str,
                    Charges=charge,
                    Bond_Amount=bond,
                    Status="In Custody",
                    Detail_URL=PORTAL_URL,
                ))
        except Exception as e:
            logger.error(f"Horry scrape failed: {e}")
        logger.info(f"Horry: {len(records)} records in {time.time()-start:.1f}s")
        return records
