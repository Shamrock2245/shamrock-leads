from curl_cffi import requests as cf
from bs4 import BeautifulSoup

url = "https://apps.osceola.org/Apps/CorrectionsReports/Report/Daily/"
headers = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

print(f"Fetching daily report page: {url}")
r = cf.get(
    url,
    headers=headers,
    timeout=20,
    impersonate="chrome131",
    verify=False,
)

if r.status_code == 200:
    soup = BeautifulSoup(r.text, 'html.parser')
    print("Page Title:", soup.title.string if soup.title else "No Title")
    
    # Let's find any tables
    tables = soup.find_all('table')
    print(f"Found {len(tables)} tables")
    
    # Print the first table or some rows if found
    for idx, table in enumerate(tables[:1]):
        print(f"\nTable {idx} HTML snippet (first 1000 chars):")
        print(str(table)[:1000])
        
        # Let's print some links
        links = table.find_all('a')
        print(f"\nFound {len(links)} links in Table {idx}:")
        for link in links[:10]:
            print(f"  Href: {link.get('href')}, Text: {link.get_text(strip=True)}")
else:
    print(f"Failed to fetch daily page: {r.status_code}")
