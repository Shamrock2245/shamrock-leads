# ShamrockLeads — Florida Arrest Intelligence Platform

> **Scrape. Score. Route. Bond.** — Real-time arrest data across all 67 Florida counties.

[![Docker](https://img.shields.io/badge/Docker-Containerized-blue)](Dockerfile)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-green)](https://python.org)
[![MongoDB Atlas](https://img.shields.io/badge/Database-MongoDB%20Atlas-brightgreen)](https://mongodb.com)
[![Counties](https://img.shields.io/badge/Active%20Scrapers-20-orange)](#county-coverage)
[![License](https://img.shields.io/badge/License-Proprietary-red)](#license)

---

## What Is This?

ShamrockLeads is a **statewide arrest intelligence and bonded-case management platform** that:

1. **Scrapes** real-time booking data from 20+ Florida county jail rosters on scheduled intervals
2. **Normalizes** every record into a standardized 39-column `ArrestRecord` schema
3. **Deduplicates** using `booking_number + county` composite keys (in-memory + MongoDB)
4. **Scores** each arrest with rule-based lead qualification (0–100: Hot / Warm / Cold / Disqualified)
5. **Alerts** bondsmen via Slack with real-time hot lead notifications
6. **Stores** everything in MongoDB Atlas with Google Sheets as a legacy fallback
7. **Visualizes** live arrest data through a Flask-powered Intelligence Dashboard

The long-term vision is a full **arrest-to-bond lifecycle** — from scraping a booking to generating paperwork, collecting signatures via SignNow, and processing payment. See [ROADMAP.md](ROADMAP.md) for the 10-phase plan.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     SCRAPER ENGINE                               │
│                                                                  │
│  20 County Scrapers (Python)                                     │
│  ┌────────────┐  ┌────────────┐  ┌──────────────┐               │
│  │ DrissionPage│  │ curl_cffi  │  │ requests +   │               │
│  │ (Chromium)  │  │ (TLS spoof)│  │ BeautifulSoup│               │
│  └─────┬──────┘  └─────┬──────┘  └──────┬───────┘               │
│        │               │                │                        │
│        └───────────┬────┴────────────────┘                       │
│                    ▼                                             │
│           BaseScraper.run()                                      │
│           ├── scrape()         → county-specific logic            │
│           ├── score()          → LeadScorer (0-100)              │
│           ├── dedup()          → DedupEngine                     │
│           ├── write()          → MongoWriter + SheetsWriter      │
│           └── alert()          → SlackNotifier                   │
└────────────────────┬─────────────────────────────────────────────┘
                     │
              APScheduler
           (per-county intervals)
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
  ┌──────────┐ ┌──────────┐ ┌──────────┐
  │ MongoDB  │ │ Google   │ │  Slack   │
  │ Atlas    │ │ Sheets   │ │ Webhooks │
  │ (primary)│ │ (legacy) │ │ (alerts) │
  └──────────┘ └──────────┘ └──────────┘
        │
        ▼
  ┌──────────────────┐
  │ Flask Dashboard  │
  │ (port 5050)      │
  │ Live arrest view │
  └──────────────────┘
```

---

## Quick Start

```bash
# Clone
git clone git@github.com:Shamrock2245/shamrock-leads.git
cd shamrock-leads

# Configure
cp .env.example .env
# Edit .env with your MongoDB URI, Slack webhooks, etc.

# Run with Docker (production)
docker compose up -d

# Run locally (dev)
pip install -r requirements.txt
python main.py
```

The Dashboard is accessible at `http://localhost:5050` when running via Docker Compose.

---

## County Coverage

**20 active scrapers** across Florida, organized by tier:

### Tier 1 — SWFL Core (7 Counties)
| County | JMS Vendor | Scraper Method | Interval |
|--------|-----------|---------------|----------|
| **Lee** | Odyssey (Tyler) | HTTP API | 20 min |
| **Collier** | Odyssey | curl_cffi (TLS fingerprint) | 30 min |
| **Charlotte** | Custom | DrissionPage | 45 min |
| **Hendry** | JailTracker | HTTP + DrissionPage hybrid | 120 min |
| **DeSoto** | JailTracker | HTTP API | 60 min |
| **Manatee** | New World (Revize) | DrissionPage | 45 min |
| **Sarasota** | Odyssey | HTTP API | 60 min |

### Tier 4 — Central & East FL (6 Counties)
| County | JMS Vendor | Scraper Method | Interval |
|--------|-----------|---------------|----------|
| **Orange** | Custom | HTTP API | 90 min |
| **Pinellas** | JailTracker | DrissionPage | 90 min |
| **Polk** | Odyssey | HTTP API | 120 min |
| **Osceola** | Custom | HTTP API | 120 min |
| **Seminole** | Custom | HTTP API | 90 min |
| **Palm Beach** | Custom | HTTP API | 120 min |

### Tier 5 — Statewide High-Pop (7 Counties)
| County | JMS Vendor | Scraper Method | Interval |
|--------|-----------|---------------|----------|
| **Hillsborough** | New World | DrissionPage (login + reCAPTCHA) | 90 min |
| **Broward** | Custom | Sequential ID probing (HTTP) | 60 min |
| **Duval** | Custom | HTTP API | 90 min |
| **Volusia** | Custom | DrissionPage (Cloudflare) | 90 min |
| **Brevard** | Odyssey | requests + BeautifulSoup | 120 min |
| **Pasco** | Custom | DrissionPage (Cloudflare) | 90 min |
| **Escambia** | Odyssey | requests + BeautifulSoup | 120 min |

> **Goal:** All 67 Florida counties. See [docs/COUNTY_REGISTRY.md](docs/COUNTY_REGISTRY.md) for the full registry.

---

## Scraper Architecture

Every county scraper inherits from `BaseScraper` and uses one of three scraping strategies:

| Strategy | Library | Use Case | Counties |
|----------|---------|----------|----------|
| **Browser Automation** | [DrissionPage](https://github.com/g1879/DrissionPage) | JavaScript-heavy pages, login walls, Cloudflare, reCAPTCHA | Charlotte, Hendry, Hillsborough, Manatee, Pinellas, Volusia, Pasco |
| **TLS Fingerprint HTTP** | [curl_cffi](https://github.com/lexiforest/curl_cffi) | APIs with TLS fingerprint detection (impersonates Chrome) | Collier |
| **Standard HTTP** | [requests](https://github.com/psf/requests) + [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) | Simple HTML pages and REST APIs | Lee, DeSoto, Sarasota, Brevard, Escambia, Orange, Polk, Osceola, Seminole, Palm Beach, Broward, Duval |

Additionally, two **shared base classes** handle common JMS platforms:

| Base Class | JMS Platform | Target Counties |
|-----------|-------------|----------------|
| `P2CBaseScraper` | Police-to-Citizen (CentralSquare) | Clay, Marion, Alachua, Putnam |
| `SmartCOPBaseScraper` | SmartCOP Solutions | Baker, Bradford, Calhoun, Columbia, Franklin + 14 more |
| `GenericAdaptiveScraper` | Auto-detect (HTML tables) | Fallback for unknown platforms |

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
├── main.py                  # Entry point: APScheduler + CLI
├── config/
│   └── settings.py          # Env-based config with feature flags
├── core/
│   ├── models.py            # ArrestRecord (39-column dataclass)
│   ├── dedup.py             # In-memory + MongoDB deduplication
│   └── scheduler.py         # APScheduler with per-county intervals
├── scrapers/
│   ├── base_scraper.py      # Abstract base: scrape → score → write → alert
│   ├── p2c_base.py          # P2C (Police-to-Citizen) platform base
│   ├── smartcop_base.py     # SmartCOP platform base (19 counties)
│   ├── generic_adaptive.py  # Auto-detect scraper for unknown JMS
│   └── counties/            # One file per county (20 active)
│       ├── lee.py           # Reference implementation (Odyssey API)
│       ├── collier.py       # curl_cffi with TLS fingerprinting
│       ├── hillsborough.py  # DrissionPage + login + reCAPTCHA
│       └── ...              # 17 more county scrapers
├── scoring/
│   └── lead_scorer.py       # Rule-based lead qualification (0–100)
├── writers/
│   ├── mongo_writer.py      # MongoDB Atlas upsert (primary)
│   ├── sheets_writer.py     # Google Sheets writer (legacy)
│   └── slack_notifier.py    # Real-time Slack alerts
├── dashboard/
│   ├── app.py               # Flask server (port 5050)
│   ├── index.html           # Desktop dashboard UI
│   ├── mobile.html          # Mobile-optimized view
│   ├── dashboard.js         # Frontend logic
│   ├── defendants.js        # Defendant viewer
│   ├── styles.css           # Dashboard styles
│   └── server.py            # Server-Sent Events for live updates
├── api/                     # Netlify Edge Functions (planned REST API)
├── docs/
│   ├── SCHEMAS.md           # 39-column ArrestRecord + MongoDB collections
│   ├── COUNTY_REGISTRY.md   # All 67 counties with JMS vendor info
│   ├── agents/              # AI agent specs (6 agents)
│   ├── policies/            # Business rule policies
│   ├── specs/               # Data schemas (bond case, surety config)
│   └── runbooks/            # Operational runbooks
├── tests/
├── Dockerfile               # Python 3.12-slim + Chromium
├── docker-compose.yml       # Scraper + Dashboard + Node-RED
├── requirements.txt
├── AGENTS.md                # Digital workforce handbook
├── ROADMAP.md               # 10-phase lifecycle plan
├── SECURITY.md              # Security policies
├── DATA_MODEL.md            # Data model reference
└── GEMINI.md                # AI agent identity & rules
```

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Language** | Python 3.12 | Core runtime |
| **Scheduling** | [APScheduler](https://github.com/agronholm/apscheduler) | Per-county cron with staggered first-runs |
| **Browser Automation** | [DrissionPage](https://github.com/g1879/DrissionPage) 4.0+ | Headless Chromium for JS-heavy/Cloudflare sites |
| **HTTP Client** | [curl_cffi](https://github.com/lexiforest/curl_cffi) | TLS fingerprint impersonation (Chrome) |
| **HTTP Client** | [requests](https://github.com/psf/requests) + [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) | Standard HTTP scraping + HTML parsing |
| **Database** | [MongoDB Atlas](https://mongodb.com) (pymongo) | Primary storage — upsert by booking+county |
| **Legacy DB** | [Google Sheets](https://sheets.google.com) (gspread) | Optional fallback writer |
| **Alerts** | [Slack SDK](https://github.com/slackapi/python-slack-sdk) | Webhook-based real-time notifications |
| **AI / Scoring** | [OpenAI](https://github.com/openai/openai-python) | Lead enrichment (future) |
| **Dashboard** | [Flask](https://github.com/pallets/flask) | Intelligence dashboard (port 5050) |
| **Hosting** | Hetzner VPS (Docker) | Production deployment |
| **Ops Dashboard** | [Node-RED](https://nodered.org) | Scheduling, alerts, data pipeline |
| **API** | Netlify Edge Functions | REST endpoints (planned) |
| **CI/CD** | GitHub Actions | Automated deployments |

---

## Docker Deployment

The `docker-compose.yml` runs three services:

| Service | Container | Port | Purpose |
|---------|-----------|------|---------|
| `shamrock-leads` | `shamrock-leads` | — | Scraper engine + APScheduler |
| `dashboard` | `shamrock-dashboard` | 5050 | Flask intelligence dashboard |
| `node-red` | `shamrock-node-red` | 1880 | Operations dashboard |

```bash
# Build and start all services
docker compose up -d --build

# View scraper logs
docker logs -f shamrock-leads

# View dashboard logs
docker logs -f shamrock-dashboard

# Check health
docker compose ps
```

---

## Roadmap

ShamrockLeads follows a 10-phase lifecycle plan. See [ROADMAP.md](ROADMAP.md) for full details.

| Phase | Name | Status |
|-------|------|--------|
| 1 | Scrape → Score → Alert | ✅ **Complete** |
| 2 | Defendant Normalization | 🔲 Planned |
| 3 | Intake Ingestion | 🔲 Planned |
| 4 | Matching Engine | 🔲 Planned |
| 5 | Bond Case + Surety + POA | 🔲 Planned |
| 6 | Paperwork Generation | 🔲 Planned |
| 7 | Signature Orchestration | 🔲 Planned |
| 8 | Payment Collection | 🔲 Planned |
| 9 | Contact Discovery (OSINT) | 🔲 Planned |
| 10 | Outreach Sequencing | 🔲 Planned |

---

## Related Repositories

| Repo | Purpose |
|------|---------|
| [`shamrock-bail-portal-site`](https://github.com/Shamrock2245/shamrock-bail-portal-site) | Wix Velo portal + GAS backend |
| [`shamrock-node-red`](https://github.com/Shamrock2245/shamrock-node-red) | Ops dashboard + 39 cron jobs |
| [`shamrock-telegram-app`](https://github.com/Shamrock2245/shamrock-telegram-app) | Telegram Mini-Apps (Netlify) |
| [`swfl-arrest-scrapers`](https://github.com/Shamrock2245/swfl-arrest-scrapers) | Legacy scrapers (predecessor to this repo) |

---

## License

Proprietary — Shamrock Active Software LLC
