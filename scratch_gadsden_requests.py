import requests
from bs4 import BeautifulSoup

url = "https://gadsdensheriff.com/inmate-lookup/"
headers = {"User-Agent": "Mozilla/5.0"}
resp = requests.get(url, headers=headers)
print("Status:", resp.status_code)
print("Length:", len(resp.text))
soup = BeautifulSoup(resp.text, 'html.parser')
print("Tables:", len(soup.find_all("table")))
