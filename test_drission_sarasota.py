from DrissionPage import ChromiumPage, ChromiumOptions
import time

try:
    co = ChromiumOptions()
    co.headless(True)
    # DrissionPage uses an internal stealth mode automatically
    
    page = ChromiumPage(co)
    print("Navigating to index.php...")
    page.get("https://cms.revize.com/revize/apps/sarasota/index.php")
    
    time.sleep(10)
    
    title = page.title
    print(f"Title: {title}")
    
    if "Just a moment" not in title:
        print("Successfully bypassed CF!")
        
        detail_url = "https://cms.revize.com/revize/apps/sarasota/viewInmate.php?id=0115011663"
        page.get(detail_url)
        time.sleep(5)
        
        if page.ele('tag:table'):
            print("Successfully found table on detail page!")
        else:
            print("No table found!")
    else:
        print("Failed to bypass CF.")
        
except Exception as e:
    print(f"Error: {e}")
finally:
    try:
        page.quit()
    except:
        pass
