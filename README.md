# ShamrockLeads — Florida Arrest Intelligence Platform

> **Scrape. Score. Route. Bond.** — Real-time arrest data across all 67 Florida counties.

[![Docker](https://img.shields.io/badge/Docker-Containerized-blue)](Dockerfile)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-green)](https://python.org)
[![MongoDB Atlas](https://img.shields.io/badge/Database-MongoDB%20Atlas-brightgreen)](https://mongodb.com)
[![Counties](https://img.shields.io/badge/Active%20Scrapers-50-orange)](#county-coverage)
[![Dashboard](https://img.shields.io/badge/Dashboard-10%20Tabs-blueviolet)](#intelligence-dashboard)
[![License](https://img.shields.io/badge/License-Proprietary-red)](#license)

---

## What Is This?

ShamrockLeads is a **statewide arrest intelligence and bail bond lifecycle platform** that:

1. **Scrapes** real-time booking data from **50 Florida county** jail rosters on scheduled intervals
2. **Normalizes** every record into a standardized 39-column `ArrestRecord` schema
3. **Deduplicates** using `booking_number + county` composite keys (in-memory + MongoDB)
4. **Scores** each arrest with rule-based lead qualification (0–100: Hot / Warm / Cold / Disqualified)
5. **Alerts** bondsmen via Slack with real-time hot lead notifications
6. **Stores** everything in MongoDB Atlas with Google Sheets as a legacy fallback
7. **Manages** the full defendant lifecycle — notes, contact logs, DNB/DNC flags, status tracking
8. **Bridges** defendants to the Outreach pipeline automatically when contacted
9. **Tracks** prospective bonds through a Kanban board (Contacted → Negotiating → Paperwork → Ready)
10. **Orchestrates** iMessage outreach via BlueBubbles bridge to the office iMac
11. **Manages** indemnitor profiles, payment plans, and surety-specific document packets
12. **Tracks** POA inventory across OSI and Palmetto surety companies
13. **Visualizes** everything through a 10-tab Intelligence Dashboard

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
│            INTELLIGENCE DASHBOARD (Quart + Flask, port 5050)         │
│                                                                      │
│  10 Tabs:                                                            │
│  📊 Command Center    │ 🔍 Lead Explorer     │ 👤 Defendants         │
│  📱 Outreach (Kanban) │ 🏥 Scraper Health    │ 🔒 Active Bonds      │
│  📍 Tracking          │ 📥 Intake Queue      │ 🤝 Indemnitors       │
│  📋 POA Inventory     │                      │                       │
│                                                                      │
│  200+ REST API Endpoints   │  15 Frontend JS Modules                 │
│  iMessage Bridge (BB)      │  AI Outreach Agent Panel                │
│  Defendant Lifecycle       │  Pipeline Auto-Sync                     │
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
**Production:** `http://178.156.179.237:8088/`

---

## Intelligence Dashboard

A premium 10-tab operations center with ~17,600 lines of frontend code:

| Tab | Module | Purpose |
|-----|--------|---------|
| 📊 **Command Center** | `sl-core.js`, `sl-data.js` | KPI cards, county heatmap, recent arrests, system status |
| 🔍 **Lead Explorer** | `sl-features.js` | Filterable arrest grid, lead scores, export to CSV/Slack |
| 👤 **Defendants** | `defendants.js`, `sl-defendant-lifecycle.js` | Card grid with lifecycle notes, contact log, DNB/DNC, bond finalize |
| 📱 **Outreach** | `sl-prospective.js` | Kanban pipeline (Contacted → Negotiating → Paperwork → Ready), iMessage bridge, AI agent |
| 🏥 **Scraper Health** | `sl-health.js` | Fleet status, error drill-down, manual triggers, auto-recovery |
| 🔒 **Active Bonds** | `sl-active-bonds.js` | Bonded cases, court dates, payment status |
| 📍 **Tracking** | `sl-tracking.js` | GPS/check-in tracking, compliance monitoring |
| 📥 **Intake Queue** | `sl-intake.js` | Wix/Telegram intake processing, defendant matching |
| 🤝 **Indemnitors** | `sl-indemnitor.js` | Full indemnitor profiles, payment plans, document packets |
| 📋 **POA Inventory** | `sl-inventory.js` | Power of Attorney management (OSI + Palmetto sureties) |

### Defendant Lifecycle Bridge

The system automatically bridges defendants from the Lead Explorer/Defendants tabs to the Outreach pipeline:

- **Auto-sync:** When a contact is logged or status changes to "contacted"+, the defendant is promoted to `prospective_bonds`
- **Manual promote:** "📱 Move to Outreach" button in the Shamrock Notes modal
- **Pipeline status:** Badge in modal header shows tracking state
- **Unified timeline:** Communication logs sync between `defendant_notes` and `prospective_bonds`

---

## County Coverage

**50 county scrapers** across Florida, with 3 shared base classes for common JMS platforms:

### Scraper Strategies

| Strategy | Library | Use Case | Counties |
|----------|---------|----------|----------|
| **Browser Automation** | DrissionPage | JS-heavy, login walls, Cloudflare | Charlotte, Hillsborough, Manatee, Pinellas, Volusia, Pasco + more |
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

The dashboard exposes 200+ REST endpoints across these modules:

| Module | Prefix | Key Endpoints |
|--------|--------|---------------|
| `arrests` | `/api/arrests` | Search, filter, export arrests |
| `leads` | `/api/leads` | Lead queries with scoring |
| `defendants` | `/api/defendants` | Defendant search and detail |
| `defendant_lifecycle` | `/api/defendant-notes` | Notes, contact log, DNB/DNC, promote-to-pipeline |
| `prospective_bonds` | `/api/prospective` | Kanban pipeline CRUD, stage transitions |
| `stats` | `/api/stats` | KPIs, county breakdown, scraper health |
| `intake` | `/api/intake` | Intake queue management |
| `contacts` | `/api/contacts` | Contact/indemnitor lookup |
| `bonds` | `/api/bonds` | Active bond management |
| `poa` | `/api/poa` | POA inventory (OSI + Palmetto) |
| `tracking` | `/api/tracking` | GPS/check-in compliance |
| `court_reminders` | `/api/court` | Court date reminders |
| `payments` | `/api/payments` | Payment plan tracking |
| `scraper_control` | `/api/scraper` | Manual triggers, fleet status |
| `bb_*` (6 modules) | `/api/bb/*` | BlueBubbles iMessage bridge |
| `imessage_automation` | `/api/imessage` | AI-powered message automation |
| `agent_brain` | `/api/agent` | AI outreach agent control |

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
│   ├── app.py                 # Quart server (port 5050)
│   ├── extensions.py          # MongoDB connection pool
│   ├── index.html             # 10-tab dashboard UI
│   ├── styles.css             # 1,160+ lines of premium CSS
│   ├── sl-core.js             # Tab system, KPI cards, theme
│   ├── sl-data.js             # Data fetching, caching, SSE
│   ├── sl-features.js         # Lead explorer, filters, export
│   ├── sl-health.js           # Scraper fleet health monitor
│   ├── sl-prospective.js      # Outreach Kanban pipeline
│   ├── sl-defendant-lifecycle.js  # Notes, contact log, lifecycle bridge
│   ├── sl-indemnitor.js       # Indemnitor management
│   ├── sl-inventory.js        # POA inventory modal
│   ├── sl-intake.js           # Intake queue processing
│   ├── sl-active-bonds.js     # Active bonds management
│   ├── sl-tracking.js         # GPS/compliance tracking
│   ├── defendants.js          # Defendant card grid
│   └── api/                   # 30+ REST endpoint modules
│       ├── arrests.py         # Arrest search/filter
│       ├── defendant_lifecycle.py  # Notes + pipeline bridge
│       ├── prospective_bonds.py    # Kanban pipeline
│       ├── bb_prospecting.py  # BlueBubbles iMessage outreach
│       ├── imessage_automation.py  # AI message agent
│       ├── poa.py             # POA inventory management
│       └── ...                # 24 more API modules
├── docs/
│   ├── SCHEMAS.md             # Data model reference
│   ├── COUNTY_REGISTRY.md     # All 67 counties with JMS vendor info
│   ├── agents/                # AI agent specs
│   ├── policies/              # Business rule policies
│   └── runbooks/              # Operational runbooks
├── .agent/skills/             # 16 AI agent skills
├── Dockerfile                 # Python 3.12-slim + Chromium
├── docker-compose.yml         # Scraper + Dashboard + Node-RED
├── AGENTS.md                  # Digital workforce handbook
├── ROADMAP.md                 # 10-phase lifecycle plan
├── DATA_MODEL.md              # Entity relationship reference
└── GEMINI.md                  # AI agent identity & rules
```

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Language** | Python 3.12 | Core runtime (~32K lines) |
| **Scheduling** | APScheduler | Per-county cron with staggered first-runs |
| **Browser** | DrissionPage 4.0+ | Headless Chromium for JS-heavy sites |
| **Stealth Browser** | Patchright | Playwright fork for advanced anti-bot |
| **HTTP** | curl_cffi | TLS fingerprint impersonation |
| **HTTP** | requests + BS4 | Standard scraping + HTML parsing |
| **Database** | MongoDB Atlas (motor) | Primary async storage |
| **Legacy DB** | Google Sheets (gspread) | Optional fallback writer |
| **Alerts** | Slack SDK | Webhook-based real-time notifications |
| **Dashboard** | Quart (async Flask) | 10-tab intelligence dashboard |
| **Frontend** | Vanilla JS + CSS | 17,600 lines, 11 modules, premium design |
| **iMessage** | BlueBubbles API | Office iMac bridge for text outreach |
| **AI** | OpenAI GPT-4o | Lead enrichment, auto-reply agent |
| **Hosting** | Hetzner VPS (Docker) | Production at 178.156.179.237 |
| **Ops** | Node-RED | Scheduling, alerts, data pipeline |
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

See [ROADMAP.md](ROADMAP.md) for full details.

| Phase | Name | Status |
|-------|------|--------|
| 1 | Scrape → Score → Alert | ✅ Complete (50 counties) |
| 2 | Defendant Normalization + Lifecycle | ✅ Complete |
| 3 | Intake Queue Processing | ✅ Complete |
| 4 | Outreach Pipeline + iMessage Bridge | ✅ Complete |
| 5 | Bond Case + Surety + POA Inventory | ✅ Complete |
| 6 | Paperwork Generation | ✅ Complete |
| 7 | Signature Orchestration (SignNow) | ✅ Complete |
| 8 | Payment Collection (SwipeSimple) | ✅ Complete |
| 9 | Contact Discovery (OSINT) | ✅ Complete |
| 10 | Automated Outreach Sequencing | ✅ Complete |

---

## AI Agent Skills

16 agent skills in `.agent/skills/` for automated development workflows:

| Skill | Purpose |
|-------|---------|
| `scraper-builder` | Add a new county scraper step-by-step |
| `scraper-debugger` | Debug broken scrapers with self-healing |
| `lead-scoring-tuning` | Adjust scoring weights and thresholds |
| `contact-discovery` | OSINT family/friend contact finder |
| `county-jms-patterns` | JMS vendor reverse-engineering |
| `docker-ops` | Container management on Hetzner |
| `frontend-design` | Premium UI/UX design system |
| `systematic-debugging` | Root-cause-first debugging |
| `pdf-processing` | Bond paperwork PDF manipulation |
| `programmatic-seo` | County-specific landing pages at scale |
| `git-sync-deploy` | Multi-environment sync workflow |
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
| [`shamrock-telegram-app`](https://github.com/Shamrock2245/shamrock-telegram-app) | Telegram Mini-Apps (Netlify) |
| [`swfl-arrest-scrapers`](https://github.com/Shamrock2245/swfl-arrest-scrapers) | Legacy scrapers (predecessor) |

---

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `MONGODB_URI` | ✅ | MongoDB Atlas connection string |
| `MONGODB_DB_NAME` | ✅ | Database name (`shamrock_leads`) |
| `SLACK_WEBHOOK_ARRESTS` | ✅ | #new-arrests Slack channel |
| `SLACK_WEBHOOK_LEADS` | ✅ | #leads channel (hot leads) |
| `SLACK_WEBHOOK_ERRORS` | ✅ | #scraper-errors channel |
| `BLUEBUBBLES_URL` | Optional | BlueBubbles server URL (iMessage) |
| `BLUEBUBBLES_PASSWORD` | Optional | BlueBubbles API password |
| `OPENAI_API_KEY` | Optional | AI outreach agent |
| `GOOGLE_APPLICATION_CREDENTIALS` | Optional | GCP service account (Sheets) |
| `GOOGLE_SPREADSHEET_ID` | Optional | Legacy Sheets writer |

---

## License

Proprietary — Shamrock Active Software LLC
