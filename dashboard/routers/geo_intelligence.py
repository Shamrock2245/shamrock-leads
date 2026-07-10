from __future__ import annotations

"""
ShamrockLeads — Geo Intelligence API Blueprint
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REST endpoints for the GPS tracking intelligence layer.
Exposes device management, geofencing, position sync, vehicle watch,
compliance metrics, and Traccar webhook ingestion.

All endpoints prefixed with /api/geo-intel/
Traccar webhook at /api/traccar/webhook (no auth — Docker-internal only)
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Frontend + docs expect /api/geo-intel/* ; webhook at /api/traccar/webhook
geo_intel_bp = APIRouter(prefix="/api/geo-intel", tags=["geo_intelligence"])
traccar_webhook_bp = APIRouter(prefix="/api/traccar", tags=["traccar_webhook"])


def _get_service():
    from dashboard.services.geo_intelligence import GeoIntelligenceService
    return GeoIntelligenceService()


def _get_traccar():
    from dashboard.services.traccar_client import get_traccar_client
    return get_traccar_client()


# ═══════════════════════════════════════════════════════════════════════════════
# TRACCAR SERVER HEALTH
# ═══════════════════════════════════════════════════════════════════════════════

@geo_intel_bp.get("/health")
async def traccar_health():
    """Check Traccar server connectivity."""
    client = _get_traccar()
    status = await client.health_check()
    return status


# ═══════════════════════════════════════════════════════════════════════════════
# DEVICE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@geo_intel_bp.get("/devices")
async def list_devices(booking_number: str | None = Query(default=None)):
    """List all registered tracking devices, optionally by booking number."""
    booking = booking_number
    svc = _get_service()
    devices = await svc.list_devices(booking)
    return {"devices": devices, "count": len(devices)}


@geo_intel_bp.post("/devices")
async def register_device(request: Request):
    """Register a new tracking device and bind to a defendant.

    Body: {booking_number, county, device_type, unique_id, label?, phone?, vehicle_info?}

    Creates the device in Traccar AND binds it in MongoDB.
    """
    data = await request.json()
    booking = data.get("booking_number", "")
    county = data.get("county", "")
    device_type = data.get("device_type", "phone_app")
    unique_id = data.get("unique_id", "")
    label = data.get("label", "")
    phone = data.get("phone", "")
    vehicle_info = data.get("vehicle_info")

    if not booking or not unique_id:
        return JSONResponse({"error": "booking_number and unique_id are required"}, status_code=400)

    # Category mapping for Traccar
    category_map = {
        "phone_app": "person",
        "vehicle_tracker": "car",
        "personal_tracker": "person",
        "ankle_monitor": "person",
    }
    category = category_map.get(device_type, "person")

    # Create in Traccar first
    traccar = _get_traccar()
    try:
        tc_device = await traccar.create_device(
            name=f"{label or booking} — {county}",
            unique_id=unique_id,
            category=category,
            phone=phone,
            attributes={
                "booking_number": booking,
                "county": county,
                "shamrock_device_type": device_type,
            },
        )
        traccar_id = tc_device.get("id")
    except Exception as e:
        logger.error("Traccar device creation failed: %s", e)
        return JSONResponse({"error": f"Traccar error: {str(e)}"}, status_code=502)

    # Bind in MongoDB
    svc = _get_service()
    device = await svc.register_device(
        booking_number=booking,
        county=county,
        device_type=device_type,
        traccar_device_id=traccar_id,
        unique_id=unique_id,
        label=label,
        phone=phone,
        vehicle_info=vehicle_info,
    )

    return JSONResponse(status_code=201, content={"device": device, "traccar_id": traccar_id})


@geo_intel_bp.post("/devices/{device_id}/deactivate")
async def deactivate_device(request: Request, device_id: str):
    """Deactivate a tracking device."""
    data = await request.json() or {}
    reason = data.get("reason", "")
    svc = _get_service()
    ok = await svc.deactivate_device(device_id, reason)
    if not ok:
        return JSONResponse({"error": "Device not found"}, status_code=404)
    return {"success": True, "device_id": device_id}


# ═══════════════════════════════════════════════════════════════════════════════
# POSITIONS & TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

@geo_intel_bp.get("/positions/latest")
async def latest_positions():
    """Get the latest position for all active devices (from Traccar)."""
    traccar = _get_traccar()
    try:
        positions = await traccar.get_latest_positions()
        return {"positions": positions, "count": len(positions)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@geo_intel_bp.get("/positions/route")
async def device_route(traccar_device_id: int = Query(default=0), from_: str = Query(default=""), to: str = Query(default="")):
    """Get full position trail for a device.
    Query: ?traccar_device_id=&from=&to= (ISO 8601)
    """
    device_id = traccar_device_id
    from_dt = from_
    to_dt = to
    if not device_id or not from_dt or not to_dt:
        return JSONResponse({"error": "traccar_device_id, from, and to are required"}, status_code=400)

    traccar = _get_traccar()
    try:
        route = await traccar.get_route(device_id, from_dt, to_dt)
        return {"route": route, "count": len(route)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


# ═══════════════════════════════════════════════════════════════════════════════
# GEOFENCE ZONES
# ═══════════════════════════════════════════════════════════════════════════════

@geo_intel_bp.get("/zones")

async def list_zones(booking_number: str | None = Query(default=None)):
    """List geofence zones, optionally by booking number."""
    booking = booking_number
    svc = _get_service()
    zones = await svc.list_zones(booking)
    return {"zones": zones, "count": len(zones)}


@geo_intel_bp.post("/zones")
async def create_zone(request: Request):
    """Create an inclusion or exclusion geofence zone.

    Body: {booking_number, zone_type, name, center_lat, center_lng, radius_miles,
           address?, notes?, schedule?}

    Also creates in Traccar and links to all defendant devices.
    """
    data = await request.json()
    required = ["booking_number", "zone_type", "name", "center_lat", "center_lng", "radius_miles"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return JSONResponse({"error": f"Missing fields: {missing}"}, status_code=400)

    if data["zone_type"] not in ("inclusion", "exclusion"):
        return JSONResponse({"error": "zone_type must be 'inclusion' or 'exclusion'"}, status_code=400)

    # Create in Traccar
    traccar = _get_traccar()
    radius_m = float(data["radius_miles"]) * 1609.34
    traccar_fence_id = None
    try:
        area_wkt = f"CIRCLE ({data['center_lat']} {data['center_lng']}, {radius_m:.0f})"
        tc_fence = await traccar.create_geofence(
            name=data["name"],
            area=area_wkt,
            attributes={"zone_type": data["zone_type"], "booking_number": data["booking_number"]},
        )
        traccar_fence_id = tc_fence.get("id")

        # Link to all defendant devices
        svc = _get_service()
        devices = await svc.list_devices(data["booking_number"])
        for dev in devices:
            tc_id = dev.get("traccar_device_id")
            if tc_id and traccar_fence_id:
                try:
                    await traccar.link_device_geofence(tc_id, traccar_fence_id)
                except Exception:
                    pass
    except Exception as e:
        logger.warning("Traccar geofence creation failed (proceeding with MongoDB only): %s", e)

    # Create in MongoDB
    svc = _get_service()
    zone = await svc.create_zone(
        booking_number=data["booking_number"],
        zone_type=data["zone_type"],
        name=data["name"],
        center_lat=float(data["center_lat"]),
        center_lng=float(data["center_lng"]),
        radius_miles=float(data["radius_miles"]),
        traccar_geofence_id=traccar_fence_id,
        address=data.get("address", ""),
        notes=data.get("notes", ""),
        schedule=data.get("schedule"),
    )

    return JSONResponse(status_code=201, content={"zone": zone, "traccar_geofence_id": traccar_fence_id})


@geo_intel_bp.delete("/zones/{zone_id}")
async def delete_zone(zone_id: str):
    """Delete a geofence zone."""
    svc = _get_service()
    ok = await svc.delete_zone(zone_id)
    if not ok:
        return JSONResponse({"error": "Zone not found"}, status_code=404)
    return {"success": True, "zone_id": zone_id}


# ═══════════════════════════════════════════════════════════════════════════════
# VEHICLE WATCH
# ═══════════════════════════════════════════════════════════════════════════════

@geo_intel_bp.get("/vehicle-watch")
async def list_vehicle_watches():
    """List all active vehicle watches."""
    svc = _get_service()
    watches = await svc.list_vehicle_watches()
    return {"vehicles": watches, "count": len(watches)}


@geo_intel_bp.post("/vehicle-watch")
async def add_vehicle_watch(request: Request):
    """Add a vehicle to the watch list.

    Body: {booking_number, vehicle_info: {make, model, year, color, plate, vin}, reason?}
    """
    data = await request.json()
    if not data.get("booking_number") or not data.get("vehicle_info"):
        return JSONResponse({"error": "booking_number and vehicle_info required"}, status_code=400)

    svc = _get_service()
    watch = await svc.add_vehicle_watch(
        booking_number=data["booking_number"],
        vehicle_info=data["vehicle_info"],
        reason=data.get("reason", ""),
    )
    return JSONResponse(status_code=201, content={"vehicle_watch": watch})


@geo_intel_bp.post("/vehicle-watch/{watch_id}/sighting")
async def record_vehicle_sighting(request: Request, watch_id: str):
    """Record a vehicle sighting.

    Body: {lat, lng, address?}
    """
    data = await request.json()
    svc = _get_service()
    ok = await svc.update_vehicle_sighting(
        watch_id,
        float(data.get("lat", 0)),
        float(data.get("lng", 0)),
        data.get("address", ""),
    )
    if not ok:
        return JSONResponse({"error": "Vehicle watch not found"}, status_code=404)
    return {"success": True}


# ═══════════════════════════════════════════════════════════════════════════════
# VIOLATIONS & EVENTS
# ═══════════════════════════════════════════════════════════════════════════════

@geo_intel_bp.get("/violations")
async def violation_feed(limit: int = Query(default=50), booking_number: str | None = Query(default=None)):
    """Get recent geofence violations."""
    limit = limit
    booking = booking_number
    svc = _get_service()
    events = await svc.get_violation_feed(limit, booking)
    return {"violations": events, "count": len(events)}


@geo_intel_bp.post("/violations/{event_id}/acknowledge")
async def acknowledge_violation(request: Request, event_id: str):
    """Acknowledge a geofence violation alert."""
    data = await request.json() or {}
    svc = _get_service()
    ok = await svc.acknowledge_violation(event_id, data.get("agent", ""))
    if not ok:
        return JSONResponse({"error": "Violation not found"}, status_code=404)
    return {"success": True}


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════════

@geo_intel_bp.get("/overview")
async def tracking_overview():
    """Aggregate tracking metrics for the dashboard cards."""
    svc = _get_service()
    overview = await svc.get_tracking_overview()
    return overview


# ═══════════════════════════════════════════════════════════════════════════════
# PHOTO-VERIFIED CHECK-INS
# ═══════════════════════════════════════════════════════════════════════════════

@geo_intel_bp.post("/photo-checkin")
async def photo_checkin(request: Request):
    """Record a GPS + photo verified check-in.

    Body: {booking_number, lat, lng, photo_url, accuracy?, source?}
    """
    data = await request.json()
    if not data.get("booking_number") or not data.get("photo_url"):
        return JSONResponse({"error": "booking_number and photo_url required"}, status_code=400)

    svc = _get_service()
    checkin = await svc.record_photo_checkin(
        booking_number=data["booking_number"],
        lat=float(data.get("lat", 0)),
        lng=float(data.get("lng", 0)),
        photo_url=data["photo_url"],
        accuracy=float(data.get("accuracy", 0)),
        source=data.get("source", "manual"),
    )
    return JSONResponse(status_code=201, content={"checkin": checkin})


# ═══════════════════════════════════════════════════════════════════════════════
# TRACCAR WEBHOOK — Ingests real-time position/event data from Traccar
# ═══════════════════════════════════════════════════════════════════════════════

@traccar_webhook_bp.post("/webhook")
async def traccar_webhook(request: Request):
    """Receive forwarded position/event data from Traccar.

    Path: POST /api/traccar/webhook (matches config/traccar/traccar.xml forward.url)
    Traccar sends JSON with device + position on every update.
    Format: {device: {...}, position: {latitude, longitude, ...}}
    """
    data = await request.json()
    if not data:
        return JSONResponse({"error": "No data"}, status_code=400)

    device = data.get("device", {})
    position = data.get("position", {})

    if not position:
        # Event-only webhook (no position data)
        logger.debug("Traccar event (no position): %s", data.get("event", {}).get("type"))
        return {"ok": True, "type": "event_only"}

    traccar_device_id = device.get("id")
    if not traccar_device_id:
        return JSONResponse({"error": "Missing device.id"}, status_code=400)

    svc = _get_service()
    result = await svc.sync_position(
        traccar_device_id=traccar_device_id,
        lat=position.get("latitude", 0),
        lng=position.get("longitude", 0),
        accuracy=position.get("accuracy", 0),
        speed=position.get("speed", 0),
        course=position.get("course", 0),
        altitude=position.get("altitude", 0),
        address=position.get("address", ""),
        timestamp=position.get("fixTime", ""),
        attributes=position.get("attributes", {}),
    )

    if result:
        return {"ok": True, "synced": True}
    else:
        return {"ok": True, "synced": False, "reason": "No matching device binding"}