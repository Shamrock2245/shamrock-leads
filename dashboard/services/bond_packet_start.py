"""
Write Bond → DocuSeal packet start.

start_indemnitor_bond_packet is the callable path for the paperwork route
and for a later super-admin "Start bond packet → indemnitor" flow. It
hydrates bond data, resolves the template for (surety, tenant), and runs
the same identity, packet-binding, and POA gates the route already enforces.
It does not skip those gates. DocuSeal is called only after they pass.

Default tenant is shamrock. Callers pass tenant_id only after their own
auth has chosen it. This module does not read a tenant off an HTTP body.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from dashboard.services.docuseal_service import (
    DocuSealPacketValidationError,
    apply_writing_agent,
    build_bond_data_from_dashboard,
    resolve_template_id_for_surety,
    resolve_writing_agent,
    validate_docuseal_packet_binding,
    validate_shannon_voice_packet,
)
from dashboard.services.surety_template_store import DEFAULT_TENANT_ID, normalize_tenant_id

logger = logging.getLogger(__name__)

_POA_UNSET = object()


class BondPacketStartError(DocuSealPacketValidationError):
    """Fail-closed packet start. code is the stable machine error."""

    def __init__(self, message: str, code: str = "docuseal_packet_binding_invalid"):
        self.code = code
        super().__init__(message)


def poa_assignment_block(
    poa_doc: Optional[Mapping[str, Any]],
    bond_data: Mapping[str, Any],
) -> Optional[tuple]:
    """Same POA assignment and tier gate the finalize route returns as 422."""
    if not poa_doc:
        return (
            "docuseal_poa_not_assigned",
            (
                "DocuSeal packet blocked: the selected POA must be assigned in the "
                "matching surety inventory before paperwork can be created."
            ),
        )
    try:
        poa_limit = float(poa_doc.get("max_bond_value") or 0)
        bond_amount = float(bond_data.get("bond_amount") or 0)
    except (TypeError, ValueError):
        poa_limit, bond_amount = 0, 0
    if poa_limit <= 0 or bond_amount <= 0 or bond_amount > poa_limit:
        return (
            "docuseal_poa_tier_invalid",
            (
                "DocuSeal packet blocked: the assigned POA tier must cover the "
                "authoritative BondCase amount."
            ),
        )
    return None


async def _lookup_assigned_poa(bond_data: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        from dashboard.extensions import get_collection

        return await get_collection("poa_inventory").find_one(
            {
                "poa_number": bond_data.get("poa_number"),
                "surety_id": bond_data.get("surety_id"),
                "status": {"$in": ["assigned", "used"]},
            },
            {"_id": 0, "max_bond_value": 1},
        )
    except Exception as exc:
        logger.warning(
            "[bond-packet] POA lookup failed closed error_type=%s",
            type(exc).__name__,
        )
        return None


def _hydrate(
    *,
    surety_id: str,
    bond_data: Optional[Mapping[str, Any]],
    ctx: Optional[Mapping[str, Any]],
    intake_doc: Optional[Mapping[str, Any]],
    body: Optional[Mapping[str, Any]],
    field_overrides: Optional[Mapping[str, Any]],
    session: Optional[Mapping[str, Any]] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    if bond_data is not None:
        hydrated = dict(bond_data)
        if surety_id:
            hydrated["surety_id"] = str(surety_id).strip().lower()
    else:
        hydrated = build_bond_data_from_dashboard(
            ctx=dict(ctx or {}),
            intake_doc=dict(intake_doc or {}),
            field_overrides=dict(field_overrides or {}),
            body=dict(body or {}),
            surety_id=surety_id or "osi",
            session=session,
        )
    name, license_no = resolve_writing_agent(
        hydrated, session=session, tenant=tenant_id,
    )
    return apply_writing_agent(hydrated, name, license_no)


async def start_indemnitor_bond_packet(
    *,
    packet_id: str,
    surety_id: str,
    bond_data: Optional[Mapping[str, Any]] = None,
    ctx: Optional[Mapping[str, Any]] = None,
    intake_doc: Optional[Mapping[str, Any]] = None,
    body: Optional[Mapping[str, Any]] = None,
    field_overrides: Optional[Mapping[str, Any]] = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    indemnitors: Optional[list] = None,
    defendant: Optional[Dict[str, Any]] = None,
    include_defendant: bool = True,
    send_email: bool = False,
    completed_redirect_url: Optional[str] = None,
    skip_bond_binding: bool = False,
    poa_record: Any = _POA_UNSET,
    docuseal: Any = None,
    session: Optional[Mapping[str, Any]] = None,
    staff_test_case: bool = False,
) -> Dict[str, Any]:
    """
    Resolve, gate, and submit one indemnitor bond packet.

    Raises BondPacketStartError (a DocuSealPacketValidationError) when
    identity, binding, POA, template entitlement, or DocuSeal config fails.
    create_submission_for_packet runs only after those gates.
    """
    try:
        tenant = normalize_tenant_id(tenant_id)
    except Exception as exc:
        raise BondPacketStartError(
            "DocuSeal packet blocked: tenant id is not valid.",
            code="invalid_tenant",
        ) from exc

    hydrated = _hydrate(
        surety_id=surety_id,
        bond_data=bond_data,
        ctx=ctx,
        intake_doc=intake_doc,
        body=body,
        field_overrides=field_overrides,
        session=session,
        tenant_id=tenant,
    )
    parties = indemnitors if indemnitors is not None else hydrated.get("indemnitors")

    try:
        if skip_bond_binding:
            validate_shannon_voice_packet(
                packet_id=packet_id,
                bond_data=hydrated,
                indemnitors=parties,
                defendant=defendant,
            )
        else:
            validate_docuseal_packet_binding(
                packet_id=packet_id,
                bond_data=hydrated,
                indemnitors=parties,
                defendant=defendant,
                include_defendant=include_defendant,
            )
    except DocuSealPacketValidationError as exc:
        raise BondPacketStartError(str(exc), code="docuseal_packet_binding_invalid") from exc

    # After binding, before POA lookup or DocuSeal. Synthetic TEST- packets skip.
    from dashboard.services.identity_verification_service import (
        IndemnitorIdentityError,
        require_verified_indemnitors,
    )

    try:
        await require_verified_indemnitors(
            bond_data=hydrated,
            indemnitors=parties,
            bond_case_id=str(hydrated.get("bond_case_id") or ""),
            booking_number=str(hydrated.get("booking_number") or ""),
            packet_id=str(packet_id or ""),
            staff_test_case=bool(staff_test_case),
        )
    except IndemnitorIdentityError as exc:
        raise BondPacketStartError(str(exc), code=exc.code) from exc

    if not skip_bond_binding:
        if staff_test_case:
            poa_doc = poa_record if poa_record is not _POA_UNSET else None
        else:
            poa_doc = await _lookup_assigned_poa(hydrated) if poa_record is _POA_UNSET else poa_record
        blocked = poa_assignment_block(poa_doc, hydrated)
        if blocked:
            raise BondPacketStartError(blocked[1], code=blocked[0])

    template_id = resolve_template_id_for_surety(hydrated.get("surety_id"), tenant)
    if not template_id:
        raise BondPacketStartError(
            "DocuSeal packet blocked: no template is entitled for this surety and tenant.",
            code="template_unavailable",
        )

    client = docuseal
    if client is None:
        from dashboard.services.docuseal_service import get_docuseal_service

        client = get_docuseal_service()
    if not getattr(client, "is_configured", False):
        raise BondPacketStartError(
            "DocuSeal packet blocked: set DOCUSEAL_URL and DOCUSEAL_API_KEY.",
            code="docuseal_not_configured",
        )

    submission = await client.create_submission_for_packet(
        template_id=template_id,
        packet_id=packet_id,
        bond_data=hydrated,
        indemnitors=parties,
        defendant=defendant,
        send_email=False if staff_test_case else send_email,
        include_defendant=include_defendant,
        completed_redirect_url=completed_redirect_url,
        skip_bond_binding=skip_bond_binding,
        staff_test_case=staff_test_case,
    )
    return {
        "template_id": template_id,
        "tenant_id": tenant,
        "surety_id": hydrated.get("surety_id"),
        "submission": submission,
    }
