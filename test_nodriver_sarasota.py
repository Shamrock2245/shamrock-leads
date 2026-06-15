import asyncio
import nodriver as uc

async def main():
    browser = await uc.start(
        browser_executable_path="/usr/bin/chromium",
        headless=True,
        browser_args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
        ],
    )
    try:
        page = await browser.get("https://cms.revize.com/revize/apps/sarasota/index.php")
        await asyncio.sleep(10)
        content = await page.get_content()
        print("Title:", await page.evaluate("document.title"))
        if "Just a moment" not in await page.evaluate("document.title"):
            print("Successfully bypassed CF!")
            # Try getting the detail page
            detail = await browser.get("https://cms.revize.com/revize/apps/sarasota/viewInmate.php?id=0115011663")
            await asyncio.sleep(5)
            dcontent = await detail.get_content()
            print("Detail Title:", await detail.evaluate("document.title"))
            if "table" in dcontent.lower():
                print("Successfully found table on detail page!")
            else:
                print("No table found!")
        else:
            print("Failed to bypass CF (still Just a moment...)")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        browser.stop()

if __name__ == "__main__":
    asyncio.run(main())
