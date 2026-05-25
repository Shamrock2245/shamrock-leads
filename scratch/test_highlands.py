import os
import time
import json
from DrissionPage import ChromiumPage, ChromiumOptions

def setup_browser():
    co = ChromiumOptions()
    co.auto_port()
    chrome_path = os.getenv("CHROME_PATH")
    if chrome_path:
        co.set_browser_path(chrome_path)
    co.headless(True)
    co.set_argument("--headless=new")
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--window-size=1920,1080")
    co.set_argument("--ignore-certificate-errors")
    co.set_argument("--ignore-ssl-errors")
    return ChromiumPage(co)

print("Starting browser...")
page = setup_browser()
try:
    page.listen.start("api")
    url = "https://www.highlandssheriff.org/inmateSearch"
    print(f"Navigating to {url}...")
    page.get(url)
    print("Waiting 10s for page load...")
    time.sleep(10)
    
    print("Page Title:", page.title)
    print("Body text snippet:")
    print(page.ele("tag:body").text[:1000] if page.ele("tag:body") else "No body text")
    
    # Dump HTML to scratch/highlands.html
    os.makedirs("scratch", exist_ok=True)
    with open("scratch/highlands.html", "w", encoding="utf-8") as f:
        f.write(page.html)
    print("Saved page HTML to scratch/highlands.html")
    
    # Print intercepted requests
    print("Intercepted requests:")
    for pkt in page.listen.steps(timeout=5):
        print(f"Request URL: {pkt.url}")
        if pkt.response:
            print(f"  Response Status: {pkt.response.status_code}")
            try:
                body = pkt.response.body
                print(f"  Response Type: {type(body)}")
                if isinstance(body, str):
                    print(f"  Response snippet: {body[:200]}")
                elif isinstance(body, dict):
                    print(f"  Response keys: {list(body.keys())}")
            except Exception as e:
                print(f"  Error reading body: {e}")
                
except Exception as e:
    print("Error:", e)
finally:
    page.quit()
