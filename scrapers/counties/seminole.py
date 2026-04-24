"""
Seminole County Arrest Scraper — NorthPointe Suite Custody Portal.
Source: https://seminole.northpointesuite.com/custodyportal
Method: Selenium → click searchBtn → wait for results → extract goToDetails JSON
Stack: Selenium (JS-rendered Angular portal)

Ported from swfl-arrest-scrapers/counties/seminole/solver.py (proven working).
"""
import logging
import re
import time
import html
import json
import os
from datetime import datetime, timezone
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PORTAL_URL = "https://seminole.northpointesuite.com/custodyportal"
DETAIL_BASE = "https://seminole.northpointesuite.com/custodyportal/details"
FACILITY = "John E Polk Correctional Facility"
COUNTY = "Seminole"


def _setup_browser():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    chrome_path = os.getenv("CHROME_PATH", "/usr/bin/chromium-browser")
    if os.path.exists(chrome_path):
        options.binary_location = chrome_path

    try:
        from webdriver_manager.chrome import ChromeDriverManager
        from webdriver_manager.core.os_manager import ChromeType
        service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
    except Exception:
        service = Service()

    driver = webdriver.Chrome(service=service, options=options)
    driver.implicitly_wait(10)
    driver.set_page_load_timeout(60)
    return driver


def _extract_records_from_source(page_source: str) -> list:
    decoded = html.unescape(page_source)
    pattern = r"javascript:goToDetails\((\{[^}]+\})\)"
    matches = re.findall(pattern, decoded)
    records = []
    for match in matches:
        try:
            data = json.loads(match)
            first = (data.get("firstName") or "").strip()
            last = (data.get("lastName") or "").strip()
            middle = (data.get("middleName") or "").strip()
            if middle:
                full_name = f"{last}, {first} {middle}".upper()
            else:
                full_name = f"{last}, {first}".upper()
            records.append({
                "full_name": full_name,
                "first_name": first.upper(),
                "middle_name": middle.upper(),
                "last_name": last.upper(),
                "person_id": str(data.get("personId", "")),
                "age": str(data.get("age", "")),
                "sex": (data.get("gender") or ""),
                "race": (data.get("race") or ""),
                "height": (data.get("height") or ""),
                "weight": (data.get("weight") or ""),
            })
        except Exception:
            continue
    return records


def _fetch_detail(driver, person_id: str) -> dict:
    detail = {}
    try:
        driver.get(f"{DETAIL_BASE}/{person_id}")
        time.sleep(2)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(driver.page_source, "html.parser")
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                label = cells[0].get_text(strip=True).lower()
                val = cells[1].get_text(strip=True)
                if "booking" in label and "number" in label:
                    detail["booking_number"] = val
                elif "booking" in label and "date" in label:
                    detail["booking_date"] = val
                elif "agency" in label:
                    detail["agency"] = val
                elif "bond" in label and "total" in label:
                    detail["bond_amount"] = val
                elif "status" in label:
                    detail["status"] = val
        charges = []
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 3:
                charge_text = cells[0].get_text(strip=True)
                if charge_text and len(charge_text) > 3 and "charge" not in charge_text.lower():
                    charges.append(charge_text)
        if charges:
            detail["charges"] = " | ".join(charges[:5])
    except Exception as e:
        logger.debug(f"Seminole detail fetch error for {person_id}: {e}")
    return detail


class SeminoleCountyScraper(BaseScraper):
    """Seminole County — NorthPointe Suite Custody Portal"""

    @property
    def county(self) -> str:
        return "Seminole"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from selenium import webdriver  # noqa
        except ImportError:
            logger.error("Seminole: selenium not installed")
            return []

        driver = _setup_browser()
        records = []

        try:
            logger.info("Seminole: loading custody portal")
            driver.get(PORTAL_URL)
            time.sleep(5)

            try:
                from selenium.webdriver.common.by import By
                from selenium.webdriver.support.ui import WebDriverWait
                from selenium.webdriver.support import expected_conditions as EC

                search_btn = WebDriverWait(driver, 15).until(
                    EC.element_to_be_clickable((By.ID, "searchBtn"))
                )
                search_btn.click()
                time.sleep(5)

                for _ in range(20):
                    if "goToDetails" in driver.page_source:
                        break
                    time.sleep(2)
            except Exception as e:
                logger.warning(f"Seminole: search button error: {e}")

            rows = _extract_records_from_source(driver.page_source)
            logger.info(f"Seminole: found {len(rows)} inmates in portal")

            for row in rows:
                person_id = row["person_id"]
                detail = _fetch_detail(driver, person_id) if person_id else {}

                records.append(ArrestRecord(
                    County=COUNTY,
                    State="FL",
                    Facility=FACILITY,
                    Full_Name=row["full_name"],
                    First_Name=row["first_name"],
                    Middle_Name=row["middle_name"],
                    Last_Name=row["last_name"],
                    Person_ID=person_id,
                    Booking_Number=detail.get("booking_number", person_id),
                    Booking_Date=detail.get("booking_date", ""),
                    Arrest_Date=detail.get("booking_date", ""),
                    Agency=detail.get("agency", "Seminole County SO"),
                    Status=detail.get("status", "In Custody"),
                    Charges=detail.get("charges", ""),
                    Bond_Amount=detail.get("bond_amount", "0"),
                    Race=row["race"],
                    Sex=row["sex"],
                    Height=row["height"],
                    Weight=row["weight"],
                    Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
                    LastChecked=datetime.now(timezone.utc).isoformat(),
                    LastCheckedMode="scrape",
                ))

        except Exception as e:
            logger.error(f"Seminole: scraper error — {e}")
        finally:
            try:
                driver.quit()
            except:
                pass

        logger.info(f"Seminole: total {len(records)} records")
        return records
