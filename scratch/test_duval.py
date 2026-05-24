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
logger = logging.getLogger("test-duval")

class TestDuval(BaseScraper):
    @property
    def county(self): return "Duval"
    def scrape(self): return []

scraper = TestDuval()
co = scraper._get_browser_options()
page = ChromiumPage(co)

try:
    logger.info("Navigating to https://inmatesearch.jaxsheriff.org/ ...")
    page.get("https://inmatesearch.jaxsheriff.org/")
    time.sleep(8)
    
    logger.info(f"Initial Page title: {page.title}")
    
    btn = page.ele("tag:button@@text():I'm not a robot", timeout=5)
    if btn:
        logger.info("Found CAPTCHA verification button! Clicking it...")
        btn.click()
        time.sleep(8)
        
        logger.info(f"Page title after click: {page.title}")
        
        # Check inputs now
        inputs = page.eles("tag:input")
        logger.info(f"Found {len(inputs)} input elements after clicking:")
        for idx, inp in enumerate(inputs):
            logger.info(f"Input {idx}: html='{inp.html[:200]}', type='{inp.attr('type')}', placeholder='{inp.attr('placeholder')}'")
            
        # Check buttons now
        buttons = page.eles("tag:button")
        logger.info(f"Found {len(buttons)} button elements after clicking:")
        for idx, btn in enumerate(buttons):
            logger.info(f"Button {idx}: html='{btn.html[:200]}', text='{btn.text}'")
            
        # BS4 body text
        soup = BeautifulSoup(page.html, "html.parser")
        logger.info("BS4 body structure after click:")
        logger.info(soup.body.get_text(" ", strip=True)[:1000])
    else:
        logger.warning("Could not find the 'I'm not a robot' button!")

finally:
    page.quit()
