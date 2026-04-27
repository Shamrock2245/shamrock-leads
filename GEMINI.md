# ShamrockLeads — Agent Identity & Rules

> **Repo:** `Shamrock2245/shamrock-leads`
> **Mission:** Statewide arrest intelligence → Lead scoring → Contact discovery → Bail bond sales

---

## 🚀 What Is This

ShamrockLeads is a **multi-tenant arrest intelligence SaaS** that scrapes all 67 Florida county jails, scores every arrestee as a bail bond lead, discovers contact information for their family/friends, and surfaces hot leads to bondsmen in real-time.

## 🏗 Repository Structure

```
shamrock-leads/
├── main.py                 # Entry point: APScheduler + CLI
├── config/settings.py      # Env-based config with feature flags
├── core/
│   ├── models.py           # ArrestRecord (39-column schema)
│   ├── dedup.py            # In-memory + MongoDB deduplication
│   └── scheduler.py        # APScheduler with per-county configs
├── scrapers/
│   ├── base_scraper.py     # Abstract base: scrape → score → write → alert
│   └── counties/           # One file per county (67 total)
│       └── lee.py          # Reference implementation (Odyssey API)
├── scoring/
│   └── lead_scorer.py      # 0-100 scoring with Hot/Warm/Cold/Disqualified
├── writers/
│   ├── mongo_writer.py     # Upsert to MongoDB Atlas
│   ├── sheets_writer.py    # Legacy Google Sheets writer
│   └── slack_notifier.py   # Real-time Slack alerts
├── discovery/              # Phase 4: Contact finder (OSINT)
├── api/                    # Netlify Edge Functions (REST API)
├── docs/
│   ├── SCHEMAS.md          # Data model reference
│   └── COUNTY_REGISTRY.md  # All 67 counties with JMS vendor info
├── tests/
├── docker-compose.yml      # shamrock-leads + node-red
├── Dockerfile
├── AGENTS.md               # Digital workforce handbook
└── .agent/
    ├── skills/             # AI agent skills
    └── workflows/          # Step-by-step procedures
```

## 📜 Prime Directives

1. **Scrape Respectfully** — Rate-limit requests. Rotate user agents. Never DDoS a county server.
2. **Idempotent Writes** — `Booking_Number + County` is the dedup key. Always check before insert.
3. **Score Everything** — No record enters the DB without a lead score.
4. **Fail Loudly** — Every scraper error fires a Slack alert. Silent failures are unacceptable.
5. **Human-in-the-Loop for Outreach** — No automated client contact without human approval.
6. **PII is Sacred** — Never log phone numbers, SSNs, or addresses to Slack or console in production.
7. **BaseScraper Pattern** — Every county scraper inherits from `BaseScraper`. The `run()` method handles scoring, writing, and alerting automatically.
8. **Docker-First** — All code runs in Docker containers on Hetzner. Never require manual server setup.

## 🛠 Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12 |
| Scheduling | APScheduler |
| Database | MongoDB Atlas |
| Legacy DB | Google Sheets (gspread) |
| Alerts | Slack (webhook blocks) |
| Scoring | Rule-based (0-100), OpenAI enrichment (future) |
| Hosting | Hetzner VPS (Docker) |
| Dashboard | Flask (internal 5050, external 8088 on VPS) |
| Ops Dashboard | Node-RED (port 1880) |
| API | Netlify Edge Functions |
| Frontend | React/Vite SPA on Netlify |
| CI/CD | GitHub Actions |

## 🔍 Key Skills

| Skill | Purpose | Path |
|-------|---------|------|
| `scraper-builder` | Add a new county scraper | `.agent/skills/scraper-builder/` |
| `scraper-debugger` | Debug a broken scraper | `.agent/skills/scraper-debugger/` |
| `lead-scoring-tuning` | Adjust scoring weights | `.agent/skills/lead-scoring-tuning/` |
| `contact-discovery` | Find family/friend contacts | `.agent/skills/contact-discovery/` |
| `county-jms-patterns` | JMS vendor reverse-engineering | `.agent/skills/county-jms-patterns/` |
| `docker-ops` | Container management on Hetzner | `.agent/skills/docker-ops/` |
| `systematic-debugging` | Root-cause-first debugging | `.agent/skills/systematic-debugging/` |
| `self-improving-agent` | Session learning & skill creation | `.agent/skills/self-improving-agent/` |

## 🔄 Key Workflows

| Workflow | Trigger |
|----------|---------|
| `add-county-scraper` | "Add [county] scraper" |
| `deploy-to-hetzner` | "Deploy", "push to production" |
| `debug-scraper-failure` | Slack error alert, stale data |

## 📊 Key Reference Docs

- `AGENTS.md` — Digital workforce roles & scoring rules
- `docs/SCHEMAS.md` — 39-column ArrestRecord, MongoDB collections
- `docs/COUNTY_REGISTRY.md` — All 67 counties with JMS vendors
