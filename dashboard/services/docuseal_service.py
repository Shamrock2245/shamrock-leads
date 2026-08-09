"""
ShamrockLeads — DocuSeal E-Sign Service
=======================================
Self-hosted open-source e-sign client (replaces SignNow for *new* packets).

Upstream: https://github.com/docusealco/docuseal
Ops:      docker compose --profile paperwork up -d
Public:   DOCUSEAL_URL (default https://sign.shamrockbailbonds.biz)
Docs:     docs/PAPERWORK_PORTAL_DOCUSEAL.md

Cost posture (S1):
  - Self-host OSS — $0 software, unlimited UI signers
  - Use submitter **sign links** (`/s/{slug}`) — not paid Pro embed path
  - Archive completed PDFs via our Google Drive Completed Bonds helper

Auth header: X-Auth-Token: {DOCUSEAL_API_KEY}
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)

# Default public host for self-hosted DocuSeal
DEFAULT_DOCUSEAL_URL = "https://sign.shamrockbailbonds.biz"

# Completed Bonds Drive folder (also COMPLETED_BONDS_FOLDER_ID / GOOGLE_DRIVE_OUTPUT_FOLDER_ID)
DEFAULT_COMPLETED_BONDS_FOLDER = "1WnjwtxoaoXVW8_B6s-0ftdCPf_5WfKgs"

# Role names MUST match DocuSeal template submitter names exactly
# (live template 1 "shamrock-osi-paperwork-complete"):
#   indemnitor | Defendant | Coindemnitor | Bondsman
ROLE_INDEMNITOR = "indemnitor"
ROLE_DEFENDANT = "Defendant"
ROLE_CO_INDEMNITOR = "Coindemnitor"
ROLE_BONDSMAN = "Bondsman"
ROLE_INDEMNITOR_N = "Coindemnitor"  # template only has one co-role; reuse Coindemnitor


def _safe_money(val: Any) -> float:
    """Parse currency-ish values without raising; None/blank → 0.0."""
    if val is None or isinstance(val, (dict, list, tuple, set)):
        return 0.0
    if isinstance(val, bool):
        return 0.0
    if isinstance(val, (int, float)):
        try:
            f = float(val)
            if f != f:  # NaN
                return 0.0
            return f
        except (ValueError, TypeError, OverflowError):
            return 0.0
    if not isinstance(val, str):
        return 0.0
    s = val.strip()
    if not s or s.lower() in ("none", "null", "n/a", "tbd"):
        return 0.0
    # Keep digits, dot, minus
    cleaned = "".join(ch for ch in s if ch.isdigit() or ch in ".-")
    if cleaned in ("", ".", "-", "-."):
        return 0.0
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def _number_to_words(n: int) -> str:
    """Integer to English words (USD dollars portion). Safe for n >= 0."""
    try:
        n = int(n)
    except (ValueError, TypeError):
        return "Zero"
    if n < 0:
        return "Zero"
    if n == 0:
        return "Zero"
    units = [
        "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
        "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
        "Eighteen", "Nineteen",
    ]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
    if n < 20:
        return units[n]
    if n < 100:
        return tens[n // 10] + (" " + units[n % 10] if n % 10 != 0 else "")
    if n < 1000:
        return units[n // 100] + " Hundred" + (" " + _number_to_words(n % 100) if n % 100 != 0 else "")
    if n < 1_000_000:
        return _number_to_words(n // 1000) + " Thousand" + (
            " " + _number_to_words(n % 1000) if n % 1000 != 0 else ""
        )
    if n < 1_000_000_000:
        return _number_to_words(n // 1_000_000) + " Million" + (
            " " + _number_to_words(n % 1_000_000) if n % 1_000_000 != 0 else ""
        )
    return str(n)


def _amount_to_words(val: Any) -> str:
    """Currency amount → 'Five Thousand and 00/100' style (never raises)."""
    clean = _safe_money(val)
    if clean <= 0:
        return ""
    dollars = int(clean)
    cents = int(round((clean - dollars) * 100))
    if cents >= 100:
        dollars += 1
        cents = 0
    return f"{_number_to_words(dollars)} and {cents:02d}/100"


def _role_for_indemnitor_index(idx: int) -> str:
    """Map indemnitor list index → DocuSeal template role name."""
    if idx <= 0:
        return ROLE_INDEMNITOR
    if idx == 1:
        return ROLE_CO_INDEMNITOR
    return ROLE_INDEMNITOR_N.format(n=idx + 1)


def _nonempty_party(p: Any) -> bool:
    """True if party dict has usable name, email, or phone."""
    if not isinstance(p, dict):
        return False
    for k in ("name", "full_name", "email", "phone", "first_name", "firstName", "last_name", "lastName"):
        v = p.get(k)
        if v is not None and str(v).strip():
            return True
    return False


class DocuSealService:
    """
    Thin async client for DocuSeal REST API + packet helpers.

    Typical Write Bond → e-sign flow:
      1. Staff selects surety + hydrates bond data
      2. create_submission_for_packet(...) with indemnitor(s) + defendant
      3. Parties open sign_url (or paperwork portal → link) after PIN
      4. Webhook submission.completed / form.completed
      5. download_combined_pdf + file to Completed Bonds Drive
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 60.0,
    ):
        raw = (base_url or os.getenv("DOCUSEAL_URL") or DEFAULT_DOCUSEAL_URL).strip()
        # Internal Docker DNS when dashboard talks to container on same network
        internal = os.getenv("DOCUSEAL_INTERNAL_URL", "").strip()
        self.public_url = raw.rstrip("/")
        self.base_url = (internal or raw).rstrip("/")
        self.api_key = (api_key if api_key is not None else os.getenv("DOCUSEAL_API_KEY", "")).strip()
        self.timeout = timeout

    # ── Config ──────────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    def _headers(self) -> Dict[str, str]:
        return {
            "X-Auth-Token": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def sign_url_for_slug(self, slug: str) -> str:
        """Build party-facing signing link (free OSS path — not Pro embed)."""
        slug = (slug or "").strip()
        if not slug:
            return ""
        # Prefer public URL so parties hit nginx, not docker hostname
        return f"{self.public_url}/s/{slug}"

    # ── Low-level HTTP ──────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[dict] = None,
    ) -> Any:
        if not self.is_configured:
            raise RuntimeError(
                "DocuSeal not configured — set DOCUSEAL_URL and DOCUSEAL_API_KEY "
                "(create API key in DocuSeal admin after first login)"
            )
        p = path if path.startswith("/") else "/" + path
        if not p.startswith("/api/"):
            p = f"/api{p}"
        url = f"{self.base_url}{p}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.request(
                method,
                url,
                headers=self._headers(),
                json=json,
                params=params,
            )
            if resp.status_code >= 400:
                body = (resp.text or "")[:500]
                logger.error(
                    "[docuseal] %s %s → %s %s",
                    method,
                    path,
                    resp.status_code,
                    body,
                )
                raise httpx.HTTPStatusError(
                    f"DocuSeal {method} {path} failed: {resp.status_code} {body}",
                    request=resp.request,
                    response=resp,
                )
            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()

    async def health(self) -> Dict[str, Any]:
        """Best-effort connectivity check (templates list)."""
        out: Dict[str, Any] = {
            "configured": self.is_configured,
            "base_url": self.base_url,
            "public_url": self.public_url,
            "ok": False,
            "error": None,
            "template_count": None,
        }
        if not self.is_configured:
            out["error"] = "missing_api_key_or_url"
            return out
        try:
            data = await self.list_templates(limit=5)
            items = data.get("data") if isinstance(data, dict) else data
            out["ok"] = True
            out["template_count"] = len(items or []) if isinstance(items, list) else None
        except Exception as exc:
            out["error"] = str(exc)[:300]
        return out

    # ── Templates ───────────────────────────────────────────────────────────

    async def list_templates(
        self,
        *,
        q: Optional[str] = None,
        limit: int = 50,
        folder: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": min(limit, 100)}
        if q:
            params["q"] = q
        if folder:
            params["folder"] = folder
        return await self._request("GET", "/templates", params=params)

    async def get_template(self, template_id: Union[int, str]) -> Dict[str, Any]:
        return await self._request("GET", f"/templates/{template_id}")

    async def archive_template(self, template_id: Union[int, str]) -> Dict[str, Any]:
        """Archive a template (soft-delete in DocuSeal)."""
        return await self._request("DELETE", f"/templates/{template_id}")

    async def clone_template(
        self,
        template_id: Union[int, str],
        *,
        name: Optional[str] = None,
        folder_name: Optional[str] = None,
        external_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Clone an existing template (ops / surety variants)."""
        body: Dict[str, Any] = {}
        if name:
            body["name"] = name
        if folder_name:
            body["folder_name"] = folder_name
        if external_id:
            body["external_id"] = external_id
        return await self._request("POST", f"/templates/{template_id}/clone", json=body or None)

    async def create_template_from_pdf(
        self,
        *,
        name: str,
        file_b64_or_url: str,
        external_id: Optional[str] = None,
        folder_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Upload a PDF as a DocuSeal template (admin/bootstrap).
        Field placement is finished in DocuSeal UI after upload if not tagged.
        """
        body: Dict[str, Any] = {
            "name": name,
            "documents": [{"name": name, "file": file_b64_or_url}],
        }
        if external_id:
            body["external_id"] = external_id
        if folder_name:
            body["folder_name"] = folder_name
        return await self._request("POST", "/templates/pdf", json=body)

    # ── Submissions ─────────────────────────────────────────────────────────

    async def list_submissions(
        self,
        *,
        template_id: Optional[Union[int, str]] = None,
        status: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 50,
        after: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List submissions (CLI: `docuseal submissions list`)."""
        params: Dict[str, Any] = {"limit": min(int(limit or 50), 100)}
        if template_id is not None and str(template_id).strip() != "":
            params["template_id"] = template_id
        if status:
            params["status"] = status
        if q:
            params["q"] = q
        if after:
            params["after"] = after
        return await self._request("GET", "/submissions", params=params)

    async def archive_submission(self, submission_id: Union[int, str]) -> Dict[str, Any]:
        """Archive a submission (CLI: `docuseal submissions archive`)."""
        return await self._request("DELETE", f"/submissions/{submission_id}")

    async def create_submission(
        self,
        *,
        template_id: Union[int, str],
        submitters: List[Dict[str, Any]],
        send_email: bool = False,
        order: str = "preserved",
        message: Optional[dict] = None,
        completed_redirect_url: Optional[str] = None,
        variables: Optional[dict] = None,
        expire_at: Optional[str] = None,
    ) -> Any:
        """
        Create a multi-party submission from an existing template.

        send_email defaults False — Shamrock portal/iMessage owns delivery;
        parties open sign links after PIN unlock.
        """
        body: Dict[str, Any] = {
            "template_id": int(template_id) if str(template_id).isdigit() else template_id,
            "send_email": send_email,
            "order": order,
            "submitters": submitters,
        }
        if message:
            body["message"] = message
        if completed_redirect_url:
            body["completed_redirect_url"] = completed_redirect_url
        if variables:
            body["variables"] = variables
        if expire_at:
            body["expire_at"] = expire_at
        return await self._request("POST", "/submissions", json=body)

    async def get_submission(self, submission_id: Union[int, str]) -> Dict[str, Any]:
        return await self._request("GET", f"/submissions/{submission_id}")

    async def get_submission_documents(
        self,
        submission_id: Union[int, str],
        *,
        merge: bool = True,
    ) -> Dict[str, Any]:
        return await self._request(
            "GET",
            f"/submissions/{submission_id}/documents",
            params={"merge": str(merge).lower()},
        )

    # ── Submitters (CLI parity for chase / resend / contact fix) ─────────────

    async def list_submitters(
        self,
        *,
        submission_id: Optional[Union[int, str]] = None,
        q: Optional[str] = None,
        slug: Optional[str] = None,
        external_id: Optional[str] = None,
        limit: int = 50,
        after: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List submitters (CLI: `docuseal submitters list`)."""
        params: Dict[str, Any] = {"limit": min(int(limit or 50), 100)}
        if submission_id is not None and str(submission_id).strip() != "":
            params["submission_id"] = submission_id
        if q:
            params["q"] = q
        if slug:
            params["slug"] = slug
        if external_id:
            params["external_id"] = external_id
        if after:
            params["after"] = after
        return await self._request("GET", "/submitters", params=params)

    async def get_submitter(self, submitter_id: Union[int, str]) -> Dict[str, Any]:
        return await self._request("GET", f"/submitters/{submitter_id}")

    async def update_submitter(
        self,
        submitter_id: Union[int, str],
        *,
        email: Optional[str] = None,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        send_email: Optional[bool] = None,
        send_sms: Optional[bool] = None,
        completed: Optional[bool] = None,
        values: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        external_id: Optional[str] = None,
        completed_redirect_url: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Update a submitter — contact fix, re-send, prefill, or mark completed.

        Mirrors CLI: `docuseal submitters update <id> --send-email …`
        """
        body: Dict[str, Any] = {}
        if email is not None:
            body["email"] = (email or "").strip()
        if name is not None:
            body["name"] = name
        if phone is not None:
            body["phone"] = phone
        if send_email is not None:
            body["send_email"] = bool(send_email)
        if send_sms is not None:
            body["send_sms"] = bool(send_sms)
        if completed is not None:
            body["completed"] = bool(completed)
        if values is not None:
            body["values"] = values
        if metadata is not None:
            body["metadata"] = metadata
        if external_id is not None:
            body["external_id"] = external_id
        if completed_redirect_url is not None:
            body["completed_redirect_url"] = completed_redirect_url
        if extra:
            body.update(extra)
        return await self._request("PUT", f"/submitters/{submitter_id}", json=body)

    def normalize_submitter_record(self, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Normalize a submitter API object to Shamrock packet shape (sign links)."""
        if not isinstance(raw, dict):
            return {}
        slug = (raw.get("slug") or "").strip()
        sign_url = raw.get("embed_src") or (self.sign_url_for_slug(slug) if slug else "")
        return {
            "id": raw.get("id"),
            "uuid": raw.get("uuid"),
            "submission_id": raw.get("submission_id"),
            "role": raw.get("role"),
            "email": raw.get("email"),
            "name": raw.get("name"),
            "phone": raw.get("phone"),
            "external_id": raw.get("external_id"),
            "status": raw.get("status"),
            "slug": slug,
            "sign_url": sign_url,
            "metadata": raw.get("metadata") or {},
            "completed_at": raw.get("completed_at"),
        }

    async def download_url_bytes(self, url: str) -> bytes:
        """Download a signed PDF from a DocuSeal file URL."""
        if not url:
            raise ValueError("empty download url")
        headers = {}
        if self.api_key:
            headers["X-Auth-Token"] = self.api_key
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.content

    async def download_combined_pdf(self, submission_id: Union[int, str]) -> bytes:
        """
        Fetch merged signed PDF bytes for a completed submission.
        Prefers combined_document_url / documents[0].url from API.
        """
        # Try documents endpoint with merge
        try:
            docs = await self.get_submission_documents(submission_id, merge=True)
            if isinstance(docs, dict):
                for d in docs.get("documents") or []:
                    u = d.get("url")
                    if u:
                        return await self.download_url_bytes(u)
        except Exception as exc:
            logger.warning("[docuseal] documents merge path failed: %s", exc)

        sub = await self.get_submission(submission_id)
        for key in ("combined_document_url", "audit_log_url"):
            u = sub.get(key)
            if u and key == "combined_document_url":
                return await self.download_url_bytes(u)
        for d in sub.get("documents") or []:
            u = d.get("url")
            if u:
                return await self.download_url_bytes(u)
        raise RuntimeError(f"No downloadable PDF for submission {submission_id}")

    # ── Packet helpers ──────────────────────────────────────────────────────

    @staticmethod
    def build_submitter(
        *,
        role: str,
        email: str,
        name: str = "",
        phone: str = "",
        external_id: str = "",
        values: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        send_email: bool = False,
        order: Optional[int] = None,
    ) -> Dict[str, Any]:
        s: Dict[str, Any] = {
            "role": role,
            "email": (email or "").strip() or f"unsigned+{uuid.uuid4().hex[:8]}@shamrockbailbonds.biz",
            "send_email": send_email,
        }
        if name:
            s["name"] = name
        if phone:
            raw_phone = str(phone).strip()
            digits = "".join(ch for ch in raw_phone if ch.isdigit())
            if digits:
                if not raw_phone.startswith("+"):
                    if len(digits) == 10:
                        s["phone"] = f"+1{digits}"
                    elif len(digits) == 11 and digits.startswith("1"):
                        s["phone"] = f"+{digits}"
                    else:
                        s["phone"] = f"+{digits}"
                else:
                    s["phone"] = raw_phone
        if external_id:
            s["external_id"] = external_id
        if values:
            s["values"] = values
        if metadata:
            s["metadata"] = metadata
        if order is not None:
            s["order"] = order
        return s

    @staticmethod
    def prefill_values_from_bond(bond_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map dashboard bond / intake fields → DocuSeal template field names.

        Template fields should use these names (or aliases set in DocuSeal UI).
        Mirrors keys used by SignNowPacketService._build_prefill_fields.
        """
        bond_data = bond_data or {}
        def_ = bond_data.get("defendant") if isinstance(bond_data.get("defendant"), dict) else {}
        ind = bond_data.get("indemnitor") if isinstance(bond_data.get("indemnitor"), dict) else {}

        defendant_name = (
            bond_data.get("defendant_name")
            or def_.get("name")
            or " ".join(
                filter(
                    None,
                    [
                        def_.get("firstName") or def_.get("first_name"),
                        def_.get("middleName") or def_.get("middle_name"),
                        def_.get("lastName") or def_.get("last_name"),
                    ],
                )
            )
            or "To Be Named"
        ).strip()
        indemnitor_name = (
            bond_data.get("indemnitor_name")
            or ind.get("name")
            or " ".join(
                filter(
                    None,
                    [
                        ind.get("firstName") or ind.get("first_name"),
                        ind.get("lastName") or ind.get("last_name"),
                    ],
                )
            )
            or ""
        ).strip()

        county = (
            bond_data.get("county")
            or bond_data.get("defendant_county")
            or def_.get("county")
            or ""
        )
        if county and not county.lower().endswith("county"):
            county_full = f"{county} County"
        else:
            county_full = county or "Lee County"

        court_type = (
            bond_data.get("court_type")
            or bond_data.get("CourtType")
            or "County/Circuit"
        )

        case_number = (
            bond_data.get("case_number")
            or bond_data.get("Case_Number")
            or def_.get("caseNumber")
            or def_.get("case_number")
            or ""
        )
        case_number = str(case_number or "").strip()

        poa_raw = bond_data.get("poa_number") or bond_data.get("POA_Number") or bond_data.get("poa_numbers") or ""
        if isinstance(poa_raw, list):
            poa_list = [str(x).strip() for x in poa_raw if str(x).strip()]
            poa = poa_list[0] if poa_list else ""
            poa_all = ", ".join(poa_list)
        else:
            poa_list = [x.strip() for x in str(poa_raw).split(",") if x.strip()]
            poa = poa_list[0] if poa_list else ""
            poa_all = ", ".join(poa_list)

        booking = (
            bond_data.get("booking_number")
            or bond_data.get("defendant_booking_number")
            or def_.get("booking_number")
            or ""
        )
        court_date = bond_data.get("court_date") or def_.get("court_date") or "TBN"
        if not str(court_date).strip():
            court_date = "TBN"

        # Format Charges Summary (truncated cleanly if > 3 charges)
        # Prefer structured charge_details (Write Bond / lead explorer) over free-text charges
        charges_raw = (
            bond_data.get("charge_details")
            or bond_data.get("charge_list")
            or bond_data.get("charges")
            or def_.get("charge_details")
            or def_.get("charges")
            or []
        )
        charges_list = []
        if isinstance(charges_raw, list):
            for c in charges_raw:
                if isinstance(c, dict):
                    desc = c.get("charge") or c.get("description") or c.get("name") or ""
                else:
                    desc = str(c) if c is not None else ""
                if desc.strip():
                    charges_list.append(desc.strip())
        elif isinstance(charges_raw, str) and charges_raw.strip():
            charges_list = [c.strip() for c in charges_raw.split(",") if c.strip()]

        # Primary case # from first structured charge if top-level missing
        if not case_number and isinstance(charges_raw, list):
            for c in charges_raw:
                if isinstance(c, dict):
                    cn = str(c.get("case_number") or c.get("Case_Number") or "").strip()
                    if cn:
                        case_number = cn
                        break
        if not case_number:
            case_number = "TBN"

        if len(charges_list) > 3:
            charges_summary = ", ".join(charges_list[:3]) + " (see case file)"
        elif charges_list:
            charges_summary = ", ".join(charges_list)
        else:
            charges_summary = bond_data.get("charges_summary") or "As charged"

        ind_address = (
            bond_data.get("indemnitor_address")
            or ind.get("address")
            or ""
        )
        def_address = (
            bond_data.get("defendant_address")
            or bond_data.get("address")
            or def_.get("address")
            or ""
        )

        now = datetime.now(timezone.utc)
        today_iso = now.strftime("%Y-%m-%d")
        today_slash = now.strftime("%m/%d/%Y")
        today_long = now.strftime("%B %d, %Y").replace(" 0", " ")  # e.g. August 7, 2026
        today_day = str(now.day)                                  # e.g. 7
        today_month = now.strftime("%B")                          # e.g. August
        today_year_2digit = now.strftime("%y")                    # e.g. 26

        raw_bond_amt = (
            bond_data.get("bond_amount")
            or bond_data.get("total_bond_amount")
            or bond_data.get("bond_amount_raw")
            or def_.get("bond_amount")
            or 0
        )
        bond_float = _safe_money(raw_bond_amt)
        # If no top-level bond amount, sum charge rows
        if bond_float <= 0 and isinstance(charges_raw, list):
            for c in charges_raw:
                if isinstance(c, dict):
                    bond_float += _safe_money(
                        c.get("bond_amount") or c.get("amount") or c.get("bond")
                    )

        bond_formatted = f"{bond_float:,.2f}" if bond_float > 0 else ""
        bond_formatted_dollar = f"${bond_float:,.2f}" if bond_float > 0 else ""
        bond_words = _amount_to_words(bond_float) if bond_float > 0 else ""

        # Florida statutory premium: 10% per charge, $100 minimum per charge
        explicit_prem = bond_data.get("premium_amount") or bond_data.get("premium") or bond_data.get("total_premium")
        if explicit_prem is not None and str(explicit_prem).strip() != "":
            prem_float = _safe_money(explicit_prem)
        elif isinstance(charges_raw, list) and any(
            isinstance(c, dict) and _safe_money(c.get("bond_amount") or c.get("amount") or c.get("bond")) > 0
            for c in charges_raw
        ):
            prem_float = 0.0
            for c in charges_raw:
                if isinstance(c, dict):
                    amt = _safe_money(c.get("bond_amount") or c.get("amount") or c.get("bond"))
                    if amt > 0:
                        prem_float += max(100.0, amt * 0.10)
            if prem_float <= 0 and bond_float > 0:
                prem_float = max(100.0, bond_float * 0.10)
        else:
            prem_float = max(100.0, bond_float * 0.10) if bond_float > 0 else 0.0

        prem_formatted = f"{prem_float:,.2f}" if prem_float > 0 else ""
        prem_formatted_dollar = f"${prem_float:,.2f}" if prem_float > 0 else ""
        prem_words = _amount_to_words(prem_float) if prem_float > 0 else ""

        # Build 4-row per-charge breakdown for legal schedule tables
        row_fields = {}
        for i in range(1, 5):
            idx = i - 1
            charge_obj = charges_raw[idx] if (isinstance(charges_raw, list) and idx < len(charges_raw)) else None
            if isinstance(charge_obj, dict):
                c_desc = charge_obj.get("charge") or charge_obj.get("description") or charge_obj.get("name") or ""
                c_case = charge_obj.get("case_number") or case_number
                c_poa = charge_obj.get("poa_number") or (poa_list[idx] if idx < len(poa_list) else poa)
                c_amt_float = _safe_money(
                    charge_obj.get("bond_amount") or charge_obj.get("amount") or charge_obj.get("bond") or 0
                )
                c_amt_str = f"{c_amt_float:,.2f}" if c_amt_float > 0 else ""
            elif isinstance(charge_obj, str) and charge_obj.strip():
                c_desc = charge_obj.strip()
                c_case = case_number
                c_poa = poa_list[idx] if idx < len(poa_list) else poa
                c_amt_str = bond_formatted if idx == 0 else ""
            elif idx == 0 and charges_summary:
                c_desc = charges_summary
                c_case = case_number
                c_poa = poa
                c_amt_str = bond_formatted
            else:
                c_desc, c_case, c_poa, c_amt_str = "", "", "", ""

            row_fields[f"offense_{i}"] = c_desc
            row_fields[f"charge_{i}"] = c_desc
            row_fields[f"case_number_{i}"] = c_case
            row_fields[f"case_{i}"] = c_case
            row_fields[f"poa_number_{i}"] = c_poa
            row_fields[f"poa_{i}"] = c_poa
            row_fields[f"bond_amount_{i}"] = c_amt_str
            row_fields[f"numeric_bond_amount_{i}"] = c_amt_str

        # Keys intentionally duplicated for OSI/Palmetto template naming variance
        values: Dict[str, Any] = {
            **row_fields,
            "defendant_name": defendant_name,
            "DefendantName": defendant_name,
            "FullName": indemnitor_name or defendant_name,
            "indemnitor_name": indemnitor_name,
            "IndemnitorName": indemnitor_name,
            "IndName": indemnitor_name,
            "county": county,
            "County": county,
            "county_full": county_full,
            "state": "FL",
            "State": "FL",
            "court_type": court_type,
            "CourtType": court_type,
            "charges_summary": charges_summary,
            "charges": charges_summary,
            "case_number": case_number,
            "CaseNum": case_number,
            "poa_number": poa,
            "PowerNum": poa,
            "all_poa_numbers": poa_all or poa,
            "poa_numbers": poa_all or poa,
            "bond_numbers": poa_all or poa,
            "BondNumbers": poa_all or poa,
            "booking_number": booking,
            "court_date": court_date,
            "CourtDate": court_date,
            "date": today_long,
            "Date": today_long,
            "today_date": today_slash,
            "today_date_long": today_long,
            "date_written_out": today_long,
            "bond_date_written": today_long,
            "formatted_date": today_long,
            "today_day": today_day,
            "bond_date_day": today_day,
            "today_month": today_month,
            "bond_date_month": today_month,
            "today_year_2digit": today_year_2digit,
            "bond_date_year_2digit": today_year_2digit,
            "numeric_full_bond_amount": bond_formatted,
            "numeric_full_bond_amount_dollar": bond_formatted_dollar,
            "bond_amount": bond_formatted_dollar,
            "bond_amount_numeric": bond_formatted,
            "bond_amount_words": bond_words,
            "bond_amount_written": bond_words,
            "full_bond_amount_words": bond_words,
            "numeric_premium": prem_formatted,
            "numeric_premium_dollar": prem_formatted_dollar,
            "premium_amount": prem_formatted_dollar,
            "premium_numeric": prem_formatted,
            "premium_words": prem_words,
            "premium_written": prem_words,
            "written_premium": prem_words,
            "indemnitor_address": ind_address,
            "defendant_address": def_address,
            "DefAddress": def_address,
            "defendant_phone": bond_data.get("defendant_phone") or def_.get("phone") or "",
            "defendant_email": bond_data.get("defendant_email") or def_.get("email") or "",
            "defendant_dob": bond_data.get("defendant_dob") or def_.get("dob") or def_.get("date_of_birth") or "",
            "defendant_dl": bond_data.get("defendant_dl") or def_.get("dl") or def_.get("dl_number") or "",
            "defendant_dl_state": bond_data.get("defendant_dl_state") or def_.get("dl_state") or "FL",
            "defendant_ssn": bond_data.get("defendant_ssn") or def_.get("ssn") or "",
            "defendant_city": bond_data.get("defendant_city") or def_.get("city") or "",
            "defendant_state": bond_data.get("defendant_state") or def_.get("state") or "FL",
            "defendant_zip": bond_data.get("defendant_zip") or def_.get("zip") or def_.get("zipcode") or "",
            "defendant_children_names_ages": bond_data.get("children_names_ages") or def_.get("children_names_ages") or "",
            "children_names_ages_1": bond_data.get("children_names_ages_1") or bond_data.get("children_names_ages") or def_.get("children_names_ages") or "",
            "children_names_ages_2": bond_data.get("children_names_ages_2") or "",
            "children_school_1": bond_data.get("children_school_1") or bond_data.get("children_school") or "",
            "children_school_2": bond_data.get("children_school_2") or "",
            "ssa_release_reason": bond_data.get("ssa_release_reason") or "Bail bond underwriting and supervision",
            "ssa_other_records_text": bond_data.get("ssa_other_records_text") or "any and all for location purposes",
            "down_payment_amount": bond_data.get("down_payment_amount") or bond_data.get("down_payment") or "",
            "balance_financed_amount": bond_data.get("balance_financed_amount") or bond_data.get("balance_financed") or "",
            "number_of_payments": bond_data.get("number_of_payments") or bond_data.get("num_payments") or "",
            "payment_amount": bond_data.get("payment_amount") or "",
            "first_payment_due_date": bond_data.get("first_payment_due_date") or bond_data.get("first_due_date") or "",
            "final_payment_due_date": bond_data.get("final_payment_due_date") or bond_data.get("final_due_date") or "",
            "payment_due_date_1": bond_data.get("payment_due_date_1") or "",
            "payment_amount_1": bond_data.get("payment_amount_1") or "",
            "payment_due_date_2": bond_data.get("payment_due_date_2") or "",
            "payment_amount_2": bond_data.get("payment_amount_2") or "",
            "payment_due_date_3": bond_data.get("payment_due_date_3") or "",
            "payment_amount_3": bond_data.get("payment_amount_3") or "",
            "payment_due_date_4": bond_data.get("payment_due_date_4") or "",
            "payment_amount_4": bond_data.get("payment_amount_4") or "",
            "indemnitor_phone": bond_data.get("indemnitor_phone") or ind.get("phone") or "",
            "indemnitor_email": bond_data.get("indemnitor_email") or ind.get("email") or "",
            "indemnitor_dob": bond_data.get("indemnitor_dob") or ind.get("dob") or "",
            "indemnitor_dl": bond_data.get("indemnitor_dl") or ind.get("dl") or ind.get("dl_number") or "",
            "indemnitor_ssn": bond_data.get("indemnitor_ssn") or ind.get("ssn") or "",
            "indemnitor_city": bond_data.get("indemnitor_city") or ind.get("city") or "",
            "indemnitor_state": bond_data.get("indemnitor_state") or ind.get("state") or "FL",
            "indemnitor_zip": bond_data.get("indemnitor_zip") or ind.get("zip") or "",
            "relationship": bond_data.get("relationship") or ind.get("relationship") or "",
            "indemnitor_relationship": bond_data.get("relationship") or ind.get("relationship") or "",
            "indemnitor_city_state_zip": bond_data.get("indemnitor_city_state_zip") or ind.get("city_state_zip") or "",
            "indemnitor_employer": bond_data.get("indemnitor_employer") or ind.get("employer") or "",
            "indemnitor_employer_phone": bond_data.get("indemnitor_employer_phone") or ind.get("employer_phone") or "",
            "indemnitor_employer_address": bond_data.get("indemnitor_employer_address") or ind.get("employer_address") or "",
            "AgencyName": "Shamrock Bail Bonds",
            "agency_name": "Shamrock Bail Bonds",
            "AgentName": os.getenv("BOND_AGENT_NAME", "Brendan O'Neal"),
            "agent_name": os.getenv("BOND_AGENT_NAME", "Brendan O'Neal"),
            "AgentLicense": os.getenv("BOND_AGENT_LICENSE", "P139768"),
            "agent_license": os.getenv("BOND_AGENT_LICENSE", "P139768"),
            "bondsman_name": os.getenv("BOND_AGENT_NAME", "Brendan O'Neal"),
            "bondsman_license": os.getenv("BOND_AGENT_LICENSE", "P139768"),
        }
        # Drop empty strings so DocuSeal doesn't overwrite blank required fields with ""
        return {k: v for k, v in values.items() if v is not None and str(v).strip() != ""}

    def normalize_create_response(self, raw: Any) -> Dict[str, Any]:
        """
        Create submission returns either a list of submitters or a submission object.
        Normalize to { submission_id, submitters: [{id, role, email, slug, sign_url, ...}] }
        """
        submitters: List[Dict[str, Any]] = []
        submission_id = None

        if isinstance(raw, list):
            submitters = list(raw)
            if submitters:
                submission_id = submitters[0].get("submission_id")
        elif isinstance(raw, dict):
            submission_id = raw.get("id") or raw.get("submission_id")
            submitters = list(raw.get("submitters") or [])
            if not submitters and raw.get("slug"):
                # single-submitter shape
                submitters = [raw]
                submission_id = raw.get("submission_id") or submission_id

        normalized = []
        for s in submitters:
            slug = s.get("slug") or ""
            embed = s.get("embed_src") or ""
            sign_url = embed or self.sign_url_for_slug(slug)
            normalized.append(
                {
                    "id": s.get("id"),
                    "uuid": s.get("uuid"),
                    "submission_id": s.get("submission_id") or submission_id,
                    "role": s.get("role"),
                    "email": s.get("email"),
                    "name": s.get("name"),
                    "phone": s.get("phone"),
                    "external_id": s.get("external_id"),
                    "status": s.get("status"),
                    "slug": slug,
                    "sign_url": sign_url,
                    "metadata": s.get("metadata") or {},
                }
            )
        if submission_id is None and normalized:
            submission_id = normalized[0].get("submission_id")

        return {
            "submission_id": submission_id,
            "submitters": normalized,
            "raw": raw,
        }

    async def create_submission_for_packet(
        self,
        *,
        template_id: Union[int, str],
        packet_id: str,
        bond_data: Dict[str, Any],
        indemnitors: Optional[List[Dict[str, Any]]] = None,
        defendant: Optional[Dict[str, Any]] = None,
        send_email: bool = False,
        order: str = "preserved",
        include_defendant: bool = True,
        completed_redirect_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Build multi-party submitters from bond/packet context and create submission.

        indemnitors: list of {name, email, phone, ...}; falls back to bond_data indemnitor.
        defendant: optional override; falls back to bond_data defendant fields.
        """
        bond_data = dict(bond_data or {})
        values = self.prefill_values_from_bond(bond_data)

        # Collect indemnitors (primary + co-indemnitors)
        inds: List[Dict[str, Any]] = []
        if indemnitors:
            inds = [i for i in indemnitors if _nonempty_party(i)]
        elif isinstance(bond_data.get("indemnitors"), list):
            inds = [i for i in bond_data["indemnitors"] if _nonempty_party(i)]
        if not inds:
            primary = {
                "name": values.get("indemnitor_name") or bond_data.get("indemnitor_name"),
                "email": values.get("indemnitor_email") or bond_data.get("indemnitor_email"),
                "phone": values.get("indemnitor_phone") or bond_data.get("indemnitor_phone"),
            }
            if _nonempty_party(primary):
                inds = [primary]

        submitters: List[Dict[str, Any]] = []
        for idx, ind in enumerate(inds):
            # Template roles: Indemnitor, Co-Indemnitor, Indemnitor 3+
            role = _role_for_indemnitor_index(idx)
            email = (ind.get("email") or "").strip()
            name = (
                ind.get("name")
                or ind.get("full_name")
                or " ".join(
                    filter(
                        None,
                        [
                            ind.get("firstName") or ind.get("first_name"),
                            ind.get("lastName") or ind.get("last_name"),
                        ],
                    )
                )
                or ""
            ).strip()
            phone = (ind.get("phone") or "").strip()
            party_role = "indemnitor" if idx == 0 else "co_indemnitor"
            submitters.append(
                self.build_submitter(
                    role=role,
                    email=email,
                    name=name,
                    phone=phone,
                    external_id=f"{packet_id}:indemnitor:{idx}",
                    values=values,
                    metadata={
                        "packet_id": packet_id,
                        "party_role": party_role,
                        "indemnitor_index": idx,
                    },
                    send_email=send_email,
                    order=idx,
                )
            )

        if include_defendant:
            def_info = defendant if isinstance(defendant, dict) else {}
            def_name = (
                def_info.get("name")
                or values.get("defendant_name")
                or bond_data.get("defendant_name")
                or ""
            )
            def_email = (
                def_info.get("email")
                or bond_data.get("defendant_email")
                or ""
            )
            def_phone = (
                def_info.get("phone")
                or bond_data.get("defendant_phone")
                or ""
            )
            submitters.append(
                self.build_submitter(
                    role=ROLE_DEFENDANT,
                    email=def_email,
                    name=def_name,
                    phone=def_phone,
                    external_id=f"{packet_id}:defendant",
                    values=values,
                    metadata={
                        "packet_id": packet_id,
                        "party_role": "defendant",
                    },
                    send_email=send_email,
                    order=len(submitters),
                )
            )

        # Optional Bondsman / agent role (template may require signature block)
        include_bondsman = bool(
            bond_data.get("include_bondsman")
            or os.getenv("DOCUSEAL_INCLUDE_BONDSMAN", "false").lower() in ("1", "true", "yes")
        )
        if include_bondsman:
            agent_name = os.getenv("BOND_AGENT_NAME", "Brendan O'Neal")
            agent_email = (
                bond_data.get("bondsman_email")
                or os.getenv("BOND_AGENT_EMAIL", "admin@shamrockbailbonds.biz")
            )
            submitters.append(
                self.build_submitter(
                    role=ROLE_BONDSMAN,
                    email=agent_email,
                    name=agent_name,
                    phone=os.getenv("BOND_AGENT_PHONE", "2393322245"),
                    external_id=f"{packet_id}:bondsman",
                    values=values,
                    metadata={"packet_id": packet_id, "party_role": "bondsman"},
                    send_email=False,
                    order=len(submitters),
                )
            )

        if not submitters:
            raise ValueError("No submitters to create DocuSeal submission")

        portal = (os.getenv("PAPERWORK_PUBLIC_URL") or "").rstrip("/")
        redirect = completed_redirect_url or (f"{portal}/done" if portal else None)

        raw = await self.create_submission(
            template_id=template_id,
            submitters=submitters,
            send_email=send_email,
            order=order,
            completed_redirect_url=redirect,
        )
        result = self.normalize_create_response(raw)
        result["packet_id"] = packet_id
        result["template_id"] = template_id
        result["created_at"] = datetime.now(timezone.utc).isoformat()
        result["esign_provider"] = "docuseal"
        return result

    # ── Drive archive ───────────────────────────────────────────────────────

    def file_signed_pdf_to_drive(
        self,
        pdf_bytes: bytes,
        *,
        defendant_name: str,
        surety_id: str = "osi",
        packet_id: str = "",
        booking_number: str = "",
    ) -> Dict[str, Any]:
        """
        Upload signed packet to:
          Completed Bonds / {OSI|PALMETTO} / {Last, F_YYYYMMDD} /
        """
        from dashboard.services.google_drive_service import GoogleDriveService

        if not pdf_bytes:
            return {"ok": False, "error": "empty_pdf"}

        drive = GoogleDriveService()
        if not drive.is_configured:
            logger.warning("[docuseal] Google Drive OAuth not configured — skip archive")
            return {"ok": False, "error": "google_drive_not_configured"}

        root = (
            os.getenv("COMPLETED_BONDS_FOLDER_ID")
            or os.getenv("GOOGLE_DRIVE_OUTPUT_FOLDER_ID")
            or DEFAULT_COMPLETED_BONDS_FOLDER
        )
        surety = (surety_id or "osi").lower().strip()
        if surety not in ("osi", "palmetto"):
            surety = "osi"
        surety_label = surety.upper()

        mmddyy = datetime.now().strftime("%m%d%y")
        safe_full = (defendant_name or "Unknown").replace("/", "-").strip() or "Unknown"

        # Extract defendant's last name (handles "Doe, John" or "John Doe")
        if "," in safe_full:
            last_name = safe_full.split(",")[0].strip()
        else:
            parts = safe_full.split()
            last_name = parts[-1].strip() if parts else "Unknown"

        last_name_clean = re.sub(r"[^\w\-]", "", last_name.replace(" ", "_")) or "Unknown"
        date_str = datetime.now().strftime("%Y%m%d")
        folder_name = f"{safe_full.replace(' ', '_')}_{date_str}"
        filename = f"{last_name_clean}_{mmddyy}_{surety_label}.pdf"

        # Prefer Completed Bonds / {Surety} / {Defendant_YYYYMMDD}/
        # Fall back to surety folder, then root, if nested create fails.
        upload_folder = None
        try:
            surety_folder = drive.get_or_create_folder(surety_label, root)
            if surety_folder:
                upload_folder = drive.get_or_create_folder(folder_name, surety_folder) or surety_folder
            else:
                logger.warning(
                    "[docuseal] surety folder failed — uploading under Completed Bonds root"
                )
                upload_folder = root
        except Exception as exc:
            logger.warning("[docuseal] Drive folder create error: %s — using root", exc)
            upload_folder = root

        if not upload_folder:
            return {"ok": False, "error": "no_upload_folder"}

        try:
            link = drive.upload_pdf(pdf_bytes, filename, upload_folder)
        except Exception as exc:
            logger.error("[docuseal] Drive upload exception: %s", exc)
            return {"ok": False, "error": f"upload_exception:{exc}"[:200]}

        if not link:
            return {"ok": False, "error": "upload_failed"}
        return {
            "ok": True,
            "drive_url": link,
            "drive_folder_id": upload_folder,
            "filename": filename,
            "surety": surety_label,
        }


def resolve_template_id_for_surety(surety_id: str = "osi") -> Optional[str]:
    """
    Env-based template IDs until admin maps all packet docs in DocuSeal UI.

      DOCUSEAL_TEMPLATE_ID_OSI      — OSI combined packet (preferred for osi)
      DOCUSEAL_TEMPLATE_ID          — OSI fallback
      DOCUSEAL_TEMPLATE_ID_PALMETTO — Palmetto only (no silent OSI fallback)

    Palmetto requires its own template id so we never send Palmetto bonds
    to the OSI DocuSeal form by accident.
    """
    surety = (surety_id or "osi").lower().strip()
    if surety == "palmetto":
        tid = (os.getenv("DOCUSEAL_TEMPLATE_ID_PALMETTO") or "").strip()
        return tid or None
    tid = (
        os.getenv("DOCUSEAL_TEMPLATE_ID_OSI")
        or os.getenv("DOCUSEAL_TEMPLATE_ID")
        or ""
    ).strip()
    return tid or None


def build_bond_data_from_dashboard(
    *,
    ctx: Optional[Dict[str, Any]] = None,
    intake_doc: Optional[Dict[str, Any]] = None,
    field_overrides: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    surety_id: str = "osi",
) -> Dict[str, Any]:
    """
    Merge packet-builder context + UI overrides into the shape expected by
    prefill_values_from_bond / create_submission_for_packet.

    This is the single alignment point between the dashboard and DocuSeal.
    """
    ctx = ctx or {}
    intake_doc = intake_doc or {}
    overrides = field_overrides if isinstance(field_overrides, dict) else {}
    body = body if isinstance(body, dict) else {}
    def_ = ctx.get("defendant") if isinstance(ctx.get("defendant"), dict) else {}
    ind = ctx.get("indemnitor") if isinstance(ctx.get("indemnitor"), dict) else {}

    # Structured charges from lead / write-bond / body
    charge_details = (
        body.get("charge_details")
        or body.get("charge_list")
        or ctx.get("charge_details")
        or ctx.get("charge_list")
        or intake_doc.get("charge_details")
        or intake_doc.get("charge_list")
    )
    charges = ctx.get("charges") or intake_doc.get("charges") or body.get("charges")

    bond_amount = (
        overrides.get("bond_amount")
        or body.get("bond_amount")
        or ctx.get("bond_amount")
        or intake_doc.get("bond_amount")
        or 0
    )
    premium_amount = (
        body.get("premium_amount")
        or ctx.get("premium_amount")
        or intake_doc.get("premium_amount")
    )

    poa = (
        overrides.get("poa_number")
        or body.get("poa_number")
        or ctx.get("poa_number")
        or ""
    )
    # Multi-POA list if provided
    poa_numbers = body.get("poa_numbers") or ctx.get("poa_numbers") or poa

    bond_data: Dict[str, Any] = {
        **intake_doc,
        "surety_id": (surety_id or ctx.get("surety_id") or "osi").lower(),
        "defendant": def_ or intake_doc.get("defendant") or {},
        "indemnitor": ind or intake_doc.get("indemnitor") or {},
        "defendant_name": overrides.get("defendant_name")
            or def_.get("name")
            or intake_doc.get("defendant_name")
            or ctx.get("defendant_name")
            or "",
        "defendant_dob": overrides.get("defendant_dob") or def_.get("dob") or "",
        "defendant_phone": overrides.get("defendant_phone") or def_.get("phone") or "",
        "defendant_email": overrides.get("defendant_email") or def_.get("email") or "",
        "defendant_address": overrides.get("defendant_address") or def_.get("address") or "",
        "defendant_city": def_.get("city") or "",
        "defendant_state": def_.get("state") or "FL",
        "defendant_zip": def_.get("zip") or "",
        "defendant_dl": def_.get("dl") or "",
        "defendant_dl_state": def_.get("dl_state") or "FL",
        "defendant_ssn": def_.get("ssn") or "",
        "indemnitor_name": overrides.get("indemnitor_name")
            or ind.get("name")
            or intake_doc.get("indemnitor_name")
            or "",
        "indemnitor_phone": overrides.get("indemnitor_phone")
            or ind.get("phone")
            or intake_doc.get("indemnitor_phone")
            or "",
        "indemnitor_email": overrides.get("indemnitor_email")
            or ind.get("email")
            or intake_doc.get("indemnitor_email")
            or body.get("signer_email")
            or "",
        "indemnitor_address": overrides.get("indemnitor_address") or ind.get("address") or "",
        "indemnitor_dob": overrides.get("indemnitor_dob") or ind.get("dob") or "",
        "indemnitor_dl": ind.get("dl") or "",
        "indemnitor_ssn": ind.get("ssn") or "",
        "indemnitor_city": ind.get("city") or "",
        "indemnitor_state": ind.get("state") or "FL",
        "indemnitor_zip": ind.get("zip") or "",
        "relationship": ind.get("relationship") or "",
        "county": ctx.get("county") or intake_doc.get("defendant_county") or intake_doc.get("county") or "",
        "case_number": overrides.get("case_number")
            or ctx.get("case_number")
            or intake_doc.get("case_number")
            or "",
        "booking_number": overrides.get("booking_number")
            or ctx.get("booking_number")
            or intake_doc.get("defendant_booking_number")
            or "",
        "poa_number": poa,
        "poa_numbers": poa_numbers,
        "bond_amount": bond_amount,
        "premium_amount": premium_amount,
        "court_date": ctx.get("court_date") or body.get("court_date") or "TBN",
        "charges": charges,
        "charge_details": charge_details,
        # Payment plan (optional UI / body)
        "down_payment_amount": body.get("down_payment_amount") or body.get("down_payment"),
        "balance_financed_amount": body.get("balance_financed_amount") or body.get("balance_financed"),
        "number_of_payments": body.get("number_of_payments") or body.get("num_payments"),
        "payment_amount": body.get("payment_amount"),
        "first_payment_due_date": body.get("first_payment_due_date") or body.get("first_due_date"),
        "final_payment_due_date": body.get("final_payment_due_date") or body.get("final_due_date"),
        "payment_due_date_1": body.get("payment_due_date_1"),
        "payment_amount_1": body.get("payment_amount_1"),
        "payment_due_date_2": body.get("payment_due_date_2"),
        "payment_amount_2": body.get("payment_amount_2"),
        "payment_due_date_3": body.get("payment_due_date_3"),
        "payment_amount_3": body.get("payment_amount_3"),
        "payment_due_date_4": body.get("payment_due_date_4"),
        "payment_amount_4": body.get("payment_amount_4"),
    }

    # Multi-indemnitor list for Co-Indemnitor role
    inds = body.get("indemnitors") or ctx.get("indemnitors") or intake_doc.get("indemnitors")
    if isinstance(inds, list) and inds:
        bond_data["indemnitors"] = inds
    else:
        bond_data["indemnitors"] = [
            {
                "name": bond_data.get("indemnitor_name"),
                "email": bond_data.get("indemnitor_email"),
                "phone": bond_data.get("indemnitor_phone"),
            }
        ]

    return bond_data


def readiness_report(bond_data: Optional[Dict[str, Any]] = None, surety_id: str = "osi") -> Dict[str, Any]:
    """
    Dashboard-facing readiness check: env + template + sample prefill key count.
    """
    svc = get_docuseal_service()
    tid = resolve_template_id_for_surety(surety_id)
    values = DocuSealService.prefill_values_from_bond(bond_data or {})
    required_for_sign = ["defendant_name", "indemnitor_name", "county"]
    missing = [k for k in required_for_sign if not values.get(k)]
    return {
        "configured": svc.is_configured,
        "public_url": svc.public_url,
        "base_url": svc.base_url,
        "surety_id": surety_id,
        "template_id": tid,
        "template_ready": bool(tid),
        "prefill_key_count": len(values),
        "prefill_keys": sorted(values.keys()),
        "sample_values": {k: values[k] for k in sorted(values.keys())[:40]},
        "missing_core_fields": missing,
        "ready_to_send": bool(svc.is_configured and tid and not missing),
        "hints": [
            h
            for h in [
                None if svc.is_configured else "Set DOCUSEAL_API_KEY (+ DOCUSEAL_URL)",
                None if tid else (
                    "Set DOCUSEAL_TEMPLATE_ID_PALMETTO for Palmetto"
                    if surety_id == "palmetto"
                    else "Set DOCUSEAL_TEMPLATE_ID_OSI or DOCUSEAL_TEMPLATE_ID"
                ),
                None if not missing else f"Missing prefill: {', '.join(missing)}",
            ]
            if h
        ],
    }


def get_docuseal_service() -> DocuSealService:
    """Factory for FastAPI routes."""
    return DocuSealService()
