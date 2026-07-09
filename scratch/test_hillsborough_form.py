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
logger = logging.getLogger("test-hillsborough-form")

class TestHillsborough(BaseScraper):
    @property
    def county(self): return "Hillsborough"
    def scrape(self): return []

scraper = TestHillsborough()
co = scraper._get_browser_options()
page = ChromiumPage(co)

email = os.getenv("HCSO_EMAIL", "admin@shamrockbailbonds.biz")
password = os.getenv("HCSO_PASSWORD") or ""

try:
    logger.info("Navigating to Hillsborough login page...")
    page.get("https://webapps.hcso.tampa.fl.us/arrestinquiry/Account/Login")
    time.sleep(5)
    
    logger.info(f"Login page title: {page.title}")
    
    email_field = page.ele("#Email", timeout=10)
    pwd_field = page.ele("#Password", timeout=5)
    
    if email_field and pwd_field:
        logger.info("Entering credentials...")
        email_field.clear(); email_field.input(email)
        pwd_field.clear(); pwd_field.input(password)
        
        # Try checking for reCAPTCHA
        recaptcha_iframe = page.ele("tag:iframe@@title=reCAPTCHA", timeout=3)
        if recaptcha_iframe:
            logger.info("reCAPTCHA found! Clicking it...")
            checkbox = recaptcha_iframe.ele("tag:div@@class:recaptcha-checkbox-border", timeout=3)
            if checkbox:
                checkbox.click()
                time.sleep(5)
                
        login_btn = page.ele("tag:button@@text():Log in", timeout=5) or page.ele("tag:input@@type=submit", timeout=3)
        if login_btn:
            logger.info("Clicking Log in...")
            login_btn.click()
            time.sleep(8)
            
        logger.info(f"Url after login attempt: {page.url}")
        logger.info(f"Page title after login attempt: {page.title}")
        
        # Let's save html to inspect
        os.makedirs("scratch", exist_ok=True)
        with open("scratch/hillsborough_after_login.html", "w", encoding="utf-8") as f:
            f.write(page.html)
            
        # Navigate to search page
        logger.info("Navigating to Search URL...")
        page.get("https://webapps.hcso.tampa.fl.us/arrestinquiry/Home/Search")
        time.sleep(5)
        
        logger.info(f"Search page title: {page.title}")
        with open("scratch/hillsborough_search_page.html", "w", encoding="utf-8") as f:
            f.write(page.html)
            
        # Inspect inputs on search page
        soup = BeautifulSoup(page.html, "html.parser")
        inputs = soup.find_all("input")
        logger.info(f"Found {len(inputs)} inputs on Search page:")
        for inp in inputs:
            logger.info(f"  Input: name='{inp.get('name')}', id='{inp.get('id')}', type='{inp.get('type')}', value='{inp.get('value')}'")
            
        # Check buttons
        buttons = soup.find_all("button")
        logger.info(f"Found {len(buttons)} buttons on Search page:")
        for btn in buttons:
            logger.info(f"  Button: name='{btn.get('name')}', id='{btn.get('id')}', text='{btn.get_text(strip=True)}'")
            
    else:
        logger.error("Email or Password field not found!")
        
finally:
    page.quit()
