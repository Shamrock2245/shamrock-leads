from DrissionPage import ChromiumPage

page = ChromiumPage()
page.get("https://gadsdensheriff.com/inmate-lookup/")
import time
time.sleep(3)
html = page.html
print("Length of HTML:", len(html))
print("Page Title:", page.title)
if "table" in html.lower():
    print("Has table!")
else:
    print("No table found.")
page.quit()
