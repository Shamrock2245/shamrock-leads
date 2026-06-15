import asyncio
import nodriver as uc
from bs4 import BeautifulSoup

async def main():
    browser = None
    try:
        print("Starting nodriver...")
        browser = await uc.start(headless=True)
        print("Loading main page...")
        page = await browser.get("https://cms.revize.com/revize/apps/sarasota/index.php")
        await asyncio.sleep(10)
        
        content = await page.get_content()
        soup = BeautifulSoup(content, 'html.parser')
        title = soup.title.string if soup.title else "No title"
        print(f"Main page title: {title}")
        if "Just a moment" in title:
            print("Failed on main page!")
            return
            
        print("Loading detail page...")
        detail_page = await browser.get("https://cms.revize.com/revize/apps/sarasota/viewInmate.php?id=0115011663")
        await asyncio.sleep(5)
        
        detail_content = await detail_page.get_content()
        dsoup = BeautifulSoup(detail_content, 'html.parser')
        dtitle = dsoup.title.string if dsoup.title else "No title"
        print(f"Detail page title: {dtitle}")
        
        # Check if table exists
        if dsoup.find('table'):
            print("Successfully found table on detail page!")
        else:
            print("No table found!")
            print(dsoup.body.get_text()[:500])
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if browser:
            browser.stop()

if __name__ == "__main__":
    asyncio.run(main())
