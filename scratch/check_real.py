import urllib.request, os
from dotenv import load_dotenv
load_dotenv()
token = os.getenv('SIGNNOW_API_TOKEN')

import re
with open("dashboard/services/signnow_packet_service.py", "r") as f:
    content = f.read()

import ast
# We'll just extract the dictionary manually
import json

lines = []
in_dict = False
for line in content.split("\n"):
    if line.strip().startswith("TEMPLATE_MAP = {"):
        in_dict = True
        lines.append("{")
        continue
    if in_dict:
        if line.strip().startswith("}"):
            lines.append("}")
            break
        # ignore comments
        if "#" in line:
            line = line[:line.index("#")]
        lines.append(line)

# Let's just run validate_templates_exist by calling the API ourselves
TEMPLATE_MAP = eval("".join(lines).strip())

for name, tid in TEMPLATE_MAP.items():
    if not tid:
        continue
    req = urllib.request.Request(f"https://api.signnow.com/document/{tid}")
    req.add_header('Authorization', f'Bearer {token}')
    try:
        urllib.request.urlopen(req)
        print(f"[OK] {name}")
    except urllib.error.HTTPError as e:
        print(f"[FAIL] {name} - {e.code}")

