"""
ShamrockLeads — PIN Authentication Blueprint
Staff-only PIN gate (stored in .env as DASHBOARD_PIN).
"""
from __future__ import annotations

import os
from functools import wraps
from quart import Blueprint, request, jsonify, session, redirect

pin_auth_bp = Blueprint("pin_auth", __name__)

DASHBOARD_PIN = os.getenv("DASHBOARD_PIN", "")


@pin_auth_bp.route("/login", methods=["GET"])
async def login_page():
    """Serve a simple PIN entry page."""
    html = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shamrock — Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,#0a0f1a 0%,#1a2332 50%,#0d1520 100%);
  font-family:'Inter',system-ui,sans-serif;color:#e0e0e0}
.card{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);
  border-radius:20px;padding:48px 40px;width:380px;backdrop-filter:blur(20px);
  box-shadow:0 20px 60px rgba(0,0,0,0.4)}
.logo{font-size:28px;font-weight:700;text-align:center;margin-bottom:8px;
  background:linear-gradient(135deg,#00d26a,#00b85c);-webkit-background-clip:text;
  -webkit-text-fill-color:transparent}
.sub{text-align:center;color:#8899aa;font-size:14px;margin-bottom:32px}
input{width:100%;padding:14px 16px;border:1px solid rgba(255,255,255,0.12);
  background:rgba(255,255,255,0.06);border-radius:12px;color:#fff;font-size:18px;
  letter-spacing:8px;text-align:center;outline:none;transition:border-color .3s}
input:focus{border-color:#00d26a}
button{width:100%;margin-top:16px;padding:14px;border:none;border-radius:12px;
  background:linear-gradient(135deg,#00d26a,#00b85c);color:#000;font-size:16px;
  font-weight:600;cursor:pointer;transition:transform .2s,box-shadow .2s}
button:hover{transform:translateY(-1px);box-shadow:0 8px 24px rgba(0,210,106,0.3)}
.err{color:#ff6b6b;text-align:center;margin-top:12px;font-size:13px;min-height:20px}
</style></head><body>
<div class="card">
  <div class="logo">☘️ Shamrock</div>
  <div class="sub">Staff Dashboard — Enter PIN</div>
  <form id="f" method="POST" action="/login">
    <input type="password" name="pin" id="pin" maxlength="6" placeholder="••••••" autofocus>
    <button type="submit">Unlock</button>
    <div class="err" id="err"></div>
  </form>
</div>
<script>
document.getElementById('f').addEventListener('submit',async e=>{
  e.preventDefault();
  const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({pin:document.getElementById('pin').value})});
  if(r.ok){window.location='/'}
  else{document.getElementById('err').textContent='Invalid PIN';
    document.getElementById('pin').value=''}
});
</script></body></html>"""
    return html, 200, {"Content-Type": "text/html"}


@pin_auth_bp.route("/login", methods=["POST"])
async def login_submit():
    """Validate PIN and set session cookie."""
    data = await request.get_json(force=True, silent=True) or {}
    pin = data.get("pin", "")

    if not DASHBOARD_PIN:
        # No PIN configured — allow access
        session["authenticated"] = True
        return jsonify({"success": True})

    if pin == DASHBOARD_PIN:
        session["authenticated"] = True
        return jsonify({"success": True})

    return jsonify({"error": "Invalid PIN"}), 401


@pin_auth_bp.before_app_request
async def require_pin():
    """Gate all routes behind PIN auth (except /login and /health)."""
    if not DASHBOARD_PIN:
        return  # No PIN configured, skip auth

    path = request.path
    if path in ("/login", "/health") or path.startswith("/api/webhooks/"):
        return  # Allow webhooks and health without auth

    if not session.get("authenticated"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required"}), 401
        return redirect("/login")
