from playwright.sync_api import sync_playwright
import time

def test():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        print("Navigating to Revize CMS...")
        page.goto("https://cms.revize.com/revize/apps/sarasota/index.php", wait_until="networkidle")
        time.sleep(5)
        print("Title:", page.title())
        page.screenshot(path="sarasota_revize.png")
        print("Screenshot saved to sarasota_revize.png")
        
        # get all text
        print("Body text snippet:")
        print(page.evaluate("() => document.body.innerText.substring(0, 500)"))

        browser.close()

if __name__ == "__main__":
    test()
