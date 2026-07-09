"""
Automation Sweeps — Node-RED / service orchestration endpoints

Machine-authenticated (GAS_API_KEY or X-API-Key) sweeps that power:
  1. Lead qualification (score hot/warm arrests, surface high-value bonds)
  2. Bond / relationship lifecycle (stuck stages, open bonds, pipeline hygiene)
  3. Risk mitigation (FTA risk, forfeiture clocks, missed check-ins)

Node-RED tabs call these on crons; business rules stay in Mongo + Python here
or in GAS. Node-RED is the router, not the processor.

Auth: X-API-Key / X-Api-Key header or ?api_key= must match GAS_API_KEY.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dashboard.deps import get_collection

logger = logging.getLogger(__name__)

automation_bp = APIRouter(prefix="/api/automation", tags=["automation"])


def _authorized(request: Request, api_key: str = "") -> bool:
    expected = (os.getenv("GAS_API_KEY") or "").strip()
    if not expected:
        logger.error("[automation] GAS_API_KEY not configured — fail closed")
        return False
    provided = (
        request.headers.get("X-API-Key")
        or request.headers.get("X-Api-Key")
        or api_key
        or ""
    ).strip()
    return bool(provided) and provided == expected


def _unauthorized() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)


@automation_bp.get("/health")
async def automation_health():
    """Liveness for Watchdog / Node-RED."""
    return {
        "ok": True,
        "service": "automation_sweeps",
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@automation_bp.get("/schedule")
async def automation_schedule(request: Request, api_key: str = ""):
    """
    Machine-readable Node-RED / external cron pack.
    Auth: GAS_API_KEY (same as other automation sweeps).
    """
    if not _authorized(request, api_key):
        return _unauthorized()
    from dashboard.services.automation_schedule import NODE_RED_SCHEDULE

    return {
        "ok": True,
        "auth_header": "X-API-Key: <GAS_API_KEY>",
        "base_url": os.getenv("DASHBOARD_PUBLIC_URL", "https://leads.shamrockbailbonds.biz"),
        "jobs": NODE_RED_SCHEDULE,
        "in_process_crons_note": (
            "Revenue automations (speed_to_contact, paperwork_chase, intake_recovery, "
            "poa_low_stock, surety_weekly_reports) run inside the FastAPI process via "
            "dashboard/cron.py — no Node-RED required. Toggle in Super CRM Automations."
        ),
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@automation_bp.post("/ops-digest")
async def ops_digest(request: Request, api_key: str = ""):
    """
    Run lead-qualification + bond-lifecycle + risk-mitigation once and
    optionally post a compact Slack summary (for Node-RED morning pack).
    """
    if not _authorized(request, api_key):
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        body = {}

    post = bool(body.get("post_slack", True))
    # Reuse handlers by calling internal logic via nested requests is heavy —
    # run simplified combined queries here for the digest only.
    hours_back = int(body.get("hours_back") or 24)
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours_back)

    summary: dict[str, Any] = {"ok": True, "action": "ops_digest", "hours_back": hours_back}

    try:
        arrests = get_collection("arrests")
        hot = await arrests.count_documents({
            "lead_score": {"$gte": 70},
            "$or": [
                {"scraped_at": {"$gte": since}},
                {"created_at": {"$gte": since}},
            ],
        })
        summary["hot_leads_24h"] = hot
    except Exception as e:
        summary["hot_leads_error"] = str(e)[:120]

    try:
        ab = get_collection("active_bonds")
        active_q = {"status": {"$in": ["active", "monitoring", "alert", "reinstated"]}}
        active_n = await ab.count_documents(active_q)
        missing_court = await ab.count_documents({
            **active_q,
            "$or": [
                {"court_date": {"$exists": False}},
                {"court_date": None},
                {"court_date": ""},
            ],
        })
        summary["active_bonds"] = active_n
        summary["missing_court_date"] = missing_court
    except Exception as e:
        summary["bonds_error"] = str(e)[:120]

    try:
        ab = get_collection("active_bonds")
        fort = await ab.count_documents({
            "status": {"$regex": "forfeit|fta|warrant|estreature", "$options": "i"},
        })
        summary["forfeiture_flags"] = fort
    except Exception:
        pass

    if post:
        try:
            from dashboard.services.automation_digest import digest_ops_sweep
            counts = {k: v for k, v in summary.items() if isinstance(v, int)}
            await digest_ops_sweep("ops_digest", counts)
            summary["slack_posted"] = True
        except Exception as e:
            summary["slack_posted"] = False
            summary["slack_error"] = str(e)[:120]

    summary["ts"] = now.isoformat()
    logger.info("[automation] ops-digest %s", summary)
    return summary


@automation_bp.post("/lead-qualification")
async def lead_qualification_sweep(request: Request, api_key: str = ""):
    """
    Score-surface recent arrests that need attention:
      - Hot (score ≥ 70) not yet contacted
      - Warm (40–69) aging > 2h without outreach
      - High-value bonds (>$2,500) unposted
    """
    if not _authorized(request, api_key):
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        body = {}

    hours_back = int(body.get("hours_back") or 24)
    min_hot = int(body.get("hot_threshold") or 70)
    min_warm = int(body.get("warm_threshold") or 40)
    min_bond = float(body.get("high_value_bond") or 2500)
    limit = min(int(body.get("limit") or 50), 200)

    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    arrests = get_collection("arrests")

    # Flexible timestamp field
    query_recent = {
        "$or": [
            {"scraped_at": {"$gte": since}},
            {"created_at": {"$gte": since}},
            {"booking_date": {"$gte": since.isoformat()[:10]}},
        ]
    }

    hot: list[dict[str, Any]] = []
    warm: list[dict[str, Any]] = []
    high_value: list[dict[str, Any]] = []
    disqualified = 0

    cursor = arrests.find(query_recent).sort([("scraped_at", -1)]).limit(500)
    docs = await cursor.to_list(length=500)

    for doc in docs:
        score = int(doc.get("lead_score") or 0)
        status = str(doc.get("lead_status") or doc.get("status") or "").lower()
        bond = float(doc.get("bond_amount") or doc.get("total_bond") or 0 or 0)
        if status in ("released", "disqualified") or bond == 0 and "released" in status:
            disqualified += 1
            continue

        summary = {
            "booking_number": doc.get("booking_number") or doc.get("bookingNumber"),
            "name": doc.get("full_name") or doc.get("name") or doc.get("defendant_name"),
            "county": doc.get("county"),
            "lead_score": score,
            "lead_status": doc.get("lead_status"),
            "bond_amount": bond,
            "charges": (doc.get("charges") or "")[:200],
        }

        if score >= min_hot:
            hot.append(summary)
        elif score >= min_warm:
            warm.append(summary)

        if bond >= min_bond and status not in ("posted", "bonded", "released"):
            high_value.append(summary)

    hot = hot[:limit]
    warm = warm[:limit]
    high_value = high_value[:limit]

    result = {
        "ok": True,
        "action": "lead_qualification",
        "window_hours": hours_back,
        "counts": {
            "scanned": len(docs),
            "hot": len(hot),
            "warm": len(warm),
            "high_value": len(high_value),
            "disqualified_seen": disqualified,
        },
        "hot": hot,
        "warm": warm,
        "high_value": high_value,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(
        "[automation] lead-qualification scanned=%s hot=%s warm=%s hv=%s",
        len(docs),
        len(hot),
        len(warm),
        len(high_value),
    )
    return result


@automation_bp.post("/bond-lifecycle")
async def bond_lifecycle_sweep(request: Request, api_key: str = ""):
    """
    Relationship / bond lifecycle hygiene:
      - Active bonds missing court dates
      - Pipeline stuck (no stage change > N days)
      - Open bonds approaching forfeiture windows
      - Incomplete paperwork / unsigned packets
    """
    if not _authorized(request, api_key):
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        body = {}

    stuck_days = int(body.get("stuck_days") or 3)
    limit = min(int(body.get("limit") or 40), 100)
    now = datetime.now(timezone.utc)
    stuck_before = now - timedelta(days=stuck_days)

    # Canonical collection for bonded cases (not legacy "bonds")
    bonds = get_collection("active_bonds")
    notes = get_collection("defendant_notes")
    issues: list[dict[str, Any]] = []

    # 7-status Kanban: open operational statuses only
    active_q = {
        "status": {
            "$in": [
                "active",
                "monitoring",
                "alert",
                "reinstated",
                "open",
                "posted",
                "pending",
                "Active",
                "Open",
                "Posted",
            ]
        }
    }
    try:
        active_bonds = await bonds.find(active_q).limit(300).to_list(length=300)
    except Exception as e:
        logger.warning("[automation] active_bonds query failed: %s", e)
        active_bonds = []

    missing_court = 0
    stuck = 0
    for b in active_bonds:
        booking = b.get("booking_number") or b.get("bookingNumber")
        court = b.get("court_date") or b.get("next_court_date")
        updated = b.get("updated_at") or b.get("status_updated_at") or b.get("created_at")
        stage = b.get("stage") or b.get("lifecycle_status") or b.get("status")

        flags = []
        if not court:
            flags.append("missing_court_date")
            missing_court += 1

        if isinstance(updated, datetime):
            u = updated if updated.tzinfo else updated.replace(tzinfo=timezone.utc)
            if u < stuck_before:
                flags.append("stuck_stage")
                stuck += 1

        if flags:
            issues.append(
                {
                    "booking_number": booking,
                    "defendant": b.get("defendant_name") or b.get("name"),
                    "county": b.get("county"),
                    "stage": stage,
                    "bond_amount": b.get("bond_amount") or b.get("amount"),
                    "flags": flags,
                }
            )

    # Pipeline notes stuck in early stages
    pipeline_stuck: list[dict[str, Any]] = []
    try:
        early = await notes.find(
            {
                "pipeline_stage": {
                    "$in": ["arrest", "contact", "negotiate", "paperwork", "lead", "new"]
                }
            }
        ).limit(200).to_list(length=200)
        for n in early:
            updated = n.get("updated_at") or n.get("last_contact_at") or n.get("created_at")
            if isinstance(updated, datetime):
                u = updated if updated.tzinfo else updated.replace(tzinfo=timezone.utc)
                if u < stuck_before:
                    pipeline_stuck.append(
                        {
                            "booking_number": n.get("booking_number"),
                            "stage": n.get("pipeline_stage") or n.get("lifecycle_status"),
                            "defendant": n.get("defendant_name") or n.get("name"),
                        }
                    )
    except Exception as e:
        logger.warning("[automation] defendant_notes query failed: %s", e)

    issues = issues[:limit]
    pipeline_stuck = pipeline_stuck[:limit]

    result = {
        "ok": True,
        "action": "bond_lifecycle",
        "counts": {
            "active_bonds_scanned": len(active_bonds),
            "issues": len(issues),
            "missing_court_date": missing_court,
            "stuck_stage": stuck,
            "pipeline_stuck": len(pipeline_stuck),
        },
        "issues": issues,
        "pipeline_stuck": pipeline_stuck,
        "stuck_days": stuck_days,
        "ts": now.isoformat(),
    }
    logger.info(
        "[automation] bond-lifecycle active=%s issues=%s pipeline_stuck=%s",
        len(active_bonds),
        len(issues),
        len(pipeline_stuck),
    )
    return result


@automation_bp.post("/risk-mitigation")
async def risk_mitigation_sweep(request: Request, api_key: str = ""):
    """
    Risk surfaces for Watchdog / staff:
      - High flight-risk scores
      - Upcoming court (48h) without confirmation
      - Forfeiture / FTA flags
      - Missed check-ins
    """
    if not _authorized(request, api_key):
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        body = {}

    high_risk_threshold = int(body.get("high_risk_threshold") or 70)
    court_hours = int(body.get("court_hours") or 48)
    limit = min(int(body.get("limit") or 40), 100)
    now = datetime.now(timezone.utc)
    court_until = now + timedelta(hours=court_hours)

    high_risk: list[dict[str, Any]] = []
    court_soon: list[dict[str, Any]] = []
    forfeiture: list[dict[str, Any]] = []
    missed_checkins: list[dict[str, Any]] = []

    # Bonds / defendants with risk scores
    for coll_name in ("bonds", "defendants", "active_bonds"):
        try:
            coll = get_collection(coll_name)
            cursor = coll.find(
                {
                    "$or": [
                        {"risk_score": {"$gte": high_risk_threshold}},
                        {"flight_risk": {"$gte": high_risk_threshold}},
                        {"ai_risk_score": {"$gte": high_risk_threshold}},
                    ]
                }
            ).limit(100)
            docs = await cursor.to_list(length=100)
            for d in docs:
                high_risk.append(
                    {
                        "source": coll_name,
                        "booking_number": d.get("booking_number"),
                        "name": d.get("defendant_name") or d.get("name"),
                        "risk_score": d.get("risk_score")
                        or d.get("flight_risk")
                        or d.get("ai_risk_score"),
                        "county": d.get("county"),
                    }
                )
        except Exception:
            continue

    # Court soon — active_bonds is the operational source of truth
    try:
        bonds = get_collection("active_bonds")
        # ISO string or datetime court dates in window
        court_docs = await bonds.find(
            {
                "status": {
                    "$in": [
                        "active",
                        "monitoring",
                        "alert",
                        "reinstated",
                        "open",
                        "posted",
                        "Active",
                        "Open",
                        "Posted",
                    ]
                },
                "court_date": {"$exists": True, "$nin": [None, ""]},
            }
        ).limit(200).to_list(length=200)
        for b in court_docs:
            cd = b.get("court_date") or b.get("next_court_date")
            parsed = None
            if isinstance(cd, datetime):
                parsed = cd if cd.tzinfo else cd.replace(tzinfo=timezone.utc)
            elif isinstance(cd, str) and cd:
                try:
                    parsed = datetime.fromisoformat(cd.replace("Z", "+00:00"))
                except Exception:
                    try:
                        parsed = datetime.strptime(cd[:10], "%Y-%m-%d").replace(
                            tzinfo=timezone.utc
                        )
                    except Exception:
                        parsed = None
            if parsed and now <= parsed <= court_until:
                court_soon.append(
                    {
                        "booking_number": b.get("booking_number"),
                        "name": b.get("defendant_name") or b.get("name"),
                        "court_date": str(cd),
                        "county": b.get("county"),
                    }
                )
    except Exception as e:
        logger.warning("[automation] court_soon query: %s", e)

    # Forfeiture-ish statuses on active_bonds
    try:
        bonds = get_collection("active_bonds")
        fort_docs = await bonds.find(
            {
                "status": {
                    "$regex": "forfeit|fta|warrant|estreature",
                    "$options": "i",
                }
            }
        ).limit(50).to_list(length=50)
        for b in fort_docs:
            forfeiture.append(
                {
                    "booking_number": b.get("booking_number"),
                    "name": b.get("defendant_name") or b.get("name"),
                    "status": b.get("status"),
                    "county": b.get("county"),
                }
            )
    except Exception as e:
        logger.warning("[automation] forfeiture query: %s", e)

    # Missed check-ins (if collection exists)
    try:
        checkins = get_collection("check_ins")
        cutoff = now - timedelta(hours=36)
        miss = await checkins.find(
            {
                "status": {"$in": ["missed", "failed", "no_response", "overdue"]},
                "$or": [
                    {"due_at": {"$lt": cutoff}},
                    {"created_at": {"$lt": cutoff}},
                ],
            }
        ).limit(50).to_list(length=50)
        for c in miss:
            missed_checkins.append(
                {
                    "booking_number": c.get("booking_number"),
                    "name": c.get("defendant_name") or c.get("name"),
                    "status": c.get("status"),
                    "due_at": str(c.get("due_at") or c.get("created_at") or ""),
                }
            )
    except Exception:
        pass

    high_risk = high_risk[:limit]
    court_soon = court_soon[:limit]
    forfeiture = forfeiture[:limit]
    missed_checkins = missed_checkins[:limit]

    # Priority actions for Slack / staff
    actions: list[str] = []
    if high_risk:
        actions.append(f"Review {len(high_risk)} high flight-risk defendants")
    if court_soon:
        actions.append(f"Confirm court appearance for {len(court_soon)} cases in {court_hours}h")
    if forfeiture:
        actions.append(f"URGENT: {len(forfeiture)} forfeiture/FTA status bonds")
    if missed_checkins:
        actions.append(f"Escalate {len(missed_checkins)} missed check-ins")

    result = {
        "ok": True,
        "action": "risk_mitigation",
        "counts": {
            "high_risk": len(high_risk),
            "court_soon": len(court_soon),
            "forfeiture": len(forfeiture),
            "missed_checkins": len(missed_checkins),
        },
        "high_risk": high_risk,
        "court_soon": court_soon,
        "forfeiture": forfeiture,
        "missed_checkins": missed_checkins,
        "recommended_actions": actions,
        "ts": now.isoformat(),
    }
    logger.info(
        "[automation] risk-mitigation high=%s court=%s fort=%s checkins=%s",
        len(high_risk),
        len(court_soon),
        len(forfeiture),
        len(missed_checkins),
    )
    return result


@automation_bp.post("/court-email-scan")
async def court_email_scan(request: Request, api_key: str = ""):
    """
    Scan Gmail for court dates, forfeitures, discharges, and other court events.
    Pipeline: Gmail → parse → Calendar → email clients → BlueBubbles → Slack.
    """
    if not _authorized(request, api_key):
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        body = {}

    since_hours = int(body.get("since_hours") or 24)

    try:
        import asyncio
        import os as _os

        from pymongo import MongoClient
        from dashboard.services.court_email_scheduler import CourtEmailScheduler

        uri = (_os.getenv("MONGODB_URI") or "").strip()
        if not uri:
            return JSONResponse(
                {"ok": False, "error": "MONGODB_URI not configured"},
                status_code=500,
            )

        def _sync_scan() -> dict:
            # CourtEmailScheduler uses sync PyMongo (find_one/insert_one).
            # Motor async DB makes every find_one() a coroutine → always truthy
            # → every email treated as duplicate. Match cron.py pattern.
            client = MongoClient(uri, serverSelectionTimeoutMS=10000)
            try:
                db = client[_os.getenv("MONGODB_DB_NAME", "ShamrockBailDB")]
                sched = CourtEmailScheduler(db=db)
                if since_hours <= 1:
                    return sched.process_all()
                return _process_court_emails_extended(sched, since_hours)
            finally:
                client.close()

        result = await asyncio.to_thread(_sync_scan)

        return {
            "ok": True,
            "action": "court_email_scan",
            "result": result,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.exception("[automation] court-email-scan failed: %s", exc)
        return JSONResponse(
            {"ok": False, "error": str(exc)[:400]},
            status_code=500,
        )


def _process_court_emails_extended(sched, since_hours: int) -> dict:
    """Run court email pipeline with a custom lookback window."""
    from dashboard.services.gmail_reader import GmailReaderService
    from dashboard.services.court_email_processor import CourtEmailProcessor

    stats = {
        "processed": 0,
        "skipped_duplicate": 0,
        "errors": 0,
        "calendar_events_created": 0,
        "messages_sent": 0,
        "emails_sent": 0,
        "by_type": {"courtDate": 0, "forfeiture": 0, "discharge": 0, "unknown": 0},
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "since_hours": since_hours,
    }

    reader = GmailReaderService()
    if not reader.is_configured:
        stats["error"] = "gmail_not_configured"
        return stats

    emails = reader.fetch_unread_court_emails(since_hours=since_hours)
    if not emails:
        return stats

    for email_data in emails:
        try:
            msg_id = email_data["message_id"]
            if sched._is_duplicate(msg_id):
                stats["skipped_duplicate"] += 1
                continue

            parsed = CourtEmailProcessor.process_email(
                subject=email_data["subject"],
                body=email_data["body"],
                sender=email_data["sender"],
            )
            event_type = parsed.get("event_type", "unknown")
            stats["by_type"][event_type] = stats["by_type"].get(event_type, 0) + 1
            case_number = parsed.get("case_number")

            if case_number and parsed.get("datetime_info"):
                try:
                    from dashboard.services.google_calendar_service import GoogleCalendarService
                    cal_svc = GoogleCalendarService()
                    event = cal_svc.create_event(parsed)
                    if event:
                        stats["calendar_events_created"] += 1
                except Exception as cal_err:
                    logger.warning("[automation] calendar: %s", cal_err)

            sched._notify_slack(event_type, parsed)

            if event_type == "discharge" and case_number:
                try:
                    stats["bonds_exonerated"] = stats.get("bonds_exonerated", 0) + sched._auto_exonerate_bond(
                        case_number=case_number,
                        defendant_name=parsed.get("defendant_name"),
                        note=f"Discharge email: {email_data.get('subject', '')}",
                    )
                except Exception:
                    pass

            contacts = sched._find_notification_contacts(
                parsed.get("defendant_name"), case_number
            )
            stats["emails_sent"] += sched._send_court_emails(parsed, contacts, event_type)
            sms_text = CourtEmailProcessor.generate_sms_summary(parsed)
            if sms_text:
                for phone in contacts.get("phones") or []:
                    sched._send_bb_notification(phone, sms_text)
                    stats["messages_sent"] += 1

            if event_type == "courtDate" and parsed.get("datetime_info"):
                try:
                    sched._schedule_court_reminders(parsed, contacts)
                except Exception:
                    pass

            sched._log_processed_email(msg_id, email_data, parsed)
            try:
                reader.mark_as_read(msg_id)
            except Exception:
                pass
            stats["processed"] += 1
        except Exception as e:
            logger.error("[automation] email process error: %s", e)
            stats["errors"] += 1

    return stats


@automation_bp.post("/bond-report")
async def generate_bond_report(request: Request, api_key: str = ""):
    """
    Generate official OSI or Palmetto bond report (XLSX) — Fortune-50 internal style
    matching historical Shamrock bond report workbooks.
    """
    if not _authorized(request, api_key):
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        body = {}

    surety = (body.get("surety") or "OSI").upper()
    if surety not in ("OSI", "PALMETTO"):
        surety = "OSI"
    include_discharges = bool(body.get("include_discharges", True))
    store = bool(body.get("store", True))

    try:
        from dashboard.services.bond_report_xlsx import (
            build_official_bond_report,
            filename_for,
        )
        import base64

        bonds_col = get_collection("active_bonds")
        # Active / open bonds for surety
        q = {
            "status": {
                "$nin": [
                    "void", "voided", "expired", "exonerated", "surrendered",
                    "discharged", "forfeited", "closed", "cancelled",
                ]
            }
        }
        # surety match flexible
        surety_q = {
            "$or": [
                {"surety": {"$regex": surety, "$options": "i"}},
                {"surety_id": {"$regex": surety, "$options": "i"}},
                {"insurance_company": {"$regex": surety, "$options": "i"}},
            ]
        }
        # If no surety fields, still include all and filter in builder
        docs = await bonds_col.find({**q}).sort("bond_date", -1).to_list(2000)

        voids = await bonds_col.find(
            {"status": {"$in": ["void", "voided", "expired", "VOID"]}}
        ).to_list(500)

        discharges = []
        if include_discharges:
            discharges = await bonds_col.find(
                {"status": {"$in": ["exonerated", "surrendered", "discharged"]}}
            ).sort("updated_at", -1).to_list(500)

        xlsx_bytes = build_official_bond_report(
            docs,
            surety=surety,
            report_type="Active Bond Liability Report",
            voids=voids,
            discharges=discharges,
        )
        fname = filename_for(surety, "Bond_Report")

        meta = {
            "ok": True,
            "action": "bond_report",
            "surety": surety,
            "filename": fname,
            "size_bytes": len(xlsx_bytes),
            "active_rows_scanned": len(docs),
            "voids": len(voids),
            "discharges": len(discharges),
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        if store:
            try:
                reports = get_collection("generated_reports")
                await reports.insert_one({
                    **meta,
                    "report_type": "bond_report",
                    "created_at": datetime.now(timezone.utc),
                    # store base64 for later download (keep under 15MB docs)
                    "xlsx_b64": base64.b64encode(xlsx_bytes).decode("ascii")
                    if len(xlsx_bytes) < 12_000_000
                    else None,
                })
            except Exception as store_err:
                logger.warning("[automation] report store failed: %s", store_err)

        # Return base64 for Node-RED / Slack file upload pipelines
        meta["xlsx_base64"] = base64.b64encode(xlsx_bytes).decode("ascii")
        return meta
    except Exception as exc:
        logger.exception("[automation] bond-report failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=500)


@automation_bp.post("/discharge-report")
async def generate_discharge_report(request: Request, api_key: str = ""):
    """
    Official discharge / exoneration report — same grammar as bond reports
    (Power #, defendant names, liability, premium, BUF, discharge date).
    """
    if not _authorized(request, api_key):
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        body = {}

    surety = (body.get("surety") or "ALL").upper()
    days_back = int(body.get("days_back") or 90)

    try:
        from dashboard.services.bond_report_xlsx import (
            build_official_bond_report,
            filename_for,
        )
        import base64

        since = datetime.now(timezone.utc) - timedelta(days=days_back)
        since_iso = since.isoformat()
        bonds_col = get_collection("active_bonds")
        # Apply days_back window across timestamp field variants (datetime + ISO strings)
        date_window = {
            "$or": [
                {"updated_at": {"$gte": since}},
                {"exonerated_at": {"$gte": since}},
                {"exonerated_at": {"$gte": since_iso}},
                {"discharge_date": {"$gte": since}},
                {"discharge_date": {"$gte": since_iso}},
                {"status_updated_at": {"$gte": since}},
            ]
        }
        q: dict[str, Any] = {
            "status": {"$in": ["exonerated", "surrendered", "discharged"]},
            **date_window,
        }
        docs = await bonds_col.find(q).sort("updated_at", -1).to_list(1000)

        # Also merge discharge_queue items that may not yet be on bonds
        try:
            dq = get_collection("discharge_queue")
            dq_q: dict[str, Any] = {
                "status": {"$in": ["processed", "pending", "done"]},
                "$or": [
                    {"processed_at": {"$gte": since}},
                    {"created_at": {"$gte": since}},
                    {"processed_at": {"$gte": since_iso}},
                    {"created_at": {"$gte": since_iso}},
                ],
            }
            queued = await dq.find(dq_q).to_list(200)
            for item in queued:
                docs.append({
                    "defendant_name": item.get("defendant_name"),
                    "case_number": item.get("case_number") or item.get("booking_number"),
                    "county": item.get("county"),
                    "status": "exonerated",
                    "discharge_date": item.get("processed_at") or item.get("created_at"),
                    "notes": "From discharge_queue",
                    "surety": item.get("surety") or surety if surety != "ALL" else "OSI",
                })
        except Exception:
            pass

        targets = ["OSI", "PALMETTO"] if surety == "ALL" else [surety]
        # Single workbook for primary surety (OSI default when ALL — include all rows)
        primary = targets[0]
        xlsx_bytes = build_official_bond_report(
            [],  # no active lines — discharge-focused
            surety=primary if surety != "ALL" else "OSI",
            report_type="Discharge / Exoneration Report",
            title_override="Discharge / Exoneration Register",
            discharges=docs,
            voids=[],
        )
        fname = filename_for(primary if surety != "ALL" else "OSI", "Discharge_Report")

        meta = {
            "ok": True,
            "action": "discharge_report",
            "surety": surety,
            "filename": fname,
            "size_bytes": len(xlsx_bytes),
            "discharge_rows": len(docs),
            "days_back": days_back,
            "xlsx_base64": base64.b64encode(xlsx_bytes).decode("ascii"),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        try:
            reports = get_collection("generated_reports")
            await reports.insert_one({
                **{k: v for k, v in meta.items() if k != "xlsx_base64"},
                "report_type": "discharge_report",
                "created_at": datetime.now(timezone.utc),
                "xlsx_b64": meta["xlsx_base64"] if len(xlsx_bytes) < 12_000_000 else None,
            })
        except Exception:
            pass
        return meta
    except Exception as exc:
        logger.exception("[automation] discharge-report failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=500)
