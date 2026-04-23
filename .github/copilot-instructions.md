# Copilot Instructions — ShamrockLeads

## What This Repo Is

ShamrockLeads is a statewide arrest intelligence platform for bail bond lead generation. It scrapes Florida county jail rosters, scores leads, and alerts bondsmen.

## Required Reading Before Any Code Change

1. `AGENTS.md` — Agent roles, safety rules, identity model
2. `DATA_MODEL.md` — Entity definitions with phase markers
3. `ROADMAP.md` — What's implemented vs planned

## Key Rules

- **Check phase markers**: Most entities in DATA_MODEL.md are `[PLANNED]`. Don't build against nonexistent schemas.
- **Identity model**: ArrestLead (County + Booking_Number) ≠ Defendant ≠ Indemnitor ≠ Match ≠ BondCase
- **Dedup key**: `County + Booking_Number` for arrest records
- **Two sureties**: OSI (O'Shaughnahill) and Palmetto. Every bond case specifies which.
- **POA numbers are physical assets**: They come from inventory receipts, not auto-generated.
- **No paperwork before validated match**: The chain is law.
- **BaseScraper pattern**: All county scrapers inherit from `BaseScraper`. The `run()` method handles scoring, writing, and alerting.
- **Self-healing**: BaseScraper retries 3x, classifies errors, auto-disables after 5 failures.

## Tech Stack

- Python 3.12
- MongoDB Atlas (primary database)
- APScheduler (scraper scheduling)
- Slack webhooks (alerts)
- Docker (deployment on Hetzner VPS)
- Flask (dashboard)

## Code Patterns

- Models in `core/models.py` (dataclasses)
- Config in `config/settings.py` (env-based)
- One scraper per county in `scrapers/counties/`
- Writers in `writers/` (mongo, sheets, slack)
- Scoring in `scoring/lead_scorer.py`
- Tests in `tests/`

## Don't

- Don't log PII to console or Slack
- Don't hardcode secrets
- Don't skip dedup checks
- Don't create BondCase/Packet/Payment entities yet (Phase 5+)
- Don't collapse identity boundaries (lead ≠ defendant ≠ case)
