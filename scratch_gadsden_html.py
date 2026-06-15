import requests

url = "https://gadsdensheriff.com/inmate-lookup/"
headers = {"User-Agent": "Mozilla/5.0"}
resp = requests.get(url, headers=headers)
with open("gadsden.html", "w") as f:
    f.write(resp.text)
