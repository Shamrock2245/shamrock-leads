import requests
import json
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

url = "https://cdn.myocv.com/ocvapps/a26133870/inmates.json"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

resp = requests.get(url, headers=headers, verify=False, timeout=10)
print("Status Code:", resp.status_code)
if resp.status_code == 200:
    data = resp.json()
    print("Keys in root:", list(data.keys()) if isinstance(data, dict) else "List")
    
    items = data.get("Information", []) if isinstance(data, dict) else data
    print("Total items:", len(items))
    if len(items) > 0:
        print("First record keys:")
        first_item = items[0]
        print(json.dumps(first_item, indent=2))
        
        # Look for charges or bonds
        print("\nChecking for charges/bonds in first few records:")
        for idx, it in enumerate(items[:5]):
            print(f"Record {idx+1}: name={it.get('Name')}, booking={it.get('BookingNumber')}, charges={it.get('Charges')[:100] if it.get('Charges') else 'None'}")
