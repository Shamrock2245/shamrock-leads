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

ALLOWED_UPLOAD_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "heic"}

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

KYC_DOC_TYPES = {
    "govt_id_front": "Government ID (Front)",
    "govt_id_back": "Government ID (Back)",
    "selfie": "Selfie / Photo ID Verification",
    "pay_stub": "Pay Stub / Proof of Income",
    "utility_bill": "Utility Bill / Proof of Address",
    "other": "Other Supporting Document",
}


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
    """List all indemnitors across prospective_bonds AND active_bonds."""
    try:
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")
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
        if not booking_number:
            return JSONResponse({"error": "booking_number required to link indemnitor to a bond"}, status_code=400)

        # Find the bond (prospective or active)
        doc = await prospective_bonds.find_one({"booking_number": booking_number})
        collection = prospective_bonds
        bond_type = "prospective"
        if not doc:
            doc = await active_bonds.find_one({"booking_number": booking_number})
            collection = active_bonds
            bond_type = "active"
        if not doc:
            return JSONResponse({"error": f"Bond {booking_number} not found"}, status_code=404)

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

        return {
            "success": True,
            "action": "created",
            "indemnitors": existing_indemnitors,
            "booking_number": booking_number,
            "bond_type": bond_type,
        }
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


@router.get("/indemnitors/{booking_number}/uploads")
async def api_indemnitor_uploads_list(booking_number: str):
    """List all uploaded KYC documents for an indemnitor's bond."""
    try:
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        doc = await prospective_bonds.find_one({"booking_number": booking_number})
        if not doc:
            doc = await active_bonds.find_one({"booking_number": booking_number})
        if not doc:
            return JSONResponse({"error": "Bond not found"}, status_code=404)

        uploads = doc.get("kyc_uploads", [])
        return {
            "success": True,
            "booking_number": booking_number,
            "uploads": uploads,
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
    """Upload a KYC document/image for an indemnitor's bond."""
    try:
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        if not file.filename:
            return JSONResponse({"error": "Empty filename"}, status_code=400)

        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            return JSONResponse({"error": f"File type .{ext} not allowed. Use: {', '.join(ALLOWED_UPLOAD_EXTENSIONS)}"}, status_code=400)

        if doc_type not in KYC_DOC_TYPES:
            doc_type = "other"

        booking_dir = UPLOAD_DIR / booking_number
        booking_dir.mkdir(exist_ok=True)

        file_id = str(uuid.uuid4())[:8]
        safe_name = f"{doc_type}_{file_id}.{ext}"
        file_path = booking_dir / safe_name

        contents = await file.read()
        file_path.write_bytes(contents)
        file_size = file_path.stat().st_size

        now = datetime.now(timezone.utc)
        upload_meta = {
            "file_id": file_id,
            "filename": file.filename,
            "saved_as": safe_name,
            "doc_type": doc_type,
            "doc_type_label": KYC_DOC_TYPES.get(doc_type, "Other"),
            "extension": ext,
            "size_bytes": file_size,
            "uploaded_at": now.isoformat(),
            "path": str(file_path),
        }

        for coll in [prospective_bonds, active_bonds]:
            result = await coll.update_one(
                {"booking_number": booking_number},
                {"$push": {"kyc_uploads": upload_meta}, "$set": {"updated_at": now}},
            )
            if result.matched_count > 0:
                break

        return JSONResponse({
            "success": True,
            "file_id": file_id,
            "filename": safe_name,
            "doc_type": doc_type,
            "doc_type_label": KYC_DOC_TYPES.get(doc_type, "Other"),
            "size_bytes": file_size,
        }, status_code=201)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/indemnitors/{booking_number}/uploads/{file_id}")
async def api_indemnitor_upload_delete(booking_number: str, file_id: str):
    """Delete a specific uploaded KYC document."""
    try:
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        for coll in [prospective_bonds, active_bonds]:
            doc = await coll.find_one({"booking_number": booking_number})
            if doc:
                uploads = doc.get("kyc_uploads", [])
                target = next((u for u in uploads if u.get("file_id") == file_id), None)
                if target:
                    file_path = Path(target.get("path", ""))
                    if file_path.exists():
                        file_path.unlink()
                    await coll.update_one(
                        {"booking_number": booking_number},
                        {"$pull": {"kyc_uploads": {"file_id": file_id}}},
                    )
                    return {"success": True, "deleted": file_id}
                break

        return JSONResponse({"error": "Upload not found"}, status_code=404)
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


@router.get("/indemnitors/{booking_number}")
async def api_indemnitor_detail(booking_number: str):
    """Get full indemnitor profile for a booking number."""
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
