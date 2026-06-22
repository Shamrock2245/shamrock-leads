import urllib.request, os
from dotenv import load_dotenv
load_dotenv()
token = os.getenv('SIGNNOW_API_TOKEN')

req = urllib.request.Request("https://api.signnow.com/document/b53c6ad815854b7999881180fb22467d1ce5c9c5")
req.add_header('Authorization', f'Bearer {token}')
try:
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    print(e.read().decode('utf-8'))

