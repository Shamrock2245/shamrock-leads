import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        await page.goto("https://cms.revize.com/revize/apps/sarasota/index.php", wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)
        
        content = await page.content()
        import re
        match = re.search(r'data-sitekey="([^"]+)"', content)
        if match:
            print("Found sitekey:", match.group(1))
        else:
            print("No data-sitekey found. Looking for other patterns...")
            # Look for any 32-char or similar alphanumeric strings near turnstile
            snippets = re.findall(r'.{0,50}turnstile.{0,50}', content)
            for s in set(snippets):
                print(s)
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
