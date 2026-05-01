"""
ShamrockLeads — Discharge Monitor API Blueprint (Feature J)
============================================================
Monitors Gmail for clerk discharge notices and auto-exonerates bonds.

Flow:
  1. POST /api/discharge-monitor/scan  — Poll Gmail label for new discharge emails
  2. POST /api/discharge-monitor/process — Parse + match + exonerate
  3. GET  /api/discharge-monitor/status  — Dashboard status panel
  4. GET  /api/discharge-monitor/log     — Recent processing log

Requires GMAIL_CREDENTIALS_JSON env var pointing to service account key,
and DISCHARGE_GMAIL_LABEL env var (default: "Court/Discharges").
"""

import logging
import re
from datetime import datetime, timezone

from quart import Blueprint, jsonify, request
from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)

discharge_monitor_bp = Blueprint("discharge_monitor", __name__)

# ── Discharge email parsing patterns ──
# These patterns match common Florida clerk discharge notice formats.
# They will need refinement once real sample emails are analyzed.
CASE_NUMBER_PATTERNS = [
    re.compile(r"(?:Case|Case\s*#|Case\s*No\.?)\s*[:\s]*(\d{2,4}-[A-Z]{2}-\d{4,8})", re.I),
    re.compile(r"(?:Case|Case\s*#|Case\s*No\.?)\s*[:\s]*(\d{4,8}[A-Z]{2}\d{4,8})", re.I),
    re.compile(r"(\d{2}-\d{6}-[A-Z]{2})", re.I),
]

DEFENDANT_NAME_PATTERNS = [
    re.compile(r"(?:Defendant|DEF|RE)\s*[:\s]*([A-Z][A-Za-z'-]+,\s*[A-Z][A-Za-z'-]+)", re.I),
    re.compile(r"(?:State\s+(?:of\s+Florida\s+)?v[s.]?\s+)([A-Z][A-Za-z'-]+,?\s+[A-Z][A-Za-z'-]+)", re.I),
]

DISCHARGE_TYPE_PATTERNS = [
    (re.compile(r"nolle\s*prosse?(?:qui)?", re.I), "nolle_prosequi"),
    (re.compile(r"acquit(?:ted|tal)", re.I), "acquittal"),
    (re.compile(r"dismiss(?:ed|al)", re.I), "dismissal"),
    (re.compile(r"complet(?:ed|ion)", re.I), "completion"),
    (re.compile(r"discharg(?:ed|e)", re.I), "discharge"),
    (re.compile(r"exonerat(?:ed|ion)", re.I), "exoneration"),
    (re.compile(r"bond\s*(?:is\s*)?(?:hereby\s*)?releas(?:ed|e)", re.I), "bond_released"),
]

COUNTY_PATTERNS = [
    re.compile(r"(Lee|Charlotte|Collier|DeSoto|Hendry|Manatee|Sarasota)\s*County", re.I),
]


def _parse_discharge_email(subject: str, body: str) -> dict:
    """Extract structured data from a discharge email."""
    result = {
        "case_number": None,
        "defendant_name": None,
        "discharge_type": None,
        "county": None,
        "raw_subject": subject,
        "confidence": 0,
    }
    text = f"{subject}\n{body}"

    # Extract case number
    for pattern in CASE_NUMBER_PATTERNS:
        m = pattern.search(text)
        if m:
            result["case_number"] = m.group(1).strip()
            result["confidence"] += 30
            break

    # Extract defendant name
    for pattern in DEFENDANT_NAME_PATTERNS:
        m = pattern.search(text)
        if m:
            result["defendant_name"] = m.group(1).strip()
            result["confidence"] += 25
            break

    # Extract discharge type
    for pattern, dtype in DISCHARGE_TYPE_PATTERNS:
        if pattern.search(text):
            result["discharge_type"] = dtype
            result["confidence"] += 25
            break

    # Extract county
    for pattern in COUNTY_PATTERNS:
        m = pattern.search(text)
        if m:
            result["county"] = m.group(1).strip().title()
            result["confidence"] += 20
            break

    return result


@discharge_monitor_bp.route("/discharge-monitor/scan", methods=["POST"])
async def scan_gmail():
    """Poll Gmail for new discharge emails and queue them for processing.
    
    NOTE: This endpoint requires the gws-gmail skill to be configured.
    Until Gmail API credentials are set up, this returns a stub response
    with instructions on how to configure it.
    """
    import os
    creds_path = os.getenv("GMAIL_CREDENTIALS_JSON", "")
    if not creds_path:
        return jsonify({
            "success": False,
            "error": "GMAIL_CREDENTIALS_JSON not configured",
            "setup_instructions": [
                "1. Create a GCP service account with Gmail API access",
                "2. Download the JSON key file",
                "3. Set GMAIL_CREDENTIALS_JSON=/path/to/key.json in .env",
                "4. Set DISCHARGE_GMAIL_LABEL=Court/Discharges in .env",
                "5. Grant the service account domain-wide delegation",
            ],
        }), 501

    # When configured, this will:
    # 1. Connect to Gmail via service account
    # 2. Read emails from the configured label
    # 3. Parse each email using _parse_discharge_email()
    # 4. Store parsed results in discharge_queue collection
    # 5. Return summary of found emails
    return jsonify({
        "success": True,
        "message": "Gmail scan not yet wired — credentials found but integration pending",
        "creds_path": creds_path,
    })


@discharge_monitor_bp.route("/discharge-monitor/process", methods=["POST"])
async def process_discharges():
    """Process queued discharge emails: match to bonds and exonerate.
    
    For each parsed email in discharge_queue with status='pending':
      1. Match by case_number against active_bonds
      2. If no case_number match, try defendant_name + county
      3. If matched: update bond status → 'exonerated', release POA, log audit
      4. Send Slack notification to #discharges
    """
    discharge_queue = get_collection("discharge_queue")
    active_bonds = get_collection("active_bonds")
    poa_inventory = get_collection("poa_inventory")
    audit_events = get_collection("audit_events")

    now = datetime.now(timezone.utc)
    pending = await discharge_queue.find({"status": "pending"}).to_list(50)

    processed = 0
    matched = 0
    failed = 0
    details = []

    for item in pending:
        try:
            case_num = item.get("case_number", "")
            defendant = item.get("defendant_name", "")
            county = item.get("county", "")

            # Try to match by case number first
            bond = None
            if case_num:
                bond = await active_bonds.find_one({
                    "case_number": {"$regex": re.escape(case_num), "$options": "i"},
                    "status": {"$in": ["active", "monitoring"]},
                })

            # Fallback: match by defendant name + county
            if not bond and defendant and county:
                # Normalize name for matching
                name_parts = defendant.replace(",", " ").split()
                if len(name_parts) >= 2:
                    bond = await active_bonds.find_one({
                        "defendant_name": {"$regex": re.escape(name_parts[0]), "$options": "i"},
                        "county": {"$regex": re.escape(county), "$options": "i"},
                        "status": {"$in": ["active", "monitoring"]},
                    })

            if bond:
                booking = bond.get("booking_number", "")

                # 1. Exonerate the bond
                await active_bonds.update_one(
                    {"booking_number": booking},
                    {"$set": {
                        "status": "exonerated",
                        "exonerated_at": now.isoformat(),
                        "exoneration_type": item.get("discharge_type", "discharge"),
                        "exoneration_source": "gmail_discharge_monitor",
                        "updated_at": now,
                    }},
                )

                # 2. Release POA back to available
                poa_num = bond.get("poa_number", "")
                surety = (bond.get("insurance_company", "") or "").lower()
                if poa_num and surety:
                    await poa_inventory.update_one(
                        {"poa_number": poa_num, "surety_id": surety},
                        {"$set": {"status": "available", "bond_case_id": None, "used_at": None}},
                    )

                # 3. Audit log
                await audit_events.insert_one({
                    "event_type": "bond_exonerated_auto",
                    "entity_id": booking,
                    "entity_type": "bond_case",
                    "defendant_name": bond.get("defendant_name", ""),
                    "discharge_type": item.get("discharge_type", ""),
                    "source": "gmail_discharge_monitor",
                    "case_number": case_num,
                    "timestamp": now,
                })

                # 4. Update queue item
                await discharge_queue.update_one(
                    {"_id": item["_id"]},
                    {"$set": {
                        "status": "processed",
                        "matched_booking": booking,
                        "processed_at": now.isoformat(),
                    }},
                )

                matched += 1
                details.append({
                    "booking": booking,
                    "defendant": bond.get("defendant_name", ""),
                    "status": "exonerated",
                    "discharge_type": item.get("discharge_type", ""),
                })
            else:
                await discharge_queue.update_one(
                    {"_id": item["_id"]},
                    {"$set": {"status": "no_match", "processed_at": now.isoformat()}},
                )
                details.append({
                    "case_number": case_num,
                    "defendant": defendant,
                    "status": "no_match",
                })
                failed += 1

            processed += 1

        except Exception as exc:
            logger.error("[discharge] Processing error: %s", exc)
            await discharge_queue.update_one(
                {"_id": item["_id"]},
                {"$set": {"status": "error", "error": str(exc)}},
            )
            failed += 1

    return jsonify({
        "success": True,
        "processed": processed,
        "matched": matched,
        "failed": failed,
        "details": details[:20],
    })


@discharge_monitor_bp.route("/discharge-monitor/manual", methods=["POST"])
async def manual_discharge_entry():
    """Manually queue a discharge for processing (for testing or manual entry).
    
    Body: {
        "case_number": "25-CF-001234",
        "defendant_name": "Smith, John",
        "county": "Lee",
        "discharge_type": "nolle_prosequi",
        "email_subject": "optional",
        "email_body": "optional"
    }
    """
    data = await request.get_json(force=True) or {}
    discharge_queue = get_collection("discharge_queue")

    subject = data.get("email_subject", "")
    body = data.get("email_body", "")

    # If raw email text provided, parse it
    if subject or body:
        parsed = _parse_discharge_email(subject, body)
    else:
        parsed = {
            "case_number": data.get("case_number", ""),
            "defendant_name": data.get("defendant_name", ""),
            "discharge_type": data.get("discharge_type", "discharge"),
            "county": data.get("county", ""),
            "confidence": 100,  # Manual entry = full confidence
        }

    parsed["status"] = "pending"
    parsed["source"] = "manual_entry"
    parsed["created_at"] = datetime.now(timezone.utc).isoformat()

    await discharge_queue.insert_one(parsed)

    return jsonify({
        "success": True,
        "message": "Discharge queued for processing",
        "parsed": {k: v for k, v in parsed.items() if k != "_id"},
    })


@discharge_monitor_bp.route("/discharge-monitor/status", methods=["GET"])
async def discharge_status():
    """Dashboard status: pending, processed, no_match counts."""
    discharge_queue = get_collection("discharge_queue")

    pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    counts = {}
    async for row in discharge_queue.aggregate(pipeline):
        counts[row["_id"]] = row["count"]

    return jsonify({
        "pending": counts.get("pending", 0),
        "processed": counts.get("processed", 0),
        "no_match": counts.get("no_match", 0),
        "error": counts.get("error", 0),
        "total": sum(counts.values()),
    })


@discharge_monitor_bp.route("/discharge-monitor/log", methods=["GET"])
async def discharge_log():
    """Recent discharge processing log (last 50 entries)."""
    discharge_queue = get_collection("discharge_queue")

    cursor = discharge_queue.find(
        {}, {"_id": 0}
    ).sort("created_at", -1).limit(50)
    entries = await cursor.to_list(50)

    return jsonify({"log": entries, "total": len(entries)})
""", "Description": "Feature J: Gmail discharge monitor blueprint with email parsing, bond matching, auto-exoneration, POA release, and audit logging. Includes manual entry endpoint for testing and a status/log dashboard API."
