"""
ShamrockLeads — Lee County Master Intelligence & Auto-Outreach Engine
Master command engine for Shamrock's home county (Lee County / Fort Myers / Ortiz Ave facility).

Provides:
  - Real-time Lee County booking aggregation & status pipeline
  - Family member & co-signer discovery (graph / indemnitors / skip-trace)
  - Speed-to-contact 1-click & auto-pilot outreach via BlueBubbles iMessage / SMS
  - "Make It Work" flexible bond payment calculations & terms
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Defaults
LEE_COUNTY_KEY = "Lee"
LEE_FACILITY_NAME = "Lee County Core Facility (Ortiz Ave, Fort Myers)"
LEE_BONDSMAN_PHONE = "(239) 332-2245"
LEE_BONDSMAN_DESK = "(239) 955-0301"
DEFAULT_MIN_SCORE = 70
DEFAULT_MIN_BOND = 500
COOLDOWN_HOURS = 24

def normalize_phone_e164(raw: str) -> str:
    """Normalize phone number to E.164 string (+1XXXXXXXXXX)."""
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw.strip())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if raw.strip().startswith("+") and digits:
        return f"+{digits}"
    return f"+1{digits}" if len(digits) > 7 else digits


def calculate_make_it_work_terms(total_bond: float) -> Dict[str, Any]:
    """
    Calculate statutory premium and flexible "Make It Work" payment plan options.
    Florida Statutory Rule (F.S. 648 / 903):
      - 10% standard statutory premium on bonds > $1,000 ($100 minimum per charge).
    'Make It Work' Local Payment Plan:
      - Down payment: 5% of total bond (or 50% of premium).
      - Remainder split over 4 or 8 weekly terms.
    """
    bond = max(0.0, float(total_bond or 0.0))
    statutory_premium = max(100.0, bond * 0.10) if bond > 0 else 0.0

    # 5% Down plan
    down_payment = round(statutory_premium * 0.50, 2)
    remaining_balance = round(statutory_premium - down_payment, 2)
    weekly_4_terms = round(remaining_balance / 4.0, 2) if remaining_balance > 0 else 0.0
    weekly_8_terms = round(remaining_balance / 8.0, 2) if remaining_balance > 0 else 0.0

    return {
        "total_bond": bond,
        "statutory_premium": statutory_premium,
        "premium_rate_pct": 10.0,
        "down_payment": down_payment,
        "down_payment_pct": 5.0,
        "remaining_balance": remaining_balance,
        "weekly_4_installments": weekly_4_terms,
        "weekly_8_installments": weekly_8_terms,
        "collateral_required": bond >= 15000.0,
        "qualified_for_terms": bond >= 1000.0,
    }


def generate_family_outreach_message(
    defendant_first_name: str,
    family_name: Optional[str] = None,
    bond_amount: float = 0.0,
    booking_number: str = "",
    charges_summary: str = "",
) -> str:
    """Generate high-conversion, empathetic outreach text tailored for Lee County."""
    greeting = f"Hi {family_name}" if family_name and family_name.strip() else "Hello"
    def_name = defendant_first_name.strip().title() if defendant_first_name else "your family member"
    terms = calculate_make_it_work_terms(bond_amount)
    prem = terms["statutory_premium"]
    down = terms["down_payment"]

    link = f"https://shamrockbailbonds.biz/portal-start"
    if booking_number:
        link += f"?booking={booking_number}&county=Lee"

    if bond_amount > 0:
        return (
            f"{greeting}, this is Shamrock Bail Bonds in Fort Myers. We noticed {def_name} "
            f"was booked at the Lee County Jail (Ortiz Ave). Total bond is ${bond_amount:,.0f} "
            f"(standard 10% premium: ${prem:,.0f}). We offer fast 5% down payment options (${down:,.0f} down) "
            f"and instant mobile paperwork to get them released. "
            f"Reply to this text 24/7 or tap here to begin: {link}"
        )
    else:
        return (
            f"{greeting}, this is Shamrock Bail Bonds in Fort Myers. We noticed {def_name} "
            f"was booked at the Lee County Jail (Ortiz Ave) and is scheduled for 10:00 AM First Appearance "
            f"before the judge. We are monitoring the court docket and can post their bond immediately "
            f"once set. Reply here or call (239) 332-2245 to speak with our local bondsman 24/7."
        )


async def get_lee_county_overview(db) -> Dict[str, Any]:
    """Retrieve top-level KPI metrics for Lee County."""
    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)
    iso_24h = since_24h.isoformat()

    lee_filter = {"$or": [{"county": "Lee"}, {"county": {"$regex": r"^Lee\b", "$options": "i"}}]}
    arrests_col = db["arrests"]
    outreach_col = db["lee_county_outreach_log"]
    config_col = db["lee_county_config"]

    # Read config
    cfg = await config_col.find_one({"county": "Lee"}) or {}
    autopilot_enabled = bool(cfg.get("autopilot_enabled", False))
    min_score = int(cfg.get("min_score", DEFAULT_MIN_SCORE))
    min_bond = float(cfg.get("min_bond", DEFAULT_MIN_BOND))

    # Aggregations
    total_24h = await arrests_col.count_documents({
        "$and": [
            lee_filter,
            {"$or": [
                {"scraped_at": {"$gte": iso_24h}},
                {"booking_date": {"$gte": iso_24h[:10]}},
            ]}
        ]
    })

    # In custody count
    in_custody_count = await arrests_col.count_documents({
        "$and": [
            lee_filter,
            {"$or": [
                {"status": {"$in": ["In Custody", "in_custody", "active", "In Jail"]}},
                {"status": {"$exists": False}},
            ]}
        ]
    })

    # Bond ready (bond > 0 and score >= min_score)
    bond_ready_count = await arrests_col.count_documents({
        "$and": [
            lee_filter,
            {"total_bond": {"$gte": min_bond}},
            {"lead_score": {"$gte": min_score}},
        ]
    })

    # First appearance count (bond == 0 or No Bond)
    fa_count = await arrests_col.count_documents({
        "$and": [
            lee_filter,
            {"$or": [
                {"total_bond": {"$in": [0, 0.0, None]}},
                {"total_bond": {"$exists": False}},
                {"charges.bond": {"$in": [0, "0", "NO BOND", "No Bond", "None"]}},
            ]}
        ]
    })

    # Sum of active bond pool
    pipeline = [
        {"$match": lee_filter},
        {"$group": {"_id": None, "total_pool": {"$sum": "$total_bond"}}}
    ]
    cursor = arrests_col.aggregate(pipeline)
    pool_docs = await cursor.to_list(length=1)
    total_pool = float(pool_docs[0].get("total_pool", 0.0)) if pool_docs else 0.0

    # Total outreach sent
    outreach_sent = await outreach_col.count_documents({})
    outreach_24h = await outreach_col.count_documents({"sent_at": {"$gte": iso_24h}})

    return {
        "ok": True,
        "county": "Lee",
        "facility": LEE_FACILITY_NAME,
        "local_time_fort_myers": datetime.now(timezone(timedelta(hours=-4))).strftime("%I:%M %p EDT"),
        "metrics": {
            "bookings_24h": total_24h,
            "in_custody": in_custody_count,
            "bond_ready_qualified": bond_ready_count,
            "first_appearance_waiting": fa_count,
            "total_bond_pool_value": total_pool,
            "estimated_commission_pool": round(total_pool * 0.10, 2),
            "outreach_total_sent": outreach_sent,
            "outreach_sent_24h": outreach_24h,
        },
        "autopilot": {
            "enabled": autopilot_enabled,
            "min_score": min_score,
            "min_bond": min_bond,
            "quiet_hours": cfg.get("quiet_hours", "23:00-06:30"),
            "daily_budget_texts": cfg.get("daily_budget_texts", 100),
            "emergency_bypass": bool(cfg.get("emergency_bypass", True)),
        }
    }


async def get_lee_county_leads(
    db,
    filter_type: str = "all",
    limit: int = 100,
    skip: int = 0,
    search: str = "",
) -> Dict[str, Any]:
    """Query and hydrate Lee County leads with family contact counts & deal terms."""
    arrests_col = db["arrests"]
    outreach_col = db["lee_county_outreach_log"]

    lee_filter: Dict[str, Any] = {"$or": [{"county": "Lee"}, {"county": {"$regex": r"^Lee\b", "$options": "i"}}]}
    match_clauses: List[Dict[str, Any]] = [lee_filter]

    if filter_type == "bond_ready":
        match_clauses.append({"total_bond": {"$gt": 0}})
        match_clauses.append({"lead_score": {"$gte": 60}})
    elif filter_type == "hot":
        match_clauses.append({"lead_score": {"$gte": 75}})
    elif filter_type == "first_appearance":
        match_clauses.append({"$or": [
            {"total_bond": {"$in": [0, 0.0, None]}},
            {"total_bond": {"$exists": False}},
        ]})
    elif filter_type == "high_value":
        match_clauses.append({"total_bond": {"$gte": 2500}})
    elif filter_type == "contacted":
        match_clauses.append({"lee_outreach.status": "sent"})

    if search and search.strip():
        s = search.strip()
        match_clauses.append({
            "$or": [
                {"first_name": {"$regex": s, "$options": "i"}},
                {"last_name": {"$regex": s, "$options": "i"}},
                {"booking_number": {"$regex": s, "$options": "i"}},
                {"charges.charge": {"$regex": s, "$options": "i"}},
            ]
        })

    query = {"$and": match_clauses} if len(match_clauses) > 1 else match_clauses[0]
    total = await arrests_col.count_documents(query)

    cursor = arrests_col.find(query).sort([("scraped_at", -1), ("booking_date", -1)]).skip(skip).limit(limit)
    raw_leads = await cursor.to_list(length=limit)

    leads = []
    for doc in raw_leads:
        doc_id = str(doc.get("_id", ""))
        booking_no = str(doc.get("booking_number") or doc.get("booking_no") or "")
        first_name = doc.get("first_name") or ""
        last_name = doc.get("last_name") or ""
        full_name = f"{first_name} {last_name}".strip() or doc.get("defendant_name") or "Unknown"
        total_bond = float(doc.get("total_bond") or 0.0)
        lead_score = int(doc.get("lead_score") or doc.get("score") or 50)
        charges = doc.get("charges") or []

        # Calculate terms
        terms = calculate_make_it_work_terms(total_bond)

        # Check outreach status
        outreach_info = doc.get("lee_outreach") or {}
        if not outreach_info and booking_no:
            log_doc = await outreach_col.find_one({"booking_number": booking_no})
            if log_doc:
                outreach_info = {
                    "status": "sent",
                    "sent_at": log_doc.get("sent_at"),
                    "recipient_phone": log_doc.get("recipient_phone"),
                    "recipient_name": log_doc.get("recipient_name"),
                }

        leads.append({
            "id": doc_id,
            "booking_number": booking_no,
            "full_name": full_name,
            "first_name": first_name,
            "last_name": last_name,
            "county": "Lee",
            "facility": LEE_FACILITY_NAME,
            "booking_date": doc.get("booking_date") or doc.get("scraped_at") or "",
            "total_bond": total_bond,
            "lead_score": lead_score,
            "status": doc.get("status") or "In Custody",
            "charges": charges,
            "terms": terms,
            "outreach": outreach_info,
            "mugshot_url": doc.get("mugshot_url") or doc.get("image_url") or "",
        })

    return {
        "ok": True,
        "total": total,
        "limit": limit,
        "skip": skip,
        "filter": filter_type,
        "leads": leads,
    }


async def find_family_contacts(
    db,
    defendant_name: str = "",
    booking_number: str = "",
    defendant_phone: str = "",
) -> List[Dict[str, Any]]:
    """Discover family members, cosigners, and emergency contacts for a defendant."""
    contacts: List[Dict[str, Any]] = []
    seen_phones = set()

    clean_def_phone = normalize_phone_e164(defendant_phone)
    if clean_def_phone:
        seen_phones.add(clean_def_phone)

    # 1. Search existing family graph
    family_col = db["family_graph"]
    if defendant_name:
        async for doc in family_col.find({
            "$or": [
                {"defendant_name": {"$regex": defendant_name, "$options": "i"}},
                {"relative_name": {"$regex": defendant_name, "$options": "i"}},
            ]
        }):
            phone = normalize_phone_e164(doc.get("phone") or doc.get("relative_phone") or "")
            if phone and phone not in seen_phones:
                seen_phones.add(phone)
                contacts.append({
                    "name": doc.get("relative_name") or doc.get("name") or "Family Contact",
                    "relationship": doc.get("relationship") or "Relative",
                    "phone": phone,
                    "source": "Family Graph",
                    "confidence": float(doc.get("confidence", 0.90)),
                })

    # 2. Search indemnitors / past cosigners
    indemnitors_col = db["indemnitors"]
    if defendant_name:
        async for doc in indemnitors_col.find({
            "$or": [
                {"defendant_name": {"$regex": defendant_name, "$options": "i"}},
                {"defendants_bonded": {"$regex": defendant_name, "$options": "i"}},
            ]
        }):
            phone = normalize_phone_e164(doc.get("phone") or "")
            if phone and phone not in seen_phones:
                seen_phones.add(phone)
                contacts.append({
                    "name": doc.get("name") or f"{doc.get('first_name', '')} {doc.get('last_name', '')}".strip() or "Cosigner",
                    "relationship": doc.get("relationship") or "Prior Indemnitor",
                    "phone": phone,
                    "source": "Super CRM Indemnitor DB",
                    "confidence": 0.95,
                })

    # 3. Search intake records / emergency contacts
    intakes_col = db["intakes"]
    if booking_number or defendant_name:
        q = {}
        if booking_number:
            q["booking_number"] = booking_number
        elif defendant_name:
            q["defendant_name"] = {"$regex": defendant_name, "$options": "i"}

        async for doc in intakes_col.find(q):
            # Indemnitor phone
            i_phone = normalize_phone_e164(doc.get("indemnitor_phone") or "")
            if i_phone and i_phone not in seen_phones:
                seen_phones.add(i_phone)
                contacts.append({
                    "name": doc.get("indemnitor_name") or "Intake Indemnitor",
                    "relationship": doc.get("indemnitor_relationship") or "Emergency Contact",
                    "phone": i_phone,
                    "source": "Intake Submission",
                    "confidence": 0.98,
                })

    # 4. Search enrichment / skip-trace collection
    enrich_col = db["enrichment_data"]
    if booking_number:
        async for doc in enrich_col.find({"booking_number": booking_number}):
            for rel in doc.get("relatives", []) + doc.get("associates", []):
                phone = normalize_phone_e164(rel.get("phone") or "")
                if phone and phone not in seen_phones:
                    seen_phones.add(phone)
                    contacts.append({
                        "name": rel.get("name") or "Relative",
                        "relationship": rel.get("relation") or rel.get("type") or "Skip-Trace Match",
                        "phone": phone,
                        "source": "OSINT / Skip-Trace",
                        "confidence": float(rel.get("confidence", 0.75)),
                    })

    # Sort contacts by confidence
    contacts.sort(key=lambda x: x.get("confidence", 0.0), reverse=True)
    return contacts


async def send_lee_county_outreach(
    db,
    booking_number: str,
    recipient_phone: str,
    recipient_name: str = "",
    custom_message: Optional[str] = None,
    triggered_by: str = "manual_dashboard",
) -> Dict[str, Any]:
    """Send personalized outreach text for a Lee County arrestee and log the event."""
    clean_phone = normalize_phone_e164(recipient_phone)
    if not clean_phone or len(clean_phone) < 10:
        return {"ok": False, "error": "Invalid recipient phone number"}

    # 1. Check DNC
    dnc_col = db["dnc_list"]
    if await dnc_col.find_one({"phone": clean_phone}):
        return {"ok": False, "error": f"Phone number {clean_phone} is on the Do Not Call (DNC) list"}

    # 2. Check Cooldown
    outreach_col = db["lee_county_outreach_log"]
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=COOLDOWN_HOURS)).isoformat()
    recent = await outreach_col.find_one({
        "recipient_phone": clean_phone,
        "booking_number": booking_number,
        "sent_at": {"$gte": cutoff},
    })
    if recent:
        return {
            "ok": False,
            "error": f"Outreach already sent to {clean_phone} for booking {booking_number} within last {COOLDOWN_HOURS}h",
            "last_sent_at": recent.get("sent_at"),
        }

    # 3. Retrieve arrest info
    arrests_col = db["arrests"]
    arrest_doc = await arrests_col.find_one({"$or": [
        {"booking_number": booking_number},
        {"booking_no": booking_number},
    ]}) or {}

    first_name = arrest_doc.get("first_name") or ""
    bond_amount = float(arrest_doc.get("total_bond") or 0.0)

    # 4. Construct message if not provided
    message = custom_message or generate_family_outreach_message(
        defendant_first_name=first_name,
        family_name=recipient_name,
        bond_amount=bond_amount,
        booking_number=booking_number,
    )

    # 5. Dispatch message via universal bridge
    send_res: Dict[str, Any] = {}
    try:
        from dashboard.services.bb_client import send_message_universal
        send_res = await send_message_universal(clean_phone, message)
    except Exception as exc:
        logger.error(f"❌ Failed to dispatch Lee County outreach to {clean_phone}: {exc}")
        send_res = {"success": False, "error": str(exc)}

    is_success = bool(send_res.get("success", False) or send_res.get("status") in ("sent", "delivered", "queued"))
    now_iso = datetime.now(timezone.utc).isoformat()

    # 6. Record log entry
    log_doc = {
        "booking_number": booking_number,
        "defendant_name": f"{arrest_doc.get('first_name', '')} {arrest_doc.get('last_name', '')}".strip(),
        "recipient_phone": clean_phone,
        "recipient_name": recipient_name,
        "message": message,
        "sent_at": now_iso,
        "triggered_by": triggered_by,
        "status": "sent" if is_success else "failed",
        "provider_response": send_res,
    }
    await outreach_col.insert_one(log_doc)

    # 7. Update arrest record
    if is_success:
        await arrests_col.update_one(
            {"_id": arrest_doc.get("_id")},
            {"$set": {
                "lee_outreach": {
                    "status": "sent",
                    "sent_at": now_iso,
                    "recipient_phone": clean_phone,
                    "recipient_name": recipient_name,
                    "triggered_by": triggered_by,
                }
            }}
        )

    return {
        "ok": is_success,
        "booking_number": booking_number,
        "recipient_phone": clean_phone,
        "recipient_name": recipient_name,
        "sent_at": now_iso,
        "message": message,
        "provider_response": send_res,
    }


async def run_lee_county_autopilot_sweep(db) -> Dict[str, Any]:
    """
    Execute an automated sweep of high-priority Lee County leads and auto-dispatch
    messages to discovered family contacts when auto-pilot conditions are satisfied.
    """
    config_col = db["lee_county_config"]
    cfg = await config_col.find_one({"county": "Lee"}) or {}

    if not cfg.get("autopilot_enabled", False):
        return {"ok": True, "status": "skipped", "reason": "Autopilot is disabled in configuration"}

    # Check quiet hours (unless emergency bypass is enabled)
    now_est = datetime.now(timezone(timedelta(hours=-4)))
    cur_hour = now_est.hour
    is_quiet_hours = (cur_hour >= 23 or cur_hour < 6)
    if is_quiet_hours and not cfg.get("emergency_bypass", True):
        return {"ok": True, "status": "skipped", "reason": "Quiet hours active (11:00 PM – 6:30 AM)"}

    min_score = int(cfg.get("min_score", DEFAULT_MIN_SCORE))
    min_bond = float(cfg.get("min_bond", DEFAULT_MIN_BOND))

    # Query eligible leads
    arrests_col = db["arrests"]
    query = {
        "$and": [
            {"$or": [{"county": "Lee"}, {"county": {"$regex": r"^Lee\b", "$options": "i"}}]},
            {"total_bond": {"$gte": min_bond}},
            {"lead_score": {"$gte": min_score}},
            {"lee_outreach.status": {"$ne": "sent"}},
        ]
    }

    cursor = arrests_col.find(query).sort([("lead_score", -1)]).limit(20)
    candidates = await cursor.to_list(length=20)

    processed = 0
    contacted = 0
    errors = []

    for c in candidates:
        processed += 1
        booking_no = c.get("booking_number") or c.get("booking_no") or ""
        def_name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()

        family_list = await find_family_contacts(db, defendant_name=def_name, booking_number=booking_no)
        if not family_list:
            continue

        top_contact = family_list[0]
        phone = top_contact.get("phone")
        name = top_contact.get("name")

        res = await send_lee_county_outreach(
            db=db,
            booking_number=booking_no,
            recipient_phone=phone,
            recipient_name=name,
            triggered_by="autopilot_cron",
        )

        if res.get("ok"):
            contacted += 1
        else:
            errors.append({"booking_number": booking_no, "error": res.get("error")})

    return {
        "ok": True,
        "status": "completed",
        "processed_candidates": processed,
        "contacted_count": contacted,
        "errors": errors,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
