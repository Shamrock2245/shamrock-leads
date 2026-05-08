# BlueBubbles Tunnel — Permanent ngrok Static Domain

## Status: ✅ FULLY OPERATIONAL (May 8, 2026)

The BlueBubbles iMessage bridge on the office iMac (`shamrockbailoffice@gmail.com`, phone `239-955-0178`)
is running on a **permanent ngrok static domain**.

### All Checks Passing ✅
- ngrok tunnel: stable connection to `pseudospherical-etta-untactually.ngrok-free.dev` ✅
- BB Server: v1.9.6 running on iMac port 1234 (macOS 14.4.1) ✅
- Local API: `curl localhost:1234/api/v1/server/info?password=2245Bail` works ✅
- Remote API: `curl -H "ngrok-skip-browser-warning: true" https://pseudospherical-etta-untactually.ngrok-free.dev/api/v1/server/info?password=2245Bail` works ✅
- Private API: enabled, helper_connected: true ✅

### Migration History
- **v1** (2025): Cloudflare quick tunnels (`trycloudflare.com`) — random URLs, rotated on every restart
- **v2** (Late 2025): Attempted Cloudflare named tunnel (`bb.shamrockbailbonds.biz`) — DNS propagation issues, Wix-hosted zone conflicts with Cloudflare NS requirements
- **v3** (May 2026): **ngrok permanent static domain** — `pseudospherical-etta-untactually.ngrok-free.dev` — **stable, permanent, working**

---

## Permanent Tunnel URL

```
https://pseudospherical-etta-untactually.ngrok-free.dev
```

This is a **permanent ngrok static domain** — it never changes, even after ngrok restarts.

**Important:** All machine-to-machine API calls MUST include the header:
```
ngrok-skip-browser-warning: true
```
Without this header, ngrok returns an HTML interstitial page instead of the API response.

---

## iMac Setup

### Quick Start
```bash
# Install ngrok
brew install ngrok

# Authenticate with ngrok account
ngrok config add-authtoken <your-ngrok-authtoken>

# Start tunnel to BlueBubbles port 1234 with static domain
ngrok http 1234 --domain=pseudospherical-etta-untactually.ngrok-free.dev
```

### Auto-Start on Boot (LaunchAgent)

Create `~/Library/LaunchAgents/com.ngrok.bluebubbles.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ngrok.bluebubbles</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/ngrok</string>
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
    <string>/tmp/ngrok-bb-err.log</string>
</dict>
</plist>
```

Load it:
```bash
launchctl load ~/Library/LaunchAgents/com.ngrok.bluebubbles.plist
```

### Verify
```bash
curl -s -H "ngrok-skip-browser-warning: true" \
  'https://pseudospherical-etta-untactually.ngrok-free.dev/api/v1/server/info?password=2245Bail' \
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

## Architecture (v3 — ngrok Static Domain)

```
Office iMac (BlueBubbles Server, port 1234)
    ↕ ngrok tunnel (permanent static domain)
https://pseudospherical-etta-untactually.ngrok-free.dev
    ↕ ngrok Edge (TLS termination)
    ↕
Hetzner VPS (Docker: shamrock-leads container)
    → BlueBubblesClient (bb_private_api.py) — all outbound calls
    → BB Webhook Receiver (/api/webhooks/bluebubbles) — inbound events
    → BB Health Monitor — 5-min health checks with Slack alerts
```

### Key Design Decisions
- **ngrok over Cloudflare** — Cloudflare named tunnel required NS transfer away from Wix, which broke DNS for the main domain. ngrok works independently.
- **Static domain** — ngrok free tier supports one permanent static domain per account. No URL rotation.
- **`ngrok-skip-browser-warning` header** — Required on all API calls to bypass ngrok's browser interstitial. Already wired into `BlueBubblesClient` (`bb_private_api.py`).
- **Auto-restarts** — LaunchAgent keeps ngrok alive after reboots.

---

## Troubleshooting

### Tunnel shows offline
```bash
# Check LaunchAgent status
launchctl list | grep ngrok

# Check logs
tail -50 /tmp/ngrok-bb.log
tail -50 /tmp/ngrok-bb-err.log

# Manually restart
launchctl unload ~/Library/LaunchAgents/com.ngrok.bluebubbles.plist
launchctl load ~/Library/LaunchAgents/com.ngrok.bluebubbles.plist
```

### VPS still using old URL
```bash
# Hot-swap (no rebuild needed)
curl -X PATCH https://leads.shamrockbailbonds.biz/api/bb-health/update-url \
  -H 'Content-Type: application/json' \
  -d '{"suffix":"0178","url":"https://pseudospherical-etta-untactually.ngrok-free.dev","api_key":"shamrock-bb-sync-2245"}'

# Or rebuild for persistence
docker compose build --no-cache dashboard && docker compose up -d dashboard
```

### ngrok returning HTML instead of JSON
Ensure the `ngrok-skip-browser-warning: true` header is present on all API calls.
The `BlueBubblesClient` in `bb_private_api.py` already includes this header automatically.

---

## Code Changes Applied

| File | Change |
|------|--------|
| `dashboard/api/bb_private_api.py` | Added `ngrok-skip-browser-warning: true` header to all requests |
| `dashboard/services/bb_client.py` | Same header added in the service-layer client |
| `.env` | Updated BB URLs to ngrok permanent domain |
| `.env.example` | Updated with correct ngrok URL |
| `TUNNEL_FIX.md` | Rewritten for ngrok (this file) |
