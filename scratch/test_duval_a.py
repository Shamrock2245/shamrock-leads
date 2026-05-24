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
logger = logging.getLogger("test-duval-a")

class TestDuval(BaseScraper):
    @property
    def county(self): return "Duval"
    def scrape(self): return []

scraper = TestDuval()
co = scraper._get_browser_options()
page = ChromiumPage(co)

try:
    logger.info("Navigating to https://inmatesearch.jaxsheriff.org/ ...")
    page.listen.start("api")
    page.get("https://inmatesearch.jaxsheriff.org/")
    time.sleep(8)
    
    btn = page.ele("tag:button@@text():I'm not a robot", timeout=5)
    if btn:
        logger.info("Found CAPTCHA verification button! Clicking it...")
        btn.click()
        time.sleep(8)
    
    # Enter 'a' in lastName input
    last_name_input = page.ele("tag:input@@name=lastName", timeout=5)
    if last_name_input:
        logger.info("Entering 'a' in lastName...")
        last_name_input.input("a")
        time.sleep(1)
        
        search_btn = page.ele("tag:button@@text():search", timeout=5) or page.ele("tag:button@@text():Search", timeout=5)
        if search_btn:
            logger.info("Clicking Search button...")
            search_btn.click()
            time.sleep(10)
            
            # Check packets
            packets = page.listen.steps(timeout=5)
            for p in packets:
                if hasattr(p, "response") and p.response:
                    logger.info(f"Packet URL: {p.url}, Status: {p.response.status}")
                    try:
                        logger.info(f"Packet content snippet: {str(p.response.body)[:300]}")
                    except: pass
            
            # Check DOM rows
            soup = BeautifulSoup(page.html, "html.parser")
            rows = soup.select("table tr, .inmate-row, .result-row")
            logger.info(f"Found {len(rows)} table/result rows in DOM.")
            if len(rows) > 0:
                logger.info(f"First row HTML: {rows[0]}")
        else:
            logger.warning("Search button not found!")
    else:
        logger.warning("lastName input not found!")
        
finally:
    page.listen.stop()
    page.quit()
