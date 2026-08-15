# ShamrockLeads — Multi-State Arrest Intelligence + Bond Auto-CRM

> **Scrape. Score. Route. Bond.** — Real-time arrest data and bond lifecycle ops across FL · GA · SC · NC · TN · TX · LA · AL · CT · MS.

[![Docker](https://img.shields.io/badge/Docker-Containerized-blue)](Dockerfile)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-green)](https://python.org)
[![MongoDB Atlas](https://img.shields.io/badge/Database-MongoDB%20Atlas-brightgreen)](https://mongodb.com)
[![Counties](https://img.shields.io/badge/Registered%20Scrapers-358-orange)](#county-coverage)
[![States](https://img.shields.io/badge/States-10-blue)](#county-coverage)
[![Dashboard](https://img.shields.io/badge/Dashboard-Super%20CRM-blueviolet)](#intelligence-dashboard)
[![License](https://img.shields.io/badge/License-Proprietary-red)](#license)

**True status:** see [`STATUS.md`](./STATUS.md) · Multi-state: [`docs/MULTI_STATE_SCRAPER_ROADMAP.md`](./docs/MULTI_STATE_SCRAPER_ROADMAP.md) · Super CRM: [`docs/SUPER_CRM.md`](./docs/SUPER_CRM.md) · Ecosystem: [`docs/ECOSYSTEM.md`](./docs/ECOSYSTEM.md)

---

## What Is This?

ShamrockLeads is the **bond Auto-CRM and arrest intelligence engine** for [Shamrock Bail Bonds](https://shamrockbailbonds.biz): scrape → score → outreach → intake → match → paperwork → pay → active bond lifecycle.

**Product boundary:** Bail School education is **`shamrock-bail-school`** (separate funnel). This repo does not host the student LMS.

**Strategic goal:** Scale from $3–5M/year (Lee County) to $50M+/year across the **Palmetto licensed footprint** (FL, SC, NC, TN, TX, CT, LA, MS), with **OSI primary in Florida**. Georgia and Alabama remain adjacent repository coverage markets; their scraper presence does not assert an active surety-writing license.

### What It Does

1. **Scrapes** real-time booking data from **358 registered county scrapers** across 10 states (GA 85 · FL 67 · NC 60 · SC 46 · TX 34 · TN 22 · AL 16 · LA 13 · MS 9 · CT 6) on scheduled intervals
2. **Normalizes** every record into a standardized 39-column `ArrestRecord` schema (includes `State`)
3. **Deduplicates** using `booking_number + county` composite keys (in-memory + MongoDB)
4. **Scores** each arrest with rule-based lead qualification (0–100: Hot / Warm / Cold / Disqualified)
5. **Alerts** bondsmen via Slack with real-time hot lead notifications
6. **Stores** everything in MongoDB Atlas (`ShamrockBailDB`)
7. **Automates First Appearance Bond Filling**: Continuous 24/7 background worker (`FirstAppearanceWatcher`) re-checks unset/$0 bond records across target active counties (Lee, Collier, Charlotte, Sarasota, Manatee, Hendry, DeSoto) every 30 mins to auto-populate newly set bonds post-hearing
8. **Supports Per-Charge Bond Breakdown**: Stores structured `charge_details` (`[{"charge": "...", "bond_amount": 1500, "bond_type": "Surety"}, ...]`) with interactive UI modal editing and `POST /api/leads/update-charge-bonds` auto-rescoring
9. **Powers Multi-State Query Engine**: Seamless state and county sorting/filtering across all 10 states with case-insensitive regex matching and dynamic state-scoped county selector
10. **Manages** defendants (notes, contact logs, DNB/DNC flags, lifecycle tracking)
11. **Matches** indemnitor intake to defendants via confidence-scored matching engine
12. **Creates** bonded cases with surety selection (OSI / Palmetto) and POA assignment
13. **Generates** surety-specific 14-document paperwork packets via DocuSeal (`https://sign.shamrockbailbonds.biz`)
14. **Orchestrates** e-signatures with webhook-driven completion tracking and automatic Google Drive archiving (`<LastName>_<MMDDYY>_<SURETY>.pdf`)
15. **Collects** premium payments via SwipeSimple integration
16. **Manages** the 7-status active bond lifecycle via drag-and-drop Kanban
17. **Automates** iMessage outreach via BlueBubbles bridge to the office iMac
18. **Detects** re-arrests of defendants on active bonds
19. **Monitors** Gmail for court discharge/exoneration emails
20. **Syncs** court dates to Google Calendar with Twilio SMS reminders
21. **Tracks** defendant GPS location via Traccar integration (OsmAnd, vehicle trackers)
22. **Visualizes** multi-state ops via Super CRM + **Multi-State Ops** + **Bond Intelligence**
23. **Generates** official surety bond & liability XLSX financial reports with 2012+ date ranges and auto-chronological sorting
24. **Automates** social media presence across platforms via Postiz integration
25. **Monitors** 284 live Florida DOT (FL511) traffic cameras via Fast-ALPR with interactive 2.5s live snapshot stream viewing and automated watchlist matching across SWFL and major highway corridors
26. **Executes** multi-engine OSINT intelligence scans (Maigret, Sherlock, Blackbird, SpiderFoot 4.0, Ignorant, Toutatis, Instaloader, ExifTool with GPS reverse geocoding)
27. **Enforces** God-Admin (PIN 224545) and Sub-Agent role-based authentication with FL License # tracking and full POA Inventory bulk management

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        SCRAPER ENGINE                                │
│                                                                      │
│  358 County Scrapers across 10 States (Python 3.12)                   │
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
│              INTELLIGENCE DASHBOARD (FastAPI, port 5050)             │
│                                                                      │
│  24 Operations Tabs:                                                 │
│  📊 Command Center  │ 🔍 Lead Explorer   │ 🗺️ Multi-State Ops       │
│  ⚡ Bond Intelligence│ 👤 Defendants      │ 📱 Outreach (Kanban)     │
│  🏥 Scraper Health  │ 🔒 Active Bonds    │ 📍 Tracking (GPS)        │
│  📥 Intake Queue    │ 🤝 Indemnitors     │ 📋 POA Inventory         │
│  📈 Analytics       │ 🧠 Intelligence    │ ⚖️ Legal NLP             │
│  📅 Calendar        │ 📄 Reports         │ 🌐 Client Portal         │
│  💬 iMessage        │ 💰 Accounting      │ 🎯 Alpha Intel           │
│  🚨 FTA Alerts      │ 📣 Social Media    │ 🔬 Enrichment            │
│  🧹 Data Retention                                                   │
│                                                                      │
│  66 API modules  │  45 service modules  │  45 frontend JS modules   │
│  ~35,000 LOC (frontend JS+CSS+HTML)  │  ~43,000 LOC (backend)      │
└──────────────┬───────────────────────────────────────────────────────┘
               │
    ┌──────────┼──────────────┐
    ▼          ▼              ▼
┌────────┐ ┌──────────┐ ┌──────────┐
│Traccar │ │BlueBubbles│ │ DocuSeal │
│GPS     │ │ iMessage  │ │ E-Sign   │
│Tracking│ │ Bridge    │ │ Packets  │
└────────┘ └──────────┘ └──────────┘
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
python main.py ga_fulton
python main.py sc_charleston
python main.py nc_mecklenburg
python main.py tx_bexar
```

**Dashboard:** `http://localhost:5050` (Docker maps external 8088 → internal 5050)  
**Production:** `https://leads.shamrockbailbonds.biz` (Nginx reverse proxy → `178.156.179.237:8088`)

---

## Intelligence Dashboard

A premium **24-tab operations center** with ~25,700 lines of frontend JS and ~10,200 lines of CSS across 45 JS modules:

| Tab | Module | Purpose |
|-----|--------|---------| 
| 📊 **Command Center** | `sl-core.js`, `sl-data.js` | KPI cards, bond-ready queue, county heatmap, re-arrest alerts, compliance tasks |
| 🔍 **Lead Explorer** | `sl-features.js` | Filterable arrest grid, lead scores, state/county filters, live sort, export CSV/Slack |
| 🗺️ **Multi-State Ops** | `sl-multi-state.js` | 10-state coverage radar, registered scrapers registry, live feed, state KPIs |
| ⚡ **Bond Intelligence** | `sl-bond-intelligence.js` | Multi-state bond portfolio analytics, risk tiering, regional performance |
| 👤 **Defendants** | `defendants.js`, `sl-defendant-lifecycle.js` | Card grid with lifecycle notes, contact log, DNB/DNC, bond finalize |
| 📱 **Outreach** | `sl-prospective.js` | Kanban pipeline (Contacted → Negotiating → Paperwork → Ready), iMessage bridge |
| 🏥 **Scraper Health** | `sl-health.js` | Fleet status across 358 registered scrapers, source-contract posture, error drill-down, manual triggers, auto-recovery |
| 🔒 **Active Bonds** | `sl-active-bonds.js` | 7-status Kanban (Active → Monitoring → Alert → Exonerated/Forfeited/Surrendered → Reinstated) |
| 📍 **Tracking** | `sl-tracking.js`, `sl-geo-intelligence.js` | GPS/check-in tracking, geofencing, Traccar integration |
| 📥 **Intake Queue** | `sl-intake.js` | Wix/Telegram intake processing, defendant matching |
| 🤝 **Indemnitors** | `sl-indemnitor.js` | Full indemnitor profiles, payment plans, document packets |
| 📋 **POA Inventory** | `sl-inventory.js` | Power of Attorney management (OSI + Palmetto sureties) |
| 📈 **Analytics** | `sl-analytics.js`, `sl-analytics-apex.js` | Revenue sparkline, county treemap, risk heatmap (ApexCharts) |
| 🧠 **Intelligence** | `sl-intelligence.js` | AI-powered insights, lead enrichment, pattern detection |
| ⚖️ **Legal NLP** | `sl-legal-nlp.js` | Charge analysis, statute lookup, NLP classification |
| 📅 **Calendar** | `sl-calendar.js`, `sl-calendar-ext.js` | Court date calendar with Google Calendar sync |
| 📄 **Reports** | `sl-reports.js`, `sl-reports-ui.js` | Surety financial liability, 2012+ date ranges, chronological auto-sort |
| 🌐 **Client Portal** | `sl-portal.js` | Client-facing portal management |
| 💬 **iMessage** | `sl-imessage.js` | BlueBubbles control center — inbox, health, FindMy, automation |
| 💰 **Accounting** | `sl-accounting.js` | Revenue tracking, commission splits, surety reporting |
| 🎯 **Alpha Intel** | `sl-alpha-intel.js` | Source performance analytics, lead source ROI |
| 🚨 **FTA Alerts** | `sl-fta.js` | Failure-to-appear detection, surrender coordination |
| 📣 **Social Media** | `sl-social.js` | Social media command center, Postiz integration |
| 🧹 **Data Retention** | `sl-retention.js` | Tiered purge policies for M0 512MB limits |

---

## County Coverage

**358 registered scrapers** (dashboard `REGISTERED_COUNTIES` in `dashboard/extensions.py`) across **10 states**, utilizing shared platform bases. Registry coverage is not proof that a county source is currently record-emitting; see [`docs/recon/COUNTY_SOURCE_CONTRACT_MATRIX.md`](./docs/recon/COUNTY_SOURCE_CONTRACT_MATRIX.md) for the complete, evidence-bound 947-scope reconnaissance matrix (942 Census county-equivalents plus five registered non-county scopes):

| State | Registered | Path | Job ID form | CLI command |
|-------|----------:|------|-------------|-------------|
| **Georgia** | 85 | `scrapers/counties_ga/` | `scraper_ga_<county>` | `python main.py ga_fulton` |
| **Florida** | 67 | `scrapers/counties/` | `scraper_<county>` (legacy) | `python main.py lee` |
| **South Carolina** | 46 | `scrapers/counties_sc/` | `scraper_sc_<county>` | `python main.py sc_charleston` |
| **North Carolina** | 60 | `scrapers/counties_nc/` | `scraper_nc_<county>` | `python main.py nc_mecklenburg` |
| **Texas** | 34 | `scrapers/counties_tx/` | `scraper_tx_<county>` | `python main.py tx_bexar` |
| **Tennessee** | 22 | `scrapers/counties_tn/` | `scraper_tn_<county>` | `python main.py tn_davidson` |
| **Louisiana** | 13 | `scrapers/counties_la/` | `scraper_la_<parish>` | `python main.py la_orleans` |
| **Alabama** | 16 | `scrapers/counties_al/` | `scraper_al_<county>` | `python main.py al_jefferson` |
| **Connecticut** | 6 | `scrapers/counties_ct/` | `scraper_ct_*` | `python main.py ct_doc` |
| **Mississippi** | 9 | `scrapers/counties_ms/` | `scraper_ms_<county>` | `python main.py ms_hinds` |
| **Total** | **358** | `dashboard/extensions.py` | Labels: `County (ST)` | |

### Shared Base Classes

| Base Class | JMS Platform | Used in |
|-----------|-------------|---------|
| `EASBaseScraper` | Eagle Advantage Solutions | GA rural batch |
| `P2CBaseScraper` | Police-to-Citizen (CentralSquare) | FL, GA, SC, NC |
| `SmartCOPBaseScraper` | SmartCOP Solutions | FL, GA, SC |
| `ZuercherBaseScraper` | Zuercher Technologies | GA, SC, NC |
| `SouthernSWBaseScraper` | Southern Software | GA, SC, NC |
| `JailTrackerBaseScraper` | JailTracker | FL, SC, GA |
| `NewWorldBaseScraper` | New World InmateInquiry | FL, GA, SC |
| `DCNBaseScraper` | DevExpress DCN | NC |
| `OCVInmatesBaseScraper` | OCV S3 inmates.json | NC, TN |
| `KologikBaseScraper` | Kologik Vue roster | FL (reusable) |
| `OdysseyBaseScraper` | Tyler Odyssey family | GA, TX |

> Registries & Docs: [FL](docs/COUNTY_REGISTRY.md) · [GA](docs/GEORGIA_COUNTY_REGISTRY.md) · [SC](docs/SC_COUNTY_REGISTRY.md) · [NC](docs/NC_COUNTY_REGISTRY.md) · [TX](docs/TX_COUNTY_REGISTRY.md) · [Multi-state roadmap](docs/MULTI_STATE_SCRAPER_ROADMAP.md).

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
│   ├── models.py              # ArrestRecord (39-column dataclass) + SuretyConfig
│   ├── dedup.py               # In-memory + MongoDB deduplication
│   └── scheduler.py           # APScheduler with per-county intervals
├── scrapers/
│   ├── base_scraper.py        # Abstract base: scrape → score → write → alert
│   ├── p2c_base.py            # P2C (Police-to-Citizen) platform base
│   ├── smartcop_base.py       # SmartCOP platform base
│   ├── eas_base.py            # Eagle Advantage Solutions (Georgia)
│   ├── zuercher_base.py       # Zuercher Portal (Georgia/SC/NC)
│   ├── southern_sw_base.py    # Southern Software (Georgia/SC/NC)
│   ├── socrata_base.py        # Socrata Open Data (Georgia)
│   ├── generic_adaptive.py    # Auto-detect scraper for unknown JMS
│   ├── counties/              # Florida county scrapers (67 registered)
│   ├── counties_ga/           # Georgia county scrapers (85 registered)
│   ├── counties_sc/           # South Carolina county scrapers (46 registered)
│   ├── counties_nc/           # North Carolina county scrapers (60 registered)
│   ├── counties_tx/           # Texas county scrapers (34 registered)
│   ├── counties_tn/           # Tennessee county scrapers (22 registered)
│   ├── counties_la/           # Louisiana parish scrapers (13 registered)
│   ├── counties_al/           # Alabama county scrapers (16 registered)
│   ├── counties_ct/           # Connecticut scrapers (6 registered)
│   └── counties_ms/           # Mississippi county scrapers (9 registered)
├── scoring/
│   └── lead_scorer.py         # Rule-based lead qualification (0–100)
├── writers/
│   ├── mongo_writer.py        # MongoDB Atlas upsert (primary)
│   ├── sheets_writer.py       # Google Sheets writer (legacy)
│   └── slack_notifier.py      # Real-time Slack alerts
├── dashboard/                 # Super CRM FastAPI Application
└── tests/                     # 222+ Automated Unit Tests
```

---

## License

Proprietary — Shamrock Active Software LLC  
*Maintained by: Brendan / Shamrock Active Software LLC | `admin@shamrockbailbonds.biz`*
