import asyncio
import os
import httpx
import sys

sys.path.append(os.path.join(os.getcwd(), 'dashboard'))
from services.signnow_packet_service import SignNowPacketService

async def main():
    service = SignNowPacketService()
    token = await service._get_token()
    
    # We will just verify the endpoint URL format exists
    async with httpx.AsyncClient() as client:
        # Just an invalid UUID to see if we get a 404 vs 405 vs 401
        group_id = "0000000000000000000000000000000000000000"
        resp = await client.post(
            f"{service.base_url}/v2/document-groups/{group_id}/embedded-invites",
            headers={"Authorization": f"Bearer {token}"},
            json={"invites": [{"email": "test@example.com", "role_name": "Signer 1", "order": 1, "auth_method": "none"}]}
        )
        print("Status:", resp.status_code)
        print("Body:", resp.text)

asyncio.run(main())
