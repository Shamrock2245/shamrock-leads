import requests
import json

URL = "https://api.correctionsrecordssearch.com/instances/01K343RER5XCX3V9KQA5876BE8/inmates?page-size=50&page-number=1&sort-by=arrestDate&sort=desc"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://correctionsrecordssearch.com"
}

print(f"Direct API GET from: {URL}")
resp = requests.get(URL, headers=headers, timeout=20)
print("Status Code:", resp.status_code)

if resp.status_code == 200:
    data = resp.json()
    print("Success! Keys in response:", data.keys() if isinstance(data, dict) else "List response")
    
    # Dump first record to see the structure
    items = data.get("inmates", []) if isinstance(data, dict) else data
    print(f"Total items in page: {len(items)}")
    if len(items) > 0:
        print("First inmate record structure:")
        print(json.dumps(items[0], indent=2))
        
        # Print list of names in this page
        print("\nList of names in first 10:")
        for idx, item in enumerate(items[:10]):
            fn = item.get("firstName", "")
            ln = item.get("lastName", "")
            mn = item.get("middleName", "")
            ad = item.get("arrestDate", "")
            bk = item.get("bookingNumber", "")
            bnd = item.get("totalBondAmount", 0)
            print(f"{idx+1}: {ln}, {fn} {mn} | Arrested: {ad} | Booking#: {bk} | Bond: ${bnd}")
else:
    print(resp.text[:500])
