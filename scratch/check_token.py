import urllib.request, os
from dotenv import load_dotenv
load_dotenv()
token = os.getenv('SIGNNOW_API_TOKEN')

req = urllib.request.Request("https://api.signnow.com/user")
req.add_header('Authorization', f'Bearer {token}')
try:
    res = urllib.request.urlopen(req)
    print("User data:", res.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print(e.read().decode('utf-8'))

