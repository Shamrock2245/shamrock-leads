import time
import sys
import logging
import csv
import os
import re
from DrissionPage import ChromiumPage, ChromiumOptions
from DrissionPage.common import By

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

def is_public_defender(firm_name):
    if not firm_name:
        return False
    name = firm_name.lower()
    blocklist = [
        "public defender",
        "state attorney",
        "attorney general",
        "circuit court",
        "regional counsel",
        "department of",
        "county attorney"
    ]
    return any(b in name for b in blocklist)

def scrape_county(page, county):
    logger.info(f"Navigating to Florida Bar advanced search for {county} County...")
    page.get("https://www.floridabar.org/directories/find-mbr/")
    page.wait.load_start()
    time.sleep(5)

    try:
        loc_type_option = page.ele('text:County', timeout=3)
        if loc_type_option:
            loc_type_option.click()
    except Exception as e:
        logger.warning(f"Could not click 'County' option: {e}")

    loc_input = page.ele('#location-name-input')
    if loc_input:
        loc_input.clear()
        loc_input.input(county)
    else:
        logger.error("Could not find location-name-input")
        return []

    try:
        prac_option = page.ele('text:Criminal Law', timeout=3)
        if prac_option:
            prac_option.click()
    except Exception as e:
        logger.warning(f"Could not click 'Criminal Law' option: {e}")

    submit_btn = page.ele('@type=submit')
    if not submit_btn:
        logger.error("Could not find submit button")
        return []

    submit_btn.click()
    page.wait.load_start()
    time.sleep(5)

    results = []
    page_num = 1

    while True:
        logger.info(f"Scraping page {page_num} for {county} County...")
        profiles = page.eles('.profile-contact')
        if not profiles:
            profiles = page.eles('.profile-name')
            if not profiles:
                logger.info("No profiles found on this page. Stopping.")
                break
            profiles = [p.parent(2) for p in profiles] 

        for p in profiles:
            name_ele = p.ele('.profile-name')
            name = name_ele.text if name_ele else ""
            
            firm = ""
            if "Public Defender" in p.html or "State Attorney" in p.html:
                firm = "Public Defender / State"
            else:
                lines = p.text.split('\n')
                if len(lines) > 1:
                    firm = lines[1]

            email = ""
            email_link = p.ele('tag:a@@href:mailto:')
            if email_link:
                email = email_link.attr('href').replace('mailto:', '').split('?')[0].strip()

            if not email:
                match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', p.text)
                if match:
                    email = match.group(0)

            if not is_public_defender(firm) and not is_public_defender(p.text):
                if name and email:
                    results.append({
                        'County': county,
                        'Firm_Name': firm.strip(),
                        'Name': name.strip(),
                        'Email': email.lower()
                    })

        next_btn = page.ele('.next')
        if not next_btn or "disabled" in next_btn.attr('class'):
            next_link = page.ele('text:Next')
            if not next_link or "disabled" in next_link.parent().attr('class'):
                break
            else:
                next_link.click()
        else:
            next_btn.click()
            
        page.wait.load_start()
        time.sleep(3)
        page_num += 1

    return results

def main():
    counties = ['Lee', 'Charlotte', 'Collier', 'Sarasota']
    
    out_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'scratch')
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, 'florida_bar_attorneys.csv')

    page = create_scraper()
    if not page:
        return

    all_results = []
    
    try:
        for county in counties:
            results = scrape_county(page, county)
            logger.info(f"Finished {county} County: Found {len(results)} attorneys")
            all_results.extend(results)
            time.sleep(5)
    except Exception as e:
        logger.error(f"Error during scraping: {e}")
    finally:
        page.quit()

    if all_results:
        with open(out_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['County', 'Firm_Name', 'Name', 'Email'])
            writer.writeheader()
            seen = set()
            for r in all_results:
                if r['Email'] not in seen:
                    writer.writerow(r)
                    seen.add(r['Email'])
        logger.info(f"Saved {len(seen)} unique attorneys to {out_file}")
    else:
        logger.info("No attorneys found.")

if __name__ == "__main__":
    main()
