"""
Martin County Arrest Scraper — martinso.us Inmate Search.
Source: https://www.martinso.us/inmatesearch/
Method: DrissionPage (Cloudflare-protected site)
Stack: DrissionPage browser

Ported from swfl-arrest-scrapers/counties/martin/solver.py (proven working).
Old URL https://www.mcsofl.org/223/Jail-Inmate-Search returned 404.
"""
import logging
import re
import time
import os
from datetime import datetime, timezone
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

SEARCH_URLS = [
    "https://www.martinso.us/inmatesearch/",
    "https://www.mcsofl.org/224/Recent-Bookings",
    "https://www.martinso.us/arrests/",
]
FACILITY = "Martin County Jail"
COUNTY = "Martin"


def _setup_browser():
    from DrissionPage import ChromiumPage, ChromiumOptions
    co = ChromiumOptions()
    co.auto_port()
    chrome_path = os.getenv("CHROME_PATH")
    if chrome_path:
        co.set_browser_path(chrome_path)
    co.headless(True)
    co.set_argument("--headless=new")
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--window-size=1920,1080")
    co.set_user_agent(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    )
    return ChromiumPage(co)


def _parse_name(full_name: str):
    first, middle, last = "", "", ""
    if not full_name:
        return first, middle, last
    if "," in full_name:
        parts = full_name.split(",", 1)
        last = parts[0].strip()
        remainder = parts[1].strip()
        if " " in remainder:
            r_parts = remainder.split(" ", 1)
            first = r_parts[0].strip()
            middle = r_parts[1].strip()
        else:
            first = remainder
    else:
        parts = full_name.split()
        last = parts[-1] if parts else ""
        first = parts[0] if len(parts) > 1 else ""
        middle = " ".join(parts[1:-1]) if len(parts) > 2 else ""
    return first, middle, last


class MartinCountyScraper(BaseScraper):
    """Martin County — martinso.us Inmate Search (DrissionPage)"""

    @property
    def county(self) -> str:
        return "Martin"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage  # noqa
        except ImportError:
            logger.error("Martin: DrissionPage not installed")
            return []

        page = _setup_browser()
        records = []

        try:
            loaded = False
            for url in SEARCH_URLS:
                try:
                    logger.info(f"Martin: trying {url}")
                    page.get(url)
                    time.sleep(5)

                    for attempt in range(10):
                        title = page.title or ""
                        if "just a moment" in title.lower():
                            logger.info(f"Martin: waiting for Cloudflare ({attempt+1}/10)")
                            time.sleep(3)
                        else:
                            break

                    body_text = page.ele("tag:body").text if page.ele("tag:body") else ""
                    if len(body_text) > 200 and "just a moment" not in body_text.lower():
                        loaded = True
                        logger.info(f"Martin: loaded {url}")
                        break
                except Exception as e:
                    logger.warning(f"Martin {url}: {e}")

            if not loaded:
                logger.error("Martin: all URLs failed")
                return []

            # Accept disclaimer or click search
            for btn_text in ["Search", "View Inmates", "Continue", "Accept", "I Agree"]:
                try:
                    btn = page.ele(f"tag:button@@text():{btn_text}", timeout=2)
                    if btn:
                        btn.click()
                        time.sleep(3)
                        break
                except:
                    pass

            # Parse HTML
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(page.html, "html.parser")

            # Try table first
            table = soup.find("table")
            if table:
                rows = table.find_all("tr")
                for row in rows[1:]:
                    cells = row.find_all("td")
                    if len(cells) < 2:
                        continue
                    texts = [c.get_text(strip=True) for c in cells]
                    full_name = texts[0] if texts else ""
                    if not full_name or full_name.lower() in ("name", "inmate"):
                        continue
                    booking_num = texts[1] if len(texts) > 1 else ""
                    booking_date = texts[2] if len(texts) > 2 else ""
                    charges = texts[3] if len(texts) > 3 else ""
                    bond_raw = texts[4] if len(texts) > 4 else "0"
                    bond_m = re.search(r"([\d,]+(?:\.\d{2})?)", bond_raw)
                    bond = bond_m.group(1).replace(",", "") if bond_m else "0"
                    link = row.find("a", href=True)
                    detail_url = ""
                    if link:
                        href = link["href"]
                        detail_url = href if href.startswith("http") else f"https://www.martinso.us{href}"
                    first, middle, last = _parse_name(full_name)
                    records.append(ArrestRecord(
                        County=COUNTY, State="FL", Facility=FACILITY,
                        Full_Name=full_name.upper(), First_Name=first.upper(),
                        Middle_Name=middle.upper(), Last_Name=last.upper(),
                        DOB="",
                        Booking_Number=booking_num, Booking_Date=booking_date,
                        Arrest_Date=booking_date, Charges=charges, Bond_Amount=bond,
                        Detail_URL=detail_url, Status="In Custody",
                        Release_Date="",
                        Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
                        LastChecked=datetime.now(timezone.utc).isoformat(),
                        LastCheckedMode="scrape",
                    ))

            # Fallback: cards/links
            if not records:
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    text = a.get_text(strip=True)
                    if re.match(r"[A-Z][A-Z\s,]+", text) and len(text) > 5:
                        full_name = text
                        first, middle, last = _parse_name(full_name)
                        detail_url = href if href.startswith("http") else f"https://www.martinso.us{href}"
                        records.append(ArrestRecord(
                            County=COUNTY, State="FL", Facility=FACILITY,
                            Full_Name=full_name.upper(), First_Name=first.upper(),
                            Middle_Name=middle.upper(), Last_Name=last.upper(),
                        DOB="",
                            Detail_URL=detail_url, Status="In Custody",
                        Release_Date="",
                            Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
                            LastChecked=datetime.now(timezone.utc).isoformat(),
                            LastCheckedMode="scrape",
                        ))

        except Exception as e:
            logger.error(f"Martin: scraper error — {e}")
        finally:
            try:
                page.quit()
            except:
                pass

        logger.info(f"Martin: total {len(records)} records")
        return records
