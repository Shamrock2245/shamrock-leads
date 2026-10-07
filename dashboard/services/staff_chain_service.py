"""
ShamrockLeads — Staff-Directed Match + BondCase Chain Ensure Service
====================================================================
Ensures the canonical paperwork chain:
    ArrestLead → Defendant → Indemnitor → validated Match → BondCase → Packet

Fails closed on missing CRM facts:
- Refuses inventing contact info (indemnitor name + email must be on file in CRM).
- Refuses missing bond amount.
- Refuses missing or zero premium. Never invents a premium (including 10% of bond).
- Refuses missing or unassigned POAs. Does not pull an available POA from inventory.
- Refuses unvalidated surety.
- Strictly idempotent: repeated calls return existing IDs and preserve state.
  A POA is ours when bond_case_id or assigned_to matches the booking number,
  the case number, or the BondCase UUID already stored for this booking.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dashboard.extensions import get_collection
from dashboard.services.surety_registry import normalize_surety, is_supported_surety

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_first_last(name: str) -> Tuple[str, str]:
    parts = (name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


# Case-ownership fields only. assigned_to_agent is the writing agent, not the case.
_POA_OWNER_FIELDS = ("bond_case_id", "Bond_Case_ID", "assigned_to", "Assigned_To")


def _owner_token(value: Any) -> str:
    return str(value or "").strip()


def _positive_amount(value: Any) -> Optional[float]:
    """Positive money amount already on file or explicitly submitted.

    Blank, non-numeric, and zero are not amounts. Callers must not substitute
    a calculated premium for those values.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return amount


def _doc_bond_case_id(doc: Optional[Dict[str, Any]]) -> str:
    if not isinstance(doc, dict):
        return ""
    return _owner_token(doc.get("bond_case_id") or doc.get("Bond_Case_ID"))


def _poa_owner_tokens(poa_doc: Dict[str, Any]) -> List[str]:
    tokens: List[str] = []
    for key in _POA_OWNER_FIELDS:
        token = _owner_token(poa_doc.get(key))
        if token and token not in tokens:
            tokens.append(token)
    return tokens


async def ensure_match_bondcase(
    *,
    booking_number: str,
    surety_id: Optional[str] = None,
    case_number: Optional[str] = None,
    poa_numbers: Optional[List[str]] = None,
    poa_number: Optional[str] = None,
    indemnitor_name: Optional[str] = None,
    indemnitor_email: Optional[str] = None,
    indemnitor_phone: Optional[str] = None,
    bond_amount: Optional[float] = None,
    premium: Optional[float] = None,
    packet_id: Optional[str] = None,
    charge_details: Optional[List[Dict[str, Any]]] = None,
    actor_email: str = "admin@shamrockbailbonds.biz",
    source: str = "staff_ensure_match_bondcase",
) -> Dict[str, Any]:
    """
    Ensure the complete validated chain for a booking:
    1. Loads arrest + active_bonds (404 if missing).
    2. Validates surety and POAs against poa_inventory.
    3. Validates indemnitor on-file contact (no fabricated contact).
    4. Ensures defendants doc.
    5. Ensures indemnitors doc.
    6. Upserts matches doc with status 'validated'.
    7. Upserts bond_cases doc.
    8. Patches active_bonds with IDs and status history.
    9. Patches poa_inventory with assignment to case.
    10. If packet_id given, patches packet identity & clears pending_staff_match.
    11. Writes immutable audit_events record.
    """
    booking = str(booking_number or "").strip()
    if not booking:
        return {"success": False, "status_code": 400, "error": "booking_number_required", "message": "Booking number is required"}

    # 1. Load arrest
    arrest = await get_collection("arrests").find_one(
        {"$or": [{"booking_number": booking}, {"Booking_Number": booking}]}
    )
    if not arrest:
        return {
            "success": False,
            "status_code": 404,
            "error": "arrest_missing",
            "message": f"Arrest record not found for booking {booking}",
        }

    # 2. Load active_bonds
    active = await get_collection("active_bonds").find_one(
        {"$or": [{"booking_number": booking}, {"Booking_Number": booking}]}
    )
    if not active:
        return {
            "success": False,
            "status_code": 404,
            "error": "active_bond_missing",
            "message": f"Active bond not found for booking {booking}. Create or promote intake first.",
        }

    # 3. Resolve and validate surety
    raw_surety = (
        surety_id
        or active.get("surety_id")
        or active.get("insurance_company")
        or arrest.get("surety_id")
        or ""
    )
    resolved_surety = normalize_surety(raw_surety)
    if not resolved_surety or not is_supported_surety(resolved_surety):
        return {
            "success": False,
            "status_code": 400,
            "error": "surety_id_required",
            "message": f"Valid surety_id ('osi' or 'palmetto') is required. Received: {raw_surety!r}",
        }

    # 4. Resolve and validate case number
    resolved_case_num = (
        case_number
        or active.get("case_number")
        or active.get("Case_Number")
        or arrest.get("case_number")
        or ""
    ).strip()
    if not resolved_case_num:
        # Check charge_details
        cds = charge_details or active.get("charge_details") or arrest.get("charge_details") or []
        for cd in cds:
            if isinstance(cd, dict) and cd.get("case_number"):
                resolved_case_num = str(cd["case_number"]).strip()
                break
    if not resolved_case_num:
        return {
            "success": False,
            "status_code": 400,
            "error": "case_number_required",
            "message": "Court case number is required for bond chain validation",
        }

    # 5. Resolve and validate POAs
    poa_list: List[str] = []
    if poa_numbers and isinstance(poa_numbers, list):
        poa_list = [str(p).strip() for p in poa_numbers if str(p).strip()]
    elif poa_number and str(poa_number).strip():
        poa_list = [str(poa_number).strip()]
    elif active.get("poa_numbers") and isinstance(active["poa_numbers"], list):
        poa_list = [str(p).strip() for p in active["poa_numbers"] if str(p).strip()]
    elif active.get("poa_number") and str(active["poa_number"]).strip():
        poa_list = [str(active["poa_number"]).strip()]

    if not poa_list:
        return {
            "success": False,
            "status_code": 400,
            "error": "poa_required",
            "message": "At least one POA number is required for bond chain validation",
        }

    # Ownership allowlist must include the BondCase UUID before the POA read.
    # The first ensure writes that UUID onto poa_inventory; a second ensure has
    # to recognize it as ours or it 409s with poa_assigned_elsewhere.
    owned_tokens = {token for token in (booking, resolved_case_num) if token}
    active_case_token = _doc_bond_case_id(active)
    if active_case_token:
        owned_tokens.add(active_case_token)
    bc_or: List[Dict[str, Any]] = [
        {"booking_number": booking},
        {"Booking_Number": booking},
    ]
    if active_case_token:
        bc_or.append({"bond_case_id": active_case_token})
        bc_or.append({"Bond_Case_ID": active_case_token})
    existing_bc = await get_collection("bond_cases").find_one({"$or": bc_or})
    if not isinstance(existing_bc, dict):
        existing_bc = None
    existing_case_token = _doc_bond_case_id(existing_bc)
    if existing_case_token:
        owned_tokens.add(existing_case_token)

    poa_col = get_collection("poa_inventory")
    for p_num in poa_list:
        poa_doc = await poa_col.find_one({"poa_number": p_num})
        if not isinstance(poa_doc, dict):
            return {
                "success": False,
                "status_code": 400,
                "error": f"poa_not_found:{p_num}",
                "message": f"POA {p_num} not found in inventory",
            }
        owner_tokens = _poa_owner_tokens(poa_doc)
        foreign = next((token for token in owner_tokens if token not in owned_tokens), "")
        # Every populated case field must be this booking, this case number,
        # or the BondCase UUID. Legacy bulk-assign stores the booking number
        # in bond_case_id; that remains ours. A different UUID does not.
        if foreign:
            return {
                "success": False,
                "status_code": 409,
                "error": f"poa_assigned_elsewhere:{p_num}",
                "message": f"POA {p_num} is already assigned to case/booking {foreign}",
            }
        if not owner_tokens:
            return {
                "success": False,
                "status_code": 400,
                "error": f"poa_not_assigned:{p_num}",
                "message": (
                    f"POA {p_num} is not assigned to booking {booking}. "
                    "Run /api/poa/bulk-assign first."
                ),
            }

    primary_poa = poa_list[0]

    # 6. Resolve amounts
    try:
        resolved_bond_amt = float(
            bond_amount
            or active.get("bond_amount")
            or active.get("Bond_Amount")
            or arrest.get("bond_amount")
            or 0.0
        )
    except (ValueError, TypeError):
        resolved_bond_amt = 0.0

    if resolved_bond_amt <= 0:
        return {
            "success": False,
            "status_code": 400,
            "error": "bond_amount_required",
            "message": "Bond amount must be greater than zero",
        }

    # Explicit zero/blank is a refusal, not a cue to fall back or invent 10%.
    premium_submitted = premium is not None and not (isinstance(premium, str) and not premium.strip())
    if premium_submitted:
        resolved_premium = _positive_amount(premium)
        if resolved_premium is None:
            return {
                "success": False,
                "status_code": 400,
                "error": "premium_required",
                "message": (
                    "Premium must be greater than zero. "
                    "Refusing to invent a premium, including 10% of the bond amount."
                ),
            }
    else:
        resolved_premium = _positive_amount(active.get("premium"))
        if resolved_premium is None:
            resolved_premium = _positive_amount(active.get("premium_amount"))
        if resolved_premium is None:
            resolved_premium = _positive_amount(active.get("Premium"))
        if resolved_premium is None:
            return {
                "success": False,
                "status_code": 400,
                "error": "premium_required",
                "message": (
                    "Premium must already be on the active bond or sent explicitly on this request. "
                    "Refusing to invent a premium, including 10% of the bond amount."
                ),
            }

    # 7. Indemnitor verification (no fabricated contact info)
    active_ind = active.get("indemnitor") if isinstance(active.get("indemnitor"), dict) else {}
    on_file_name = (
        active.get("indemnitor_name")
        or active_ind.get("name")
        or active_ind.get("fullName")
        or ""
    ).strip()
    on_file_email = (
        active.get("indemnitor_email")
        or active_ind.get("email")
        or ""
    ).strip()
    on_file_phone = (
        active.get("indemnitor_phone")
        or active_ind.get("phone")
        or ""
    ).strip()

    # Fallback to intake_queue if active_bonds record lacks indemnitor
    if not on_file_name or not on_file_email:
        intake_doc = await get_collection("intake_queue").find_one(
            {"$or": [{"booking_number": booking}, {"Booking_Number": booking}]}
        )
        if intake_doc:
            intake_ind = intake_doc.get("indemnitor") if isinstance(intake_doc.get("indemnitor"), dict) else {}
            on_file_name = on_file_name or (intake_doc.get("indemnitor_name") or intake_ind.get("name") or "").strip()
            on_file_email = on_file_email or (intake_doc.get("indemnitor_email") or intake_ind.get("email") or "").strip()
            on_file_phone = on_file_phone or (intake_doc.get("indemnitor_phone") or intake_ind.get("phone") or "").strip()

    resolved_ind_name = (indemnitor_name or on_file_name).strip()
    resolved_ind_email = (indemnitor_email or on_file_email).strip()
    resolved_ind_phone = (indemnitor_phone or on_file_phone).strip()

    # Fail closed: must have on-file name and email
    if not on_file_name or not on_file_email:
        return {
            "success": False,
            "status_code": 400,
            "error": "indemnitor_contact_missing",
            "message": (
                "Indemnitor name and email must already exist on file in CRM (active_bonds or intake_queue). "
                "Fail-closed: refusing invented or unverified indemnitor contact."
            ),
        }

    # If caller passed explicit email that conflicts with on-file record
    if indemnitor_email and indemnitor_email.strip().lower() != on_file_email.lower():
        return {
            "success": False,
            "status_code": 400,
            "error": "indemnitor_contact_mismatch",
            "message": f"Submitted email {indemnitor_email} does not match verified on-file email {on_file_email}",
        }

    now_iso = _now_iso()
    now_dt = datetime.now(timezone.utc)

    # 8. Ensure defendants document
    def_col = get_collection("defendants")
    existing_def = await def_col.find_one(
        {"$or": [{"booking_number": booking}, {"Booking_Number": booking}]}
    )
    if not existing_def and active.get("defendant_id"):
        existing_def = await def_col.find_one(
            {"$or": [{"defendant_id": active["defendant_id"]}, {"Defendant_ID": active["defendant_id"]}]}
        )

    first_n = (arrest.get("first_name") or "").strip().title()
    middle_n = (arrest.get("middle_name") or "").strip().title()
    last_n = (arrest.get("last_name") or "").strip().title()
    full_def_name = f"{first_n} {middle_n} {last_n}".strip() or (
        arrest.get("full_name") or active.get("defendant_name") or ""
    ).strip().title()
    dob = arrest.get("dob") or ""
    county = arrest.get("county") or active.get("county") or "Lee"

    if existing_def:
        defendant_id = existing_def.get("defendant_id") or existing_def.get("Defendant_ID")
    else:
        defendant_id = str(uuid.uuid4())
        addr_raw = (arrest.get("address") or "").strip()
        city = (arrest.get("city") or "FORT MYERS").strip().upper()
        state = (arrest.get("state") or "FL").strip().upper()
        zip_code = (arrest.get("zip_code") or arrest.get("zip") or "33901").strip()
        identity_key = f"{last_n.lower()}:{first_n.lower()}:{dob or booking}"

        defendant_doc = {
            "defendant_id": defendant_id,
            "Defendant_ID": defendant_id,
            "booking_number": booking,
            "Booking_Number": booking,
            "identity_key": identity_key,
            "active": True,
            "first_name": first_n,
            "middle_name": middle_n,
            "last_name": last_n,
            "full_name": full_def_name,
            "dob": dob,
            "address": addr_raw or "1528 Broadway",
            "city": city,
            "state": state,
            "zip_code": zip_code,
            "county": county,
            "counties": [county],
            "race": arrest.get("race") or "",
            "sex": arrest.get("sex") or "",
            "height": str(arrest.get("height") or ""),
            "weight": str(arrest.get("weight") or ""),
            "source": source,
            "created_at": now_iso,
            "first_seen": now_iso,
            "last_seen": now_iso,
            "updated_at": now_iso,
            "esign_provider": "docuseal",
            "esign_provider_updated_at": now_iso,
        }
        await def_col.insert_one(defendant_doc)

    # 9. Ensure indemnitors document
    ind_col = get_collection("indemnitors")
    existing_ind = None
    if active.get("indemnitor_id"):
        existing_ind = await ind_col.find_one(
            {"$or": [{"indemnitor_id": active["indemnitor_id"]}, {"Indemnitor_ID": active["indemnitor_id"]}]}
        )
    if not existing_ind:
        existing_ind = await ind_col.find_one(
            {"$or": [{"booking_number": booking}, {"email": resolved_ind_email, "name": resolved_ind_name}]}
        )

    if existing_ind:
        indemnitor_id = existing_ind.get("indemnitor_id") or existing_ind.get("Indemnitor_ID")
    else:
        indemnitor_id = str(uuid.uuid4())
        ind_first, ind_last = _parse_first_last(resolved_ind_name)
        ind_doc = {
            "indemnitor_id": indemnitor_id,
            "Indemnitor_ID": indemnitor_id,
            "booking_number": booking,
            "name": resolved_ind_name,
            "firstName": ind_first,
            "lastName": ind_last,
            "email": resolved_ind_email,
            "phone": resolved_ind_phone,
            "state": active_ind.get("state") or "FL",
            "dl_state": active_ind.get("dl_state") or "FL",
            "role": "primary",
            "notes": f"Staff-directed ensure for booking {booking}",
            "source": source,
            "created_at": now_iso,
            "updated_at": now_iso,
            "esign_provider": "docuseal",
            "esign_provider_updated_at": now_iso,
        }
        await ind_col.insert_one(ind_doc)

    # 10. Upsert matches document (status: validated)
    match_col = get_collection("matches")
    existing_match = await match_col.find_one(
        {"$or": [{"booking_number": booking}, {"arrest_booking_number": booking}, {"match_id": active.get("match_id")}]}
    )
    if existing_match:
        match_id = existing_match.get("match_id") or existing_match.get("Match_ID")
        match_filter = {"_id": existing_match["_id"]} if existing_match.get("_id") else {"match_id": match_id}
        await match_col.update_one(
            match_filter,
            {
                "$set": {
                    "status": "validated",
                    "Status": "validated",
                    "confidence": 100,
                    "Confidence": 100,
                    "defendant_id": defendant_id,
                    "Defendant_ID": defendant_id,
                    "indemnitor_id": indemnitor_id,
                    "Indemnitor_ID": indemnitor_id,
                    "surety_id": resolved_surety,
                    "confirmed_at": now_iso,
                    "confirmed_by": actor_email,
                    "reason": f"Staff-directed ensure Match + BondCase for {full_def_name}; indemnitor {resolved_ind_name}",
                    "source": source,
                    "updated_at": now_iso,
                }
            },
        )
    else:
        match_id = str(uuid.uuid4())
        match_doc = {
            "match_id": match_id,
            "Match_ID": match_id,
            "booking_number": booking,
            "arrest_booking_number": booking,
            "county": county,
            "defendant_id": defendant_id,
            "Defendant_ID": defendant_id,
            "indemnitor_id": indemnitor_id,
            "Indemnitor_ID": indemnitor_id,
            "confidence": 100,
            "Confidence": 100,
            "status": "validated",
            "Status": "validated",
            "confirmed_at": now_iso,
            "confirmed_by": actor_email,
            "surety_id": resolved_surety,
            "reason": f"Staff-directed ensure Match + BondCase for {full_def_name}; indemnitor {resolved_ind_name}",
            "source": source,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        await match_col.insert_one(match_doc)

    # 11. Upsert bond_cases document (existing_bc loaded before the POA check)
    bc_col = get_collection("bond_cases")

    resolved_cds = charge_details or active.get("charge_details") or arrest.get("charge_details") or []
    charges_str = active.get("charges") or arrest.get("charges") or ""

    if existing_bc:
        bond_case_id = _doc_bond_case_id(existing_bc) or str(uuid.uuid4())
        bc_patch: Dict[str, Any] = {
            "bond_case_id": bond_case_id,
            "Bond_Case_ID": bond_case_id,
            "defendant_id": defendant_id,
            "defendant_name": full_def_name,
            "indemnitor_id": indemnitor_id,
            "match_id": match_id,
            "case_number": resolved_case_num,
            "Case_Number": resolved_case_num,
            "poa_number": primary_poa,
            "POA_Number": primary_poa,
            "poa_numbers": poa_list,
            "surety_id": resolved_surety,
            "Surety_ID": resolved_surety,
            "insurance_company": resolved_surety.upper(),
            "bond_amount": resolved_bond_amt,
            "Bond_Amount": resolved_bond_amt,
            "premium": resolved_premium,
            "premium_amount": resolved_premium,
            "status": "active",
            "source": source,
            "updated_at": now_iso,
        }
        if resolved_cds:
            bc_patch["charge_details"] = resolved_cds
        if charges_str:
            bc_patch["charges"] = charges_str
        if packet_id:
            bc_patch["packet_id"] = packet_id
        bc_filter = {"_id": existing_bc["_id"]} if existing_bc.get("_id") else {"bond_case_id": bond_case_id}
        await bc_col.update_one(bc_filter, {"$set": bc_patch})
    else:
        bond_case_id = str(uuid.uuid4())
        bond_case_doc = {
            "bond_case_id": bond_case_id,
            "Bond_Case_ID": bond_case_id,
            "booking_number": booking,
            "Booking_Number": booking,
            "case_number": resolved_case_num,
            "Case_Number": resolved_case_num,
            "defendant_id": defendant_id,
            "defendant_name": full_def_name,
            "indemnitor_id": indemnitor_id,
            "match_id": match_id,
            "poa_number": primary_poa,
            "POA_Number": primary_poa,
            "poa_numbers": poa_list,
            "surety_id": resolved_surety,
            "Surety_ID": resolved_surety,
            "insurance_company": resolved_surety.upper(),
            "bond_amount": resolved_bond_amt,
            "Bond_Amount": resolved_bond_amt,
            "premium": resolved_premium,
            "premium_amount": resolved_premium,
            "county": county,
            "court_date": active.get("court_date") or arrest.get("court_date") or "",
            "court_time": active.get("court_time") or arrest.get("court_time") or "",
            "court_location": active.get("court_location") or arrest.get("court_location") or "",
            "charges": charges_str,
            "charge_details": resolved_cds,
            "status": "active",
            "packet_id": packet_id or "",
            "created_at": now_iso,
            "updated_at": now_iso,
            "source": source,
            "agent_name": actor_email,
        }
        await bc_col.insert_one(bond_case_doc)

    # 12. Patch active_bonds
    active_filter = {"_id": active["_id"]} if active.get("_id") else {"booking_number": booking}
    await get_collection("active_bonds").update_one(
        active_filter,
        {
            "$set": {
                "bond_case_id": bond_case_id,
                "Bond_Case_ID": bond_case_id,
                "match_id": match_id,
                "defendant_id": defendant_id,
                "indemnitor_id": indemnitor_id,
                "poa_number": primary_poa,
                "poa_numbers": poa_list,
                "insurance_company": resolved_surety.upper(),
                "surety_id": resolved_surety,
                "case_number": resolved_case_num,
                "updated_at": now_dt,
            },
            "$push": {
                "status_history": {
                    "status": "match_bondcase_ensured",
                    "timestamp": now_iso,
                    "agent": actor_email,
                    "note": f"Staff ensure Match {match_id} + BondCase {bond_case_id}",
                }
            },
        },
    )

    # 13. Align poa_inventory
    await poa_col.update_many(
        {"poa_number": {"$in": poa_list}},
        {
            "$set": {
                "assigned_defendant": full_def_name,
                "assigned_to": bond_case_id,
                "bond_case_id": bond_case_id,
                "updated_at": now_iso,
            }
        },
    )

    # 14. If packet_id given, patch packet and clear pending_staff_match
    packet_cleared = False
    if packet_id:
        pkt_col = get_collection("paperwork_packets")
        pkt = await pkt_col.find_one({"packet_id": packet_id})
        if pkt:
            pkt_filter = {"_id": pkt["_id"]} if pkt.get("_id") else {"packet_id": packet_id}
            await pkt_col.update_one(
                pkt_filter,
                {
                    "$set": {
                        "pending_staff_match": False,
                        "bond_case_id": bond_case_id,
                        "match_id": match_id,
                        "defendant_id": defendant_id,
                        "indemnitor_id": indemnitor_id,
                        "booking_number": booking,
                        "case_number": resolved_case_num,
                        "poa_number": primary_poa,
                        "poa_numbers": poa_list,
                        "surety_id": resolved_surety,
                        "updated_at": now_iso,
                        "staff_match_backfilled_at": now_iso,
                        "staff_match_backfill_source": source,
                    }
                },
            )
            packet_cleared = True

    # 15. Immutable audit_events record
    await get_collection("audit_events").insert_one(
        {
            "event_id": str(uuid.uuid4()),
            "event_type": "staff_ensure_match_bondcase",
            "booking_number": booking,
            "actor": actor_email,
            "timestamp": now_iso,
            "details": {
                "defendant_id": defendant_id,
                "indemnitor_id": indemnitor_id,
                "match_id": match_id,
                "bond_case_id": bond_case_id,
                "packet_id": packet_id,
                "poa_numbers": poa_list,
                "case_number": resolved_case_num,
                "surety_id": resolved_surety,
                "bond_amount": resolved_bond_amt,
                "premium": resolved_premium,
                "pending_staff_match_cleared": packet_cleared,
            },
        }
    )

    return {
        "success": True,
        "action": "ensured",
        "booking_number": booking,
        "defendant_id": defendant_id,
        "indemnitor_id": indemnitor_id,
        "match_id": match_id,
        "bond_case_id": bond_case_id,
        "packet_id": packet_id,
        "case_number": resolved_case_num,
        "surety_id": resolved_surety,
        "poa_numbers": poa_list,
        "primary_poa": primary_poa,
        "bond_amount": resolved_bond_amt,
        "premium": resolved_premium,
        "pending_staff_match": False,
    }
