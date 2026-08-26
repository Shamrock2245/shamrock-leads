# BlueBubbles Versioning — ShamrockLeads

> **Last updated:** 2026-08-26  
> **Office server (live):** BlueBubbles **Server v1.9.9** · Private API connected · Tailscale primary · frp backup  
> **Do not confuse App releases with Server releases.**

---

## 1. Two products, two version lines

| Product | Repo | What it is | Shamrock uses it? |
|---------|------|------------|-------------------|
| **BlueBubbles Server** | [BlueBubblesApp/bluebubbles-server](https://github.com/BlueBubblesApp/bluebubbles-server) | Electron app on the **office iMac** — REST API, webhooks, Private API helper | **Yes — required** |
| **BlueBubbles App** | [BlueBubblesApp/bluebubbles-app](https://github.com/BlueBubblesApp/bluebubbles-app) | Flutter client for Android / Windows / Linux / web (read iMessage on non-Apple devices) | **Optional** (staff personal use) |

**Example:** [App v2.0.0+89](https://github.com/BlueBubblesApp/bluebubbles-app/releases/tag/v2.0.0%2B89) (2026-07-24) is a **client rewrite** (performance, themes, notifications). It does **not** replace or upgrade the Mac server. Server latest as of this writing remains **v1.9.9**.

---

## 2. Repos that inform our integration

| Repo / resource | Use for Shamrock |
|-----------------|------------------|
| `bluebubbles-server` | REST + webhook contracts; release notes for **server** only |
| `bluebubbles-docs` | Official API / Private API install docs |
| `bluebubbles-n8n-node` | Webhook automation patterns (`isFromMe === false`) |
| `bluebubbles-helper` | Private API dylib (already installed for typing/tapbacks/effects) |
| `bluebubbles-app` | Reference for modern **query** APIs / socket clients — not imported into the dashboard |
| Community API gist patterns | Confirms `POST /api/v1/message/query`, `POST /api/v1/chat/query` |

**Not used in CRM path:** Wear OS, themes, copilot, desktop-app legacy forks.

---

## 3. API contracts we depend on (Server 1.9.x)

Verified live against office Server **1.9.9**:

| Operation | Correct API | Broken / legacy |
|-----------|-------------|-----------------|
| List recent messages | `POST /api/v1/message/query` | `GET /api/v1/message` → **404** |
| List chats | `POST /api/v1/chat/query` | `GET /api/v1/chat` → **404** |
| Chat history | `POST /api/v1/message/query` + `chatGuid` | Prefer over old chat GUID GET |
| Send text | `POST /api/v1/message/text` | — |
| Webhooks | `GET/POST/DELETE /api/v1/webhook` | — |
| Webhook payload | `{ "type": "new-message", "data": { …message fields… } }` | `data` **is** the message (not `data.message`) |

Shamrock implementation: `dashboard/routers/bb_private_api.py`, `bb_webhook_receiver.py`, `imessage_automation.py`.

---

## 4. Inbound path (desktop replies)

```
BB Server (iMac)
  ├─ Webhook POST → /api/webhooks/bluebubbles  (real-time)
  └─ Poller        → POST message/query every ~30s (always on)
         ↓
  MongoDB imessage_outreach
         ↓
  GET /api/imessage/thread/{phone}?hydrate=1  (also pulls live chat history)
         ↓
  sl-imessage.js  (SSE message_received / new_reply + poll)
```

- Auto-reply **AI** is gated by `outreach_config.enabled` (default off).
- **Inbound polling is not gated** by that flag (so replies always land in the inbox).

---

## 5. Structured upgrade procedure

### A. App only (v2.0.0+89 on a phone/Windows box)

1. Install from Play Store / Microsoft Store / GitHub release.
2. Point the app at the existing server URL + password.
3. No VPS or Shamrock code change required.

### B. Server upgrade (when a **new server** tag ships, e.g. 1.10+ or 2.x server)

1. **Document** current: server version, Private API status, SIP state, ngrok domain, webhook URLs.
2. **Snapshot** iMac (Time Machine / disk image if available).
3. Download **server** DMG from [server releases](https://github.com/BlueBubblesApp/bluebubbles-server/releases) only.
4. Install off-hours; re-enable Private API if helper breaks.
5. Confirm: `GET /api/v1/server/info` → new version; `POST message/query` → 200.
6. Re-register webhooks: `POST /api/webhooks/bluebubbles/register` (or BB UI).
7. Smoke: send from dashboard → reply on phone → reply appears in desktop thread within 30s.
8. Only then update `STATUS.md` server version line.

### C. Do **not**

- Install App v2 expecting server API changes.
- Jump macOS major (e.g. Tahoe 26) without checking BB Private API issues for that OS.
- Rely on `GET /api/v1/message` — removed / broken on modern servers.

---

## 6. Env knobs (dashboard)

| Variable | Role |
|----------|------|
| `BLUEBUBBLES_URL_0178` / `BLUEBUBBLES_PASSWORD_0178` | Primary office line |
| `BLUEBUBBLES_URL_0314` / `…_0314` | Optional second line |
| `BB_WEBHOOK_PUBLIC_URL` | Public HTTPS base for webhook registration |
| `BB_WEBHOOK_SECRET` | Optional; BB usually does **not** send HMAC — empty signature is accepted when secret is set |

---

## 7. Related code & docs

- `dashboard/routers/bb_private_api.py` — async BB client  
- `dashboard/routers/bb_webhook_receiver.py` — inbound webhook  
- `dashboard/routers/imessage_automation.py` — poller, thread, hydrate  
- `dashboard/sl-imessage.js` — desktop Control Center  
- `CHANGELOG.md` — 2.17.0  
- `STATUS.md` — live truth  
