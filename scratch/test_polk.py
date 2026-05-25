import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.polksheriff.org"

def check_profile_html():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    booking_num = "2615571"
    profile_url = f"{BASE_URL}/inmate-profile/{booking_num}"
    
    resp = session.get(profile_url, timeout=20, verify=False)
    soup = BeautifulSoup(resp.text, "html.parser")
    
    # Check if there is any text related to charges in the HTML
    text = soup.get_text(" ", strip=True)
    print("Does profile HTML contain 'charge' or 'docket' words?")
    print(f"  Contains 'charge': {'charge' in text.lower()}")
    print(f"  Contains 'docket': {'docket' in text.lower()}")
    print(f"  Contains 'bond': {'bond' in text.lower()}")
    
    # Let's find any tables or divs related to charges
    tables = soup.find_all("table")
    print(f"Found {len(tables)} tables on profile page.")
    for idx, t in enumerate(tables):
        print(f"  Table {idx+1} Text Snippet: {t.get_text(' ', strip=True)[:150]}")

if __name__ == "__main__":
    check_profile_html()
