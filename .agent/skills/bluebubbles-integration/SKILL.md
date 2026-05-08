---
name: bluebubbles-integration
description: "iMessage outreach via BlueBubbles bridge. Covers REST API, Private API (unsend/edit/typing/effects), human-feel messaging patterns, auto-reply AI agent, SMS/RCS fallback, and the 9-module Python integration layer."
---

# BlueBubbles iMessage Integration Skill

## When to Use
- Building or modifying any iMessage outreach feature
- Adding new BB API endpoints to the dashboard
- Debugging message delivery issues
- Implementing new auto-reply or AI agent behavior
- Working with typing indicators, tapbacks, or message effects
- Adding SMS/RCS fallback for non-iPhone recipients

## Infrastructure
- **Office iMac:** `shamrockbailoffice@gmail.com` / Apple ID paired with `239-955-0178`
- **BB Server:** Electron app on iMac, port 1234 (v1.9.6, Private API enabled, macOS 14.4.1)
- **BB Password:** `2245Bail`
- **Permanent URL:** `https://bb.shamrockbailbonds.biz` (Cloudflare Named Tunnel — ✅ verified 2026-05-08)
- **SSH Access:** `ssh imac` via `imac.shamrockbailbonds.biz` (Cloudflare tunnel)
- **Env vars:** `BLUEBUBBLES_URL` (on VPS `.env`), `BLUEBUBBLES_PASSWORD`
- **Private API:** Requires SIP disabled + helper bundle on the iMac
- **iMac Local IP:** `10.1.10.225` (office LAN)
- **Office Public IP:** `96.79.229.158`

## Tunnel Architecture (Cloudflare Named Tunnel)

**Tunnel UUID:** `bd9101bf-39a5-4b7a-97a8-d024c973c769`
**Tunnel Name:** `bluebubbles`

### How It Works
```
Client → DNS (bb.shamrockbailbonds.biz)
       → CNAME → bd9101bf-...cfargotunnel.com
       → Cloudflare Edge (Miami POPs: mia02, mia05, mia09)
       → Cloudflare Tunnel → iMac cloudflared
       → localhost:1234 (BlueBubbles Server)
```

### DNS Records (Wix DNS — NOT Cloudflare DNS)
| Host | Type | Value |
|------|------|-------|
| `bb` | CNAME | `bd9101bf-39a5-4b7a-97a8-d024c973c769.cfargotunnel.com` |
| `imac` | CNAME | `bd9101bf-39a5-4b7a-97a8-d024c973c769.cfargotunnel.com` |

> **Important:** Nameservers are on Wix (`ns6/ns7.wixdns.net`), NOT Cloudflare.
> The Cloudflare zone is "pending" — this is fine because the CNAME records are in **Wix DNS**,
> which resolves `bb` → `cfargotunnel.com` directly. Verified working 2026-05-08.

### iMac Config (`~/.cloudflared/config.yml`)
```yaml
tunnel: bd9101bf-39a5-4b7a-97a8-d024c973c769
credentials-file: /Users/shamrockbailbonds/.cloudflared/bd9101bf-39a5-4b7a-97a8-d024c973c769.json

ingress:
  - hostname: bb.shamrockbailbonds.biz
    service: http://localhost:1234
  - hostname: imac.shamrockbailbonds.biz
    service: ssh://localhost:22
  - service: http_status:404
```

### Persistence (macOS LaunchAgent)
- **Plist:** `~/Library/LaunchAgents/com.cloudflare.bluebubbles-tunnel.plist`
- **Restart:** `launchctl unload <plist> && launchctl load <plist>`
- **Logs:** `log show --predicate 'process == "cloudflared"' --last 5m`

### Cloudflare API Credentials
- **Account ID:** `e3ceb175a0ebe60c6e02fe2c38e17691`
- **Zone ID:** `5baadd20dd3c4502abaf78019bdde524`
- **API Token:** Stored in VPS `.env` as `CLOUDFLARE_API_TOKEN`
- **Permissions:** Zone Read, Tunnel List, Account Details (no DNS write)

### Fix Script
- **Path:** `scripts/cf_tunnel_check.py` (run on iMac with `--fix` flag)
- **What it does:** Reads tunnel status, checks ingress rules, pushes correct config via Cloudflare API
- **Usage:** `python3 ~/Downloads/cf_tunnel_check.py --fix` (or wherever it lives on the iMac)
- **When to use:** If routes get deleted or corrupted (e.g., by a browser subagent in the Cloudflare dashboard)

### Troubleshooting
| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `curl` to `bb.shamrockbailbonds.biz` times out | cloudflared not running on iMac | `launchctl load` the plist |
| DNS resolves but connection fails | BB server not listening on 1234 | Open BlueBubbles app on iMac |
| `cloudflared tunnel list` shows 0 connections | Tunnel daemon crashed | Restart via launchctl |
| Routes show `--` in CF dashboard | Published routes deleted | Run `cf_tunnel_check.py --fix` on iMac |
| Tunnel healthy but timeout | DNS CNAME missing in Wix | Add CNAME `bb` → `bd9101bf-...cfargotunnel.com` in Wix DNS |

### Health Check Commands (iMac)
```bash
# Is BlueBubbles running?
curl "http://localhost:1234/api/v1/server/info?password=2245Bail" | python3 -m json.tool

# Is cloudflared running?
launchctl list | grep cloudflare

# Tunnel status
cloudflared tunnel list

# View config
cat ~/.cloudflared/config.yml

# Restart tunnel
launchctl unload ~/Library/LaunchAgents/com.cloudflare.bluebubbles-tunnel.plist
launchctl load ~/Library/LaunchAgents/com.cloudflare.bluebubbles-tunnel.plist
```

## Key Modules (dashboard/api/)
| Module | LOC | Purpose |
|--------|-----|---------|
| `bb_private_api.py` | 656 | `BlueBubblesClient` async httpx wrapper |
| `agent_brain.py` | 672 | GPT-4o auto-reply + intent classification |
| `imessage_automation.py` | 667 | Inbox poller (30s), auto-reply orchestration |
| `bb_prospecting.py` | 471 | First-mover outreach to relatives |
| `bb_scheduled_messages.py` | 421 | Future-send scheduling |
| `bb_webhook_receiver.py` | 349 | Inbound webhook processing |
| `bb_health_monitor.py` | 256 | Server health + tunnel status |
| `bb_contact_sync.py` | 251 | BB ↔ MongoDB contact sync |
| `bb_document_delivery.py` | 247 | SignNow/PDF links via iMessage |

## Human-Feel Rules (NON-NEGOTIABLE)
1. **No bot-speak** — never say "I'm an automated system"
2. **Typing indicator first** — always 2-4s before sending
3. **Mark as read** — show presence immediately
4. **Tapback reactions** — ❤️ on positive, 👍 on confirmations
5. **One message at a time** — never spam 3 messages in a row
6. **Time-aware** — respect business hours unless urgent
7. **Natural delays** — random 1-5s between read and typing
8. **Graceful handoff** — "I want a real person" → route to Brendan instantly

## API Quick Reference
- **Send text:** `POST /api/v1/message/text?password=xxx`
- **Chat GUID format:** `iMessage;-;+12395551234` or `SMS;-;+12395551234`
- **Typing:** `POST /api/v1/message/:chatGuid/typing`
- **React:** `POST /api/v1/message/:guid/react`
- **Unsend:** `POST /api/v1/message/:guid/unsend` (Private API)
- **Schedule:** `POST /api/v1/message/schedule` (BB native scheduling)

## Anti-Spam Rules
- Max 20 cold messages per hour
- Random delays between messages (30s-5min)
- Stop sequence immediately on any reply
- Never message the same number twice in 24h without a reply
- Content-hash dedup protects against BB Issue #765 (re-emitted messages)

## References
- [BB API Docs](https://documenter.getpostman.com/view/765844/UV5RnfwM)
- [BB Server Repo](https://github.com/BlueBubblesApp/bluebubbles-server)
- [BB Community Projects](https://github.com/BlueBubblesApp/bluebubbles-community-projects)
- [ChatGPT Agent Reference](https://github.com/omega-bred/bluebubbles-chatgpt-agent)
- [BB MCP Server](https://github.com/jfiggins/bluebubbles-mcp-server)
