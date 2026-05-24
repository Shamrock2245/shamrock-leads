import os
import sys
import time
from datetime import datetime, timedelta

# Load dotenv
from dotenv import load_dotenv
load_dotenv()

from DrissionPage import ChromiumPage, ChromiumOptions

# Reuse base scraper browser options
from scrapers.counties.brevard import BrevardCountyScraper
scraper = BrevardCountyScraper()
co = scraper._get_browser_options()
page = ChromiumPage(addr_or_opts=co)

try:
    print("Navigating to Brevard Sheriff main search page...")
    page.get("https://inmatesearch.brevardsheriff.org/")
    time.sleep(5)
    
    to_el = page.ele("#SearchForm_ToDate", timeout=5)
    max_date_str = to_el.attr("max") if to_el else None
    
    if max_date_str:
        print(f"Max date constraint found: {max_date_str}")
        to_date = datetime.strptime(max_date_str, "%Y-%m-%d")
    else:
        to_date = datetime.now() - timedelta(days=1)
        print(f"No max constraint found, defaulting to yesterday: {to_date.strftime('%Y-%m-%d')}")
        
    from_date = to_date - timedelta(days=7)
    
    from_date_str = from_date.strftime("%Y-%m-%d")
    to_date_str = to_date.strftime("%Y-%m-%d")
    
    print(f"Setting dates directly via JS: From={from_date_str}, To={to_date_str}...")
    page.run_js(f"document.getElementById('SearchForm_FromDate').value = '{from_date_str}';")
    page.run_js(f"document.getElementById('SearchForm_ToDate').value = '{to_date_str}';")
    
    # Double check values in DOM
    from_val = page.run_js("return document.getElementById('SearchForm_FromDate').value;")
    to_val = page.run_js("return document.getElementById('SearchForm_ToDate').value;")
    print(f"Verified values in DOM: From={from_val}, To={to_val}")
    
    # Click search button
    submit = page.ele("tag:button@@text():Search", timeout=5)
    if submit:
        print("Clicking Search...")
        submit.click()
        time.sleep(10)  # Wait for page load
        
    print("New URL:", page.url)
    print("New Title:", page.title)
    
    # Save HTML to check results
    os.makedirs("scratch", exist_ok=True)
    html_path = "scratch/brevard_results.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page.html)
    print(f"Saved results HTML to {html_path}")
    
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page.html, "html.parser")
    tables = soup.find_all("table")
    print(f"Tables found: {len(tables)}")
    
    for idx, table in enumerate(tables):
        rows = table.find_all("tr")
        print(f"Table {idx} Rows: {len(rows)}")
        # Print first few rows of data
        for r_idx, row in enumerate(rows[1:5], 1):
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            print(f"  Row {r_idx} Cells: {cells}")
            
except Exception as e:
    print("Error:", e)
finally:
    page.quit()
