"""
ShamrockLeads — SignNow Packet Service
Handles two-phase SignNow packet assembly, template mapping, document
multiplication, field prefill, document grouping, and embedded invite
generation. Migrated from GAS SignNow_SendPaperwork.js.

Phase 1 — Indemnitor signs first (no POA required):
  paperwork-header, faq-cosigners, indemnity-agreement,
  promissory-note, disclosure-form, ssa-release

Phase 2 — After bondsman approval + POA entry:
  faq-defendants, defendant-application, surety-terms,
  master-waiver, collateral-receipt, payment-plan
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# SignNow API base
SIGNNOW_BASE = "https://api.signnow.com"

# Agent constants
AGENT_NAME = "Brendan O'Shaughnahill"
AGENT_LICENSE = "P322089"
AGENCY_NAME = "Shamrock Bail Bonds"
AGENCY_PHONE = "(239) 244-4114"


class SignNowPacketService:
    """
    Handles the two-phase SignNow packet assembly, template mapping,
    document multiplication, prefilling, grouping, and inviting.

    Migrated from GAS SignNow_SendPaperwork.js and Telegram_Documents.js.
    """

    # ─── Single Source of Truth for Template IDs ────────────────────────
    # All template IDs live HERE and only here.
    # extensions.py references this location — do NOT duplicate there.
    #
    # To add Palmetto-specific templates:
    #   1. Create the template in SignNow (admin@shamrockbailbonds.biz)
    #   2. Add the ID below under "Palmetto-Specific Overrides"
    #   3. The surety-routing logic in build_packet_manifest() will pick it up
    # ──────────────────────────────────────────────────────────────────────
    TEMPLATE_MAP = {
        # ── Shared Templates (used by both OSI and Palmetto) ──
        "paperwork-header":      "9b9dad3e319f4b1580094e05f9844929d5a6f7de",
        "faq-cosigners":         "0820b9fef3bd4c38a91643455881021f3f0c3a88",
        "faq-defendants":        "1524f1c816c54a72be76d14fe128e4a6034579dc",
        "indemnity-agreement":   "ed5e6ca0a3444796a127fbeb6a880658371aafd7",
        "defendant-application": "d50adc808f3245f087b218d33da89e4ace15ecd4",
        "promissory-note":       "460bd43c2f514305a3b296481713a00ee8311c79",
        "disclosure-form":       "fb8b57bf55ac4d5e8bff820b018a0bfd3b17a37a",
        "surety-terms":          "192aeb246230446bb0d7f658765afd2832704964",
        "master-waiver":         "3b0e71188b3049cc8760d144e6c49df227ccd741",
        "ssa-release":           "4800defff07541079760889d83109059585b0cea",
        # ── OSI-Specific Templates ──
        "appearance-bond":       "7ba703e101e04604a2f1458c21d3addfce9ca86b",  # Appearance Bond blank (OSI)
        "collateral-receipt":    "4b1f5611840f4de4bc891677617f5dbf6ff7ad05",  # osi-premium-collateral-template
        "payment-plan":          "1861b158d7a447d48be5ac1dd24755f727f0773b",  # shamrock-premium-finance-notice

        # ── Palmetto-Specific Overrides ──
        "appearance-bond-palmetto":    "9b1d3d0b64004153b347ceccda07420a906350e5",  # shamrock-palmetto-appearance-bond
        # TODO: Add remaining Palmetto template IDs once created in SignNow
        # "collateral-receipt-palmetto": "<TEMPLATE_ID>",
        # "payment-plan-palmetto":       "<TEMPLATE_ID>",
    }

    # Document Multiplication Rules
    DOC_RULES = {
        "paperwork-header":      {"rule": "static",         "label": "Paperwork Header"},
        "faq-cosigners":         {"rule": "shared",         "label": "FAQ - Cosigners"},
        "faq-defendants":        {"rule": "shared",         "label": "FAQ - Defendants"},
        "indemnity-agreement":   {"rule": "per-indemnitor", "label": "Indemnity Agreement"},
        "defendant-application": {"rule": "static",         "label": "Defendant Application"},
        "promissory-note":       {"rule": "shared",         "label": "Promissory Note"},
        "disclosure-form":       {"rule": "shared",         "label": "Disclosure Form"},
        "surety-terms":          {"rule": "shared",         "label": "Surety Terms"},
        "master-waiver":         {"rule": "shared",         "label": "Master Waiver"},
        "ssa-release":           {"rule": "per-person",     "label": "SSA Release"},
        "collateral-receipt":    {"rule": "shared",         "label": "Collateral & Premium Receipt"},
        "payment-plan":          {"rule": "shared",         "label": "Payment Plan Agreement"},
        "appearance-bond":       {"rule": "print-only",     "label": "Appearance Bond (Print Only)"},
    }

    def __init__(self):
        self.api_token = os.environ.get("SIGNNOW_API_TOKEN", "")
        self.base_url = SIGNNOW_BASE

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    async def _get_token(self) -> str:
        """
        Obtain a fresh Bearer token via Resource Owner Password Credentials.
        Falls back to the env var SIGNNOW_API_TOKEN if already set.
        """
        if self.api_token:
            return self.api_token

        basic_auth = os.environ.get(
            "SIGNNOW_BASIC_AUTH",
            "M2I0ZGQ1MWUwYTA3NTU3ZTViMGU2YjQyNDE1NzU5ZGI6YjQ2MzNiZmU3ZjkwNDgzYWJjZjQ4MDE2MjBhZWRjNTk=",
        )
        username = os.environ.get("SIGNNOW_USERNAME", "admin@shamrockbailbonds.biz")
        password = os.environ.get("SIGNNOW_PASSWORD", "")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/oauth2/token",
                headers={
                    "Authorization": f"Basic {basic_auth}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "password",
                    "username": username,
                    "password": password,
                    "scope": "*",
                },
                timeout=30,
            )
            resp.raise_for_status()
            token = resp.json().get("access_token", "")
            self.api_token = token
            return token

    async def _copy_template(self, client: httpx.AsyncClient, template_id: str, name: str) -> str:
        """POST /template/{id}/copy — returns new document_id."""
        resp = await client.post(
            f"{self.base_url}/template/{template_id}/copy",
            headers=self._headers,
            json={"document_name": name},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("id", "")

    async def _prefill_fields(
        self,
        client: httpx.AsyncClient,
        document_id: str,
        fields: List[Dict[str, Any]],
    ) -> None:
        """PUT /document/{id} — hydrate text fields."""
        if not fields:
            return
        resp = await client.put(
            f"{self.base_url}/document/{document_id}",
            headers=self._headers,
            json={"fields": fields},
            timeout=30,
        )
        resp.raise_for_status()

    async def _create_document_group(
        self,
        client: httpx.AsyncClient,
        document_ids: List[str],
        group_name: str,
    ) -> str:
        """POST /documentgroup — returns group_id."""
        resp = await client.post(
            f"{self.base_url}/documentgroup",
            headers=self._headers,
            json={"document_ids": document_ids, "group_name": group_name},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("id", "")

    async def _get_embedded_link(
        self,
        client: httpx.AsyncClient,
        document_id: str,
        signer_email: str,
    ) -> str:
        """
        POST /v2/documents/{id}/embedded-invites then
        POST /v2/documents/embedded-invites/{link_id}/link
        Returns a one-time signing URL (45-minute expiry).
        """
        invite_payload = {
            "invites": [
                {
                    "email": signer_email,
                    "role_name": "Signer 1",
                    "order": 1,
                    "auth_method": "none",
                }
            ]
        }
        resp = await client.post(
            f"{self.base_url}/v2/documents/{document_id}/embedded-invites",
            headers=self._headers,
            json=invite_payload,
            timeout=30,
        )
        # 409 / code 19004002 = invite already exists — fetch existing
        if resp.status_code == 409:
            get_resp = await client.get(
                f"{self.base_url}/v2/documents/{document_id}/embedded-invites",
                headers=self._headers,
                timeout=30,
            )
            get_resp.raise_for_status()
            invite_data = get_resp.json()
        else:
            resp.raise_for_status()
            invite_data = resp.json()

        link_id = (
            (invite_data.get("data") or [{}])[0].get("id")
            or invite_data.get("id")
            or ""
        )
        if not link_id:
            logger.warning("No link_id returned from embedded-invites for doc %s", document_id)
            return ""

        link_resp = await client.post(
            f"{self.base_url}/v2/documents/embedded-invites/{link_id}/link",
            headers=self._headers,
            json={"auth_method": "none", "link_expiration": 45},
            timeout=30,
        )
        link_resp.raise_for_status()
        return link_resp.json().get("data", {}).get("link", "")

    @staticmethod
    def _build_prefill_fields(intake_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Build the list of {field_name, prefilled_text} dicts from an intake doc.
        Field names match those tagged in the SignNow templates.
        """
        ind = intake_doc.get("indemnitor", {})
        def_ = intake_doc.get("defendant", {})

        defendant_name = intake_doc.get("defendant_name") or def_.get("name", "")
        indemnitor_name = intake_doc.get("indemnitor_name") or (
            " ".join(filter(None, [ind.get("firstName"), ind.get("lastName")]))
        )
        booking_number = (
            intake_doc.get("defendant_booking_number") or def_.get("bookingNumber", "")
        )
        county = intake_doc.get("defendant_county") or def_.get("county", "")
        bond_amount_raw = def_.get("bondAmount", "") or intake_doc.get("bond_amount", "")
        try:
            bond_amount = float(str(bond_amount_raw).replace("$", "").replace(",", ""))
            premium = bond_amount * 0.10
            premium_str = f"${premium:,.2f}"
            bond_amount_str = f"${bond_amount:,.2f}"
        except (ValueError, TypeError):
            premium_str = ""
            bond_amount_str = str(bond_amount_raw)

        today = datetime.now(timezone.utc).strftime("%m/%d/%Y")

        raw_fields = {
            "defendant_name":      defendant_name,
            "DefendantName":       defendant_name,
            "defendant_dob":       def_.get("dob", ""),
            "booking_number":      booking_number,
            "arrest_number":       booking_number,
            "county":              county,
            "facility":            def_.get("facility", intake_doc.get("defendant_facility", "")),
            "charges":             def_.get("charges", ""),
            "bond_amount":         bond_amount_str,
            "BondAmount":          bond_amount_str,
            "indemnitor_name":     indemnitor_name,
            "IndemnitorName":      indemnitor_name,
            "indemnitor_address":  ind.get("address", ""),
            "indemnitor_city":     ind.get("city", ""),
            "indemnitor_state":    ind.get("state", "FL"),
            "indemnitor_zip":      ind.get("zip", ""),
            "indemnitor_phone":    ind.get("phone", intake_doc.get("indemnitor_phone", "")),
            "indemnitor_email":    ind.get("email", intake_doc.get("indemnitor_email", "")),
            "indemnitor_dob":      ind.get("dob", ""),
            "indemnitor_dl":       ind.get("dl", ""),
            "indemnitor_dl_state": ind.get("dlState", "FL"),
            "premium_amount":      premium_str,
            "PremiumAmount":       premium_str,
            "agent_name":          AGENT_NAME,
            "AgentName":           AGENT_NAME,
            "agent_license":       AGENT_LICENSE,
            "AgentLicense":        AGENT_LICENSE,
            "agency_name":         AGENCY_NAME,
            "AgencyName":          AGENCY_NAME,
            "agency_phone":        AGENCY_PHONE,
            "date":                today,
            "Date":                today,
            "intake_id":           intake_doc.get("intake_id", ""),
        }

        return [
            {"field_name": k, "prefilled_text": str(v)}
            for k, v in raw_fields.items()
            if v
        ]

    def build_packet_manifest(
        self,
        phase: int,
        surety_id: str = "osi",
        num_indemnitors: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Build the manifest of documents needed for a specific phase.
        Handles surety-specific templates and multiplication rules.
        """
        manifest = []

        phase_1_docs = [
            "paperwork-header",
            "faq-cosigners",
            "indemnity-agreement",
            "promissory-note",
            "disclosure-form",
            "ssa-release",
        ]
        phase_2_docs = [
            "faq-defendants",
            "defendant-application",
            "surety-terms",
            "master-waiver",
            "collateral-receipt",
            "payment-plan",
        ]

        target_docs = phase_1_docs if phase == 1 else phase_2_docs

        for doc_key in target_docs:
            template_key = doc_key
            # Route to surety-specific templates when Palmetto is selected
            if doc_key in ("appearance-bond", "collateral-receipt", "payment-plan") and surety_id == "palmetto":
                template_key = f"{doc_key}-palmetto"

            template_id = self.TEMPLATE_MAP.get(template_key)
            if not template_id:
                logger.warning("Template ID not found for %s", template_key)
                continue

            rule = self.DOC_RULES.get(doc_key, {}).get("rule", "static")

            copies_needed = 1
            if rule == "per-indemnitor":
                copies_needed = num_indemnitors
            elif rule == "per-person":
                copies_needed = num_indemnitors + 1  # +1 for defendant

            for i in range(copies_needed):
                manifest.append(
                    {
                        "doc_key": doc_key,
                        "template_id": template_id,
                        "copy_index": i + 1,
                        "rule": rule,
                        "label": self.DOC_RULES[doc_key]["label"],
                    }
                )

        return manifest

    def handle_send_phase_1(
        self,
        form_data: Dict[str, Any],
        signer_email: str,
        signer_name: str,
    ) -> Dict[str, Any]:
        """Phase 1 manifest builder — sync wrapper. Use create_packet() for real sending."""
        logger.info("Phase 1 manifest built for %s", signer_email)
        num_indemnitors = len(form_data.get("indemnitors", [{}]))
        manifest = self.build_packet_manifest(phase=1, num_indemnitors=num_indemnitors)
        return {
            "status": "success",
            "phase": 1,
            "message": "Phase 1 packet manifest ready — call create_packet() to send",
            "manifest_size": len(manifest),
            "signer_email": signer_email,
        }

    def handle_send_phase_2(
        self,
        form_data: Dict[str, Any],
        signer_email: str,
        signer_name: str,
        poa_number: str,
        agent_name: str,
        agent_license: str,
        surety_id: str = "osi",
    ) -> Dict[str, Any]:
        """Phase 2 manifest builder — sync wrapper. Use create_packet() for real sending."""
        if not poa_number:
            raise ValueError("Phase 2 requires a valid POA number")
        logger.info("Phase 2 manifest built for %s with POA %s", signer_email, poa_number)
        manifest = self.build_packet_manifest(phase=2, surety_id=surety_id)
        return {
            "status": "success",
            "phase": 2,
            "message": "Phase 2 packet manifest ready — call create_packet() to send",
            "manifest_size": len(manifest),
            "poa_number": poa_number,
            "signer_email": signer_email,
        }

    async def create_packet(
        self,
        intake_doc: Dict[str, Any],
        packet_id: str,
        phase: int = 1,
        surety_id: Optional[str] = None,
        signer_email: Optional[str] = None,
        signer_name: Optional[str] = None,
        poa_number: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Full production SignNow packet creation.

        Steps:
          1. Ensure valid Bearer token (auto-fetches via ROPC if needed).
          2. Build document manifest for the requested phase.
          3. Copy each template to a new document.
          4. Prefill all text fields on each copied document.
          5. Group all documents into a SignNow document group.
          6. Create an embedded invite on the first signable document.
          7. Return invite_id, signing_link, document_ids, group_id.

        Args:
            intake_doc:   Full intake record from MongoDB intake_queue.
            packet_id:    Our internal packet ID (used for document naming).
            phase:        1 = indemnitor signs, 2 = post-approval (requires poa_number).
            surety_id:    "osi" or "palmetto" — determines template set.
            signer_email: Override indemnitor email (defaults to intake_doc value).
            signer_name:  Override indemnitor name (defaults to intake_doc value).
            poa_number:   Required for phase 2.
        """
        intake_id = intake_doc.get("intake_id", "unknown")
        logger.info(
            "[signnow] Creating Phase %d packet %s for intake %s",
            phase, packet_id, intake_id,
        )

        if surety_id is None:
            surety_id = intake_doc.get("surety_id", "osi")
        if signer_email is None:
            signer_email = (
                intake_doc.get("indemnitor_email")
                or intake_doc.get("indemnitor", {}).get("email", "")
            )
        if signer_name is None:
            signer_name = intake_doc.get("indemnitor_name", "Indemnitor")

        if phase == 2 and not poa_number:
            raise ValueError("Phase 2 requires a valid POA number")

        await self._get_token()

        num_indemnitors = max(
            1,
            len(intake_doc.get("indemnitors", [intake_doc.get("indemnitor", {})])),
        )
        manifest = self.build_packet_manifest(
            phase=phase,
            surety_id=surety_id,
            num_indemnitors=num_indemnitors,
        )

        prefill_fields = self._build_prefill_fields(intake_doc)
        if poa_number:
            prefill_fields.append({"field_name": "poa_number", "prefilled_text": poa_number})
            prefill_fields.append({"field_name": "PowerNum", "prefilled_text": poa_number})

        document_ids: List[str] = []
        defendant_name = intake_doc.get("defendant_name", "Unknown")

        async with httpx.AsyncClient(timeout=60) as client:
            # Step 3 + 4: Copy templates and prefill
            for item in manifest:
                if item["rule"] == "print-only":
                    continue

                doc_name = (
                    f"{item['label']} — {defendant_name} "
                    f"[{packet_id}] #{item['copy_index']}"
                )
                try:
                    doc_id = await self._copy_template(client, item["template_id"], doc_name)
                    logger.info(
                        "[signnow] Copied template %s -> doc %s (%s)",
                        item["template_id"], doc_id, item["label"],
                    )
                    await self._prefill_fields(client, doc_id, prefill_fields)
                    document_ids.append(doc_id)
                except httpx.HTTPStatusError as exc:
                    logger.error(
                        "[signnow] Failed to copy/prefill %s: %s — %s",
                        item["doc_key"],
                        exc.response.status_code,
                        exc.response.text[:200],
                    )
                    raise

            if not document_ids:
                raise RuntimeError("No documents were created — all templates failed to copy")

            # Step 5: Group documents
            group_name = (
                f"Shamrock Bail Bonds — {defendant_name} Phase {phase} [{packet_id}]"
            )
            group_id = ""
            if len(document_ids) > 1:
                try:
                    group_id = await self._create_document_group(
                        client, document_ids, group_name
                    )
                    logger.info("[signnow] Document group created: %s", group_id)
                except httpx.HTTPStatusError as exc:
                    logger.warning(
                        "[signnow] Document group creation failed (%s) — "
                        "falling back to single-doc invite",
                        exc.response.status_code,
                    )

            # Step 6: Create embedded invite on first document
            primary_doc_id = document_ids[0]
            signing_link = ""
            invite_id = ""
            if signer_email:
                try:
                    signing_link = await self._get_embedded_link(
                        client, primary_doc_id, signer_email
                    )
                    invite_id = f"embed_{primary_doc_id}"
                    logger.info(
                        "[signnow] Embedded signing link for %s: %s",
                        signer_email,
                        signing_link[:60] if signing_link else "(empty)",
                    )
                except httpx.HTTPStatusError as exc:
                    logger.error(
                        "[signnow] Embedded invite failed: %s — %s",
                        exc.response.status_code,
                        exc.response.text[:200],
                    )
            else:
                logger.warning("[signnow] No signer_email — skipping embedded invite")

        return {
            "invite_id": invite_id,
            "signing_link": signing_link,
            "group_id": group_id,
            "document_ids": document_ids,
            "manifest_size": len(manifest),
            "phase": phase,
            "surety_id": surety_id,
            "status": "success",
        }
