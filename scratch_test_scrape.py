import time
import sys
import logging
import csv
import os
import re
from DrissionPage import ChromiumPage, ChromiumOptions

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def create_scraper():
    co = ChromiumOptions()
    co.headless(True)
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_user_agent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
    try:
        page = ChromiumPage(addr_or_opts=co)
        return page
    except Exception as e:
        logger.error(f"Failed to start DrissionPage: {e}")
        return None

def test_county(page, county):
    logger.info(f"Testing advanced search for {county} County...")
    page.get("https://www.floridabar.org/directories/find-mbr/")
    page.wait.load_start()
    time.sleep(3)

    loc_type = page.ele('#location-type-selector')
    if loc_type:
        loc_type.select('County')

    loc_input = page.ele('#location-name-input')
    if loc_input:
        loc_input.input(county)

    prac_select = page.ele('#asmSelect1')
    if prac_select:
        prac_select.select('Criminal Law')

    submit_btn = page.ele('@type=submit')
    if submit_btn:
        submit_btn.click()
    
    page.wait.load_start()
    time.sleep(5)
    
    profiles = page.eles('.profile-name')
    logger.info(f"Found {len(profiles)} profile names on page 1")
    for p in profiles[:2]:
        logger.info(f"Profile: {p.text}")
        parent = p.parent(2)
        logger.info(f"Text: {parent.text[:100]}")

def main():
    page = create_scraper()
    if page:
        try:
            test_county(page, 'Lee')
        finally:
            page.quit()

if __name__ == "__main__":
    main()
