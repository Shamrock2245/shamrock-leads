from __future__ import annotations
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
        """
        Create all documents from templates, prefill, and return invite links.
        Delegates to SignNowPacketService.create_packet() which handles the full
        two-phase workflow: template copy, field prefill, document group, embedded invite.

        Args:
            defendant_data: Intake/defendant record dict (same shape as intake_queue docs).
            surety_id:       "osi" or "palmetto" -- determines template set.

        Returns:
            Dict with invite_id, signing_link, group_id, document_ids, manifest_size.
        """
        import uuid
        from dashboard.services.signnow_packet_service import SignNowPacketService

        svc = SignNowPacketService()
        # Propagate our already-authenticated token so we don't double-auth
        if self.token:
            svc.api_token = self.token

        packet_id = defendant_data.get("intake_id") or f"PKT-{uuid.uuid4().hex[:8].upper()}"
        return await svc.create_packet(
            intake_doc=defendant_data,
            packet_id=packet_id,
            phase=1,
            surety_id=surety_id,
        )
