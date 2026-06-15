import asyncio
from playwright.async_api import async_playwright
import playwright_stealth

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            '--disable-blink-features=AutomationControlled',
            '--disable-features=IsolateOrigins,site-per-process',
        ])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()
        await playwright_stealth.stealth_sync(page) if hasattr(playwright_stealth, 'stealth_sync') else None
        
        print("Loading main page...")
        await page.goto("https://cms.revize.com/revize/apps/sarasota/index.php", wait_until="domcontentloaded")
        await page.wait_for_timeout(10000)
        
        title = await page.title()
        print(f"Title: {title}")
        if "Just a moment" in title:
            print("Still blocked on main page.")
            content = await page.content()
            if "cf-turnstile" in content:
                print("CF Turnstile widget found.")
        else:
            print("Bypassed CF!")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
