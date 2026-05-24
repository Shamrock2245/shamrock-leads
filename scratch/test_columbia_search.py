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
logger = logging.getLogger("test-columbia-search")

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
    
    # List all inputs
    inputs = page.eles("tag:input")
    logger.info(f"Found {len(inputs)} input elements on initial load:")
    for idx, inp in enumerate(inputs):
        logger.info(f"Input {idx}: html='{inp.html[:200]}', id='{inp.attr('id')}', name='{inp.attr('name')}', type='{inp.attr('type')}', label='{inp.attr('aria-label')}'")
        
    # Find firstName and lastName fields (or similarly named fields)
    first_name_input = page.ele("@placeholder=First name") or page.ele("tag:input@@id=firstName") or page.ele("tag:input@@name=firstName")
    last_name_input = page.ele("@placeholder=Last name") or page.ele("tag:input@@id=lastName") or page.ele("tag:input@@name=lastName")
    
    if not first_name_input:
        # Let's search by class or other placeholder
        for inp in inputs:
            html = inp.html.lower()
            if "first" in html:
                first_name_input = inp
            if "last" in html:
                last_name_input = inp
                
    if first_name_input and last_name_input:
        logger.info("Found first and last name inputs!")
        
        # Test 1: Enter '%' in both (or just a common character)
        logger.info("Entering '%' in both First and Last Name...")
        first_name_input.input("%")
        last_name_input.input("%")
        time.sleep(1)
        
        submit = page.ele("tag:input@@type=submit") or page.ele("tag:button@@text():Submit") or page.ele("tag:input@@value=Submit")
        if submit:
            logger.info("Clicking Submit with '%' search...")
            submit.click()
            time.sleep(8)
            
            soup = BeautifulSoup(page.html, "html.parser")
            logger.info(f"Page text after search: {soup.body.get_text(' ', strip=True)[:1000]}")
            tables = soup.find_all("table")
            logger.info(f"Found {len(tables)} tables after '%' search.")
            
            # If no tables, clear and try 'a' in both
            if len(tables) == 0:
                logger.info("Trying clear and enter 'a' in both...")
                # Refresh page to be clean
                page.get("https://columbiacountyso.policetocitizen.com/Inmates")
                time.sleep(5)
                
                first_name_input = page.ele("@placeholder=First name") or page.ele("tag:input@@id=firstName") or page.ele("tag:input@@name=firstName")
                last_name_input = page.ele("@placeholder=Last name") or page.ele("tag:input@@id=lastName") or page.ele("tag:input@@name=lastName")
                
                if first_name_input and last_name_input:
                    first_name_input.input("a")
                    last_name_input.input("a")
                    time.sleep(1)
                    submit = page.ele("tag:input@@type=submit") or page.ele("tag:button@@text():Submit") or page.ele("tag:input@@value=Submit")
                    if submit:
                        submit.click()
                        time.sleep(8)
                        soup = BeautifulSoup(page.html, "html.parser")
                        logger.info(f"Page text after 'a' search: {soup.body.get_text(' ', strip=True)[:1000]}")
                        tables = soup.find_all("table")
                        logger.info(f"Found {len(tables)} tables after 'a' search.")
        else:
            logger.warning("Submit button not found!")
    else:
        logger.warning(f"Could not find first/last name inputs! first_name={first_name_input}, last_name={last_name_input}")
        
finally:
    page.quit()
