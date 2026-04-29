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
- **BB Server:** Electron app on iMac, port 1234, exposed via Cloudflare tunnel
- **Env vars:** `BLUEBUBBLES_URL_0178`, `BLUEBUBBLES_PASSWORD_0178`
- **Private API:** Requires SIP disabled + helper bundle on the iMac

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
