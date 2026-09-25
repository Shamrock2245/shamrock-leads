"""
Lifecycle automations — staff-first cron jobs for the bond chain.

Jobs (all fail closed, no unsolicited client contact):
  1. Forfeiture portfolio scan → risk scores + Slack top risks
  2. SignNow status poller → sync packet status from API (legacy)
  2b. DocuSeal status poller → sync open DocuSeal submissions
  3. Signed packet → collect-payment task + Slack
  4. Compliance task backfill for active bonds
  5. Matching backlog review digest (batch_match + Slack)

PII: never log full phones/emails; Slack uses names + booking only.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def _backfill_pause(seconds: float) -> None:
    """Pause between Drive backfill uploads (rate limit). Separate for tests."""
    await asyncio.sleep(seconds)

# SignNow invite / document statuses that mean fully signed
_SIGNED_STATUSES = frozenset({
    "fulfilled", "signed", "complete", "completed", "document.complete",
})
_VOID_STATUSES = frozenset({
    "declined", "canceled", "cancelled", "void", "voided", "expired",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _booking(doc: dict) -> str:
    return (
        doc.get("booking_number")
        or doc.get("bookingNumber")
        or doc.get("Booking_Number")
        or ""
    )


def interpret_signnow_payload(payload: dict) -> str:
    """
    Map SignNow GET /document/{id} JSON → local status string.

    Returns: signed | pending | voided | unknown
    """
    if not payload or not isinstance(payload, dict):
        return "unknown"

    invites = payload.get("field_invites") or payload.get("requests") or []
    if isinstance(invites, list) and invites:
        statuses = [
            str(i.get("status") or "").lower().strip()
            for i in invites
            if isinstance(i, dict)
        ]
        statuses = [s for s in statuses if s]
        if statuses and all(s in _SIGNED_STATUSES for s in statuses):
            return "signed"
        if any(s in _VOID_STATUSES for s in statuses):
            return "voided"
        if statuses:
            return "pending"

    top = str(payload.get("status") or "").lower().strip()
    if top in _SIGNED_STATUSES:
        return "signed"
    if top in _VOID_STATUSES:
        return "voided"
    if top:
        return "pending"
    return "unknown"


class LifecycleAutomations:
    """Orchestrates portfolio / paperwork / matching lifecycle crons."""

    def __init__(self, db):
        self.db = db

    # ─────────────────────────────────────────────────────────────────────
    # 1. Forfeiture portfolio
    # ─────────────────────────────────────────────────────────────────────
    async def run_forfeiture_scan(self, config: Optional[dict] = None) -> dict:
        """Score active bonds; persist risk; Slack critical/high digest."""
        cfg = config or {}
        limit = min(int(cfg.get("limit") or 100), 300)
        min_tier_slack = (cfg.get("slack_min_tier") or "high").lower()
        create_tasks = bool(cfg.get("create_tasks", True))

        from dashboard.services.forfeiture_predictor import score_portfolio

        result = await score_portfolio(self.db, limit=limit)
        if not result.get("success"):
            return {
                "ok": False,
                "error": result.get("error", "score_portfolio failed"),
                "scanned": 0,
            }

        scored = result.get("results") or []
        updated = 0
        tasks_created = 0
        alert_rows: list[dict] = []

        tier_rank = {"critical": 3, "high": 2, "medium": 1, "low": 0}
        min_rank = tier_rank.get(min_tier_slack, 2)

        for row in scored:
            booking = (row.get("booking_number") or "").strip()
            mongo_id = (row.get("_id") or "").strip()
            try:
                filt: dict[str, Any] = {}
                if booking:
                    filt = {"booking_number": booking}
                elif mongo_id:
                    from bson import ObjectId
                    try:
                        filt = {"_id": ObjectId(mongo_id)}
                    except Exception:
                        filt = {"Bond_Case_ID": row.get("bond_case_id")}
                if not filt:
                    continue

                risk_set = {
                    "forfeiture_probability": row.get("forfeiture_probability"),
                    "forfeiture_risk_tier": row.get("risk_tier"),
                    "forfeiture_priority": row.get("priority_score"),
                    "forfeiture_signals": (row.get("warning_signals") or [])[:8],
                    "forfeiture_scored_at": _now_iso(),
                }
                ur = await self.db["active_bonds"].update_one(filt, {"$set": risk_set})
                if ur.matched_count:
                    updated += 1

                tier = (row.get("risk_tier") or "low").lower()
                if create_tasks and booking and tier_rank.get(tier, 0) >= 2:
                    try:
                        from dashboard.services.task_engine import TaskEngine
                        await TaskEngine.create_task(
                            booking_number=booking,
                            title=f"Forfeiture risk: {tier.upper()}",
                            description=(
                                f"Probability {row.get('forfeiture_probability', 0):.0%}. "
                                f"Signals: {', '.join((row.get('warning_signals') or [])[:3]) or 'n/a'}. "
                                f"Review interventions before FTA."
                            ),
                            due_date=_now(),
                            task_type="forfeiture_review",
                        )
                        tasks_created += 1
                    except Exception as te:
                        logger.debug("[forfeiture] task create: %s", te)

                if tier_rank.get(tier, 0) >= min_rank:
                    alert_rows.append({
                        "booking_number": booking or "?",
                        "defendant": row.get("defendant_name") or "Unknown",
                        "tier": row.get("risk_tier"),
                        "prob": row.get("forfeiture_probability"),
                        "amount": row.get("bond_amount"),
                        "county": row.get("county"),
                    })
            except Exception as e:
                logger.warning("[forfeiture] update row failed: %s", e)

        out = {
            "ok": True,
            "scanned": result.get("bonds_scored", 0),
            "updated": updated,
            "critical_count": result.get("critical_count", 0),
            "high_risk_count": result.get("high_risk_count", 0),
            "total_at_risk_exposure": result.get("total_at_risk_exposure", 0),
            "tasks_created": tasks_created,
            "alert_count": len(alert_rows),
            "top_risks": alert_rows[:10],
        }

        if cfg.get("slack_digest", True) and alert_rows:
            try:
                from dashboard.services.automation_digest import post_slack
                lines = [
                    f"🔴 *Forfeiture Portfolio Scan*",
                    f"Critical: *{out['critical_count']}* · High: *{out['high_risk_count']}* · "
                    f"Exposure: ${out['total_at_risk_exposure']:,.0f}",
                ]
                for r in alert_rows[:8]:
                    lines.append(
                        f"• `{r['booking_number']}` {r['defendant']} — "
                        f"{r['tier']} ({(r.get('prob') or 0):.0%}) ${r.get('amount') or 0:,.0f}"
                    )
                await post_slack("\n".join(lines), webhook_env="SLACK_WEBHOOK_ALERTS")
            except Exception as e:
                logger.debug("[forfeiture] slack: %s", e)

        return out

    # ─────────────────────────────────────────────────────────────────────
    # 2–3. SignNow poller + payment tasks
    # ─────────────────────────────────────────────────────────────────────
    async def run_docuseal_poller(self, config: Optional[dict] = None) -> dict:
        """
        Poll DocuSeal for open submissions and finish completions.
        Backup for missed webhooks (same spirit as SignNow poller).

        Every completion goes through the SAME exactly-once handler as the
        webhook (dashboard/services/docuseal_completion.py): atomic claim, then
        Drive / packet status / bond_cases / SSE / legacy-link switch /
        stage-only share invoice / court sync / Slack, each stamped once.

        Three work sets (deduped, each capped by ``limit``):
          1. retry  — packets the shared handler started but didn't finish
                      (failed step, crashed run with expired lease, or a
                      webhook-requested legacy link). No DocuSeal GET needed.
          2. drive backfill — packets completed before the shared handler
                      (status=signed, no completion record, or a record with
                      preexisting_completion) that have NO Drive link and NO
                      Drive file id. DRIVE-ONLY: every other step (bond_cases,
                      SSE, legacy payment link, share invoice, court sync,
                      Slack) is stamped skipped, never replayed — so no customer
                      message, invoice, court sync or Slack post.
                      Idempotent: dedup on Drive link AND file id (query + a
                      pre-check + the handler's own check), atomic claim.
                      Rate-limited: at most ``drive_backfill_batch`` (default
                      10, max 50) per tick with ``drive_backfill_delay_seconds``
                      (default 2.0, max 30) between them; the rest is deferred
                      to the next tick. Kill switch: ``drive_backfill_legacy:
                      false`` (or ``file_to_drive: false``). Each run logs a
                      count summary (no PII).
          3. open   — open packets: GET the submission; completed → handler.
                      Note: no longer filters on docuseal_status, so packets a
                      staff status refresh marked docuseal_status=completed
                      (before that endpoint was fixed) are picked up again.
        """
        cfg = config or {}
        limit = min(int(cfg.get("limit") or 40), 100)

        from dashboard.services.docuseal_service import DocuSealService
        from dashboard.services.docuseal_completion import (
            SOURCE_POLLER,
            drive_backfill_limits,
            drive_record_reason,
            handle_docuseal_completion,
            is_backfill_packet,
            poller_legacy_drive_query,
            poller_retry_query,
        )

        ds = DocuSealService()
        if not ds.is_configured:
            return {
                "ok": False,
                "error": "DOCUSEAL_API_KEY not configured",
                "scanned": 0,
                "skipped": True,
            }

        packets = self.db["paperwork_packets"]
        file_to_drive = bool(cfg.get("file_to_drive", True))

        backfill_enabled = file_to_drive and bool(cfg.get("drive_backfill_legacy", True))
        bf_batch, bf_delay = drive_backfill_limits(cfg)
        backfill = {
            "enabled": backfill_enabled,
            "batch": bf_batch,
            "scanned": 0,
            "uploaded": 0,
            "skipped_existing": 0,
            "failed": 0,
            "deferred": 0,
            "claim_busy": 0,
        }

        results = {
            "ok": True,
            "scanned": 0,
            "signed": 0,
            "still_pending": 0,
            "errors": 0,
            "filed_drive": 0,
            "completion_retried": 0,
            "claim_busy": 0,
            "signed_packets": [],
            "drive_backfill": backfill,
        }

        work: list[tuple[str, dict]] = []
        seen: set = set()

        async def _collect(kind: str, query: dict, cap: int) -> None:
            async for pkt in packets.find(query).limit(cap):
                key = pkt.get("_id") if pkt.get("_id") is not None else pkt.get("packet_id")
                if key in seen:
                    continue
                seen.add(key)
                k = kind
                if kind == "retry" and is_backfill_packet(pkt):
                    # A pre-handler completion whose Drive upload is being
                    # retried is still backfill: same batch/delay + kill switch.
                    if not backfill_enabled:
                        continue
                    k = "backfill"
                work.append((k, pkt))

        await _collect("retry", poller_retry_query(), limit)
        if backfill_enabled:
            # Fetch one extra so "deferred" can be reported without a count query.
            await _collect("backfill", poller_legacy_drive_query(), bf_batch + 1)
        await _collect("open", {
            "esign_provider": "docuseal",
            "status": {"$in": [
                "pending_signature", "delivered", "sent", "pending", "open",
                "pending_esign", "finalized",
            ]},
            "docuseal_submission_id": {"$exists": True, "$ne": None},
        }, limit)

        def _get_col(name: str):
            return self.db[name]

        backfill_attempted = 0
        for kind, packet in work:
            packet_id = packet.get("packet_id") or str(packet.get("_id", ""))
            sub_id = packet.get("docuseal_submission_id")
            if kind == "backfill":
                if backfill["scanned"] >= bf_batch:
                    backfill["deferred"] += 1   # next tick
                    continue
                backfill["scanned"] += 1
                if drive_record_reason(packet):
                    backfill["skipped_existing"] += 1
                    continue
            results["scanned"] += 1
            if not sub_id:
                continue
            try:
                if kind == "open":
                    sub = await ds.get_submission(sub_id)
                    status = (sub.get("status") or "").lower()
                    if status not in ("completed", "complete", "signed"):
                        await packets.update_one(
                            {"_id": packet["_id"]},
                            {"$set": {
                                "docuseal_status": status or packet.get("docuseal_status") or "pending",
                                "docuseal_polled_at": _now_iso(),
                            }},
                        )
                        results["still_pending"] += 1
                        continue
                elif kind == "backfill":
                    if backfill_attempted and bf_delay > 0:
                        await _backfill_pause(bf_delay)
                    backfill_attempted += 1
                    results["completion_retried"] += 1
                else:
                    results["completion_retried"] += 1

                res = await handle_docuseal_completion(
                    packet,
                    get_col=_get_col,
                    source=SOURCE_POLLER,
                    submission_id=sub_id,
                    event_type="poller.completed",
                    cfg=cfg,
                )
                if not res.get("claimed"):
                    results["claim_busy"] += 1
                    if kind == "backfill":
                        backfill["claim_busy"] += 1
                    continue
                if res.get("filed_drive"):
                    results["filed_drive"] += 1
                if res.get("drive_error"):
                    results.setdefault("drive_errors", []).append({
                        "packet_id": packet_id,
                        **res["drive_error"],
                    })
                if kind == "backfill":
                    if res.get("filed_drive"):
                        backfill["uploaded"] += 1
                    elif res.get("drive_error"):
                        backfill["failed"] += 1
                    elif any(
                        step == "drive_upload" and state == "skipped"
                        for step, state in res.get("ran", [])
                    ):
                        backfill["skipped_existing"] += 1
                if res.get("signed"):
                    results["signed"] += 1
                    results["signed_packets"].append({
                        "packet_id": packet_id,
                        "booking_number": _booking(packet),
                        "defendant": packet.get("defendant_name") or "Unknown",
                    })
            except Exception as e:
                results["errors"] += 1
                if kind == "backfill":
                    backfill["failed"] += 1
                logger.warning("[docuseal-poll] packet %s: err_type=%s", packet_id, type(e).__name__)

        # Per-run count summary for the Drive backfill (counts only — no PII).
        if backfill_enabled:
            logger.info(
                "[docuseal-poll] drive_backfill summary scanned=%d uploaded=%d "
                "skipped_existing=%d failed=%d deferred=%d claim_busy=%d batch=%d",
                backfill["scanned"], backfill["uploaded"], backfill["skipped_existing"],
                backfill["failed"], backfill["deferred"], backfill["claim_busy"], bf_batch,
            )
        else:
            logger.info("[docuseal-poll] drive_backfill disabled (kill switch)")

        return results

    @staticmethod
    def _packet_document_ids(packet: dict) -> list[str]:
        """Normalize document id field variants on paperwork_packets."""
        ids: list[str] = []
        raw = packet.get("signnow_document_id")
        if isinstance(raw, list):
            ids.extend(str(x) for x in raw if x)
        elif raw:
            ids.append(str(raw))
        for d in packet.get("document_ids") or []:
            if d and str(d) not in ids:
                ids.append(str(d))
        single = packet.get("document_id") or packet.get("signnow_doc_id")
        if single and str(single) not in ids:
            ids.append(str(single))
        return ids

    # ─────────────────────────────────────────────────────────────────────
    # 4. Compliance task backfill
    # ─────────────────────────────────────────────────────────────────────
    async def run_compliance_backfill(self, config: Optional[dict] = None) -> dict:
        """Ensure active bonds have the standard compliance task suite."""
        cfg = config or {}
        limit = min(int(cfg.get("limit") or 80), 200)

        active_q = {"status": {"$in": ["active", "monitoring", "alert", "reinstated"]}}
        cursor = self.db["active_bonds"].find(
            active_q,
            {"booking_number": 1, "defendant_name": 1, "court_date": 1},
        ).limit(limit)

        from dashboard.services.task_engine import TaskEngine

        results = {
            "ok": True,
            "scanned": 0,
            "backfilled": 0,
            "skipped_has_tasks": 0,
            "skipped_no_booking": 0,
            "errors": 0,
        }

        async for bond in cursor:
            results["scanned"] += 1
            booking = _booking(bond)
            if not booking:
                results["skipped_no_booking"] += 1
                continue

            try:
                existing = await self.db["tasks"].find_one({
                    "booking_number": booking,
                    "task_type": {"$in": ["check_in", "check_in_30d", "court_reminder"]},
                    "status": {"$in": ["pending", "overdue"]},
                })
                if existing:
                    results["skipped_has_tasks"] += 1
                    # Still refresh court reminder if court date exists
                    if bond.get("court_date"):
                        await TaskEngine.schedule_court_reminder(booking)
                    continue

                await TaskEngine.schedule_compliance_tasks(booking)
                results["backfilled"] += 1
            except Exception as e:
                results["errors"] += 1
                logger.warning("[compliance-backfill] %s: %s", booking, e)

        if cfg.get("slack_digest", True) and results["backfilled"]:
            try:
                from dashboard.services.automation_digest import post_slack
                await post_slack(
                    f"📋 *Compliance Task Backfill*\n"
                    f"Scanned {results['scanned']} · "
                    f"Created suites: *{results['backfilled']}* · "
                    f"Already had tasks: {results['skipped_has_tasks']}"
                )
            except Exception:
                pass

        return results

    # ─────────────────────────────────────────────────────────────────────
    # 5. Matching backlog
    # ─────────────────────────────────────────────────────────────────────
    async def run_matching_backlog(self, config: Optional[dict] = None) -> dict:
        """
        Batch-match unmatched intakes. Auto-link only when engine says so
        (matching_engine already human-gates ambiguity). Digest the rest.
        """
        cfg = config or {}
        limit = min(int(cfg.get("limit") or 50), 150)

        from dashboard.services.matching_engine import MatchingEngine

        engine = MatchingEngine(self.db)
        batch = await engine.batch_match(limit=limit)

        # Surface ambiguous / unmatched for staff
        pending_review = 0
        no_match_samples: list[dict] = []
        try:
            cursor = self.db["intake_queue"].find({
                "status": {"$in": ["pending", "in_progress", "needs_match", "review"]},
                "matched_booking_number": {"$exists": False},
            }).sort("created_at", -1).limit(15)
            async for intake in cursor:
                pending_review += 1
                if len(no_match_samples) < 8:
                    no_match_samples.append({
                        "intake_id": intake.get("intake_id") or str(intake.get("_id", ""))[:8],
                        "defendant": intake.get("defendant_name") or intake.get("defendant_first_name") or "?",
                        "county": intake.get("county") or "",
                    })
        except Exception as e:
            logger.debug("[matching-backlog] sample query: %s", e)

        out = {
            "ok": True,
            "total_processed": batch.get("total_processed", 0),
            "auto_linked": batch.get("auto_linked", 0),
            "candidates_found": batch.get("candidates_found", 0),
            "no_match": batch.get("no_match", 0),
            "still_unmatched_sample": no_match_samples,
            "pending_review_approx": pending_review,
        }

        acted = out["auto_linked"] or out["candidates_found"] or out["no_match"]
        if cfg.get("slack_digest", True) and acted:
            try:
                from dashboard.services.automation_digest import post_slack
                lines = [
                    "🔗 *Matching Backlog Sweep*",
                    f"Processed: {out['total_processed']} · "
                    f"Auto-linked: *{out['auto_linked']}* · "
                    f"Needs human (candidates): *{out['candidates_found']}* · "
                    f"No match: {out['no_match']}",
                ]
                for s in no_match_samples[:5]:
                    lines.append(
                        f"• {s.get('defendant')} ({s.get('county') or '?'}) — review intake"
                    )
                await post_slack("\n".join(lines))
            except Exception as e:
                logger.debug("[matching-backlog] slack: %s", e)

        return out
