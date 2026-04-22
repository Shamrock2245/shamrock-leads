# ShamrockLeads — Florida Arrest Intelligence Platform

> **Scrape. Score. Route.** — Real-time arrest data across all 67 Florida counties.

[![Docker](https://img.shields.io/badge/Docker-Containerized-blue)](Dockerfile)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-green)](https://python.org)
[![MongoDB Atlas](https://img.shields.io/badge/Database-MongoDB%20Atlas-brightgreen)](https://mongodb.com)

## What Is This?

ShamrockLeads is a standalone arrest scraping and lead qualification engine that:

1. **Scrapes** real-time booking data from all 67 Florida county jail rosters
2. **Deduplicates** records using `booking_number + county` composite keys
3. **Scores** each arrest with AI-powered lead qualification (0-100)
4. **Routes** qualified leads to subscribed bail bond agencies
5. **Stores** everything in MongoDB Atlas for instant querying

## Architecture

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  SCRAPER      │   │  LEAD         │   │  DATA         │
│  ENGINE       │   │  ENGINE       │   │  LAYER        │
│              │   │              │   │              │
│  67 County   │──▶│  AI Scoring  │──▶│  MongoDB     │
│  Scrapers    │   │  Dedup       │   │  Atlas       │
│  (Python)    │   │  Routing     │   │              │
└──────────────┘   └──────────────┘   └──────────────┘
       │                                      │
       ▼                                      ▼
  APScheduler                          Slack / API
  (in-process)                         Alerts
```

## Quick Start

```bash
# Clone
git clone git@github.com:Shamrock2245/shamrock-leads.git
cd shamrock-leads

# Configure
cp .env.example .env
# Edit .env with your MongoDB URI, API keys, etc.

# Run with Docker
docker compose up -d

# Run locally (dev)
pip install -r requirements.txt
python main.py
```

## Project Structure

```
shamrock-leads/
├── main.py                  # Entry point + APScheduler
├── config/
│   ├── settings.py          # Centralized configuration
│   └── counties.json        # 67-county registry
├── core/
│   ├── scheduler.py         # APScheduler job management
│   ├── dedup.py             # Deduplication engine
│   └── models.py            # ArrestRecord + Lead models
├── scrapers/
│   ├── base_scraper.py      # Abstract scraper interface
│   └── counties/            # One file per county
│       ├── lee.py
│       ├── charlotte.py
│       └── ...
├── scoring/
│   └── lead_scorer.py       # Lead qualification engine
├── writers/
│   ├── mongo_writer.py      # MongoDB Atlas writer (primary)
│   └── sheets_writer.py     # Google Sheets writer (optional/legacy)
├── api/                     # Future: Netlify API endpoints
├── tests/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## Migration from swfl-arrest-scrapers

This repo consolidates:
- `swfl-arrest-scrapers/python_scrapers/` → `scrapers/`
- `swfl-arrest-scrapers/python_scrapers/writers/` → `writers/`
- `swfl-arrest-scrapers/python_scrapers/scoring/` → `scoring/`
- `shamrock-bail-portal-site/backend-gas/ArrestScraper_LeeCounty.js` → `scrapers/counties/lee.py`

## License

Proprietary — Shamrock Active Software LLC
