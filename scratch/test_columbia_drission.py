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
logger = logging.getLogger("test-columbia-drission")

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
    
    logger.info(f"Initial Page title: {page.title}")
    
    # Try finding and clicking the Submit button
    submit = page.ele("tag:input@@type=submit", timeout=5) or page.ele("tag:button@@text():Submit", timeout=5) or page.ele("tag:input@@value=Submit", timeout=5)
    if submit:
        logger.info("Found Submit button! Clicking it...")
        submit.click()
        time.sleep(10)
        
        logger.info(f"Page title after click: {page.title}")
        
        soup = BeautifulSoup(page.html, "html.parser")
        logger.info("BS4 body structure snippet after clicking Submit:")
        logger.info(soup.body.get_text(" ", strip=True)[:1500])
        
        # Search for tables or cards again
        tables = soup.find_all("table")
        logger.info(f"Found {len(tables)} table elements.")
        for idx, table in enumerate(tables):
            logger.info(f"Table {idx} rows count: {len(table.find_all('tr'))}")
            if len(table.find_all('tr')) > 0:
                # Print first row HTML to inspect it
                logger.info(f"First row HTML snippet: {table.find_all('tr')[0]}")
    else:
        logger.warning("Could not find the Submit button!")

finally:
    page.quit()
