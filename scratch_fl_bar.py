from curl_cffi import requests
from bs4 import BeautifulSoup
import sys

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

session = requests.Session(impersonate="chrome")

print("Fetching Florida Bar search page...")
resp = session.get("https://www.floridabar.org/directories/find-mbr/?fName=&lName=&eligible=N&deceased=N&firm=&locValue=Lee&locType=C&pracAreas=C07&lawSchool=&services=&langs=&certifications=&campaigns=&county=LEE&circuit=&practiceAreas=Criminal+Law&sections=", headers=HEADERS)

print("Status:", resp.status_code)
if resp.status_code == 200:
    soup = BeautifulSoup(resp.text, 'html.parser')
    profiles = soup.find_all('li', class_='profile-contact')
    print("Found profiles:", len(profiles))
    
    links = soup.find_all('a', class_='profile-name')
    print("Found links:", len(links))
    for link in links[:5]:
        print(link.get_text(strip=True), link.get('href'))
else:
    print(resp.text[:500])
