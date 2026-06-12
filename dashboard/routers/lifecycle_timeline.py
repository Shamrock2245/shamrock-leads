
"""
ShamrockLeads — Bond Lifecycle Timeline API
Aggregates the complete history of a bond from arrest through discharge
across ALL collections: arrests, defendant_notes, prospective_bonds,
active_bonds, audit_events, court_reminders, imessage_outreach, payments.

GET /api/lifecycle/<booking_number>
Returns a unified timeline with stage, meta, and all events sorted by timestamp.
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from dashboard.extensions import get_collection
import logging

logger = logging.getLogger(__name__)
lifecycle_timeline_bp = APIRouter(prefix="/api", tags=["lifecycle_timeline"])
def _iso(dt):
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt) if dt else None


def _ts(doc, *fields):
    """Extract the best timestamp from a document."""
    for f in fields:
        v = doc.get(f)
        if v:
            if isinstance(v, datetime):
                return v
            try:
                return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            except Exception:
                pass
    return datetime.min.replace(tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle stages in order
# ─────────────────────────────────────────────────────────────────────────────
STAGES = [
    {"id": "arrested",    "label": "Arrested",    "icon": "🚨"},
    {"id": "contacted",   "label": "Contacted",   "icon": "📞"},
    {"id": "negotiating", "label": "Negotiating", "icon": "💬"},
    {"id": "paperwork",   "label": "Paperwork",   "icon": "📋"},
    {"id": "bonded",      "label": "Bonded",      "icon": "✅"},
    {"id": "active",      "label": "Active",      "icon": "🔒"},
    {"id": "court",       "label": "Court",       "icon": "🏛️"},
    {"id": "discharged",  "label": "Discharged",  "icon": "🎉"},
]

STAGE_ORDER = {s["id"]: i for i, s in enumerate(STAGES)}


def _determine_current_stage(arrest, notes, pb, ab):
    """Determine the furthest lifecycle stage reached."""
    if ab:
        status = ab.get("status", "active")
        if status in ("exonerated", "discharged", "surrendered"):
            return "discharged"
        return "active"
    if pb:
        stage = pb.get("stage", "contacted")
        if stage == "ready":
            return "paperwork"
        return stage if stage in STAGE_ORDER else "contacted"
    if notes:
        status = notes.get("shamrock_status", "new")
        if status in STAGE_ORDER:
            return status
        if status not in ("new", "cold", "disqualified"):
            return "contacted"
    return "arrested"


@lifecycle_timeline_bp.get("/lifecycle/{booking_number}")
async def get_lifecycle(booking_number: str):
    """Return the complete bond lifecycle timeline for a booking number."""
    try:
        # ── Fetch from all collections in parallel ──
        arrests_col = get_collection("arrests")
        notes_col = get_collection("defendant_notes")
        pb_col = get_collection("prospective_bonds")
        ab_col = get_collection("active_bonds")
        audit_col = get_collection("audit_events")
        reminders_col = get_collection("court_reminders")
        messages_col = get_collection("imessage_outreach")
        payments_col = get_collection("payments")

        # Fetch all docs
        arrest = await arrests_col.find_one(
            {"booking_number": booking_number}, {"_id": 0}
        )
        if not arrest:
            # Try leads collection as fallback
            leads_col = get_collection("leads")
            arrest = await leads_col.find_one(
                {"booking_number": booking_number}, {"_id": 0}
            )

        notes = await notes_col.find_one(
            {"booking_number": booking_number}, {"_id": 0}
        )
        pb = await pb_col.find_one(
            {"booking_number": booking_number}, {"_id": 0}
        )
        ab = await ab_col.find_one(
            {"booking_number": booking_number}, {"_id": 0}
        )

        # Audit events
        audit_cursor = audit_col.find(
            {"booking_number": booking_number}, {"_id": 0}
        ).sort("timestamp", 1).limit(200)
        audit_events = await audit_cursor.to_list(200)

        # Court reminders
        reminders_cursor = reminders_col.find(
            {"booking_number": booking_number}, {"_id": 0}
        ).sort("court_date", 1).limit(50)
        reminders = await reminders_cursor.to_list(50)

        # Messages
        msgs_cursor = messages_col.find(
            {"booking_number": booking_number}, {"_id": 0}
        ).sort("sent_at", -1).limit(50)
        messages = await msgs_cursor.to_list(50)

        # Payments
        pay_cursor = payments_col.find(
            {"booking_number": booking_number}, {"_id": 0}
        ).sort("paid_at", -1).limit(20)
        payments = await pay_cursor.to_list(20)

        # ── Build timeline events ──
        events = []

        # 1. Arrest event
        if arrest:
            events.append({
                "type": "arrest",
                "icon_class": "arrest",
                "icon": "🚨",
                "title": f"Arrested — {arrest.get('county', 'Unknown')} County",
                "detail": f"Bond: ${arrest.get('bond_amount', 0):,.0f} · {arrest.get('charges', 'Unknown charges')[:80]}",
                "timestamp": _iso(_ts(arrest, "arrest_date", "created_at", "scraped_at")),
                "badge": None,
            })

        # 2. Notes / contact events
        if notes:
            contact_log = notes.get("contact_log", [])
            for entry in contact_log[-20:]:  # last 20 contacts
                events.append({
                    "type": "contact",
                    "icon_class": "contact",
                    "icon": "📞",
                    "title": f"{entry.get('method', 'Contact').title()} — {entry.get('direction', 'outbound').title()}",
                    "detail": entry.get("summary", entry.get("notes", ""))[:120],
                    "timestamp": _iso(_ts(entry, "ts", "timestamp")),
                    "badge": None,
                })
            # Notes saves
            for note_entry in notes.get("notes", [])[-10:]:
                if isinstance(note_entry, dict):
                    events.append({
                        "type": "note",
                        "icon_class": "note",
                        "icon": "📝",
                        "title": "Note Added",
                        "detail": str(note_entry.get("text", note_entry.get("content", "")))[:120],
                        "timestamp": _iso(_ts(note_entry, "ts", "timestamp", "created_at")),
                        "badge": None,
                    })

        # 3. Pipeline stage changes
        if pb:
            timeline = pb.get("timeline", [])
            for entry in timeline:
                events.append({
                    "type": "contact",
                    "icon_class": "contact",
                    "icon": "🔄",
                    "title": entry.get("event", "Stage Change").replace("_", " ").title(),
                    "detail": entry.get("detail", "")[:120],
                    "timestamp": _iso(_ts(entry, "timestamp")),
                    "badge": None,
                })
            # Outreach messages from pipeline
            for msg in pb.get("communication_log", [])[-10:]:
                events.append({
                    "type": "message",
                    "icon_class": "message",
                    "icon": "💬",
                    "title": f"iMessage — {msg.get('direction', 'outbound').title()}",
                    "detail": str(msg.get("text", msg.get("message", "")))[:120],
                    "timestamp": _iso(_ts(msg, "timestamp")),
                    "badge": None,
                })

        # 4. Bond written
        if ab:
            events.append({
                "type": "bond",
                "icon_class": "bond",
                "icon": "✅",
                "title": f"Bond Written — ${ab.get('bond_amount', 0):,.0f}",
                "detail": f"Surety: {ab.get('surety', 'Unknown')} · Agent: {ab.get('agent', 'Unknown')}",
                "timestamp": _iso(_ts(ab, "bond_date", "written_at", "created_at")),
                "badge": {"text": "BONDED", "class": "green"},
            })
            # Check-ins
            for ci in ab.get("checkins", [])[-10:]:
                events.append({
                    "type": "contact",
                    "icon_class": "contact",
                    "icon": "📍",
                    "title": "Check-In Recorded",
                    "detail": ci.get("notes", f"Location: {ci.get('lat', '')},{ci.get('lng', '')}"),
                    "timestamp": _iso(_ts(ci, "timestamp", "checked_in_at")),
                    "badge": None,
                })

        # 5. Audit events
        for ae in audit_events:
            etype = ae.get("type", ae.get("event_type", "note"))
            icon_map = {
                "bond_exonerated": ("discharge", "🎉"),
                "bond_written": ("bond", "✅"),
                "status_change": ("contact", "🔄"),
                "alert": ("alert", "🚨"),
                "payment": ("payment", "💰"),
                "court_reminder": ("court", "🏛️"),
                "inbound_sms": ("message", "💬"),
                "outbound_sms": ("message", "💬"),
            }
            icon_class, icon = icon_map.get(etype, ("note", "📋"))
            detail_str = ae.get("detail", ae.get("notes", ""))
            if not detail_str and etype in ("inbound_sms", "outbound_sms"):
                payload = ae.get("payload", {})
                if isinstance(payload, dict):
                    detail_str = payload.get("Body", str(payload))
                else:
                    detail_str = str(payload)
            events.append({
                "type": etype,
                "icon_class": icon_class,
                "icon": icon,
                "title": ae.get("description", etype.replace("_", " ").title()),
                "detail": detail_str[:120],
                "timestamp": _iso(_ts(ae, "timestamp", "created_at")),
                "badge": None,
            })

        # 6. Court reminders
        for rem in reminders:
            events.append({
                "type": "court",
                "icon_class": "court",
                "icon": "🏛️",
                "title": f"Court Date — {rem.get('court_date', 'Unknown')}",
                "detail": f"{rem.get('case_number', '')} · {rem.get('court_name', '')} · Status: {rem.get('status', 'scheduled')}",
                "timestamp": _iso(_ts(rem, "created_at", "court_date")),
                "badge": {"text": rem.get("status", "scheduled").upper(), "class": "amber"},
            })

        # 7. iMessage outreach
        for msg in messages:
            events.append({
                "type": "message",
                "icon_class": "message",
                "icon": "💬",
                "title": f"iMessage — {msg.get('direction', 'outbound').title()}",
                "detail": str(msg.get("body", msg.get("message", "")))[:120],
                "timestamp": _iso(_ts(msg, "sent_at", "created_at")),
                "badge": None,
            })

        # 8. Payments
        for pay in payments:
            events.append({
                "type": "payment",
                "icon_class": "payment",
                "icon": "💰",
                "title": f"Payment — ${pay.get('amount', 0):,.2f}",
                "detail": f"Method: {pay.get('method', 'Unknown')} · {pay.get('notes', '')}",
                "timestamp": _iso(_ts(pay, "paid_at", "created_at")),
                "badge": {"text": "PAID", "class": "green"},
            })

        # 9. Discharge / exoneration
        if ab and ab.get("status") in ("exonerated", "discharged", "surrendered"):
            events.append({
                "type": "discharge",
                "icon_class": "discharge",
                "icon": "🎉",
                "title": f"Bond {ab.get('status', 'Discharged').title()} — Tracking Stopped",
                "detail": ab.get("exoneration_notes", ab.get("discharge_reason", "Bond obligations fulfilled")),
                "timestamp": _iso(_ts(ab, "exonerated_at", "discharged_at", "updated_at")),
                "badge": {"text": "DISCHARGED", "class": "green"},
            })

        # ── Sort all events by timestamp ──
        def sort_key(e):
            ts = e.get("timestamp")
            if not ts:
                return datetime.min.replace(tzinfo=timezone.utc)
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                return datetime.min.replace(tzinfo=timezone.utc)

        events.sort(key=sort_key)

        # ── Build meta ──
        current_stage = _determine_current_stage(arrest, notes, pb, ab)

        meta = {
            "booking_number": booking_number,
            "defendant_name": (
                (arrest or {}).get("defendant_name")
                or (ab or {}).get("defendant_name")
                or (pb or {}).get("defendant_name")
                or "Unknown"
            ),
            "bond_amount": (
                (ab or {}).get("bond_amount")
                or (pb or {}).get("bond_amount")
                or (arrest or {}).get("bond_amount")
                or 0
            ),
            "county": (
                (arrest or {}).get("county")
                or (ab or {}).get("county")
                or (pb or {}).get("county")
                or "Unknown"
            ),
            "surety": (ab or {}).get("surety") or (pb or {}).get("surety") or "—",
            "agent": (ab or {}).get("agent") or (pb or {}).get("agent") or "—",
            "current_stage": current_stage,
            "has_active_bond": bool(ab),
            "has_pipeline_entry": bool(pb),
            "has_arrest_record": bool(arrest),
            "court_dates_count": len(reminders),
            "messages_count": len(messages),
            "payments_count": len(payments),
        }

        # Stage progress for the UI
        stage_progress = []
        current_idx = STAGE_ORDER.get(current_stage, 0)
        for i, stage in enumerate(STAGES):
            if i < current_idx:
                status = "done"
            elif i == current_idx:
                status = "current"
            else:
                status = "future"
            stage_progress.append({**stage, "status": status})

        return {
            "ok": True,
            "meta": meta,
            "stages": stage_progress,
            "events": events,
            "event_count": len(events),
        }

    except Exception as e:
        logger.exception(f"Lifecycle timeline error for {booking_number}: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
