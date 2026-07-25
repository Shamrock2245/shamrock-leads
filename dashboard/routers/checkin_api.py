"""
Check-in API Blueprint — ShamrockLeads
Serves mobile HTML check-in page (/checkin/{token}) and handles selfie + GPS submissions.
"""

from fastapi import APIRouter, Request, Path, Query
from starlette.responses import HTMLResponse, Response
from fastapi.responses import JSONResponse
from typing import Optional

from dashboard.services.checkin_web_service import (
    create_checkin_request,
    get_checkin_request,
    process_mobile_checkin
)

checkin_bp = APIRouter(tags=["checkin"])

@checkin_bp.get("/checkin/{token}", response_class=HTMLResponse)
async def serve_mobile_checkin_page(token: str = Path(...)):
    """Render mobile web check-in interface requesting selfie camera + HTML5 GPS."""
    req = await get_checkin_request(token)
    if not req:
        return HTMLResponse(content="<h2>❌ Check-in link invalid or expired. Please contact Shamrock Bail Bonds at (239) 955-0178.</h2>", status_code=404)

    if req.get("status") == "completed":
        return HTMLResponse(content="<h2 style='color:green; font-family:sans-serif; text-align:center; padding-top:40px;'>✅ You have already completed your check-in today. Thank you!</h2>", status_code=200)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Shamrock Bail Bonds — Client Check-In</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; }}
        .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 24px; max-width: 400px; width: 100%; box-shadow: 0 10px 25px rgba(0,0,0,0.5); text-align: center; }}
        h1 {{ color: #22c55e; font-size: 22px; margin-top: 0; }}
        p {{ color: #94a3b8; font-size: 14px; margin-bottom: 20px; }}
        .video-box {{ width: 100%; height: 240px; background: #000; border-radius: 12px; overflow: hidden; position: relative; margin-bottom: 15px; border: 2px solid #334155; }}
        video, canvas {{ width: 100%; height: 100%; object-fit: cover; }}
        .btn {{ background: #22c55e; color: #000; font-weight: bold; padding: 14px 20px; border: none; border-radius: 10px; font-size: 16px; width: 100%; cursor: pointer; margin-top: 10px; transition: 0.2s; }}
        .btn:disabled {{ background: #475569; color: #94a3b8; cursor: not-allowed; }}
        .status {{ margin-top: 15px; font-size: 13px; color: #f59e0b; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>☘️ Shamrock Client Check-In</h1>
        <p>Booking #: <strong>{req.get("booking_number")}</strong></p>
        <p>Please take a selfie photo and allow location access to complete your mandatory check-in.</p>
        
        <div class="video-box">
            <video id="video" autoplay playsinline></video>
            <canvas id="canvas" style="display:none;"></canvas>
        </div>

        <div id="status" class="status">Click camera & location permissions to start...</div>
        <button id="snap-btn" class="btn" onclick="captureAndSubmit()">📸 Take Photo & Submit Check-In</button>
    </div>

    <script>
        const video = document.getElementById('video');
        const canvas = document.getElementById('canvas');
        const status = document.getElementById('status');
        const btn = document.getElementById('snap-btn');
        let userLat = null, userLng = null, userAcc = null;

        // Request Camera Access
        navigator.mediaDevices.getUserMedia({{ video: {{ facingMode: "user" }} }})
            .then(stream => {{ video.srcObject = stream; }})
            .catch(err => {{ status.textContent = '⚠️ Camera permission required to check in.'; }});

        // Request GPS Location
        if (navigator.geolocation) {{
            navigator.geolocation.getCurrentPosition(
                pos => {{
                    userLat = pos.coords.latitude;
                    userLng = pos.coords.longitude;
                    userAcc = pos.coords.accuracy;
                    status.textContent = '📍 GPS Location verified! Ready to capture photo.';
                    status.style.color = '#22c55e';
                }},
                err => {{
                    status.textContent = '⚠️ Location permission required for check-in verification.';
                    status.style.color = '#ef4444';
                }},
                {{ enableHighAccuracy: true }}
            );
        }}

        async function captureAndSubmit() {{
            btn.disabled = true;
            btn.textContent = 'Submitting Check-In...';

            const context = canvas.getContext('2d');
            canvas.width = video.videoWidth || 640;
            canvas.height = video.videoHeight || 480;
            context.drawImage(video, 0, 0, canvas.width, canvas.height);
            const selfieB64 = canvas.toDataURL('image/jpeg', 0.8);

            try {{
                const res = await fetch('/api/checkin/submit', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        token: '{token}',
                        selfie_b64: selfieB64,
                        lat: userLat,
                        lng: userLng,
                        accuracy: userAcc
                    }})
                }});
                const data = await res.json();
                if (data.success) {{
                    document.body.innerHTML = '<div class="card"><h1 style="color:#22c55e;">✅ Check-In Completed Successfully!</h1><p style="color:#cbd5e1;">Thank you for checking in with Shamrock Bail Bonds. Have a safe day!</p></div>';
                }} else {{
                    status.textContent = '❌ Error: ' + (data.error || 'Submission failed');
                    btn.disabled = false;
                }}
            }} catch (err) {{
                status.textContent = 'Network error: ' + err.message;
                btn.disabled = false;
            }}
        }}
    </script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)

@checkin_bp.post("/api/checkin/create-request")
async def api_create_checkin_request(request: Request):
    """Generate a mobile check-in token for a defendant."""
    try:
        data = await request.json() or {}
        booking_number = data.get("booking_number")
        phone = data.get("defendant_phone")
        if not booking_number:
            return JSONResponse(status_code=400, content={"success": False, "error": "Missing booking_number"})
        req = await create_checkin_request(booking_number=booking_number, defendant_phone=phone)
        return JSONResponse(status_code=200, content={"success": True, "checkin_request": req})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})

@checkin_bp.post("/api/checkin/submit")
async def api_submit_checkin(request: Request):
    """Handle defendant selfie photo + GPS location check-in submission."""
    try:
        data = await request.json() or {}
        token = data.get("token")
        selfie = data.get("selfie_b64", "")
        lat = data.get("lat")
        lng = data.get("lng")
        acc = data.get("accuracy")
        ua = request.headers.get("user-agent", "")

        if not token:
            return JSONResponse(status_code=400, content={"success": False, "error": "Missing check-in token"})

        res = await process_mobile_checkin(
            token=token,
            selfie_b64=selfie,
            lat=lat,
            lng=lng,
            accuracy=acc,
            user_agent=ua
        )
        return JSONResponse(status_code=200, content=res)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})
