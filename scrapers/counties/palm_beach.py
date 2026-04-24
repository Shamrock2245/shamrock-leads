"""
Palm Beach County Arrest Scraper — PBSO Booking Blotter.
Source: https://www3.pbso.org/blotter/index.cfm
Method: DrissionPage → date search form → paginate → parse result cards
Stack: DrissionPage (browser required — site uses JS-rendered result cards)

Ported from swfl-arrest-scrapers/counties/palm_beach/solver.py (proven working).
"""
import logging
import re
import time
import os
from datetime import datetime, timedelta, timezone
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BLOTTER_URL = "https://www3.pbso.org/blotter/index.cfm"
FACILITY = "Palm Beach County Jail"
COUNTY = "Palm Beach"


def _clean(text: str) -> str:
    if not text:
        return ""
    return " ".join(text.strip().split())


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
        last = full_name
    return first, middle, last


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
    co.set_argument("--disable-gpu")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--window-size=1920,1080")
    co.set_user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    return ChromiumPage(addr_or_opts=co)


def _click_next_page(page) -> bool:
    try:
        for link in page.eles("xpath://a"):
            text = link.text.strip()
            if text in ("»", ">", ">>") and "last" not in link.text.lower():
                link.click()
                return True
        page_info = page.ele("xpath://*[contains(text(), 'Page ')]")
        if page_info:
            m = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", page_info.text)
            if m:
                current, total = int(m.group(1)), int(m.group(2))
                if current >= total:
                    return False
                next_link = page.ele(f'xpath://a[normalize-space(text())="{current + 1}"]')
                if next_link:
                    next_link.click()
                    return True
    except Exception as e:
        logger.debug(f"Pagination error: {e}")
    return False


def _parse_list_row(row) -> dict:
    def get_val(label):
        try:
            strong = row.ele(f'xpath:.//strong[contains(text(), "{label}")]')
            if strong:
                text = strong.parent().text
                val = text.split(label, 1)[-1].strip()
                return _clean(val)
        except:
            pass
        return ""

    full_name = get_val("Name:")
    race = get_val("Race:")
    sex = get_val("Gender:")
    facility = get_val("Facility:") or FACILITY
    agency = get_val("Arresting Agency:") or "PBSO"
    jacket_num = get_val("Jacket Number:")
    booking_dt_str = get_val("Booking Date/Time:")
    release_date_str = get_val("Release Date:")

    # Booking number from onclick link
    booking_num = ""
    try:
        link_ele = row.ele("css:a[onclick*='loaddetail']")
        if link_ele:
            booking_num = _clean(link_ele.text)
        else:
            link_ele = row.ele("xpath:.//a[contains(@href, 'booking')]")
            if link_ele:
                booking_num = _clean(link_ele.text)
    except:
        booking_num = get_val("Booking Number:")

    # Mugshot
    mug_url = ""
    try:
        img = row.ele("css:img")
        if img:
            src = img.attr("src") or ""
            if src and "noimage" not in src.lower():
                if not src.startswith("http"):
                    src = f"https://www3.pbso.org{src}"
                mug_url = src
    except:
        pass

    first_name, middle_name, last_name = _parse_name(full_name)

    booking_date, booking_time = "", ""
    if booking_dt_str:
        try:
            dt = datetime.strptime(booking_dt_str.strip(), "%m/%d/%Y %H:%M")
            booking_date = dt.strftime("%Y-%m-%d")
            booking_time = dt.strftime("%H:%M:00")
        except:
            booking_date = booking_dt_str.strip()

    status = "In Custody"
    if release_date_str and "N/A" not in release_date_str and release_date_str.strip():
        status = "Released"

    # Charges and bond
    charges = []
    total_bond = 0.0
    try:
        inner_rows = row.eles("css:div.row")
        for ir in inner_rows:
            txt = ir.text.strip().replace("\n", " ")
            if re.search(r"\d+\.\d+", txt) and "Booking" not in txt and "OBTS" not in txt:
                charge_part = txt
                for splitter in ["Original Bond", "Current Bond", "Bond Information"]:
                    if splitter in charge_part:
                        charge_part = charge_part.split(splitter)[0]
                charge_part = charge_part.strip()
                if charge_part:
                    charges.append(charge_part)
        bond_matches = re.findall(r"Current Bond:\s*\$([0-9,]+(?:\.\d{2})?)", row.text or "")
        for amt in bond_matches:
            try:
                total_bond += float(amt.replace(",", ""))
            except:
                pass
    except:
        pass

    return {
        "full_name": full_name,
        "first_name": first_name,
        "middle_name": middle_name,
        "last_name": last_name,
        "booking_num": booking_num,
        "jacket_num": jacket_num,
        "race": race,
        "sex": sex,
        "facility": facility,
        "agency": agency,
        "booking_date": booking_date,
        "booking_time": booking_time,
        "status": status,
        "charges": " | ".join(charges),
        "bond_amount": f"{total_bond:.2f}" if total_bond > 0 else "0",
        "mug_url": mug_url,
    }


def _search_and_collect(page, target_date: str, max_pages: int = 50) -> list:
    logger.info(f"Palm Beach: searching {target_date}")
    page.get(BLOTTER_URL)
    time.sleep(3)

    # Handle hCaptcha
    try:
        if page.ele("tag:iframe[src*='hcaptcha.com']"):
            logger.warning("Palm Beach: hCaptcha detected — waiting 30s")
            time.sleep(30)
    except:
        pass

    if not page.wait.ele_displayed("#start_date", timeout=15):
        logger.error("Palm Beach: search form did not load")
        return []

    start_input = page.ele("#start_date")
    end_input = page.ele("#end_date")
    start_input.clear()
    start_input.input(target_date)
    if end_input:
        end_input.clear()
        end_input.input(target_date)

    submit_btn = page.ele("#process") or page.ele("css:input[type=submit]")
    if not submit_btn:
        logger.error("Palm Beach: submit button not found")
        return []

    submit_btn.click()
    time.sleep(5)

    all_rows = []
    current_page = 1

    while current_page <= max_pages:
        if not page.wait.ele_displayed("css:div[id^='allresults_']", timeout=10):
            page_text = page.text or ""
            if "0 matches" in page_text or "no results" in page_text.lower():
                logger.info(f"Palm Beach: no results for {target_date}")
            break

        results = page.eles("css:div[id^='allresults_']")
        logger.info(f"Palm Beach: page {current_page} → {len(results)} records")

        for row in results:
            try:
                data = _parse_list_row(row)
                if data and data.get("booking_num"):
                    all_rows.append(data)
            except Exception as e:
                logger.debug(f"Palm Beach: row parse error: {e}")

        if not _click_next_page(page):
            break
        current_page += 1
        time.sleep(3)

    logger.info(f"Palm Beach: collected {len(all_rows)} records for {target_date}")
    return all_rows


class PalmBeachCountyScraper(BaseScraper):
    """Palm Beach County — PBSO Blotter (www3.pbso.org)"""

    @property
    def county(self) -> str:
        return "Palm Beach"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage  # noqa
        except ImportError:
            logger.error("Palm Beach: DrissionPage not installed")
            return []

        page = _setup_browser()
        records = []

        try:
            # Search last 2 days to catch overnight bookings
            for i in range(1, -1, -1):
                target_date = (datetime.now() - timedelta(days=i)).strftime("%m/%d/%Y")
                rows = _search_and_collect(page, target_date, max_pages=50)

                for row in rows:
                    records.append(ArrestRecord(
                        County=COUNTY,
                        State="FL",
                        Facility=row["facility"],
                        Agency=row["agency"],
                        Full_Name=row["full_name"],
                        First_Name=row["first_name"],
                        Middle_Name=row["middle_name"],
                        Last_Name=row["last_name"],
                        Booking_Number=row["booking_num"],
                        Person_ID=row["jacket_num"],
                        Race=row["race"],
                        Sex=row["sex"],
                        Booking_Date=row["booking_date"],
                        Booking_Time=row["booking_time"],
                        Arrest_Date=row["booking_date"],
                        Arrest_Time=row["booking_time"],
                        Status=row["status"],
                        Charges=row["charges"],
                        Bond_Amount=row["bond_amount"],
                        Mugshot_URL=row["mug_url"],
                        Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
                        LastChecked=datetime.now(timezone.utc).isoformat(),
                        LastCheckedMode="scrape",
                    ))

        except Exception as e:
            logger.error(f"Palm Beach: scraper error — {e}")
        finally:
            try:
                page.quit()
            except:
                pass

        logger.info(f"Palm Beach: total {len(records)} records")
        return records
