import httpx
import json
from typing import List, Dict, Any, Optional

class SignNowService:
    def __init__(self, api_token: str, base_url: str = "https://api.signnow.com"):
        self.token = api_token
        self.base_url = base_url.rstrip('/')
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    async def get_user(self) -> dict:
        """GET /user — verify token."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.base_url}/user", headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def copy_template(self, template_id: str, name: str) -> str:
        """POST /template/{id}/copy — returns new document_id."""
        async with httpx.AsyncClient() as client:
            payload = {"document_name": name}
            resp = await client.post(
                f"{self.base_url}/template/{template_id}/copy",
                headers=self.headers,
                json=payload
            )
            resp.raise_for_status()
            return resp.json().get("id")

    async def prefill_fields(self, document_id: str, fields: List[Dict[str, Any]]):
        """PUT /document/{id} — hydrate form fields with defendant data."""
        async with httpx.AsyncClient() as client:
            payload = {"fields": fields}
            resp = await client.put(
                f"{self.base_url}/document/{document_id}",
                headers=self.headers,
                json=payload
            )
            resp.raise_for_status()
            return resp.json()

    async def create_invite(self, document_id: str, signers: List[Dict[str, Any]]) -> dict:
        """POST /document/{id}/invite — send email invites."""
        async with httpx.AsyncClient() as client:
            payload = {"to": signers}
            resp = await client.post(
                f"{self.base_url}/document/{document_id}/invite",
                headers=self.headers,
                json=payload
            )
            resp.raise_for_status()
            return resp.json()

    async def create_embedded_invite(self, document_id: str, signers: List[Dict[str, Any]], name_formula: str = "") -> dict:
        """POST /v2/documents/{id}/embedded-invites — returns invite data."""
        async with httpx.AsyncClient() as client:
            payload = {
                "invites": signers,
                "name_formula": name_formula
            }
            resp = await client.post(
                f"{self.base_url}/v2/documents/{document_id}/embedded-invites",
                headers=self.headers,
                json=payload
            )
            
            # Handle conflict (19004002) if invite already exists
            if resp.status_code >= 400:
                error_data = resp.json()
                if error_data.get("errors") and any(e.get("code") == 19004002 for e in error_data["errors"]):
                    # Fetch existing invites
                    get_resp = await client.get(
                        f"{self.base_url}/v2/documents/{document_id}/embedded-invites",
                        headers=self.headers
                    )
                    get_resp.raise_for_status()
                    return get_resp.json()
                resp.raise_for_status()
                
            return resp.json()

    async def get_signing_link(self, link_id: str, expiration: int = 45) -> str:
        """POST /v2/documents/embedded-invites/{link_id}/link — returns URL."""
        async with httpx.AsyncClient() as client:
            payload = {
                "auth_method": "none",
                "link_expiration": expiration
            }
            resp = await client.post(
                f"{self.base_url}/v2/documents/embedded-invites/{link_id}/link",
                headers=self.headers,
                json=payload
            )
            resp.raise_for_status()
            return resp.json().get("data", {}).get("link")

    async def get_document_status(self, document_id: str) -> dict:
        """GET /document/{id} — check signing status."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/document/{document_id}",
                headers=self.headers
            )
            resp.raise_for_status()
            return resp.json()

    async def download_signed_pdf(self, document_id: str, type: str = "collapsed") -> bytes:
        """GET /document/{id}/download?type=collapsed — download signed PDF."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/document/{document_id}/download?type={type}",
                headers=self.headers
            )
            resp.raise_for_status()
            return resp.content

    async def create_full_packet(self, defendant_data: dict, surety_id: str) -> dict:
        """Create all 14 documents from templates, prefill, return invite links."""
        # This is a placeholder for the full packet creation logic
        # It will use the template IDs and field mappings to generate the full packet
        pass
