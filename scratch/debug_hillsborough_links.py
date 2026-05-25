import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

url = "https://webapps.hcso.tampa.fl.us/arrestinquiry/"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
}

try:
    print("Fetching main page...")
    resp = requests.get(url, headers=headers, verify=False, timeout=15)
    print(f"Status: {resp.status_code}")
    
    soup = BeautifulSoup(resp.text, "html.parser")
    
    print("\n--- Links found ---")
    for a in soup.find_all("a", href=True):
        print(f"Text: '{a.get_text(strip=True)}', href: '{a['href']}'")
        
    print("\n--- Forms found ---")
    for form in soup.find_all("form"):
        print(f"Action: '{form.get('action')}', method: '{form.get('method')}'")
        for inp in form.find_all("input"):
            print(f"  Input: name='{inp.get('name')}', type='{inp.get('type')}', id='{inp.get('id')}'")
            
except Exception as e:
    print(f"Error: {e}")
