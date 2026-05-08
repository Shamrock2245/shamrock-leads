#!/usr/bin/env python3
"""Check and fix Cloudflare tunnel hostname configuration."""
import urllib.request
import json
import sys
import os

ACCOUNT_ID = "e3ceb175a0ebe60c6e02fe2c38e17691"
API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")
TUNNEL_ID = "bd9101bf-39a5-4b7a-97a8-d024c973c769"

if not API_TOKEN:
    print("ERROR: Set CLOUDFLARE_API_TOKEN env var before running.")
    sys.exit(1)

headers = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json"
}

def cf_get(path):
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/{path}"
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read().decode())

def cf_put(path, data):
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/{path}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="PUT")
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read().decode())

# 1. Tunnel status
print("=" * 60)
print("🔍 TUNNEL STATUS")
print("=" * 60)
data = cf_get(f"cfd_tunnel/{TUNNEL_ID}")
r = data["result"]
print(f"  Name:    {r.get('name')}")
print(f"  Status:  {r.get('status')}")
print(f"  Created: {r.get('created_at')}")
conns = r.get("connections", [])
print(f"  Active Connections: {len(conns)}")
for c in conns:
    print(f"    └── POP: {c.get('colo_name')} | IP: {c.get('origin_ip')} | Pending: {c.get('is_pending_reconnect')}")

# 2. Tunnel configuration (hostname routing)
print()
print("=" * 60)
print("🌐 TUNNEL HOSTNAME CONFIGURATION")
print("=" * 60)
try:
    cfg = cf_get(f"cfd_tunnel/{TUNNEL_ID}/configurations")
    config = cfg.get("result", {}).get("config", {})
    ingress = config.get("ingress", [])
    if not ingress:
        print("  ❌ NO INGRESS RULES CONFIGURED!")
        print("  This is the problem — Cloudflare doesn't know where to route traffic.")
    else:
        print(f"  Found {len(ingress)} ingress rule(s):")
        for i, rule in enumerate(ingress):
            hostname = rule.get("hostname", "(catch-all)")
            service = rule.get("service", "unknown")
            print(f"    [{i}] {hostname} → {service}")
except Exception as e:
    print(f"  ⚠️ Could not read config: {e}")
    print("  This may mean no remote config exists — only local config.yml on iMac.")

# 3. If --fix flag, push hostname configuration
if "--fix" in sys.argv:
    print()
    print("=" * 60)
    print("🔧 PUSHING HOSTNAME CONFIGURATION TO CLOUDFLARE")
    print("=" * 60)
    
    new_config = {
        "config": {
            "ingress": [
                {
                    "hostname": "bb.shamrockbailbonds.biz",
                    "service": "http://localhost:1234",
                    "originRequest": {}
                },
                {
                    "hostname": "imac.shamrockbailbonds.biz",
                    "service": "ssh://localhost:22",
                    "originRequest": {}
                },
                {
                    "service": "http_status:404"
                }
            ]
        }
    }
    
    try:
        result = cf_put(f"cfd_tunnel/{TUNNEL_ID}/configurations", new_config)
        if result.get("success"):
            print("  ✅ Hostname configuration pushed successfully!")
            print("  Cloudflare now knows to route:")
            print("    bb.shamrockbailbonds.biz → http://localhost:1234")
            print("    imac.shamrockbailbonds.biz → ssh://localhost:22")
        else:
            print(f"  ❌ Failed: {json.dumps(result.get('errors', []))}")
    except Exception as e:
        print(f"  ❌ Error: {e}")
else:
    print()
    print("💡 Run with --fix to push hostname configuration to Cloudflare")
    print(f"   python3 {sys.argv[0]} --fix")
