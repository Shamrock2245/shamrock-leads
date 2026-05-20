from __future__ import annotations
from fastapi import APIRouter, Request
"""Tracking API Blueprint — Location Sync, Map Data, History, Geofence, Exoneration
   Phase 3 Enhancement:
   - map-data now includes full location_history + geo_pings merged and sorted
   - /tracking/<booking>/exonerate — stops tracking, marks bond exonerated, fires SSE
   - /tracking/<booking>/location-history — merged history from all 3 sources
   - /tracking/search — search by defendant name or booking number
   - /tracking/<booking>/send-geo-link — send fresh GPS capture link
   - /tracking/exonerations — recent exoneration log
"""
from datetime import datetime, timezone
from dashboard.extensions import get_collection
from dashboard.services.risk_engine import compute_risk_score
import logging

logger = logging.getLogger(__name__)
tracking_bp = APIRouter(prefix="/api", tags=["tracking"])
async def _merge_location_history(booking_number: str) -> list:
    """
    Merge location data from three sources for a given booking number:
      1. active_bonds.location_history  — geo-link pings pushed by geo.py
      2. geo_pings collection           — raw ping documents
      3. bond_checkins collection       — manual / scheduled check-ins with GPS
    Returns a list of dicts sorted by timestamp descending, deduplicated.
    """
    active_bonds = get_collection("active_bonds")
    geo_pings_col = get_collection("geo_pings")
    checkins_col = get_collection("bond_checkins")
    merged: list[dict] = []
    seen_ts: set = set()

    # Source 1: active_bonds.location_history
    bond = await active_bonds.find_one(
        {"booking_number": booking_number}, {"location_history": 1, "_id": 0}
    )
    if bond:
        for entry in bond.get("location_history", []):
            ts = entry.get("ts") or entry.get("timestamp") or ""
            key = f"{entry.get('lat')},{entry.get('lng')},{ts}"
            if key not in seen_ts:
                seen_ts.add(key)
                merged.append({
                    "lat": entry.get("lat"),
                    "lng": entry.get("lng"),
                    "accuracy": entry.get("accuracy"),
                    "source": entry.get("source", "geo_link"),
                    "timestamp": ts,
                    "county": entry.get("county"),
                })

    # Source 2: geo_pings collection
    async for doc in geo_pings_col.find(
        {"booking_number": booking_number},
        {"pings": 1, "created_at": 1, "_id": 0}
    ).sort("created_at", -1).limit(200):
        for ping in doc.get("pings", []):
            ts = ping.get("ts", "")
            key = f"{ping.get('lat')},{ping.get('lng')},{ts}"
            if key not in seen_ts:
                seen_ts.add(key)
                merged.append({
                    "lat": ping.get("lat"),
                    "lng": ping.get("lng"),
                    "accuracy": ping.get("accuracy"),
                    "source": "geo_link_sms",
                    "timestamp": ts,
                    "county": None,
                    "ip": ping.get("ip"),
                    "ua": ping.get("ua"),
                })

    # Source 3: bond_checkins collection
    async for ci in checkins_col.find(
        {"booking_number": booking_number}, {"_id": 0}
    ).sort("checkin_at", -1).limit(100):
        lat = ci.get("gps_lat")
        lng = ci.get("gps_lon")
        if lat is None or lng is None:
            continue
        ts = ci.get("checkin_at")
        if hasattr(ts, "isoformat"):
            ts = ts.isoformat()
        ts = ts or ""
        key = f"{lat},{lng},{ts}"
        if key not in seen_ts:
            seen_ts.add(key)
            merged.append({
                "lat": lat,
                "lng": lng,
                "accuracy": None,
                "source": ci.get("method", "manual_checkin"),
                "timestamp": ts,
                "county": ci.get("county"),
                "notes": ci.get("notes"),
            })

    merged.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    return merged[:200]


@tracking_bp.get("/tracking/map-data")
async def tracking_map_data():
    """Return all active bonds with latest location + full merged location history."""
    active_bonds = get_collection("active_bonds")
    try:
        cursor = active_bonds.find(
            {"status": {"$in": ["active", "monitoring", "alert"]}},
            {"_id": 0}
        ).sort("bond_date", -1).limit(500)

        defendants = []
        total_active = 0
        overdue = 0
        high_risk = 0
        out_of_area = 0
        now = datetime.now(timezone.utc)

        async for bond in cursor:
            total_active += 1
            for k, v in list(bond.items()):
                if isinstance(v, datetime):
                    bond[k] = v.isoformat()

            loc_history = bond.get("location_history", [])
            latest_loc = bond.get("latest_location") or (loc_history[-1] if loc_history else None)

            next_due_str = bond.get("next_check_in_due") or bond.get("next_checkin_due", "")
            is_overdue = False
            if next_due_str:
                try:
                    from dateutil import parser as dateparser
                    next_due = dateparser.parse(str(next_due_str))
                    if next_due and next_due.tzinfo is None:
                        next_due = next_due.replace(tzinfo=timezone.utc)
                    is_overdue = next_due < now if next_due else False
                except Exception:
                    pass

            risk_result = bond.get("risk_score") or compute_risk_score(bond)
            risk = risk_result.get("score", risk_result) if isinstance(risk_result, dict) else risk_result
            if is_overdue:
                overdue += 1
            if risk >= 75:
                high_risk += 1
            if bond.get("out_of_area_count", 0) > 0:
                out_of_area += 1

            booking_number = bond.get("booking_number", "")
            full_history = await _merge_location_history(booking_number)

            defendants.append({
                "booking_number": booking_number,
                "defendant_name": bond.get("defendant_name"),
                "county": bond.get("county"),
                "bond_amount": bond.get("bond_amount"),
                "premium": bond.get("premium"),
                "case_number": bond.get("case_number"),
                "status": bond.get("status"),
                "risk_score": risk,
                "last_check_in": bond.get("last_check_in") or bond.get("last_checkin"),
                "next_check_in_due": next_due_str,
                "check_in_overdue": is_overdue,
                "latest_location": latest_loc,
                "location_history": full_history,
                "location_count": len(full_history),
                "missed_check_ins": bond.get("missed_check_ins", 0),
                "out_of_area_count": bond.get("out_of_area_count", 0),
                "alerts_count": len(bond.get("alerts", [])),
                "alerts": bond.get("alerts", []),
                "indemnitor_name": bond.get("indemnitor_name"),
                "indemnitor_phone": bond.get("indemnitor_phone"),
                "geofence": bond.get("geofence"),
                "bond_date": bond.get("bond_date") or bond.get("created_at"),
                "check_in_required": bond.get("check_in_required", False),
                "check_in_frequency_days": bond.get("check_in_frequency_days", 30),
            })

        return {
            "defendants": defendants,
            "summary": {
                "total_active": total_active,
                "overdue": overdue,
                "high_risk": high_risk,
                "out_of_area": out_of_area,
            }
        }
    except Exception as e:
        logger.error("[tracking/map-data] %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@tracking_bp.get("/tracking/search")
async def tracking_search(request: Request):
    """Search active bonds by defendant name, booking number, or case number."""
    q = request.args.get("q", "").strip()
    if not q:
        return {"results": []}
    active_bonds = get_collection("active_bonds")
    try:
        pattern = re.compile(re.escape(q), re.IGNORECASE)
        cursor = active_bonds.find(
            {"$or": [
                {"defendant_name": {"$regex": pattern}},
                {"booking_number": {"$regex": pattern}},
                {"case_number": {"$regex": pattern}},
            ]},
            {"_id": 0, "booking_number": 1, "defendant_name": 1, "county": 1,
             "bond_amount": 1, "status": 1, "risk_score": 1, "latest_location": 1,
             "last_check_in": 1, "last_checkin": 1}
        ).limit(20)
        results = []
        async for bond in cursor:
            for k, v in list(bond.items()):
                if isinstance(v, datetime):
                    bond[k] = v.isoformat()
            results.append(bond)
        return {"results": results, "count": len(results)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@tracking_bp.get("/tracking/{booking_number}/location-history")
async def tracking_location_history(booking_number):
    """Return merged location history from all 3 sources."""
    try:
        history = await _merge_location_history(booking_number)
        return {
            "booking_number": booking_number,
            "location_history": history,
            "count": len(history),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@tracking_bp.get("/tracking/{booking_number}/history")
async def tracking_history(booking_number):
    """Full location history + alerts + court dates for a specific defendant."""
    active_bonds = get_collection("active_bonds")
    try:
        bond = await active_bonds.find_one(
            {"booking_number": booking_number}, {"_id": 0}
        )
        if not bond:
            return JSONResponse({"error": "Bond not found"}, status_code=404)
        for k, v in list(bond.items()):
            if isinstance(v, datetime):
                bond[k] = v.isoformat()

        full_history = await _merge_location_history(booking_number)

        court_reminders = get_collection("court_reminders")
        court_dates = []
        async for rem in court_reminders.find(
            {"booking_number": booking_number}, {"_id": 0}
        ).sort("send_at", 1):
            for k, v in list(rem.items()):
                if isinstance(v, datetime):
                    rem[k] = v.isoformat()
            court_dates.append(rem)

        return {
            "booking_number": booking_number,
            "defendant_name": bond.get("defendant_name"),
            "status": bond.get("status"),
            "bond_amount": bond.get("bond_amount"),
            "county": bond.get("county"),
            "case_number": bond.get("case_number"),
            "indemnitor_name": bond.get("indemnitor_name"),
            "indemnitor_phone": bond.get("indemnitor_phone"),
            "risk_score": bond.get("risk_score"),
            "geofence": bond.get("geofence"),
            "check_in_required": bond.get("check_in_required", False),
            "check_in_frequency_days": bond.get("check_in_frequency_days", 30),
            "last_check_in": bond.get("last_check_in") or bond.get("last_checkin"),
            "next_check_in_due": bond.get("next_check_in_due") or bond.get("next_checkin_due"),
            "location_history": full_history,
            "location_count": len(full_history),
            "alerts": bond.get("alerts", []),
            "court_dates": court_dates,
            "exonerated_at": bond.get("exonerated_at"),
            "exoneration_source": bond.get("exoneration_source"),
            "exoneration_case_number": bond.get("exoneration_case_number"),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@tracking_bp.post("/tracking/{booking_number}/geofence")
async def tracking_set_geofence(request: Request, booking_number):
    """Set a geofence radius (miles) around home address."""
    active_bonds = get_collection("active_bonds")
    try:
        data = await request.json()
        radius_miles = float(data.get("radius_miles", 50))
        center_lat = data.get("center_lat")
        center_lng = data.get("center_lng")
        result = await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "geofence": {
                    "radius_miles": radius_miles,
                    "center_lat": center_lat,
                    "center_lng": center_lng,
                    "set_at": datetime.now(timezone.utc).isoformat(),
                },
                "updated_at": datetime.now(timezone.utc),
            }}
        )
        if result.matched_count == 0:
            return JSONResponse({"error": "Bond not found"}, status_code=404)
        return {"success": True, "booking_number": booking_number, "radius_miles": radius_miles}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@tracking_bp.post("/tracking/{booking_number}/exonerate")
async def tracking_exonerate(request: Request, booking_number):
    """
    Exonerate a bond — stops all location tracking, cancels pending geo tokens
    and court reminders, writes audit log, fires SSE bond_exonerated event.

    Called automatically by court_email_scheduler on discharge emails,
    or manually by staff from the dashboard.

    POST body (all optional):
      {
        "case_number": "25-CF-001234",
        "source": "court_email" | "manual",
        "note": "Discharge email received from Lee County Clerk",
        "notify_indemnitor": true
      }
    """
    active_bonds = get_collection("active_bonds")
    audit_col = get_collection("audit_events")
    now = datetime.now(timezone.utc)

    try:
        data = await request.json() or {}
        source = data.get("source", "manual")
        note = data.get("note", "")
        case_number = data.get("case_number", "")
        notify = data.get("notify_indemnitor", False)

        bond = await active_bonds.find_one({"booking_number": booking_number}, {"_id": 0})
        if not bond:
            return JSONResponse({"success": False, "error": "Bond not found"}, status_code=404)

        defendant_name = bond.get("defendant_name", "")
        current_status = bond.get("status", "")

        if current_status == "exonerated":
            return {
                "success": True,
                "already_exonerated": True,
                "exonerated_at": bond.get("exonerated_at"),
                "message": f"{defendant_name} was already exonerated."
            }

        await active_bonds.update_one(
            {"booking_number": booking_number},
            {"$set": {
                "status": "exonerated",
                "tracking_active": False,
                "check_in_required": False,
                "exonerated_at": now.isoformat(),
                "exoneration_source": source,
                "exoneration_case_number": case_number or bond.get("case_number", ""),
                "exoneration_note": note,
                "updated_at": now,
            }}
        )

        # Cancel pending geo_pings tokens
        geo_pings = get_collection("geo_pings")
        await geo_pings.update_many(
            {"booking_number": booking_number, "status": {"$in": ["pending", "captured"]}},
            {"$set": {"status": "cancelled_exonerated", "cancelled_at": now.isoformat()}}
        )

        # Cancel pending court reminders
        court_reminders = get_collection("court_reminders")
        await court_reminders.update_many(
            {"booking_number": booking_number, "status": {"$in": ["scheduled", "pending"]}},
            {"$set": {"status": "cancelled_exonerated", "cancelled_at": now.isoformat()}}
        )

        # Audit log
        await audit_col.insert_one({
            "event_type": "bond_exonerated",
            "entity_id": booking_number,
            "entity_type": "bond_case",
            "defendant_name": defendant_name,
            "case_number": case_number or bond.get("case_number", ""),
            "source": source,
            "note": note,
            "exonerated_at": now,
            "timestamp": now,
        })

        # Notify indemnitor via BlueBubbles if requested
        notify_result = None
        if notify and bond.get("indemnitor_phone"):
            try:
                from dashboard.services.bb_client import send_message_universal
                first_name = (bond.get("indemnitor_name") or "").split()[0] or "there"
                msg = (
                    f"Hi {first_name}! Great news — {defendant_name}'s bond obligation "
                    f"with Shamrock Bail Bonds has been officially discharged. "
                    f"No further check-ins are required. Thank you for your cooperation! "
                    f"☘️ Shamrock Bail Bonds (239) 332-2245"
                )
                notify_result = await send_message_universal(bond["indemnitor_phone"], msg)
            except Exception as notify_err:
                logger.warning("[exonerate] Indemnitor notification failed: %s", notify_err)
                notify_result = {"success": False, "error": str(notify_err)}

        # Fire SSE event
        try:
            from dashboard.routers.events import publish_event
            sse_payload = {
                "booking_number": booking_number,
                "defendant_name": defendant_name,
                "county": bond.get("county", ""),
                "source": source,
                "exonerated_at": now.isoformat(),
            }
            await publish_event("bond_exonerated", sse_payload)
        except Exception as sse_err:
            logger.debug("[exonerate] SSE fire failed: %s", sse_err)

        logger.info(
            "[exonerate] Bond %s exonerated — %s (source: %s)",
            booking_number, defendant_name, source
        )

        return {
            "success": True,
            "booking_number": booking_number,
            "defendant_name": defendant_name,
            "exonerated_at": now.isoformat(),
            "source": source,
            "tracking_stopped": True,
            "reminders_cancelled": True,
            "notify_result": notify_result,
        }
    except Exception as e:
        logger.error("[exonerate] Error for %s: %s", booking_number, e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@tracking_bp.get("/tracking/exonerations")
async def tracking_exonerations(request: Request):
    """Return recent bond exonerations for the dashboard exoneration log panel."""
    audit_col = get_collection("audit_events")
    try:
        limit = int(request.args.get("limit", 50))
        cursor = audit_col.find(
            {"event_type": "bond_exonerated"},
            {"_id": 0}
        ).sort("timestamp", -1).limit(min(limit, 200))
        records = []
        async for doc in cursor:
            for k, v in list(doc.items()):
                if isinstance(v, datetime):
                    doc[k] = v.isoformat()
            records.append(doc)
        return {"exonerations": records, "count": len(records)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@tracking_bp.post("/tracking/{booking_number}/send-geo-link")
async def tracking_send_geo_link(request: Request, booking_number):
    """Send a fresh GPS capture link to defendant or indemnitor via iMessage/SMS."""
    active_bonds = get_collection("active_bonds")
    try:
        data = await request.json() or {}
        bond = await active_bonds.find_one({"booking_number": booking_number}, {"_id": 0})
        if not bond:
            return JSONResponse({"success": False, "error": "Bond not found"}, status_code=404)

        phone = data.get("phone") or bond.get("indemnitor_phone", "")
        recipient = data.get("recipient", "indemnitor")
        if not phone:
            return JSONResponse({"success": False, "error": "No phone number available"}, status_code=400)

        defendant_name = bond.get("defendant_name", "defendant")
        geo_pings = get_collection("geo_pings")
        token = secrets.token_urlsafe(12)
        public_url = os.getenv("DASHBOARD_PUBLIC_URL", "https://shamrockbailbonds.biz")
        geo_url = f"{public_url}/g/{token}"

        await geo_pings.insert_one({
            "token": token,
            "booking_number": booking_number,
            "phone": phone,
            "recipient": recipient,
            "status": "pending",
            "ping_count": 0,
            "pings": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        from dashboard.services.bb_client import send_message_universal
        first = (bond.get("indemnitor_name") or defendant_name).split()[0]
        msg = (
            f"Hi {first}! Shamrock Bail Bonds needs to verify {defendant_name}'s location. "
            f"Please tap the link below — it only takes a second:\n{geo_url}\n\n"
            f"☘️ Shamrock Bail Bonds (239) 332-2245"
        )
        result = await send_message_universal(phone, msg)
        return {
            "success": result.get("success", False),
            "token": token,
            "geo_url": geo_url,
            "phone": phone,
            "channel": result.get("channel"),
        }
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
