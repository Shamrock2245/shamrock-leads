import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})

try:
    print("Step 1: GET root...")
    resp = session.get("https://volusiamug.vcgov.org/", verify=False, timeout=15)
    
    soup = BeautifulSoup(resp.text, 'html.parser')
    viewstate = soup.find('input', {'name': '__VIEWSTATE'}).get('value')
    generator = soup.find('input', {'name': '__VIEWSTATEGENERATOR'}).get('value')
    validation = soup.find('input', {'name': '__EVENTVALIDATION'}).get('value')
    
    payload = {
        "__VIEWSTATE": viewstate,
        "__VIEWSTATEGENERATOR": generator,
        "__EVENTVALIDATION": validation,
        "ButtonAccept": "Accept"
    }
    
    session.post("https://volusiamug.vcgov.org/Disclaimer.aspx", data=payload, verify=False, timeout=15)
    
    detail_url = "https://volusiamug.vcgov.org/Details.aspx?InmateRID=626895"
    detail_resp = session.get(detail_url, verify=False, timeout=15)
    
    detail_soup = BeautifulSoup(detail_resp.text, 'html.parser')
    
    print("\n--- All IMGs on details page ---")
    for img in detail_soup.find_all('img'):
        print(f"src={img.get('src')}, id={img.get('id')}")

except Exception as e:
    print("Error:", e)
