"""
ShamrockLeads — Forfeiture Alert Service
=========================================
Detects forfeiture keywords in emails and sends immediate BlueBubbles
iMessage/SMS alerts to configured phone numbers.

Alert recipients are stored in MongoDB `system_config` collection
(key: "forfeiture_alert_phones") so God-Admin can add/remove via dashboard.

Default phones (hardcoded as bootstrap):
  - 239-784-9365 (Brendan)
  - 239-955-0178 (Office)
  - 239-955-0314 (Additional)
"""

import logging
import re
from datetime import datetime, timezone

from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)

# ── Default forfeiture alert recipients (bootstrap) ─────────────────────────
DEFAULT_FORFEITURE_PHONES = [
    "+12397849365",
    "+12399550178",
    "+12399550314",
]

# ── Forfeiture-specific keywords (separate from discharge keywords) ─────────
FORFEITURE_KEYWORDS = [
    "bond forfeiture", "forfeiture", "estreature", "estreated",
    "failure to appear", "fta", "bench warrant issued",
    "bond estreated", "forfeited bond", "bond has been forfeited",
    "notice of forfeiture", "judgment of forfeiture",
    "summary judgment", "remittitur",
    "f.s. 903.26", "903.26", "903.27", "903.28",
    "order of forfeiture", "forfeiture notice",
    "bond estreature", "capias issued",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def detect_forfeiture(subject: str, body: str) -> dict:
    """
    Detect forfeiture signals in an email subject + body.
    Returns dict with is_forfeiture, matched_keywords, confidence.
    """
    text = f"{subject}\n{body}".lower()
    matched = [kw for kw in FORFEITURE_KEYWORDS if kw in text]
    confidence = min(len(matched) * 25, 100)

    # Extract defendant name if present
    defendant_name = None
    for pat in [
        re.compile(r'defendant[:\s]+([A-Z][A-Za-z\-\']+(?:\s[A-Z][A-Za-z\-\']+){1,3})', re.I),
        re.compile(r'bond\s+(?:for|of)[:\s]+([A-Z][A-Za-z\-\']+(?:\s[A-Z][A-Za-z\-\']+){1,3})', re.I),
        re.compile(r'inmate[:\s]+([A-Z][A-Za-z\-\']+(?:\s[A-Z][A-Za-z\-\']+){1,3})', re.I),
    ]:
        m = pat.search(f"{subject}\n{body}")
        if m:
            defendant_name = m.group(1).strip().title()
            break

    # Extract case number
    case_number = None
    case_pat = re.compile(r'(?:case|docket|cause)\s*(?:#|no\.?|number)?[:\s]+([A-Z0-9\-\/]{6,20})', re.I)
    m = case_pat.search(f"{subject}\n{body}")
    if m:
        case_number = m.group(1).upper()

    # Extract bond amount
    bond_amount = None
    amt_pat = re.compile(r'\$[\s]*([\d,]+(?:\.\d{2})?)')
    m = amt_pat.search(f"{subject}\n{body}")
    if m:
        try:
            bond_amount = float(m.group(1).replace(",", ""))
        except ValueError:
            pass

    return {
        "is_forfeiture": len(matched) > 0,
        "matched_keywords": matched,
        "confidence": confidence,
        "defendant_name": defendant_name,
        "case_number": case_number,
        "bond_amount": bond_amount,
    }


async def get_forfeiture_alert_phones() -> list[str]:
    """Get the list of phone numbers that receive forfeiture alerts."""
    try:
        config = get_collection("system_config")
        doc = await config.find_one({"key": "forfeiture_alert_phones"})
        if doc and doc.get("phones"):
            return doc["phones"]
    except Exception as e:
        logger.error("[forfeiture] Failed to load alert phones: %s", e)
    return DEFAULT_FORFEITURE_PHONES


async def set_forfeiture_alert_phones(phones: list[str]) -> dict:
    """Set the list of phone numbers that receive forfeiture alerts."""
    config = get_collection("system_config")
    # Normalize phone numbers
    normalized = []
    for p in phones:
        p = re.sub(r'[^\d+]', '', p.strip())
        if p and not p.startswith("+"):
            if len(p) == 10:
                p = "+1" + p
            elif len(p) == 11 and p.startswith("1"):
                p = "+" + p
        if p:
            normalized.append(p)

    await config.update_one(
        {"key": "forfeiture_alert_phones"},
        {"$set": {"key": "forfeiture_alert_phones", "phones": normalized, "updated_at": _utc_now().isoformat()}},
        upsert=True,
    )
    return {"success": True, "phones": normalized}


async def send_forfeiture_alerts(
    defendant_name: str = "Unknown",
    county: str = "",
    case_number: str = "",
    bond_amount: float = 0,
    subject: str = "",
) -> dict:
    """
    Send immediate BlueBubbles iMessage/SMS to all forfeiture alert phones.
    """
    try:
        from dashboard.services.bb_client import send_imessage
    except ImportError:
        logger.error("[forfeiture] BlueBubbles client not available")
        return {"success": False, "error": "BlueBubbles client not available"}

    phones = await get_forfeiture_alert_phones()
    if not phones:
        logger.warning("[forfeiture] No alert phones configured")
        return {"success": False, "error": "No forfeiture alert phones configured"}

    # Build alert message
    amt_str = f"${bond_amount:,.0f}" if bond_amount else "Unknown"
    msg_parts = [
        "🚨 FORFEITURE ALERT 🚨",
        "",
        f"👤 Defendant: {defendant_name}",
    ]
    if county:
        msg_parts.append(f"📍 County: {county}")
    if case_number:
        msg_parts.append(f"📋 Case #: {case_number}")
    msg_parts.append(f"💰 Bond: {amt_str}")
    if subject:
        msg_parts.append(f"📧 Email: {subject[:80]}")
    msg_parts.append("")
    msg_parts.append("Check email immediately.")
    msg_parts.append("— Shamrock Bail Bonds ☘️")
    message = "\n".join(msg_parts)

    sent = 0
    errors = []
    for phone in phones:
        try:
            result = await send_imessage(phone, message)
            # BB client returns {success: bool, ...} or raw API payload with status/data
            ok = bool(
                result
                and (
                    result.get("success") is True
                    or result.get("status") in (200, "200", "success")
                    or result.get("data") is not None
                )
                and result.get("success") is not False
            )
            if ok:
                sent += 1
                logger.info("[forfeiture] ✅ Alert sent to ...%s", phone[-4:])
            else:
                errors.append({"phone": phone[-4:], "error": result.get("error", "unknown")})
        except Exception as e:
            errors.append({"phone": phone[-4:], "error": str(e)})
            logger.error("[forfeiture] Failed to send to ...%s: %s", phone[-4:], e)

    # Log to audit collection
    try:
        audit = get_collection("audit_events")
        await audit.insert_one({
            "event_type": "forfeiture_alert_sent",
            "defendant_name": defendant_name,
            "county": county,
            "case_number": case_number,
            "bond_amount": bond_amount,
            "phones_notified": sent,
            "errors": errors,
            "timestamp": _utc_now(),
        })
    except Exception:
        pass

    return {"success": sent > 0, "sent": sent, "total_phones": len(phones), "errors": errors}
