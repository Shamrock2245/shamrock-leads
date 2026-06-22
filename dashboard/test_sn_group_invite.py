import asyncio
import os
import httpx

from services.signnow_packet_service import SignNowPacketService

async def main():
    service = SignNowPacketService()
    token = await service._get_token()
    
    async with httpx.AsyncClient() as client:
        group_id = "0000000000000000000000000000000000000000"
        payload = {
            "invites": [
                {
                    "email": "test@example.com",
                    "role_name": "Signer 1",
                    "order": 1,
                    "auth_method": "none"
                },
                {
                    "email": "test2@example.com",
                    "role_name": "Signer 2",
                    "order": 2,
                    "auth_method": "none"
                }
            ]
        }
        resp = await client.post(
            f"{service.base_url}/v2/document-groups/{group_id}/embedded-invites",
            headers={"Authorization": f"Bearer {token}"},
            json=payload
        )
        print("Status:", resp.status_code)
        print("Body:", resp.text)

asyncio.run(main())
