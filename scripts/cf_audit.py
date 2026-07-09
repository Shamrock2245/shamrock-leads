#!/usr/bin/env python3
"""
Cloudflare Audit v2 — handles partial token permissions gracefully.
Tests DNS propagation, tunnel health, and available CF settings.
"""
import urllib.request
import json
import subprocess
import sys
import os

API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")
if not API_TOKEN:
    print("ERROR: Set CLOUDFLARE_API_TOKEN env var before running.")
    sys.exit(1)
ZONE_ID = "5baadd20dd3c4502abaf78019bdde524"
ACCOUNT_ID = "e3ceb175a0ebe60c6e02fe2c38e17691"

def cf_get(url):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        return data
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"success": False, "http_code": e.code, "error": body[:300]}

def cf_patch(url, data):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }, method="PATCH")
    req.data = json.dumps(data).encode()
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"success": False, "http_code": e.code, "error": body[:300]}

ZBASE = f"https://api.cloudflare.com/client/v4/zones/{ZONE_ID}"
ABASE = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}"

# ═══════════════════════════════════════════════════════════
print("=" * 70)
print("🍀 Shamrock DNS & Cloudflare Audit v2")
print("=" * 70)

# ═══════════════════════════════════════════════════════════
# 1. VERIFY TOKEN
# ═══════════════════════════════════════════════════════════
print("\n1️⃣  Token Verification:")
verify = cf_get("https://api.cloudflare.com/client/v4/user/tokens/verify")
if verify.get("success"):
    status = verify["result"]["status"]
    print(f"   ✅ Token is {status}")
else:
    print(f"   ❌ Token error: {verify}")
    
# Check what permissions we actually have
print("\n   Testing permission scopes:")
tests = {
    "Zone Read":       f"{ZBASE}",
    "DNS Read":        f"{ZBASE}/dns_records?per_page=1",
    "Zone Settings":   f"{ZBASE}/settings/ssl",
    "Tunnel List":     f"{ABASE}/cfd_tunnel",
    "Account Details": f"{ABASE}",
}
perms = {}
for name, url in tests.items():
    result = cf_get(url)
    ok = result.get("success", False)
    code = result.get("http_code", 200 if ok else "?")
    perms[name] = ok
    icon = "✅" if ok else f"❌ ({code})"
    print(f"   {icon} {name}")

# ═══════════════════════════════════════════════════════════
# 2. ZONE INFO
# ═══════════════════════════════════════════════════════════
print(f"\n2️⃣  Zone Info:")
zone = cf_get(ZBASE)
if zone.get("success"):
    z = zone["result"]
    print(f"   Zone: {z['name']}")
    print(f"   Status: {z['status']}")
    print(f"   CF NS: {', '.join(z['name_servers'])}")
    print(f"   Current NS: {', '.join(z.get('original_name_servers', []))}")
    if z['status'] == 'pending':
        print(f"   ℹ️  Zone is 'pending' because NS are on Wix, not CF.")
        print(f"      This is FINE — tunnels work regardless of zone status.")

# ═══════════════════════════════════════════════════════════
# 3. DNS RECORDS (if we have permission)
# ═══════════════════════════════════════════════════════════
print(f"\n3️⃣  Cloudflare DNS Records:")
if perms.get("DNS Read"):
    records = cf_get(f"{ZBASE}/dns_records?per_page=100")
    if records.get("success"):
        recs = records["result"]
        print(f"   Found {len(recs)} records:")
        for r in recs:
            proxy = "🟠" if r.get("proxied") else "⚪"
            print(f"   {proxy} {r['type']:6} {r['name'][:45]:45} → {r['content'][:50]}")
else:
    print(f"   ⚠️  Token lacks DNS read permission — skipping")
    print(f"      (This is OK — DNS is managed via Wix anyway)")

# ═══════════════════════════════════════════════════════════
# 4. TUNNEL STATUS
# ═══════════════════════════════════════════════════════════
print(f"\n4️⃣  Cloudflare Tunnels:")
if perms.get("Tunnel List"):
    tunnels = cf_get(f"{ABASE}/cfd_tunnel?is_deleted=false")
    if tunnels.get("success"):
        for t in tunnels["result"]:
            conns = t.get("connections", [])
            status = t.get("status", "unknown")
            emoji = "🟢" if status == "healthy" else "🔴" if status == "down" else "🟡"
            print(f"   {emoji} {t['name']:15} Status: {status:10} Connections: {len(conns)}")
            print(f"      ID: {t['id']}")
            for c in conns:
                colo = c.get("colo_name", "?")
                ip = c.get("origin_ip", "?")
                print(f"      └─ {colo} (origin: {ip})")
    else:
        print(f"   Error: {tunnels.get('error', 'unknown')[:100]}")
else:
    print(f"   ⚠️  Token lacks tunnel permission — skipping")

# ═══════════════════════════════════════════════════════════
# 5. ZONE SETTINGS (if we have permission)
# ═══════════════════════════════════════════════════════════
print(f"\n5️⃣  Zone Settings:")
if perms.get("Zone Settings"):
    settings = ["ssl", "always_use_https", "min_tls_version", 
                "security_level", "browser_check", "http2", "brotli"]
    for sid in settings:
        s = cf_get(f"{ZBASE}/settings/{sid}")
        if s.get("success"):
            val = s["result"]["value"]
            print(f"   {sid:25} = {val}")
        else:
            print(f"   {sid:25} = (unavailable)")
    
    # Try to optimize
    print(f"\n   🔧 Applying optimizations...")
    opts = {
        "ssl": "full",
        "always_use_https": "on",
        "min_tls_version": "1.2",
        "security_level": "medium",
        "browser_check": "on",
    }
    for sid, val in opts.items():
        result = cf_patch(f"{ZBASE}/settings/{sid}", {"value": val})
        if result.get("success"):
            print(f"   ✅ {sid} → {val}")
        else:
            code = result.get("http_code", "?")
            print(f"   ⚠️  {sid}: HTTP {code} (may need higher perms)")
else:
    print(f"   ⚠️  Token lacks settings permission — skipping")

# ═══════════════════════════════════════════════════════════
# 6. DNS PROPAGATION TEST (local dig/nslookup)
# ═══════════════════════════════════════════════════════════
print(f"\n6️⃣  DNS Propagation Test (local resolution):")
subdomains = ["bb.shamrockbailbonds.biz", "imac.shamrockbailbonds.biz", 
              "leads.shamrockbailbonds.biz", "shamrockbailbonds.biz"]
for sub in subdomains:
    try:
        result = subprocess.run(["dig", "+short", sub], capture_output=True, text=True, timeout=5)
        answer = result.stdout.strip() or "(no answer)"
        lines = answer.split('\n')
        print(f"   {sub:40} → {lines[0]}")
        for line in lines[1:]:
            print(f"   {'':40}   {line}")
    except Exception as e:
        print(f"   {sub:40} → (dig failed: {e})")

# ═══════════════════════════════════════════════════════════
# 7. BLUEBUBBLES CONNECTIVITY TEST
# ═══════════════════════════════════════════════════════════
print(f"\n7️⃣  BlueBubbles Connectivity Test:")
import os as _os
_bb_pw = _os.environ.get("BB_PASSWORD") or _os.environ.get("BLUEBUBBLES_PASSWORD", "")
if not _bb_pw:
    print("   ⚠️  Set BB_PASSWORD or BLUEBUBBLES_PASSWORD to test BlueBubbles")
    bb_url = None
else:
    bb_url = f"https://bb.shamrockbailbonds.biz/api/v1/server/info?password={_bb_pw}"
try:
    if not bb_url:
        raise RuntimeError("BB_PASSWORD not set")
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(bb_url)
    resp = urllib.request.urlopen(req, timeout=10, context=ctx)
    data = json.loads(resp.read())
    print(f"   ✅ BlueBubbles Server ONLINE!")
    print(f"   Server: {data.get('data', {}).get('server_version', '?')}")
    print(f"   OS: {data.get('data', {}).get('os_version', '?')}")
    print(f"   Private API: {data.get('data', {}).get('private_api', '?')}")
except urllib.error.HTTPError as e:
    print(f"   ❌ HTTP {e.code}: {e.read().decode()[:200]}")
except urllib.error.URLError as e:
    print(f"   ❌ Connection failed: {e.reason}")
    print(f"      This likely means DNS hasn't propagated yet.")
    print(f"      Wait 5-10 minutes and try again.")
except Exception as e:
    print(f"   ❌ Error: {e}")

print(f"\n{'='*70}")
print("🍀 Audit complete!")
print("=" * 70)
