# BlueBubbles connectivity — Tailscale + frp

## Status: live (2026-08-26)

Office iMac (`shamrockbailoffice@gmail.com`, **239-955-0178**), BlueBubbles **v1.9.9**, Private API on.

| Client | Use |
|--------|-----|
| Super CRM on Hetzner | **Tailscale** `http://100.102.10.86:1234` |
| Super CRM if mesh down | **frp** `http://178.156.179.237:12434` |
| Shannon / Node-RED | Super CRM `/api/imessage/*` (never Twilio SMS) |
| Wix site | Super CRM relay (GAS `sendBlueBubblesRelay`). Do not point CRM at `bb.shamrockbailbonds.biz` |
| Scrapers needing a home IP | **Warren** / Tailscale exit node — not BlueBubbles |

Health: `GET https://leads.shamrockbailbonds.biz/api/imessage/status` → `connected` + `path_in_use: tailscale`.

### Migration history (do not revive as primary)
- **v1** (2025): Cloudflare quick tunnels (`trycloudflare.com`) — URLs rotated
- **v2**: Named tunnel `bb.shamrockbailbonds.biz` — Wix DNS + MagicDNS conflict (`shamrockbailbonds.biz` is the tailnet)
- **v3** (May 2026): ngrok static domain — worked, not OSS; Firebase kept overwriting the mesh URL
- **v4**: Tailscale mesh — **current primary**
- **v5** (July 2026): frp on the VPS — **current backup**

---

## URLs (do not put ngrok in `BLUEBUBBLES_URL_*`)

```
# Primary (VPS .env)
BLUEBUBBLES_URL_0178=http://100.102.10.86:1234

# Backup
BLUEBUBBLES_FRP_URL=http://178.156.179.237:12434
```

Dashboard `init_bluebubbles()` prefers Tailscale when `100.102.10.86:1234` answers. Firebase ngrok URLs are ignored while the mesh is up.

---

## iMac Setup

### Network & Access
- **Tailscale IP (Primary Remote)**: `100.102.10.86`
- **Tailscale Device Name**: `shamrocksimac`
- **Tailnet Domain**: `shamrockbailbonds.biz`
- **Tailscale Remote SSH Command**: `ssh shamrockbailbonds@100.102.10.86` or `ssh shamrockbailbonds@shamrocksimac`
- **Local LAN IP**: `10.1.10.52`
- **Public WAN IP**: `96.79.229.158`
- **LAN SSH**: `ssh shamrockbailbonds@10.1.10.52` (Host `imac` in laptop `~/.ssh/config`)

### Verify
```bash
# From the VPS or any tailnet device
curl -sS -m 8 "http://100.102.10.86:1234/api/v1/ping?password=$BLUEBUBBLES_PASSWORD"
curl -sS "https://leads.shamrockbailbonds.biz/api/imessage/status"

# frp backup
curl -sS -m 8 "http://178.156.179.237:12434/api/v1/ping?password=$BLUEBUBBLES_PASSWORD"
```

Expected: ping `pong`; status `connected: true`, `path_in_use: tailscale`, `private_api: true`.

Passwords live in VPS `.env` (`BLUEBUBBLES_PASSWORD_0178`) and iMac LaunchAgent env — not in git.

### iMac keep-alives
- BlueBubbles.app (v1.9.9) + Messages.app logged in
- Tailscale.app on, hostname `shamrocksimac`, IP `100.102.10.86`
- `com.shamrock.frpc` LaunchAgent (backup)
- `com.shamrock.bb-watchdog` LaunchAgent (restart BB if `:1234` dies)

See `scripts/imac/` and `docs/FRP_TUNNEL.md`.

---

## VPS `.env` Values

```env
BLUEBUBBLES_URL_0178=http://100.102.10.86:1234
BLUEBUBBLES_URL=http://100.102.10.86:1234
BLUEBUBBLES_FRP_URL=http://178.156.179.237:12434
BLUEBUBBLES_PASSWORD_0178=<script property / VPS env only>
BB_WEBHOOK_PUBLIC_URL=https://leads.shamrockbailbonds.biz
```

---

## Historical diagram (v3 ngrok — not current)

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
