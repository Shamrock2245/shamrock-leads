# ShamrockLeads — Multi-State Arrest Intelligence + Bond Auto-CRM

> **Scrape. Score. Route. Bond.** — Real-time arrest data and bond lifecycle ops across FL · GA · SC · NC.

[![Docker](https://img.shields.io/badge/Docker-Containerized-blue)](Dockerfile)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-green)](https://python.org)
[![MongoDB Atlas](https://img.shields.io/badge/Database-MongoDB%20Atlas-brightgreen)](https://mongodb.com)
[![Counties](https://img.shields.io/badge/Registered%20Scrapers-198-orange)](#county-coverage)
[![Dashboard](https://img.shields.io/badge/Dashboard-Super%20CRM-blueviolet)](#intelligence-dashboard)
[![License](https://img.shields.io/badge/License-Proprietary-red)](#license)

**True status:** see [`STATUS.md`](./STATUS.md) · Multi-state: [`docs/MULTI_STATE_SCRAPER_ROADMAP.md`](./docs/MULTI_STATE_SCRAPER_ROADMAP.md) · Super CRM: [`docs/SUPER_CRM.md`](./docs/SUPER_CRM.md) · Ecosystem: [`docs/ECOSYSTEM.md`](./docs/ECOSYSTEM.md)

---

## What Is This?

ShamrockLeads is the **bond Auto-CRM and arrest intelligence engine** for [Shamrock Bail Bonds](https://shamrockbailbonds.biz): scrape → score → outreach → intake → match → paperwork → pay → active bond lifecycle.

**Product boundary:** Bail School education is **`shamrock-bail-school`** (separate funnel). This repo does not host the student LMS.

**Strategic goal:** Scale from $3–5M/year (Lee County) to $50M+/year across the **Palmetto surety footprint** (FL, SC, NC, TN, TX, CT, LA, MS) plus **Georgia** adjacent market — with OSI primary in Florida.

### What It Does

1. **Scrapes** real-time booking data from **198 registered county scrapers** (51 FL · 74 GA · 46 SC · 27 NC) on scheduled intervals
2. **Normalizes** every record into a standardized 39-column `ArrestRecord` schema (includes `State`)
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
18. **Tracks** defendant GPS location via Traccar integration (OsmAnd, vehicle trackers)
19. **Visualizes** multi-state ops via Super CRM + **Multi-State Ops** + **Bond Intelligence**
20. **Automates** social media presence across platforms via Postiz integration

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        SCRAPER ENGINE                                │
│                                                                      │
│  90 County Scrapers (Python 3.12)                                    │
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
│  21 Tabs:                                                            │
│  📊 Command Center  │ 🔍 Lead Explorer   │ 👤 Defendants            │
│  📱 Outreach        │ 🏥 Scraper Health  │ 🔒 Active Bonds          │
│  📍 Tracking        │ 📥 Intake Queue    │ 🤝 Indemnitors           │
│  📈 Analytics       │ 🧠 Intelligence    │ ⚖️ Legal NLP             │
│  📅 Calendar        │ 📄 Reports         │ 🌐 Client Portal         │
│  💬 iMessage        │ 💰 Accounting      │ 🎯 Alpha Intel           │
│  🚨 FTA Alerts      │ 📣 Social Media    │ 🔬 Enrichment            │
│                                                                      │
│  66 API modules  │  45 service modules  │  45 frontend JS modules   │
│  ~34,450 LOC (frontend JS+CSS+HTML)  │  ~42,000 LOC (backend)      │
└──────────────┬───────────────────────────────────────────────────────┘
               │
    ┌──────────┼──────────────┐
    ▼          ▼              ▼
┌────────┐ ┌──────────┐ ┌──────────┐
│Traccar │ │BlueBubbles│ │ SignNow  │
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
```

**Dashboard:** `http://localhost:5050` (Docker maps external 8088 → internal 5050)
**Production:** `https://leads.shamrockbailbonds.biz` (Nginx reverse proxy → `178.156.179.237:8088`)

---

## Intelligence Dashboard

A premium **21-tab operations center** with ~24,900 lines of frontend JS and ~9,600 lines of CSS across 45 JS modules:

| Tab | Module | Purpose |
|-----|--------|---------| 
| 📊 **Command Center** | `sl-core.js`, `sl-data.js` | KPI cards, bond-ready queue, county heatmap, re-arrest alerts, compliance tasks |
| 🔍 **Lead Explorer** | `sl-features.js` | Filterable arrest grid, lead scores, export to CSV/Slack |
| 👤 **Defendants** | `defendants.js`, `sl-defendant-lifecycle.js` | Card grid with lifecycle notes, contact log, DNB/DNC, bond finalize |
| 📱 **Outreach** | `sl-prospective.js` | Kanban pipeline (Contacted → Negotiating → Paperwork → Ready), iMessage bridge |
| 🏥 **Scraper Health** | `sl-health.js` | Fleet status, error drill-down, manual triggers, auto-recovery |
| 🔒 **Active Bonds** | `sl-active-bonds.js` | 7-status Kanban (Active → Monitoring → Alert → Exonerated/Forfeited/Surrendered → Reinstated) |
| 📍 **Tracking** | `sl-tracking.js`, `sl-geo-intelligence.js` | GPS/check-in tracking, geofencing, Traccar integration |
| 📥 **Intake Queue** | `sl-intake.js` | Wix/Telegram intake processing, defendant matching |
| 🤝 **Indemnitors** | `sl-indemnitor.js` | Full indemnitor profiles, payment plans, document packets |
| 📋 **POA Inventory** | `sl-inventory.js` | Power of Attorney management (OSI + Palmetto sureties) |
| 📈 **Analytics** | `sl-analytics.js`, `sl-analytics-apex.js` | Revenue sparkline, county treemap, risk heatmap (ApexCharts) |
| 🧠 **Intelligence** | `sl-intelligence.js` | AI-powered insights, lead enrichment, pattern detection |
| ⚖️ **Legal NLP** | `sl-legal-nlp.js` | Charge analysis, statute lookup, NLP classification |
| 📅 **Calendar** | `sl-calendar.js`, `sl-calendar-ext.js` | Court date calendar with Google Calendar sync |
| 📄 **Reports** | `sl-reports.js`, `sl-reports-ui.js` | Liability, commissions, reconciliation reports |
| 🌐 **Client Portal** | `sl-portal.js` | Client-facing portal management |
| 💬 **iMessage** | `sl-imessage.js` | BlueBubbles control center — inbox, health, FindMy, automation |
| 💰 **Accounting** | `sl-accounting.js` | Revenue tracking, commission splits, surety reporting |
| 🎯 **Alpha Intel** | `sl-alpha-intel.js` | Source performance analytics, lead source ROI |
| 🚨 **FTA Alerts** | `sl-fta.js` | Failure-to-appear detection, surrender coordination |
| 📣 **Social Media** | `sl-social.js` | Social media command center, content pipeline |
| 🔬 **Enrichment** | `sl-enrichment.js` | Data enrichment workflows, OSINT integration |

---

## County Coverage

**198 registered scrapers** (dashboard `REGISTERED_COUNTIES`) across **FL · GA · SC · NC**, reusing shared JMS bases:

| State | Registered | Path | Job ID form |
|-------|----------:|------|-------------|
| Florida | 51 | `scrapers/counties/` | `scraper_<county>` (legacy) |
| Georgia | 74 | `scrapers/counties_ga/` | `scraper_ga_<county>` |
| South Carolina | 46 | `scrapers/counties_sc/` | `scraper_sc_<county>` |
| North Carolina | 27 (wave-1) | `scrapers/counties_nc/` | `scraper_nc_<county>` |

**CLI:** `python main.py lee` · `python main.py sc_jasper` · `python main.py nc_mecklenburg`

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
| `KologikBaseScraper` | Kologik Vue roster | FL (reusable) |
| `OdysseyBaseScraper` | Tyler Odyssey family | GA stubs |
| `SocrataBaseScraper` / `XMLFeedBaseScraper` | Open data / XML | GA |

> **Roadmap:** Palmetto states TN → LA/MS → TX → CT after NC depth.  
> Registries: [FL](docs/COUNTY_REGISTRY.md) · [GA](docs/GEORGIA_COUNTY_REGISTRY.md) · [SC](docs/SC_COUNTY_REGISTRY.md) · [NC](docs/NC_COUNTY_REGISTRY.md) · [Multi-state plan](docs/MULTI_STATE_SCRAPER_ROADMAP.md).

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
│   ├── smartcop_base.py       # SmartCOP platform base (19 counties)
│   ├── eas_base.py            # Eagle Advantage Solutions (Georgia)
│   ├── zuercher_base.py       # Zuercher Portal (Georgia)
│   ├── southern_sw_base.py    # Southern Software (Georgia)
│   ├── socrata_base.py        # Socrata Open Data (Georgia)
│   ├── generic_adaptive.py    # Auto-detect scraper for unknown JMS
│   ├── counties/              # Florida county scrapers (52 active)
│   └── counties_ga/           # Georgia county scrapers (108 active)
├── scoring/
│   └── lead_scorer.py         # Rule-based lead qualification (0–100)
├── writers/
│   ├── mongo_writer.py        # MongoDB Atlas upsert (primary)
│   ├── sheets_writer.py       # Google Sheets writer (legacy)
│   └── slack_notifier.py      # Real-time Slack alerts
├── dashboard/                 # Super CRM FastAPI Application
```

---

## License

Proprietary — Shamrock Active Software LLC
*Maintained by: Brendan / Shamrock Active Software LLC | `admin@shamrockbailbonds.biz`*
