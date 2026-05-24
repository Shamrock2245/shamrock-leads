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
logger = logging.getLogger("test-duval-search")

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
    
    # Try empty search first
    search_btn = page.ele("tag:button@@text():search", timeout=5) or page.ele("tag:button@@text():Search", timeout=5)
    if search_btn:
        logger.info("Found Search button! Clicking it directly to see if empty search is allowed...")
        search_btn.click()
        time.sleep(8)
        
        # Check packets/responses
        packets = page.listen.steps(timeout=5)
        for p in packets:
            if hasattr(p, "response") and p.response:
                logger.info(f"Packet URL: {p.url}, Status: {p.response.status}")
                try:
                    logger.info(f"Packet content snippet: {str(p.response.body)[:500]}")
                except Exception as e:
                    logger.info(f"Error reading body: {e}")
                    
        # Check if table or rows are loaded in DOM
        soup = BeautifulSoup(page.html, "html.parser")
        rows = soup.select("table tr, .inmate-row, .result-row")
        logger.info(f"Found {len(rows)} table/result rows in DOM.")
        
        # If no rows, try entering 'a' in last name
        if len(rows) <= 1:
            logger.info("Empty search did not return rows. Trying to enter 'a' in lastName...")
            last_name_input = page.ele("tag:input@@name=lastName", timeout=5)
            if last_name_input:
                last_name_input.input("a")
                time.sleep(1)
                search_btn.click()
                time.sleep(8)
                
                packets2 = page.listen.steps(timeout=5)
                for p in packets2:
                    if hasattr(p, "response") and p.response:
                        logger.info(f"Packet URL: {p.url}, Status: {p.response.status}")
                        try:
                            logger.info(f"Packet content snippet: {str(p.response.body)[:500]}")
                        except: pass
                
                soup2 = BeautifulSoup(page.html, "html.parser")
                rows2 = soup2.select("table tr, .inmate-row, .result-row")
                logger.info(f"Found {len(rows2)} table/result rows in DOM after searching for 'a'.")
            else:
                logger.warning("Could not find lastName input!")
                
finally:
    page.listen.stop()
    page.quit()
