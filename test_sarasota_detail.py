import re
from playwright.sync_api import sync_playwright

def test():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        
        url = "https://cms.revize.com/revize/apps/sarasota/viewInmate.php?id=0115011663"
        print(f"Navigating to {url}")
        
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            
            title = page.title().lower()
            print("Title:", title)
            
            if "just a moment" in title:
                print("Hit Cloudflare on detail page!")
            
            print("Body text snippet:")
            print(page.evaluate("() => document.body.innerText.substring(0, 500)"))
            
            print("Tables found:")
            print(page.evaluate("() => document.querySelectorAll('table').length"))
        except Exception as e:
            print("Error:", e)
        finally:
            browser.close()

if __name__ == "__main__":
    test()
