# ShamrockLeads — Florida Arrest Intelligence Platform

> **Scrape. Score. Route. Bond.** — Real-time arrest data across all 67 Florida counties.

[![Docker](https://img.shields.io/badge/Docker-Containerized-blue)](Dockerfile)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-green)](https://python.org)
[![MongoDB Atlas](https://img.shields.io/badge/Database-MongoDB%20Atlas-brightgreen)](https://mongodb.com)
[![Counties](https://img.shields.io/badge/Active%20Scrapers-50-orange)](#county-coverage)
[![Dashboard](https://img.shields.io/badge/Dashboard-15%20Tabs-blueviolet)](#intelligence-dashboard)
[![License](https://img.shields.io/badge/License-Proprietary-red)](#license)

---

## What Is This?

ShamrockLeads is a **statewide arrest intelligence and bail bond lifecycle platform** that:

1. **Scrapes** real-time booking data from **50 Florida county** jail rosters on scheduled intervals
2. **Normalizes** every record into a standardized 39-column `ArrestRecord` schema
3. **Deduplicates** using `booking_number + county` composite keys (in-memory + MongoDB)
4. **Scores** each arrest with rule-based lead qualification (0–100: Hot / Warm / Cold / Disqualified)
5. **Alerts** bondsmen via Slack with real-time hot lead notifications
6. **Stores** everything in MongoDB Atlas (`ShamrockBailDB`)
7. **Manages** defendants (notes, contact logs, DNB/DNC flags, lifecycle tracking)
8. **Matches** indemnitor intake to defendants via confidence-scored matching engine
9. **Creates** bonded cases with surety selection (OSI / Palmetto) and POA assignment
10. **Generates** surety-specific 14-document paperwork packets via SignNow
11. **Orchestrates** e-signatures with webhook-driven completion tracking
12. **Collects** premium payments via SwipeSimple integration
13. **Manages** the 7-status active bond lifecycle via drag-and-drop Kanban
14. **Automates** iMessage outreach via BlueBubbles bridge to the office iMac
15. **Detects** re-arrests of defendants on active bonds
16. **Monitors** Gmail for court discharge/exoneration emails
17. **Syncs** court dates to Google Calendar with Twilio SMS reminders
18. **Visualizes** everything through a **15-tab Intelligence Dashboard**

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        SCRAPER ENGINE                                │
│                                                                      │
│  50 County Scrapers (Python 3.12)                                    │
│  ┌────────────┐  ┌────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │DrissionPage│  │ curl_cffi  │  │ requests +   │  │ Patchright │  │
│  │ (Chromium) │  │(TLS spoof) │  │BeautifulSoup │  │ (Stealth)  │  │
│  └─────┬──────┘  └─────┬──────┘  └──────┬───────┘  └─────┬──────┘  │
│        └───────────┬────┴────────────────┴────────────────┘         │
│                    ▼                                                 │
│           BaseScraper.run()                                          │
│           ├── scrape()      → county-specific logic                  │
│           ├── score()       → LeadScorer (0-100)                     │
│           ├── dedup()       → DedupEngine                            │
│           ├── write()       → MongoWriter + SheetsWriter             │
│           └── alert()       → SlackNotifier                          │
│                                                                      │
│  Self-Healing: URL pre-flight, 3x retry, error classification,      │
│  auto-disable after 5 failures, auto-recovery attempts               │
└──────────────────────┬───────────────────────────────────────────────┘
                       │
                APScheduler (per-county intervals)
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐
    │ MongoDB  │ │ Google   │ │  Slack   │
    │ Atlas    │ │ Sheets   │ │ Webhooks │
    │ (primary)│ │ (legacy) │ │ (alerts) │
    └────┬─────┘ └──────────┘ └──────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│               INTELLIGENCE DASHBOARD (Quart, port 5050)              │
│                                                                      │
│  15 Tabs:                                                            │
│  📊 Command Center    │ 🔍 Lead Explorer     │ 👤 Defendants         │
│  📱 Outreach (Kanban) │ 🏥 Scraper Health    │ 🔒 Active Bonds      │
│  📍 Tracking          │ 📥 Intake Queue      │ 🤝 Indemnitors       │
│  📋 POA Inventory     │ 💬 iMessage          │ 📈 Analytics          │
│  📅 Calendar          │ 📄 Reports           │ 🔔 Notifications      │
│                                                                      │
│  49 API modules  │  21 service modules  │  32 frontend JS modules    │
│  ~25,700 LOC (frontend)  │  ~24,300 LOC (backend)                    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

```bash
# Clone
git clone git@github.com:Shamrock2245/shamrock-leads.git
cd shamrock-leads

# Configure
cp .env.example .env
# Edit .env with your MongoDB URI, Slack webhooks, BlueBubbles URL, etc.

# Run with Docker (production)
docker compose up -d

# Run locally (dev)
pip install -r requirements.txt
python main.py

# Run a single county
python main.py lee
```

**Dashboard:** `http://localhost:5050` (Docker maps external 8088 → internal 5050)
**Production:** `https://leads.shamrockbailbonds.biz` (Nginx reverse proxy → `178.156.179.237:8088`)

---

## Intelligence Dashboard

A premium **15-tab operations center** with ~25,700 lines of frontend code across 32 JS modules:

| Tab | Module | Purpose |
|-----|--------|---------|
| 📊 **Command Center** | `sl-core.js`, `sl-data.js` | KPI cards, county heatmap, recent arrests, system status |
| 🔍 **Lead Explorer** | `sl-features.js` | Filterable arrest grid, lead scores, export to CSV/Slack |
| 👤 **Defendants** | `defendants.js`, `sl-defendant-lifecycle.js` | Card grid with lifecycle notes, contact log, DNB/DNC, bond finalize |
| 📱 **Outreach** | `sl-prospective.js` | Kanban pipeline (Contacted → Negotiating → Paperwork → Ready), iMessage bridge |
| 🏥 **Scraper Health** | `sl-health.js` | Fleet status, error drill-down, manual triggers, auto-recovery |
| 🔒 **Active Bonds** | `sl-active-bonds.js` | 7-status Kanban (Active → Monitoring → Alert → Exonerated/Forfeited/Surrendered → Reinstated), destructive drop confirmations, POA auto-release |
| 📍 **Tracking** | `sl-tracking.js` | GPS/check-in tracking, compliance monitoring |
| 📥 **Intake Queue** | `sl-intake.js` | Wix/Telegram intake processing, defendant matching |
| 🤝 **Indemnitors** | `sl-indemnitor.js` | Full indemnitor profiles, payment plans, document packets |
| 📋 **POA Inventory** | `sl-inventory.js` | Power of Attorney management (OSI + Palmetto sureties) |
| 💬 **iMessage** | `sl-imessage.js` | BlueBubbles control center — inbox, health, FindMy, automation |
| 📈 **Analytics** | `sl-analytics.js`, `sl-analytics-apex.js` | Revenue sparkline, county treemap, risk heatmap (ApexCharts) |
| 📅 **Calendar** | `sl-calendar.js`, `sl-calendar-ext.js` | Court date calendar with Google Calendar sync |
| 📄 **Reports** | `sl-reports.js`, `sl-reports-ui.js` | Liability, commissions, reconciliation reports |
| 🔔 **Notifications** | `sl-notifications.js` | Notification center — re-arrest alerts, system events |

### Additional Frontend Modules

| Module | Purpose |
|--------|---------|
| `sl-overhaul.js` + `sl-overhaul.css` | Command Palette (Ctrl+K), unified toast system, county badges, KPI animations |
| `sl-design-system.css` | Design tokens, CSS custom properties, component primitives |
| `sl-record-bond.js` | Bond recording modal with surety/POA/case# selection |
| `sl-tab-polish.js` | Tab transition animations |
| `sl-animations.js` | Micro-animations (count-up, hover lift, fade-in) |
| `sl-refinements.js` | UX polish and edge-case handling |

---

## County Coverage

**50 county scrapers** across Florida, with 3 shared base classes for common JMS platforms:

### Scraper Strategies

| Strategy | Library | Use Case | Counties |
|----------|---------|----------|----------|
| **Browser Automation** | DrissionPage | JS-heavy, login walls, anti-bot | Charlotte, Hillsborough, Manatee, Pinellas, Volusia, Pasco + more |
| **Stealth Browser** | Patchright | Advanced anti-bot (Playwright fork) | Sarasota |
| **TLS Fingerprint** | curl_cffi | TLS fingerprint detection | Collier, Hendry |
| **Standard HTTP** | requests + BeautifulSoup | Simple HTML/REST APIs | Lee, DeSoto, Brevard, Escambia, Orange, Polk + more |

### Shared Base Classes

| Base Class | JMS Platform | Counties |
|-----------|-------------|----------|
| `P2CBaseScraper` | Police-to-Citizen (CentralSquare) | Clay, Marion, Alachua, Putnam |
| `SmartCOPBaseScraper` | SmartCOP Solutions | Baker, Bradford, Calhoun, Columbia, Franklin + 14 more |
| `GenericAdaptiveScraper` | Auto-detect (HTML tables) | Fallback for unknown platforms |

> **Goal:** All 67 Florida counties. See [COUNTY_REGISTRY.md](docs/COUNTY_REGISTRY.md) for the full registry.

---

## API Endpoints

The dashboard exposes **200+ REST endpoints** across **49 API modules** and **21 service modules**:

| Module | Prefix | Key Endpoints |
|--------|--------|---------------|
| `arrests` | `/api/arrests` | Search, filter, export arrests |
| `leads` | `/api/leads` | Lead queries with scoring |
| `defendants` | `/api/defendants` | Defendant search and detail |
| `defendant_lifecycle` | `/api/defendant-notes` | Notes, contact log, DNB/DNC, promote-to-pipeline |
| `prospective_bonds` | `/api/prospective` | Kanban pipeline CRUD, stage transitions |
| `bonds` | `/api/bonds` | Active bond management, 7-status lifecycle, Kanban |
| `bond_lifecycle` | `/api/bond-lifecycle` | SignNow webhook, court email processing, signing initiation |
| `poa` | `/api/poa` | POA inventory (OSI + Palmetto), assign/release/swap |
| `matching` | `/api/matching` | Defendant↔Indemnitor matching engine |
| `intake` | `/api/intake` | Intake queue management |
| `paperwork` | `/api/paperwork` | SignNow packet generation and delivery |
| `payments` | `/api/payments` | Payment log, payment plans |
| `contacts` | `/api/contacts` | OSINT contact discovery |
| `outreach` | `/api/outreach` | iMessage drip campaign sequencing |
| `tracking` | `/api/tracking` | GPS/check-in compliance |
| `court_reminders` | `/api/court` | Court date reminders (Twilio SMS) |
| `calendar` | `/api/calendar` | Google Calendar sync |
| `discharge_monitor` | `/api/discharge` | Gmail discharge/exoneration scanner |
| `rearrest_detector` | `/api/rearrest` | Re-arrest detection on active bonds |
| `data_retention` | `/api/retention` | Tiered data purge for M0 512MB limit |
| `events` | `/api/events` | Immutable audit event log |
| `bb_*` (8 modules) | `/api/bb/*` | BlueBubbles iMessage bridge (health, webhook, prospecting, scheduling, documents, contacts, Firebase sync) |
| `imessage_automation` | `/api/imessage` | AI-powered message automation |
| `agent_brain` | `/api/agent` | Shannon AI auto-reply agent |
| `scraper_control` | `/api/scraper` | Manual triggers, fleet status |
| `stats` | `/api/stats` | KPIs, county breakdown |

---

## Lead Scoring

Every `ArrestRecord` is scored 0–100 before storage:

| Factor | Points | Condition |
|--------|--------|-----------|
| **Bond Amount** | +30 / +50 | $500+ / $1,500+ |
| **Recency** | +10 / +20 | Arrested <1 day / <2 days |
| **Charge Severity** | +20 | Keywords: Battery, DUI, Theft, Domestic |
| **Disqualified** | → 0 | Status = "Released" or Bond = $0 |

**Tiers:** 🔥 Hot (≥70) · 🟡 Warm (40–69) · ❄️ Cold (10–39) · ⛔ Disqualified (<10)

Hot leads fire a real-time Slack alert with defendant info and bond details.

---

## Project Structure

```
shamrock-leads/
├── main.py                    # Entry point: APScheduler + CLI
├── config/settings.py         # Env-based config with feature flags
├── core/
│   ├── models.py              # ArrestRecord (39-column dataclass)
│   ├── dedup.py               # In-memory + MongoDB deduplication
│   └── scheduler.py           # APScheduler with per-county intervals
├── scrapers/
│   ├── base_scraper.py        # Abstract base: scrape → score → write → alert
│   ├── p2c_base.py            # P2C (Police-to-Citizen) platform base
│   ├── smartcop_base.py       # SmartCOP platform base (19 counties)
│   ├── generic_adaptive.py    # Auto-detect scraper for unknown JMS
│   └── counties/              # One file per county (50 active)
├── scoring/
│   └── lead_scorer.py         # Rule-based lead qualification (0–100)
├── writers/
│   ├── mongo_writer.py        # MongoDB Atlas upsert (primary)
│   ├── sheets_writer.py       # Google Sheets writer (legacy)
│   └── slack_notifier.py      # Real-time Slack alerts
├── dashboard/
│   ├── app.py                 # Quart async server (port 5050)
│   ├── extensions.py          # MongoDB connection pool (motor)
│   ├── index.html             # 15-tab dashboard UI
│   ├── styles.css             # Core CSS (~2,700 lines)
│   ├── sl-overhaul.css        # Design overhaul layer
│   ├── sl-imessage.css        # iMessage tab styles
│   ├── sl-design-system.css   # Design tokens & primitives
│   ├── sl-core.js             # Tab system, KPI cards, theme
│   ├── sl-data.js             # Data fetching, caching, SSE
│   ├── sl-features.js         # Lead explorer, filters, export
│   ├── sl-health.js           # Scraper fleet health monitor
│   ├── sl-prospective.js      # Outreach Kanban pipeline
│   ├── sl-active-bonds.js     # Active Bonds 7-status Kanban
│   ├── sl-defendant-lifecycle.js  # Notes, contact log, lifecycle bridge
│   ├── sl-indemnitor.js       # Indemnitor management
│   ├── sl-inventory.js        # POA inventory modal
│   ├── sl-intake.js           # Intake queue processing
│   ├── sl-tracking.js         # GPS/compliance tracking
│   ├── sl-imessage.js         # iMessage control center
│   ├── sl-analytics.js        # Analytics dashboard
│   ├── sl-analytics-apex.js   # ApexCharts integration
│   ├── sl-calendar.js         # Court calendar
│   ├── sl-calendar-ext.js     # Calendar extensions
│   ├── sl-reports.js          # Reports module
│   ├── sl-reports-ui.js       # Reports UI components
│   ├── sl-notifications.js    # Notification center
│   ├── sl-overhaul.js         # Command palette, toasts, badges
│   ├── sl-record-bond.js      # Bond recording modal
│   ├── sl-tab-polish.js       # Tab transition polish
│   ├── sl-animations.js       # Micro-animations
│   ├── sl-refinements.js      # UX refinements
│   ├── defendants.js          # Defendant card grid
│   ├── api/                   # 49 REST API blueprint modules
│   │   ├── arrests.py         # Arrest search/filter
│   │   ├── bonds.py           # Active bond management
│   │   ├── bond_lifecycle.py  # Signing, webhooks, court emails
│   │   ├── matching.py        # Matching engine
│   │   ├── paperwork.py       # SignNow packet generation
│   │   ├── payments.py        # Payment tracking
│   │   ├── poa.py             # POA inventory
│   │   ├── rearrest_detector.py  # Re-arrest detection
│   │   ├── discharge_monitor.py  # Gmail discharge scanner
│   │   ├── agent_brain.py     # Shannon AI agent
│   │   ├── bb_*.py (8 files)  # BlueBubbles iMessage modules
│   │   └── ...                # 38 more API modules
│   └── services/              # 21 service modules
│       ├── matching_engine.py     # 4-strategy matching pipeline
│       ├── signnow_service.py     # SignNow API wrapper
│       ├── signnow_packet_service.py  # Packet hydration + delivery
│       ├── poa_service.py         # POA tier logic
│       ├── bb_client.py           # BlueBubbles REST client
│       ├── outreach_sequencer.py  # Drip campaign state machine
│       ├── contact_discovery.py   # OSINT contact finder
│       ├── court_reminder_service.py  # Twilio SMS reminders
│       ├── twilio_service.py      # Twilio API wrapper
│       └── ...                    # 12 more service modules
├── docs/
│   ├── SCHEMAS.md             # Data model reference
│   ├── COUNTY_REGISTRY.md     # All 67 counties with JMS vendor info
│   ├── agents/                # AI agent specs
│   ├── policies/              # Business rule policies (surety, matching, signature)
│   └── runbooks/              # Operational runbooks
├── .agent/skills/             # 34 AI agent skills
├── Dockerfile                 # Python 3.12-slim + Chromium
├── docker-compose.yml         # Scraper + Dashboard + Node-RED
├── BRAND.md                   # Identity, vision, design standards
├── AGENTS.md                  # Digital workforce handbook (15 agents)
├── ROADMAP.md                 # 15-phase lifecycle (all complete)
├── DATA_MODEL.md              # 16 MongoDB collections, full schemas
└── GEMINI.md                  # AI agent configuration
```

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Language** | Python 3.12 | Core runtime (~24,300 lines backend) |
| **Scheduling** | APScheduler | Per-county cron with staggered first-runs |
| **Browser** | DrissionPage 4.0+ | Headless Chromium for JS-heavy sites |
| **Stealth Browser** | Patchright | Playwright fork for advanced anti-bot |
| **HTTP** | curl_cffi | TLS fingerprint impersonation |
| **HTTP** | requests + BS4 | Standard scraping + HTML parsing |
| **Database** | MongoDB Atlas (motor) | Primary async storage — `ShamrockBailDB` |
| **Legacy DB** | Google Sheets (gspread) | Optional fallback writer |
| **Alerts** | Slack SDK | Webhook-based real-time notifications (12+ channels) |
| **Dashboard** | Quart (async Flask) | 15-tab intelligence dashboard |
| **Frontend** | Vanilla JS + CSS | ~25,700 lines, 32 modules, premium dark-theme design |
| **iMessage** | BlueBubbles API | Office iMac via ngrok permanent tunnel |
| **AI** | OpenAI GPT-4o | Lead enrichment, auto-reply agent (Shannon) |
| **Signatures** | SignNow API | 14-doc packet orchestration |
| **Payments** | SwipeSimple | Bond premium collection |
| **SMS** | Twilio | Court reminders, 10DLC compliant |
| **Hosting** | Hetzner VPS (Docker) | Production at `178.156.179.237` |
| **Proxy** | Nginx | Reverse proxy → `leads.shamrockbailbonds.biz` |
| **Ops** | Node-RED | 39+ cron jobs, ops dashboard |
| **CI/CD** | GitHub Actions | Automated deployments |

---

## Docker Deployment

```bash
# Build and start all services
docker compose up -d --build

# Rebuild dashboard only (after frontend/API changes)
docker compose build --no-cache dashboard && docker compose up -d dashboard

# View logs
docker logs -f shamrock-leads      # Scraper engine
docker logs -f shamrock-dashboard  # Dashboard

# Deploy from local to Hetzner
ssh root@178.156.179.237 "cd /opt/shamrock-leads && git pull origin main && docker compose build --no-cache && docker compose up -d"
```

| Service | Container | Internal Port | External Port | Purpose |
|---------|-----------|---------------|---------------|---------|
| `shamrock-leads` | `shamrock-leads` | — | — | Scraper engine + APScheduler |
| `dashboard` | `shamrock-dashboard` | 5050 | 8088 | Intelligence dashboard |
| `node-red` | `shamrock-node-red` | 1880 | 1880 | Operations dashboard |

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for full details. **All 15 phases are complete.**

| Phase | Name | Status |
|-------|------|--------|
| 1 | Scrape → Score → Alert | ✅ Complete |
| 1b | County Expansion (50 scrapers) | ✅ Complete |
| 2 | Defendant Normalization + Contact Discovery | ✅ Complete |
| 3 | Intake Queue Processing | ✅ Complete |
| 4 | Matching Engine | ✅ Complete |
| 5 | Bond Case + Surety + POA Inventory | ✅ Complete |
| 6 | Paperwork Generation (SignNow) | ✅ Complete |
| 7 | Signature Orchestration | ✅ Complete |
| 8 | Payment Collection (SwipeSimple) | ✅ Complete |
| 9 | Contact Discovery (OSINT) | ✅ Complete |
| 10 | Outreach Sequencing (iMessage) | ✅ Complete |
| 11 | Bond Tracker — Location Intelligence | ✅ Complete |
| 12 | BlueBubbles Enhancement Suite | ✅ Complete |
| 13 | Bond Lifecycle Kanban + POA Automation | ✅ Complete |
| 14 | Court Automation + Discharge Monitoring | ✅ Complete |
| 15 | Intelligence Dashboard Overhaul | ✅ Complete |

---

## AI Agent Skills

**34 agent skills** in `.agent/skills/` for automated development workflows:

| Skill | Purpose |
|-------|---------|
| `scraper-builder` | Add a new county scraper step-by-step |
| `scraper-debugger` | Debug broken scrapers with self-healing |
| `lead-scoring-tuning` | Adjust scoring weights and thresholds |
| `frontend-design` | Premium dashboard UI/UX |
| `docker-ops` | Container management on Hetzner |
| `git-sync-deploy` | Multi-environment sync workflow |
| `bluebubbles-integration` | iMessage automation (9-module Python layer) |
| `contact-discovery` | OSINT family/friend contact finder |
| `county-jms-patterns` | JMS vendor reverse-engineering |
| `systematic-debugging` | Root-cause-first debugging |
| `pdf-processing` | Bond paperwork PDF manipulation |
| `programmatic-seo` | County-specific landing pages at scale |
| `mongodb-*` (3 skills) | Query optimization, schema design, NL querying |
| `gws-*` (4 skills) | Google Workspace: Calendar, Gmail, Drive, shared auth |
| `cloudflare-*` (3 skills) | Platform, deploy, email |
| `sentry-*` (2 skills) | Error monitoring + fix workflow |
| `wix-*` (3 skills) | App builder, REST management, design system |
| `openai-agents-sdk` | Multi-agent orchestration |
| `elevenlabs-agents` | Shannon voice agent |
| `mcp-builder` | MCP server development |
| `seo-audit` | Page performance & SEO auditing |
| `skill-creator` | Create new agent skills |
| `self-improving-agent` | Session learning & knowledge base |
| `verification-before-completion` | No claims without evidence |

---

## Related Repositories

| Repo | Purpose |
|------|---------|
| [`shamrock-bail-portal-site`](https://github.com/Shamrock2245/shamrock-bail-portal-site) | Wix Velo portal + GAS backend (190+ files) |
| [`shamrock-node-red`](https://github.com/Shamrock2245/shamrock-node-red) | Ops dashboard + 39 cron jobs |
| [`shamrock-bond-tracker`](https://github.com/Shamrock2245/shamrock-bond-tracker) | IP-based location tracking + flight risk scoring |
| [`shamrock-telegram-app`](https://github.com/Shamrock2245/shamrock-telegram-app) | Telegram Mini-Apps (Netlify) |
| [`swfl-arrest-scrapers`](https://github.com/Shamrock2245/swfl-arrest-scrapers) | Legacy scrapers (predecessor) |

---

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `MONGODB_URI` | ✅ | MongoDB Atlas connection string |
| `MONGODB_DB_NAME` | ✅ | Database name (`ShamrockBailDB`) |
| `DASHBOARD_PIN` | ✅ | Dashboard authentication PIN |
| `SECRET_KEY` | ✅ | Session encryption key |
| `SLACK_WEBHOOK_ARRESTS` | ✅ | #new-arrests Slack channel |
| `SLACK_WEBHOOK_LEADS` | ✅ | #leads channel (hot leads) |
| `SLACK_WEBHOOK_ERRORS` | ✅ | #scraper-errors channel |
| `BLUEBUBBLES_URL_0178` | ✅ | ngrok permanent tunnel URL |
| `BLUEBUBBLES_PASSWORD_0178` | ✅ | BlueBubbles API password |
| `SIGNNOW_API_TOKEN` | ✅ | SignNow bearer token |
| `SIGNNOW_BASIC_AUTH` | ✅ | Base64 client_id:client_secret |
| `SIGNNOW_USERNAME` | ✅ | `admin@shamrockbailbonds.biz` |
| `SIGNNOW_PASSWORD` | ✅ | SignNow account password |
| `OPENAI_API_KEY` | Optional | AI agent (Shannon auto-reply) |
| `TWILIO_ACCOUNT_SID` | Optional | Twilio SID for SMS court reminders |
| `TWILIO_AUTH_TOKEN` | Optional | Twilio auth token |
| `TWILIO_FROM_NUMBER` | Optional | Twilio sender number |
| `GOOGLE_APPLICATION_CREDENTIALS` | Optional | GCP service account (Sheets) |
| `FIREBASE_ADMINSDK_PATH` | Optional | Firebase admin SDK for BB URL sync |

---

## License

Proprietary — Shamrock Active Software LLC
