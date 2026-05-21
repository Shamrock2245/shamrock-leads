import requests
import re

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

BASE_URL = "https://www.baysomobile.org/is"
session = requests.Session()
session.headers.update(HEADERS)

resp = session.get(f"{BASE_URL}/", timeout=30)
with open("bay_get_raw.html", "w", encoding="utf-8") as f:
    f.write(resp.text)

print("Saved GET raw HTML to bay_get_raw.html")

# Find all Javascript patterns
scripts = re.findall(r"<script.*?>([\s\S]*?)</script>", resp.text)
print(f"Found {len(scripts)} script tags")

for i, script in enumerate(scripts):
    if "_S_ID" in script or "ajax" in script.lower():
        print(f"\n--- Script {i} contains _S_ID or ajax ---")
        # Print first 500 chars of matching script
        print(script[:1000])
