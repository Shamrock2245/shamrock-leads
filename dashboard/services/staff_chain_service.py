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
from dashboard.services.exclusive_insert import insert_new
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


# ── Self-serve draft (no invented bond, premium, or POA) ────────────────────
# ensure_match_bondcase stays the path for a booking that already has an
# arrest, an active bond, a surety, a case number, a POA, and a premium.
# A walk-in ID confirm has none of those. This helper dedupes on phone +
# name + DOB and, on a miss, inserts one draft BondCase. It does not pull a
# power and it does not calculate a premium.
#
# The draft's ``_id`` is a uuid5 of the dedupe key. MongoDB's existing unique
# ``_id`` index makes the second insert fail with DuplicateKeyError. No new
# index is created. A legacy case is reused only when the role-slot lookup
# sees it. Two visible matches fail closed. Rows that already exist are not
# deleted.

_SELF_SERVE_SOURCE = "self_serve"
_SELF_SERVE_STATUS = "pending_owner_review"
_HARD_CHAIN_ERRORS = frozenset({
    "poa_assigned_elsewhere",
    "indemnitor_contact_mismatch",
})


def _stable_id(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, label))


def self_serve_ids(dedupe_key: str, role: str) -> Dict[str, str]:
    """Ids both workers can compute before either one reads the database."""
    case_id = _stable_id("shamrock-self-serve-case-v1:" + dedupe_key)
    person_id = _stable_id(f"shamrock-self-serve-person-v1:{case_id}:{role}")
    match_id = _stable_id(f"shamrock-self-serve-match-v1:{case_id}:{role}")
    return {"bond_case_id": case_id, "person_id": person_id, "match_id": match_id}


def _digits_phone(raw: Any) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else ""


def _normalize_dob(raw: Any) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) != 8:
        return digits
    year_first = int(digits[:4])
    if 1900 <= year_first <= 2100:
        return digits
    month = int(digits[:2])
    if 1 <= month <= 12:
        return digits[4:8] + digits[:4]
    return digits


def _first_text(doc: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = doc.get(key)
        if isinstance(value, dict):
            continue
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _same_identity(phone: str, name: str, dob: str, doc_phone: Any, doc_name: Any, doc_dob: Any) -> bool:
    from dashboard.services.identity_verification_service import names_match

    if not phone or not name or not dob:
        return False
    if _digits_phone(doc_phone) != phone:
        return False
    if _normalize_dob(doc_dob) != dob:
        return False
    return names_match(name, doc_name)


async def _collect_docs(name: str, query: Dict[str, Any], limit: int = 25) -> List[Dict[str, Any]]:
    cursor = get_collection(name).find(query)
    if hasattr(cursor, "to_list"):
        rows = await cursor.to_list(length=limit)
        return [row for row in rows if isinstance(row, dict)]
    rows = []
    async for row in cursor:
        if isinstance(row, dict):
            rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _case_id_of(doc: Dict[str, Any]) -> str:
    return _owner_token(doc.get("bond_case_id") or doc.get("Bond_Case_ID"))


def _canonical_self_serve_case(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Earliest draft wins when a race inserted more than one row for one key."""
    usable = [row for row in rows if isinstance(row, dict) and _case_id_of(row)]
    if not usable:
        return None
    usable.sort(key=lambda row: (str(row.get("created_at") or ""), _case_id_of(row)))
    return usable[0]


def _slot_identity(case: Dict[str, Any], role: str) -> Tuple[str, str, str, str]:
    """Return person id, phone, name, dob for one role slot. Empty when unset."""
    nested_key = {"defendant": "defendant", "coindemnitor": "coindemnitor"}.get(role, "indemnitor")
    nested = case.get(nested_key) if isinstance(case.get(nested_key), dict) else {}
    if role == "defendant":
        return (
            _owner_token(case.get("defendant_id") or case.get("Defendant_ID") or nested.get("defendant_id")),
            _first_text(case, "defendant_phone") or _first_text(nested, "phone"),
            _first_text(case, "defendant_name") or _first_text(nested, "name", "full_name"),
            _first_text(case, "defendant_dob") or _first_text(nested, "dob"),
        )
    if role == "coindemnitor":
        return (
            _owner_token(case.get("co_indemnitor_id") or nested.get("indemnitor_id") or nested.get("co_indemnitor_id")),
            _first_text(case, "coindemnitor_phone") or _first_text(nested, "phone"),
            _first_text(case, "coindemnitor_name") or _first_text(nested, "name", "full_name"),
            _first_text(case, "coindemnitor_dob") or _first_text(nested, "dob"),
        )
    return (
        _owner_token(case.get("indemnitor_id") or case.get("Indemnitor_ID") or nested.get("indemnitor_id")),
        _first_text(case, "indemnitor_phone") or _first_text(nested, "phone"),
        _first_text(case, "indemnitor_name") or _first_text(nested, "name", "full_name"),
        _first_text(case, "indemnitor_dob") or _first_text(nested, "dob"),
    )


def _case_matches_signer(case: Dict[str, Any], phone: str, name: str, dob: str, role: str) -> bool:
    """True only when this role's slot is the same phone, name, and date of birth."""
    _person_id, slot_phone, slot_name, slot_dob = _slot_identity(case, role)
    return _same_identity(phone, name, dob, slot_phone, slot_name, slot_dob)


async def _cases_for_signer(
    phone: str,
    name: str,
    dob: str,
    dedupe_key: str,
    role: str,
) -> List[Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}

    def _keep(case: Optional[Dict[str, Any]]) -> None:
        if not isinstance(case, dict):
            return
        case_id = _case_id_of(case)
        if case_id and case_id not in found and _case_matches_signer(case, phone, name, dob, role):
            found[case_id] = case

    keyed = await _collect_docs("bond_cases", {"self_serve_dedupe_key": dedupe_key})
    for row in keyed:
        if _case_matches_signer(row, phone, name, dob, role) or not _slot_identity(row, role)[2]:
            case_id = _case_id_of(row)
            if case_id:
                found[case_id] = row
    phone_field = {
        "defendant": "defendant_phone",
        "coindemnitor": "coindemnitor_phone",
    }.get(role, "indemnitor_phone")
    for row in await _collect_docs("bond_cases", {phone_field: {"$regex": phone + "$"}}):
        if _case_matches_signer(row, phone, name, dob, role):
            _keep(row)
    if role == "defendant":
        people = await _collect_docs("defendants", {"phone": {"$regex": phone + "$"}})
        id_query = lambda person_id: {"$or": [{"defendant_id": person_id}, {"Defendant_ID": person_id}]}
    else:
        people = await _collect_docs("indemnitors", {"phone": {"$regex": phone + "$"}})
        if role == "coindemnitor":
            id_query = lambda person_id: {"co_indemnitor_id": person_id}
        else:
            id_query = lambda person_id: {"$or": [{"indemnitor_id": person_id}, {"Indemnitor_ID": person_id}]}
    id_field = "defendant_id" if role == "defendant" else "indemnitor_id"
    alt_field = "Defendant_ID" if role == "defendant" else "Indemnitor_ID"
    for person in people:
        if role == "coindemnitor" and str(person.get("role") or "").strip().lower() not in {"coindemnitor", "co_indemnitor", "co-indemnitor"}:
            continue
        if role == "indemnitor" and str(person.get("role") or "").strip().lower() in {"coindemnitor", "co_indemnitor", "co-indemnitor"}:
            continue
        person_name = _first_text(person, "name", "full_name")
        person_dob = _first_text(person, "dob")
        if not _same_identity(phone, name, dob, person.get("phone"), person_name, person_dob):
            continue
        person_id = _owner_token(person.get(id_field) or person.get(alt_field))
        if not person_id:
            continue
        for case in await _collect_docs("bond_cases", id_query(person_id)):
            if _case_matches_signer(case, phone, name, dob, role):
                _keep(case)
    return list(found.values())


def _identity_patch(role: str, identity: Dict[str, str], phone: str, email: str) -> Dict[str, Any]:
    """Identity and contact only. Bond, premium, and POA are not in this patch."""
    prefix = {"defendant": "defendant", "coindemnitor": "coindemnitor"}.get(role, "indemnitor")
    patch: Dict[str, Any] = {}
    for suffix in ("name", "dob", "address", "city", "state", "zip", "dl", "dl_state", "sex", "dl_expiration", "phone", "email"):
        key = f"{prefix}_{suffix}"
        value = str(identity.get(key) or "").strip()
        if suffix == "phone" and phone:
            value = phone
        if suffix == "email" and email:
            value = email
        if value:
            patch[key] = value
    return patch


async def _write_party_identity(
    *,
    role: str,
    person_id: str,
    case_id: str,
    identity: Dict[str, str],
    phone: str,
    email: str,
    name: str,
    dob: str,
    scan_blob: Dict[str, Any],
    now_iso: str,
) -> None:
    collection = "defendants" if role == "defendant" else "indemnitors"
    id_field = "defendant_id" if role == "defendant" else "indemnitor_id"
    col = get_collection(collection)
    alt_field = "Defendant_ID" if role == "defendant" else "Indemnitor_ID"
    existing = await col.find_one({"$or": [{id_field: person_id}, {alt_field: person_id}]})
    prefix = {"defendant": "defendant", "coindemnitor": "coindemnitor"}.get(role, "indemnitor")
    party_fields = {
        "name": name,
        "full_name": name,
        "phone": phone,
        "email": email,
        "dob": dob,
        "address": identity.get(f"{prefix}_address") or "",
        "city": identity.get(f"{prefix}_city") or "",
        "state": identity.get(f"{prefix}_state") or "",
        "zip": identity.get(f"{prefix}_zip") or "",
        "dl": identity.get(f"{prefix}_dl") or "",
        "dl_state": identity.get(f"{prefix}_dl_state") or "",
        "sex": identity.get(f"{prefix}_sex") or "",
        "dl_expiration": identity.get(f"{prefix}_dl_expiration") or "",
        "bond_case_id": case_id,
        "role": "coindemnitor" if role == "coindemnitor" else ("defendant" if role == "defendant" else "primary"),
        "source": _SELF_SERVE_SOURCE,
        "updated_at": now_iso,
    }
    party_fields = {key: value for key, value in party_fields.items() if value not in ("", None)}
    party_fields["bond_case_id"] = case_id
    party_fields["source"] = _SELF_SERVE_SOURCE
    scan_row = {
        "scan_id": scan_blob.get("scan_id") or "",
        "role": role,
        "source": _SELF_SERVE_SOURCE,
        "recorded_at": now_iso,
        "success": True,
        "packet_id": "",
        "extracted": {
            "full_name": str(scan_blob.get("full_name") or name),
            "dl_state": str(scan_blob.get("dl_state") or ""),
        },
    }
    already = [
        row for row in (existing.get("id_scan_records") or [])
        if isinstance(row, dict) and row.get("scan_id") == scan_row["scan_id"]
    ] if isinstance(existing, dict) else []
    if isinstance(existing, dict):
        update: Dict[str, Any] = {"$set": party_fields}
        if not already:
            update["$push"] = {"id_scan_records": scan_row}
        if not isinstance(existing.get("id_scan"), dict) or not existing.get("id_scan"):
            update["$set"]["id_scan"] = {
                "success": True,
                "full_name": scan_row["extracted"]["full_name"],
                "dl_state": scan_row["extracted"]["dl_state"],
                "role": role,
                "scan_id": scan_row["scan_id"],
            }
        filt = {"_id": existing["_id"]} if existing.get("_id") else {id_field: person_id}
        await col.update_one(filt, update)
        return
    doc = {
        "_id": person_id,
        id_field: person_id,
        "Indemnitor_ID" if role != "defendant" else "Defendant_ID": person_id,
        "created_at": now_iso,
        "id_scan": {
            "success": True,
            "full_name": scan_row["extracted"]["full_name"],
            "dl_state": scan_row["extracted"]["dl_state"],
            "role": role,
            "scan_id": scan_row["scan_id"],
        },
        "id_scan_records": [scan_row],
        **party_fields,
    }
    inserted = await insert_new(col, doc)
    if inserted:
        return
    raced = await col.find_one({"_id": person_id})
    if isinstance(raced, dict):
        await _write_party_identity(
            role=role,
            person_id=person_id,
            case_id=case_id,
            identity=identity,
            phone=phone,
            email=email,
            name=name,
            dob=dob,
            scan_blob=scan_blob,
            now_iso=now_iso,
        )


async def ensure_self_serve_bondcase(
    *,
    phone: str,
    name: str,
    dob: str,
    role: str,
    email: str = "",
    identity: Optional[Dict[str, str]] = None,
    scan_blob: Optional[Dict[str, Any]] = None,
    booking_number: str = "",
    actor: str = "auto",
    walk_in_channel: str = "",
) -> Dict[str, Any]:
    """Reuse one BondCase for this phone + name + DOB, or insert one draft.

    ``ensure_match_bondcase`` runs first when a booking number is already
    on the session. It still refuses a missing arrest, POA, premium, or bond
    amount, and this function does not fill those in. A miss inserts one
    draft with ``source=self_serve`` and ``status=pending_owner_review``.
    A second call with the same dedupe key returns the same case.
    """
    from dashboard.services.id_ocr_service import name_letters_key, normalize_person_name
    from dashboard.services.identity_verification_service import names_match

    role = str(role or "").strip().lower()
    if role not in {"indemnitor", "coindemnitor", "defendant"}:
        return {"success": False, "error": "role_invalid"}
    phone_n = _digits_phone(phone)
    dob_n = _normalize_dob(dob)
    name_n = normalize_person_name(name)
    if len(phone_n) != 10:
        return {"success": False, "error": "phone_required"}
    if not dob_n:
        return {"success": False, "error": "dob_required"}
    if not names_match(name_n, name_n):
        return {"success": False, "error": "name_required"}
    identity = dict(identity or {})
    scan_blob = dict(scan_blob or {})
    dedupe_key = "|".join((phone_n, name_letters_key(name_n), dob_n, role))
    booking = str(booking_number or "").strip()
    return await _ensure_self_serve_locked(
        phone=phone_n,
        name=name_n,
        dob=dob_n,
        role=role,
        email=email,
        identity=identity,
        scan_blob=scan_blob,
        dedupe_key=dedupe_key,
        booking=booking,
        actor=actor or "auto",
        walk_in_channel=str(walk_in_channel or ""),
    )


async def _ensure_self_serve_locked(
    *,
    phone: str,
    name: str,
    dob: str,
    role: str,
    email: str,
    identity: Dict[str, str],
    scan_blob: Dict[str, Any],
    dedupe_key: str,
    booking: str,
    actor: str,
    walk_in_channel: str = "",
) -> Dict[str, Any]:
    if booking:
        chain_kwargs: Dict[str, Any] = {
            "booking_number": booking,
            "actor_email": actor or "auto",
            "source": _SELF_SERVE_SOURCE,
        }
        if role == "indemnitor":
            chain_kwargs["indemnitor_name"] = name
            chain_kwargs["indemnitor_email"] = email or None
            chain_kwargs["indemnitor_phone"] = phone
        ensured = await ensure_match_bondcase(**chain_kwargs)
        if ensured.get("success"):
            case = await get_collection("bond_cases").find_one(
                {"bond_case_id": ensured.get("bond_case_id")}
            )
            if isinstance(case, dict):
                return await _attach_signer_to_case(
                    case,
                    role=role,
                    phone=phone,
                    name=name,
                    dob=dob,
                    email=email,
                    identity=identity,
                    scan_blob=scan_blob,
                    dedupe_key=dedupe_key,
                    created=False,
                    reused=True,
                    actor=actor or "auto",
                )
        error = str(ensured.get("error") or "")
        error_code = error.split(":", 1)[0]
        if error_code in _HARD_CHAIN_ERRORS:
            return {"success": False, "error": error_code, "case_created": False}

    matches = await _cases_for_signer(phone, name, dob, dedupe_key, role)
    if len(matches) > 1:
        return {"success": False, "error": "ambiguous_case", "case_created": False}
    if len(matches) == 1:
        return await _attach_signer_to_case(
            matches[0],
            role=role,
            phone=phone,
            name=name,
            dob=dob,
            email=email,
            identity=identity,
            scan_blob=scan_blob,
            dedupe_key=dedupe_key,
            created=False,
            reused=True,
            actor=actor or "auto",
        )
    return await _insert_self_serve_draft(
        role=role,
        phone=phone,
        name=name,
        dob=dob,
        email=email,
        identity=identity,
        scan_blob=scan_blob,
        dedupe_key=dedupe_key,
        actor=actor or "auto",
        walk_in_channel=walk_in_channel,
    )


async def _attach_signer_to_case(
    case: Dict[str, Any],
    *,
    role: str,
    phone: str,
    name: str,
    dob: str,
    email: str,
    identity: Dict[str, str],
    scan_blob: Dict[str, Any],
    dedupe_key: str,
    created: bool,
    reused: bool,
    actor: str,
) -> Dict[str, Any]:
    from dashboard.services.identity_verification_service import names_match

    case_id = _case_id_of(case)
    slot_id, slot_phone, slot_name, slot_dob = _slot_identity(case, role)
    if slot_phone and _digits_phone(slot_phone) != _digits_phone(phone):
        return {
            "success": False,
            "error": "role_phone_mismatch",
            "bond_case_id": case_id,
            "case_created": False,
        }
    if slot_name and not names_match(name, slot_name):
        return {"success": False, "error": "role_conflict", "bond_case_id": case_id, "case_created": False}
    if slot_dob and _normalize_dob(slot_dob) != dob:
        return {"success": False, "error": "role_conflict", "bond_case_id": case_id, "case_created": False}
    now_iso = _now_iso()
    person_id = slot_id or str(uuid.uuid4())
    patch = _identity_patch(role, identity, phone, email)
    nested_key = {"defendant": "defendant", "coindemnitor": "coindemnitor"}.get(role, "indemnitor")
    nested = case.get(nested_key) if isinstance(case.get(nested_key), dict) else {}
    nested = {**nested, "name": name, "phone": phone, "dob": dob, "email": email}
    if role == "defendant":
        patch["defendant_id"] = person_id
        patch["Defendant_ID"] = person_id
    elif role == "coindemnitor":
        patch["co_indemnitor_id"] = person_id
    else:
        patch["indemnitor_id"] = person_id
        patch["Indemnitor_ID"] = person_id
    patch[nested_key] = nested
    if not case.get("self_serve_dedupe_key"):
        patch["self_serve_dedupe_key"] = dedupe_key
    contact_keys = ("name", "phone", "email", "dob")
    nested_before = case.get(nested_key) if isinstance(case.get(nested_key), dict) else {}
    old_state: Dict[str, Any] = {}
    new_state: Dict[str, Any] = {}
    for key, value in patch.items():
        if key in {"indemnitor", "coindemnitor", "defendant", "self_serve_dedupe_key"}:
            continue
        if key.endswith("_id") or key.endswith("_ID"):
            continue
        previous = case.get(key, "")
        if previous != value:
            old_state[key] = previous if previous is not None else ""
            new_state[key] = value
    old_contact = {key: nested_before.get(key, "") for key in contact_keys}
    new_contact = {key: nested.get(key, "") for key in contact_keys}
    if old_contact != new_contact:
        old_state[nested_key] = old_contact
        new_state[nested_key] = new_contact
    collection = "defendants" if role == "defendant" else "indemnitors"
    id_field = "defendant_id" if role == "defendant" else "indemnitor_id"
    alt_field = "Defendant_ID" if role == "defendant" else "Indemnitor_ID"
    prior_person = await get_collection(collection).find_one({"$or": [{id_field: person_id}, {alt_field: person_id}]})
    person_keys = ("name", "phone", "email", "dob", "address", "city", "state", "zip", "dl", "dl_state")
    if isinstance(prior_person, dict):
        prefix = {"defendant": "defendant", "coindemnitor": "coindemnitor"}.get(role, "indemnitor")
        person_old = {key: prior_person.get(key, "") for key in person_keys}
        person_new = {
            "name": name,
            "phone": phone,
            "email": email,
            "dob": dob,
            "address": identity.get(f"{prefix}_address") or prior_person.get("address") or "",
            "city": identity.get(f"{prefix}_city") or prior_person.get("city") or "",
            "state": identity.get(f"{prefix}_state") or prior_person.get("state") or "",
            "zip": identity.get(f"{prefix}_zip") or prior_person.get("zip") or "",
            "dl": identity.get(f"{prefix}_dl") or prior_person.get("dl") or "",
            "dl_state": identity.get(f"{prefix}_dl_state") or prior_person.get("dl_state") or "",
        }
        if person_old != person_new:
            old_state["person"] = person_old
            new_state["person"] = person_new
    filt = {"_id": case["_id"]} if case.get("_id") else {"bond_case_id": case_id}
    await get_collection("bond_cases").update_one(filt, {"$set": patch})
    await _write_party_identity(
        role=role,
        person_id=person_id,
        case_id=case_id,
        identity=identity,
        phone=phone,
        email=email,
        name=name,
        dob=dob,
        scan_blob=scan_blob,
        now_iso=now_iso,
    )
    if str(case.get("source") or "") == _SELF_SERVE_SOURCE or str(patch.get("source") or "") == _SELF_SERVE_SOURCE:
        await _ensure_self_serve_match(
            {**case, **patch},
            role=role,
            person_id=person_id,
            actor=actor,
            now_iso=now_iso,
        )
        await _ensure_draft_audit(
            {**case, **patch},
            role=role,
            person_id=person_id,
            actor=actor,
            now_iso=now_iso,
        )
    if old_state or new_state:
        await get_collection("audit_events").insert_one({
            "event_id": str(uuid.uuid4()),
            "event_type": "self_serve_identity_updated",
            "action": "self_serve_identity_updated",
            "entity_type": "bond_case",
            "entity_id": case_id,
            "bond_case_id": case_id,
            "actor": actor or "auto",
            "agent_name": "paperwork_self_serve",
            "role": role,
            "person_id": person_id,
            "source": _SELF_SERVE_SOURCE,
            "reason": "self_serve_identity_confirmed",
            "created_at": now_iso,
            "timestamp": now_iso,
            "old_state": old_state,
            "new_state": new_state,
        })
    refreshed = await get_collection("bond_cases").find_one({"bond_case_id": case_id}) or {**case, **patch}
    return {
        "success": True,
        "created": created,
        "reused": reused,
        "bond_case_id": case_id,
        "person_id": person_id,
        "defendant_id": _owner_token(refreshed.get("defendant_id") or refreshed.get("Defendant_ID")),
        "indemnitor_id": _owner_token(refreshed.get("indemnitor_id") or refreshed.get("Indemnitor_ID")),
        "co_indemnitor_id": _owner_token(refreshed.get("co_indemnitor_id")),
        "match_id": _owner_token(refreshed.get("match_id") or refreshed.get("Match_ID")),
        "case": refreshed,
    }


async def _ensure_self_serve_match(
    case: Dict[str, Any],
    *,
    role: str,
    person_id: str,
    actor: str,
    now_iso: str,
) -> str:
    """One match row per draft. A second insert of the same ``_id`` is a no-op."""
    case_id = _case_id_of(case)
    match_id = _owner_token(case.get("match_id") or case.get("Match_ID")) or _stable_id(
        f"shamrock-self-serve-match-v1:{case_id}:{role}"
    )
    defendant_id = _owner_token(case.get("defendant_id") or case.get("Defendant_ID"))
    indemnitor_id = _owner_token(case.get("indemnitor_id") or case.get("Indemnitor_ID") or case.get("co_indemnitor_id"))
    await insert_new(get_collection("matches"), {
        "_id": match_id,
        "match_id": match_id,
        "Match_ID": match_id,
        "bond_case_id": case_id,
        "defendant_id": defendant_id,
        "indemnitor_id": indemnitor_id,
        "status": _SELF_SERVE_STATUS,
        "Status": _SELF_SERVE_STATUS,
        "source": _SELF_SERVE_SOURCE,
        "confirmed_by": actor or "auto",
        "created_at": now_iso,
        "updated_at": now_iso,
    })
    return match_id


async def _ensure_draft_audit(
    case: Dict[str, Any],
    *,
    role: str,
    person_id: str,
    actor: str,
    now_iso: str,
) -> None:
    """Insert the creation audit once. A retry keeps the first row."""
    case_id = _case_id_of(case)
    audit_id = _stable_id(f"shamrock-self-serve-draft-audit-v1:{case_id}:{role}")
    await insert_new(get_collection("audit_events"), {
        "_id": audit_id,
        "event_id": audit_id,
        "event_type": "self_serve_draft_bondcase",
        "action": "self_serve_draft_bondcase",
        "entity_type": "bond_case",
        "entity_id": case_id,
        "bond_case_id": case_id,
        "actor": actor or "auto",
        "agent_name": "paperwork_self_serve",
        "role": role,
        "person_id": person_id,
        "source": _SELF_SERVE_SOURCE,
        "reason": "self_serve_draft_created",
        "created_at": now_iso,
        "timestamp": now_iso,
        "old_state": {},
        "new_state": {
            "status": _SELF_SERVE_STATUS,
            "source": _SELF_SERVE_SOURCE,
            **(
                {"surety_id": str(case.get("surety_id"))}
                if str(case.get("surety_id") or "").strip()
                else {}
            ),
            **(
                {"intake_channel": str(case.get("intake_channel"))}
                if str(case.get("intake_channel") or "").strip()
                else {}
            ),
        },
    })


async def _insert_self_serve_draft(
    *,
    role: str,
    phone: str,
    name: str,
    dob: str,
    email: str,
    identity: Dict[str, str],
    scan_blob: Dict[str, Any],
    dedupe_key: str,
    actor: str,
    walk_in_channel: str = "",
) -> Dict[str, Any]:
    now_iso = _now_iso()
    ids = self_serve_ids(dedupe_key, role)
    bond_case_id = ids["bond_case_id"]
    person_id = ids["person_id"]
    existing = await get_collection("bond_cases").find_one({"_id": bond_case_id})
    if not isinstance(existing, dict):
        existing = await get_collection("bond_cases").find_one({"self_serve_dedupe_key": dedupe_key})
    if isinstance(existing, dict):
        return await _attach_signer_to_case(
            existing,
            role=role,
            phone=phone,
            name=name,
            dob=dob,
            email=email,
            identity=identity,
            scan_blob=scan_blob,
            dedupe_key=dedupe_key,
            created=False,
            reused=True,
            actor=actor,
        )
    defendant_id = person_id if role == "defendant" else ""
    indemnitor_id = person_id if role == "indemnitor" else ""
    co_id = person_id if role == "coindemnitor" else ""
    # Surety stays unset unless this request is creating a walk-in draft.
    # An existing case, including one with no surety, is not updated here.
    patch = _identity_patch(role, identity, phone, email)
    nested_key = {"defendant": "defendant", "coindemnitor": "coindemnitor"}.get(role, "indemnitor")
    patch[nested_key] = {"name": name, "phone": phone, "dob": dob, "email": email}
    doc: Dict[str, Any] = {
        "_id": bond_case_id,
        "bond_case_id": bond_case_id,
        "Bond_Case_ID": bond_case_id,
        "self_serve_dedupe_key": dedupe_key,
        "match_id": ids["match_id"],
        "Match_ID": ids["match_id"],
        "match_status": _SELF_SERVE_STATUS,
        "status": _SELF_SERVE_STATUS,
        "source": _SELF_SERVE_SOURCE,
        "defendant_id": defendant_id,
        "Defendant_ID": defendant_id,
        "indemnitor_id": indemnitor_id,
        "Indemnitor_ID": indemnitor_id,
        "co_indemnitor_id": co_id,
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_by": actor or "auto",
        **patch,
    }
    from dashboard.services.paperwork_self_serve import _WALK_IN_CHANNELS, _WALK_IN_SURETY

    channel = str(walk_in_channel or "").strip().lower()
    if channel in _WALK_IN_CHANNELS and not str(doc.get("surety_id") or "").strip():
        doc["surety_id"] = _WALK_IN_SURETY
        doc["Surety_ID"] = _WALK_IN_SURETY
        doc["intake_channel"] = channel
    inserted = await insert_new(get_collection("bond_cases"), doc)
    if not inserted:
        stored = await get_collection("bond_cases").find_one({"_id": bond_case_id})
        if not isinstance(stored, dict):
            return {"success": False, "error": "case_not_stored", "case_created": False}
        return await _attach_signer_to_case(
            stored,
            role=role,
            phone=phone,
            name=name,
            dob=dob,
            email=email,
            identity=identity,
            scan_blob=scan_blob,
            dedupe_key=dedupe_key,
            created=False,
            reused=True,
            actor=actor,
        )
    logger.info("[self-serve] draft bond case %s role=%s", bond_case_id, role)
    return await _attach_signer_to_case(
        doc,
        role=role,
        phone=phone,
        name=name,
        dob=dob,
        email=email,
        identity=identity,
        scan_blob=scan_blob,
        dedupe_key=dedupe_key,
        created=True,
        reused=False,
        actor=actor,
    )
