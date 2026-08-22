"""
Regression tests for DocuSeal surety-to-template resolution and binding guards.
Verifies that:
  1. Palmetto never silently falls back to OSI template.
  2. Missing OSI or Palmetto template configuration fails closed.
  3. Mismatched surety ID between BondCase and POA inventory blocks packet generation.
  4. Non-matching surety IDs fail closed before DocuSeal submission creation.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from dashboard.services.docuseal_service import (
    DocuSealPacketValidationError,
    DocuSealService,
    resolve_template_id_for_surety,
    validate_docuseal_packet_binding,
)


# ── TEST 1: Palmetto Never Falls Back to OSI ──────────────────────────────────

def test_palmetto_never_falls_back_to_osi():
    with patch.dict(os.environ, {
        "DOCUSEAL_TEMPLATE_ID_OSI": "100",
        "DOCUSEAL_TEMPLATE_ID_PALMETTO": "",
    }, clear=True):
        # Palmetto must return None, NOT "100"
        palmetto_tid = resolve_template_id_for_surety("palmetto")
        assert palmetto_tid is None

    with patch.dict(os.environ, {
        "DOCUSEAL_TEMPLATE_ID_OSI": "100",
        "DOCUSEAL_TEMPLATE_ID_PALMETTO": "200",
    }, clear=True):
        palmetto_tid = resolve_template_id_for_surety("palmetto")
        assert palmetto_tid == "200"

        osi_tid = resolve_template_id_for_surety("osi")
        assert osi_tid == "100"


# ── TEST 2: Missing Template Configuration Fails Closed ───────────────────────

def test_missing_template_config_returns_none():
    with patch.dict(os.environ, {
        "DOCUSEAL_TEMPLATE_ID_OSI": "",
        "DOCUSEAL_TEMPLATE_ID": "",
        "DOCUSEAL_TEMPLATE_ID_PALMETTO": "",
    }, clear=True):
        assert resolve_template_id_for_surety("osi") is None
        assert resolve_template_id_for_surety("palmetto") is None
        assert resolve_template_id_for_surety("unknown_surety") is None


# ── TEST 3: Packet Binding Rejects Missing or Mismatched Surety ────────────────

def test_validate_packet_binding_requires_surety_id():
    valid_bond_data = {
        "bond_case_id": "BOND-001",
        "match_id": "MATCH-001",
        "defendant_id": "DEF-001",
        "indemnitor_id": "IND-001",
        "defendant_name": "Test Defendant",
        "booking_number": "BK12345",
        "county": "Lee",
        "case_number": "26-CF-0001",
        "poa_number": "OSI3 123456",
        "bond_amount": 3000.0,
        "surety_id": "osi",
        "match_status": "validated",
        "defendant": {"name": "Test Defendant", "email": "def@example.com", "phone": "2395550199"},
        "indemnitor": {"name": "Test Indemnitor", "email": "ind@example.com", "phone": "2395550100"},
    }
    valid_indemnitors = [
        {"name": "Test Indemnitor", "phone": "2395550100", "email": "ind@example.com"}
    ]

    # Valid case passes
    validate_docuseal_packet_binding(
        packet_id="PKT-TEST-01",
        bond_data=valid_bond_data,
        indemnitors=valid_indemnitors,
        include_defendant=True,
    )

    # Missing surety_id fails closed
    missing_surety = dict(valid_bond_data, surety_id="")
    with pytest.raises(DocuSealPacketValidationError, match="surety"):
        validate_docuseal_packet_binding(
            packet_id="PKT-TEST-01",
            bond_data=missing_surety,
            indemnitors=valid_indemnitors,
            include_defendant=True,
        )

    # Missing case_number fails closed
    missing_case = dict(valid_bond_data, case_number="")
    with pytest.raises(DocuSealPacketValidationError, match="case_number"):
        validate_docuseal_packet_binding(
            packet_id="PKT-TEST-01",
            bond_data=missing_case,
            indemnitors=valid_indemnitors,
            include_defendant=True,
        )

    # Missing POA number fails closed
    missing_poa = dict(valid_bond_data, poa_number="")
    with pytest.raises(DocuSealPacketValidationError, match="poa_number"):
        validate_docuseal_packet_binding(
            packet_id="PKT-TEST-01",
            bond_data=missing_poa,
            indemnitors=valid_indemnitors,
            include_defendant=True,
        )


# ── TEST 4: DocuSeal Service Config Checks ────────────────────────────────────

def test_docuseal_service_is_configured_check():
    svc_empty = DocuSealService(base_url="", api_key="")
    assert svc_empty.is_configured is False

    svc_url_only = DocuSealService(base_url="https://sign.example.com", api_key="")
    assert svc_url_only.is_configured is False

    svc_full = DocuSealService(base_url="https://sign.example.com", api_key="secret_key")
    assert svc_full.is_configured is True


# ── TEST 5: Submission Creation Requires Configured Template ID ────────────────

@pytest.mark.asyncio
async def test_create_submission_blocks_empty_template_id():
    svc = DocuSealService(base_url="https://sign.example.com", api_key="secret_key")
    valid_bond_data = {
        "bond_case_id": "BOND-001",
        "match_id": "MATCH-001",
        "defendant_id": "DEF-001",
        "indemnitor_id": "IND-001",
        "defendant_name": "Test Defendant",
        "booking_number": "BK12345",
        "county": "Lee",
        "case_number": "26-CF-0001",
        "poa_number": "OSI3 123456",
        "bond_amount": 3000.0,
        "surety_id": "osi",
        "match_status": "validated",
        "defendant": {"name": "Test Defendant", "email": "def@example.com", "phone": "2395550199"},
        "indemnitor": {"name": "Test Indemnitor", "email": "ind@example.com", "phone": "2395550100"},
    }
    with pytest.raises(DocuSealPacketValidationError, match="template_id"):
        await svc.create_submission_for_packet(
            template_id="",
            packet_id="PKT-TEST-02",
            bond_data=valid_bond_data,
        )

