import requests
from bs4 import BeautifulSoup
import urllib3
import re

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
}

def main():
    url = "https://www.ircsheriff.org/booking-details/1725627172"
    resp = requests.get(url, headers=HEADERS, verify=False, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")
    
    # Let's find all headers like h2, h3, h4
    print("--- HEADERS ---")
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        print(f"{h.name}: {h.get_text(strip=True)}")
        
    # Let's print the tables and their structures in detail
    print("\n--- TABLES ---")
    for i, t in enumerate(soup.find_all("table")):
        print(f"\nTable {i}:")
        for r in t.find_all("tr"):
            cols = [c.get_text(strip=True) for c in r.find_all(["td", "th"])]
            print(f"  {cols}")
            
    # Let's print any element that looks like the "Charges" container or contains "Charges"
    print("\n--- CHARGES SECTION ---")
    charges_header = soup.find(lambda tag: tag.name in ["h2", "h3", "h4", "div"] and tag.text and "charges" in tag.text.lower())
    if charges_header:
        print(f"Found charges header/element: {charges_header.name} -> {charges_header.text}")
        # Print next few siblings
        sib = charges_header.next_sibling
        for _ in range(20):
            if not sib:
                break
            if sib.name:
                print(f"Sibling {sib.name}: {sib.get_text(' ', strip=True)[:300]}")
            sib = sib.next_sibling
    else:
        # Search the whole body for text "BATTERY"
        print("Could not find charges header, searching for BATTERY in text")
        battery_nodes = soup.find_all(text=re.compile("BATTERY", re.IGNORECASE))
        for node in battery_nodes:
            parent = node.parent
            print(f"Found battery parent: {parent.name} -> {parent.get_text(' ', strip=True)[:300]}")

if __name__ == "__main__":
    main()
