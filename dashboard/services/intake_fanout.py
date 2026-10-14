"""
Non-blocking fan-out AFTER an intake is saved in MongoDB ``intake_queue``.

Owner rule (2026-09-27): Wix applications land in the CRM first (Mongo is the
source of truth). After a successful save the CRM copies the intake to
  1. Google Sheets — one "Intake Ledger" row per intake (reporting / data viz)
     via the GAS web app action ``appendIntakeLedger``; and
  2. Slack — a short new-application notice.

Guarantees
  * Never fails or delays the intake: callers get control back immediately;
    every error is caught, logged, and recorded on an outbox row.
  * Retried with backoff by the ``intake_fanout_retry`` cron (dashboard/cron.py)
    until it succeeds or hits MAX_ATTEMPTS (then status=dead + error log).
  * Idempotent per (intake_id, target): the outbox row is unique, and the GAS
    ledger handler skips an intake_id it has already written.
  * Minimal PII: names, county, booking #, amounts, last-4 phone. No SSN, DOB,
    DL, or street address leaves the CRM through this path.

Config (VPS .env)
  GAS_WEB_APP_URL + GAS_API_KEY      → Sheets copy (already used by leads)
  SLACK_WEBHOOK_INTAKE               → Slack copy (falls back to SLACK_WEBHOOK_LEADS)
  INTAKE_FANOUT_DISABLED=1           → kill switch (rows stay pending)

Collection: ``intake_fanout_outbox``
  {intake_id, target: sheets|slack,
   status: pending|in_flight|sent|failed|skipped_unconfigured|dead,
   attempts, next_attempt_at, last_error, payload, created_at, updated_at, sent_at}
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo
from core.booking_identity import redact_internal_keys

logger = logging.getLogger(__name__)

OUTBOX = "intake_fanout_outbox"
TARGETS = ("sheets", "slack")
MAX_ATTEMPTS = 10
# Delay before attempt N+1 (seconds). Past the list, the last value repeats.
BACKOFF = (60, 300, 900, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 24 * 3600)
ET = ZoneInfo("America/New_York")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _disabled() -> bool:
    return (os.getenv("INTAKE_FANOUT_DISABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def _last4(v: Any) -> str:
    digits = "".join(ch for ch in str(v or "") if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else ""


def _stamp(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def apply_auto_link_snapshot(doc: Dict[str, Any], match_result: Optional[dict]) -> Dict[str, Any]:
    """Mirror a successful auto-link onto the intake copy used for fan-out."""
    out = dict(doc or {})
    if not isinstance(match_result, dict) or not match_result.get("auto_linked"):
        return out
    best = match_result.get("best_match") if isinstance(match_result.get("best_match"), dict) else {}
    booking = best.get("booking_number") or ""
    if booking:
        out["matched_booking_number"] = booking
    if best.get("county"):
        out["matched_county"] = best.get("county")
    if best.get("state"):
        out["matched_state"] = best.get("state")
    if best.get("defendant_id"):
        out["matched_defendant_id"] = best.get("defendant_id")
    if match_result.get("confidence") is not None:
        out["match_confidence"] = match_result.get("confidence")
    strategy = best.get("strategy") or match_result.get("strategy") or ""
    if strategy and strategy != "ambiguous":
        out["match_strategy"] = strategy
    out["status"] = "matched"
    if not out.get("match_timestamp"):
        out["match_timestamp"] = _now()
    return out


async def intake_for_fanout(
    collection,
    intake_id: str,
    fallback: Dict[str, Any],
    match_result: Optional[dict] = None,
) -> Dict[str, Any]:
    """Reload the persisted intake after match, then fill any missing link fields.

    The Sheets outbox uses ``$setOnInsert``, so the document scheduled here is
    the one the ledger keeps. Matching writes status, county, state, strategy,
    and timestamp onto the intake row; the in-memory copy from before that
    write does not have them.
    """
    saved = None
    try:
        if collection is not None and intake_id:
            saved = await collection.find_one({"intake_id": intake_id}, {"_id": 0})
    except Exception as exc:
        logger.warning("[intake_fanout] reload before fan-out failed for %s: %s", intake_id, exc)
        saved = None
    base = dict(saved) if isinstance(saved, dict) else dict(fallback or {})
    base.pop("_id", None)
    if str(base.get("status") or "") == "matched" and base.get("match_strategy"):
        return base
    return apply_auto_link_snapshot(base, match_result)


def _short_name(full: str) -> str:
    parts = [p for p in str(full or "").split() if p]
    if not parts or parts == ["Unknown"]:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."


def ledger_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Reporting row (no SSN / DOB / DL / street address)."""
    created = doc.get("created_at")
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except Exception:
            created = None
    if not isinstance(created, datetime):
        created = _now()
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    et = created.astimezone(ET)
    d = doc.get("defendant") or {}
    return {
        "date_et": et.strftime("%Y-%m-%d"),
        "time_et": et.strftime("%H:%M:%S"),
        "intake_id": doc.get("intake_id", ""),
        "source": doc.get("source", ""),
        "form_type": doc.get("form_type", ""),
        "submitted_by_role": doc.get("submitted_by_role", ""),
        "defendant_name": doc.get("defendant_name", ""),
        "indemnitor_name": doc.get("indemnitor_name", "") if doc.get("indemnitor_name") != "Unknown" else "",
        "county": doc.get("matched_county") or doc.get("defendant_county") or d.get("county") or "",
        "state": doc.get("matched_state") or doc.get("defendant_state") or d.get("state") or doc.get("state") or "",
        "booking_number": doc.get("defendant_booking_number", "") or d.get("bookingNumber", ""),
        "bond_amount": d.get("bondAmount", ""),
        "surety": doc.get("surety_id") or "",
        "surety_requested": doc.get("surety_unrecognized") or "",
        "contact_phone_last4": _last4(doc.get("indemnitor_phone") or d.get("phone")),
        "has_email": bool(doc.get("indemnitor_email") or d.get("email")),
        "match_confidence": doc.get("match_confidence") if doc.get("match_confidence") is not None else "",
        "matched_booking_number": doc.get("matched_booking_number") or "",
        "match_strategy": doc.get("match_strategy") or "",
        "match_timestamp": _stamp(doc.get("match_timestamp")),
        "status": doc.get("status", ""),
    }


_SLACK_KIND = {
    "wix_webhook": "website application",
    "wix_portal": "website application",
    "telegram": "Telegram application",
    "telegram_mini_app": "Telegram mini-app application",
    "telegram_miniapp": "Telegram mini-app application",
    "walk_in": "walk-in intake",
    "phone_call": "phone intake",
    "elevenlabs_voice": "Shannon voice intake",
    "shannon": "Shannon voice intake",
    "shannon_voice": "Shannon voice intake",
    "bookmarklet": "bookmarklet booking intake",
    "manual_entry": "manual intake",
    "shamrock-leads-dashboard": "dashboard intake",
}


def slack_text(doc: Dict[str, Any]) -> str:
    row = ledger_row(doc)
    kind = _SLACK_KIND.get(str(doc.get("source") or ""), "intake")
    role = row["submitted_by_role"]
    who = ""
    if role == "indemnitor":
        who = " (Indemnitor form)"
    elif role == "defendant":
        who = " (Defendant form)"
    lines = [f"📥 *New {kind}*{who} — `{row['intake_id']}`"]
    if row["defendant_name"]:
        lines.append(f"• Defendant: *{row['defendant_name']}*")
    ind = _short_name(row["indemnitor_name"])
    if ind:
        lines.append(f"• Indemnitor: {ind}")
    if row["county"]:
        lines.append(f"• County: {row['county']}")
    if row["booking_number"]:
        lines.append(f"• Booking #: {row['booking_number']}")
    if row["contact_phone_last4"]:
        lines.append(f"• Phone: …{row['contact_phone_last4']}")
    if row["matched_booking_number"]:
        lines.append(f"• Auto-match: {row['matched_booking_number']} ({row['match_confidence']})")
    else:
        lines.append("• Auto-match: none yet — review in CRM Intake Queue")
    if row["surety_requested"]:
        lines.append(f"• ⚠️ Surety named but not active: {row['surety_requested']}")
    base = (os.getenv("DASHBOARD_PUBLIC_URL") or "https://leads.shamrockbailbonds.biz").rstrip("/")
    lines.append(f"<{base}/#intake|Open Intake Queue>")
    return "\n".join(lines)


# ── Senders (return (ok, error, unconfigured)) ────────────────────────────────

async def _send_sheets(row: Dict[str, Any]) -> tuple[bool, str, bool]:
    url = (os.getenv("GAS_WEB_APP_URL") or "").strip()
    key = (os.getenv("GAS_API_KEY") or "").strip()
    if not url or not key:
        return False, "GAS_WEB_APP_URL/GAS_API_KEY not set", True
    import httpx

    body = {"action": "appendIntakeLedger", "apiKey": key, "row": row}
    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
        resp = await client.post(url, json=redact_internal_keys(body))
    try:
        data = resp.json()
    except Exception:
        return False, f"GAS HTTP {resp.status_code} non-JSON", False
    if resp.status_code == 200 and isinstance(data, dict) and data.get("success") is True:
        return True, "", False
    err = (data or {}).get("error") if isinstance(data, dict) else ""
    return False, f"GAS HTTP {resp.status_code}: {str(err)[:200]}", False


async def _send_slack(text: str) -> tuple[bool, str, bool]:
    env = "SLACK_WEBHOOK_INTAKE" if (os.getenv("SLACK_WEBHOOK_INTAKE") or "").strip() else "SLACK_WEBHOOK_LEADS"
    if not (os.getenv(env) or "").strip():
        return False, "SLACK_WEBHOOK_INTAKE/SLACK_WEBHOOK_LEADS not set", True
    from dashboard.services.automation_digest import post_slack

    ok = await post_slack(text, webhook_env=env)
    return (True, "", False) if ok else (False, "Slack webhook post failed", False)


async def _deliver(target: str, payload: Dict[str, Any]) -> tuple[bool, str, bool]:
    try:
        if target == "sheets":
            return await _send_sheets(payload["row"])
        if target == "slack":
            return await _send_slack(payload["text"])
        return False, f"unknown target {target}", False
    except Exception as exc:  # network, timeouts, bad config
        return False, f"{type(exc).__name__}: {str(exc)[:200]}", False


# ── Outbox ────────────────────────────────────────────────────────────────────

def _outbox():
    from dashboard.extensions import get_collection

    return get_collection(OUTBOX)


async def _attempt(row_doc: Dict[str, Any]) -> str:
    """Try one outbox row once and record the result. Returns the new status."""
    col = _outbox()
    target = row_doc["target"]
    if _disabled():
        return row_doc.get("status") or "pending"
    # Atomic claim so the post-save task and the retry cron never double-send.
    now = _now()
    claimed = await col.find_one_and_update(
        {
            "intake_id": row_doc["intake_id"],
            "target": target,
            "$or": [
                {"status": {"$in": ["pending", "failed", "skipped_unconfigured"]}},
                {"status": "in_flight", "next_attempt_at": {"$lte": now}},
            ],
        },
        {"$set": {"status": "in_flight", "next_attempt_at": now + timedelta(seconds=600), "updated_at": now}},
    )
    if not claimed:
        return "claimed_elsewhere"
    row_doc = claimed
    attempts = int(row_doc.get("attempts") or 0)
    ok, err, unconfigured = await _deliver(target, row_doc.get("payload") or {})
    now = _now()
    if ok:
        status = "sent"
        update = {"status": status, "sent_at": now, "last_error": "", "updated_at": now}
        logger.info("[intake_fanout] %s → %s sent", row_doc.get("intake_id"), target)
    else:
        attempts += 1
        if attempts >= MAX_ATTEMPTS:
            status = "dead"
            logger.error("[intake_fanout] %s → %s DEAD after %s attempts: %s",
                         row_doc.get("intake_id"), target, attempts, err)
        else:
            status = "skipped_unconfigured" if unconfigured else "failed"
            logger.warning("[intake_fanout] %s → %s %s (attempt %s): %s",
                           row_doc.get("intake_id"), target, status, attempts, err)
        delay = BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
        update = {
            "status": status,
            "attempts": attempts,
            "last_error": err,
            "next_attempt_at": now + timedelta(seconds=delay),
            "updated_at": now,
        }
    await col.update_one({"intake_id": row_doc["intake_id"], "target": target}, {"$set": update})
    return status


async def enqueue(intake_doc: Dict[str, Any], targets=TARGETS) -> None:
    """Create (idempotently) one outbox row per target. Raises nothing."""
    try:
        col = _outbox()
        now = _now()
        row = ledger_row(intake_doc)
        text = slack_text(intake_doc)
        for target in targets:
            payload = {"row": row} if target == "sheets" else {"text": text}
            await col.update_one(
                {"intake_id": intake_doc.get("intake_id"), "target": target},
                {"$setOnInsert": {
                    "intake_id": intake_doc.get("intake_id"),
                    "target": target,
                    "status": "pending",
                    "attempts": 0,
                    "next_attempt_at": now,
                    "last_error": "",
                    "payload": payload,
                    "created_at": now,
                    "updated_at": now,
                }},
                upsert=True,
            )
    except Exception as exc:
        logger.error("[intake_fanout] enqueue failed for %s: %s", intake_doc.get("intake_id"), exc)


async def process_intake(intake_id: str) -> Dict[str, str]:
    """Attempt every due, unsent row for one intake. Raises nothing."""
    out: Dict[str, str] = {}
    try:
        col = _outbox()
        async for row_doc in col.find({"intake_id": intake_id, "status": {"$nin": ["sent", "dead"]}}):
            out[row_doc["target"]] = await _attempt(row_doc)
    except Exception as exc:
        logger.error("[intake_fanout] process failed for %s: %s", intake_id, exc)
    return out


def schedule_after_save(intake_doc: Dict[str, Any]) -> Optional[asyncio.Task]:
    """Fire-and-forget: enqueue + first attempt in a background task.

    Call ONLY after the Mongo write succeeded. Returns immediately; the task's
    exceptions are swallowed and logged. Missed attempts are picked up by the
    retry cron because the outbox row is written first.
    """
    async def _run():
        await enqueue(intake_doc)
        await process_intake(intake_doc.get("intake_id"))

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(_run(), name=f"intake_fanout_{intake_doc.get('intake_id')}")
    def _done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.error("[intake_fanout] background task error: %s", exc)

    task.add_done_callback(_done)
    return task


async def retry_due(limit: int = 100) -> Dict[str, int]:
    """Cron entry point: retry failed / unconfigured / stuck-pending rows that are due."""
    counts: Dict[str, int] = {}
    if _disabled():
        return counts
    try:
        col = _outbox()
        now = _now()
        cursor = col.find({
            "status": {"$in": ["pending", "failed", "skipped_unconfigured", "in_flight"]},
            "next_attempt_at": {"$lte": now},
            "attempts": {"$lt": MAX_ATTEMPTS},
        }).limit(limit)
        async for row_doc in cursor:
            status = await _attempt(row_doc)
            counts[status] = counts.get(status, 0) + 1
    except Exception as exc:
        logger.error("[intake_fanout] retry sweep failed: %s", exc)
    return counts
