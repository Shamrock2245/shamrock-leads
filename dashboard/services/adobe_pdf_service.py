"""
ShamrockLeads — Adobe PDF Services + Acrobat Sign clients
=========================================================

PDF Services (OAuth Server-to-Server / DCAPI):
  - Fill AcroForm fields when PDF Services form-fill is available
  - Combine multiple PDFs
  - Linearize / compress as a professional flatten pass

Acrobat Sign (optional separate product):
  - Transient upload + agreement create for e-signature
  - Requires ADOBE_SIGN_INTEGRATION_KEY (or access token)

Credentials for PDF Services live in:
  config/adobe-pdf-services.json  (mounted at /app/config/ in Docker)
  or env ADOBE_PDF_* / ADOBE_PDF_CREDENTIALS_JSON
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# Official PDF Services token endpoint (preferred):
# https://developer.adobe.com/document-services/docs/overview/pdf-services-api/gettingstarted/
PDF_SERVICES_TOKEN_URL = "https://pdf-services.adobe.io/token"
# Fallback IMS client_credentials (OAuth Server-to-Server console creds)
IMS_TOKEN_URL = "https://ims-na1.adobelogin.com/ims/token/v3"
PDF_SERVICES_BASE = "https://pdf-services.adobe.io"

# Candidate credential file locations (host + container)
def _cred_candidates() -> list[Path]:
    paths: list[Path] = []
    env_path = (os.getenv("ADOBE_PDF_CREDENTIALS_JSON") or "").strip()
    if env_path:
        paths.append(Path(env_path))
    paths.append(Path("/app/config/adobe-pdf-services.json"))
    paths.append(Path(__file__).resolve().parents[2] / "config" / "adobe-pdf-services.json")
    return paths


def _load_pdf_credentials() -> Dict[str, Any]:
    """Load OAuth S2S credentials from JSON file or environment."""
    for p in _cred_candidates():
        if not p or str(p) in (".", ""):
            continue
        try:
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                if data.get("CLIENT_ID") or data.get("client_id"):
                    logger.info("[adobe-pdf] loaded credentials from %s", p)
                    return data
        except Exception as exc:
            logger.warning("[adobe-pdf] failed reading %s: %s", p, exc)

    client_id = os.getenv("ADOBE_PDF_CLIENT_ID", "")
    client_secret = os.getenv("ADOBE_PDF_CLIENT_SECRET", "")
    if client_id and client_secret:
        return {
            "CLIENT_ID": client_id,
            "CLIENT_SECRETS": [client_secret],
            "ORG_ID": os.getenv("ADOBE_PDF_ORG_ID", ""),
            "TECHNICAL_ACCOUNT_ID": os.getenv("ADOBE_PDF_TECHNICAL_ACCOUNT_ID", ""),
            "SCOPES": [
                s.strip()
                for s in os.getenv(
                    "ADOBE_PDF_SCOPES",
                    "openid,AdobeID,DCAPI",
                ).split(",")
                if s.strip()
            ],
        }
    return {}


class AdobePDFServicesClient:
    """Adobe PDF Services (Document Cloud) — fill / combine / flatten assist."""

    def __init__(self, creds: Optional[Dict[str, Any]] = None):
        self.creds = creds if creds is not None else _load_pdf_credentials()
        self._token: str = ""
        self._token_expires_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        cid = self.creds.get("CLIENT_ID") or self.creds.get("client_id") or ""
        secrets = self.creds.get("CLIENT_SECRETS") or self.creds.get("client_secrets") or []
        if isinstance(secrets, str):
            secrets = [secrets]
        secret = secrets[0] if secrets else self.creds.get("CLIENT_SECRET") or self.creds.get("client_secret") or ""
        return bool(cid and secret)

    def _client_id(self) -> str:
        return self.creds.get("CLIENT_ID") or self.creds.get("client_id") or ""

    def _client_secret(self) -> str:
        secrets = self.creds.get("CLIENT_SECRETS") or self.creds.get("client_secrets") or []
        if isinstance(secrets, str):
            return secrets
        if secrets:
            return secrets[0]
        return self.creds.get("CLIENT_SECRET") or self.creds.get("client_secret") or ""

    def _scopes(self) -> str:
        scopes = self.creds.get("SCOPES") or self.creds.get("scopes") or [
            "openid", "AdobeID", "DCAPI",
        ]
        if isinstance(scopes, list):
            return ",".join(scopes)
        return str(scopes)

    async def get_access_token(self) -> str:
        """
        Obtain access_token per Adobe PDF Services getting-started docs:
          POST https://pdf-services.adobe.io/token
          client_id + client_secret (form-urlencoded)

        Falls back to IMS client_credentials with declared scopes.
        """
        if not self.configured:
            raise RuntimeError("Adobe PDF Services credentials not configured")

        async with self._lock:
            if self._token and time.time() < self._token_expires_at - 60:
                return self._token

            cid = self._client_id()
            secret = self._client_secret()
            async with httpx.AsyncClient(timeout=30) as client:
                # 1) Official PDF Services token endpoint
                resp = await client.post(
                    PDF_SERVICES_TOKEN_URL,
                    data={"client_id": cid, "client_secret": secret},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if resp.status_code >= 400:
                    # 2) IMS OAuth Server-to-Server fallback
                    logger.warning(
                        "[adobe-pdf] /token HTTP %s — trying IMS client_credentials",
                        resp.status_code,
                    )
                    resp = await client.post(
                        IMS_TOKEN_URL,
                        data={
                            "grant_type": "client_credentials",
                            "client_id": cid,
                            "client_secret": secret,
                            "scope": self._scopes(),
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"Adobe token failed HTTP {resp.status_code}: {resp.text[:300]}"
                    )
                payload = resp.json()
                token = payload.get("access_token") or ""
                if not token:
                    raise RuntimeError("Adobe token response missing access_token")
                expires_in = int(payload.get("expires_in") or 3600)
                self._token = token
                self._token_expires_at = time.time() + expires_in
                logger.info("[adobe-pdf] access_token acquired (expires_in=%ss)", expires_in)
                return token

    def _headers(self, token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "x-api-key": self._client_id(),
        }

    async def _upload_asset(self, client: httpx.AsyncClient, token: str, pdf_bytes: bytes, name: str) -> str:
        """Upload binary via get-upload-uri → PUT → return assetID."""
        # Step 1: get pre-signed upload URI
        meta = await client.post(
            f"{PDF_SERVICES_BASE}/assets",
            headers={
                **self._headers(token),
                "Content-Type": "application/json",
            },
            json={"mediaType": "application/pdf"},
        )
        if meta.status_code >= 400:
            raise RuntimeError(f"Adobe asset URI failed HTTP {meta.status_code}: {meta.text[:300]}")
        body = meta.json()
        upload_uri = body.get("uploadUri")
        asset_id = body.get("assetID") or body.get("assetId")
        if not upload_uri or not asset_id:
            raise RuntimeError(f"Adobe asset response missing uploadUri/assetID: {body}")

        # Step 2: PUT bytes to pre-signed URL (no Adobe auth headers)
        put = await client.put(
            upload_uri,
            content=pdf_bytes,
            headers={"Content-Type": "application/pdf"},
        )
        if put.status_code >= 400:
            raise RuntimeError(f"Adobe asset PUT failed HTTP {put.status_code}: {put.text[:200]}")
        logger.debug("[adobe-pdf] uploaded asset %s (%s bytes, name=%s)", asset_id, len(pdf_bytes), name)
        return asset_id

    async def _poll_job(self, client: httpx.AsyncClient, token: str, location: str, timeout_s: float = 120) -> Dict[str, Any]:
        """
        Poll job status URL until done.
        Per docs: status is 'in progress' | 'done' | 'failed'; when done,
        response includes downloadUri (docs sometimes spell it dowloadUri).
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            resp = await client.get(location, headers=self._headers(token))
            if resp.status_code >= 400:
                raise RuntimeError(f"Adobe job poll HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            status = str(data.get("status") or data.get("jobStatus") or "").strip().lower()
            if status in ("done", "success", "completed"):
                return data
            if status in ("failed", "error"):
                raise RuntimeError(f"Adobe job failed: {data}")
            # in progress / empty — keep polling
            await asyncio.sleep(1.5)
        raise TimeoutError(f"Adobe job timed out after {timeout_s}s: {location}")

    def _extract_download_uri(self, job_result: Dict[str, Any]) -> Optional[str]:
        """Pull download pre-signed URI from a completed job status body."""
        for key in ("downloadUri", "downloadURI", "dowloadUri"):  # include docs typo
            if job_result.get(key):
                return job_result[key]
        asset = job_result.get("asset") or {}
        if isinstance(asset, dict):
            for key in ("downloadUri", "downloadURI", "dowloadUri"):
                if asset.get(key):
                    return asset[key]
        assets = job_result.get("assets") or []
        if isinstance(assets, list) and assets:
            a0 = assets[0] if isinstance(assets[0], dict) else {}
            for key in ("downloadUri", "downloadURI", "dowloadUri"):
                if a0.get(key):
                    return a0[key]
        return None

    async def _download_from_job_result(self, client: httpx.AsyncClient, job_result: Dict[str, Any]) -> bytes:
        uri = self._extract_download_uri(job_result)
        if not uri:
            raise RuntimeError(f"Adobe job done but no downloadUri: {job_result}")
        dl = await client.get(uri)
        if dl.status_code >= 400:
            raise RuntimeError(f"Adobe download failed HTTP {dl.status_code}")
        return dl.content

    async def combine_pdfs(self, pdf_parts: List[bytes], names: Optional[List[str]] = None) -> bytes:
        """Combine multiple PDF blobs into one via PDF Services."""
        if not pdf_parts:
            return b""
        if len(pdf_parts) == 1:
            return pdf_parts[0]

        token = await self.get_access_token()
        async with httpx.AsyncClient(timeout=120) as client:
            asset_ids = []
            for i, blob in enumerate(pdf_parts):
                name = (names[i] if names and i < len(names) else f"part-{i+1}.pdf")
                asset_ids.append(await self._upload_asset(client, token, blob, name))

            # Combine PDF job
            payload = {
                "assets": [{"assetID": aid} for aid in asset_ids],
            }
            job = await client.post(
                f"{PDF_SERVICES_BASE}/operation/combinepdf",
                headers={
                    **self._headers(token),
                    "Content-Type": "application/json",
                    "x-request-id": str(uuid.uuid4()),
                },
                json=payload,
            )
            # 201 + Location header is typical
            if job.status_code not in (200, 201, 202):
                raise RuntimeError(f"Adobe combine failed HTTP {job.status_code}: {job.text[:400]}")
            location = job.headers.get("location") or job.headers.get("Location")
            if not location:
                # Some APIs return jobID in body
                body = job.json() if job.content else {}
                job_id = body.get("jobID") or body.get("jobId")
                if job_id:
                    location = f"{PDF_SERVICES_BASE}/operation/combinepdf/{job_id}/status"
                else:
                    raise RuntimeError(f"Adobe combine missing Location header: {job.headers}")

            result = await self._poll_job(client, token, location)
            return await self._download_from_job_result(client, result)

    async def compress_pdf(self, pdf_bytes: bytes) -> bytes:
        """Compress / optimize a PDF (optional flatten assist)."""
        if not pdf_bytes:
            return b""
        token = await self.get_access_token()
        async with httpx.AsyncClient(timeout=120) as client:
            asset_id = await self._upload_asset(client, token, pdf_bytes, "input.pdf")
            job = await client.post(
                f"{PDF_SERVICES_BASE}/operation/compresspdf",
                headers={
                    **self._headers(token),
                    "Content-Type": "application/json",
                    "x-request-id": str(uuid.uuid4()),
                },
                json={
                    "assetID": asset_id,
                    "compressionLevel": "MEDIUM",
                },
            )
            if job.status_code not in (200, 201, 202):
                # Non-fatal — return original
                logger.warning("[adobe-pdf] compress unavailable HTTP %s", job.status_code)
                return pdf_bytes
            location = job.headers.get("location") or job.headers.get("Location")
            if not location:
                return pdf_bytes
            result = await self._poll_job(client, token, location)
            try:
                return await self._download_from_job_result(client, result)
            except Exception as exc:
                logger.warning("[adobe-pdf] compress download failed: %s", exc)
                return pdf_bytes

    async def fill_and_flatten_local_first(
        self,
        pdf_bytes: bytes,
        field_map: Dict[str, Any],
    ) -> Tuple[bytes, Dict[str, Any]]:
        """
        Fill AcroForm fields with PyMuPDF (reliable for standard fillable PDFs),
        then optionally run Adobe compress as a polish step.
        Returns (pdf_bytes, meta).
        """
        meta: Dict[str, Any] = {"filled_fields": 0, "engine": "none"}
        filled = pdf_bytes
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            count = 0
            for page in doc:
                for widget in (page.widgets() or []):
                    name = widget.field_name or ""
                    if not name:
                        continue
                    # Direct or case-insensitive match
                    val = field_map.get(name)
                    if val is None:
                        for k, v in field_map.items():
                            if k.lower() == name.lower():
                                val = v
                                break
                    if val is None or str(val).strip() == "":
                        continue
                    try:
                        widget.field_value = str(val)
                        widget.update()
                        count += 1
                    except Exception:
                        continue
            # Flatten: burn widgets into content
            try:
                doc.bake()  # PyMuPDF 1.23+
            except Exception:
                # Older: set need_appearances and form flatten flags if available
                try:
                    for page in doc:
                        for widget in (page.widgets() or []):
                            widget.field_flags |= 1  # read-only-ish
                            widget.update()
                except Exception:
                    pass
            filled = doc.tobytes(deflate=True, garbage=3)
            doc.close()
            meta["filled_fields"] = count
            meta["engine"] = "pymupdf"
        except Exception as exc:
            logger.warning("[adobe-pdf] local fill failed: %s", exc)
            meta["engine"] = "passthrough"
            meta["fill_error"] = str(exc)

        # Adobe polish when configured
        if self.configured and filled:
            try:
                polished = await self.compress_pdf(filled)
                if polished and len(polished) > 100:
                    filled = polished
                    meta["adobe_compress"] = True
            except Exception as exc:
                meta["adobe_compress"] = False
                meta["adobe_compress_error"] = str(exc)

        return filled, meta

    async def build_flattened_packet(
        self,
        pdf_parts: List[bytes],
        field_map: Optional[Dict[str, Any]] = None,
        names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Fill each part (when field_map provided), combine, optional Adobe compress.
        """
        field_map = field_map or {}
        filled_parts: List[bytes] = []
        fill_meta: List[Dict[str, Any]] = []
        for i, part in enumerate(pdf_parts):
            if not part:
                continue
            if field_map:
                blob, meta = await self.fill_and_flatten_local_first(part, field_map)
            else:
                blob, meta = part, {"engine": "raw"}
            filled_parts.append(blob)
            fill_meta.append(meta)

        if not filled_parts:
            return {"success": False, "error": "No PDF parts to flatten", "pdf_bytes": b""}

        combined = filled_parts[0]
        combine_engine = "single"
        if len(filled_parts) > 1:
            if self.configured:
                try:
                    combined = await self.combine_pdfs(filled_parts, names=names)
                    combine_engine = "adobe_combine"
                except Exception as exc:
                    logger.warning("[adobe-pdf] combine failed, local merge: %s", exc)
                    combine_engine = f"local_fallback:{exc}"
                    try:
                        import fitz
                        out = fitz.open()
                        for blob in filled_parts:
                            src = fitz.open(stream=blob, filetype="pdf")
                            out.insert_pdf(src)
                            src.close()
                        combined = out.tobytes(deflate=True, garbage=3)
                        out.close()
                        combine_engine = "pymupdf_merge"
                    except Exception as exc2:
                        return {"success": False, "error": str(exc2), "pdf_bytes": filled_parts[0]}
            else:
                try:
                    import fitz
                    out = fitz.open()
                    for blob in filled_parts:
                        src = fitz.open(stream=blob, filetype="pdf")
                        out.insert_pdf(src)
                        src.close()
                    combined = out.tobytes(deflate=True, garbage=3)
                    out.close()
                    combine_engine = "pymupdf_merge"
                except Exception as exc:
                    return {"success": False, "error": str(exc), "pdf_bytes": filled_parts[0]}

        return {
            "success": True,
            "pdf_bytes": combined,
            "size": len(combined),
            "parts": len(filled_parts),
            "combine_engine": combine_engine,
            "fill_meta": fill_meta,
            "adobe_pdf_configured": self.configured,
        }


class AdobeSignClient:
    """Adobe Acrobat Sign — e-signature (separate from PDF Services)."""

    def __init__(self):
        self.token = (
            os.getenv("ADOBE_SIGN_INTEGRATION_KEY")
            or os.getenv("ADOBE_SIGN_ACCESS_TOKEN")
            or ""
        )
        self.base = (
            os.getenv("ADOBE_SIGN_API_BASE")
            or "https://api.na1.adobesign.com/api/rest/v6"
        ).rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.token)

    async def send_for_signature(
        self,
        *,
        pdf_bytes: bytes,
        filename: str,
        signer_email: str,
        signer_name: str,
        agreement_name: str,
    ) -> Dict[str, Any]:
        if not self.configured:
            return {
                "success": False,
                "provider": "adobe_sign",
                "error": (
                    "Adobe Sign not configured. Add ADOBE_SIGN_INTEGRATION_KEY "
                    "(Acrobat Sign admin → API) — separate from PDF Services OAuth."
                ),
            }
        if not signer_email:
            return {"success": False, "provider": "adobe_sign", "error": "signer_email required"}
        if not pdf_bytes:
            return {"success": False, "provider": "adobe_sign", "error": "empty PDF"}

        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                files = {
                    "File-Name": (None, filename),
                    "File": (filename, pdf_bytes, "application/pdf"),
                }
                up = await client.post(
                    f"{self.base}/transientDocuments",
                    headers=headers,
                    files=files,
                )
                if up.status_code >= 400:
                    return {
                        "success": False,
                        "provider": "adobe_sign",
                        "error": f"Upload failed HTTP {up.status_code}: {up.text[:300]}",
                    }
                transient_id = up.json().get("transientDocumentId")
                if not transient_id:
                    return {
                        "success": False,
                        "provider": "adobe_sign",
                        "error": "No transientDocumentId",
                    }

                payload = {
                    "fileInfos": [{"transientDocumentId": transient_id}],
                    "name": agreement_name or filename,
                    "participantSetsInfo": [{
                        "order": 1,
                        "role": "SIGNER",
                        "memberInfos": [{
                            "email": signer_email,
                            "name": signer_name or "Signer",
                        }],
                    }],
                    "signatureType": "ESIGN",
                    "state": "IN_PROCESS",
                }
                ag = await client.post(
                    f"{self.base}/agreements",
                    headers={**headers, "Content-Type": "application/json"},
                    json=payload,
                )
                if ag.status_code >= 400:
                    return {
                        "success": False,
                        "provider": "adobe_sign",
                        "error": f"Agreement failed HTTP {ag.status_code}: {ag.text[:300]}",
                    }
                agreement_id = ag.json().get("id") or ag.json().get("agreementId") or ""
                return {
                    "success": True,
                    "provider": "adobe_sign",
                    "agreement_id": agreement_id,
                    "signer_email": signer_email,
                    "status": "sent",
                }
        except Exception as exc:
            logger.exception("Adobe Sign send failed")
            return {"success": False, "provider": "adobe_sign", "error": str(exc)}


# ── Module-level helpers ──────────────────────────────────────────────────────

_pdf_client: Optional[AdobePDFServicesClient] = None


def get_adobe_pdf_client() -> AdobePDFServicesClient:
    global _pdf_client
    if _pdf_client is None:
        _pdf_client = AdobePDFServicesClient()
    return _pdf_client


def get_adobe_sign_client() -> AdobeSignClient:
    return AdobeSignClient()


def adobe_status() -> Dict[str, Any]:
    pdf = get_adobe_pdf_client()
    sign = get_adobe_sign_client()
    return {
        "pdf_services": {
            "configured": pdf.configured,
            "client_id_present": bool(pdf._client_id()) if pdf.configured else False,
            "credentials_path_checked": [str(p) for p in _cred_candidates()],
        },
        "acrobat_sign": {
            "configured": sign.configured,
            "api_base": sign.base if sign.configured else None,
        },
    }
