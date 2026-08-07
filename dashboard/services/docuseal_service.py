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

# Role names used on DocuSeal templates (must match field role assignment in UI)
ROLE_INDEMNITOR = "Indemnitor"
ROLE_DEFENDANT = "Defendant"
ROLE_INDEMNITOR_N = "Indemnitor {n}"  # multi-indemnitor templates


def _number_to_words(n: int) -> str:
    if n <= 0:
        return "Zero"
    units = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
             "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
    if n < 20:
        return units[n]
    if n < 100:
        return tens[n // 10] + (" " + units[n % 10] if n % 10 != 0 else "")
    if n < 1000:
        return units[n // 100] + " Hundred" + (" " + _number_to_words(n % 100) if n % 100 != 0 else "")
    if n < 1000000:
        return _number_to_words(n // 1000) + " Thousand" + (" " + _number_to_words(n % 1000) if n % 1000 != 0 else "")
    return str(n)


def _amount_to_words(val: Any) -> str:
    try:
        clean = float(str(val).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return str(val or "")
    dollars = int(clean)
    cents = int(round((clean - dollars) * 100))
    return f"{_number_to_words(dollars)} and {cents:02d}/100"


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
            s["phone"] = phone
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
            or ""
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
        ).strip()
        if not case_number:
            case_number = "TBN"

        poa_raw = bond_data.get("poa_number") or bond_data.get("POA_Number") or bond_data.get("poa_numbers") or ""
        if isinstance(poa_raw, list):
            poa = str(poa_raw[0]) if poa_raw else ""
        else:
            poa = str(poa_raw).split(",")[0].strip()

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
        charges_raw = bond_data.get("charges") or bond_data.get("charge_details") or def_.get("charges") or []
        charges_list = []
        if isinstance(charges_raw, list):
            for c in charges_raw:
                if isinstance(c, dict):
                    desc = c.get("charge") or c.get("description") or c.get("name") or ""
                else:
                    desc = str(c)
                if desc.strip():
                    charges_list.append(desc.strip())
        elif isinstance(charges_raw, str) and charges_raw.strip():
            charges_list = [c.strip() for c in charges_raw.split(",") if c.strip()]

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
        try:
            bond_float = float(str(raw_bond_amt).replace("$", "").replace(",", "").strip())
        except (ValueError, TypeError):
            bond_float = 0.0

        bond_formatted = f"{bond_float:,.2f}" if bond_float > 0 else ""
        bond_formatted_dollar = f"${bond_float:,.2f}" if bond_float > 0 else ""
        bond_words = _amount_to_words(bond_float) if bond_float > 0 else ""

        # Calculate Florida Statutory Premium (10% per charge, $100 min per charge)
        explicit_prem = bond_data.get("premium_amount") or bond_data.get("premium") or bond_data.get("total_premium")
        if explicit_prem is not None:
            try:
                prem_float = float(str(explicit_prem).replace("$", "").replace(",", "").strip())
            except (ValueError, TypeError):
                prem_float = 0.0
        elif isinstance(charges_raw, list) and charges_raw and any(isinstance(c, dict) and "bond_amount" in c for c in charges_raw):
            prem_float = 0.0
            for c in charges_raw:
                if isinstance(c, dict):
                    try:
                        amt = float(str(c.get("bond_amount", 0)).replace("$", "").replace(",", "").strip())
                        if amt > 0:
                            prem_float += max(100.0, amt * 0.10)
                    except (ValueError, TypeError):
                        pass
            if prem_float == 0.0 and bond_float > 0:
                prem_float = max(100.0, bond_float * 0.10)
        else:
            prem_float = max(100.0, bond_float * 0.10) if bond_float > 0 else 0.0

        prem_formatted = f"{prem_float:,.2f}" if prem_float > 0 else ""
        prem_formatted_dollar = f"${prem_float:,.2f}" if prem_float > 0 else ""
        prem_words = _amount_to_words(prem_float) if prem_float > 0 else ""

        # Keys intentionally duplicated for OSI/Palmetto template naming variance
        values: Dict[str, Any] = {
            "defendant_name": defendant_name,
            "DefendantName": defendant_name,
            "FullName": indemnitor_name or defendant_name,
            "indemnitor_name": indemnitor_name,
            "IndemnitorName": indemnitor_name,
            "IndName": indemnitor_name,
            "county": county,
            "County": county,
            "county_full": county_full,
            "court_type": court_type,
            "CourtType": court_type,
            "charges_summary": charges_summary,
            "charges": charges_summary,
            "case_number": case_number,
            "CaseNum": case_number,
            "poa_number": poa,
            "PowerNum": poa,
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
            "defendant_zip": bond_data.get("defendant_zip") or def_.get("zip") or "",
            "indemnitor_phone": bond_data.get("indemnitor_phone") or ind.get("phone") or "",
            "indemnitor_email": bond_data.get("indemnitor_email") or ind.get("email") or "",
            "indemnitor_dob": bond_data.get("indemnitor_dob") or ind.get("dob") or "",
            "indemnitor_dl": bond_data.get("indemnitor_dl") or ind.get("dl") or ind.get("dl_number") or "",
            "indemnitor_ssn": bond_data.get("indemnitor_ssn") or ind.get("ssn") or "",
            "relationship": bond_data.get("relationship") or ind.get("relationship") or "",
            "indemnitor_relationship": bond_data.get("relationship") or ind.get("relationship") or "",
            "indemnitor_city_state_zip": bond_data.get("indemnitor_city_state_zip") or ind.get("city_state_zip") or "",
            "indemnitor_employer": bond_data.get("indemnitor_employer") or ind.get("employer") or "",
            "indemnitor_employer_phone": bond_data.get("indemnitor_employer_phone") or ind.get("employer_phone") or "",
            "indemnitor_employer_address": bond_data.get("indemnitor_employer_address") or ind.get("employer_address") or "",
            "AgencyName": "Shamrock Bail Bonds",
            "AgentName": os.getenv("BOND_AGENT_NAME", "Brendan O'Neal"),
            "AgentLicense": os.getenv("BOND_AGENT_LICENSE", "P139768"),
        }
        # Drop empty strings so DocuSeal doesn't overwrite blank
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

        # Collect indemnitors
        inds: List[Dict[str, Any]] = []
        if indemnitors:
            inds = list(indemnitors)
        elif bond_data.get("indemnitors"):
            inds = list(bond_data["indemnitors"])
        else:
            inds = [
                {
                    "name": values.get("indemnitor_name") or bond_data.get("indemnitor_name"),
                    "email": values.get("indemnitor_email") or bond_data.get("indemnitor_email"),
                    "phone": values.get("indemnitor_phone") or bond_data.get("indemnitor_phone"),
                }
            ]
        inds = [i for i in inds if i]

        submitters: List[Dict[str, Any]] = []
        for idx, ind in enumerate(inds):
            role = ROLE_INDEMNITOR if idx == 0 else ROLE_INDEMNITOR_N.format(n=idx + 1)
            # Many templates only define "Indemnitor" once — use that for first,
            # extra indemnitors need multi-role template fields.
            if idx == 0:
                role = ROLE_INDEMNITOR
            email = (ind.get("email") or "").strip()
            name = (ind.get("name") or ind.get("full_name") or "").strip()
            phone = (ind.get("phone") or "").strip()
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
                        "party_role": "indemnitor",
                        "indemnitor_index": idx,
                    },
                    send_email=send_email,
                    order=idx,
                )
            )

        if include_defendant:
            def_info = defendant or {}
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

        drive = GoogleDriveService()
        if not drive.is_configured:
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

        surety_folder = drive.get_or_create_folder(surety_label, root)
        if not surety_folder:
            return {"ok": False, "error": "surety_folder_failed"}

        date_str = datetime.now().strftime("%Y%m%d")
        safe = (defendant_name or "Unknown").replace("/", "-").strip() or "Unknown"
        folder_name = f"{safe.replace(' ', '_')}_{date_str}"
        def_folder = drive.get_or_create_folder(folder_name, surety_folder)
        if not def_folder:
            return {"ok": False, "error": "defendant_folder_failed"}

        booking_part = (booking_number or "nobooking")[:32]
        pkt = (packet_id or "packet")[:24]
        filename = f"SIGNED_{safe.replace(' ', '_')}_{booking_part}_{pkt}_docuseal.pdf"
        link = drive.upload_pdf(pdf_bytes, filename, def_folder)
        if not link:
            return {"ok": False, "error": "upload_failed"}
        return {
            "ok": True,
            "drive_url": link,
            "drive_folder_id": def_folder,
            "filename": filename,
            "surety": surety_label,
        }


def resolve_template_id_for_surety(surety_id: str = "osi") -> Optional[str]:
    """
    Env-based template IDs until admin maps all packet docs in DocuSeal UI.

      DOCUSEAL_TEMPLATE_ID          — default / fallback
      DOCUSEAL_TEMPLATE_ID_OSI      — OSI combined packet template
      DOCUSEAL_TEMPLATE_ID_PALMETTO — Palmetto combined packet template
    """
    surety = (surety_id or "osi").lower().strip()
    if surety == "palmetto":
        tid = (
            os.getenv("DOCUSEAL_TEMPLATE_ID_PALMETTO")
            or os.getenv("DOCUSEAL_TEMPLATE_ID")
            or ""
        ).strip()
    else:
        tid = (
            os.getenv("DOCUSEAL_TEMPLATE_ID_OSI")
            or os.getenv("DOCUSEAL_TEMPLATE_ID")
            or ""
        ).strip()
    return tid or None


def get_docuseal_service() -> DocuSealService:
    """Factory for FastAPI routes."""
    return DocuSealService()
