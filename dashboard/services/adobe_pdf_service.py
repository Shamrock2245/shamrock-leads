"""
ShamrockLeads — Adobe PDF Services (official Python SDK) + Acrobat Sign
========================================================================

PDF Services (fill / combine / flatten assist)
  Official quickstart:
    https://developer.adobe.com/document-services/docs/overview/pdf-services-api/quickstarts/python/

  Auth (ServicePrincipalCredentials):
    client_id  + client_secret  (from OAuth S2S JSON or env)

  Jobs used:
    ImportPDFFormDataJob  — fill AcroForm fields from JSON
    CombinePDFJob         — merge packet PDFs
    CompressPDFJob        — optional size polish

Acrobat Sign (optional, separate product)
  ADOBE_SIGN_INTEGRATION_KEY for e-signature agreements

Credentials file (gitignored):
  config/adobe-pdf-services.json
  Supports either Adobe sample shape or Developer Console OAuth S2S export:
    { "CLIENT_ID", "CLIENT_SECRETS": [...], "ORG_ID", ... }
    { "client_credentials": { "client_id", "client_secret" }, ... }
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _cred_candidates() -> list[Path]:
    paths: list[Path] = []
    env_path = (os.getenv("ADOBE_PDF_CREDENTIALS_JSON") or "").strip()
    if env_path:
        paths.append(Path(env_path))
    # Adobe sample default name
    paths.append(Path("/app/config/pdfservices-api-credentials.json"))
    paths.append(Path("/app/config/adobe-pdf-services.json"))
    root = Path(__file__).resolve().parents[2]
    paths.append(root / "config" / "pdfservices-api-credentials.json")
    paths.append(root / "config" / "adobe-pdf-services.json")
    return paths


def _load_raw_credentials() -> Dict[str, Any]:
    for p in _cred_candidates():
        try:
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                logger.info("[adobe-pdf] loaded credentials from %s", p)
                return data
        except Exception as exc:
            logger.warning("[adobe-pdf] failed reading %s: %s", p, exc)
    return {}


def resolve_client_id_secret(raw: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """
    Normalize credentials to (client_id, client_secret).

    Accepts:
      - Env PDF_SERVICES_CLIENT_ID / PDF_SERVICES_CLIENT_SECRET (Adobe quickstart)
      - Env ADOBE_PDF_CLIENT_ID / ADOBE_PDF_CLIENT_SECRET
      - JSON: client_credentials.client_id / client_secret
      - JSON: CLIENT_ID / CLIENT_SECRETS[0]  (Developer Console OAuth S2S export)
    """
    # Official quickstart env names first
    cid = (os.getenv("PDF_SERVICES_CLIENT_ID") or os.getenv("ADOBE_PDF_CLIENT_ID") or "").strip()
    secret = (os.getenv("PDF_SERVICES_CLIENT_SECRET") or os.getenv("ADOBE_PDF_CLIENT_SECRET") or "").strip()
    if cid and secret:
        return cid, secret

    raw = raw if raw is not None else _load_raw_credentials()
    if not raw:
        return "", ""

    # Adobe sample shape
    cc = raw.get("client_credentials") or {}
    if isinstance(cc, dict):
        cid = (cc.get("client_id") or cc.get("CLIENT_ID") or "").strip()
        secret = (cc.get("client_secret") or cc.get("CLIENT_SECRET") or "").strip()
        if cid and secret:
            return cid, secret

    # Developer Console OAuth Server-to-Server export
    cid = (raw.get("CLIENT_ID") or raw.get("client_id") or "").strip()
    secrets = raw.get("CLIENT_SECRETS") or raw.get("client_secrets") or []
    if isinstance(secrets, str):
        secrets = [secrets]
    secret = (secrets[0] if secrets else raw.get("CLIENT_SECRET") or raw.get("client_secret") or "")
    secret = str(secret).strip()
    return cid, secret


def _sdk_available() -> bool:
    try:
        from adobe.pdfservices.operation.pdf_services import PDFServices  # noqa: F401
        return True
    except Exception:
        return False


class AdobePDFServicesClient:
    """
    Adobe PDF Services via official Python SDK (preferred).

    Mirrors quickstart pattern:
      credentials = ServicePrincipalCredentials(client_id=..., client_secret=...)
      pdf_services = PDFServices(credentials=credentials)
    """

    def __init__(self, raw_creds: Optional[Dict[str, Any]] = None):
        self._raw = raw_creds if raw_creds is not None else _load_raw_credentials()
        self._client_id, self._client_secret = resolve_client_id_secret(self._raw)
        self._pdf_services = None

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def _client_id_public(self) -> str:
        return self._client_id

    def _get_pdf_services(self):
        """Lazy SDK client (sync — Adobe SDK is synchronous)."""
        if self._pdf_services is not None:
            return self._pdf_services
        if not self.configured:
            raise RuntimeError("Adobe PDF Services credentials not configured")
        if not _sdk_available():
            raise RuntimeError(
                "pdfservices-sdk not installed. "
                "pip install pdfservices-sdk  "
                "(see https://developer.adobe.com/document-services/docs/overview/pdf-services-api/quickstarts/python/)"
            )
        from adobe.pdfservices.operation.auth.service_principal_credentials import (
            ServicePrincipalCredentials,
        )
        from adobe.pdfservices.operation.pdf_services import PDFServices

        credentials = ServicePrincipalCredentials(
            client_id=self._client_id,
            client_secret=self._client_secret,
        )
        self._pdf_services = PDFServices(credentials=credentials)
        return self._pdf_services

    async def get_access_token(self) -> str:
        """
        Health-check: obtain a token via official PDF Services endpoint.
        (SDK manages tokens internally for jobs; this is for status probes.)
        """
        import httpx

        if not self.configured:
            raise RuntimeError("Adobe PDF Services credentials not configured")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://pdf-services.adobe.io/token",
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Adobe token failed HTTP {resp.status_code}: {resp.text[:300]}"
                )
            token = resp.json().get("access_token") or ""
            if not token:
                raise RuntimeError("Adobe token response missing access_token")
            return token

    def _upload(self, pdf_services, pdf_bytes: bytes):
        from adobe.pdfservices.operation.pdf_services_media_type import PDFServicesMediaType

        return pdf_services.upload(
            input_stream=pdf_bytes,
            mime_type=PDFServicesMediaType.PDF,
        )

    def _download_result_bytes(self, pdf_services, result_asset) -> bytes:
        stream_asset = pdf_services.get_content(result_asset)
        data = stream_asset.get_input_stream()
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        # file-like
        return data.read() if hasattr(data, "read") else bytes(data)

    def import_form_data_sync(self, pdf_bytes: bytes, field_map: Dict[str, Any]) -> bytes:
        """
        Fill PDF AcroForm fields using ImportPDFFormDataJob (official SDK).
        field_map keys must match form field names in the PDF.
        """
        if not pdf_bytes:
            return b""
        # Coerce values to strings (Adobe form data is typically stringly)
        form_data = {
            str(k): ("" if v is None else str(v))
            for k, v in (field_map or {}).items()
            if k
        }
        if not form_data:
            return pdf_bytes

        from adobe.pdfservices.operation.pdfjobs.jobs.import_pdf_form_data_job import (
            ImportPDFFormDataJob,
        )
        from adobe.pdfservices.operation.pdfjobs.params.import_pdf_form_data.import_pdf_form_data_params import (
            ImportPDFFormDataParams,
        )
        from adobe.pdfservices.operation.pdfjobs.result.import_pdf_form_data_result import (
            ImportPDFFormDataResult,
        )

        pdf_services = self._get_pdf_services()
        input_asset = self._upload(pdf_services, pdf_bytes)
        params = ImportPDFFormDataParams(json_form_fields_data=form_data)
        job = ImportPDFFormDataJob(input_asset)
        job.set_params(params)
        location = pdf_services.submit(job)
        response = pdf_services.get_job_result(location, ImportPDFFormDataResult)
        result_asset = response.get_result().get_asset()
        return self._download_result_bytes(pdf_services, result_asset)

    def combine_pdfs_sync(self, pdf_parts: List[bytes]) -> bytes:
        """Combine PDFs via CombinePDFJob (official SDK)."""
        if not pdf_parts:
            return b""
        if len(pdf_parts) == 1:
            return pdf_parts[0]

        from adobe.pdfservices.operation.pdfjobs.jobs.combine_pdf_job import CombinePDFJob
        from adobe.pdfservices.operation.pdfjobs.params.combine_pdf.combine_pdf_params import (
            CombinePDFParams,
        )
        from adobe.pdfservices.operation.pdfjobs.result.combine_pdf_result import CombinePDFResult

        pdf_services = self._get_pdf_services()
        params = CombinePDFParams()
        for part in pdf_parts:
            if not part:
                continue
            asset = self._upload(pdf_services, part)
            params.add_asset(asset)
        job = CombinePDFJob(combine_pdf_params=params)
        location = pdf_services.submit(job)
        response = pdf_services.get_job_result(location, CombinePDFResult)
        result_asset = response.get_result().get_asset()
        return self._download_result_bytes(pdf_services, result_asset)

    def compress_pdf_sync(self, pdf_bytes: bytes) -> bytes:
        """Optional CompressPDFJob polish."""
        if not pdf_bytes:
            return b""
        from adobe.pdfservices.operation.pdfjobs.jobs.compress_pdf_job import CompressPDFJob
        from adobe.pdfservices.operation.pdfjobs.result.compress_pdf_result import CompressPDFResult

        pdf_services = self._get_pdf_services()
        input_asset = self._upload(pdf_services, pdf_bytes)
        job = CompressPDFJob(input_asset=input_asset)
        location = pdf_services.submit(job)
        response = pdf_services.get_job_result(location, CompressPDFResult)
        result_asset = response.get_result().get_asset()
        return self._download_result_bytes(pdf_services, result_asset)

    # ── Async wrappers (run sync SDK in thread to avoid blocking event loop) ──

    async def _to_thread(self, fn, *args, **kwargs):
        import asyncio
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def import_form_data(self, pdf_bytes: bytes, field_map: Dict[str, Any]) -> bytes:
        return await self._to_thread(self.import_form_data_sync, pdf_bytes, field_map)

    async def combine_pdfs(self, pdf_parts: List[bytes], names: Optional[List[str]] = None) -> bytes:
        return await self._to_thread(self.combine_pdfs_sync, pdf_parts)

    async def compress_pdf(self, pdf_bytes: bytes) -> bytes:
        return await self._to_thread(self.compress_pdf_sync, pdf_bytes)

    def fill_local_pymupdf(self, pdf_bytes: bytes, field_map: Dict[str, Any]) -> Tuple[bytes, Dict[str, Any]]:
        """Local AcroForm fill + bake (fallback when SDK form-import fails)."""
        meta: Dict[str, Any] = {"filled_fields": 0, "engine": "none"}
        try:
            import fitz

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            count = 0
            for page in doc:
                for widget in (page.widgets() or []):
                    name = widget.field_name or ""
                    if not name:
                        continue
                    val = field_map.get(name)
                    if val is None:
                        for k, v in field_map.items():
                            if str(k).lower() == name.lower():
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
            try:
                doc.bake()
            except Exception:
                pass
            out = doc.tobytes(deflate=True, garbage=3)
            doc.close()
            meta["filled_fields"] = count
            meta["engine"] = "pymupdf"
            return out, meta
        except Exception as exc:
            meta["engine"] = "passthrough"
            meta["fill_error"] = str(exc)
            return pdf_bytes, meta

    async def fill_and_flatten_local_first(
        self,
        pdf_bytes: bytes,
        field_map: Dict[str, Any],
    ) -> Tuple[bytes, Dict[str, Any]]:
        """
        Prefer Adobe ImportPDFFormDataJob when configured; fall back to PyMuPDF.
        Optionally compress via Adobe after fill.
        """
        meta: Dict[str, Any] = {"filled_fields": 0, "engine": "none"}
        filled = pdf_bytes

        if self.configured and field_map and _sdk_available():
            try:
                filled = await self.import_form_data(pdf_bytes, field_map)
                meta["engine"] = "adobe_import_form_data"
                meta["filled_fields"] = len(field_map)
            except Exception as exc:
                logger.warning("[adobe-pdf] ImportPDFFormData failed, local fill: %s", exc)
                filled, meta = self.fill_local_pymupdf(pdf_bytes, field_map)
                meta["adobe_import_error"] = str(exc)[:200]
        else:
            filled, meta = self.fill_local_pymupdf(pdf_bytes, field_map)

        if self.configured and filled and _sdk_available():
            try:
                polished = await self.compress_pdf(filled)
                if polished and len(polished) > 100:
                    filled = polished
                    meta["adobe_compress"] = True
            except Exception as exc:
                meta["adobe_compress"] = False
                meta["adobe_compress_error"] = str(exc)[:200]

        return filled, meta

    async def build_flattened_packet(
        self,
        pdf_parts: List[bytes],
        field_map: Optional[Dict[str, Any]] = None,
        names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Fill each part (when field_map provided), combine via SDK, optional compress.
        """
        field_map = field_map or {}
        filled_parts: List[bytes] = []
        fill_meta: List[Dict[str, Any]] = []
        for part in pdf_parts:
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
            if self.configured and _sdk_available():
                try:
                    combined = await self.combine_pdfs(filled_parts, names=names)
                    combine_engine = "adobe_sdk_combine"
                except Exception as exc:
                    logger.warning("[adobe-pdf] SDK combine failed, local merge: %s", exc)
                    combine_engine = f"local_fallback:{exc}"
                    combined = self._local_merge(filled_parts)
                    if combine_engine.startswith("local"):
                        combine_engine = "pymupdf_merge"
            else:
                combined = self._local_merge(filled_parts)
                combine_engine = "pymupdf_merge"

        return {
            "success": True,
            "pdf_bytes": combined,
            "size": len(combined or b""),
            "parts": len(filled_parts),
            "combine_engine": combine_engine,
            "fill_meta": fill_meta,
            "adobe_pdf_configured": self.configured,
            "sdk_available": _sdk_available(),
        }

    @staticmethod
    def _local_merge(parts: List[bytes]) -> bytes:
        import fitz

        out = fitz.open()
        for blob in parts:
            src = fitz.open(stream=blob, filetype="pdf")
            out.insert_pdf(src)
            src.close()
        data = out.tobytes(deflate=True, garbage=3)
        out.close()
        return data


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

        import httpx

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
            "client_id_present": bool(pdf._client_id_public()),
            "sdk_available": _sdk_available(),
            "credentials_path_checked": [str(p) for p in _cred_candidates()],
            "auth_pattern": "ServicePrincipalCredentials (official Python SDK)",
            "docs": "https://developer.adobe.com/document-services/docs/overview/pdf-services-api/quickstarts/python/",
        },
        "acrobat_sign": {
            "configured": sign.configured,
            "api_base": sign.base if sign.configured else None,
        },
    }
