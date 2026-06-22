import requests
import json
import os

token = "0c35edbbf6823555a8434624aaec4830fd4477bb5befee3da2fa29e2b258913d"  # From .env
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/json"
}
# List all documents and templates
url = "https://api.signnow.com/document" # This might only list documents. Let's try /folder
response = requests.get(url, headers=headers)
if response.status_code == 200:
    docs = response.json()
    print("Fetched successfully. Sample keys:", list(docs.keys()) if isinstance(docs, dict) else type(docs))
else:
    print(f"Error {response.status_code}: {response.text}")
