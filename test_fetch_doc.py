import asyncio
import os
import sys

sys.path.append(os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env')

from dashboard.services.signnow_packet_service import SignNowPacketService
import httpx
import json

async def run():
    service = SignNowPacketService()
    token = await service._get_token()
    # Any template ID, e.g., faq-defendants "1524f1c816c54a72be76d14fe128e4a6034579dc"
    doc_id = "1524f1c816c54a72be76d14fe128e4a6034579dc"
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{service.base_url}/document/{doc_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        data = resp.json()
        print("KEYS:", data.keys())
        if 'fields' in data:
            print("FIELDS:", [f.get('name') for f in data['fields']][:10])
        if 'roles' in data:
            print("ROLES:")
            for r in data['roles']:
                print("Role:", r.get('name'))
                print("Role Fields keys:", [k for k in r.keys()])
        
        # Look for text fields specifically
        text_fields = []
        if 'fields' in data:
            for f in data['fields']:
                if f.get('type') == 'text':
                    text_fields.append(f.get('name'))
        print("TEXT FIELDS (top level):", text_fields[:10])

        if 'texts' in data:
            print("TEXTS keys:", [t.get('name') for t in data['texts']][:10])

asyncio.run(run())
