from __future__ import annotations

"""
ShamrockLeads — Bonds API Blueprint
Endpoints: /api/write-bond, /api/active-bonds (CRUD),
           /api/appearance-bond-pdf, /api/appearance-bond-batch,
           /api/appearance-bonds/print-package
"""

import json as json_lib
import os
import re
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request
from starlette.responses import Response
from fastapi.responses import JSONResponse
from dashboard.extensions import get_collection, get_db
from dashboard.services.risk_engine import compute_risk_score

bonds_bp = APIRouter(prefix="/api", tags=["bonds"])
import asyncio
import logging
import traceback
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# RECORD BOND — Retrospective manual bond entry
# Creates active_bonds + payments + assigns POA + audit log
# ═══════════════════════════════════════════════════════════════════════════════

@bonds_bp.post("/bonds/record")
async def api_record_bond(request: Request):
    """
    Record a bond retrospectively. This is for bonds that were written
    manually (in-person, phone, etc.) and need to be logged into the system
    so they feed into analytics, liability, and revenue tracking.

    Creates:
      1. active_bonds document (liability tracking)
      2. payments document (revenue tracking)
      3. poa_inventory update (marks POA as assigned)
      4. audit_events document (audit trail)

    Body:
        {
            "defendant_name":    "John Doe",
            "booking_number":    "2025-001234",
            "county":            "Lee",
            "bond_amount":       5000,
            "premium":           500,
            "surety":            "osi",
            "poa_number":        "12345",
            "case_number":       "25-CF-001234",
            "court_date":        "2025-06-15",
            "court_time":        "8:30 AM",
            "court_location":    "Lee County Justice Center",
            "bond_date":         "2025-04-30",
            "charges":           "Battery (Domestic Violence)",
            "facility":          "Lee County Jail",
            "indemnitor_name":   "Jane Doe",
            "indemnitor_phone":  "2395550000",
            "indemnitor_email":  "jane@example.com",
            "indemnitor_relationship": "Wife",
            "payment_method":    "cash",
            "agent_name":        "Brendan O'Neal",
            "notes":             "Walk-in client"
        }
    """
    data = await request.json() or {}

    # ── Validate required fields ────────────────────────────────────────────
    defendant_name = (data.get("defendant_name") or "").strip()
    booking_number = (data.get("booking_number") or "").strip()
    poa_number = (data.get("poa_number") or "").strip()
    surety = (data.get("surety") or "osi").lower().strip()

    errors = []
    if not defendant_name:
        errors.append("defendant_name is required")
    if not booking_number:
        errors.append("booking_number is required")
    if not poa_number:
        errors.append("poa_number is required")
    if surety not in ("osi", "palmetto"):
        errors.append("surety must be 'osi' or 'palmetto'")
    if errors:
        return JSONResponse({"success": False, "errors": errors}, status_code=400)

    try:
        bond_amount = float(data.get("bond_amount") or 0)
    except (ValueError, TypeError):
        bond_amount = 0.0
    try:
        premium = float(data.get("premium") or 0)
    except (ValueError, TypeError):
        premium = 0.0

    county = (data.get("county") or "").strip()
    case_number = (data.get("case_number") or "").strip()
    court_date = (data.get("court_date") or "").strip()
    court_time = (data.get("court_time") or "").strip()
    court_location = (data.get("court_location") or "").strip()
    bond_date_str = (data.get("bond_date") or "").strip()
    charges = (data.get("charges") or "").strip()
    facility = (data.get("facility") or "").strip()
    indemnitor_name = (data.get("indemnitor_name") or "").strip()
    indemnitor_phone = (data.get("indemnitor_phone") or "").strip()
    indemnitor_email = (data.get("indemnitor_email") or "").strip()
    indemnitor_relationship = (data.get("indemnitor_relationship") or "").strip()
    payment_method = (data.get("payment_method") or "cash").strip()
    agent_name = (data.get("agent_name") or "Brendan O'Neal").strip()
    notes = (data.get("notes") or "").strip()

    now = datetime.now(timezone.utc)

    # Parse bond_date or default to now
    bond_date = now
    if bond_date_str:
        try:
            bond_date = datetime.strptime(bond_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass  # Fall back to now

    # ── 0. Snapshot FTA risk intelligence from arrest record ─────────────────
    fta_risk_score = None
    fta_risk_level = ""
    fta_risk_confidence = None
    try:
        arrests = get_collection("arrests")
        arrest_doc = await arrests.find_one(
            {"booking_number": booking_number},
            {"fta_risk_score": 1, "fta_risk_level": 1, "fta_risk_confidence": 1},
        )
        if arrest_doc:
            fta_risk_score = arrest_doc.get("fta_risk_score")
            fta_risk_level = arrest_doc.get("fta_risk_level", "")
            fta_risk_confidence = arrest_doc.get("fta_risk_confidence")
    except Exception as exc:
        logger.warning("[record-bond] FTA lookup error: %s", exc)

    # ── 1. Create / upsert active_bonds document ────────────────────────────
    active_bonds = get_collection("active_bonds")
    bond_doc = {
        "booking_number": booking_number,
        "defendant_name": defendant_name,
        "county": county,
        "facility": facility,
        "bond_amount": bond_amount,
        "premium": premium,
        "insurance_company": surety.upper(),
        "poa_number": poa_number,
        "case_number": case_number,
        "charges": charges,
        "court_date": court_date,
        "court_time": court_time,
        "court_location": court_location,
        "bond_date": bond_date.isoformat(),
        "status": "active",
        "source": "retrospective_manual",
        "manual_entry": booking_number.startswith("MANUAL-"),
        "agent_name": agent_name,
        "indemnitor_name": indemnitor_name,
        "indemnitor_phone": indemnitor_phone,
        "indemnitor_email": indemnitor_email,
        "indemnitor_relationship": indemnitor_relationship,
        "payment_method": payment_method,
        "notes": notes,
        "check_in_required": False,
        "fta_risk_score": fta_risk_score,
        "fta_risk_level": fta_risk_level,
        "fta_risk_confidence": fta_risk_confidence,
        "created_at": bond_date,
        "updated_at": now,
    }

    await active_bonds.update_one(
        {"booking_number": booking_number},
        {"$set": bond_doc},
        upsert=True,
    )
    logger.info("[record-bond] Active bond created: %s — %s (%s)", booking_number, defendant_name, surety.upper())

    # ── 2. Create payments document (revenue tracking) ──────────────────────
    payment_doc = None
    if premium > 0:
        payments = get_collection("payments")
        payment_doc = {
            "booking_number": booking_number,
            "defendant_name": defendant_name,
            "county": county,
            "amount": premium,
            "bond_amount": bond_amount,
            "surety": surety.upper(),
            "poa_number": poa_number,
            "method": payment_method,
            "status": "completed",
            "source": "retrospective_manual",
            "agent_name": agent_name,
            "indemnitor_name": indemnitor_name,
            "indemnitor_phone": indemnitor_phone,
            "timestamp": bond_date,
            "created_at": now,
        }
        await payments.update_one(
            {"booking_number": booking_number, "source": "retrospective_manual"},
            {"$set": payment_doc},
            upsert=True,
        )
        logger.info("[record-bond] Payment recorded: $%.2f for %s", premium, booking_number)

    # ── 3. Assign POA in inventory ──────────────────────────────────────────
    poa_result = {"assigned": False}
    poa_inventory = get_collection("poa_inventory")
    poa_doc = await poa_inventory.find_one({"poa_number": poa_number, "surety_id": surety})
    if poa_doc:
        if poa_doc.get("status") == "available":
            await poa_inventory.update_one(
                {"poa_number": poa_number, "surety_id": surety},
                {"$set": {
                    "status": "assigned",
                    "bond_case_id": booking_number,
                    "used_at": now.isoformat(),
                    "defendant_name": defendant_name,
                }},
            )
            poa_result = {"assigned": True, "poa_number": poa_number, "was": "available"}
            logger.info("[record-bond] POA %s assigned to %s", poa_number, booking_number)
        elif poa_doc.get("status") == "assigned":
            # Already assigned — update the case link
            await poa_inventory.update_one(
                {"poa_number": poa_number, "surety_id": surety},
                {"$set": {"bond_case_id": booking_number, "defendant_name": defendant_name}},
            )
            poa_result = {"assigned": True, "poa_number": poa_number, "was": "already_assigned"}
        else:
            poa_result = {"assigned": False, "poa_number": poa_number, "reason": f"POA is {poa_doc.get('status')}"}
    else:
        # POA not in inventory — create it as assigned (user manually entered a number)
        await poa_inventory.insert_one({
            "surety_id": surety,
            "poa_prefix": "",
            "poa_number": poa_number,
            "poa_full": poa_number,
            "max_bond_value": 0,
            "status": "assigned",
            "bond_case_id": booking_number,
            "defendant_name": defendant_name,
            "used_at": now.isoformat(),
            "book_number": "manual_entry",
            "assigned_to_agent": agent_name,
            "received_at": now.isoformat(),
        })
        poa_result = {"assigned": True, "poa_number": poa_number, "was": "created_and_assigned"}
        logger.info("[record-bond] POA %s created and assigned (not in inventory)", poa_number)

    # ── 4. Audit event ──────────────────────────────────────────────────────
    try:
        audit_col = get_collection("audit_events")
        await audit_col.insert_one({
            "event_type": "bond_recorded_retroactive",
            "entity_id": booking_number,
            "entity_type": "bond_case",
            "defendant_name": defendant_name,
            "county": county,
            "bond_amount": bond_amount,
            "premium": premium,
            "surety": surety.upper(),
            "poa_number": poa_number,
            "case_number": case_number,
            "agent_name": agent_name,
            "source": "retrospective_manual",
            "timestamp": now,
        })
    except Exception as exc:
        logger.warning("[record-bond] Audit log error: %s", exc)

    # ── 5. Update arrest record with bond status (if exists) ────────────────
    try:
        arrests = get_collection("arrests")
        await arrests.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "bond_written": True,
                "bond_written_at": now.isoformat(),
                "bond_poa_number": poa_number,
                "bond_surety": surety.upper(),
                "bond_premium": premium,
            }},
        )
    except Exception as exc:
        logger.warning("[record-bond] Arrest update error: %s", exc)

    from dashboard.routers.helpers import mask_phone
    logger.info(
        "☘️ BOND RECORDED — %s | Booking: %s | County: %s | Bond: $%.2f | Premium: $%.2f | Surety: %s | POA: %s | Indemnitor: %s (%s)",
        defendant_name, booking_number, county, bond_amount, premium, surety.upper(), poa_number, indemnitor_name, mask_phone(indemnitor_phone)
    )

    # Real-time dashboard event — sl-core.js listens for 'bond_written'
    try:
        from dashboard.routers.events import publish_event
        await publish_event("bond_written", {
            "booking_number": booking_number,
            "defendant_name": defendant_name,
            "county": county,
            "bond_amount": bond_amount,
            "premium": premium,
            "surety": surety.upper(),
            "poa_number": poa_number,
        })
    except Exception:
        pass

    return {
        "success": True,
        "message": f"Bond recorded for {defendant_name}",
        "booking_number": booking_number,
        "bond_amount": bond_amount,
        "premium": premium,
        "surety": surety.upper(),
        "poa": poa_result,
        "payment_recorded": premium > 0,
    }

@bonds_bp.post("/write-bond")
async def api_write_bond(request: Request):
    """
    Accept defendant + indemnitor data + insurance company selection,
    format a GAS-compatible SignNow payload, and forward it.

    Accepts indemnitors as a list under the key "indemnitors" (up to 5),
    or a single indemnitor under the legacy key "indemnitor".
    All fields mirror Dashboard.html addIndemnitor() schema exactly.
    """
    data = await request.json()
    if not data:
        return JSONResponse({"success": False, "error": "No payload received"}, status_code=400)

    insurer = data.get("insurance_company", "osi")
    defendant = data.get("defendant", {})
    booking = data.get("booking", {})
    bond = data.get("bond", {})
    charges = data.get("charges", "")
    court = data.get("court", {})

    # ── Indemnitor(s) — accept list or single object ──────────────────────────
    raw_indemnitors = data.get("indemnitors") or []
    if not raw_indemnitors and data.get("indemnitor"):
        raw_indemnitors = [data["indemnitor"]]

    def _build_indemnitor(ind: dict) -> dict:
        """Normalize a single indemnitor dict to the GAS/Dashboard.html schema."""
        g = lambda *keys: next((str(ind.get(k, "")).strip() for k in keys if ind.get(k)), "")
        return {
            # Personal
            "firstName":        g("firstName", "IndFirstName", "indemnitorFirstName", "first_name"),
            "middleName":       g("middleName", "IndMiddleName", "indemnitorMiddleName"),
            "lastName":         g("lastName", "IndLastName", "indemnitorLastName", "last_name"),
            "relationship":     g("relationship", "IndRelation", "indemnitorRelation", "Relationship"),
            "dob":              g("dob", "IndDOB", "indemnitorDOB"),
            "ssn":              g("ssn", "IndSSN", "indemnitorSSN"),
            "dl":               g("dl", "IndDL", "indemnitorDL", "dlNumber"),
            "dlState":          g("dlState", "IndDLState", "indemnitorDLState") or "FL",
            # Contact
            "phone":            g("phone", "IndPhone", "indemnitorPhone"),
            "email":            g("email", "IndEmail", "indemnitorEmail"),
            # Address
            "address":          g("address", "IndAddress", "indemnitorStreetAddress", "indemnitorAddress"),
            "city":             g("city", "IndCity", "indemnitorCity"),
            "state":            g("state", "IndState", "indemnitorState") or "FL",
            "zip":              g("zip", "IndZip", "indemnitorZipCode", "indemnitorZip"),
            # Employment
            "employer":         g("employer", "IndEmployer", "indemnitorEmployerName"),
            "employerPhone":    g("employerPhone", "IndEmployerPhone", "indemnitorEmployerPhone"),
            "employerCity":     g("employerCity", "IndEmployerCity", "indemnitorEmployerCity"),
            "employerState":    g("employerState", "IndEmployerState", "indemnitorEmployerState"),
            "supervisor":       g("supervisor", "IndJobTitle", "indemnitorSupervisorName", "jobTitle"),
            "supervisorPhone":  g("supervisorPhone", "IndSupervisorPhone", "indemnitorSupervisorPhone"),
            # References
            "ref1Name":         g("ref1Name", "Ref1Name", "reference1Name"),
            "ref1Relation":     g("ref1Relation", "Ref1Relation", "reference1Relation"),
            "ref1Phone":        g("ref1Phone", "Ref1Phone", "reference1Phone"),
            "ref1Address":      g("ref1Address", "Ref1Address", "reference1Address"),
            "ref2Name":         g("ref2Name", "Ref2Name", "reference2Name"),
            "ref2Relation":     g("ref2Relation", "Ref2Relation", "reference2Relation"),
            "ref2Phone":        g("ref2Phone", "Ref2Phone", "reference2Phone"),
            "ref2Address":      g("ref2Address", "Ref2Address", "reference2Address"),
        }

    indemnitors_payload = [_build_indemnitor(ind) for ind in raw_indemnitors]

    # Validate required fields
    if not defendant.get("full_name"):
        return JSONResponse({"success": False, "error": "Defendant name required"}, status_code=400)
    if not booking.get("booking_number"):
        return JSONResponse({"success": False, "error": "Booking number required"}, status_code=400)

    # Bond amount — scrapers often leave $0 until first appearance; staff must set real amount
    try:
        _bond_amt = float(bond.get("amount") or bond.get("bond_amount") or 0)
    except (TypeError, ValueError):
        _bond_amt = 0.0
    if _bond_amt <= 0:
        return JSONResponse({
            "success": False,
            "error": (
                "Bond amount is $0. Set the real bond amount on the defendant record "
                "(Defendants tab or Write Bond modal) before writing. Jail sites often "
                "publish bond hours after booking."
            ),
        }, status_code=400)

    # Normalise surety to lowercase canonical form used by GAS template router
    surety_id = insurer.lower().strip()
    if surety_id not in ("osi", "palmetto"):
        surety_id = "osi"  # safe default

    # ── Format GAS-compatible payload ──
    gas_payload = {
        "action": "sendPaperwork",
        "source": "shamrock-leads-dashboard",
        # insuranceCompany: legacy GAS key (UPPERCASE) used by _shannon_buildFormData
        "insuranceCompany": surety_id.upper(),
        # surety_id: canonical lowercase key used by _resolveTemplateId in Telegram_Documents.js
        "surety_id": surety_id,
        "defendant": {
            "fullName":   defendant.get("full_name", ""),
            "firstName":  defendant.get("first_name", ""),
            "lastName":   defendant.get("last_name", ""),
            "middleName": defendant.get("middle_name", ""),
            "dob":        defendant.get("dob", ""),
            "address":    defendant.get("address", ""),
            "city":       defendant.get("city", ""),
            "state":      defendant.get("state", "FL"),
            "zip":        defendant.get("zip", ""),
            "sex":        defendant.get("sex", ""),
            "race":       defendant.get("race", ""),
            "height":     defendant.get("height", ""),
            "weight":     defendant.get("weight", ""),
        },
        "booking": {
            "bookingNumber": booking.get("booking_number", ""),
            "county":        booking.get("county", ""),
            "facility":      booking.get("facility", ""),
            "agency":        booking.get("agency", ""),
            "arrestDate":    booking.get("arrest_date", ""),
            "bookingDate":   booking.get("booking_date", ""),
        },
        "bond": {
            "totalAmount": bond.get("amount", 0),
            "premium":     bond.get("premium", 0),
            "type":        bond.get("type", ""),
            "paid":        bond.get("paid", "NO"),
        },
        "charges": charges,
        "court": {
            "date":       court.get("date", ""),
            "time":       court.get("time", ""),
            "type":       court.get("type", ""),
            "location":   court.get("location", ""),
            "caseNumber": court.get("case_number", ""),
        },
        # ── Indemnitors (full schema, mirrors Dashboard.html addIndemnitor) ──
        "indemnitors": indemnitors_payload,
        # Legacy single-indemnitor key for GAS backward compat
        "indemnitor": indemnitors_payload[0] if indemnitors_payload else {},
        # Intake source tracking
        "intake_id":  data.get("intake_id", ""),
        "intake_source": data.get("intake_source", "shamrock-leads-dashboard"),
        # Agent constants — always locked to canonical values regardless of caller
        "AgentName":         "Brendan O'Neal",
        "AgentLicense":      "P139768",
        "AgentLicenseNumber": "P139768",
    }

    # Log the formatted payload
    logger.info(
        "📋 WRITE BOND — %s | Insurance: %s | Bond: $%.2f | Premium: $%.2f | County: %s | Booking: %s | Indemnitors: %d",
        defendant.get("full_name", "Unknown"), insurer.upper(), bond.get("amount", 0), bond.get("premium", 0), booking.get("county", "Unknown"), booking.get("booking_number", "N/A"), len(indemnitors_payload)
    )

    # ── Forward to GAS (when configured) ──
    gas_url = os.getenv("GAS_WEB_APP_URL", "")
    if gas_url:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(gas_url, json=gas_payload, timeout=30)
                if resp.status_code < 400:
                    content_type = resp.headers.get("content-type", "")
                    gas_resp = resp.json() if "application/json" in content_type else resp.text[:200]

                    # Real-time dashboard event — sl-core.js listens for 'bond_written'
                    try:
                        from dashboard.routers.events import publish_event
                        await publish_event("bond_written", {
                            "booking_number": booking.get("booking_number", ""),
                            "defendant_name": defendant.get("full_name", ""),
                            "county": booking.get("county", ""),
                            "bond_amount": bond.get("amount", 0),
                            "premium": bond.get("premium", 0),
                            "surety": insurer.upper(),
                        })
                    except Exception:
                        pass

                    return {
                        "success": True,
                        "message": f"Packet sent to GAS for {defendant.get('full_name')}",
                        "insurance_company": insurer.upper(),
                        "indemnitor_count": len(indemnitors_payload),
                        "gas_response": gas_resp,
                    }
                else:
                    return JSONResponse(status_code=502, content={
                        "success": False,
                        "error": f"GAS returned {resp.status_code}: {resp.text[:200]}",
                    })
        except Exception as e:
            return JSONResponse(status_code=502, content={
                "success": False,
                "error": f"GAS connection failed: {str(e)}",
            })

    # No GAS URL configured — return success with payload for review
    return {
        "success": True,
        "message": f"Bond packet prepared for {defendant.get('full_name', 'Unknown')} via {insurer.upper()}",
        "insurance_company": insurer.upper(),
        "indemnitor_count": len(indemnitors_payload),
        "payload": gas_payload,
        "note": "GAS_WEB_APP_URL not configured — payload logged to console. Set GAS_WEB_APP_URL in .env to enable forwarding.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ACTIVE BONDS — GEOLOCATION & RISK MITIGATION
# ═══════════════════════════════════════════════════════════════════════════════

@bonds_bp.get("/active-bonds")
async def api_active_bonds_list(request: Request, limit: int = 200):
    """List active bonds with risk scores, check-in status, and FTA intelligence.

    Sub-agents only receive bonds attributed to their license / writing agent.
    """
    active_bonds = get_collection("active_bonds")
    try:
        from dashboard.auth.agent_scope import bond_scope_query, merge_scope

        lim = max(1, min(int(limit or 200), 500))
        match = merge_scope({}, bond_scope_query(request))
        cursor = active_bonds.find(match, {"_id": 0}).sort("created_at", -1).limit(lim)
        bonds = await cursor.to_list(length=lim)

        # ── Bulk-enrich FTA intelligence from arrests for bonds missing it ─────
        needs_fta = [b["booking_number"] for b in bonds
                     if b.get("fta_risk_score") is None and b.get("booking_number")]
        fta_map = {}
        if needs_fta:
            try:
                arrests = get_collection("arrests")
                fta_cursor = arrests.find(
                    {"booking_number": {"$in": needs_fta}, "fta_risk_score": {"$exists": True}},
                    {"_id": 0, "booking_number": 1, "fta_risk_score": 1, "fta_risk_level": 1, "fta_risk_confidence": 1},
                )
                async for adoc in fta_cursor:
                    fta_map[adoc["booking_number"]] = adoc
            except Exception as fta_err:
                logger.warning("[active-bonds] FTA enrichment error: %s", fta_err)

        for b in bonds:
            if hasattr(b.get("created_at"), "isoformat"):
                b["created_at"] = b["created_at"].isoformat()
            if hasattr(b.get("last_checkin"), "isoformat"):
                b["last_checkin"] = b["last_checkin"].isoformat()
            # Merge FTA data from arrest lookup if bond record doesn't have it
            if b.get("fta_risk_score") is None and b.get("booking_number") in fta_map:
                adoc = fta_map[b["booking_number"]]
                b["fta_risk_score"] = adoc.get("fta_risk_score")
                b["fta_risk_level"] = adoc.get("fta_risk_level", "")
                b["fta_risk_confidence"] = adoc.get("fta_risk_confidence")

        return {"success": True, "bonds": bonds, "count": len(bonds)}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e), "bonds": []}, status_code=500)


def _norm_phone(raw: str) -> str:
    digits = "".join(c for c in str(raw or "") if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def _norm_name(raw: str) -> str:
    return " ".join(str(raw or "").upper().split())


def _extract_parties(bond: dict) -> list[dict]:
    """Normalize defendant + indemnitor fields from a bond document."""
    parties = []
    dname = (bond.get("defendant_name") or "").strip()
    dphone = _norm_phone(bond.get("defendant_phone") or "")
    if dname or dphone:
        parties.append({"role": "defendant", "name": dname, "phone": dphone})
    ind = bond.get("indemnitor") if isinstance(bond.get("indemnitor"), dict) else {}
    iname = (bond.get("indemnitor_name") or ind.get("name") or "").strip()
    iphone = _norm_phone(bond.get("indemnitor_phone") or ind.get("phone") or "")
    if iname or iphone:
        parties.append({
            "role": "indemnitor",
            "name": iname,
            "phone": iphone,
            "relationship": bond.get("indemnitor_relationship") or ind.get("relationship") or "",
        })
    for extra in bond.get("indemnitors") or []:
        if not isinstance(extra, dict):
            continue
        en = (extra.get("name") or "").strip()
        ep = _norm_phone(extra.get("phone") or "")
        if en or ep:
            parties.append({
                "role": "indemnitor",
                "name": en,
                "phone": ep,
                "relationship": extra.get("relationship") or "",
            })
    return parties


@bonds_bp.get("/active-bonds/by-person")
async def api_bonds_by_person(
    name: str = "",
    phone: str = "",
    role: str = "any",
    limit: int = 50,
):
    """Recall all bonds where a defendant or indemnitor matches name and/or phone."""
    active_bonds = get_collection("active_bonds")
    name_q = _norm_name(name)
    phone_q = _norm_phone(phone)
    if not name_q and not phone_q:
        return JSONResponse(
            {"success": False, "error": "Provide name and/or phone"},
            status_code=400,
        )

    lim = max(1, min(int(limit or 50), 200))
    or_clauses = []
    if name.strip():
        or_clauses.extend([
            {"defendant_name": {"$regex": re.escape(name.strip()), "$options": "i"}},
            {"indemnitor_name": {"$regex": re.escape(name.strip()), "$options": "i"}},
            {"indemnitor.name": {"$regex": re.escape(name.strip()), "$options": "i"}},
        ])
    if phone_q:
        or_clauses.extend([
            {"indemnitor_phone": {"$regex": phone_q}},
            {"indemnitor.phone": {"$regex": phone_q}},
            {"defendant_phone": {"$regex": phone_q}},
        ])

    try:
        cursor = active_bonds.find({"$or": or_clauses}, {"_id": 0}).sort("created_at", -1).limit(lim)
        bonds = await cursor.to_list(length=lim)
        if role in ("defendant", "indemnitor") and name_q:
            filtered = []
            for b in bonds:
                dname = _norm_name(b.get("defendant_name"))
                iname = _norm_name(
                    b.get("indemnitor_name") or (b.get("indemnitor") or {}).get("name")
                )
                if role == "defendant" and name_q in dname:
                    filtered.append(b)
                elif role == "indemnitor" and name_q in iname:
                    filtered.append(b)
            bonds = filtered
        for b in bonds:
            for k in ("created_at", "updated_at", "last_checkin", "last_check_in"):
                if hasattr(b.get(k), "isoformat"):
                    b[k] = b[k].isoformat()
        return {
            "success": True,
            "query": {"name": name, "phone": phone, "role": role},
            "count": len(bonds),
            "bonds": bonds,
        }
    except Exception as e:
        logger.exception("by-person error: %s", e)
        return JSONResponse({"success": False, "error": str(e), "bonds": []}, status_code=500)


@bonds_bp.get("/active-bonds/relationship-graph")
async def api_relationship_graph(seed_booking: str = "", limit: int = 100):
    """Who-knows-who graph from active_bonds party data (shared bond / shared phone)."""
    active_bonds = get_collection("active_bonds")
    lim = max(1, min(int(limit or 100), 300))
    try:
        if seed_booking:
            seed = await active_bonds.find_one({"booking_number": seed_booking}, {"_id": 0})
            if not seed:
                return JSONResponse(
                    {"success": False, "error": "Seed bond not found"},
                    status_code=404,
                )
            phones, names = set(), set()
            for p in _extract_parties(seed):
                if p.get("phone"):
                    phones.add(p["phone"])
                if p.get("name"):
                    names.add(p["name"])
            or_clauses = []
            for ph in phones:
                or_clauses.append({"indemnitor_phone": {"$regex": ph}})
                or_clauses.append({"indemnitor.phone": {"$regex": ph}})
            for nm in names:
                token = nm.split(",")[0].strip()[:24]
                if len(token) >= 3:
                    or_clauses.append(
                        {"defendant_name": {"$regex": re.escape(token), "$options": "i"}}
                    )
                    or_clauses.append(
                        {"indemnitor_name": {"$regex": re.escape(token), "$options": "i"}}
                    )
            query = {"$or": or_clauses} if or_clauses else {"booking_number": seed_booking}
            bonds = await active_bonds.find(query, {"_id": 0}).limit(lim).to_list(lim)
            if not any(b.get("booking_number") == seed_booking for b in bonds):
                bonds.insert(0, seed)
        else:
            bonds = await (
                active_bonds.find({}, {"_id": 0}).sort("created_at", -1).limit(lim).to_list(lim)
            )

        nodes: dict = {}
        edges: list = []
        edge_keys: set = set()
        phone_index: dict = {}

        def node_id(role: str, name: str, phone: str) -> str:
            if phone:
                return f"p:{phone}"
            return f"n:{_norm_name(name)}:{role[0]}"

        def add_node(role: str, name: str, phone: str, booking: str):
            if not name and not phone:
                return None
            nid = node_id(role, name or "UNKNOWN", phone or "")
            if nid not in nodes:
                nodes[nid] = {
                    "id": nid,
                    "name": name or "(unknown)",
                    "phone": phone or "",
                    "roles": set(),
                    "bookings": set(),
                }
            nodes[nid]["roles"].add(role)
            nodes[nid]["bookings"].add(booking)
            return nid

        def add_edge(a: str, b: str, kind: str, booking: str):
            if not a or not b or a == b:
                return
            key = "|".join(sorted([a, b])) + f"|{kind}|{booking}"
            if key in edge_keys:
                return
            edge_keys.add(key)
            edges.append({
                "source": a, "target": b, "type": kind, "booking_number": booking,
            })

        for b in bonds:
            booking = b.get("booking_number") or ""
            party_ids = []
            for p in _extract_parties(b):
                nid = add_node(p["role"], p["name"], p["phone"], booking)
                if nid:
                    party_ids.append(nid)
                    if p["phone"]:
                        phone_index.setdefault(p["phone"], []).append(nid)
            for i, a in enumerate(party_ids):
                for c in party_ids[i + 1:]:
                    add_edge(a, c, "same_bond", booking)

        for ph, nids in phone_index.items():
            unique = list(dict.fromkeys(nids))
            for i, a in enumerate(unique):
                for c in unique[i + 1:]:
                    add_edge(a, c, "shared_phone", "")

        out_nodes = [{
            "id": n["id"],
            "name": n["name"],
            "phone": n["phone"],
            "roles": sorted(n["roles"]),
            "bond_count": len(n["bookings"]),
            "bookings": sorted(n["bookings"])[:20],
        } for n in nodes.values()]

        return {
            "success": True,
            "seed_booking": seed_booking or None,
            "node_count": len(out_nodes),
            "edge_count": len(edges),
            "nodes": out_nodes,
            "edges": edges,
            "bond_count": len(bonds),
        }
    except Exception as e:
        logger.exception("relationship-graph error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@bonds_bp.post("/active-bonds")
async def api_active_bonds_create(request: Request):
    """Create a new active bond record."""
    data = await request.json() or {}
    active_bonds = get_collection("active_bonds")
    booking_number = data.get("booking_number", "")
    if not booking_number:
        return JSONResponse({"success": False, "error": "booking_number required"}, status_code=400)
    now = datetime.now(timezone.utc)
    doc = {
        "booking_number": booking_number,
        "defendant_name": data.get("defendant_name", ""),
        "defendant_phone": data.get("defendant_phone", ""),
        "defendant_address": data.get("defendant_address", ""),
        "defendant_dob": data.get("defendant_dob", ""),
        "booking_page_url": data.get("booking_page_url", ""),
        "county": data.get("county", ""),
        "facility": data.get("facility", ""),
        "bond_amount": data.get("bond_amount", 0),
        "premium": data.get("premium", 0),
        "insurance_company": data.get("insurance_company", "osi").upper(),
        "poa_number": data.get("poa_number", ""),
        "case_number": data.get("case_number", ""),
        "status": "active",
        "risk_score": data.get("risk_score", 0),
        "check_in_required": data.get("check_in_required", False),
        "check_in_frequency_days": data.get("check_in_frequency_days", 30),
        "last_checkin": None,
        "next_checkin_due": None,
        "indemnitor_name": data.get("indemnitor_name", ""),
        "indemnitor_phone": data.get("indemnitor_phone", ""),
        "indemnitor_email": data.get("indemnitor_email", ""),
        "indemnitor_relationship": data.get("indemnitor_relationship", ""),
        "ref1_name": data.get("ref1_name", ""),
        "ref1_phone": data.get("ref1_phone", ""),
        "ref2_name": data.get("ref2_name", ""),
        "ref2_phone": data.get("ref2_phone", ""),
        "dob": data.get("defendant_dob", ""),
        "address": data.get("defendant_address", ""),
        "detail_url": data.get("booking_page_url", ""),
        "email": data.get("defendant_email", ""),
        "indemnitor": {
            "name": data.get("indemnitor_name", ""),
            "phone": data.get("indemnitor_phone", ""),
            "email": data.get("indemnitor_email", ""),
            "relationship": data.get("indemnitor_relationship", ""),
            "ref1Name": data.get("ref1_name", ""),
            "ref1Phone": data.get("ref1_phone", ""),
            "ref2Name": data.get("ref2_name", ""),
            "ref2Phone": data.get("ref2_phone", ""),
        },
        "fta_risk_score": data.get("fta_risk_score"),
        "fta_risk_level": data.get("fta_risk_level", ""),
        "fta_risk_confidence": data.get("fta_risk_confidence"),
        "created_at": now,
        "updated_at": now,
    }
    try:
        await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": doc},
            upsert=True,
        )
        return {"success": True, "booking_number": booking_number}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@bonds_bp.post("/active-bonds/{booking_number}/check-in")
async def api_active_bond_check_in(request: Request, booking_number):
    """Record a defendant check-in (staff/manual)."""
    active_bonds = get_collection("active_bonds")
    data = await request.json() or {}
    now = datetime.now(timezone.utc)
    try:
        bond = await active_bonds.find_one({"booking_number": booking_number})
        if not bond:
            return JSONResponse({"success": False, "error": "Bond not found"}, status_code=404)
        freq_days = bond.get("check_in_frequency_days", 7)
        next_due = now + timedelta(days=freq_days)
        checkin_doc = {
            "booking_number": booking_number,
            "checkin_at": now,
            "method": data.get("method", "manual"),
            "location": data.get("location", ""),
            "notes": data.get("notes", ""),
            "gps_lat": data.get("gps_lat"),
            "gps_lon": data.get("gps_lon"),
            "status": "completed",
            "source": "staff_manual",
        }
        checkins = get_collection("bond_checkins")
        await checkins.insert_one(checkin_doc)
        await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "last_checkin": now,
                "last_check_in": now.isoformat(),
                "next_checkin_due": next_due,
                "next_check_in_due": next_due.isoformat(),
                "check_in_overdue": False,
                "updated_at": now,
            }},
        )
        return {
            "success": True,
            "booking_number": booking_number,
            "checked_in_at": now.isoformat(),
            "next_due": next_due.isoformat(),
        }
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@bonds_bp.post("/active-bonds/{booking_number}/enable-checkin")
async def api_enable_checkin_monitoring(request: Request, booking_number: str):
    """
    Staff: enable transparent check-in monitoring (A+C) + Traccar device.
    Creates defendant portal link + CRM task. Does NOT auto-text the defendant.
    """
    data = await request.json() or {}
    try:
        from dashboard.services.checkin_enrollment_service import enable_checkin_monitoring
        result = await enable_checkin_monitoring(
            booking_number,
            frequency_days=int(data.get("frequency_days") or 7),
            source=data.get("source") or "staff",
            actor=data.get("actor") or data.get("agent") or "staff",
            create_staff_task=data.get("create_staff_task", True),
            force_new_token=bool(data.get("force_new_token")),
            provision_traccar=data.get("provision_traccar", True),
            continuous_gps=bool(data.get("continuous_gps")),
        )
        if not result.get("success"):
            return JSONResponse(result, status_code=404)
        return result
    except Exception as e:
        logger.exception("enable-checkin failed for %s", booking_number)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@bonds_bp.post("/active-bonds/{booking_number}/provision-traccar")
async def api_provision_traccar(request: Request, booking_number: str):
    """
    Staff: ensure Traccar device for bond (in-stack GPS).

    continuous_gps=true → also creates install task for Traccar Client app.
    """
    data = await request.json() or {}
    try:
        from dashboard.services.checkin_enrollment_service import (
            provision_traccar_device,
            enable_checkin_monitoring,
        )
        continuous = bool(data.get("continuous_gps") or data.get("continuous"))
        actor = data.get("actor") or data.get("agent") or "staff"

        # Ensure check-in monitoring is on when enabling continuous GPS
        if continuous or data.get("enable_checkin", True):
            await enable_checkin_monitoring(
                booking_number,
                source="staff_traccar",
                actor=actor,
                create_staff_task=False,
                provision_traccar=False,
                continuous_gps=continuous,
            )

        active_bonds = get_collection("active_bonds")
        bond = await active_bonds.find_one({"booking_number": booking_number}) or {}
        result = await provision_traccar_device(
            booking_number,
            defendant_name=bond.get("defendant_name") or data.get("defendant_name") or "",
            county=bond.get("county") or data.get("county") or "",
            phone=(data.get("phone") or bond.get("defendant_phone") or "")[:32],
            continuous=continuous,
            actor=actor,
        )
        if continuous and result.get("success"):
            try:
                from dashboard.services.task_engine import TaskEngine
                setup = result.get("setup") or {}
                await TaskEngine.create_task(
                    booking_number=booking_number,
                    title="Install Traccar Client (continuous GPS)",
                    description=(
                        f"Device ID: {result.get('unique_id')}. "
                        f"{setup.get('instructions', '')}"
                    ),
                    due_date=datetime.now(timezone.utc),
                    task_type="traccar_install",
                )
            except Exception as te:
                logger.warning("traccar_install task: %s", te)
        status = 200 if result.get("success") else 502
        return JSONResponse(result, status_code=status)
    except Exception as e:
        logger.exception("provision-traccar failed for %s", booking_number)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@bonds_bp.post("/active-bonds/{booking_number}/send-checkin-link")
async def api_send_checkin_link(request: Request, booking_number: str):
    """
    Staff-gated: send defendant check-in portal link via iMessage/SMS.
    Requires phone — never invents a number. Human-in-the-loop only.
    """
    data = await request.json() or {}
    phone = (data.get("phone") or "").strip()
    if not phone:
        return JSONResponse(
            {"success": False, "error": "phone is required (validated defendant number)"},
            status_code=400,
        )
    try:
        from dashboard.services.checkin_enrollment_service import send_checkin_link
        result = await send_checkin_link(
            booking_number,
            phone=phone,
            actor=data.get("actor") or data.get("agent") or "staff",
            channel=data.get("channel") or "imessage",
        )
        status = 200 if result.get("success") else 400
        return JSONResponse(result, status_code=status)
    except Exception as e:
        logger.exception("send-checkin-link failed for %s", booking_number)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@bonds_bp.get("/monitoring-conditions")
async def api_monitoring_conditions():
    """Return standard bond check-in condition language for CRM / paperwork."""
    from dashboard.services.checkin_enrollment_service import get_condition_language
    return get_condition_language()


@bonds_bp.post("/active-bonds/{booking_number}/alert")
async def api_active_bond_alert(request: Request, booking_number):
    """Create a risk alert for an active bond."""
    active_bonds = get_collection("active_bonds")
    data = await request.json() or {}
    now = datetime.now(timezone.utc)
    alert = {
        "booking_number": booking_number,
        "alert_type": data.get("alert_type", "manual"),
        "severity": data.get("severity", "medium"),
        "message": data.get("message", ""),
        "created_at": now,
    }
    try:
        alerts = get_collection("bond_alerts")
        await alerts.insert_one(alert)
        await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": {"last_alert": now, "updated_at": now}, "$inc": {"alert_count": 1}},
        )
        return {"success": True, "booking_number": booking_number, "alert_type": alert["alert_type"]}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@bonds_bp.patch("/active-bonds/{booking_number}/status")
async def api_active_bond_status(request: Request, booking_number):
    """Update bond status with full audit trail, status_history tracking, and POA lifecycle.

    Valid statuses: active | monitoring | alert | exonerated | forfeited | surrendered | reinstated
    """
    from dashboard.services.state_machine import BondStateMachine
    
    data = await request.json() or {}
    new_status = data.get("status", "")
    agent = data.get("agent", "Dashboard")
    note = data.get("note", "")
    
    valid_statuses = {"active", "monitoring", "alert", "exonerated", "forfeited", "surrendered", "reinstated"}
    if new_status not in valid_statuses:
        return JSONResponse({"success": False, "error": f"Invalid status. Must be one of: {sorted(valid_statuses)}"}, status_code=400)
        
    try:
        await BondStateMachine.transition_bond(
            booking_number=booking_number,
            new_status=new_status,
            actor=agent,
            reason=note
        )
        return {
            "success": True,
            "status": new_status,
            "note": note
        }
    except ValueError as ve:
        return JSONResponse({"success": False, "error": str(ve)}, status_code=400)
    except Exception as e:
        logger.exception(f"Error transitioning bond status for {booking_number}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@bonds_bp.get("/active-bonds/{booking_number}/status-history")
async def api_active_bond_status_history(booking_number):
    """Return the full status_history array for a bond, newest first."""
    active_bonds = get_collection("active_bonds")
    try:
        bond = await active_bonds.find_one(
            {"booking_number": booking_number},
            {"_id": 0, "status_history": 1, "status": 1, "defendant_name": 1},
        )
        if not bond:
            return JSONResponse({"success": False, "error": "Bond not found"}, status_code=404)
        history = list(reversed(bond.get("status_history", [])))
        return {
            "success": True,
            "booking_number": booking_number,
            "defendant_name": bond.get("defendant_name", ""),
            "current_status": bond.get("status", "active"),
            "history": history,
            "total": len(history),
        }
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@bonds_bp.post("/active-bonds/missed-checkins")
async def api_active_bonds_process_missed():
    """Scan for missed check-ins and create alerts."""
    active_bonds = get_collection("active_bonds")
    now = datetime.now(timezone.utc)
    try:
        cursor = active_bonds.find({
            "status": "active",
            "check_in_required": True,
            "next_checkin_due": {"$lt": now},
        }, {"_id": 0})
        overdue = await cursor.to_list(length=500)
        alerts = get_collection("bond_alerts")
        alert_docs = []
        for bond in overdue:
            alert_docs.append({
                "booking_number": bond["booking_number"],
                "alert_type": "missed_checkin",
                "severity": "high",
                "message": f"Missed check-in — due {bond.get('next_checkin_due')}",
                "created_at": now,
            })
        if alert_docs:
            await alerts.insert_many(alert_docs)
        return {
            "success": True,
            "overdue_count": len(overdue),
            "alerts_created": len(alert_docs),
        }
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ═══════════════════════════════════════════════════════════════════════════════
# APPEARANCE BOND PDF
# ═══════════════════════════════════════════════════════════════════════════════

def _build_appearance_bond_data(d: dict):
    """
    Normalize a (preferably hydrated) request dict into bond_data for PDF fill.

    Returns:
      (b_data: dict, error: Optional[str])
    """
    d = d or {}
    surety = (d.get("surety") or "osi").lower().strip()
    if surety not in ("osi", "palmetto"):
        surety = "osi"

    charges_data = (
        d.get("charge_details")
        or d.get("charge_list")
        or d.get("charges")
        or d.get("charge")
        or []
    )
    if isinstance(charges_data, str):
        parts = [c.strip() for c in re.split(r"[|\n;]", charges_data) if c.strip()]
        if not parts and charges_data.strip():
            parts = [charges_data.strip()]
        charges_data = [{"charge": p} for p in parts]
    elif not isinstance(charges_data, list):
        charges_data = []

    booking_for_case = str(d.get("booking") or d.get("booking_number") or "").strip()
    top_case = str(d.get("case_number") or "").strip()
    if _appearance_case_is_booking(top_case, booking_for_case):
        top_case = ""

    charge_details = []
    for ch in charges_data:
        if not isinstance(ch, dict):
            ch = {"charge": str(ch)}
        c_case = str(
            ch.get("case_number")
            or ch.get("appearance_bond_number")
            or top_case
            or ""
        ).strip()
        if _appearance_case_is_booking(c_case, booking_for_case):
            c_case = top_case
        c_charge = ch.get("charge") or ch.get("description") or ch.get("name") or ""
        if _appearance_charge_is_placeholder(c_charge):
            c_charge = ""
        c_court = str(ch.get("court_date") or d.get("court_date") or "").strip()
        if not c_court or c_court.upper() in ("TBN", "TBD"):
            c_court = str(d.get("court_date") or "TBN").strip() or "TBN"
        charge_details.append({
            "charge": c_charge,
            "bond_amount": ch.get(
                "bond_amount",
                ch.get("amount", ch.get("bond", d.get("bond") or d.get("bond_amount") or 0)),
            ),
            "case_number": c_case,
            "poa_number": (
                ch.get("poa_number")
                or ch.get("poa_full")
                or ch.get("POA_Number")
                or ""
            ),
            "bond_type": ch.get("bond_type") or "Surety",
            "court_date": c_court,
            "court_time": ch.get("court_time") or d.get("court_time") or "",
            "county": ch.get("county") or d.get("county") or "",
        })

    poa_list = d.get("poa_numbers") or []
    if isinstance(poa_list, str):
        poa_list = [x.strip() for x in poa_list.split(",") if x.strip()]
    if isinstance(poa_list, list):
        for i, row in enumerate(charge_details):
            if not row.get("poa_number") and i < len(poa_list):
                p = poa_list[i]
                if isinstance(p, dict):
                    row["poa_number"] = p.get("poa_full") or p.get("poa_number") or ""
                else:
                    row["poa_number"] = str(p)

    if d.get("poa_number") and charge_details and not charge_details[0].get("poa_number"):
        charge_details[0]["poa_number"] = d.get("poa_number")

    charge_details = [c for c in charge_details if (c.get("charge") or "").strip()]
    if not charge_details and d.get("charges"):
        parts = [c.strip() for c in re.split(r"[|\n;]", str(d.get("charges"))) if c.strip()]
        for p in parts:
            if _appearance_charge_is_placeholder(p):
                continue
            charge_details.append({
                "charge": p,
                "bond_amount": d.get("bond_amount") or d.get("bond") or 0,
                "case_number": top_case,
                "poa_number": d.get("poa_number") or "",
                "bond_type": "Surety",
                "court_date": d.get("court_date") or "TBN",
                "court_time": d.get("court_time") or "",
                "county": d.get("county") or "",
            })

    # Single-charge GET often sends only `charge` — already handled above
    if not charge_details:
        return {}, "No charges provided — send charge_details[], charges, or booking with arrest data"

    # Clean top-level case
    case_number = str(d.get("case_number") or top_case or "").strip()
    if _appearance_case_is_booking(case_number, booking_for_case):
        case_number = top_case or next(
            (c.get("case_number") for c in charge_details if c.get("case_number")),
            "",
        )

    b_data = {
        "name": d.get("name") or d.get("defendant_name") or "",
        "defendant_name": d.get("name") or d.get("defendant_name") or "",
        "first_name": d.get("first_name") or "",
        "last_name": d.get("last_name") or "",
        "booking_number": booking_for_case or d.get("booking") or d.get("booking_number") or "",
        "county": d.get("county") or "",
        "court_date": d.get("court_date") or "TBN",
        "court_time": d.get("court_time") or "",
        "court_type": d.get("court_type") or "",
        "surety": surety,
        "bond_date": d.get("date") or d.get("bond_date") or datetime.now().strftime("%m/%d/%Y"),
        "dob": d.get("dob") or d.get("date_of_birth") or "",
        "address": d.get("address") or d.get("defendant_address") or "",
        "indemnitor_name": d.get("indemnitor_name") or "",
        "charge_details": charge_details,
        "case_number": case_number,
        "bond_amount": d.get("bond_amount") or d.get("bond") or 0,
        "poa_numbers": [c.get("poa_number") for c in charge_details],
        "poa_number": d.get("poa_number") or (charge_details[0].get("poa_number") if charge_details else ""),
    }
    return b_data, None


@bonds_bp.api_route("/appearance-bond-pdf", methods=["GET", "POST"])
async def api_appearance_bond_pdf(request: Request):
    """
    Generate pre-populated Appearance Bond PDF(s) for print / wet-ink / jail.

    One form per charge. Stored as UNSIGNED files — never e-signed.
    Procedure: print → live wet-ink signature on paper → take to jail.

    Accepts GET query params or POST JSON body:
        name, booking, county, bond, charge(s), charge_details, surety, date, dob,
        address, court_date, court_time, case_number(s), poa_number(s), court_type,
        first_name, last_name, indemnitor_name, copies, uncollated, store

    Hydrates from Mongo arrest/lead records when booking is present so 1×/Edit
    buttons get the same charge/case/court fill as print-package.
    """
    try:
        from dashboard.bond_pdf_service import (
            generate_appearance_bonds,
            generate_safe_filename,
            merge_uncollated_bonds,
            store_appearance_bond_pdfs,
            appearance_bond_procedure_meta,
        )

        _qp = dict(request.query_params)
        d: dict = {}
        if request.method == "POST":
            try:
                d = await request.json() or {}
            except Exception:
                d = {}

        # Merge query params into body for hydrate (GET uses query only)
        for k, v in _qp.items():
            if k not in d or d.get(k) in (None, ""):
                d[k] = v

        # Alias common GET param names
        if d.get("booking") and not d.get("booking_number"):
            d["booking_number"] = d["booking"]
        if d.get("bond") and not d.get("bond_amount"):
            d["bond_amount"] = d["bond"]
        if d.get("charge") and not d.get("charges") and not d.get("charge_details"):
            d["charges"] = d["charge"]

        d = await _hydrate_appearance_bond_payload(d)
        data, err = _build_appearance_bond_data(d)
        if err:
            return JSONResponse(
                {"error": err, "hint": "Include booking_number so we can load arrest data"},
                status_code=400,
            )

        surety = data.get("surety") or "osi"
        try:
            copies_count = int(d.get("copies") or d.get("copies_per_charge") or "2")
        except (TypeError, ValueError):
            copies_count = 2
        if str(d.get("uncollated", "")).lower() in ("true", "1", "yes") or copies_count > 1:
            copies_count = max(2, copies_count)
        if copies_count < 1:
            copies_count = 1

        pdf_list = generate_appearance_bonds(data, template=surety)
        if not pdf_list:
            return JSONResponse({"error": "No appearance bond PDFs generated"}, status_code=400)

        # Persist unsigned print files (best-effort)
        stored = []
        if str(d.get("store", "1")).lower() not in ("0", "false", "no"):
            try:
                stored = store_appearance_bond_pdfs(
                    pdf_list,
                    bond_data=data,
                    surety=surety,
                    booking_number=data.get("booking_number"),
                )
            except Exception as store_exc:
                logger.warning("[appearance-bond-pdf] store failed: %s", store_exc)

        pdf_bytes = merge_uncollated_bonds(pdf_list, copies_per_charge=copies_count)
        filename = generate_safe_filename(data)
        # Make clear this is the print/unsigned package
        if "UNSIGNED" not in filename.upper():
            filename = filename.replace(".pdf", "_UNSIGNED_PRINT.pdf")

        proc = appearance_bond_procedure_meta()
        headers = {
            "Content-Disposition": f"attachment; filename={filename}",
            "X-Appearance-Bond-Print-Only": "1",
            "X-Appearance-Bond-Signature": "wet_ink_live",
            "X-Appearance-Bond-Count": str(len(pdf_list)),
            "X-Appearance-Bond-Charge": (data.get("charge_details") or [{}])[0].get("charge", "")[:120],
            "X-Appearance-Bond-Case": str(
                (data.get("charge_details") or [{}])[0].get("case_number")
                or data.get("case_number")
                or ""
            )[:80],
        }
        if stored:
            headers["X-Appearance-Bond-Stored"] = stored[0].get("file_path", "")[:200]

        return Response(
            pdf_bytes,
            media_type="application/pdf",
            headers=headers,
        )
    except FileNotFoundError as e:
        return JSONResponse({
            "error": (
                f"Template not found: {str(e)}. "
                "Ensure templates are in templates/osi/ or templates/palmetto/."
            ),
        }, status_code=404)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"error": f"PDF generation failed: {str(e)}"}, status_code=500)


@bonds_bp.api_route("/appearance-bond-batch", methods=["POST"])
async def api_appearance_bond_batch(request: Request):
    """
    Print-ready package: one appearance bond per charge, uncollated copies
    (default 2×: Court + Agency). UNSIGNED files for wet-ink → jail.
    Never e-signed. Hydrates arrest data when booking is present.
    """
    try:
        from dashboard.bond_pdf_service import (
            generate_appearance_bonds,
            merge_uncollated_bonds,
            store_appearance_bond_pdfs,
        )
        d = await request.json() or {}
        d = await _hydrate_appearance_bond_payload(d)
        b_data, err = _build_appearance_bond_data(d)
        if err:
            return JSONResponse({"error": err}, status_code=400)
        surety = b_data.get("surety") or "osi"
        try:
            copies = int(d.get("copies", 2) or 2)
        except (TypeError, ValueError):
            copies = 2
        if copies < 1:
            copies = 1

        pdf_list = generate_appearance_bonds(b_data, template=surety)

        if not pdf_list:
            return JSONResponse({"error": "No appearance bond PDFs were generated from the provided charges"}, status_code=400)

        try:
            store_appearance_bond_pdfs(
                pdf_list,
                bond_data=b_data,
                surety=surety,
                booking_number=b_data.get("booking_number"),
            )
        except Exception as store_exc:
            logger.warning("[appearance-bond-batch] store failed: %s", store_exc)

        merged_pdf = merge_uncollated_bonds(pdf_list, copies_per_charge=copies)
        safe_name = re.sub(r'[^A-Za-z0-9_-]', '_', d.get("name", "defendant"))
        filename = f"Uncollated_Appearance_Bonds_{surety.upper()}_{safe_name}_UNSIGNED_PRINT.pdf"

        # Automatic Filing to Google Drive Case Folder (best-effort; never blocks PDF download)
        drive_url = ""
        try:
            from dashboard.services.google_drive_service import GoogleDriveService
            drive_service = GoogleDriveService()
            if drive_service.is_configured:
                root_folder_id = os.getenv("GOOGLE_DRIVE_CASES_FOLDER_ID", "root")
                surety_label = "OSI Appearance Bonds" if surety.lower() == "osi" else "Palmetto Appearance Bonds"
                surety_folder_id = drive_service.get_or_create_folder(surety_label, root_folder_id)
                if surety_folder_id:
                    def_folder_name = f"{safe_name}_{d.get('booking', 'no_bk')}"
                    def_folder_id = drive_service.get_or_create_folder(def_folder_name, surety_folder_id)
                    if def_folder_id:
                        drive_url = drive_service.upload_pdf(merged_pdf, filename, def_folder_id) or ""
                        if drive_url:
                            logger.info(f"[DriveFiling] Automatically filed {filename} to Google Drive: {drive_url}")

                            # Update DB record with drive link when an active bond exists
                            bk_num = d.get("booking", "")
                            if bk_num:
                                db = get_db()
                                await db.active_bonds.update_many(
                                    {"$or": [{"booking_number": bk_num}, {"BookingNumber": bk_num}]},
                                    {"$set": {
                                        "drive_link": drive_url,
                                        "drive_folder_id": def_folder_id,
                                        "filed_to_drive": True,
                                        "updated_at": datetime.now(timezone.utc).isoformat()
                                    }}
                                )
                    else:
                        logger.warning("[DriveFiling] Could not create defendant folder — skipping upload")
                else:
                    logger.warning("[DriveFiling] Could not create surety folder — skipping upload")
        except Exception as drive_err:
            logger.warning(f"[DriveFiling] Drive filing skipped/failed: {drive_err}")

        headers = {
            "Content-Disposition": f"attachment; filename={filename}",
            "Access-Control-Expose-Headers": "x-drive-url",
            "x-drive-url": drive_url or ""
        }

        return Response(
            merged_pdf,
            media_type="application/pdf",
            headers=headers,
        )
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"error": f"Batch PDF generation failed: {str(e)}"}, status_code=500)





def _appearance_charge_is_placeholder(text) -> bool:
    """Delegate to bond_pdf_service (single source of truth)."""
    from dashboard.bond_pdf_service import _is_placeholder_charge
    return _is_placeholder_charge(text)


def _appearance_case_is_booking(case_number, booking) -> bool:
    """Delegate to bond_pdf_service (single source of truth)."""
    from dashboard.bond_pdf_service import _is_booking_as_case
    return _is_booking_as_case(case_number, booking)


def _merge_charge_rows_with_db(req_details: list, db_cd: list, booking: str, out: dict) -> list:
    """
    Patch modal charge rows with Mongo data without losing agent POAs/amounts.

    For each request row:
      - Replace placeholder charge text from DB
      - Replace booking-as-case with real court case #
      - Replace TBN court date/time when DB/top-level has a real hearing
    If request rows are all placeholders and DB has rows, rebuild from DB
    then re-apply POAs/amounts from the request by index.
    """
    if not isinstance(req_details, list):
        req_details = []
    if not isinstance(db_cd, list):
        db_cd = []

    lead_case = str(out.get("case_number") or "").strip()
    if _appearance_case_is_booking(lead_case, booking):
        lead_case = ""

    all_placeholder = bool(req_details) and all(
        _appearance_charge_is_placeholder(
            (r.get("charge") if isinstance(r, dict) else r)
        )
        for r in req_details
    )

    # Rebuild from DB when every modal charge is a placeholder
    if all_placeholder and db_cd:
        merged = []
        for i, src in enumerate(db_cd):
            if not isinstance(src, dict):
                continue
            req = req_details[i] if i < len(req_details) and isinstance(req_details[i], dict) else {}
            charge = src.get("charge") or src.get("description") or ""
            if _appearance_charge_is_placeholder(charge):
                continue
            case_num = str(src.get("case_number") or lead_case or "").strip()
            if _appearance_case_is_booking(case_num, booking):
                case_num = lead_case
            court_date = str(
                src.get("court_date") or out.get("court_date") or "TBN"
            ).strip() or "TBN"
            court_time = str(
                src.get("court_time") or out.get("court_time") or ""
            ).strip()
            merged.append({
                "charge": charge,
                "bond_amount": req.get("bond_amount", src.get("bond_amount", src.get("amount", 0))),
                "case_number": case_num,
                "poa_number": req.get("poa_number") or req.get("poa_full") or "",
                "bond_type": req.get("bond_type") or src.get("bond_type") or "Surety",
                "court_date": court_date,
                "court_time": court_time,
                "county": req.get("county") or src.get("county") or out.get("county") or "",
            })
        return merged if merged else req_details

    # Row-by-row patch (preserve agent edits)
    patched = []
    for i, r in enumerate(req_details):
        if not isinstance(r, dict):
            r = {"charge": str(r)}
        else:
            r = dict(r)
        src = {}
        if i < len(db_cd) and isinstance(db_cd[i], dict):
            src = db_cd[i]
        elif db_cd and isinstance(db_cd[0], dict):
            src = db_cd[0]

        if _appearance_charge_is_placeholder(r.get("charge")):
            alt = src.get("charge") or src.get("description") or ""
            if not _appearance_charge_is_placeholder(alt):
                r["charge"] = alt

        if _appearance_case_is_booking(r.get("case_number"), booking):
            r["case_number"] = (
                src.get("case_number")
                or lead_case
                or ""
            )
            if _appearance_case_is_booking(r.get("case_number"), booking):
                r["case_number"] = lead_case or ""

        cd = str(r.get("court_date") or "").strip()
        if not cd or cd.upper() in ("TBN", "TBD"):
            r["court_date"] = (
                src.get("court_date")
                or out.get("court_date")
                or "TBN"
            )
        if not str(r.get("court_time") or "").strip():
            r["court_time"] = src.get("court_time") or out.get("court_time") or ""

        if not r.get("county"):
            r["county"] = src.get("county") or out.get("county") or ""

        patched.append(r)
    return patched


async def _hydrate_appearance_bond_payload(d: dict) -> dict:
    """
    Merge request body with arrest/lead/active_bond records when booking is known.
    Ensures print package auto-fills name, address, DOB, charges, amounts, etc.

    Replaces modal *placeholders* (Unspecified Charge, booking-as-case, TBN when
    Mongo has a real court date) so bad defaults never win over scraped data.
    """
    out = dict(d or {})
    booking = (
        out.get("booking")
        or out.get("booking_number")
        or out.get("Booking_Number")
        or ""
    )
    booking = str(booking).strip()
    if not booking:
        return out

    try:
        lead = await get_collection("arrests").find_one(
            {"$or": [{"Booking_Number": booking}, {"booking_number": booking}]},
            {"_id": 0},
        )
        if not lead:
            lead = await get_collection("prospective_bonds").find_one(
                {"$or": [{"booking_number": booking}, {"Booking_Number": booking}]},
                {"_id": 0},
            ) or {}
        ab = await get_collection("active_bonds").find_one(
            {"$or": [{"booking_number": booking}, {"BookingNumber": booking}]},
            {"_id": 0},
        ) or {}

        def _pick(*keys, default="", prefer_db=False):
            """prefer_db=True: lead/active_bond win over empty/placeholder request values."""
            sources = (lead or {}, ab, out) if prefer_db else (out, ab, lead or {})
            for src in sources:
                if not isinstance(src, dict):
                    continue
                for k in keys:
                    v = src.get(k)
                    if v is not None and str(v).strip() != "":
                        return v
            return default

        out.setdefault("booking", booking)
        out.setdefault("booking_number", booking)
        out["name"] = _pick("name", "defendant_name", "Full_Name", "full_name", default=out.get("name", ""))
        out["defendant_name"] = out["name"]
        out["county"] = _pick("county", "County", default=out.get("county", ""))
        out["address"] = _pick(
            "address", "defendant_address", "Address", "Home_Address",
            default=out.get("address", ""),
        )
        out["dob"] = _pick("dob", "date_of_birth", "DOB", "Date_of_Birth", default=out.get("dob", ""))

        # Court date: prefer Mongo when request is missing/TBN
        req_cd = str(out.get("court_date") or "").strip()
        if not req_cd or req_cd.upper() in ("TBN", "TBD"):
            out["court_date"] = _pick(
                "court_date", "Court_Date", prefer_db=True, default=req_cd or "TBN",
            )
        else:
            out["court_date"] = req_cd
        req_ct = str(out.get("court_time") or "").strip()
        if not req_ct:
            out["court_time"] = _pick(
                "court_time", "Court_Time", prefer_db=True, default="",
            )
        out["court_type"] = _pick(
            "court_type", "Court_Type", prefer_db=True, default=out.get("court_type", ""),
        )

        # Case number: never keep booking number as case; prefer Mongo Case_Number
        req_case = str(out.get("case_number") or "").strip()
        if _appearance_case_is_booking(req_case, booking):
            out["case_number"] = _pick(
                "case_number", "Case_Number", prefer_db=True, default="",
            )
            if _appearance_case_is_booking(out.get("case_number"), booking):
                out["case_number"] = ""
        out["indemnitor_name"] = _pick(
            "indemnitor_name", "Indemnitor_Name", default=out.get("indemnitor_name", "")
        )
        if not out.get("bond") and not out.get("bond_amount"):
            out["bond_amount"] = _pick("bond_amount", "Bond_Amount", "bond", default=0)
            out["bond"] = out["bond_amount"]

        # Charge details: patch modal placeholders from Mongo (keep agent POAs)
        db_cd = (
            (lead or {}).get("charge_details")
            or (lead or {}).get("Charge_Details")
            or (ab or {}).get("charge_details")
            or []
        )
        if not isinstance(db_cd, list):
            db_cd = []
        req_details = out.get("charge_details") or out.get("charge_list") or []
        if not isinstance(req_details, list):
            req_details = []

        if req_details or db_cd:
            # Always run merge when either side has structured rows
            if req_details and db_cd:
                out["charge_details"] = _merge_charge_rows_with_db(
                    req_details, db_cd, booking, out,
                )
                out.pop("charge_list", None)
            elif not req_details and db_cd:
                out["charge_details"] = db_cd
                out.pop("charge_list", None)
            elif req_details and not db_cd:
                # Still scrub booking-as-case / TBN using top-level out fields
                out["charge_details"] = _merge_charge_rows_with_db(
                    req_details, [], booking, out,
                )
                out.pop("charge_list", None)

        # Fall back to free-text charges from arrest record when still empty
        effective = out.get("charge_details") or out.get("charge_list") or []
        has_real_charge = False
        if isinstance(effective, list):
            for r in effective:
                ch = r.get("charge") if isinstance(r, dict) else r
                if not _appearance_charge_is_placeholder(ch):
                    has_real_charge = True
                    break
        if not has_real_charge and not out.get("charges"):
            ch = _pick("charges", "Charges", "charge", prefer_db=True, default="")
            if ch and not _appearance_charge_is_placeholder(ch):
                out["charges"] = ch
                # Drop empty/placeholder structured rows so normalize uses charges string
                out.pop("charge_details", None)
                out.pop("charge_list", None)

        # Top-level case_number from lead when still empty
        if not str(out.get("case_number") or "").strip():
            # Prefer first charge_details case
            for r in (out.get("charge_details") or []):
                if isinstance(r, dict) and r.get("case_number") and not _appearance_case_is_booking(
                    r.get("case_number"), booking
                ):
                    out["case_number"] = r["case_number"]
                    break
            if not str(out.get("case_number") or "").strip():
                out["case_number"] = _pick(
                    "case_number", "Case_Number", prefer_db=True, default="",
                )

        if not out.get("poa_numbers") and not out.get("poa_number"):
            poas = (ab or {}).get("poa_numbers") or (ab or {}).get("POA_Numbers")
            if poas:
                out["poa_numbers"] = poas
            else:
                one = (ab or {}).get("poa_number") or (ab or {}).get("POA_Number")
                if one:
                    out["poa_number"] = one

        surety = (
            out.get("surety")
            or (ab or {}).get("surety_id")
            or (ab or {}).get("insurance_company")
            or "osi"
        )
        out["surety"] = str(surety).lower().strip()
        if out["surety"] not in ("osi", "palmetto"):
            out["surety"] = "osi"
    except Exception as exc:
        logger.warning("[appearance-print] hydrate failed booking=%s: %s", booking, exc)
    return out


@bonds_bp.api_route("/appearance-bonds/print-package", methods=["POST"])
async def api_appearance_bonds_print_package(request: Request):
    """
    Canonical print button: 1 filled form per charge, uncollated merge,
    default 2 copies per charge (office file + jail). UNSIGNED / wet-ink only.
    """
    try:
        from dashboard.bond_pdf_service import (
            generate_appearance_bonds,
            merge_uncollated_bonds,
            store_appearance_bond_pdfs,
            describe_appearance_bonds,
            appearance_bond_procedure_meta,
        )

        d = await request.json() or {}
        d = await _hydrate_appearance_bond_payload(d)

        try:
            copies = int(d.get("copies") or d.get("copies_per_charge") or 2)
        except (TypeError, ValueError):
            copies = 2
        if copies < 1:
            copies = 1

        b_data, err = _build_appearance_bond_data(d)
        if err:
            return JSONResponse(
                {
                    "error": err,
                    "hint": "Send charge_details[] or charges, or booking_number with arrest data",
                },
                status_code=400,
            )
        surety = b_data.get("surety") or "osi"
        b_data["copies_per_charge"] = copies

        plan = describe_appearance_bonds(b_data)
        missing_poa = [p for p in plan if not p.get("poa_number")]
        missing_case = [p for p in plan if not p.get("case_number")]
        proc = appearance_bond_procedure_meta()

        dry_run = str(d.get("dry_run") or "").lower() in ("1", "true", "yes")
        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "surety": surety,
                "charge_count": len(plan),
                "copies_per_charge": copies,
                "page_estimate": len(plan) * copies,
                "plan": plan,
                "ready": len(missing_poa) == 0 and len(missing_case) == 0,
                "warnings": {
                    "missing_poa_indices": [p["charge_index"] for p in missing_poa],
                    "missing_case_indices": [p["charge_index"] for p in missing_case],
                },
                "procedure": proc,
                "message": (
                    f"{len(plan)} charge(s) × {copies} copies = {len(plan) * copies} pages · "
                    "unsigned print / wet-ink / jail"
                ),
            }

        pdf_list = generate_appearance_bonds(b_data, template=surety)
        if not pdf_list:
            return JSONResponse({"error": "No appearance bond PDFs generated"}, status_code=400)

        stored = []
        try:
            stored = store_appearance_bond_pdfs(
                pdf_list,
                bond_data=b_data,
                surety=surety,
                packet_id=d.get("packet_id"),
                booking_number=b_data.get("booking_number"),
            )
        except Exception as store_exc:
            logger.warning("[print-package] store failed: %s", store_exc)

        merged_pdf = merge_uncollated_bonds(pdf_list, copies_per_charge=copies)
        safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", b_data.get("name") or "defendant")[:40]
        n = len(pdf_list)
        filename = (
            f"AppearanceBonds_{surety.upper()}_{safe_name}_"
            f"{n}ch_x{copies}_UNSIGNED_PRINT.pdf"
        )

        drive_url = ""
        try:
            from dashboard.services.google_drive_service import GoogleDriveService
            drive_service = GoogleDriveService()
            if drive_service.is_configured:
                root_folder_id = (
                    os.getenv("COMPLETED_BONDS_FOLDER_ID")
                    or os.getenv("GOOGLE_DRIVE_CASES_FOLDER_ID")
                    or "root"
                )
                surety_label = (
                    "OSI Appearance Bonds" if surety == "osi" else "Palmetto Appearance Bonds"
                )
                surety_folder_id = drive_service.get_or_create_folder(surety_label, root_folder_id)
                if surety_folder_id:
                    def_folder = drive_service.get_or_create_folder(
                        f"{safe_name}_{b_data.get('booking_number') or 'nobk'}",
                        surety_folder_id,
                    )
                    if def_folder:
                        drive_url = drive_service.upload_pdf(merged_pdf, filename, def_folder) or ""
        except Exception as drive_err:
            logger.warning("[print-package] drive skip: %s", drive_err)

        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": (
                "x-drive-url, x-charge-count, x-copies-per-charge, "
                "x-appearance-bond-print-only, x-missing-poa, x-missing-case"
            ),
            "X-Appearance-Bond-Print-Only": "1",
            "X-Appearance-Bond-Signature": "wet_ink_live",
            "X-Charge-Count": str(n),
            "X-Copies-Per-Charge": str(copies),
            "X-Page-Estimate": str(n * copies),
            "X-Missing-Poa": ",".join(str(p["charge_index"]) for p in missing_poa),
            "X-Missing-Case": ",".join(str(p["charge_index"]) for p in missing_case),
            "x-drive-url": drive_url or "",
        }
        if stored:
            headers["X-Stored-Count"] = str(len(stored))

        logger.info(
            "[print-package] surety=%s charges=%s copies=%s pages=%s booking=%s missing_poa=%s",
            surety, n, copies, n * copies, b_data.get("booking_number"), len(missing_poa),
        )
        return Response(merged_pdf, media_type="application/pdf", headers=headers)

    except FileNotFoundError as e:
        return JSONResponse(
            {
                "error": f"Template not found: {e}",
                "hint": "Ensure templates/osi/Appearance Bond blank.pdf is deployed",
            },
            status_code=404,
        )
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"error": f"Print package failed: {e}"}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/active-bonds/<booking_number>/release
# Mark defendant as released and trigger Phase 2 signing flow via BlueBubbles
# ─────────────────────────────────────────────────────────────────────────────

@bonds_bp.patch("/active-bonds/{booking_number}/edit")
async def api_active_bond_edit(request: Request, booking_number: str):
    """
    Full-field edit of an active bond record.
    Accepts any subset of editable fields and updates only those provided.
    """
    active_bonds = get_collection("active_bonds")
    data = await request.json() or {}
    EDITABLE = [
        "defendant_name", "defendant_phone", "defendant_address", "defendant_dob",
        "defendant_email", "county", "facility", "bond_amount", "premium",
        "insurance_company", "poa_number", "case_number", "check_in_required",
        "check_in_frequency_days", "indemnitor_name", "indemnitor_phone",
        "indemnitor_email", "indemnitor_relationship", "agent_name", "notes",
        "court_date", "court_location", "charges", "booking_page_url",
        "ref1_name", "ref1_phone", "ref2_name", "ref2_phone",
    ]
    updates = {k: data[k] for k in EDITABLE if k in data}
    if not updates:
        return JSONResponse({"success": False, "error": "No editable fields provided"}, status_code=400)

    # ── Compatibility Aliases (Defendant) ──
    if "defendant_dob" in updates and updates["defendant_dob"]:
        updates["dob"] = updates["defendant_dob"]
    if "defendant_address" in updates and updates["defendant_address"]:
        updates["address"] = updates["defendant_address"]
    if "defendant_email" in updates and updates["defendant_email"]:
        updates["email"] = updates["defendant_email"]
    if "booking_page_url" in updates and updates["booking_page_url"]:
        updates["detail_url"] = updates["booking_page_url"]

    # ── Keep nested indemnitor{} in sync for UI consumers that read bond.indemnitor ──
    if any(k.startswith("indemnitor_") or k.startswith("ref") for k in updates):
        existing = await active_bonds.find_one({"booking_number": booking_number}, {"indemnitor": 1})
        indem = dict((existing or {}).get("indemnitor") or {})
        if "indemnitor_name" in updates:
            indem["name"] = updates["indemnitor_name"]
        if "indemnitor_phone" in updates:
            indem["phone"] = updates["indemnitor_phone"]
        if "indemnitor_email" in updates:
            indem["email"] = updates["indemnitor_email"]
        if "indemnitor_relationship" in updates:
            indem["relationship"] = updates["indemnitor_relationship"]

        # Sync references into nested indemnitor object for paperwork compatibility
        for i in (1, 2):
            name_k, phone_k = f"ref{i}_name", f"ref{i}_phone"
            if name_k in updates:
                indem[f"ref{i}Name"] = updates[name_k]
            if phone_k in updates:
                indem[f"ref{i}Phone"] = updates[phone_k]

        updates["indemnitor"] = indem
    updates["updated_at"] = datetime.now(timezone.utc)
    try:
        result = await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": updates},
        )
        if result.matched_count == 0:
            return JSONResponse({"success": False, "error": "Bond not found"}, status_code=404)
        try:
            audit = get_collection("audit_events")
            await audit.insert_one({
                "entity_id": booking_number,
                "event_type": "bond_edited",
                "fields_changed": list(updates.keys()),
                "agent": data.get("agent", "Dashboard"),
                "timestamp": datetime.now(timezone.utc),
            })
        except Exception as _audit_err:
            logger.warning("[bonds] audit write failed for %s: %s", booking_number, _audit_err)
        # If court_date was updated, reschedule the Court Reminder compliance task
        if "court_date" in updates:
            try:
                from dashboard.services.task_engine import TaskEngine
                await TaskEngine.schedule_court_reminder(booking_number)
                logger.info(
                    "[bonds] Court reminder task rescheduled for %s after court_date edit",
                    booking_number,
                )
            except Exception as _te_err:
                logger.warning(
                    "[bonds] TaskEngine.schedule_court_reminder failed for %s: %s",
                    booking_number, _te_err,
                )
        return {"success": True, "booking_number": booking_number, "updated": list(updates.keys())}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@bonds_bp.post("/active-bonds/{booking_number}/release")
async def api_active_bond_release(request: Request, booking_number: str):
    """
    Mark a defendant as released from custody and trigger the post-release
    Phase 2 signing flow via BlueBubbles.

    Steps:
      1. Update bond case status to 'released' with released_at timestamp
      2. Send walk-out notification to indemnitor via BlueBubbles
      3. Generate Phase 2 SignNow signing link and send to indemnitor
      4. Log audit event

    Body (all optional — falls back to stored bond case values):
        {
            "released_at":       "2025-01-15T14:30:00Z",
            "release_facility":  "Lee County Jail",
            "next_court_date":   "2025-02-10",
            "court_location":    "Lee County Justice Center",
            "poa_number":        "OSI-12345",
            "agent_name":        "Brendan O'Neal",
            "agent_license":     "P123456",
            "surety_id":         "osi",
            "send_signing_link": true,
            "send_walkout_msg":  true
        }
    """
    active_bonds = get_collection("active_bonds")
    data = await request.json() or {}

    bond = await active_bonds.find_one({"booking_number": booking_number})
    if not bond:
        return JSONResponse({"success": False, "error": f"Bond case {booking_number} not found"}, status_code=404)

    now = datetime.now(timezone.utc)
    released_at = data.get("released_at", now.isoformat())
    release_facility = data.get("release_facility", bond.get("facility", ""))
    next_court_date = data.get("next_court_date", bond.get("next_court_date", "TBD"))
    court_location = data.get("court_location", bond.get("court_location", "Lee County Justice Center"))

    # 1. Update bond case status
    await active_bonds.update_one(
        {"booking_number": booking_number},
        {"$set": {
            "status": "released",
            "released_at": released_at,
            "release_facility": release_facility,
            "next_court_date": next_court_date,
            "court_location": court_location,
            "updated_at": now,
        }},
    )
    logger.info("[release] Bond %s marked released at %s", booking_number, released_at)

    defendant_name = bond.get("defendant_name", "your defendant")
    indemnitor_name = bond.get("indemnitor_name", "")
    indemnitor_phone = bond.get("indemnitor_phone", "")
    county = bond.get("county", "Lee")
    results = {
        "booking_number": booking_number,
        "released_at": released_at,
        "walkout_msg": None,
        "phase2_signing": None,
    }

    from dashboard.services.bb_client import send_message_universal

    # 2. Walk-out notification to indemnitor
    if data.get("send_walkout_msg", True) and indemnitor_phone:
        first_name = indemnitor_name.split()[0] if indemnitor_name else "there"
        walkout_msg = (
            f"Hi {first_name}! Great news — {defendant_name} has been released from "
            f"{county} County Jail \U0001f389\n\n"
            f"Remember: they MUST appear for ALL court dates. "
            f"Next date: {next_court_date} at {court_location}.\n\n"
            f"We'll send the remaining paperwork shortly. "
            f"— Shamrock Bail Bonds \U0001f340 (239) 332-2245"
        )
        walkout_result = await send_message_universal(indemnitor_phone, walkout_msg)
        results["walkout_msg"] = {
            "success": walkout_result.get("success"),
            "channel": walkout_result.get("channel"),
            "phone": indemnitor_phone,
        }
        from dashboard.routers.helpers import mask_phone
        logger.info("[release] Walk-out msg to %s: %s", mask_phone(indemnitor_phone), walkout_result.get("success"))

    # 3. Phase 2 SignNow packet + send link via BlueBubbles
    if data.get("send_signing_link", True):
        poa_number = data.get("poa_number", bond.get("poa_number", ""))
        agent_name = data.get("agent_name", os.getenv("DEFAULT_AGENT_NAME", "Brendan O'Neal"))
        agent_license = data.get("agent_license", os.getenv("DEFAULT_AGENT_LICENSE", ""))
        surety_id = data.get("surety_id", bond.get("insurance_company", "osi").lower())

        intake_doc = {
            "intake_id": booking_number,
            "booking_number": booking_number,
            "defendant_name": defendant_name,
            "defendant_first_name": bond.get("defendant_first_name", (defendant_name.split()[0] if defendant_name else "")),
            "defendant_last_name": bond.get("defendant_last_name", (defendant_name.split()[-1] if defendant_name else "")),
            "defendant_dob": bond.get("defendant_dob", ""),
            "defendant_address": bond.get("defendant_address", ""),
            "county": county,
            "facility": release_facility,
            "bond_amount": bond.get("bond_amount", 0),
            "premium": bond.get("premium", 0),
            "poa_number": poa_number,
            "agent_name": agent_name,
            "agent_license": agent_license,
            "surety_id": surety_id,
            "indemnitor_name": indemnitor_name,
            "indemnitor_phone": indemnitor_phone,
            "indemnitor_email": bond.get("indemnitor_email", ""),
            "next_court_date": next_court_date,
            "court_location": court_location,
            "phase": 2,
        }

        try:
            from dashboard.services.signnow_packet_service import SignNowPacketService
            svc = SignNowPacketService()
            packet_result = await svc.create_packet(
                intake_doc=intake_doc,
                packet_id=f"{booking_number}-phase2-release",
            )
            signing_link = packet_result.get("signing_link", "")
            results["phase2_signing"] = {
                "success": bool(signing_link),
                "signing_link": signing_link,
                "invite_id": packet_result.get("invite_id"),
                "group_id": packet_result.get("group_id"),
            }

            if signing_link and indemnitor_phone:
                first_name = indemnitor_name.split()[0] if indemnitor_name else "there"
                sign_msg = (
                    f"Hi {first_name}! Now that {defendant_name} has been released, "
                    f"please complete the remaining bond documents \U0001f4dd\n\n"
                    f"Tap to review and sign (~2 min):\n{signing_link}\n\n"
                    f"Questions? Call/text: (239) 332-2245 — Shamrock Bail Bonds \U0001f340"
                )
                sign_result = await send_message_universal(indemnitor_phone, sign_msg)
                results["phase2_signing"]["bb_sent"] = sign_result.get("success")
                results["phase2_signing"]["bb_channel"] = sign_result.get("channel")
                from dashboard.routers.helpers import mask_phone
                logger.info("[release] Phase 2 link sent to %s: %s", mask_phone(indemnitor_phone), sign_result.get("success"))

            await active_bonds.update_one(
                {"booking_number": booking_number},
                {"$set": {
                    "phase2_packet_sent": True,
                    "phase2_signing_link": signing_link,
                    "phase2_invite_id": packet_result.get("invite_id"),
                    "phase2_sent_at": now,
                    "updated_at": now,
                }},
            )
        except Exception as exc:
            logger.error("[release] Phase 2 SignNow error for %s: %s", booking_number, exc)
            results["phase2_signing"] = {"success": False, "error": str(exc)}

    # 4. Audit log
    try:
        audit_col = get_collection("audit_events")
        await audit_col.insert_one({
            "event_type": "defendant_released",
            "entity_id": booking_number,
            "entity_type": "bond_case",
            "defendant_name": defendant_name,
            "released_at": released_at,
            "walkout_sent": results["walkout_msg"],
            "phase2_sent": results["phase2_signing"],
            "timestamp": now,
        })
    except Exception as exc:
        logger.warning("[release] Audit log error: %s", exc)

    return {"success": True, **results}


@bonds_bp.post("/active-bonds/bulk-exonerate")
async def api_bulk_exonerate(request: Request):
    """
    Batch exonerate multiple bonds in one request.

    Body:
        {
            "booking_numbers": ["BK001", "BK002", ...],
            "source": "manual_bulk",
            "note": "Batch discharge from court email",
            "notify_indemnitors": false
        }

    Returns per-bond results with idempotency:
    - already_exonerated bonds are reported but not double-processed
    - POA is only released if status == "assigned" (safety guard)
    - Reminders are cancelled for each bond
    - Audit event written per bond
    - SSE bond_exonerated fired per bond
    """
@bonds_bp.post("/admin-pin-override")
async def api_admin_pin_override(request: Request):
    """
    Validates Admin PIN and records an admin override to allow bond posting
    even when paperwork is incomplete, promising completion within 24 hours.
    """
    _pin = os.getenv("DASHBOARD_PIN", "")
    data = await request.json() or {}
    pin_entered = str(data.get("pin", "")).strip()
    booking_number = str(data.get("booking_number", "")).strip()
    reason = str(data.get("reason", "Admin override for immediate bond posting")).strip()
    approved_by = str(data.get("approved_by", "Admin")).strip()

    if _pin and pin_entered != _pin:
        return JSONResponse({"success": False, "error": "Invalid Admin PIN"}, status_code=401)

    now = datetime.now(timezone.utc)
    deadline_24h = now + timedelta(hours=24)

    audit_col = get_collection("audit_events")
    override_event = {
        "event_id": str(uuid.uuid4()),
        "event_type": "admin_pin_override",
        "booking_number": booking_number,
        "approved_by": approved_by,
        "reason": reason,
        "status": "approved",
        "compliance_deadline_24h": deadline_24h.isoformat(),
        "created_at": now.isoformat()
    }
    await audit_col.insert_one(override_event)

    if booking_number:
        active_bonds = get_collection("active_bonds")
        await active_bonds.update_one(
            {"booking_number": booking_number},
            {
                "$set": {
                    "status": "ready",
                    "admin_pin_override": True,
                    "override_approved_by": approved_by,
                    "override_time": now.isoformat(),
                    "compliance_deadline_24h": deadline_24h.isoformat()
                }
            }
        )

    return JSONResponse({
        "success": True,
        "message": "Admin PIN verified. Bond approved for posting (24-hour compliance deadline logged).",
        "compliance_deadline_24h": deadline_24h.isoformat()
    })


    # ── Auth guard: X-Admin-Token must match DASHBOARD_PIN ──────────────────
    _pin = os.getenv("DASHBOARD_PIN", "")
    if _pin:
        token = request.headers.get("X-Admin-Token", "").strip()
        if token != _pin:
            return JSONResponse({"success": False, "error": "Unauthorized — X-Admin-Token required"}, status_code=401)
    # ────────────────────────────────────────────────────────────────────────
    active_bonds = get_collection("active_bonds")
    poa_inventory = get_collection("poa_inventory")
    court_reminders = get_collection("court_reminders")
    audit_col = get_collection("audit_events")
    now = datetime.now(timezone.utc)

    try:
        data = await request.json() or {}
        booking_numbers = data.get("booking_numbers", [])
        source = data.get("source", "manual_bulk")
        note = data.get("note", "")
        notify = data.get("notify_indemnitors", False)

        if not booking_numbers or not isinstance(booking_numbers, list):
            return JSONResponse({"success": False, "error": "booking_numbers list required"}, status_code=400)
        if len(booking_numbers) > 50:
            return JSONResponse({"success": False, "error": "Maximum 50 bonds per bulk request"}, status_code=400)

        results = []
        exonerated_count = 0
        already_done_count = 0
        not_found_count = 0

        for booking_number in booking_numbers:
            booking_number = str(booking_number).strip()
            if not booking_number:
                continue

            try:
                bond = await active_bonds.find_one({"booking_number": booking_number})
                if not bond:
                    not_found_count += 1
                    results.append({"booking_number": booking_number, "status": "not_found"})
                    continue

                # Idempotency: skip already exonerated
                if bond.get("status") == "exonerated":
                    already_done_count += 1
                    results.append({
                        "booking_number": booking_number,
                        "status": "already_exonerated",
                        "exonerated_at": bond.get("exonerated_at"),
                    })
                    continue

                defendant_name = bond.get("defendant_name", "")

                # 1. Update bond status
                await active_bonds.update_one(
                    {"booking_number": booking_number},
                    {"$set": {
                        "status": "exonerated",
                        "tracking_active": False,
                        "check_in_required": False,
                        "exonerated_at": now.isoformat(),
                        "exoneration_source": source,
                        "exoneration_note": note,
                        "updated_at": now,
                    }}
                )

                # 2. Release POA — only if status == "assigned" (safety guard)
                poa_number = bond.get("poa_number", "")
                surety_id = bond.get("insurance_company", bond.get("surety_id", ""))
                poa_released = False
                if poa_number:
                    poa_doc = await poa_inventory.find_one(
                        {"poa_number": poa_number, "status": "assigned"}
                    )
                    if poa_doc:
                        await poa_inventory.update_one(
                            {"poa_number": poa_number, "status": "assigned"},
                            {"$set": {
                                "status": "exonerated",
                                "exonerated_at": now.isoformat(),
                                "exonerated_booking": booking_number,
                            }}
                        )
                        poa_released = True

                # 3. Cancel pending reminders
                cancel_result = await court_reminders.update_many(
                    {"booking_number": booking_number, "status": {"$in": ["scheduled", "pending"]}},
                    {"$set": {"status": "cancelled_exonerated", "cancelled_at": now.isoformat()}}
                )

                # 4. Audit log
                await audit_col.insert_one({
                    "event_type": "bond_exonerated",
                    "entity_id": booking_number,
                    "entity_type": "bond_case",
                    "defendant_name": defendant_name,
                    "source": source,
                    "note": note,
                    "bulk": True,
                    "poa_released": poa_released,
                    "reminders_cancelled": cancel_result.modified_count,
                    "exonerated_at": now,
                    "timestamp": now,
                })

                # 5. Notify indemnitor via BlueBubbles (optional)
                notify_result = None
                if notify and bond.get("indemnitor_phone"):
                    try:
                        from dashboard.services.bb_client import send_message_universal
                        first_name = (bond.get("indemnitor_name") or "").split()[0] or "there"
                        msg = (
                            f"Hi {first_name}! Great news — {defendant_name}'s bond obligation "
                            f"with Shamrock Bail Bonds has been officially discharged. "
                            f"No further check-ins are required. ☘️ Shamrock Bail Bonds (239) 332-2245"
                        )
                        notify_result = await send_message_universal(bond["indemnitor_phone"], msg)
                    except Exception as notify_err:
                        notify_result = {"success": False, "error": str(notify_err)}

                # 6. SSE event
                try:
                    from dashboard.routers.events import publish_event
                    await publish_event("bond_exonerated", {
                        "booking_number": booking_number,
                        "defendant_name": defendant_name,
                        "exonerated_at": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception:
                    pass

                exonerated_count += 1
                results.append({
                    "booking_number": booking_number,
                    "status": "exonerated",
                    "defendant_name": defendant_name,
                    "poa_released": poa_released,
                    "reminders_cancelled": cancel_result.modified_count,
                    "notify_result": notify_result,
                })

            except Exception as bond_err:
                logger.error("[bulk-exonerate] Error for %s: %s", booking_number, bond_err)
                results.append({
                    "booking_number": booking_number,
                    "status": "error",
                    "error": str(bond_err),
                })

        return {
            "success": True,
            "summary": {
                "requested": len(booking_numbers),
                "exonerated": exonerated_count,
                "already_exonerated": already_done_count,
                "not_found": not_found_count,
                "errors": len([r for r in results if r.get("status") == "error"]),
            },
            "results": results,
            "processed_at": now.isoformat(),
        }

    except Exception as e:
        logger.error("[bulk-exonerate] Fatal error: %s", e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ═══════════════════════════════════════════════════════════════════════════════
# PER-DEFENDANT COMPLIANCE SUMMARY
# GET /api/active-bonds/<booking_number>/compliance
# Returns check-in compliance, court appearance, and payment status
# ═══════════════════════════════════════════════════════════════════════════════
@bonds_bp.get("/active-bonds/{booking_number}/compliance")
async def api_bond_compliance(booking_number):
    """
    Captira-style per-defendant compliance summary.
    Returns:
      - check_in: last check-in date, streak, overdue status, compliance %
      - court: next court date, days until, missed court dates
      - payment: plan status, balance remaining, days overdue
      - overall_score: 0-100 composite compliance score
    """
    try:
        db_active = get_collection("active_bonds")
        db_checkins = get_collection("bond_checkins")
        db_plans = get_collection("payment_plans")
        db_payments = get_collection("payments")

        bond = await db_active.find_one({"booking_number": booking_number})
        if not bond:
            return JSONResponse({"success": False, "error": "Bond not found"}, status_code=404)

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # ── Check-In Compliance ─────────────────────────────────────────────
        freq_days = bond.get("check_in_frequency_days") or 30
        last_checkin_raw = bond.get("last_checkin") or bond.get("last_check_in")
        next_due_raw = bond.get("next_checkin_due") or bond.get("next_check_in_due")
        missed = bond.get("missed_check_ins", 0)
        ci_required = bond.get("check_in_required", False)

        # Count check-ins in last 90 days
        cutoff_90 = (now - timedelta(days=90)).isoformat()
        ci_count = await db_checkins.count_documents({
            "booking_number": booking_number,
            "checkin_at": {"$gte": cutoff_90},
        })
        # Expected check-ins in 90 days
        expected_ci = max(1, 90 // max(1, freq_days))
        ci_compliance_pct = min(100, round((ci_count / expected_ci) * 100)) if expected_ci > 0 else 100

        # Last check-in details
        last_ci_doc = await db_checkins.find_one(
            {"booking_number": booking_number},
            sort=[("checkin_at", -1)],
        )
        last_ci_str = None
        if last_ci_doc:
            lc = last_ci_doc.get("checkin_at")
            last_ci_str = lc.isoformat() if hasattr(lc, "isoformat") else str(lc)

        # Overdue check
        ci_overdue = False
        hours_overdue = 0
        if ci_required and next_due_raw:
            nd_str = next_due_raw.isoformat() if hasattr(next_due_raw, "isoformat") else str(next_due_raw)
            if nd_str < now_iso:
                ci_overdue = True
                try:
                    nd_dt = datetime.fromisoformat(nd_str.replace("Z", "+00:00"))
                    if nd_dt.tzinfo is None:
                        nd_dt = nd_dt.replace(tzinfo=timezone.utc)
                    hours_overdue = max(0, int((now - nd_dt).total_seconds() / 3600))
                except Exception:
                    hours_overdue = 0

        # ── Court Appearance ────────────────────────────────────────────────
        court_date_raw = bond.get("court_date")
        court_date_str = None
        days_until_court = None
        court_status = "unknown"
        if court_date_raw:
            court_date_str = str(court_date_raw)[:10]
            try:
                cd = datetime.fromisoformat(court_date_str)
                diff = (cd.date() - now.date()).days
                days_until_court = diff
                if diff < 0:
                    court_status = "past"
                elif diff == 0:
                    court_status = "today"
                elif diff <= 3:
                    court_status = "imminent"
                elif diff <= 14:
                    court_status = "upcoming"
                else:
                    court_status = "scheduled"
            except Exception:
                court_status = "scheduled"

        # ── Payment Compliance ──────────────────────────────────────────────
        plan = await db_plans.find_one({"booking_number": booking_number})
        payment_status = "no_plan"
        balance_remaining = 0.0
        payment_days_overdue = 0
        total_paid = 0.0
        plan_amount = 0.0
        if plan:
            plan_status = plan.get("status", "active")
            balance_remaining = plan.get("balance_remaining", 0.0)
            total_paid = plan.get("total_paid", 0.0)
            plan_amount = plan.get("total_amount", 0.0)
            next_due_plan = plan.get("next_due_date", "")
            if plan_status == "paid":
                payment_status = "paid"
            elif next_due_plan and next_due_plan < now_iso:
                payment_status = "overdue"
                try:
                    nd_dt = datetime.fromisoformat(next_due_plan.replace("Z", "+00:00"))
                    if nd_dt.tzinfo is None:
                        nd_dt = nd_dt.replace(tzinfo=timezone.utc)
                    payment_days_overdue = max(0, (now - nd_dt).days)
                except Exception:
                    payment_days_overdue = 0
            else:
                payment_status = "current"
        else:
            # Check if premium was paid (one-time)
            premium_paid = await db_payments.count_documents({
                "booking_number": booking_number,
                "status": "completed",
                "type": {"$in": ["premium", "payment_plan"]},
            })
            if premium_paid > 0:
                payment_status = "paid"
                total_paid = bond.get("premium", 0.0)

        # ── Composite Compliance Score (0-100) ──────────────────────────────
        # Weights: check-in 40%, court 30%, payment 30%
        ci_score = ci_compliance_pct * 0.40
        if not ci_required:
            ci_score = 40  # Full credit if check-in not required

        court_score = 30
        if court_status == "past" and days_until_court is not None and days_until_court < -1:
            court_score = 0  # Missed court date
        elif court_status in ("today", "imminent"):
            court_score = 20  # Needs attention

        pay_score = 30
        if payment_status == "paid":
            pay_score = 30
        elif payment_status == "current":
            pay_score = 25
        elif payment_status == "overdue":
            pay_score = max(0, 25 - payment_days_overdue)
        elif payment_status == "no_plan":
            pay_score = 15  # Unknown

        overall_score = min(100, round(ci_score + court_score + pay_score))

        # ── Compliance Level ────────────────────────────────────────────────
        if overall_score >= 80:
            compliance_level = "compliant"
        elif overall_score >= 50:
            compliance_level = "warning"
        else:
            compliance_level = "critical"

        return {
            "success": True,
            "booking_number": booking_number,
            "defendant_name": bond.get("defendant_name", ""),
            "overall_score": overall_score,
            "compliance_level": compliance_level,
            "check_in": {
                "required": ci_required,
                "frequency_days": freq_days,
                "last_checkin": last_ci_str,
                "next_due": (next_due_raw.isoformat() if hasattr(next_due_raw, "isoformat") else str(next_due_raw)) if next_due_raw else None,
                "overdue": ci_overdue,
                "hours_overdue": hours_overdue,
                "missed_count": missed,
                "checkins_90d": ci_count,
                "compliance_pct": ci_compliance_pct,
            },
            "court": {
                "court_date": court_date_str,
                "court_location": bond.get("court_location", ""),
                "days_until": days_until_court,
                "status": court_status,
            },
            "payment": {
                "status": payment_status,
                "plan_amount": round(plan_amount, 2),
                "total_paid": round(total_paid, 2),
                "balance_remaining": round(balance_remaining, 2),
                "days_overdue": payment_days_overdue,
            },
            "evaluated_at": now_iso,
        }
    except Exception as exc:
        logger.exception("active-bonds/%s/compliance error: %s", booking_number, exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ══════════════════════════════════════════════════════════════════════════════
#  Bond Renewal / Re-Write
# ══════════════════════════════════════════════════════════════════════════════

@bonds_bp.post("/active-bonds/{booking_number}/renew")
async def api_renew_bond(request: Request, booking_number: str):
    """
    Re-write / renew an active bond.

    Handles:
      - New court date (continuance)
      - Bond amount reduction / increase
      - Charge amendment
      - New POA assignment
      - Cancels old court reminders, schedules new ones

    Body JSON:
      new_court_date     (str, ISO)   — required
      new_court_location (str)        — optional, defaults to existing
      new_bond_amount    (float)      — optional, defaults to existing
      new_charges        (str)        — optional, defaults to existing
      new_poa_number     (str)        — optional, assign new POA
      renewal_reason     (str)        — required: continuance|reduction|amendment|other
      notes              (str)        — optional agent notes
    """
    try:
        data = await request.json() or {}
        new_court_date = data.get("new_court_date")
        renewal_reason = data.get("renewal_reason", "continuance")

        if not new_court_date:
            return JSONResponse({"success": False, "error": "new_court_date is required"}, status_code=400)

        db = get_db()
        col = db["active_bonds"]
        bond = await col.find_one({"booking_number": booking_number})
        if not bond:
            return JSONResponse({"success": False, "error": "Bond not found"}, status_code=404)

        now_iso = datetime.now(timezone.utc).isoformat()

        # Build update fields
        update_fields = {
            "updated_at": now_iso,
            "last_renewed_at": now_iso,
            "renewal_count": bond.get("renewal_count", 0) + 1,
            "renewal_reason": renewal_reason,
            "previous_court_date": bond.get("court_date"),
        }

        if new_court_date:
            update_fields["court_date"] = new_court_date
        if data.get("new_court_location"):
            update_fields["court_location"] = data["new_court_location"]
        if data.get("new_bond_amount") is not None:
            old_amount = bond.get("bond_amount", 0)
            update_fields["bond_amount"] = float(data["new_bond_amount"])
            update_fields["previous_bond_amount"] = old_amount
        if data.get("new_charges"):
            update_fields["charges"] = data["new_charges"]
            update_fields["previous_charges"] = bond.get("charges")
        if data.get("notes"):
            update_fields["renewal_notes"] = data["notes"]

        # Handle POA re-assignment
        new_poa = data.get("new_poa_number")
        old_poa = bond.get("poa_number")
        if new_poa and new_poa != old_poa:
            # Release old POA back to available
            if old_poa:
                await db["poa_inventory"].update_one(
                    {"poa_number": old_poa},
                    {"$set": {"status": "available", "released_at": now_iso,
                              "released_reason": "bond_renewal"}}
                )
            # Mark new POA as used
            await db["poa_inventory"].update_one(
                {"poa_number": new_poa},
                {"$set": {"status": "used", "used_at": now_iso,
                          "booking_number": booking_number}}
            )
            update_fields["poa_number"] = new_poa
            update_fields["previous_poa_number"] = old_poa

        # Append to renewal history
        renewal_record = {
            "renewed_at": now_iso,
            "reason": renewal_reason,
            "old_court_date": bond.get("court_date"),
            "new_court_date": new_court_date,
            "old_bond_amount": bond.get("bond_amount"),
            "new_bond_amount": data.get("new_bond_amount", bond.get("bond_amount")),
            "agent": data.get("agent", "system"),
            "notes": data.get("notes", ""),
        }
        await col.update_one(
            {"booking_number": booking_number},
            {
                "$set": update_fields,
                "$push": {"renewal_history": renewal_record},
            }
        )

        # Cancel old court reminders and schedule new ones via BlueBubbles (iMessage)
        try:
            from dashboard.services.court_reminder_service import CourtReminderService
            svc = CourtReminderService(db)

            # Cancel all pending reminders for this booking
            cancelled = await svc.cancel_reminders(booking_number)
            logger.info("[BondRenewal] Cancelled %d old reminders for %s", cancelled, booking_number)

            defendant_name = bond.get("defendant_name", "")
            # Prefer indemnitor_phone on the bond doc; fall back to phone field
            phone = bond.get("indemnitor_phone") or bond.get("phone", "")
            court_location = update_fields.get("court_location", bond.get("court_location", ""))
            case_number = bond.get("case_number", "")

            # Collect all indemnitor phones from the indemnitors collection
            indemnitor_phones = []
            async for ind in db["indemnitors"].find({"booking_number": booking_number}, {"phone": 1}):
                if ind.get("phone"):
                    indemnitor_phones.append(ind["phone"])

            if phone and defendant_name:
                # schedule_reminders persists to court_reminders collection;
                # CourtReminderService processor delivers via BB iMessage
                sched_result = await svc.schedule_reminders(
                    booking_number=booking_number,
                    defendant_name=defendant_name,
                    phone=phone,
                    court_date_str=new_court_date,
                    court_location=court_location,
                    case_number=case_number,
                    indemnitor_phones=indemnitor_phones,
                )
                reminders_scheduled = sched_result.get("scheduled", 0) if isinstance(sched_result, dict) else 0
            else:
                reminders_scheduled = 0
        except Exception as rem_exc:
            logger.warning("[BondRenewal] BB reminder reschedule failed for %s: %s",
                           booking_number, rem_exc)
            reminders_scheduled = 0

        # Reschedule Court Reminder compliance task when court_date changes
        try:
            from dashboard.services.task_engine import TaskEngine
            await TaskEngine.schedule_court_reminder(booking_number)
            logger.info(
                "[BondRenewal] Court reminder task rescheduled for %s (new date: %s)",
                booking_number, new_court_date,
            )
        except Exception as _te_err:
            logger.warning(
                "[BondRenewal] TaskEngine.schedule_court_reminder failed for %s: %s",
                booking_number, _te_err,
            )

        # Fire SSE event
        try:
            from dashboard.routers.events import emit_event
            await emit_event("bond_renewed", {
                "booking_number": booking_number,
                "renewal_reason": renewal_reason,
                "new_court_date": new_court_date,
            })
        except Exception:
            pass

        logger.info("[BondRenewal] %s renewed (%s) — %d new reminders",
                    booking_number, renewal_reason, reminders_scheduled)
        return {
            "success": True,
            "booking_number": booking_number,
            "renewal_reason": renewal_reason,
            "new_court_date": new_court_date,
            "reminders_scheduled": reminders_scheduled,
            "renewal_count": update_fields["renewal_count"],
        }
    except Exception as exc:
        logger.exception("active-bonds/%s/renew error: %s", booking_number, exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/active-bonds/<booking_number>/renewal-history
# ─────────────────────────────────────────────────────────────────────────────
@bonds_bp.get("/active-bonds/{booking_number}/renewal-history")
async def api_bond_renewal_history(booking_number: str):
    """Return the renewal_history array for a bond."""
    try:
        active_bonds = get_collection("active_bonds")
        bond = await active_bonds.find_one(
            {"booking_number": booking_number},
            {"_id": 0, "renewal_history": 1, "renewal_count": 1},
        )
        if not bond:
            return JSONResponse({"success": False, "error": "Bond not found"}, status_code=404)

        history = bond.get("renewal_history", [])
        # Sort newest first
        history = sorted(history, key=lambda r: r.get("renewed_at", ""), reverse=True)

        return {
            "success": True,
            "booking_number": booking_number,
            "renewal_count": bond.get("renewal_count", len(history)),
            "renewal_history": history,
        }
    except Exception as exc:
        logger.exception("renewal-history error for %s: %s", booking_number, exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/active-bonds/<booking_number>/custom-fields
# ─────────────────────────────────────────────────────────────────────────────
@bonds_bp.patch("/active-bonds/{booking_number}/custom-fields")
async def api_bond_custom_fields(booking_number: str, request: Request):
    """Update custom fields for a bond."""
    try:
        data = (await request.json()) or {}
        custom_fields = data.get("custom_fields")
        if not isinstance(custom_fields, dict):
            return JSONResponse({"error": "custom_fields must be a dictionary"}, 400)
        
        active_bonds = get_collection("active_bonds")
        result = await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "custom_fields": custom_fields,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }}
        )
        if result.modified_count or result.matched_count:
            return {"success": True, "booking_number": booking_number}
        return JSONResponse({"success": False, "error": "Bond not found"}, 404)
    except Exception as exc:
        logger.exception("custom-fields error for %s: %s", booking_number, exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

@bonds_bp.get("/active-bonds/{booking_number}/custom-fields")
async def api_get_bond_custom_fields(booking_number: str):
    """Get custom fields for a bond."""
    try:
        active_bonds = get_collection("active_bonds")
        bond = await active_bonds.find_one({"booking_number": booking_number}, {"_id": 0, "custom_fields": 1})
        if not bond:
            return {"success": True, "custom_fields": {}}
        return {"success": True, "custom_fields": bond.get("custom_fields") or {}}
    except Exception as exc:
        logger.exception("get custom-fields error for %s: %s", booking_number, exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)