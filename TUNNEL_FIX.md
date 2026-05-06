# BlueBubbles Tunnel — Permanent ngrok Static Domain

## Status: ✅ MIGRATED (May 2025)

The BlueBubbles iMessage bridge on the office iMac (`shamrockbailoffice@gmail.com`, phone `239-955-0178`)
has been **migrated from Cloudflare quick tunnels to a permanent ngrok static domain**.

---

## Permanent Tunnel URL

```
https://pseudospherical-etta-untactually.ngrok-free.dev
```

This URL **never changes** — it is a static domain claimed on the ngrok free tier.

---

## iMac Setup (One-Time)

### 1. Disable BlueBubbles Built-in Cloudflare Proxy
- Open **BlueBubbles Server** app on the office iMac
- Go to **Settings → Proxy Service** (or Connection Settings)
- Switch from "Cloudflare" to **"None"** or **"Custom URL"**
- Save settings

### 2. Start the ngrok Tunnel
```bash
ngrok http 1234 --domain=pseudospherical-etta-untactually.ngrok-free.dev
```

### 3. Make ngrok Persistent (Survive Reboots)

**Option A — Launch Agent (recommended):**
```bash
cat > ~/Library/LaunchAgents/com.ngrok.bluebubbles.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ngrok.bluebubbles</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/ngrok</string>
        <string>http</string>
        <string>1234</string>
        <string>--domain=pseudospherical-etta-untactually.ngrok-free.dev</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/ngrok-bb.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/ngrok-bb.err</string>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.ngrok.bluebubbles.plist
```

**Option B — brew services (if supported):**
```bash
brew services start ngrok
```

### 4. Verify Tunnel is Live
```bash
curl -s -H 'ngrok-skip-browser-warning: true' \
  'https://pseudospherical-etta-untactually.ngrok-free.dev/api/v1/server?password=2245Bail' \
  | python3 -m json.tool | grep -E 'private_api|helper_connected'
```
Expected: `"private_api": true`, `"helper_connected": true`

---

## VPS `.env` Values

```env
BLUEBUBBLES_URL_0178=https://pseudospherical-etta-untactually.ngrok-free.dev
BLUEBUBBLES_URL=https://pseudospherical-etta-untactually.ngrok-free.dev
BLUEBUBBLES_PASSWORD_0178=2245Bail
BLUEBUBBLES_PASSWORD=2245Bail
BB_WEBHOOK_PUBLIC_URL=https://leads.shamrockbailbonds.biz
BB_WEBHOOK_SECRET=f44694bd92293fe5b27c4a68adaf6991911ba067fcb813926bd6653bcd556754
BB_CONFIG_API_KEY=shamrock-bb-sync-2245
```

---

## Code Changes Applied

| File | Change |
|------|--------|
| `dashboard/api/bb_private_api.py` | Added `ngrok-skip-browser-warning: true` header to `_request()` |
| `dashboard/__init__.py` | Fixed `rearrest_bp` import shadowing (aliased notifier as `rearrest_notifier_bp`) |
| `.env.example` | Updated BB URLs to ngrok permanent domain |

---

## Architecture

```
Office iMac (BlueBubbles Server, port 1234)
    ↕ ngrok tunnel (permanent static domain)
https://pseudospherical-etta-untactually.ngrok-free.dev
    ↕
Hetzner VPS (Docker: shamrock-leads container)
    → BlueBubblesClient (bb_private_api.py) — all outbound calls
    → BB Webhook Receiver (/api/webhooks/bluebubbles) — inbound events
    → BB Health Monitor — 5-min health checks with Slack alerts
```
