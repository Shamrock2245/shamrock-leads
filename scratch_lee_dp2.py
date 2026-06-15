from DrissionPage import ChromiumPage

page = ChromiumPage()
page.get("https://www.sheriffleefl.org/")
print("MAIN TITLE:", page.title)
page.get("https://www.sheriffleefl.org/public-api/bookings?inCustody=true&limit=2")
print("API TITLE:", page.title)
page.quit()
