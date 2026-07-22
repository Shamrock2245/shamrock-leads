from __future__ import annotations
"""
Indemnitors Router — FastAPI port of the indemnitor management routes in app.py.
Provides all endpoints needed by sl-indemnitor.js:
  GET  /api/indemnitors                       — list all indemnitors (by-bond view)
  GET  /api/indemnitors/by-person             — group by phone (by-person view)
  GET  /api/indemnitors/search-existing       — smart cross-entity search for Add modal
  POST /api/indemnitors/create                — create / link indemnitor to a bond
  GET  /api/indemnitors/{booking_number}      — full indemnitor detail
  PATCH /api/indemnitors/{booking_number}     — update indemnitor profile
  GET  /api/indemnitors/{booking_number}/documents   — document checklist
  PATCH /api/indemnitors/{booking_number}/documents  — toggle document signed
  GET  /api/indemnitors/{booking_number}/uploads     — list KYC uploads
  POST /api/indemnitors/{booking_number}/uploads     — upload KYC file
  DELETE /api/indemnitors/{booking_number}/uploads/{file_id} — delete KYC file
  POST /api/indemnitors/{booking_number}/payment-link — generate payment link
  POST /api/indemnitors/{booking_number}/remove       — remove a cosigner
  PATCH /api/prospective-bonds/{booking_number}/indemnitor — update single indemnitor
"""

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from dashboard.deps import get_collection, get_db

router = APIRouter(prefix="/api", tags=["indemnitors"])

# ── Constants (mirrored from app.py) ─────────────────────────────────────────

INDEMNITOR_FIELDS = [
    # Personal
    "name", "firstName", "middleName", "lastName", "relationship",
    "dob", "ssn", "dl", "dlState", "sex", "race", "height", "weight",
    # Contact
    "phone", "email", "callback_phone",
    # Address
    "address", "city", "state", "zip",
    # Employment
    "employer", "employerPhone", "employerAddress", "employerCity",
    "employerState", "supervisor", "supervisorPhone", "occupation",
    "monthlyIncome",
    # Spouse
    "spouseName", "spousePhone", "spouseEmployer", "spouseEmployerPhone",
    "spouseAddress", "spouseDob", "spouseRelationship",
    # References
    "ref1Name", "ref1Phone", "ref1Address", "ref1Relationship",
    "ref2Name", "ref2Phone", "ref2Address", "ref2Relationship",
]

DOCUMENT_CHECKLIST = {
    "shamrock": [
        {"key": "indemnity_agreement", "label": "Indemnity Agreement"},
        {"key": "bail_bond_application", "label": "Bail Bond Application"},
        {"key": "receipt", "label": "Premium Receipt"},
        {"key": "notice_to_indemnitor", "label": "Notice to Indemnitor"},
        {"key": "privacy_notice", "label": "Privacy Notice"},
        {"key": "gps_consent", "label": "GPS Monitoring Consent"},
        {"key": "payment_plan", "label": "Payment Plan Agreement"},
        {"key": "collateral_receipt", "label": "Collateral Receipt"},
    ],
    "osi": [
        {"key": "osi_appearance_bond", "label": "OSI Appearance Bond"},
        {"key": "osi_power_of_attorney", "label": "OSI Power of Attorney"},
        {"key": "osi_agent_affidavit", "label": "OSI Agent Affidavit"},
    ],
    "palmetto": [
        {"key": "palmetto_appearance_bond", "label": "Palmetto Appearance Bond"},
        {"key": "palmetto_power_of_attorney", "label": "Palmetto Power of Attorney"},
        {"key": "palmetto_agent_affidavit", "label": "Palmetto Agent Affidavit"},
    ],
}

from dashboard.services.identity_media_service import (
    ALL_DOC_TYPES as KYC_DOC_TYPES,
    ID_PHOTO_SLOTS,
    UPLOAD_DIR,
    delete_upload_file,
    merge_id_photos_field,
    save_upload_file,
    slot_map_from_uploads,
)

ALLOWED_UPLOAD_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "heic"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_dt(val) -> str:
    """Convert a datetime or any value to ISO string safely."""
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val) if val else ""


def _extract_indemnitors(doc: dict, bond_type: str, stage: str = "") -> list:
    """Extract indemnitor(s) from a bond document, handling both legacy and array formats."""
    items = []
    indemnitors = doc.get("indemnitors", [])
    if not indemnitors and doc.get("indemnitor"):
        indemnitors = [doc.get("indemnitor", {})]

    for ind in indemnitors:
        ind_name = ind.get("name") or " ".join(
            filter(None, [ind.get("firstName", ""), ind.get("lastName", "")])
        ) or ""
        if not ind_name and not ind.get("phone") and not ind.get("email"):
            continue
        items.append({
            "booking_number": doc.get("booking_number", ""),
            "defendant_name": doc.get("defendant_name", ""),
            "county": doc.get("county", ""),
            "bond_amount": doc.get("bond_amount", 0),
            "stage": stage or doc.get("stage", ""),
            "status": doc.get("status", ""),
            "bond_type": bond_type,
            "indemnitor": ind,
            "indemnitor_name": ind_name,
            "indemnitor_phone": ind.get("phone", ""),
            "indemnitor_email": ind.get("email", ""),
            "indemnitor_relationship": ind.get("relationship", ""),
            "indemnitor_role": ind.get("role", "primary"),
            "total_cosigners": len(indemnitors),
            "source": doc.get("source", "dashboard"),
            "documents": doc.get("documents", {}),
            "created_at": _safe_dt(doc.get("created_at", "")),
            "updated_at": _safe_dt(doc.get("updated_at", doc.get("created_at", ""))),
        })
    return items


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/indemnitors")
async def api_indemnitors_list(
    search: str = "",
    source: str = "",
    stage: str = "",
    limit: int = Query(100, ge=1, le=500),
):
    """List all indemnitors across prospective_bonds, active_bonds, and unlinked standalone records."""
    try:
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")
        indemnitors_coll = get_collection("indemnitors")
        results = []

        # ── Pull from prospective_bonds ──
        p_query: dict = {"$or": [{"indemnitor": {"$exists": True}}, {"indemnitors": {"$exists": True}}]}
        if search:
            p_query = {"$and": [p_query, {"$or": [
                {"indemnitor.name": {"$regex": search, "$options": "i"}},
                {"indemnitor.firstName": {"$regex": search, "$options": "i"}},
                {"indemnitor.lastName": {"$regex": search, "$options": "i"}},
                {"indemnitor.phone": {"$regex": search, "$options": "i"}},
                {"indemnitors.name": {"$regex": search, "$options": "i"}},
                {"indemnitors.phone": {"$regex": search, "$options": "i"}},
                {"defendant_name": {"$regex": search, "$options": "i"}},
                {"booking_number": {"$regex": search, "$options": "i"}},
            ]}]}
        if stage:
            p_query["stage"] = stage

        async for doc in prospective_bonds.find(p_query).sort("updated_at", -1).limit(limit):
            results.extend(_extract_indemnitors(doc, "prospective"))

        # ── Pull from active_bonds ──
        a_query: dict = {"$or": [{"indemnitor": {"$exists": True}}, {"indemnitors": {"$exists": True}}]}
        if search:
            a_query = {"$and": [a_query, {"$or": [
                {"indemnitor.name": {"$regex": search, "$options": "i"}},
                {"indemnitor_name": {"$regex": search, "$options": "i"}},
                {"indemnitors.name": {"$regex": search, "$options": "i"}},
                {"defendant_name": {"$regex": search, "$options": "i"}},
                {"booking_number": {"$regex": search, "$options": "i"}},
            ]}]}

        seen_bookings = {r["booking_number"] for r in results}
        async for doc in active_bonds.find(a_query).sort("created_at", -1).limit(limit):
            if doc.get("booking_number") in seen_bookings:
                continue
            results.extend(_extract_indemnitors(doc, "active", "bonded"))

        # ── Unlinked standalone indemnitors (saved without a booking#) ──
        # Skip stage filter — unlinked records have no pipeline stage
        if not stage or stage in ("unlinked", "all"):
            u_query: dict = {"status": "unlinked"}
            if search:
                u_query = {"$and": [
                    {"status": "unlinked"},
                    {"$or": [
                        {"name": {"$regex": search, "$options": "i"}},
                        {"firstName": {"$regex": search, "$options": "i"}},
                        {"lastName": {"$regex": search, "$options": "i"}},
                        {"phone": {"$regex": search, "$options": "i"}},
                        {"email": {"$regex": search, "$options": "i"}},
                    ]},
                ]}
            async for doc in indemnitors_coll.find(u_query).sort("updated_at", -1).limit(limit):
                ind_id = str(doc.get("Indemnitor_ID") or doc.get("_id", ""))
                ind_name = doc.get("name") or " ".join(
                    filter(None, [doc.get("firstName", ""), doc.get("lastName", "")])
                ) or ""
                # Synthetic booking key so the existing openDetail(bk) path works
                results.append({
                    "booking_number": f"UNLINKED-{ind_id}",
                    "defendant_name": "(not linked)",
                    "county": "",
                    "bond_amount": 0,
                    "stage": "unlinked",
                    "status": "unlinked",
                    "bond_type": "unlinked",
                    "indemnitor": {k: v for k, v in doc.items() if k != "_id"},
                    "indemnitor_name": ind_name,
                    "indemnitor_phone": doc.get("phone", ""),
                    "indemnitor_email": doc.get("email", ""),
                    "indemnitor_relationship": doc.get("relationship", ""),
                    "indemnitor_role": doc.get("role", "primary"),
                    "indemnitor_id": ind_id,
                    "total_cosigners": 0,
                    "source": "manual_entry",
                    "documents": {},
                    "created_at": _safe_dt(doc.get("created_at", "")),
                    "updated_at": _safe_dt(doc.get("updated_at", doc.get("created_at", ""))),
                })

        results.sort(key=lambda x: str(x.get("updated_at", "")), reverse=True)

        return {"success": True, "indemnitors": results[:limit], "total": len(results)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/indemnitors/by-person")
async def api_indemnitors_by_person(
    search: str = "",
    limit: int = Query(200, ge=1, le=1000),
):
    """Group all indemnitors by phone number so each person shows all bonds they've signed for."""
    try:
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")
        all_bonds = []

        async def _collect(coll, bond_type: str, stage_override: str = ""):
            query: dict = {"$or": [
                {"indemnitor": {"$exists": True, "$ne": {}}},
                {"indemnitors": {"$exists": True, "$ne": []}},
            ]}
            if search:
                query = {"$and": [query, {"$or": [
                    {"indemnitor.name": {"$regex": search, "$options": "i"}},
                    {"indemnitor.firstName": {"$regex": search, "$options": "i"}},
                    {"indemnitor.lastName": {"$regex": search, "$options": "i"}},
                    {"indemnitor.phone": {"$regex": search, "$options": "i"}},
                    {"indemnitor_name": {"$regex": search, "$options": "i"}},
                    {"indemnitors.name": {"$regex": search, "$options": "i"}},
                    {"defendant_name": {"$regex": search, "$options": "i"}},
                    {"booking_number": {"$regex": search, "$options": "i"}},
                ]}]}
            async for doc in coll.find(query).sort("updated_at", -1).limit(limit):
                indemnitors = doc.get("indemnitors", [])
                if not indemnitors and doc.get("indemnitor"):
                    indemnitors = [doc.get("indemnitor", {})]
                for ind in indemnitors:
                    ind_name = ind.get("name") or " ".join(
                        filter(None, [ind.get("firstName", ""), ind.get("lastName", "")])
                    ) or ""
                    phone = ind.get("phone", "").strip()
                    if not ind_name and not phone:
                        continue
                    all_bonds.append({
                        "booking_number": doc.get("booking_number", ""),
                        "defendant_name": doc.get("defendant_name", ""),
                        "county": doc.get("county", ""),
                        "bond_amount": doc.get("bond_amount", 0),
                        "stage": stage_override or doc.get("stage", ""),
                        "status": doc.get("status", ""),
                        "bond_type": bond_type,
                        "charges": doc.get("charges", ""),
                        "created_at": _safe_dt(doc.get("created_at", "")),
                        "updated_at": _safe_dt(doc.get("updated_at", doc.get("created_at", ""))),
                        "indemnitor": ind,
                        "indemnitor_name": ind_name,
                        "indemnitor_phone": phone,
                        "indemnitor_email": ind.get("email", ""),
                        "indemnitor_relationship": ind.get("relationship", ""),
                        "indemnitor_role": ind.get("role", "primary"),
                        "documents": doc.get("documents", {}),
                    })

        await _collect(prospective_bonds, "prospective")
        await _collect(active_bonds, "active", "bonded")

        # Unlinked standalone indemnitors (no bond yet)
        indemnitors_coll = get_collection("indemnitors")
        u_query: dict = {"status": "unlinked"}
        if search:
            u_query = {"$and": [
                {"status": "unlinked"},
                {"$or": [
                    {"name": {"$regex": search, "$options": "i"}},
                    {"firstName": {"$regex": search, "$options": "i"}},
                    {"lastName": {"$regex": search, "$options": "i"}},
                    {"phone": {"$regex": search, "$options": "i"}},
                ]},
            ]}
        async for doc in indemnitors_coll.find(u_query).limit(limit):
            ind_name = doc.get("name") or " ".join(
                filter(None, [doc.get("firstName", ""), doc.get("lastName", "")])
            ) or ""
            phone = (doc.get("phone") or "").strip()
            if not ind_name and not phone:
                continue
            ind_id = str(doc.get("Indemnitor_ID") or doc.get("_id", ""))
            all_bonds.append({
                "booking_number": f"UNLINKED-{ind_id}",
                "defendant_name": "(not linked)",
                "county": "",
                "bond_amount": 0,
                "stage": "unlinked",
                "status": "unlinked",
                "bond_type": "unlinked",
                "charges": "",
                "created_at": _safe_dt(doc.get("created_at", "")),
                "updated_at": _safe_dt(doc.get("updated_at", doc.get("created_at", ""))),
                "indemnitor": {k: v for k, v in doc.items() if k != "_id"},
                "indemnitor_name": ind_name,
                "indemnitor_phone": phone,
                "indemnitor_email": doc.get("email", ""),
                "indemnitor_relationship": doc.get("relationship", ""),
                "indemnitor_role": doc.get("role", "primary"),
                "documents": {},
            })

        # Group by phone (or name if no phone)
        grouped: dict = {}
        for bond in all_bonds:
            phone = bond["indemnitor_phone"]
            name = bond["indemnitor_name"]
            key = phone if phone else f"__name__{name.lower().strip()}"
            if key not in grouped:
                grouped[key] = {
                    "person_key": key,
                    "name": name,
                    "phone": phone,
                    "email": bond["indemnitor_email"],
                    "relationship": bond["indemnitor_relationship"],
                    "bonds": [],
                    "total_bond_value": 0,
                    "active_bonds": 0,
                    "latest_activity": bond.get("updated_at", ""),
                }
            grouped[key]["bonds"].append({
                "booking_number": bond["booking_number"],
                "defendant_name": bond["defendant_name"],
                "county": bond["county"],
                "bond_amount": bond["bond_amount"],
                "stage": bond["stage"],
                "bond_type": bond["bond_type"],
                "charges": bond["charges"],
                "role": bond["indemnitor_role"],
                "created_at": bond["created_at"],
            })
            grouped[key]["total_bond_value"] += bond["bond_amount"]
            if bond["bond_type"] == "active" or bond["stage"] == "bonded":
                grouped[key]["active_bonds"] += 1
            if str(bond.get("updated_at", "")) > str(grouped[key]["latest_activity"]):
                grouped[key]["latest_activity"] = bond.get("updated_at", "")

        persons = sorted(grouped.values(), key=lambda x: (-len(x["bonds"]), -x["total_bond_value"]))
        return {"success": True, "persons": persons, "total": len(persons)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/indemnitors/search-existing")
async def api_indemnitor_search_existing(q: str = ""):
    """Smart search across arrests, prospective_bonds, active_bonds.
    Used to find people already in the system before creating a new record."""
    try:
        if len(q) < 2:
            return {"results": [], "total": 0}

        db = get_db()
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        regex = {"$regex": q, "$options": "i"}
        results = []
        seen_keys: set = set()

        # Search arrests (defendants)
        arrest_query = {"$or": [
            {"full_name": regex}, {"first_name": regex}, {"last_name": regex},
            {"booking_number": regex},
        ]}
        async for doc in db["arrests"].find(arrest_query).limit(20):
            phone = doc.get("phone", "")
            dedup_key = phone or str(doc.get("_id"))
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            results.append({
                "source": "arrest",
                "name": doc.get("full_name", ""),
                "first_name": doc.get("first_name", ""),
                "last_name": doc.get("last_name", ""),
                "phone": phone,
                "email": doc.get("email", ""),
                "address": doc.get("address", ""),
                "dob": doc.get("dob", ""),
                "county": doc.get("county", ""),
                "booking_number": doc.get("booking_number", ""),
                "prior_role": "defendant",
                "prior_id": str(doc.get("_id", "")),
            })

        # Search prospective_bonds indemnitors
        pb_query = {"$or": [
            {"indemnitor.name": regex},
            {"indemnitor.firstName": regex},
            {"indemnitor.lastName": regex},
            {"indemnitor.phone": regex},
            {"defendant_name": regex},
        ]}
        async for doc in prospective_bonds.find(pb_query).limit(20):
            ind = doc.get("indemnitor", {})
            ind_name = ind.get("name") or " ".join(
                filter(None, [ind.get("firstName", ""), ind.get("lastName", "")])
            ) or ""
            phone = ind.get("phone", "")
            dedup_key = phone or ind_name.lower()
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            if ind_name or phone:
                results.append({
                    "source": "prospective_bond",
                    "name": ind_name,
                    "first_name": ind.get("firstName", ""),
                    "last_name": ind.get("lastName", ""),
                    "phone": phone,
                    "email": ind.get("email", ""),
                    "address": ind.get("address", ""),
                    "dob": ind.get("dob", ""),
                    "relationship": ind.get("relationship", ""),
                    "county": doc.get("county", ""),
                    "booking_number": doc.get("booking_number", ""),
                    "prior_role": "indemnitor",
                    "prior_id": str(doc.get("_id", "")),
                })

        # Search active_bonds indemnitors
        ab_query = {"$or": [
            {"indemnitor.name": regex},
            {"indemnitor_name": regex},
            {"defendant_name": regex},
        ]}
        async for doc in active_bonds.find(ab_query).limit(20):
            ind = doc.get("indemnitor", {})
            ind_name = ind.get("name") or doc.get("indemnitor_name", "")
            phone = ind.get("phone") or doc.get("indemnitor_phone", "")
            dedup_key = phone or ind_name.lower()
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            if ind_name or phone:
                results.append({
                    "source": "active_bond",
                    "name": ind_name,
                    "first_name": ind.get("firstName", ""),
                    "last_name": ind.get("lastName", ""),
                    "phone": phone,
                    "email": ind.get("email") or doc.get("indemnitor_email", ""),
                    "address": ind.get("address", ""),
                    "dob": ind.get("dob", ""),
                    "relationship": ind.get("relationship") or doc.get("indemnitor_relationship", ""),
                    "county": doc.get("county", ""),
                    "booking_number": doc.get("booking_number", ""),
                    "prior_role": "indemnitor",
                    "prior_id": str(doc.get("_id", "")),
                })

        # Search standalone unlinked indemnitors
        indemnitors_coll = get_collection("indemnitors")
        unlinked_query = {
            "status": "unlinked",
            "$or": [
                {"name": regex},
                {"firstName": regex},
                {"lastName": regex},
                {"phone": regex},
            ],
        }
        async for doc in indemnitors_coll.find(unlinked_query).limit(20):
            ind_name = doc.get("name") or " ".join(
                filter(None, [doc.get("firstName", ""), doc.get("lastName", "")])
            ) or ""
            phone = doc.get("phone", "")
            dedup_key = phone or ind_name.lower()
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            if ind_name or phone:
                ind_id = str(doc.get("Indemnitor_ID") or doc.get("_id", ""))
                results.append({
                    "source": "unlinked_indemnitor",
                    "name": ind_name,
                    "first_name": doc.get("firstName", ""),
                    "last_name": doc.get("lastName", ""),
                    "phone": phone,
                    "email": doc.get("email", ""),
                    "address": doc.get("address", ""),
                    "dob": doc.get("dob", ""),
                    "relationship": doc.get("relationship", ""),
                    "county": "",
                    "booking_number": f"UNLINKED-{ind_id}",
                    "prior_role": "indemnitor",
                    "prior_id": ind_id,
                })

        return {"success": True, "results": results[:30], "total": len(results)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/indemnitors/create")
async def api_indemnitor_create(request: Request):
    """Create or update an indemnitor profile and link to a bond.
    Deduplicates by phone number — if phone exists on bond, updates existing record."""
    try:
        data = await request.json()
        now = datetime.now(timezone.utc)
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        first_name = (data.get("firstName") or "").strip()
        last_name = (data.get("lastName") or "").strip()
        phone = (data.get("phone") or "").strip()
        email = (data.get("email") or "").strip()
        full_name = f"{first_name} {last_name}".strip() or data.get("name", "").strip()

        if not full_name and not phone:
            return JSONResponse({"error": "Name or phone required"}, status_code=400)

        profile = {
            "name": full_name,
            "firstName": first_name,
            "lastName": last_name,
            "phone": phone,
            "email": email,
            "address": data.get("address", ""),
            "city": data.get("city", ""),
            "state": data.get("state", "FL"),
            "zip": data.get("zip", ""),
            "dob": data.get("dob", ""),
            "ssn_last4": data.get("ssn_last4", ""),
            "relationship": data.get("relationship", ""),
            "employer": data.get("employer", ""),
            "occupation": data.get("occupation", ""),
            "dl_number": data.get("dl_number", ""),
            "dl_state": data.get("dl_state", "FL"),
            "prior_defendant_id": data.get("prior_defendant_id", ""),
            "prior_role": data.get("prior_role", ""),
            "role": data.get("role", "primary"),
            "reference1_name": data.get("reference1_name", ""),
            "reference1_phone": data.get("reference1_phone", ""),
            "reference1_relationship": data.get("reference1_relationship", ""),
            "reference2_name": data.get("reference2_name", ""),
            "reference2_phone": data.get("reference2_phone", ""),
            "reference2_relationship": data.get("reference2_relationship", ""),
            "reference3_name": data.get("reference3_name", ""),
            "reference3_phone": data.get("reference3_phone", ""),
            "reference3_relationship": data.get("reference3_relationship", ""),
            "updated_at": now.isoformat(),
        }

        booking_number = data.get("booking_number", "").strip()

        # If no booking number, save as an unlinked indemnitor for later matching
        if not booking_number:
            indemnitors_coll = get_collection("indemnitors")
            profile["created_at"] = now.isoformat()
            profile["linked_bonds"] = []
            profile["status"] = "unlinked"
            # Dedup by phone if available (same person re-entered)
            if phone:
                existing = await indemnitors_coll.find_one({"phone": phone, "status": "unlinked"})
                if existing:
                    await indemnitors_coll.update_one(
                        {"_id": existing["_id"]},
                        {"$set": {**profile, "updated_at": now.isoformat()}},
                    )
                    ind_id = str(existing.get("Indemnitor_ID") or existing["_id"])
                    return {
                        "success": True,
                        "action": "updated_existing",
                        "indemnitor_id": ind_id,
                        "booking_number": f"UNLINKED-{ind_id}",
                        "linked": False,
                    }
            profile["Indemnitor_ID"] = str(uuid.uuid4())
            result = await indemnitors_coll.insert_one(profile)
            ind_id = profile["Indemnitor_ID"]
            return {
                "success": True,
                "action": "created",
                "indemnitor_id": ind_id,
                "booking_number": f"UNLINKED-{ind_id}",
                "linked": False,
                "message": "Indemnitor saved. Open the card and enter a Booking # to link.",
            }

        # Find the bond (prospective or active)
        doc = await prospective_bonds.find_one({"booking_number": booking_number})
        collection = prospective_bonds
        bond_type = "prospective"
        if not doc:
            doc = await active_bonds.find_one({"booking_number": booking_number})
            collection = active_bonds
            bond_type = "active"

        if not doc:
            from dashboard.extensions import get_db
            db = get_db()
            arrest = await db.arrests.find_one({"booking_number": booking_number})
            if arrest:
                # Create a new prospective bond from the arrest lead
                # stage must be a VALID_STAGES value used by the In Progress pipeline
                doc = {
                    "booking_number": booking_number,
                    "county": arrest.get("county", ""),
                    "defendant_name": arrest.get("full_name", ""),
                    "bond_amount": arrest.get("total_bond_amount") or arrest.get("bond_amount", 0),
                    "charges": arrest.get("charges", []),
                    "status": "active",
                    "stage": "contacted",
                    "source": "dashboard_manual",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "timeline": [{
                        "timestamp": now.isoformat(),
                        "event": "bond_started",
                        "detail": "Started prospective bond from arrest lead via indemnitor creation",
                        "agent": "System",
                    }],
                    "indemnitors": [],
                    "documents": {},
                    "kyc_uploads": [],
                }
                await prospective_bonds.insert_one(doc)
                collection = prospective_bonds
                bond_type = "prospective"
            else:
                # No existing bond or arrest — create a stub prospective bond
                # so the indemnitor can be linked now and matched to a defendant later.
                doc = {
                    "booking_number": booking_number,
                    "county": "",
                    "defendant_name": "",
                    "bond_amount": 0,
                    "charges": [],
                    "status": "active",
                    "stage": "contacted",
                    "source": "dashboard_manual",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "timeline": [{
                        "timestamp": now.isoformat(),
                        "event": "bond_started",
                        "detail": (
                            f"Stub bond created via indemnitor intake — "
                            f"booking #{booking_number} not yet in system"
                        ),
                        "agent": "System",
                    }],
                    "indemnitors": [],
                    "documents": {},
                    "kyc_uploads": [],
                }
                await prospective_bonds.insert_one(doc)
                collection = prospective_bonds
                bond_type = "prospective"

        # Multi-cosigner: migrate from single indemnitor to indemnitors array
        existing_indemnitors = doc.get("indemnitors", [])
        if not existing_indemnitors and doc.get("indemnitor"):
            old_ind = doc.get("indemnitor", {})
            old_name = old_ind.get("name") or " ".join(
                filter(None, [old_ind.get("firstName", ""), old_ind.get("lastName", "")])
            ) or ""
            if old_name or old_ind.get("phone"):
                old_ind["role"] = old_ind.get("role", "primary")
                existing_indemnitors = [old_ind]

        # Dedup check: same phone on same bond?
        if phone:
            for existing in existing_indemnitors:
                if existing.get("phone") == phone:
                    existing.update(profile)
                    await collection.update_one(
                        {"booking_number": booking_number},
                        {"$set": {
                            "indemnitors": existing_indemnitors,
                            "indemnitor": existing_indemnitors[0] if existing_indemnitors else {},
                            "indemnitor_name": existing_indemnitors[0].get("name", "") if existing_indemnitors else "",
                            "updated_at": now,
                        }}
                    )
                    return {
                        "success": True,
                        "action": "updated_existing",
                        "indemnitors": existing_indemnitors,
                        "booking_number": booking_number,
                    }

        # Add as new cosigner
        if len(existing_indemnitors) >= 5:
            return JSONResponse({"error": "Maximum 5 indemnitors per bond"}, status_code=400)

        if not profile.get("role") or profile["role"] == "primary":
            if existing_indemnitors:
                profile["role"] = f"cosigner_{len(existing_indemnitors)}"
            else:
                profile["role"] = "primary"

        existing_indemnitors.append(profile)
        primary = existing_indemnitors[0]

        await collection.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "indemnitors": existing_indemnitors,
                "indemnitor": primary,
                "indemnitor_name": primary.get("name", ""),
                "indemnitor_phone": primary.get("phone", ""),
                "indemnitor_email": primary.get("email", ""),
                "indemnitor_relationship": primary.get("relationship", ""),
                "updated_at": now,
            }},
        )

        if collection == prospective_bonds:
            await collection.update_one(
                {"booking_number": booking_number},
                {"$push": {"timeline": {
                    "timestamp": now.isoformat(),
                    "event": "indemnitor_added",
                    "detail": f"{'Updated' if len(existing_indemnitors) == 1 else 'Added cosigner'}: {full_name} ({profile['role']})",
                    "agent": data.get("agent", "Dashboard"),
                }}}
            )

        # If this person existed as an unlinked standalone record, mark them linked
        if phone:
            indemnitors_coll = get_collection("indemnitors")
            await indemnitors_coll.update_many(
                {"phone": phone, "status": "unlinked"},
                {"$set": {
                    "status": "linked",
                    "updated_at": now.isoformat(),
                }, "$addToSet": {"linked_bonds": booking_number}},
            )

        return {
            "success": True,
            "action": "created",
            "indemnitors": existing_indemnitors,
            "booking_number": booking_number,
            "bond_type": bond_type,
            "linked": True,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/indemnitors/link")
async def api_indemnitor_link(request: Request):
    """Link a previously unlinked standalone indemnitor to a booking/bond.

    Body: {indemnitor_id: str, booking_number: str}
    Reuses the create path so stub-bond + multi-cosigner logic stays centralized.
    """
    try:
        data = await request.json() or {}
        indemnitor_id = (data.get("indemnitor_id") or "").strip()
        booking_number = (data.get("booking_number") or "").strip()
        if not indemnitor_id:
            return JSONResponse({"error": "indemnitor_id is required"}, status_code=400)
        if not booking_number:
            return JSONResponse({"error": "booking_number is required"}, status_code=400)
        if booking_number.startswith("UNLINKED-"):
            return JSONResponse({"error": "Provide a real booking number to link to"}, status_code=400)

        indemnitors_coll = get_collection("indemnitors")
        from bson import ObjectId

        ind_doc = None
        # Prefer Indemnitor_ID UUID, fall back to Mongo _id
        ind_doc = await indemnitors_coll.find_one({"Indemnitor_ID": indemnitor_id})
        if not ind_doc:
            try:
                ind_doc = await indemnitors_coll.find_one({"_id": ObjectId(indemnitor_id)})
            except Exception:
                ind_doc = None
        if not ind_doc:
            return JSONResponse({"error": "Unlinked indemnitor not found"}, status_code=404)

        # Build create payload from stored profile + target booking
        create_body = {
            "booking_number": booking_number,
            "firstName": ind_doc.get("firstName", ""),
            "lastName": ind_doc.get("lastName", ""),
            "name": ind_doc.get("name", ""),
            "phone": ind_doc.get("phone", ""),
            "email": ind_doc.get("email", ""),
            "address": ind_doc.get("address", ""),
            "city": ind_doc.get("city", ""),
            "state": ind_doc.get("state", "FL"),
            "zip": ind_doc.get("zip", ""),
            "dob": ind_doc.get("dob", ""),
            "ssn_last4": ind_doc.get("ssn_last4", ""),
            "relationship": ind_doc.get("relationship", ""),
            "employer": ind_doc.get("employer", ""),
            "occupation": ind_doc.get("occupation", ""),
            "dl_number": ind_doc.get("dl_number", ""),
            "dl_state": ind_doc.get("dl_state", "FL"),
            "role": ind_doc.get("role", "primary"),
            "reference1_name": ind_doc.get("reference1_name", ""),
            "reference1_phone": ind_doc.get("reference1_phone", ""),
            "reference1_relationship": ind_doc.get("reference1_relationship", ""),
            "reference2_name": ind_doc.get("reference2_name", ""),
            "reference2_phone": ind_doc.get("reference2_phone", ""),
            "reference2_relationship": ind_doc.get("reference2_relationship", ""),
            "reference3_name": ind_doc.get("reference3_name", ""),
            "reference3_phone": ind_doc.get("reference3_phone", ""),
            "reference3_relationship": ind_doc.get("reference3_relationship", ""),
            "agent": data.get("agent", "Dashboard"),
        }

        # Invoke create logic by constructing a Request-like path: call core inline
        # by reusing the create endpoint via a synthetic internal call is heavy;
        # instead duplicate the attach steps through a second POST-style payload.
        class _BodyRequest:
            async def json(self):
                return create_body

        result = await api_indemnitor_create(_BodyRequest())
        if isinstance(result, JSONResponse):
            return result

        # Ensure the standalone record is marked linked even if phone was empty
        now = datetime.now(timezone.utc)
        await indemnitors_coll.update_one(
            {"_id": ind_doc["_id"]},
            {"$set": {
                "status": "linked",
                "updated_at": now.isoformat(),
            }, "$addToSet": {"linked_bonds": booking_number}},
        )
        if isinstance(result, dict):
            result["linked"] = True
            result["indemnitor_id"] = str(ind_doc.get("Indemnitor_ID") or ind_doc["_id"])
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/indemnitors/{booking_number}/documents")
async def api_indemnitor_documents(booking_number: str):
    """Get document checklist for an indemnitor's bond."""
    try:
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        doc = await prospective_bonds.find_one({"booking_number": booking_number})
        bond_type = "prospective"
        if not doc:
            doc = await active_bonds.find_one({"booking_number": booking_number})
            bond_type = "active"
        if not doc:
            return JSONResponse({"error": "Bond not found"}, status_code=404)

        saved_docs = doc.get("documents", {})
        surety = doc.get("surety", "osi")

        checklist = {}
        for section, items in DOCUMENT_CHECKLIST.items():
            checklist[section] = []
            for item in items:
                checklist[section].append({
                    **item,
                    "signed": saved_docs.get(item["key"], {}).get("signed", False),
                    "signed_at": saved_docs.get(item["key"], {}).get("signed_at", ""),
                    "signnow_id": saved_docs.get(item["key"], {}).get("signnow_id", ""),
                })

        return {
            "success": True,
            "booking_number": booking_number,
            "bond_type": bond_type,
            "surety": surety,
            "checklist": checklist,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/indemnitors/{booking_number}/documents")
async def api_indemnitor_documents_update(booking_number: str, request: Request):
    """Toggle document signed status."""
    try:
        data = await request.json()
        doc_key = data.get("doc_key", "")
        signed = data.get("signed", False)
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        if not doc_key:
            return JSONResponse({"error": "doc_key required"}, status_code=400)

        doc = await prospective_bonds.find_one({"booking_number": booking_number})
        collection = prospective_bonds
        if not doc:
            doc = await active_bonds.find_one({"booking_number": booking_number})
            collection = active_bonds
        if not doc:
            return JSONResponse({"error": "Bond not found"}, status_code=404)

        now = datetime.now(timezone.utc)
        await collection.update_one(
            {"booking_number": booking_number},
            {"$set": {
                f"documents.{doc_key}.signed": signed,
                f"documents.{doc_key}.signed_at": now.isoformat() if signed else "",
                "updated_at": now,
            }},
        )
        return {"success": True, "doc_key": doc_key, "signed": signed}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def _indemnitor_upload_target(booking_number: str):
    """Resolve bond or unlinked indemnitor document for KYC storage.

    Returns (collection, query_filter, doc) or (None, None, None).
    """
    unlinked = await _load_unlinked_indemnitor(booking_number)
    if unlinked is not None:
        return get_collection("indemnitors"), {"_id": unlinked["_id"]}, unlinked

    prospective_bonds = get_collection("prospective_bonds")
    active_bonds = get_collection("active_bonds")
    doc = await prospective_bonds.find_one({"booking_number": booking_number})
    if doc:
        return prospective_bonds, {"booking_number": booking_number}, doc
    doc = await active_bonds.find_one({"booking_number": booking_number})
    if doc:
        return active_bonds, {"booking_number": booking_number}, doc
    return None, None, None


@router.get("/indemnitors/{booking_number}/uploads")
async def api_indemnitor_uploads_list(booking_number: str):
    """List KYC uploads + structured id_photos slots for an indemnitor."""
    try:
        coll, query, doc = await _indemnitor_upload_target(booking_number)
        if not doc:
            return JSONResponse({"error": "Bond or indemnitor not found"}, status_code=404)

        uploads = doc.get("kyc_uploads", [])
        id_photos = doc.get("id_photos") or slot_map_from_uploads(uploads)
        return {
            "success": True,
            "booking_number": booking_number,
            "uploads": uploads,
            "id_photos": id_photos,
            "slots": ID_PHOTO_SLOTS,
            "total": len(uploads),
            "doc_types": KYC_DOC_TYPES,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/indemnitors/{booking_number}/uploads")
async def api_indemnitor_upload(
    booking_number: str,
    file: UploadFile = File(...),
    doc_type: str = Form("other"),
):
    """Upload DL/ID front, back, selfie, or other KYC for an indemnitor."""
    try:
        coll, query, doc = await _indemnitor_upload_target(booking_number)
        if not doc:
            return JSONResponse({"error": "Bond or indemnitor not found"}, status_code=404)

        if not file.filename:
            return JSONResponse({"error": "Empty filename"}, status_code=400)

        contents = await file.read()
        if not contents:
            return JSONResponse({"error": "Empty file"}, status_code=400)

        try:
            upload_meta = save_upload_file(
                entity_key=booking_number,
                doc_type=doc_type or "other",
                original_filename=file.filename,
                contents=contents,
            )
        except ValueError as ve:
            return JSONResponse({"error": str(ve)}, status_code=400)

        now = datetime.now(timezone.utc)
        id_photos = merge_id_photos_field(doc.get("id_photos"), upload_meta)
        await coll.update_one(
            query,
            {
                "$push": {"kyc_uploads": upload_meta},
                "$set": {
                    "id_photos": id_photos,
                    "updated_at": now.isoformat() if isinstance(now, datetime) else now,
                },
            },
        )

        return JSONResponse({
            "success": True,
            "file_id": upload_meta["file_id"],
            "filename": upload_meta["saved_as"],
            "url": upload_meta["url"],
            "doc_type": upload_meta["doc_type"],
            "doc_type_label": upload_meta["doc_type_label"],
            "size_bytes": upload_meta["size_bytes"],
            "id_photos": id_photos,
        }, status_code=201)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/indemnitors/{booking_number}/uploads/{file_id}")
async def api_indemnitor_upload_delete(booking_number: str, file_id: str):
    """Delete a specific uploaded KYC document."""
    try:
        coll, query, doc = await _indemnitor_upload_target(booking_number)
        if not doc:
            return JSONResponse({"error": "Bond or indemnitor not found"}, status_code=404)

        uploads = doc.get("kyc_uploads", [])
        target = next((u for u in uploads if u.get("file_id") == file_id), None)
        if not target:
            return JSONResponse({"error": "Upload not found"}, status_code=404)

        delete_upload_file(target)
        remaining = [u for u in uploads if u.get("file_id") != file_id]
        id_photos = slot_map_from_uploads(remaining)
        # drop None slots for cleaner storage
        id_photos_clean = {k: v for k, v in id_photos.items() if v}

        now = datetime.now(timezone.utc)
        await coll.update_one(
            query,
            {"$set": {
                "kyc_uploads": remaining,
                "id_photos": id_photos_clean,
                "updated_at": now.isoformat(),
            }},
        )
        return {"success": True, "deleted": file_id}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/indemnitors/{booking_number}/payment-link")
async def api_indemnitor_payment_link(booking_number: str):
    """Generate or return a SwipeSimple payment link for this bond."""
    try:
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        doc = await prospective_bonds.find_one({"booking_number": booking_number})
        collection = prospective_bonds
        if not doc:
            doc = await active_bonds.find_one({"booking_number": booking_number})
            collection = active_bonds
        if not doc:
            return JSONResponse({"error": "Bond not found"}, status_code=404)

        ind = doc.get("indemnitor", {})
        ind_name = ind.get("name") or " ".join(
            filter(None, [ind.get("firstName", ""), ind.get("lastName", "")])
        ) or "Indemnitor"
        bond_amount = doc.get("bond_amount", 0)
        premium = round(float(bond_amount) * 0.10, 2) if bond_amount else 0

        from urllib.parse import urlencode
        base_url = os.environ.get("SWIPESIMPLE_URL", "https://shamrockbailbonds.biz/payment")
        params = {
            "amount": str(premium),
            "name": ind_name,
            "booking": booking_number,
            "county": doc.get("county", ""),
        }
        payment_url = f"{base_url}?{urlencode(params)}"

        now = datetime.now(timezone.utc)
        await collection.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "payment_link": payment_url,
                "payment_premium": premium,
                "updated_at": now,
            }},
        )

        return {
            "success": True,
            "payment_link": payment_url,
            "premium": premium,
            "bond_amount": bond_amount,
            "indemnitor_name": ind_name,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/indemnitors/{booking_number}/remove")
async def api_indemnitor_remove(booking_number: str, request: Request):
    """Remove a cosigner from a bond by phone number (cannot remove last indemnitor)."""
    try:
        data = await request.json()
        phone = (data.get("phone") or "").strip()
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        if not phone:
            return JSONResponse({"error": "phone required to identify indemnitor"}, status_code=400)

        doc = await prospective_bonds.find_one({"booking_number": booking_number})
        collection = prospective_bonds
        if not doc:
            doc = await active_bonds.find_one({"booking_number": booking_number})
            collection = active_bonds
        if not doc:
            return JSONResponse({"error": "Bond not found"}, status_code=404)

        existing = doc.get("indemnitors", [])
        if len(existing) <= 1:
            return JSONResponse({"error": "Cannot remove the last indemnitor. Each bond must have at least one."}, status_code=400)

        updated = [i for i in existing if i.get("phone") != phone]
        if len(updated) == len(existing):
            return JSONResponse({"error": "Indemnitor not found with that phone number"}, status_code=404)

        for idx, ind in enumerate(updated):
            ind["role"] = "primary" if idx == 0 else f"cosigner_{idx}"

        now = datetime.now(timezone.utc)
        primary = updated[0]
        await collection.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "indemnitors": updated,
                "indemnitor": primary,
                "indemnitor_name": primary.get("name", ""),
                "indemnitor_phone": primary.get("phone", ""),
                "updated_at": now,
            }},
        )
        return {"success": True, "indemnitors": updated, "booking_number": booking_number}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def _load_unlinked_indemnitor(booking_number: str):
    """Resolve UNLINKED-<id> synthetic keys to a standalone indemnitors document."""
    if not booking_number.startswith("UNLINKED-"):
        return None
    ind_id = booking_number[len("UNLINKED-"):]
    if not ind_id:
        return None
    indemnitors_coll = get_collection("indemnitors")
    from bson import ObjectId

    doc = await indemnitors_coll.find_one({"Indemnitor_ID": ind_id})
    if not doc:
        try:
            doc = await indemnitors_coll.find_one({"_id": ObjectId(ind_id)})
        except Exception:
            doc = None
    return doc


@router.get("/indemnitors/{booking_number}")
async def api_indemnitor_detail(booking_number: str):
    """Get full indemnitor profile for a booking number (or unlinked synthetic key)."""
    try:
        # Standalone unlinked record
        unlinked = await _load_unlinked_indemnitor(booking_number)
        if unlinked is not None:
            ind = {k: v for k, v in unlinked.items() if k != "_id"}
            ind_id = str(unlinked.get("Indemnitor_ID") or unlinked.get("_id", ""))
            return {
                "success": True,
                "booking_number": booking_number,
                "bond_type": "unlinked",
                "defendant_name": "(not linked)",
                "county": "",
                "bond_amount": 0,
                "stage": "unlinked",
                "charges": "",
                "surety": "osi",
                "indemnitor": ind,
                "indemnitors": [ind],
                "indemnitor_id": ind_id,
                "documents": {},
                "communication_log": [],
                "timeline": [],
            }

        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        doc = await prospective_bonds.find_one({"booking_number": booking_number})
        bond_type = "prospective"
        if not doc:
            doc = await active_bonds.find_one({"booking_number": booking_number})
            bond_type = "active"
        if not doc:
            return JSONResponse({"error": "Bond not found"}, status_code=404)

        ind = doc.get("indemnitor", {})
        return {
            "success": True,
            "booking_number": booking_number,
            "bond_type": bond_type,
            "defendant_name": doc.get("defendant_name", ""),
            "county": doc.get("county", ""),
            "bond_amount": doc.get("bond_amount", 0),
            "stage": doc.get("stage", ""),
            "charges": doc.get("charges", ""),
            "surety": doc.get("surety", "osi"),
            "indemnitor": ind,
            "indemnitors": doc.get("indemnitors", [ind] if ind else []),
            "documents": doc.get("documents", {}),
            "communication_log": doc.get("communication_log", []),
            "timeline": [
                {**e, "timestamp": _safe_dt(e.get("timestamp", ""))}
                for e in doc.get("timeline", [])
            ],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/indemnitors/{booking_number}")
async def api_indemnitor_update(booking_number: str, request: Request):
    """Update full indemnitor profile (searches both collections)."""
    try:
        data = await request.json()
        now = datetime.now(timezone.utc)

        # Standalone unlinked record
        unlinked = await _load_unlinked_indemnitor(booking_number)
        if unlinked is not None:
            indemnitors_coll = get_collection("indemnitors")
            updates = {}
            for field in INDEMNITOR_FIELDS:
                if data.get(field) is not None:
                    updates[field] = data[field]
            # Common FE field aliases
            for src, dest in (
                ("firstName", "firstName"),
                ("lastName", "lastName"),
                ("phone", "phone"),
                ("email", "email"),
                ("address", "address"),
                ("relationship", "relationship"),
                ("employer", "employer"),
                ("dob", "dob"),
                ("ssn_last4", "ssn_last4"),
                ("dl_number", "dl_number"),
                ("dl_state", "dl_state"),
            ):
                if data.get(src) is not None:
                    updates[dest] = data[src]
            if "firstName" in updates or "lastName" in updates:
                fn = updates.get("firstName", unlinked.get("firstName", ""))
                ln = updates.get("lastName", unlinked.get("lastName", ""))
                updates["name"] = f"{fn} {ln}".strip() or updates.get("name", unlinked.get("name", ""))
            updates["updated_at"] = now.isoformat()
            await indemnitors_coll.update_one({"_id": unlinked["_id"]}, {"$set": updates})
            return {"success": True, "booking_number": booking_number, "linked": False}

        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        doc = await prospective_bonds.find_one({"booking_number": booking_number})
        collection = prospective_bonds
        if not doc:
            doc = await active_bonds.find_one({"booking_number": booking_number})
            collection = active_bonds
        if not doc:
            return JSONResponse({"error": "Bond not found"}, status_code=404)

        indemnitor = doc.get("indemnitor", {})
        for field in INDEMNITOR_FIELDS:
            if data.get(field) is not None:
                indemnitor[field] = data[field]

        ind_name = indemnitor.get("name") or " ".join(
            filter(None, [indemnitor.get("firstName", ""), indemnitor.get("lastName", "")])
        )

        update_ops: dict = {"$set": {"indemnitor": indemnitor, "updated_at": now}}
        if collection == prospective_bonds:
            update_ops["$push"] = {"timeline": {
                "timestamp": now.isoformat(),
                "event": "indemnitor_profile_updated",
                "detail": f"Full profile updated: {ind_name}"[:200],
                "agent": data.get("agent", "Dashboard"),
            }}

        await collection.update_one({"booking_number": booking_number}, update_ops)
        return {"success": True, "indemnitor": indemnitor}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Prospective-bond single-indemnitor update (legacy compat) ─────────────────

@router.patch("/prospective-bonds/{booking_number}/indemnitor")
async def api_prospective_update_indemnitor(booking_number: str, request: Request):
    """Update the indemnitor/cosigner info on a prospective bond (full field set)."""
    try:
        data = await request.json()
        prospective_bonds = get_collection("prospective_bonds")

        existing = await prospective_bonds.find_one({"booking_number": booking_number})
        if not existing:
            return JSONResponse({"error": "Prospective bond not found"}, status_code=404)

        now = datetime.now(timezone.utc)
        indemnitor = existing.get("indemnitor", {})
        for field in INDEMNITOR_FIELDS:
            if data.get(field) is not None:
                indemnitor[field] = data[field]

        await prospective_bonds.update_one(
            {"booking_number": booking_number},
            {
                "$set": {"indemnitor": indemnitor, "updated_at": now},
                "$push": {"timeline": {
                    "timestamp": now.isoformat(),
                    "event": "indemnitor_updated",
                    "detail": f"Indemnitor info updated: {indemnitor.get('name', '') or ' '.join(filter(None, [indemnitor.get('firstName', ''), indemnitor.get('lastName', '')]))}"[:200],
                    "agent": data.get("agent", "Dashboard"),
                }},
            },
        )
        return {"success": True, "indemnitor": indemnitor}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Serve uploaded KYC files ──────────────────────────────────────────────────

@router.get("/uploads/{booking_number}/{filename}")
async def serve_upload(booking_number: str, filename: str):
    """Serve uploaded KYC files for preview in dashboard.

    Security: rejects path traversal attempts (.. or /) in filename.
    """
    from fastapi.responses import FileResponse

    # Prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)

    upload_path = UPLOAD_DIR / booking_number / filename
    if not upload_path.exists() or not upload_path.is_file():
        return JSONResponse({"error": "Not found"}, status_code=404)

    # Resolve to ensure we're still inside UPLOAD_DIR
    try:
        upload_path.resolve().relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        return JSONResponse({"error": "Access denied"}, status_code=403)

    return FileResponse(str(upload_path))
