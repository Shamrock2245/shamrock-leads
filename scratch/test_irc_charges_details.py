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
    
    # Let's find the Charges heading
    charges_section = None
    for h in soup.find_all(["h2", "h3", "h4", "div", "section"]):
        if h.text and h.text.strip() == "Charges":
            charges_section = h
            break
            
    if not charges_section:
        print("Could not find charges header")
        return
        
    print(f"Found charges header: {charges_section}")
    # Let's print the parent and siblings to understand the container
    parent = charges_section.parent
    print(f"Parent tag name: {parent.name}, class: {parent.get('class')}")
    
    # Print children of the parent to see the structure of charges
    print("\n--- Children of the Parent ---")
    for child in parent.children:
        if child.name:
            print(f"<{child.name} class='{child.get('class')}'>: {child.get_text(' | ', strip=True)[:300]}")
            # If this is a div, let's dump its first few levels of children
            if child.name == "div" or child.name == "section":
                for sub in child.find_all(recursive=False):
                    print(f"  <{sub.name} class='{sub.get('class')}'>: {sub.get_text(' | ', strip=True)[:300]}")
                    for subsub in sub.find_all(recursive=False):
                        print(f"    <{subsub.name} class='{subsub.get('class')}'>: {subsub.get_text(' | ', strip=True)[:300]}")

if __name__ == "__main__":
    main()
