import logging
import os
import sys
import time
from bs4 import BeautifulSoup
from DrissionPage import ChromiumPage

# Add app to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from scrapers.base_scraper import BaseScraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-columbia-a")

class TestColumbia(BaseScraper):
    @property
    def county(self): return "Columbia"
    def scrape(self): return []

scraper = TestColumbia()
co = scraper._get_browser_options()
page = ChromiumPage(co)

try:
    logger.info("Navigating to https://columbiacountyso.policetocitizen.com/Inmates ...")
    page.get("https://columbiacountyso.policetocitizen.com/Inmates")
    time.sleep(8)
    
    # Let's find inputs
    inputs = page.eles("tag:input")
    last_name_input = None
    for inp in inputs:
        html = inp.html.lower()
        if "last" in html or "surname" in html or "last name-input" in inp.attr("id").lower():
            last_name_input = inp
            break
            
    if not last_name_input:
        last_name_input = page.ele("tag:input@@name=Last Name", timeout=5) or page.ele("tag:input@@id=Last name-input-1", timeout=5)
        
    if last_name_input:
        logger.info("Found Last Name input! Entering 'a'...")
        last_name_input.input("a")
        time.sleep(1)
        
        submit = page.ele("tag:input@@type=submit") or page.ele("tag:button@@text():Submit") or page.ele("tag:input@@value=Submit") or page.ele("tag:button@@class*=-button")
        if submit:
            logger.info("Clicking Submit...")
            submit.click()
            time.sleep(10)
            
            soup = BeautifulSoup(page.html, "html.parser")
            logger.info(f"Page text after search: {soup.body.get_text(' ', strip=True)[:1500]}")
            
            tables = soup.find_all("table")
            logger.info(f"Found {len(tables)} tables after 'a' search.")
            for idx, table in enumerate(tables):
                logger.info(f"Table {idx} rows: {len(table.find_all('tr'))}")
                if len(table.find_all('tr')) > 0:
                    logger.info(f"First row HTML snippet: {table.find_all('tr')[0]}")
        else:
            logger.warning("Could not find Submit button!")
    else:
        logger.warning("Could not find Last Name input!")
        
finally:
    page.quit()
