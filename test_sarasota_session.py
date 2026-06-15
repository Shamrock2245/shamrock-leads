import re
from playwright.sync_api import sync_playwright

def test():
    with sync_playwright() as pw:
        # Launch with headed mode to pass CF potentially, or use stealth args
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = ctx.new_page()
        
        print("Navigating to index.php...")
        page.goto("https://cms.revize.com/revize/apps/sarasota/index.php", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(10000)
        
        print("Title after wait:", page.title())
        
        # Now navigate to detail page
        detail_url = "https://cms.revize.com/revize/apps/sarasota/viewInmate.php?id=0115011663"
        print(f"Navigating to {detail_url} with session...")
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        
        print("Detail Title:", page.title())
        print("Body text snippet:")
        print(page.evaluate("() => document.body.innerText.substring(0, 500)"))

        browser.close()

if __name__ == "__main__":
    test()
