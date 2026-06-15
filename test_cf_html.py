import requests

resp = requests.get(
    "https://cms.revize.com/revize/apps/sarasota/index.php",
    headers={"User-Agent": "Mozilla/5.0"}
)
print(f"Status: {resp.status_code}")
content = resp.text
print(f"Contains turnstile: {'turnstile' in content.lower()}")
print(f"Contains sitekey: {'sitekey' in content.lower()}")
if 'data-sitekey' in content:
    import re
    match = re.search(r'data-sitekey="([^"]+)"', content)
    if match:
        print("Found sitekey:", match.group(1))

# Search for sitekey in JS
import re
match2 = re.search(r"sitekey['\"\]\s:]+([A-Za-z0-9_-]+)", content)
if match2:
    print("Found sitekey (regex):", match2.group(1))
