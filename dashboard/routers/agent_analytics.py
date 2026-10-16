from __future__ import annotations

"""ShamrockLeads — Agent Performance Analytics API
==================================================
Measures the operational output of every AI agent in the digital workforce.

Endpoints:
  GET /api/analytics/agent-performance   — Full workforce scorecard
  GET /api/analytics/agent/:agent_id     — Individual agent deep-dive
  GET /api/analytics/scraper-accuracy    — Scraper fleet accuracy metrics
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from dashboard.extensions import get_db
from dashboard.services.payment_log_claims import (
    REVERSAL_KINDS,
    SETTLED_CHARGE_STATUSES,
    row_kind,
)

logger = logging.getLogger(__name__)

agent_analytics_bp = APIRouter(prefix="/api", tags=["agent_analytics"])
def _utc_now():
    return datetime.now(timezone.utc)


def _period_range(days: int = 30):
    """Return (start, end) for the given lookback period."""
    end = _utc_now()
    start = end - timedelta(days=days)
    return start, end


# ── Payment agent: settled, deduped by transaction id ─────────────────────
# Settled statuses are the recorder's ``SETTLED_CHARGE_STATUSES`` (case-
# insensitive): approved, paid, completed, complete, success, succeeded,
# captured, settled. A webhook event counts when the recorder classifies it
# as a charge (``payment.completed``, ``payment.captured``, and the other
# successful charge events). Refunds, failures, and ``needs_review`` rows
# are excluded. Plan /pay rows carry ``timestamp`` and poller / webhook rows
# ``created_at``; both count. The KPI query keeps a row only when
# ``timestamp`` or ``created_at`` is inside the report window (``start``
# through ``end``, inclusive), so a future ``paid_at`` on ``timestamp`` is
# outside the current period.
#  * Rows are grouped by transaction id FIRST. If any row is a recorder
#    reversal (``REVERSAL_KINDS`` via ``row_kind``: refund or failure,
#    including failed and declined) or a void / chargeback / dispute, the
#    whole transaction is excluded (conservative: a partial refund also
#    drops it from these KPIs).
#  * A transaction id counts once, preferring the plan-linked row, then a
#    ``completed`` row.
#  * A plan down-payment row with no transaction id (``POST /payments/plans``
#    before it stored one) is suppressed when a settled card row (poller /
#    webhook) for the same booking and exact amount exists within
#    ``_DOWN_PAYMENT_MATCH_WINDOW``; each card row absorbs at most one.

_REVERSAL_MARKERS = ("refund", "void", "chargeback", "charge_back", "reversal", "reversed", "dispute")
_DOWN_PAYMENT_MATCH_WINDOW = timedelta(days=3)
_PAYMENT_AGENT_FIELDS = {
    "_id": 1, "transaction_id": 1, "amount": 1, "status": 1, "plan_id": 1, "type": 1,
    "source": 1, "booking_number": 1, "timestamp": 1, "created_at": 1,
    "raw_payload.event_type": 1, "event_type": 1, "event_kind": 1,
    "needs_review": 1, "method": 1,
}


def _event(row: dict) -> str:
    raw = row.get("raw_payload")
    if isinstance(raw, dict) and raw.get("event_type") is not None:
        return str(raw.get("event_type")).strip().lower()
    return ""


def _status(row: dict) -> str:
    return str(row.get("status") or "").strip().lower()


def _is_reversal(row: dict) -> bool:
    """A refund or a failure sibling drops the whole transaction.

    ``row_kind`` covers the recorder's ``REVERSAL_KINDS`` (refund and
    failure, including failed and declined). Void, chargeback, and dispute
    markers still drop the transaction when the recorder stores them as a
    charge-shaped status.
    """
    if row_kind(row) in REVERSAL_KINDS:
        return True
    text = f"{_status(row)} {_event(row)}"
    return any(marker in text for marker in _REVERSAL_MARKERS)


def _settled_payment(row: dict) -> bool:
    """Successful charge only.

    The status must be one the provider recorder writes for a charge
    (``SETTLED_CHARGE_STATUSES``). The recorder's ``row_kind`` must be
    ``charge``, so a refund or failure event is out even when the status
    text looks settled. A row stored with ``needs_review`` is out.
    """
    if row.get("needs_review"):
        return False
    if _status(row) not in SETTLED_CHARGE_STATUSES:
        return False
    return row_kind(row) == "charge"


def _payment_rank(row: dict) -> int:
    if str(row.get("plan_id") or "").strip():
        return 0
    if _status(row) == "completed":
        return 1
    return 2


def _amount(row: dict) -> float:
    try:
        value = float(row.get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0
    return value if value == value and value not in (float("inf"), float("-inf")) else 0.0


def _cents(row: dict) -> int:
    return int(round(_amount(row) * 100))


def _when(row: dict) -> Optional[datetime]:
    for key in ("timestamp", "created_at"):
        val = row.get(key)
        if isinstance(val, datetime):
            return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
        if isinstance(val, str) and val.strip():
            try:
                dt = datetime.fromisoformat(val.strip().replace("Z", "+00:00"))
            except ValueError:
                continue
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _is_card_log_row(row: dict) -> bool:
    """Poller / webhook row (provider-logged card payment), not plan-linked."""
    if str(row.get("plan_id") or "").strip():
        return False
    return row.get("source") == "swipesimple_gmail_poll" or isinstance(row.get("raw_payload"), dict)


_CARD_METHOD_MARKERS = ("card", "credit", "debit", "swipe")


def _is_untracked_card_down_payment(row: dict) -> bool:
    """A down payment with no transaction id whose recorded method is a card.
    Cash, check, other or missing methods are never matched to a card row
    (Codex P2: a distinct cash payment must not be dropped)."""
    if str(row.get("type") or "").strip().lower() != "down_payment":
        return False
    if str(row.get("transaction_id") or "").strip():
        return False
    method = str(row.get("method") or "").strip().lower()
    return any(m in method for m in _CARD_METHOD_MARKERS)


def _card_matches(row: dict, card: dict) -> bool:
    booking = str(row.get("booking_number") or "").strip()
    if not booking or str(card.get("booking_number") or "").strip() != booking:
        return False
    if _cents(card) != _cents(row):
        return False
    when, c_when = _when(row), _when(card)
    if when is not None and c_when is not None and abs(c_when - when) > _DOWN_PAYMENT_MATCH_WINDOW:
        return False
    return True


def summarize_settled_payments(rows) -> dict:
    """{count, total, duplicates_skipped, unsettled_skipped, reversed_skipped,
    amount_conflicts, down_payments_matched, down_payments_reversed} — settled
    money, one per transaction id. Settled rows that share an id but disagree
    on the cents are left out."""
    rows = list(rows or [])
    by_txn: dict = {}
    loose: list = []
    for idx, row in enumerate(rows):
        txn = str(row.get("transaction_id") or "").strip()
        if txn:
            by_txn.setdefault(txn, []).append(row)
        else:
            loose.append((idx, row))

    unsettled = dupes = reversed_txns = amount_conflicts = 0
    chosen: list = []
    reversed_cards: list = []
    for txn, group in by_txn.items():
        if any(_is_reversal(r) for r in group):
            reversed_txns += 1
            # Keep the provider's original charge (not the refund row) so a
            # legacy card down payment for it can be excluded too (Codex P2).
            logs = [r for r in group if _is_card_log_row(r)]
            original = [r for r in logs if not _is_reversal(r)] or logs
            if original:
                reversed_cards.append(original[0])
            continue
        settled = [r for r in group if _settled_payment(r)]
        unsettled += len(group) - len(settled)
        if not settled:
            continue
        # A plan row and a provider row (or two settled rows) that share this
        # id but disagree on the cents are a conflict. Leave the transaction
        # out instead of reporting the plan amount (Codex P2).
        if len({_cents(r) for r in settled}) > 1:
            amount_conflicts += 1
            continue
        dupes += len(settled) - 1
        chosen.append(min(settled, key=_payment_rank))  # min keeps first on ties

    card_rows = [r for r in chosen if _is_card_log_row(r)]
    absorbed: set = set()
    reversed_used: set = set()
    matched = dp_reversed = 0
    for idx, row in loose:
        if _is_reversal(row) or not _settled_payment(row):
            unsettled += 1
            continue
        if _is_untracked_card_down_payment(row):
            hit = next((i for i, card in enumerate(card_rows)
                        if i not in absorbed and _card_matches(row, card)), None)
            if hit is not None:
                absorbed.add(hit)
                matched += 1
                continue
            # Its card charge was refunded / charged back: fail closed.
            rhit = next((i for i, card in enumerate(reversed_cards)
                         if i not in reversed_used and _card_matches(row, card)), None)
            if rhit is not None:
                reversed_used.add(rhit)
                dp_reversed += 1
                continue
        chosen.append(row)

    total = sum(_amount(r) for r in chosen)
    return {
        "count": len(chosen),
        "total": round(total, 2),
        "duplicates_skipped": dupes,
        "unsettled_skipped": unsettled,
        "reversed_skipped": reversed_txns,
        "amount_conflicts": amount_conflicts,
        "down_payments_matched": matched,
        "down_payments_reversed": dp_reversed,
    }


# ── Agent definitions with collection mappings ────────────────────────────
AGENT_PROFILES = {
    "the_clerk": {
        "name": "The Clerk",
        "role": "Jail Roster Parsing & Data Ingestion",
        "icon": "📋",
        "metrics_source": "arrests",
    },
    "the_analyst": {
        "name": "The Analyst",
        "role": "Lead Scoring & Risk Classification",
        "icon": "🎯",
        "metrics_source": "arrests",
    },
    "the_matcher": {
        "name": "The Matcher",
        "role": "Defendant-Indemnitor Linking",
        "icon": "🔗",
        "metrics_source": "matches",
    },
    "the_closer": {
        "name": "The Closer",
        "role": "iMessage Outreach & Lead Recovery",
        "icon": "💬",
        "metrics_source": "outreach_sequences",
    },
    "the_finder": {
        "name": "The Finder",
        "role": "OSINT Contact Discovery",
        "icon": "🔍",
        "metrics_source": "defendants",
    },
    "the_paperwork_agent": {
        "name": "The Paperwork Agent",
        "role": "SignNow Packet Generation",
        "icon": "📝",
        "metrics_source": "paperwork_packets",
    },
    "the_signature_agent": {
        "name": "The Signature Agent",
        "role": "E-Signature Orchestration",
        "icon": "✍️",
        "metrics_source": "paperwork_packets",
    },
    "the_payment_agent": {
        "name": "The Payment Agent",
        "role": "Premium Collection Tracking",
        "icon": "💳",
        "metrics_source": "payments",
    },
    "shannon": {
        "name": "Shannon",
        "role": "AI iMessage Auto-Reply Agent",
        "icon": "🤖",
        "metrics_source": "imessage_conversations",
    },
    "rearrest_detector": {
        "name": "Re-Arrest Detector",
        "role": "Active Bond Cross-Reference",
        "icon": "🚨",
        "metrics_source": "rearrest_alerts",
    },
    "the_court_clerk": {
        "name": "The Court Clerk",
        "role": "Court Date Tracking & Reminders",
        "icon": "⚖️",
        "metrics_source": "court_reminders",
    },
    "discharge_monitor": {
        "name": "Discharge Monitor",
        "role": "Gmail Exoneration Scanner",
        "icon": "📧",
        "metrics_source": "active_bonds",
    },
}


@agent_analytics_bp.get("/analytics/agent-performance")
async def agent_performance(days: str = Query(default="30")):
    """Full digital workforce scorecard."""
    try:
        db = get_db()
        days = int(days)
        start, end = _period_range(days)
        start_iso = start.isoformat()
        end_iso = end.isoformat()

        results = []

        # ── The Clerk (Scraper) ──────────────────────────────────────────
        arrests_col = db["arrests"]
        total_scraped = await arrests_col.count_documents(
            {"scraped_at": {"$gte": start_iso}}
        )
        counties = await arrests_col.distinct("county", {"scraped_at": {"$gte": start_iso}})
        results.append({
            **AGENT_PROFILES["the_clerk"],
            "agent_id": "the_clerk",
            "period_days": days,
            "kpis": {
                "records_processed": total_scraped,
                "counties_active": len(counties),
                "daily_avg": round(total_scraped / max(days, 1), 1),
            },
            "status": "active" if total_scraped > 0 else "idle",
        })

        # ── The Analyst (Lead Scoring) ───────────────────────────────────
        scored = await arrests_col.count_documents(
            {"lead_score": {"$exists": True, "$ne": None}, "scraped_at": {"$gte": start_iso}}
        )
        hot = await arrests_col.count_documents(
            {"lead_status": "Hot", "scraped_at": {"$gte": start_iso}}
        )
        warm = await arrests_col.count_documents(
            {"lead_status": "Warm", "scraped_at": {"$gte": start_iso}}
        )
        cold = await arrests_col.count_documents(
            {"lead_status": "Cold", "scraped_at": {"$gte": start_iso}}
        )
        disq = await arrests_col.count_documents(
            {"lead_status": "Disqualified", "scraped_at": {"$gte": start_iso}}
        )
        scoring_rate = round((scored / max(total_scraped, 1)) * 100, 1)
        results.append({
            **AGENT_PROFILES["the_analyst"],
            "agent_id": "the_analyst",
            "period_days": days,
            "kpis": {
                "records_scored": scored,
                "scoring_rate_pct": scoring_rate,
                "hot_leads": hot,
                "warm_leads": warm,
                "cold_leads": cold,
                "disqualified": disq,
                "hot_rate_pct": round((hot / max(scored, 1)) * 100, 1),
            },
            "status": "active" if scored > 0 else "idle",
        })

        # ── The Matcher ──────────────────────────────────────────────────
        matches_col = db["matches"]
        total_matches = await matches_col.count_documents(
            {"created_at": {"$gte": start_iso}}
        )
        validated = await matches_col.count_documents(
            {"status": "validated", "created_at": {"$gte": start_iso}}
        )
        rejected = await matches_col.count_documents(
            {"status": "rejected", "created_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["the_matcher"],
            "agent_id": "the_matcher",
            "period_days": days,
            "kpis": {
                "total_matches": total_matches,
                "validated": validated,
                "rejected": rejected,
                "accuracy_pct": round((validated / max(total_matches, 1)) * 100, 1),
            },
            "status": "active" if total_matches > 0 else "idle",
        })

        # ── The Closer (Outreach) ────────────────────────────────────────
        outreach_col = db["outreach_sequences"]
        total_seqs = await outreach_col.count_documents(
            {"created_at": {"$gte": start_iso}}
        )
        replied = await outreach_col.count_documents(
            {"status": "replied", "created_at": {"$gte": start_iso}}
        )
        converted = await outreach_col.count_documents(
            {"status": "converted", "created_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["the_closer"],
            "agent_id": "the_closer",
            "period_days": days,
            "kpis": {
                "sequences_started": total_seqs,
                "replies_received": replied,
                "conversions": converted,
                "reply_rate_pct": round((replied / max(total_seqs, 1)) * 100, 1),
                "conversion_rate_pct": round((converted / max(total_seqs, 1)) * 100, 1),
            },
            "status": "active" if total_seqs > 0 else "idle",
        })

        # ── The Finder (Contact Discovery) ───────────────────────────────
        defendants_col = db["defendants"]
        enriched = await defendants_col.count_documents(
            {"contacts_discovered": {"$exists": True, "$ne": []},
             "updated_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["the_finder"],
            "agent_id": "the_finder",
            "period_days": days,
            "kpis": {"defendants_enriched": enriched},
            "status": "active" if enriched > 0 else "idle",
        })

        # ── Paperwork & Signature ────────────────────────────────────────
        packets_col = db["paperwork_packets"]
        packets_gen = await packets_col.count_documents(
            {"created_at": {"$gte": start_iso}}
        )
        packets_signed = await packets_col.count_documents(
            {"status": "completed", "created_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["the_paperwork_agent"],
            "agent_id": "the_paperwork_agent",
            "period_days": days,
            "kpis": {
                "packets_generated": packets_gen,
                "packets_signed": packets_signed,
                "completion_rate_pct": round((packets_signed / max(packets_gen, 1)) * 100, 1),
            },
            "status": "active" if packets_gen > 0 else "idle",
        })

        # ── Payments ─────────────────────────────────────────────────────
        pay_col = db["payments"]
        # Closed window. A future paid_at (AR stores it on payments.timestamp)
        # must not land in the current period, and neither must a future created_at.
        in_window = {"$gte": start_iso, "$lte": end_iso}
        pay_rows = await pay_col.find(
            {"$or": [{"timestamp": dict(in_window)}, {"created_at": dict(in_window)}]},
            _PAYMENT_AGENT_FIELDS,
        ).to_list(None)
        pay_summary = summarize_settled_payments(pay_rows)
        payments = pay_summary["count"]
        total_collected = pay_summary["total"]
        results.append({
            **AGENT_PROFILES["the_payment_agent"],
            "agent_id": "the_payment_agent",
            "period_days": days,
            "kpis": {
                "payments_recorded": payments,
                "total_collected": round(total_collected, 2),
            },
            "status": "active" if payments > 0 else "idle",
        })

        # ── Shannon (AI auto-reply) ──────────────────────────────────────
        imsg_col = db["imessage_conversations"]
        ai_replies = await imsg_col.count_documents(
            {"ai_replied": True, "updated_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["shannon"],
            "agent_id": "shannon",
            "period_days": days,
            "kpis": {"ai_replies_sent": ai_replies},
            "status": "active" if ai_replies > 0 else "idle",
        })

        # ── Re-Arrest Detector ───────────────────────────────────────────
        rearrest_col = db["rearrest_alerts"]
        alerts = await rearrest_col.count_documents(
            {"detected_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["rearrest_detector"],
            "agent_id": "rearrest_detector",
            "period_days": days,
            "kpis": {"alerts_fired": alerts},
            "status": "active" if alerts > 0 else "idle",
        })

        # ── Court Clerk ──────────────────────────────────────────────────
        court_col = db["court_reminders"]
        reminders = await court_col.count_documents(
            {"created_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["the_court_clerk"],
            "agent_id": "the_court_clerk",
            "period_days": days,
            "kpis": {"reminders_sent": reminders},
            "status": "active" if reminders > 0 else "idle",
        })

        # ── Discharge Monitor ────────────────────────────────────────────
        bonds_col = db["active_bonds"]
        discharged = await bonds_col.count_documents(
            {"status": "exonerated", "updated_at": {"$gte": start_iso}}
        )
        results.append({
            **AGENT_PROFILES["discharge_monitor"],
            "agent_id": "discharge_monitor",
            "period_days": days,
            "kpis": {"bonds_discharged": discharged},
            "status": "active" if discharged > 0 else "idle",
        })

        # Workforce health summary
        active_count = sum(1 for r in results if r["status"] == "active")
        idle_count = sum(1 for r in results if r["status"] == "idle")

        return {
            "success": True,
            "report_type": "Digital Workforce Scorecard",
            "period_days": days,
            "generated_at": _utc_now().isoformat(),
            "workforce_health": {
                "total_agents": len(results),
                "active": active_count,
                "idle": idle_count,
                "utilization_pct": round((active_count / max(len(results), 1)) * 100, 1),
            },
            "agents": results,
        }
    except Exception as exc:
        logger.exception("analytics/agent-performance error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@agent_analytics_bp.get("/analytics/scraper-accuracy")
async def scraper_accuracy(days: str = Query(default="7")):
    """Per-county scraper accuracy and performance metrics."""
    try:
        db = get_db()
        days = int(days)
        start, _ = _period_range(days)
        start_iso = start.isoformat()

        col = db["arrests"]
        pipe = [
            {"$match": {"scraped_at": {"$gte": start_iso}}},
            {"$group": {
                "_id": "$county",
                "total": {"$sum": 1},
                "with_score": {"$sum": {"$cond": [{"$gt": ["$lead_score", None]}, 1, 0]}},
                "hot": {"$sum": {"$cond": [{"$eq": ["$lead_status", "Hot"]}, 1, 0]}},
                "avg_score": {"$avg": "$lead_score"},
                "with_bond": {"$sum": {"$cond": [{"$gt": ["$bond_amount", 0]}, 1, 0]}},
            }},
            {"$sort": {"total": -1}},
        ]
        data = await col.aggregate(pipe).to_list(None)

        counties = []
        for d in data:
            total = d["total"]
            counties.append({
                "county": d["_id"],
                "total_records": total,
                "scored_pct": round((d["with_score"] / max(total, 1)) * 100, 1),
                "hot_leads": d["hot"],
                "avg_score": round(d["avg_score"] or 0, 1),
                "bond_data_pct": round((d["with_bond"] / max(total, 1)) * 100, 1),
            })

        return {
            "success": True,
            "report_type": "Scraper Accuracy by County",
            "period_days": days,
            "generated_at": _utc_now().isoformat(),
            "counties": counties,
            "total_counties": len(counties),
            "total_records": sum(c["total_records"] for c in counties),
        }
    except Exception as exc:
        logger.exception("analytics/scraper-accuracy error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

