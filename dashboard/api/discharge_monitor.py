"""
ShamrockLeads — Gmail Discharge Monitor
========================================
Monitors admin@shamrockbailbonds.biz for court discharge / exoneration emails
and automatically triggers bond exoneration via the tracking endpoint.

Endpoints:
  GET  /api/discharge-monitor/status       — health + last scan info
  POST /api/discharge-monitor/scan         — trigger manual Gmail scan
  POST /api/discharge-monitor/manual       — manually submit email body for parsing
  GET  /api/discharge-monitor/queue        — view discharge_queue collection
  POST /api/discharge-monitor/process      — process pending discharge_queue items
  POST /api/discharge-monitor/test-parse   — dry-run parse without writing

Gmail Setup (Feature J):
  See docs/GMAIL_DISCHARGE_SETUP.md for the 5-step OAuth setup.
  Required env vars: GMAIL_CREDENTIALS_JSON, DISCHARGE_GMAIL_LABEL

When GMAIL_CREDENTIALS_JSON is not set, all scan endpoints return 501
with setup instructions. The /manual and /queue endpoints always work.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone

from quart import Blueprint, jsonify, request

from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)

discharge_monitor_bp = Blueprint("discharge_monitor", __name__)

# ── SWFL county aliases (expand as needed) ────────────────────────────────────
COUNTY_ALIASES = {
    "lee": "Lee", "collier": "Collier", "charlotte": "Charlotte",
    "hendry": "Hendry", "glades": "Glades", "desoto": "DeSoto",
    "sarasota": "Sarasota", "manatee": "Manatee",
}

# ── Regex patterns for discharge email parsing ────────────────────────────────
# Patterns cover common Florida court email formats from Clerk of Courts offices,
# JailTracker automated notifications, and Lee/Collier/Charlotte county systems.
BOOKING_PATTERNS = [
    # County-prefixed booking numbers: LEE-2024123456, COL2024123456, SWFL-123456
    re.compile(r'\b([A-Z]{2,5}[-]?\d{6,12})\b'),
    # Explicit "Booking Number:" labels (various formats)
    re.compile(r'booking\s*(?:number|#|no\.?|num\.?)[:\s]+([A-Z0-9\-]{6,18})', re.I),
    # Case/docket/cause numbers
    re.compile(r'(?:case|docket|cause)\s*(?:number|#|no\.?)[:\s]+([A-Z0-9\-\/]{6,20})', re.I),
    # POA / Power of Attorney numbers
    re.compile(r'(?:poa|power\s+of\s+attorney)\s*(?:#|no\.?|number)?[:\s]+([A-Z0-9\-]{4,15})', re.I),
    # Arrest/incident/report numbers
    re.compile(r'(?:arrest|incident|report)\s*(?:#|no\.?|number)?[:\s]+([A-Z0-9\-]{6,18})', re.I),
]
DEFENDANT_PATTERNS = [
    # Explicit "Defendant:" label
    re.compile(r'defendant[:\s]+([A-Z][A-Za-z\-\']+(?:\s[A-Z][A-Za-z\-\']+){1,3})', re.I),
    # "Re:" / "Regarding:" subject lines often contain defendant name
    re.compile(r'(?:re:|regarding|subj(?:ect)?:)[:\s]+([A-Z][A-Za-z\-\']+(?:\s[A-Z][A-Za-z\-\']+){1,3})', re.I),
    # "Bond for/of NAME" patterns
    re.compile(r'bond\s+(?:for|of|on\s+behalf\s+of)[:\s]+([A-Z][A-Za-z\-\']+(?:\s[A-Z][A-Za-z\-\']+){1,3})', re.I),
    # "Inmate: NAME" from JailTracker automated emails
    re.compile(r'inmate[:\s]+([A-Z][A-Za-z\-\']+(?:\s[A-Z][A-Za-z\-\']+){1,3})', re.I),
    # ALL-CAPS name patterns common in Florida court docs: LAST, FIRST MIDDLE
    re.compile(r'\b([A-Z]{2,20},\s+[A-Z]{2,15}(?:\s+[A-Z]{1,15})?)\b'),
]
DISCHARGE_KEYWORDS = [
    # Core exoneration terms
    "discharged", "exonerated", "released from bond", "bond discharged",
    "bond exonerated", "bond released", "obligation satisfied",
    # Case resolution
    "case dismissed", "charges dropped", "nolle prosequi", "nol pros",
    "acquitted", "not guilty", "judgment of acquittal",
    # Sentence served / custody ended
    "sentence served", "time served", "released from custody",
    "released on recognizance", "released on own recognizance",
    # Florida-specific clerk language
    "bond forfeiture vacated", "forfeiture vacated", "bond reinstated",
    "surety discharged", "surety exonerated", "bond satisfied",
    "order of discharge", "discharge of bond",
]
COUNTY_PATTERN = re.compile(
    r'\b(' + '|'.join(COUNTY_ALIASES.keys()) + r')\s+county\b', re.I
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _gmail_available() -> bool:
    return bool(os.getenv("GMAIL_CREDENTIALS_JSON"))


def _parse_discharge_email(subject: str, body: str) -> dict:
    """
    Parse a court email for discharge signals.

    Returns dict with keys:
      is_discharge (bool), booking_number (str|None), defendant_name (str|None),
      county (str|None), confidence (int 0-100), matched_keywords (list[str]).

    Confidence scoring:
      - Each matched discharge keyword adds 20 points (capped at 60)
      - Booking number match adds 20 points
      - Defendant name match adds 15 points
      - County match adds 10 points
      - Threshold for is_discharge=True: any keyword matched
    """
    text = f"{subject}\n{body}".lower()
    combined = f"{subject}\n{body}"

    # Check for discharge keywords and collect matched ones
    matched_keywords = [kw for kw in DISCHARGE_KEYWORDS if kw in text]
    is_discharge = bool(matched_keywords)

    # Extract booking number
    booking_number = None
    for pat in BOOKING_PATTERNS:
        m = pat.search(combined)
        if m:
            booking_number = m.group(1).upper().replace(" ", "")
            break

    # Extract defendant name
    defendant_name = None
    for pat in DEFENDANT_PATTERNS:
        m = pat.search(combined)
        if m:
            defendant_name = m.group(1).strip().title()
            break

    # Extract county
    county = None
    m = COUNTY_PATTERN.search(combined)
    if m:
        county = COUNTY_ALIASES.get(m.group(1).lower(), m.group(1).title())

    # Confidence scoring (0-100)
    # Each keyword match adds 20 pts (capped at 60), booking +20, name +15, county +10
    confidence = min(len(matched_keywords) * 20, 60)
    if booking_number:
        confidence += 20
    if defendant_name:
        confidence += 15
    if county:
        confidence += 10
    confidence = min(confidence, 100)

    return {
        "is_discharge": is_discharge,
        "booking_number": booking_number,
        "defendant_name": defendant_name,
        "county": county,
        "confidence": confidence,
        "matched_keywords": matched_keywords,
    }


async def _match_bond_by_booking(booking_number: str) -> dict | None:
    """Try to find an active bond by booking number."""
    if not booking_number:
        return None
    active_bonds = get_collection("active_bonds")
    return await active_bonds.find_one(
        {"booking_number": booking_number, "status": {"$ne": "exonerated"}},
        {"_id": 0, "booking_number": 1, "defendant_name": 1, "county": 1, "status": 1}
    )


async def _match_bond_by_name(defendant_name: str, county: str = None) -> dict | None:
    """Fuzzy match by defendant name + optional county."""
    if not defendant_name:
        return None
    active_bonds = get_collection("active_bonds")
    query = {
        "defendant_name": {"$regex": re.escape(defendant_name), "$options": "i"},
        "status": {"$ne": "exonerated"},
    }
    if county:
        query["county"] = {"$regex": re.escape(county), "$options": "i"}
    return await active_bonds.find_one(query, {"_id": 0, "booking_number": 1, "defendant_name": 1, "county": 1, "status": 1})


# ── Endpoints ─────────────────────────────────────────────────────────────────

@discharge_monitor_bp.route("/discharge-monitor/status", methods=["GET"])
async def discharge_status():
    """Health check — returns Gmail availability and last scan info."""
    try:
        discharge_queue = get_collection("discharge_queue")
        pending = await discharge_queue.count_documents({"status": "pending"})
        processed = await discharge_queue.count_documents({"status": "processed"})
        failed = await discharge_queue.count_documents({"status": "failed"})
        last_item = await discharge_queue.find_one(
            {}, {"_id": 0, "created_at": 1, "status": 1}, sort=[("created_at", -1)]
        )
        return jsonify({
            "success": True,
            "gmail_available": _gmail_available(),
            "gmail_label": os.getenv("DISCHARGE_GMAIL_LABEL", "Court/Discharges"),
            "queue": {"pending": pending, "processed": processed, "failed": failed},
            "last_item_at": last_item.get("created_at", "").isoformat() if last_item and isinstance(last_item.get("created_at"), datetime) else str(last_item.get("created_at", "") if last_item else ""),
            "setup_docs": "docs/GMAIL_DISCHARGE_SETUP.md" if not _gmail_available() else None,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@discharge_monitor_bp.route("/discharge-monitor/scan", methods=["POST"])
async def discharge_scan():
    """Trigger a Gmail scan for discharge emails. Returns 501 if not configured."""
    if not _gmail_available():
        return jsonify({
            "success": False,
            "error": "Gmail not configured",
            "code": "GMAIL_NOT_CONFIGURED",
            "setup_steps": [
                "1. Create a Google Cloud project at console.cloud.google.com",
                "2. Enable the Gmail API",
                "3. Create OAuth2 credentials (Desktop app type)",
                "4. Run: python scripts/get_gmail_token.py",
                "5. Set GMAIL_CREDENTIALS_JSON env var to the credentials JSON path",
            ],
            "docs": "docs/GMAIL_DISCHARGE_SETUP.md",
        }), 501

    # Gmail scan implementation (requires google-auth + googleapiclient)
    try:
        import base64
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds_path = os.getenv("GMAIL_CREDENTIALS_JSON")
        creds = Credentials.from_authorized_user_file(creds_path)
        service = build("gmail", "v1", credentials=creds)
        label = os.getenv("DISCHARGE_GMAIL_LABEL", "Court/Discharges")

        # Find label ID
        labels_result = service.users().labels().list(userId="me").execute()
        label_id = None
        for lbl in labels_result.get("labels", []):
            if lbl.get("name", "").lower() == label.lower():
                label_id = lbl["id"]
                break

        query = f"label:{label}" if not label_id else ""
        messages_result = service.users().messages().list(
            userId="me", q=query, labelIds=[label_id] if label_id else [], maxResults=50
        ).execute()

        messages = messages_result.get("messages", [])
        queued = 0
        discharge_queue = get_collection("discharge_queue")

        for msg_ref in messages:
            msg_id = msg_ref["id"]
            # Skip already processed
            existing = await discharge_queue.find_one({"gmail_message_id": msg_id})
            if existing:
                continue

            msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
            headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
            subject = headers.get("Subject", "")
            body_data = msg["payload"].get("body", {}).get("data", "")
            if not body_data and msg["payload"].get("parts"):
                for part in msg["payload"]["parts"]:
                    if part.get("mimeType") == "text/plain":
                        body_data = part.get("body", {}).get("data", "")
                        break
            body = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="ignore") if body_data else ""

            parsed = _parse_discharge_email(subject, body)
            if parsed["is_discharge"] and parsed["confidence"] >= 50:
                await discharge_queue.insert_one({
                    "gmail_message_id": msg_id,
                    "subject": subject,
                    "body_snippet": body[:500],
                    "parsed": parsed,
                    "status": "pending",
                    "created_at": _utc_now(),
                })
                queued += 1

        return jsonify({"success": True, "messages_checked": len(messages), "queued": queued})
    except ImportError:
        return jsonify({
            "success": False,
            "error": "google-auth package not installed. Run: pip install google-auth google-auth-oauthlib google-api-python-client",
        }), 501
    except Exception as e:
        logger.exception("[discharge-scan] Error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@discharge_monitor_bp.route("/discharge-monitor/manual", methods=["POST"])
async def discharge_manual():
    """
    Manually submit an email body for discharge parsing.
    Useful for testing without Gmail credentials.
    Body: { "subject": "...", "body": "...", "auto_process": true }
    """
    try:
        data = await request.get_json(force=True) or {}
        subject = data.get("subject", "")
        body = data.get("body", "")
        auto_process = data.get("auto_process", False)

        if not body:
            return jsonify({"success": False, "error": "body is required"}), 400

        parsed = _parse_discharge_email(subject, body)

        # Try to match a bond
        matched_bond = None
        if parsed["booking_number"]:
            matched_bond = await _match_bond_by_booking(parsed["booking_number"])
        if not matched_bond and parsed["defendant_name"]:
            matched_bond = await _match_bond_by_name(parsed["defendant_name"], parsed["county"])

        result = {
            "success": True,
            "parsed": parsed,
            "matched_bond": matched_bond,
        }

        if parsed["is_discharge"] and parsed["confidence"] >= 50:
            discharge_queue = get_collection("discharge_queue")
            doc = {
                "gmail_message_id": f"manual-{_utc_now().timestamp()}",
                "subject": subject,
                "body_snippet": body[:500],
                "parsed": parsed,
                "matched_bond": matched_bond,
                "status": "pending",
                "created_at": _utc_now(),
            }
            insert_result = await discharge_queue.insert_one(doc)
            result["queued"] = True
            result["queue_id"] = str(insert_result.inserted_id)

            if auto_process and matched_bond:
                exon_result = await _auto_exonerate(matched_bond["booking_number"], "gmail_discharge_monitor")
                result["auto_exonerated"] = exon_result

        return jsonify(result)
    except Exception as e:
        logger.exception("[discharge-manual] Error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@discharge_monitor_bp.route("/discharge-monitor/queue", methods=["GET"])
async def discharge_queue_view():
    """View the discharge_queue collection."""
    try:
        discharge_queue = get_collection("discharge_queue")
        status_filter = request.args.get("status", "")
        query = {}
        if status_filter:
            query["status"] = status_filter
        items = await discharge_queue.find(query, {"_id": 0}).sort("created_at", -1).to_list(100)
        for item in items:
            for k, v in item.items():
                if isinstance(v, datetime):
                    item[k] = v.isoformat()
        return jsonify({"success": True, "items": items, "total": len(items)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@discharge_monitor_bp.route("/discharge-monitor/process", methods=["POST"])
async def discharge_process():
    """
    Process all pending items in discharge_queue.
    For each item with a matched bond, triggers exoneration.
    """
    try:
        discharge_queue = get_collection("discharge_queue")
        pending = await discharge_queue.find({"status": "pending"}).to_list(50)

        results = []
        for item in pending:
            item_id = item.get("_id")
            parsed = item.get("parsed", {})
            booking_number = parsed.get("booking_number")
            defendant_name = parsed.get("defendant_name")
            county = parsed.get("county")

            # Try to match bond
            matched_bond = item.get("matched_bond")
            if not matched_bond:
                if booking_number:
                    matched_bond = await _match_bond_by_booking(booking_number)
                if not matched_bond and defendant_name:
                    matched_bond = await _match_bond_by_name(defendant_name, county)

            if matched_bond:
                exon = await _auto_exonerate(matched_bond["booking_number"], "gmail_discharge_monitor")
                await discharge_queue.update_one(
                    {"_id": item_id},
                    {"$set": {"status": "processed", "processed_at": _utc_now(), "exoneration": exon}}
                )
                results.append({"booking_number": matched_bond["booking_number"], "status": "exonerated", "result": exon})
            else:
                await discharge_queue.update_one(
                    {"_id": item_id},
                    {"$set": {"status": "failed", "failure_reason": "no_bond_match", "processed_at": _utc_now()}}
                )
                results.append({"booking_number": booking_number, "status": "no_match"})

        return jsonify({"success": True, "processed": len(results), "results": results})
    except Exception as e:
        logger.exception("[discharge-process] Error: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@discharge_monitor_bp.route("/discharge-monitor/test-parse", methods=["POST"])
async def discharge_test_parse():
    """Dry-run parse — no database writes. For testing regex patterns."""
    try:
        data = await request.get_json(force=True) or {}
        subject = data.get("subject", "")
        body = data.get("body", "")
        parsed = _parse_discharge_email(subject, body)
        return jsonify({"success": True, "parsed": parsed, "dry_run": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


async def _auto_exonerate(booking_number: str, source: str) -> dict:
    """Internal helper — exonerates a bond (same logic as tracking endpoint)."""
    try:
        active_bonds = get_collection("active_bonds")
        audit_col = get_collection("audit_events")
        court_reminders = get_collection("court_reminders")
        now = _utc_now()

        bond = await active_bonds.find_one({"booking_number": booking_number})
        if not bond:
            return {"success": False, "error": "Bond not found"}
        if bond.get("status") == "exonerated":
            return {"success": True, "already_exonerated": True}

        await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "status": "exonerated",
                "tracking_active": False,
                "check_in_required": False,
                "exonerated_at": now.isoformat(),
                "exoneration_source": source,
                "updated_at": now,
            }}
        )
        await court_reminders.update_many(
            {"booking_number": booking_number, "status": {"$in": ["scheduled", "pending"]}},
            {"$set": {"status": "cancelled_exonerated", "cancelled_at": now.isoformat()}}
        )
        await audit_col.insert_one({
            "event_type": "bond_exonerated",
            "entity_id": booking_number,
            "entity_type": "bond_case",
            "defendant_name": bond.get("defendant_name", ""),
            "source": source,
            "exonerated_at": now,
            "timestamp": now,
        })
        return {"success": True, "booking_number": booking_number, "exonerated_at": now.isoformat()}
    except Exception as e:
        logger.error("[auto_exonerate] Error for %s: %s", booking_number, e)
        return {"success": False, "error": str(e)}
