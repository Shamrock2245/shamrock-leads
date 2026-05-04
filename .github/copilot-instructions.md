# Copilot Instructions — ShamrockLeads Intelligence Platform

> **Read `BRAND.md` before this file.** It defines who we are and what we're building.

---

## What This Repository Is

`shamrock-leads` is the core intelligence engine for **Shamrock Bail Bonds** — a Florida bail bond agency building the most automated, data-driven bond operation in the state. This repository contains:

- **49 county jail scrapers** — Python scrapers covering 49 of 67 Florida counties, running every 10–120 minutes on a Hetzner VPS
- **Lead scoring engine** — 0–100 scoring on every arrest record before a human sees it
- **13-tab intelligence dashboard** — Quart/async Python API + 17,600-line Vanilla JS frontend
- **Full bond lifecycle** — From arrest scrape to signed paperwork to payment collection
- **iMessage automation** — BlueBubbles bridge for human-feel outreach via the office iMac

---

## Required Reading Before Any Code Change

Read these files **in order** before touching any code:

1. `BRAND.md` — Identity, vision, design standards, non-negotiables
2. `AGENTS.md` — Agent roles, safety rules, scoring system, prime directives
3. `DATA_MODEL.md` — Entity definitions with phase markers (`[IMPLEMENTED]` vs `[PLANNED]`)
4. `ROADMAP.md` — What is built vs. what is planned

---

## Identity & Brand Rules (Absolute)

- **GitHub:** All work goes to `Shamrock2245` account only
- **Email:** `admin@shamrockbailbonds.biz` for all integrations
- **No WTF:** Never reference, access, or commit to any non-Shamrock repository or identity
- **Sureties:** OSI (`osi`) and Palmetto (`palmetto`) only. Every bond specifies which.

---

## Key Rules

- **Check phase markers**: Most entities in `DATA_MODEL.md` are `[PLANNED]`. Do not build against nonexistent schemas.
- **Identity model**: `ArrestLead` ≠ `Defendant` ≠ `Indemnitor` ≠ `Match` ≠ `BondCase`. These are separate entities with separate MongoDB collections.
- **Dedup key**: `County + Booking_Number` for all arrest records. Always check before insert.
- **Two sureties**: OSI and Palmetto. Every bond case specifies which. Never assume.
- **POA numbers are physical assets**: They come from inventory receipts, not auto-generated.
- **No paperwork before validated match**: The chain is law. See `DATA_MODEL.md`.
- **BaseScraper pattern**: All county scrapers inherit from `BaseScraper`. The `run()` method handles scoring, writing, and alerting.
- **Self-healing**: `BaseScraper` retries 3x, classifies errors, auto-disables after 5 consecutive failures.
- **Audit everything**: Every state change must create an `AuditEvent` in the `audit_events` collection.

---

## Design Standards

The dashboard is a **premium, Fortune 50-level intelligence platform**. All UI work must meet this bar:

- Dark theme: `#0f172a` background, `#10b981` Shamrock green accent
- CSS design tokens: `--accent`, `--bg-card`, `--text-primary`, etc. Never hardcode colors.
- Subtle animations: count-up counters, 2px hover lift, smooth transitions
- Mobile-first: PWA-ready, touch targets >= 44px
- Competitor benchmark: Captira and Bail Books are the floor, not the ceiling

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12 |
| Scheduling | APScheduler |
| Database | MongoDB Atlas (motor, async) |
| Dashboard API | Quart (async Flask) |
| Frontend | Vanilla JS + CSS |
| iMessage | BlueBubbles API |
| AI | OpenAI GPT-4o |
| Signatures | SignNow API |
| Payments | SwipeSimple |
| Alerts | Slack SDK |
| Hosting | Hetzner VPS (Docker Compose) |
| CI/CD | GitHub Actions |

---

## Code Patterns

- Models in `core/models.py` (dataclasses, 39-field `ArrestRecord`)
- Config in `config/settings.py` (env-based, no hardcoded secrets)
- One scraper per county in `scrapers/counties/`
- Writers in `writers/` (mongo, sheets, slack)
- Scoring in `scoring/lead_scorer.py`
- Dashboard API blueprints in `dashboard/api/`
- Dashboard services in `dashboard/services/`
- Tests in `tests/`

---

## Compliance Standards

We are building toward **SOC II Type II** compliance:

- PII never logged to Slack or console in production
- All secrets via environment variables only
- Immutable audit trail in `audit_events` collection
- Webhook authentication via `WIX_WEBHOOK_SECRET`
- MongoDB Atlas encryption at rest

---

## Agent Skills Available

Use these skills to accelerate work and maintain consistency:

| Skill | Path | Use When |
|-------|------|----------|
| `scraper-builder` | `.agent/skills/scraper-builder/` | Adding a new county scraper |
| `scraper-debugger` | `.agent/skills/scraper-debugger/` | Debugging a broken scraper |
| `lead-scoring-tuning` | `.agent/skills/lead-scoring-tuning/` | Adjusting scoring weights |
| `contact-discovery` | `.agent/skills/contact-discovery/` | OSINT family/friend lookup |
| `docker-ops` | `.agent/skills/docker-ops/` | Container management on Hetzner |
| `frontend-design` | `.agent/skills/frontend-design/` | Dashboard UI/UX work |
| `bluebubbles-integration` | `.agent/skills/bluebubbles-integration/` | iMessage automation |
| `systematic-debugging` | `.agent/skills/systematic-debugging/` | Root-cause-first debugging |
| `git-sync-deploy` | `.agent/skills/git-sync-deploy/` | Deploy to Hetzner VPS |
| `gws-shared` | `.agent/skills/gws-shared/` | Google Workspace CLI |
| `mongodb-query-optimizer` | `.agent/skills/mongodb-query-optimizer/` | Query optimization |

---

## Never Do

- Never log PII (phone numbers, SSNs, addresses) to console or Slack
- Never hardcode secrets or API keys
- Never skip the dedup check before writing an arrest record
- Never create a `BondCase`, `DocumentPacket`, or `PaymentRequest` without a validated `Match`
- Never collapse identity boundaries (lead != defendant != indemnitor != case)
- Never commit to or reference non-Shamrock repositories or identities
- Never build against a `[PLANNED]` schema in `DATA_MODEL.md`
- Never send outreach messages without human approval
- Never use a POA from the wrong surety's inventory

---

## Deployment Quick Reference

```bash
# Dashboard only (fast rebuild)
docker compose build --no-cache dashboard && docker compose up -d dashboard

# Verify health
docker logs shamrock-dashboard --tail 20
curl -s http://localhost:5050/health

# Full deploy from local to Hetzner
ssh root@178.156.179.237
cd /opt/shamrock-leads
git pull origin main
docker compose build --no-cache dashboard
docker compose up -d dashboard
```

---

*Maintained by: Brendan / Shamrock Active Software LLC | `admin@shamrockbailbonds.biz`*
