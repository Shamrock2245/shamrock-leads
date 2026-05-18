"""
ShamrockLeads — Indemnitor Management API Blueprint (Quart)
Unified indemnitor tab across prospective_bonds AND active_bonds.

Endpoints:
  GET    /api/indemnitors                              — List all (by-bond view)
  GET    /api/indemnitors/by-person                   — Group by phone (by-person view)
  GET    /api/indemnitors/search-existing             — Smart cross-entity search
  POST   /api/indemnitors/create                      — Create / link indemnitor to bond
  GET    /api/indemnitors/<booking_number>            — Full indemnitor detail
  PATCH  /api/indemnitors/<booking_number>            — Update indemnitor profile
  GET    /api/indemnitors/<booking_number>/documents  — Document checklist
  PATCH  /api/indemnitors/<booking_number>/documents  — Toggle document signed
  GET    /api/indemnitors/<booking_number>/uploads    — List KYC uploads
  POST   /api/indemnitors/<booking_number>/uploads    — Upload KYC file
  DELETE /api/indemnitors/<booking_number>/uploads/<file_id> — Delete KYC file
  POST   /api/indemnitors/<booking_number>/payment-link — Generate payment link
  POST   /api/indemnitors/<booking_number>/remove     — Remove a cosigner
  PATCH  /api/prospective-bonds/<booking_number>/indemnitor — Legacy single update
"""
from __future__ import annotations

import os
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path

from quart import Blueprint, request, jsonify, send_from_directory
from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)
indemnitors_bp = Blueprint("indemnitors", __name__)

# ── Constants ─────────────────────────────────────────────────────────────────

INDEMNITOR_FIELDS = [
    "name", "firstName", "middleName", "lastName", "relationship",
    "dob", "ssn", "dl", "dlState", "sex", "race", "height", "weight",
    "phone", "email", "callback_phone",
    "address", "city", "state", "zip",
    "employer", "employerPhone", "employerAddress", "employerCity",
    "employerState", "supervisor", "supervisorPhone", "occupation",
    "monthlyIncome",
    "spouseName", "spousePhone", "spouseEmployer", "spouseEmployerPhone",
    "spouseAddress", "spouseDob", "spouseRelationship",
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
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val) if val else ""


def _ind_name(ind: dict) -> str:
    return ind.get("name") or " ".join(
        filter(None, [ind.get("firstName", ""), ind.get("lastName", "")])
    ) or ""


def _extract_indemnitors(doc: dict, bond_type: str, stage: str = "") -> list:
    items = []
    indemnitors = doc.get("indemnitors", [])
    if not indemnitors and doc.get("indemnitor"):
        indemnitors = [doc.get("indemnitor", {})]
    for ind in indemnitors:
        name = _ind_name(ind)
        if not name and not ind.get("phone") and not ind.get("email"):
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
            "indemnitor_name": name,
            "indemnitor_phone": ind.get("phone", ""),
            "indemnitor_email": ind.get("email", ""),
            "indemnitor_relationship": ind.get("relationship", ""),
            "indemnitor_role": ind.get("role", "primary"),
            "total_cosigners": len(indemnitors),
            "documents": doc.get("documents", {}),
            "created_at": _safe_dt(doc.get("created_at", "")),
            "updated_at": _safe_dt(doc.get("updated_at", doc.get("created_at", ""))),
        })
    return items


# ── Routes ────────────────────────────────────────────────────────────────────

@indemnitors_bp.route("/indemnitors", methods=["GET"])
async def api_indemnitors_list():
    """List all indemnitors across prospective_bonds AND active_bonds."""
    try:
        search = request.args.get("search", "").strip()
        stage = request.args.get("stage", "").strip()
        limit = min(int(request.args.get("limit", 100)), 500)

        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")
        results = []

        base_q = {"$or": [{"indemnitor": {"$exists": True}}, {"indemnitors": {"$exists": True}}]}
        if search:
            search_q = {"$or": [
                {"indemnitor.name": {"$regex": search, "$options": "i"}},
                {"indemnitor.firstName": {"$regex": search, "$options": "i"}},
                {"indemnitor.lastName": {"$regex": search, "$options": "i"}},
                {"indemnitor.phone": {"$regex": search, "$options": "i"}},
                {"indemnitors.name": {"$regex": search, "$options": "i"}},
                {"defendant_name": {"$regex": search, "$options": "i"}},
                {"booking_number": {"$regex": search, "$options": "i"}},
            ]}
            p_query = {"$and": [base_q, search_q]}
        else:
            p_query = base_q.copy()

        if stage:
            p_query["stage"] = stage

        async for doc in prospective_bonds.find(p_query).sort("updated_at", -1).limit(limit):
            results.extend(_extract_indemnitors(doc, "prospective"))

        seen = {r["booking_number"] for r in results}
        a_query = p_query.copy()
        if "stage" in a_query:
            del a_query["stage"]

        async for doc in active_bonds.find(a_query).sort("created_at", -1).limit(limit):
            if doc.get("booking_number") in seen:
                continue
            results.extend(_extract_indemnitors(doc, "active", "bonded"))

        results.sort(key=lambda x: str(x.get("updated_at", "")), reverse=True)
        return jsonify({"success": True, "indemnitors": results[:limit], "total": len(results)})
    except Exception as e:
        logger.exception("api_indemnitors_list error")
        return jsonify({"error": str(e)}), 500


@indemnitors_bp.route("/indemnitors/by-person", methods=["GET"])
async def api_indemnitors_by_person():
    """Group all indemnitors by phone number."""
    try:
        search = request.args.get("search", "").strip()
        limit = min(int(request.args.get("limit", 200)), 1000)

        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")
        all_bonds = []

        def _build_query(search):
            base = {"$or": [
                {"indemnitor": {"$exists": True, "$ne": {}}},
                {"indemnitors": {"$exists": True, "$ne": []}},
            ]}
            if not search:
                return base
            return {"$and": [base, {"$or": [
                {"indemnitor.name": {"$regex": search, "$options": "i"}},
                {"indemnitor.firstName": {"$regex": search, "$options": "i"}},
                {"indemnitor.lastName": {"$regex": search, "$options": "i"}},
                {"indemnitor.phone": {"$regex": search, "$options": "i"}},
                {"indemnitor_name": {"$regex": search, "$options": "i"}},
                {"indemnitors.name": {"$regex": search, "$options": "i"}},
                {"defendant_name": {"$regex": search, "$options": "i"}},
                {"booking_number": {"$regex": search, "$options": "i"}},
            ]}]}

        async for doc in prospective_bonds.find(_build_query(search)).sort("updated_at", -1).limit(limit):
            indemnitors = doc.get("indemnitors", [])
            if not indemnitors and doc.get("indemnitor"):
                indemnitors = [doc.get("indemnitor", {})]
            for ind in indemnitors:
                name = _ind_name(ind)
                phone = ind.get("phone", "").strip()
                if not name and not phone:
                    continue
                all_bonds.append({
                    "booking_number": doc.get("booking_number", ""),
                    "defendant_name": doc.get("defendant_name", ""),
                    "county": doc.get("county", ""),
                    "bond_amount": doc.get("bond_amount", 0),
                    "stage": doc.get("stage", ""),
                    "bond_type": "prospective",
                    "charges": doc.get("charges", ""),
                    "created_at": _safe_dt(doc.get("created_at", "")),
                    "updated_at": _safe_dt(doc.get("updated_at", doc.get("created_at", ""))),
                    "indemnitor_name": name,
                    "indemnitor_phone": phone,
                    "indemnitor_email": ind.get("email", ""),
                    "indemnitor_relationship": ind.get("relationship", ""),
                    "indemnitor_role": ind.get("role", "primary"),
                    "indemnitor": ind,
                })

        async for doc in active_bonds.find(_build_query(search)).sort("updated_at", -1).limit(limit):
            indemnitors = doc.get("indemnitors", [])
            if not indemnitors and doc.get("indemnitor"):
                indemnitors = [doc.get("indemnitor", {})]
            for ind in indemnitors:
                name = _ind_name(ind)
                phone = ind.get("phone", "").strip()
                if not name and not phone:
                    continue
                all_bonds.append({
                    "booking_number": doc.get("booking_number", ""),
                    "defendant_name": doc.get("defendant_name", ""),
                    "county": doc.get("county", ""),
                    "bond_amount": doc.get("bond_amount", 0),
                    "stage": "bonded",
                    "bond_type": "active",
                    "charges": doc.get("charges", ""),
                    "created_at": _safe_dt(doc.get("created_at", "")),
                    "updated_at": _safe_dt(doc.get("updated_at", doc.get("created_at", ""))),
                    "indemnitor_name": name,
                    "indemnitor_phone": phone,
                    "indemnitor_email": ind.get("email", ""),
                    "indemnitor_relationship": ind.get("relationship", ""),
                    "indemnitor_role": ind.get("role", "primary"),
                    "indemnitor": ind,
                })

        grouped = {}
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
        return jsonify({"success": True, "persons": persons, "total": len(persons)})
    except Exception as e:
        logger.exception("api_indemnitors_by_person error")
        return jsonify({"error": str(e)}), 500


@indemnitors_bp.route("/indemnitors/search-existing", methods=["GET"])
async def api_indemnitor_search_existing():
    """Smart search across arrests, prospective_bonds, active_bonds."""
    try:
        q = request.args.get("q", "").strip()
        if len(q) < 2:
            return jsonify({"results": [], "total": 0})

        from dashboard.extensions import get_db
        db = get_db()
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        regex = {"$regex": q, "$options": "i"}
        results = []
        seen: set = set()

        async for doc in db["arrests"].find({"$or": [
            {"full_name": regex}, {"first_name": regex}, {"last_name": regex},
            {"booking_number": regex},
        ]}).limit(20):
            phone = doc.get("phone", "")
            key = phone or str(doc.get("_id"))
            if key in seen:
                continue
            seen.add(key)
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
            })

        async for doc in prospective_bonds.find({"$or": [
            {"indemnitor.name": regex}, {"indemnitor.firstName": regex},
            {"indemnitor.lastName": regex}, {"indemnitor.phone": regex},
            {"defendant_name": regex},
        ]}).limit(20):
            ind = doc.get("indemnitor", {})
            name = _ind_name(ind)
            phone = ind.get("phone", "")
            key = phone or name.lower()
            if key in seen or (not name and not phone):
                continue
            seen.add(key)
            results.append({
                "source": "prospective_bond",
                "name": name,
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
            })

        async for doc in active_bonds.find({"$or": [
            {"indemnitor.name": regex}, {"indemnitor_name": regex},
            {"defendant_name": regex},
        ]}).limit(20):
            ind = doc.get("indemnitor", {})
            name = _ind_name(ind) or doc.get("indemnitor_name", "")
            phone = ind.get("phone") or doc.get("indemnitor_phone", "")
            key = phone or name.lower()
            if key in seen or (not name and not phone):
                continue
            seen.add(key)
            results.append({
                "source": "active_bond",
                "name": name,
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
            })

        return jsonify({"success": True, "results": results[:30], "total": len(results)})
    except Exception as e:
        logger.exception("api_indemnitor_search_existing error")
        return jsonify({"error": str(e)}), 500


@indemnitors_bp.route("/indemnitors/create", methods=["POST"])
async def api_indemnitor_create():
    """Create or update an indemnitor and link to a bond."""
    try:
        data = await request.get_json(force=True)
        now = datetime.now(timezone.utc)
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        first_name = (data.get("firstName") or "").strip()
        last_name = (data.get("lastName") or "").strip()
        phone = (data.get("phone") or "").strip()
        email = (data.get("email") or "").strip()
        full_name = f"{first_name} {last_name}".strip() or data.get("name", "").strip()

        if not full_name and not phone:
            return jsonify({"error": "Name or phone required"}), 400

        booking_number = (data.get("booking_number") or "").strip()
        if not booking_number:
            return jsonify({"error": "booking_number required"}), 400

        doc = await prospective_bonds.find_one({"booking_number": booking_number})
        collection = prospective_bonds
        bond_type = "prospective"
        if not doc:
            doc = await active_bonds.find_one({"booking_number": booking_number})
            collection = active_bonds
            bond_type = "active"
        if not doc:
            return jsonify({"error": f"Bond {booking_number} not found"}), 404

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
            "updated_at": now.isoformat(),
        }

        # Migrate legacy single indemnitor to array
        existing_indemnitors = doc.get("indemnitors", [])
        if not existing_indemnitors and doc.get("indemnitor"):
            old = doc.get("indemnitor", {})
            if _ind_name(old) or old.get("phone"):
                old["role"] = old.get("role", "primary")
                existing_indemnitors = [old]

        # Dedup by phone
        if phone:
            for existing in existing_indemnitors:
                if existing.get("phone") == phone:
                    existing.update(profile)
                    await collection.update_one(
                        {"booking_number": booking_number},
                        {"$set": {
                            "indemnitors": existing_indemnitors,
                            "indemnitor": existing_indemnitors[0],
                            "indemnitor_name": _ind_name(existing_indemnitors[0]),
                            "updated_at": now,
                        }}
                    )
                    return jsonify({
                        "success": True, "action": "updated_existing",
                        "indemnitors": existing_indemnitors,
                        "booking_number": booking_number,
                    })

        if len(existing_indemnitors) >= 5:
            return jsonify({"error": "Maximum 5 indemnitors per bond"}), 400

        if not profile.get("role") or profile["role"] == "primary":
            profile["role"] = "primary" if not existing_indemnitors else f"cosigner_{len(existing_indemnitors)}"

        existing_indemnitors.append(profile)
        primary = existing_indemnitors[0]

        await collection.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "indemnitors": existing_indemnitors,
                "indemnitor": primary,
                "indemnitor_name": _ind_name(primary),
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

        return jsonify({
            "success": True, "action": "created",
            "indemnitors": existing_indemnitors,
            "booking_number": booking_number,
            "bond_type": bond_type,
        })
    except Exception as e:
        logger.exception("api_indemnitor_create error")
        return jsonify({"error": str(e)}), 500


@indemnitors_bp.route("/indemnitors/<booking_number>", methods=["GET"])
async def api_indemnitor_detail(booking_number):
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
            return jsonify({"error": "Bond not found"}), 404

        ind = doc.get("indemnitor", {})
        return jsonify({
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
        })
    except Exception as e:
        logger.exception("api_indemnitor_detail error")
        return jsonify({"error": str(e)}), 500


@indemnitors_bp.route("/indemnitors/<booking_number>", methods=["PATCH"])
async def api_indemnitor_update(booking_number):
    """Update full indemnitor profile."""
    try:
        data = await request.get_json(force=True)
        now = datetime.now(timezone.utc)
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        doc = await prospective_bonds.find_one({"booking_number": booking_number})
        collection = prospective_bonds
        if not doc:
            doc = await active_bonds.find_one({"booking_number": booking_number})
            collection = active_bonds
        if not doc:
            return jsonify({"error": "Bond not found"}), 404

        indemnitor = doc.get("indemnitor", {})
        for field in INDEMNITOR_FIELDS:
            if data.get(field) is not None:
                indemnitor[field] = data[field]

        update_ops = {"$set": {"indemnitor": indemnitor, "updated_at": now}}
        if collection == prospective_bonds:
            update_ops["$push"] = {"timeline": {
                "timestamp": now.isoformat(),
                "event": "indemnitor_profile_updated",
                "detail": f"Full profile updated: {_ind_name(indemnitor)}"[:200],
                "agent": data.get("agent", "Dashboard"),
            }}

        await collection.update_one({"booking_number": booking_number}, update_ops)
        return jsonify({"success": True, "indemnitor": indemnitor})
    except Exception as e:
        logger.exception("api_indemnitor_update error")
        return jsonify({"error": str(e)}), 500


@indemnitors_bp.route("/indemnitors/<booking_number>/documents", methods=["GET"])
async def api_indemnitor_documents(booking_number):
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
            return jsonify({"error": "Bond not found"}), 404

        saved_docs = doc.get("documents", {})
        checklist = {}
        for section, items in DOCUMENT_CHECKLIST.items():
            checklist[section] = [{
                **item,
                "signed": saved_docs.get(item["key"], {}).get("signed", False),
                "signed_at": saved_docs.get(item["key"], {}).get("signed_at", ""),
                "signnow_id": saved_docs.get(item["key"], {}).get("signnow_id", ""),
            } for item in items]

        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "bond_type": bond_type,
            "surety": doc.get("surety", "osi"),
            "checklist": checklist,
        })
    except Exception as e:
        logger.exception("api_indemnitor_documents error")
        return jsonify({"error": str(e)}), 500


@indemnitors_bp.route("/indemnitors/<booking_number>/documents", methods=["PATCH"])
async def api_indemnitor_documents_update(booking_number):
    """Toggle document signed status."""
    try:
        data = await request.get_json(force=True)
        doc_key = data.get("doc_key", "")
        signed = data.get("signed", False)
        if not doc_key:
            return jsonify({"error": "doc_key required"}), 400

        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        doc = await prospective_bonds.find_one({"booking_number": booking_number})
        collection = prospective_bonds
        if not doc:
            doc = await active_bonds.find_one({"booking_number": booking_number})
            collection = active_bonds
        if not doc:
            return jsonify({"error": "Bond not found"}), 404

        now = datetime.now(timezone.utc)
        await collection.update_one(
            {"booking_number": booking_number},
            {"$set": {
                f"documents.{doc_key}.signed": signed,
                f"documents.{doc_key}.signed_at": now.isoformat() if signed else "",
                "updated_at": now,
            }},
        )
        return jsonify({"success": True, "doc_key": doc_key, "signed": signed})
    except Exception as e:
        logger.exception("api_indemnitor_documents_update error")
        return jsonify({"error": str(e)}), 500


@indemnitors_bp.route("/indemnitors/<booking_number>/uploads", methods=["GET"])
async def api_indemnitor_uploads_list(booking_number):
    """List all uploaded KYC documents for an indemnitor's bond."""
    try:
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        doc = await prospective_bonds.find_one({"booking_number": booking_number})
        if not doc:
            doc = await active_bonds.find_one({"booking_number": booking_number})
        if not doc:
            return jsonify({"error": "Bond not found"}), 404

        return jsonify({
            "success": True,
            "booking_number": booking_number,
            "uploads": doc.get("kyc_uploads", []),
            "total": len(doc.get("kyc_uploads", [])),
            "doc_types": KYC_DOC_TYPES,
        })
    except Exception as e:
        logger.exception("api_indemnitor_uploads_list error")
        return jsonify({"error": str(e)}), 500


@indemnitors_bp.route("/indemnitors/<booking_number>/uploads", methods=["POST"])
async def api_indemnitor_upload(booking_number):
    """Upload a KYC document/image for an indemnitor's bond."""
    try:
        files = await request.files
        form = await request.form
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        if "file" not in files:
            return jsonify({"error": "No file uploaded"}), 400

        file = files["file"]
        if not file.filename:
            return jsonify({"error": "Empty filename"}), 400

        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            return jsonify({"error": f"File type .{ext} not allowed"}), 400

        doc_type = form.get("doc_type", "other")
        if doc_type not in KYC_DOC_TYPES:
            doc_type = "other"

        booking_dir = UPLOAD_DIR / booking_number
        booking_dir.mkdir(exist_ok=True)

        file_id = str(uuid.uuid4())[:8]
        safe_name = f"{doc_type}_{file_id}.{ext}"
        file_path = booking_dir / safe_name
        await file.save(str(file_path))

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

        return jsonify({
            "success": True, "file_id": file_id, "filename": safe_name,
            "doc_type": doc_type, "doc_type_label": KYC_DOC_TYPES.get(doc_type, "Other"),
            "size_bytes": file_size,
        }), 201
    except Exception as e:
        logger.exception("api_indemnitor_upload error")
        return jsonify({"error": str(e)}), 500


@indemnitors_bp.route("/indemnitors/<booking_number>/uploads/<file_id>", methods=["DELETE"])
async def api_indemnitor_upload_delete(booking_number, file_id):
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
                    fp = Path(target.get("path", ""))
                    if fp.exists():
                        fp.unlink()
                    await coll.update_one(
                        {"booking_number": booking_number},
                        {"$pull": {"kyc_uploads": {"file_id": file_id}}},
                    )
                    return jsonify({"success": True, "deleted": file_id})
                break

        return jsonify({"error": "Upload not found"}), 404
    except Exception as e:
        logger.exception("api_indemnitor_upload_delete error")
        return jsonify({"error": str(e)}), 500


@indemnitors_bp.route("/indemnitors/<booking_number>/payment-link", methods=["POST"])
async def api_indemnitor_payment_link(booking_number):
    """Generate a SwipeSimple payment link for this bond."""
    try:
        from urllib.parse import urlencode
        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        doc = await prospective_bonds.find_one({"booking_number": booking_number})
        collection = prospective_bonds
        if not doc:
            doc = await active_bonds.find_one({"booking_number": booking_number})
            collection = active_bonds
        if not doc:
            return jsonify({"error": "Bond not found"}), 404

        ind = doc.get("indemnitor", {})
        ind_name = _ind_name(ind) or "Indemnitor"
        bond_amount = doc.get("bond_amount", 0)
        premium = round(float(bond_amount) * 0.10, 2) if bond_amount else 0

        base_url = os.environ.get("SWIPESIMPLE_URL", "https://shamrockbailbonds.biz/payment")
        payment_url = f"{base_url}?{urlencode({'amount': str(premium), 'name': ind_name, 'booking': booking_number, 'county': doc.get('county', '')})}"

        now = datetime.now(timezone.utc)
        await collection.update_one(
            {"booking_number": booking_number},
            {"$set": {"payment_link": payment_url, "payment_premium": premium, "updated_at": now}},
        )
        return jsonify({
            "success": True, "payment_link": payment_url,
            "premium": premium, "bond_amount": bond_amount, "indemnitor_name": ind_name,
        })
    except Exception as e:
        logger.exception("api_indemnitor_payment_link error")
        return jsonify({"error": str(e)}), 500


@indemnitors_bp.route("/indemnitors/<booking_number>/remove", methods=["POST"])
async def api_indemnitor_remove(booking_number):
    """Remove a cosigner from a bond by phone number."""
    try:
        data = await request.get_json(force=True)
        phone = (data.get("phone") or "").strip()
        if not phone:
            return jsonify({"error": "phone required to identify indemnitor"}), 400

        prospective_bonds = get_collection("prospective_bonds")
        active_bonds = get_collection("active_bonds")

        doc = await prospective_bonds.find_one({"booking_number": booking_number})
        collection = prospective_bonds
        if not doc:
            doc = await active_bonds.find_one({"booking_number": booking_number})
            collection = active_bonds
        if not doc:
            return jsonify({"error": "Bond not found"}), 404

        existing = doc.get("indemnitors", [])
        if len(existing) <= 1:
            return jsonify({"error": "Cannot remove the last indemnitor"}), 400

        updated = [i for i in existing if i.get("phone") != phone]
        if len(updated) == len(existing):
            return jsonify({"error": "Indemnitor not found with that phone number"}), 404

        for idx, ind in enumerate(updated):
            ind["role"] = "primary" if idx == 0 else f"cosigner_{idx}"

        now = datetime.now(timezone.utc)
        primary = updated[0]
        await collection.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "indemnitors": updated,
                "indemnitor": primary,
                "indemnitor_name": _ind_name(primary),
                "indemnitor_phone": primary.get("phone", ""),
                "updated_at": now,
            }},
        )
        return jsonify({"success": True, "indemnitors": updated, "booking_number": booking_number})
    except Exception as e:
        logger.exception("api_indemnitor_remove error")
        return jsonify({"error": str(e)}), 500



# ── Serve uploaded files ──────────────────────────────────────────────────────

@indemnitors_bp.route("/uploads/<booking_number>/<filename>")
async def serve_upload(booking_number, filename):
    """Serve uploaded KYC files for preview in dashboard."""
    upload_path = UPLOAD_DIR / booking_number
    if not upload_path.exists():
        return jsonify({"error": "Not found"}), 404
    return await send_from_directory(str(upload_path), filename)
