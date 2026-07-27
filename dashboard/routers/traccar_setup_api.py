"""
ShamrockLeads — Traccar Device 1-Click Auto-Provisioning & Setup Router
════════════════════════════════════════════════════════════════════════
Serves the mobile-friendly defendant setup landing page (/traccar/setup/{device_id}),
deep links, QR codes, and live connection status API.
"""
from __future__ import annotations

import logging
import os
import urllib.parse
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from dashboard.extensions import get_collection

logger = logging.getLogger(__name__)

traccar_setup_router = APIRouter(tags=["traccar_setup"])


@traccar_setup_router.get("/api/traccar/device-status/{device_id}")
async def api_traccar_device_status(device_id: str):
    """
    Check if a device has connected and transmitted position data to Traccar.
    """
    try:
        geo_devices = get_collection("geo_devices")
        device = await geo_devices.find_one({"$or": [{"unique_id": device_id}, {"booking_number": device_id}]})
        
        last_seen = None
        lat = None
        lng = None
        online = False
        
        if device:
            last_seen = device.get("last_seen") or device.get("updated_at")
            last_pos = device.get("last_position") or {}
            lat = last_pos.get("lat")
            lng = last_pos.get("lng")
            if last_seen:
                online = True

        return {
            "success": True,
            "device_id": device_id,
            "connected": online,
            "last_seen": last_seen,
            "lat": lat,
            "lng": lng,
        }
    except Exception as e:
        logger.error("Failed to check device status: %s", e)
        return {"success": False, "error": str(e), "connected": False}


@traccar_setup_router.get("/traccar/setup/{device_id}", response_class=HTMLResponse)
async def traccar_setup_page(request: Request, device_id: str):
    """
    Serve 1-click mobile auto-configuration page for Traccar Client app.
    """
    public_host = os.getenv("TRACCAR_PUBLIC_HOST", "leads.shamrockbailbonds.biz")
    server_url = f"http://{public_host}:5055"
    
    # Traccar Client deep link parameters
    params = {
        "url": server_url,
        "id": device_id,
        "frequency": "60",
        "distance": "100",
        "angle": "30",
    }
    deeplink = f"org.traccar.client://?{urllib.parse.urlencode(params)}"
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Shamrock GPS Setup</title>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #0b0f19;
      --card-bg: #151c2c;
      --accent: #00e676;
      --accent-hover: #00c853;
      --text: #f0f4f8;
      --subtext: #94a3b8;
      --border: #2a364f;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', sans-serif; }}
    body {{ background: var(--bg); color: var(--text); padding: 20px 16px; min-height: 100vh; display: flex; justify-content: center; align-items: flex-start; }}
    .container {{ width: 100%; max-width: 480px; margin: 0 auto; }}
    .header {{ text-align: center; margin-bottom: 24px; margin-top: 12px; }}
    .header h1 {{ font-size: 26px; font-weight: 700; color: #fff; margin-bottom: 6px; }}
    .header p {{ color: var(--subtext); font-size: 14px; }}
    .status-badge {{ display: inline-flex; align-items: center; gap: 8px; padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 600; background: #1e293b; color: var(--subtext); margin-top: 12px; border: 1px solid var(--border); }}
    .status-badge.online {{ background: rgba(0, 230, 118, 0.15); color: var(--accent); border-color: var(--accent); }}
    .pulse-dot {{ width: 8px; height: 8px; border-radius: 50%; background: #64748b; }}
    .status-badge.online .pulse-dot {{ background: var(--accent); box-shadow: 0 0 10px var(--accent); animation: pulse 1.5s infinite; }}
    @keyframes pulse {{ 0% {{ opacity: 0.4; }} 50% {{ opacity: 1; }} 100% {{ opacity: 0.4; }} }}
    
    .card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 16px; padding: 24px; margin-bottom: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.3); }}
    .step-title {{ font-size: 16px; font-weight: 600; color: #fff; margin-bottom: 12px; display: flex; align-items: center; gap: 10px; }}
    .step-num {{ background: var(--accent); color: #000; width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 700; flex-shrink: 0; }}
    
    .btn {{ display: flex; align-items: center; justify-content: center; gap: 10px; width: 100%; padding: 16px; border-radius: 12px; font-weight: 700; font-size: 16px; text-decoration: none; border: none; cursor: pointer; transition: all 0.2s ease; }}
    .btn-primary {{ background: var(--accent); color: #051a0e; box-shadow: 0 4px 16px rgba(0,230,118,0.3); }}
    .btn-primary:active {{ transform: scale(0.98); }}
    .btn-secondary {{ background: #1e293b; color: #fff; border: 1px solid var(--border); margin-top: 10px; }}
    
    .field-group {{ margin-top: 14px; }}
    .field-label {{ font-size: 12px; color: var(--subtext); margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }}
    .field-box {{ display: flex; align-items: center; justify-content: space-between; background: #0f172a; border: 1px solid var(--border); padding: 12px 14px; border-radius: 8px; font-family: monospace; font-size: 14px; color: #e2e8f0; }}
    .copy-btn {{ background: none; border: none; color: var(--accent); font-size: 13px; font-weight: 600; cursor: pointer; padding: 4px 8px; }}
    .instructions {{ font-size: 13px; color: var(--subtext); line-height: 1.5; margin-top: 8px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>☘️ Shamrock GPS Setup</h1>
      <p>Bail Bond Condition GPS Monitoring</p>
      <div id="statusBadge" class="status-badge">
        <div class="pulse-dot"></div>
        <span id="statusText">Waiting for connection...</span>
      </div>
    </div>

    <!-- Step 1: Download App -->
    <div class="card">
      <div class="step-title">
        <div class="step-num">1</div>
        <span>Download Traccar Client</span>
      </div>
      <p class="instructions">If you haven't already, download the free Traccar Client app onto your phone:</p>
      <div style="display: flex; gap: 10px; margin-top: 14px;">
        <a href="https://apps.apple.com/app/traccar-client/id1437105051" target="_blank" class="btn btn-secondary" style="flex: 1; font-size: 14px; padding: 12px;">🍏 iPhone (iOS)</a>
        <a href="https://play.google.com/store/apps/details?id=org.traccar.client" target="_blank" class="btn btn-secondary" style="flex: 1; font-size: 14px; padding: 12px;">🤖 Android</a>
      </div>
    </div>

    <!-- Step 2: Auto-Configure -->
    <div class="card">
      <div class="step-title">
        <div class="step-num">2</div>
        <span>Auto-Configure Credentials</span>
      </div>
      <p class="instructions">Tap below to automatically install your server credentials into the Traccar app:</p>
      <a href="{deeplink}" class="btn btn-primary" style="margin-top: 14px;">⚡ 1-Click Auto-Configure App</a>
    </div>

    <!-- Step 3: Turn Service ON -->
    <div class="card">
      <div class="step-title">
        <div class="step-num">3</div>
        <span>Turn Service ON inside App</span>
      </div>
      <p class="instructions">Open Traccar Client, switch <strong>Service Status</strong> to <strong>ON</strong>, and allow location permissions "Always".</p>
      
      <div style="margin-top: 18px; border-top: 1px dashed var(--border); padding-top: 16px;">
        <p style="font-size: 12px; color: var(--subtext); font-weight: 600; margin-bottom: 8px;">MANUAL CREDENTIALS (IF NEEDED):</p>
        
        <div class="field-group">
          <div class="field-label">Server URL</div>
          <div class="field-box">
            <span id="srvUrl">{server_url}</span>
            <button class="copy-btn" onclick="copyTxt('{server_url}')">Copy</button>
          </div>
        </div>
        
        <div class="field-group">
          <div class="field-label">Device Identifier</div>
          <div class="field-box">
            <span id="devId">{device_id}</span>
            <button class="copy-btn" onclick="copyTxt('{device_id}')">Copy</button>
          </div>
        </div>

        <div class="field-group">
          <div class="field-label">Location Frequency</div>
          <div class="field-box">
            <span>60 seconds</span>
            <button class="copy-btn" onclick="copyTxt('60')">Copy</button>
          </div>
        </div>
      </div>
    </div>
  </div>

  <script>
    function copyTxt(txt) {{
      navigator.clipboard.writeText(txt).then(() => {{
        alert('Copied: ' + txt);
      }}).catch(() => {{
        prompt('Copy manually:', txt);
      }});
    }}

    async function checkStatus() {{
      try {{
        const r = await fetch('/api/traccar/device-status/{device_id}');
        const data = await r.json();
        const badge = document.getElementById('statusBadge');
        const text = document.getElementById('statusText');
        
        if (data.connected) {{
          badge.classList.add('online');
          text.textContent = '✅ Connected & Active';
        }} else {{
          badge.classList.remove('online');
          text.textContent = 'Waiting for connection...';
        }}
      }} catch (e) {{
        console.warn('Status poll error:', e);
      }}
    }}

    checkStatus();
    setInterval(checkStatus, 4000);
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)
