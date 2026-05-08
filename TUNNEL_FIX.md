# BlueBubbles Tunnel — Permanent Cloudflare Named Tunnel

## Status: ✅ FULLY OPERATIONAL (May 8, 2026)

The BlueBubbles iMessage bridge on the office iMac (`shamrockbailoffice@gmail.com`, phone `239-955-0178`)
is running on a **permanent Cloudflare Named Tunnel**.

### All Checks Passing ✅
- Tunnel daemon: 4 active connections from Miami POPs (mia02, mia04) ✅
- DNS: `bb.shamrockbailbonds.biz` → CNAME → `bd9101bf-...cfargotunnel.com` ✅
- BB Server: v1.9.6 running on iMac port 1234 (macOS 14.4.1) ✅
- Local API: `curl localhost:1234/api/v1/server/info?password=2245Bail` works ✅
- Remote API: `curl https://bb.shamrockbailbonds.biz/api/v1/server/info?password=2245Bail` works ✅
- Private API: enabled, helper_connected: true ✅

### Root Cause of Previous Timeout
The Cloudflare zone was in "pending" status (NS on Wix, not CF). Cloudflare's
auto-created DNS records were invisible to the internet. **Fix:** Add CNAME
records directly in **Wix DNS** pointing `bb` → `cfargotunnel.com`.

### Migration History
- **v1** (2025): Cloudflare quick tunnels (`trycloudflare.com`) — random URLs, rotated on every restart
- **v2** (May 2025): ngrok static domain (`pseudospherical-etta-untactually.ngrok-free.dev`) — died
- **v3** (May 2026): Cloudflare named tunnel → `bb.shamrockbailbonds.biz` — **permanent, stable, ours**

---

## Permanent Tunnel URL

```
https://bb.shamrockbailbonds.biz
```

This URL **never changes** — it's a CNAME to our Cloudflare named tunnel.

---

## iMac Setup

### Quick Start (One Command)
```bash
cd /path/to/shamrock-leads
bash scripts/setup_bb_tunnel.sh
```

### Manual Steps

#### 1. Install cloudflared
```bash
brew install cloudflare/cloudflare/cloudflared
```

#### 2. Disable BlueBubbles Built-in Proxy
- Open **BlueBubbles Server** on the iMac
- Go to **Settings → Proxy Service**
- Switch to **"None"** or **"Custom URL"**
- Save

#### 3. Authenticate & Create Tunnel
```bash
# Login (opens browser → select shamrockbailbonds.biz zone)
cloudflared tunnel login

# Create named tunnel
cloudflared tunnel create bluebubbles

# Route DNS
cloudflared tunnel route dns bluebubbles bb.shamrockbailbonds.biz
```

#### 4. Configure Tunnel
```bash
cat > ~/.cloudflared/config.yml << 'EOF'
tunnel: bd9101bf-39a5-4b7a-97a8-d024c973c769
credentials-file: ~/.cloudflared/bd9101bf-39a5-4b7a-97a8-d024c973c769.json

ingress:
  - hostname: bb.shamrockbailbonds.biz
    service: http://localhost:1234
    originRequest:
      noTLSVerify: true
  - service: http_status:404
EOF
```

#### 5. Install as LaunchAgent (Auto-Start on Boot)
The `setup_bb_tunnel.sh` script handles this automatically. It creates:
```
~/Library/LaunchAgents/com.cloudflare.bluebubbles-tunnel.plist
```

#### 6. Verify
```bash
curl -s 'https://bb.shamrockbailbonds.biz/api/v1/server?password=2245Bail' \
  | python3 -m json.tool | grep -E 'private_api|helper_connected'
```
Expected: `"private_api": true`, `"helper_connected": true`

---

## VPS `.env` Values

```env
BLUEBUBBLES_URL_0178=https://bb.shamrockbailbonds.biz
BLUEBUBBLES_URL=https://bb.shamrockbailbonds.biz
BLUEBUBBLES_PASSWORD_0178=2245Bail
BLUEBUBBLES_PASSWORD=2245Bail
BB_WEBHOOK_PUBLIC_URL=https://leads.shamrockbailbonds.biz
BB_WEBHOOK_SECRET=f44694bd92293fe5b27c4a68adaf6991911ba067fcb813926bd6653bcd556754
BB_CONFIG_API_KEY=shamrock-bb-sync-2245
```

---

## Cloudflare Credentials

| Key | Value |
|-----|-------|
| Account ID | `e3ceb175a0ebe60c6e02fe2c38e17691` |
| Zone ID | `5baadd20dd3c4502abaf78019bdde524` |
| API Token | *(stored in `.env` as `CLOUDFLARE_API_TOKEN` — never commit)* |
| Token Name | `fancy-fog-982a` |

---

## Architecture (v3 — Cloudflare Named Tunnel)

```
Office iMac (BlueBubbles Server, port 1234)
    ↕ cloudflared named tunnel (permanent)
https://bb.shamrockbailbonds.biz
    ↕ CNAME → bd9101bf-39a5-4b7a-97a8-d024c973c769.cfargotunnel.com
    ↕ Cloudflare Edge (TLS termination, DDoS protection)
    ↕
Hetzner VPS (Docker: shamrock-leads container)
    → BlueBubblesClient (bb_private_api.py) — all outbound calls
    → BB Webhook Receiver (/api/webhooks/bluebubbles) — inbound events
    → BB Health Monitor — 5-min health checks with Slack alerts
```

### Key Advantages Over ngrok/trycloudflare
- **Zero URL rotation** — `bb.shamrockbailbonds.biz` never changes
- **Our own domain** — professional, memorable, controlled
- **Free** — Cloudflare Tunnel is free for any amount of traffic
- **Auto-restarts** — LaunchAgent keeps it alive after reboots
- **No interstitial pages** — unlike ngrok's browser warning
- **DDoS protection** — Cloudflare edge handles abuse

---

## Troubleshooting

### Tunnel shows offline
```bash
# Check LaunchAgent status
launchctl list | grep cloudflare

# Check logs
tail -50 /tmp/cloudflared-bb.log
tail -50 /tmp/cloudflared-bb-err.log

# Manually restart
launchctl unload ~/Library/LaunchAgents/com.cloudflare.bluebubbles-tunnel.plist
launchctl load ~/Library/LaunchAgents/com.cloudflare.bluebubbles-tunnel.plist
```

### DNS not resolving
```bash
# Check DNS propagation
dig bb.shamrockbailbonds.biz CNAME

# Should show: bb.shamrockbailbonds.biz → bd9101bf-39a5-4b7a-97a8-d024c973c769.cfargotunnel.com
```

### VPS still using old URL
```bash
# Hot-swap (no rebuild needed)
curl -X PATCH https://leads.shamrockbailbonds.biz/api/bb-health/update-url \
  -H 'Content-Type: application/json' \
  -d '{"suffix":"0178","url":"https://bb.shamrockbailbonds.biz","api_key":"shamrock-bb-sync-2245"}'

# Or rebuild for persistence
docker compose build --no-cache dashboard && docker compose up -d dashboard
```

---

## Code Changes Applied

| File | Change |
|------|--------|
| `dashboard/api/bb_private_api.py` | Added `ngrok-skip-browser-warning: true` header (retained for compat) |
| `.env` | Updated BB URLs to `bb.shamrockbailbonds.biz` |
| `.env` | Added Cloudflare credentials (CLOUDFLARE_ACCOUNT_ID, ZONE_ID, API_TOKEN) |
| `scripts/setup_bb_tunnel.sh` | Full automated tunnel setup script for iMac |
| `TUNNEL_FIX.md` | Updated to v3 (Cloudflare named tunnel) |
