# ARCHITECTURE.md — ShamrockLeads System Architecture

> **Last Updated:** 2026-05-15
> **Read `BRAND.md` first** — it defines who we are, what we're building, and the non-negotiable standards.

---

## System Overview

ShamrockLeads is a **statewide arrest intelligence and bond lifecycle platform** deployed on a single Hetzner VPS via Docker Compose. It consists of four Docker services, a cloud database (MongoDB Atlas), and integrations with 10+ external services.

```
                    ┌────────────────────────────────────────┐
                    │          Hetzner VPS (Docker)           │
                    │          178.156.179.237                │
                    │                                        │
                    │  ┌──────────────┐  ┌────────────────┐  │
                    │  │ shamrock-    │  │ shamrock-      │  │
                    │  │ leads        │  │ dashboard      │  │
                    │  │              │  │                │  │
                    │  │ APScheduler  │  │ FastAPI :5050 │  │
                    │  │ 176 Scrapers  │  │ → Nginx :443   │  │
                    │  │ Self-Healing │  │ → ext :8088    │  │
                    │  └──────┬───────┘  └───┬──────┬─────┘  │
                    │         │              │      │        │
                    │  ┌──────┴──────────────┴──┐   │        │
                    │  │    shamrock-net         │   │        │
                    │  │    (Docker bridge)      │   │        │
                    │  └──────┬──────────────────┘   │        │
                    │         │                      │        │
                    │  ┌──────┴───────┐  ┌───────────┴──┐    │
                    │  │ shamrock-    │  │ shamrock-    │    │
                    │  │ traccar      │  │ node-red     │    │
                    │  │ GPS :8082    │  │ Ops :1880    │    │
                    │  │ OsmAnd :5055 │  │ (profile:ops)│    │
                    │  └──────────────┘  └──────────────┘    │
                    └────────────────────────────────────────┘
                               │          │
              ┌────────────────┼──────────┼────────────────┐
              │                │          │                │
        ┌─────▼─────┐  ┌──────▼────┐  ┌──▼──────┐  ┌─────▼─────┐
        │ MongoDB   │  │ Slack     │  │ Blue    │  │ SignNow   │
        │ Atlas     │  │ Webhooks  │  │ Bubbles │  │ E-Sign    │
        │ (primary) │  │ (12 ch)   │  │ iMessage│  │ API       │
        └───────────┘  └───────────┘  └─────────┘  └───────────┘
              │
    ┌─────────┼──────────────────────┐
    │         │                      │
┌───▼───┐ ┌──▼──────┐  ┌────────────▼──┐
│Twilio │ │SwipeSimp│  │Google Workspace│
│SMS    │ │Payments │  │Gmail, GCal,   │
│       │ │         │  │Drive, Sheets  │
└───────┘ └─────────┘  └───────────────┘
```

---

## Docker Services

| Service | Container | Internal | External | Technology | Purpose |
|---------|-----------|----------|----------|------------|---------|
| `shamrock-leads` | `shamrock-leads` | — | — | Python 3.12 + APScheduler | Scraper engine: 176 county scrapers, lead scoring, dedup |
| `dashboard` | `shamrock-dashboard` | 5050 | 8088 | FastAPI + Uvicorn | Intelligence dashboard: 61 API modules, 36 services, 42 JS modules |
| `traccar` | `shamrock-traccar` | 8082 | 8082 | Java (Traccar) | GPS tracking: OsmAnd (5055), TK103 (5001), H02 (5013), GT06 (5023) |
| `node-red` | `shamrock-node-red` | 1880 | 1880 | Node-RED | Ops dashboard, 39+ cron jobs (profile: `ops`) |

### Networking

All services share the `shamrock-net` Docker bridge network. Container hostnames resolve via Docker DNS (e.g., Traccar forwards webhooks to `http://shamrock-dashboard:5050/api/traccar/webhook`).

### DNS

Both `shamrock-leads` and `shamrock-dashboard` use custom DNS (`8.8.8.8`, `1.1.1.1`) to bypass VPS resolver issues with external services.

---

## Data Flow

### Scraper Pipeline

```
County Jail Roster
    │
    ▼
BaseScraper.run()
    ├── Pre-flight URL check (HEAD request)
    ├── County-specific scrape_arrests()
    ├── Parse → list[ArrestRecord] (39 fields)
    ├── DedupEngine (County + Booking_Number)
    ├── LeadScorer (0-100, Hot/Warm/Cold/Disqualified)
    ├── MongoWriter → arrests collection (upsert)
    ├── SheetsWriter → Google Sheets (legacy, optional)
    └── SlackNotifier → #new-arrests / #leads (hot only)
```

### Bond Lifecycle Pipeline

```
ArrestLead (scraped)
    → Defendant (normalized, deduplicated)
        → Indemnitor Intake (Wix / Telegram / Walk-in / Phone)
            → Match (confidence-scored, human-gated)
                → BondCase (Surety + POA + Case#)
                    → DocumentPacket (SignNow templates, hydrated)
                        → Signature (SignNow webhook confirms)
                            → Payment (SwipeSimple premium)
                                → Active Bond (7-status Kanban lifecycle)
                                    → Court Reminders → Discharge → Exoneration
```

### Active Bond Lifecycle (7-Status Kanban)

```
Active → Monitoring → Alert → Exonerated
                           → Forfeited    } POA auto-released
                           → Surrendered  }
                                    → Reinstated
```

- **Destructive transitions** (Forfeited, Surrendered): require confirmation modal
- **POA auto-release**: triggered on Exonerated, Forfeited, Surrendered
- **Audit trail**: every transition logged to `status_history[]` + `audit_events` collection

---

## External Integrations

| Service | Direction | Mechanism | Data |
|---------|-----------|-----------|------|
| **MongoDB Atlas** | Read/Write | `motor` (async) | All entities (16 collections) |
| **Slack** | Write | Webhook POST | Arrest alerts, hot leads, errors, ops (12+ channels) |
| **BlueBubbles** | Read/Write | REST API via ngrok tunnel | iMessage send/receive, contact sync, automation |
| **SignNow** | Read/Write | REST API + webhooks | Packet creation, field hydration, signing status, `document.complete` |
| **SwipeSimple** | Write | Payment links | Bond premium collection, payment plan tracking |
| **Twilio** | Write | REST API | Court reminder SMS (7d, 3d, 1d), 10DLC compliant |
| **Google Sheets** | Write | `gspread` | Legacy arrest data storage (optional) |
| **Gmail** | Read | OAuth2 API | Discharge/exoneration email scanning |
| **Google Calendar** | Write | API | Court date sync with color-coding and reminders |
| **Google Drive** | Write | API | Signed PDF storage in case folders |
| **OpenAI** | Read/Write | API | Shannon AI auto-reply, lead enrichment |
| **Traccar** | Read/Write | REST + device protocols | GPS tracking, geofencing, location webhooks |

---

## MongoDB Collections

| Collection | Purpose | Dedup Key |
|------------|---------|-----------|
| `arrests` | Raw scraped arrest records (39 fields) | `county` + `booking_number` |
| `defendants` | Normalized defendant profiles | `Defendant_ID` (UUID) |
| `indemnitors` | Indemnitor intake records | `Indemnitor_ID` (UUID) |
| `matches` | Validated defendant↔indemnitor links | `Match_ID` (UUID) |
| `active_bonds` | Bonded cases with 7-status lifecycle | `Bond_Case_ID` (UUID) |
| `prospective_bonds` | Pre-bond pipeline (leads being worked) | `_id` (ObjectId) |
| `poa_inventory` | Power of Attorney inventory per surety | `poa_number` (unique) |
| `paperwork_packets` | SignNow document packet metadata | `Packet_ID` (UUID) |
| `payments` | Payment log (SwipeSimple) | `Payment_ID` (UUID) |
| `payment_plans` | Scheduled payment plans | `_id` (ObjectId) |
| `intake_queue` | Incoming intake submissions | `_id` (ObjectId) |
| `audit_events` | Immutable state change log | `Event_ID` (UUID) |
| `defendant_notes` | Free-text notes on defendants | `_id` (ObjectId) |
| `court_reminders` | Scheduled SMS court reminders | `_id` (ObjectId) |
| `notifications` | Dashboard notification center | `_id` (ObjectId) |
| `outreach_sequences` | Drip campaign state machine | `_id` (ObjectId) |

See [DATA_MODEL.md](../DATA_MODEL.md) for full schemas and [SCHEMAS.md](SCHEMAS.md) for field-level detail.

---

## Self-Healing Infrastructure

| Feature | Description |
|---------|-------------|
| **Pre-flight URL check** | HEAD request to roster URL before scraping — detects 404/403/SSL early |
| **Retry with backoff** | 3 attempts with exponential backoff (2s, 4s, 8s) |
| **Error classification** | Auto-classifies: `network`, `anti_bot`, `url_changed`, `parse_error`, `ssl_error`, `rate_limited` |
| **Auto-disable** | Scraper disabled after 5 consecutive failures |
| **Auto-re-enable** | Disabled scraper tries one recovery per interval — re-enables on success |
| **Failure history** | Last 10 failures stored with timestamps + error types |
| **Force re-enable** | `scraper.force_enable()` for human override |
| **Health dashboard** | Scraper Health tab shows real-time fleet status with error drill-down |
| **Slack alerts** | Every failure fires to `#scraper-errors` |

---

## Security Architecture

| Layer | Implementation |
|-------|---------------|
| **Authentication** | Dashboard PIN + session cookie (via `SECRET_KEY`) |
| **Secrets** | `.env` file (git-ignored), Docker env injection |
| **PII Protection** | Never logged to Slack/console. MongoDB encryption at rest. |
| **Audit Trail** | Immutable `audit_events` collection for all state changes |
| **Network** | Nginx reverse proxy with SSL (`leads.shamrockbailbonds.biz`) |
| **iMessage Tunnel** | ngrok permanent tunnel with static domain |
| **API Keys** | Rotated via environment variables. Never in source code. |

---

## Codebase Metrics

| Metric | Count |
|--------|-------|
| County scraper files | 51 |
| API blueprint modules | 61 |
| Service modules | 36 |
| Frontend JS modules | 42 |
| Frontend CSS files | 4 |
| Frontend JS LOC | ~22,400 |
| Frontend CSS LOC | ~8,400 |
| Backend API LOC | ~24,300 |
| Backend services LOC | ~13,200 |
| Agent skills | 34 |
| Dashboard tabs | 15 |
| MongoDB collections | 16 |
| External integrations | 12 |

---

*Maintained by: Brendan / Shamrock Active Software LLC | `admin@shamrockbailbonds.biz`*
