import requests
from bs4 import BeautifulSoup

URL = "https://www.leoncountyso.com/About-us/Departments/Detention-Facility/Inmate-search"
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})

print("Fetching page to get tokens...")
r = session.get(URL, verify=False)
soup = BeautifulSoup(r.text, "html.parser")

hidden = {}
for inp in soup.find_all("input", {"type": "hidden"}):
    name = inp.get("name")
    val = inp.get("value", "")
    if name:
        hidden[name] = val

post_data = dict(hidden)
post_data["dnn$ctr633$View$Textbox_633_3"] = "%"
post_data["dnn$ctr633$View$Textbox_633_4"] = "%"
post_data["dnn$ctr633$View$Dropdown_633_5"] = " "
post_data["dnn$ctr633$View$Radiobutton_633_6"] = "Male"
post_data["dnn$ctr633$View$Submitbutton_633_8"] = "Search Roster"

print("Submitting POST...")
resp = session.post(URL, data=post_data, timeout=30, verify=False, allow_redirects=False)
print("Status Code:", resp.status_code)

if resp.status_code == 500:
    res_soup = BeautifulSoup(resp.text, "html.parser")
    print("Page Title:", res_soup.title.text.strip() if res_soup.title else "No Title")
    print("Page body snippet:")
    print(res_soup.get_text('\n', strip=True)[:1000])
