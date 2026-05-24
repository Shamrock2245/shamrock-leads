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
logger = logging.getLogger("test-columbia-screenshot")

class TestColumbia(BaseScraper):
    @property
    def county(self): return "Columbia"
    def scrape(self): return []

scraper = TestColumbia()
co = scraper._get_browser_options()
page = ChromiumPage(co)

try:
    logger.info("Navigating to Columbia inmate search...")
    page.get("https://columbiacountyso.policetocitizen.com/Inmates")
    page.wait(8)
    
    first_name_input = page.ele("tag:input@@id=First name-input-0", timeout=5) or page.ele("tag:input@@name=First Name", timeout=5)
    last_name_input = page.ele("tag:input@@id=Last name-input-1", timeout=5) or page.ele("tag:input@@name=Last Name", timeout=5)
    
    if not first_name_input or not last_name_input:
        inputs = page.eles("tag:input")
        for inp in inputs:
            html = inp.html.lower()
            if "first" in html: first_name_input = inp
            if "last" in html: last_name_input = inp
            
    if first_name_input and last_name_input:
        logger.info("Found inputs! Inputting 'a' and 'a'...")
        first_name_input.clear()
        first_name_input.input("a")
        time.sleep(0.5)
        last_name_input.clear()
        last_name_input.input("a")
        time.sleep(0.5)
        
        submit = page.ele("tag:input@@type=submit") or page.ele("tag:button@@text():Submit") or page.ele("tag:input@@value=Submit") or page.ele("tag:button@@class*=-button")
        if submit:
            logger.info("Clicking Submit...")
            submit.click()
            page.wait(8)
            
            # Print page text
            soup = BeautifulSoup(page.html, "html.parser")
            logger.info(f"Page text after search: {soup.body.get_text(' ', strip=True)[:1500]}")
            
            # Save screenshot
            screenshot_path = "/app/scratch/columbia_screenshot.png"
            page.get_screenshot(path=screenshot_path, full_page=True)
            logger.info(f"Screenshot saved to {screenshot_path}")
        else:
            logger.warning("Submit button not found!")
    else:
        logger.warning("Inputs not found!")
        
finally:
    page.quit()
