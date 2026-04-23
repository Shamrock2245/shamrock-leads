# ROADMAP.md — ShamrockLeads Phase Progression

> **Purpose:** Define what exists vs what is coming. Every agent must check this before acting.

## Phase Overview

| Phase | Name | Status |
|-------|------|--------|
| 1 | Scrape → Score → Alert | ✅ Complete |
| 2 | Defendant Normalization | 🔲 Planned |
| 3 | Intake Ingestion | 🔲 Planned |
| 4 | Matching Engine | 🔲 Planned |
| 5 | Bond Case + Surety + POA | 🔲 Planned |
| 6 | Paperwork Generation | 🔲 Planned |
| 7 | Signature Orchestration | 🔲 Planned |
| 8 | Payment Collection | 🔲 Planned |
| 9 | Contact Discovery (OSINT) | 🔲 Planned |
| 10 | Outreach Sequencing | 🔲 Planned |

## Phase 1: Scrape → Score → Alert ✅ COMPLETE
- 20 county scrapers running on APScheduler
- Self-healing BaseScraper with retry, auto-disable, error classification
- Lead scoring (0-100) with Hot/Warm/Cold/Disqualified
- MongoDB Atlas storage (upsert by County + Booking_Number)
- Real-time Slack alerts for hot leads
- Docker deployment on Hetzner VPS
