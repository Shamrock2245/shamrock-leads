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

        IMPORTANT: The live SignNow templates use 4 different naming conventions:
          1. kebab-case:  defendant-full-name, numeric-bond-amount  (Header, Disclosure, Surety Terms)
          2. PascalCase:  DefendantName, BondAmount, IndemnitorName  (Def App, Promissory, Collateral, FAQs)
          3. Ind-prefix:  IndName, IndAddress, IndDOB               (Indemnity Agreement)
          4. Def-prefix:  DefLastName, DefFirstName, DefDOB          (Defendant Application)

        We send ALL conventions for each piece of data. SignNow silently ignores
        field names that don't exist on a given document, so duplicates are safe.
        """
        ind = intake_doc.get("indemnitor", {})
        def_ = intake_doc.get("defendant", {})

        # ── Extract core values ──────────────────────────────────────────────
        defendant_name = intake_doc.get("defendant_name") or def_.get("name", "")
        def_first = def_.get("firstName", "") or def_.get("first_name", "")
        def_last = def_.get("lastName", "") or def_.get("last_name", "")
        def_middle = def_.get("middleName", "") or def_.get("middle_name", "")
        if not defendant_name and (def_first or def_last):
            defendant_name = " ".join(filter(None, [def_first, def_middle, def_last]))

        ind_first = ind.get("firstName", "") or ind.get("first_name", "")
        ind_last = ind.get("lastName", "") or ind.get("last_name", "")
        indemnitor_name = intake_doc.get("indemnitor_name") or (
            " ".join(filter(None, [ind_first, ind_last]))
        )

        booking_number = (
            intake_doc.get("defendant_booking_number") or def_.get("bookingNumber", "")
        )
        county = intake_doc.get("defendant_county") or def_.get("county", "")
        facility = def_.get("facility", intake_doc.get("defendant_facility", ""))
        charges = def_.get("charges", "") or intake_doc.get("charges", "")
        case_number = def_.get("caseNumber", "") or intake_doc.get("case_number", "")
        court_date = def_.get("courtDate", "") or intake_doc.get("court_date", "")
        court_time = def_.get("courtTime", "") or intake_doc.get("court_time", "")
        court_location = def_.get("courtLocation", "") or intake_doc.get("court_location", "")
        arrest_date = def_.get("arrestDate", "") or intake_doc.get("arrest_date", "")

        # Indemnitor contact
        ind_phone = ind.get("phone", intake_doc.get("indemnitor_phone", ""))
        ind_email = ind.get("email", intake_doc.get("indemnitor_email", ""))
        ind_address = ind.get("address", "")
        ind_city = ind.get("city", "")
        ind_state = ind.get("state", "FL")
        ind_zip = ind.get("zip", "")
        ind_city_state_zip = ", ".join(filter(None, [ind_city, ind_state])) + (f" {ind_zip}" if ind_zip else "")
        ind_dob = ind.get("dob", "")
        ind_dl = ind.get("dl", "") or ind.get("dlNumber", "")
        ind_dl_state = ind.get("dlState", "FL")
        ind_ssn = ind.get("ssn", "")
        ind_relation = ind.get("relationship", "") or ind.get("relation", "")

        # Indemnitor employment
        ind_employer = ind.get("employer", "") or ind.get("employerName", "")
        ind_emp_phone = ind.get("employerPhone", "")
        ind_emp_address = ind.get("employerAddress", "") or ind.get("employerCity", "")

        # Indemnitor vehicle
        ind_car_make = ind.get("carMake", "")
        ind_car_model = ind.get("carModel", "")
        ind_car_year = ind.get("carYear", "")
        ind_car_color = ind.get("carColor", "")

        # References
        ref1_name = ind.get("ref1Name", "") or ind.get("reference1Name", "")
        ref1_phone = ind.get("ref1Phone", "") or ind.get("reference1Phone", "")
        ref1_relation = ind.get("ref1Relation", "") or ind.get("reference1Relation", "")
        ref1_address = ind.get("ref1Address", "") or ind.get("reference1Address", "")
        ref2_name = ind.get("ref2Name", "") or ind.get("reference2Name", "")
        ref2_phone = ind.get("ref2Phone", "") or ind.get("reference2Phone", "")
        ref2_relation = ind.get("ref2Relation", "") or ind.get("reference2Relation", "")
        ref2_address = ind.get("ref2Address", "") or ind.get("reference2Address", "")

        # Defendant descriptors
        def_dob = def_.get("dob", "") or intake_doc.get("defendant_dob", "")
        def_address = def_.get("address", "")
        def_city = def_.get("city", "")
        def_state = def_.get("state", "FL")
        def_zip = def_.get("zip", "")
        def_phone = def_.get("phone", "")
        def_email = def_.get("email", "")
        def_height = def_.get("height", "")
        def_weight = def_.get("weight", "")
        def_race = def_.get("race", "")
        def_hair = def_.get("hair", "") or def_.get("hairColor", "")
        def_eyes = def_.get("eyes", "") or def_.get("eyeColor", "")
        def_dl = def_.get("dl", "") or def_.get("dlNumber", "")
        def_dl_state = def_.get("dlState", "FL")
        def_sex = def_.get("sex", "")
        def_employer = def_.get("employer", "")
        def_emp_phone = def_.get("employerPhone", "")
        def_emp_address = def_.get("employerAddress", "")

        # Bond / payment math
        bond_amount_raw = def_.get("bondAmount", "") or intake_doc.get("bond_amount", "")
        try:
            bond_amount = float(str(bond_amount_raw).replace("$", "").replace(",", ""))
            premium = bond_amount * 0.10
            premium_str = f"${premium:,.2f}"
            bond_amount_str = f"${bond_amount:,.2f}"
        except (ValueError, TypeError):
            bond_amount = 0.0
            premium = 0.0
            premium_str = ""
            bond_amount_str = str(bond_amount_raw)

        today = datetime.now(timezone.utc).strftime("%m/%d/%Y")
        now = datetime.now(timezone.utc)
        day_dd = now.strftime("%d")
        month_name = now.strftime("%B")
        year_yy = now.strftime("%y")

        # ── Build field map with ALL naming conventions ──────────────────────
        # SignNow silently ignores field names not present on a template,
        # so sending all variants is safe and ensures universal hydration.
        raw_fields = {
            # ─── Defendant Name (all conventions) ─────────────────────────
            "defendant_name":         defendant_name,
            "DefendantName":          defendant_name,
            "defendant-full-name":    defendant_name,
            "DefName":                defendant_name,
            "Defendant Print Name":   defendant_name,
            "Defendants NameRow1":    defendant_name,
            "DefFirstName":           def_first,
            "DefLastName":            def_last,
            "DefMiddleName":          def_middle,

            # ─── Defendant Details (Def-prefix for Defendant Application) ─
            "DefDOB":                 def_dob,
            "defendant_dob":          def_dob,
            "DefPhone":               def_phone,
            "defendant-phone":        def_phone,
            "defendant-email":        def_email,
            "DefAddress":             def_address,
            "defendant-address":      def_address,
            "DefCity":                def_city,
            "DefState":               def_state,
            "DefZip":                 def_zip,
            "DefCounty":              county,
            "DefHeight":              def_height,
            "DefWeight":              def_weight,
            "DefRace":                def_race,
            "DefHair":                def_hair,
            "DefEyes":                def_eyes,
            "DefSex":                 def_sex,
            "DefDL":                  def_dl,
            "DefDLState":             def_dl_state,
            "DefEmployer":            def_employer,
            "DefEmpPhone":            def_emp_phone,
            "DefEmpAddress":          def_emp_address,

            # ─── Indemnitor Name (all conventions) ────────────────────────
            "indemnitor_name":        indemnitor_name,
            "IndemnitorName":         indemnitor_name,
            "indemnitor-full-name":   indemnitor_name,
            "IndName":                indemnitor_name,

            # ─── Indemnitor Details (Ind-prefix for Indemnity Agreement) ──
            "IndAddress":             ind_address,
            "indemnitor-address":     ind_address,
            "indemnitor_address":     ind_address,
            "IndCityStateZip":        ind_city_state_zip,
            "indemnitor_city":        ind_city,
            "indemnitor_state":       ind_state,
            "indemnitor_zip":         ind_zip,
            "IndPhone":               ind_phone,
            "indemnitor-phone":       ind_phone,
            "indemnitor_phone":       ind_phone,
            "Phone":                  ind_phone,        # SSA Release uses bare "Phone"
            "IndDL":                  ind_dl,
            "indemnitor_dl":          ind_dl,
            "indemnitor_dl_state":    ind_dl_state,
            "IndDOB":                 ind_dob,
            "indemnitor_dob":         ind_dob,
            "IndSSN":                 ind_ssn,
            "indemnitor-email":       ind_email,
            "indemnitor_email":       ind_email,
            "IndRelation":            ind_relation,

            # ─── Indemnitor Employment ────────────────────────────────────
            "IndEmployer":            ind_employer,
            "IndEmpPhone":            ind_emp_phone,
            "IndEmpAddress":          ind_emp_address,

            # ─── Indemnitor Vehicle ───────────────────────────────────────
            "IndCarMake":             ind_car_make,
            "IndCarModel":            ind_car_model,
            "IndCarYear":             ind_car_year,
            "IndCarColor":            ind_car_color,

            # ─── References ──────────────────────────────────────────────
            "Ref1Name":               ref1_name,
            "Ref1Phone":              ref1_phone,
            "Ref1Relation":           ref1_relation,
            "Ref1Address":            ref1_address,
            "Ref2Name":               ref2_name,
            "Ref2Phone":              ref2_phone,
            "Ref2Relation":           ref2_relation,
            "Ref2Address":            ref2_address,

            # ─── SSA Release (uses bare names) ───────────────────────────
            "FullName":               indemnitor_name,  # SSA Release
            "Social":                 ind_ssn,          # SSA Release

            # ─── Bond / Financial ─────────────────────────────────────────
            "bond_amount":            bond_amount_str,
            "BondAmount":             bond_amount_str,
            "numeric-bond-amount":    bond_amount_str,
            "NumericBondAmount":      bond_amount_str,
            "premium_amount":         premium_str,
            "PremiumAmount":          premium_str,
            "premium-amount":         premium_str,
            "Premium":                premium_str,

            # ─── Booking / Arrest ─────────────────────────────────────────
            "booking_number":         booking_number,
            "BookingNumber":          booking_number,
            "arrest_number":          booking_number,
            "county":                 county,
            "arrest-county":          county,
            "ArrestCounty":           county,
            "facility":              facility,
            "JailFacility":           facility,
            "jail-facility":          facility,
            "charges":               charges,
            "ChargeDescription":      charges,
            "charge-description":     charges,
            "arrest-date":            arrest_date,
            "ArrestDate":             arrest_date,

            # ─── Court ────────────────────────────────────────────────────
            "case-number":            case_number,
            "CaseNum":                case_number,
            "CaseNumber":             case_number,
            "court-date":             court_date,
            "CourtDate":              court_date,
            "court-time":             court_time,
            "CourtTime":              court_time,
            "court-location":         court_location,
            "CourtLocation":          court_location,

            # ─── Agent / Agency ───────────────────────────────────────────
            "agent_name":             AGENT_NAME,
            "AgentName":              AGENT_NAME,
            "agent_license":          AGENT_LICENSE,
            "AgentLicense":           AGENT_LICENSE,
            "AgentLicenseNumber":     AGENT_LICENSE,
            "agency_name":            AGENCY_NAME,
            "AgencyName":             AGENCY_NAME,
            "agency_phone":           AGENCY_PHONE,
            "AgentPhone":             AGENCY_PHONE,
            "AgentAddress":           "1528 Broadway",
            "AgentCity":              "Fort Myers",
            "AgentState":             "FL",
            "AgentZip":               "33901",
            "ReceiptNumber":          intake_doc.get("intake_id", ""),

            # ─── Dates (all conventions) ──────────────────────────────────
            "date":                   today,
            "Date":                   today,
            "DateSigned":             today,
            "date-signed":            today,
            "date-signed-ind":        today,
            "date-signed-def":        today,
            "date-signed-waiver":     today,
            "DateDD":                 day_dd,
            "Month":                  month_name,
            "YearYY":                 year_yy,

            # ─── Tracking ────────────────────────────────────────────────
            "intake_id":              intake_doc.get("intake_id", ""),
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
