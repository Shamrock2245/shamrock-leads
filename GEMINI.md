# GEMINI.md — ShamrockLeads Intelligence Platform

> This file configures Gemini (Google AI Studio) and other AI coding assistants.
> **Read `BRAND.md` first.** It defines who we are and what we're building.

---

## What This Is

ShamrockLeads is the core intelligence engine for **Shamrock Bail Bonds** — a Florida bail bond agency
automating the full bond lifecycle from arrest scrape to signed paperwork to payment collection.

**Strategic goal:** Scale from $3-5M/year (Lee County) to $20-50M/year (67 counties statewide).

---

## Identity & Brand (Non-Negotiable)

- **GitHub:** `Shamrock2245` only. Never reference WTF or other non-Shamrock identities.
- **Email:** `admin@shamrockbailbonds.biz` for all integrations.
- **Sureties:** OSI (`osi`) and Palmetto (`palmetto`). Every bond specifies which.
- **Production VPS:** `178.156.179.237` (Hetzner, root access)
- **Dashboard URL:** `http://178.156.179.237:8088/`

---

## Architecture

```
Hetzner VPS (Docker Compose)
  shamrock-leads     → Python 3.12 scraper engine + APScheduler
  shamrock-dashboard → Quart async API (port 5050 → external 8088)
  node-red           → Ops dashboard (port 1880)

MongoDB Atlas        → Primary database (all entities)
BlueBubbles          → iMessage bridge (office iMac, port 1234)
SignNow              → E-signature orchestration
SwipeSimple          → Bond premium payment collection
Slack                → Real-time operational alerts
Google Workspace     → GAS write-bond forwarding, Drive case files
```

---

## The Data Chain (The Law)

```
ArrestLead -> Defendant -> Indemnitor -> Match(validated) ->
  BondCase(Surety + POA + Case#) -> DocumentPacket -> Signature -> Payment
```

**Dedup key:** `County + Booking_Number`. Always check before insert.
**Identity rule:** ArrestLead != Defendant != Indemnitor != Match != BondCase.

---

## Prime Directives

1. **Scrape Respectfully** — Rate-limit. Rotate user agents. Never DDoS a county server.
2. **Idempotent Writes** — `Booking_Number + County` is the dedup key. Always check before insert.
3. **Score Everything** — No record enters the DB without a lead score.
4. **Fail Loudly** — Every scraper error fires a Slack alert. Silent failures are unacceptable.
5. **Self-Heal First** — BaseScraper retries 3x, classifies errors, auto-disables. Fix root causes.
6. **Human-in-the-Loop for Outreach** — No automated client contact without human approval.
7. **PII is Sacred** — Never log phone numbers, SSNs, or addresses to Slack or console in production.
8. **Audit Everything** — Every state change creates an immutable AuditEvent in MongoDB.
9. **Know Your Surety** — Every bond case carries a `Surety_ID`. POAs come from surety-specific inventory.
10. **The Chain Is Law** — No shortcuts. No paperwork before a validated match.
11. **Shamrock Exclusive** — Never reference or use non-Shamrock resources.
12. **SOC II Ready** — Build with compliance in mind: secure data flow, encryption, audit trails.

---

## Brand & Compliance Standards

1. **Shamrock Exclusive**: We are Shamrock Bail Bonds. Never reference 'WTF' or other identities.
2. **SOC II Compliance**: Architecture must support SOC II standards (secure data flow across GAS, SignNow, Twilio, Drive).
3. **High-Autonomy**: Agents are authorized to proactively fix bugs and fill gaps during audits without breaking existing functionality.
4. **Premium UI**: The dashboard must surpass Captira and Bail Books in both functionality and design.

---

## Key Skills

| Skill | Purpose | Path |
|-------|---------|------|
| `scraper-builder` | Add a new county scraper | `.agent/skills/scraper-builder/` |
| `scraper-debugger` | Debug a broken scraper | `.agent/skills/scraper-debugger/` |
| `lead-scoring-tuning` | Adjust scoring weights | `.agent/skills/lead-scoring-tuning/` |
| `contact-discovery` | Find family/friend contacts | `.agent/skills/contact-discovery/` |
| `county-jms-patterns` | JMS vendor reverse-engineering | `.agent/skills/county-jms-patterns/` |
| `docker-ops` | Container management on Hetzner | `.agent/skills/docker-ops/` |
| `frontend-design` | Premium dashboard UI/UX | `.agent/skills/frontend-design/` |
| `bluebubbles-integration` | iMessage automation | `.agent/skills/bluebubbles-integration/` |
| `systematic-debugging` | Root-cause-first debugging | `.agent/skills/systematic-debugging/` |
| `self-improving-agent` | Session learning & skill creation | `.agent/skills/self-improving-agent/` |
| `gws-shared` | Google Workspace CLI auth & global flags | `.agent/skills/gws-shared/` |
| `gws-calendar` | Google Calendar: court dates & events | `.agent/skills/gws-calendar/` |
| `gws-gmail` | Gmail: intake email triage & automation | `.agent/skills/gws-gmail/` |
| `gws-drive` | Google Drive: case file management | `.agent/skills/gws-drive/` |
| `mongodb-query-optimizer` | MongoDB query optimization & indexing | `.agent/skills/mongodb-query-optimizer/` |
| `mongodb-schema-design` | MongoDB schema patterns & anti-patterns | `.agent/skills/mongodb-schema-design/` |
| `mongodb-natural-language-querying` | Natural language to MongoDB queries | `.agent/skills/mongodb-natural-language-querying/` |

---

## Key Workflows

| Workflow | Trigger |
|----------|---------|
| `add-county-scraper` | "Add [county] scraper" |
| `deploy-to-hetzner` | "Deploy", "push to production" |
| `debug-scraper-failure` | Slack error alert, stale data |

---

## Key Reference Docs

- `BRAND.md` — Identity, vision, design standards, non-negotiables
- `AGENTS.md` — Digital workforce roles & scoring rules
- `DATA_MODEL.md` — Entity definitions (39-field ArrestRecord, MongoDB collections)
- `ROADMAP.md` — Phase-by-phase implementation status
- `docs/SCHEMAS.md` — Full MongoDB collection schemas
- `docs/COUNTY_REGISTRY.md` — All 67 counties with JMS vendors and scraper status
- `docs/policies/surety-policy.md` — POA inventory, surety selection, premium splits
- `docs/policies/matching-policy.md` — Match confidence scoring and validation gates

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12 |
| Scheduling | APScheduler |
| Database | MongoDB Atlas (motor, async) |
| Dashboard API | Quart (async Flask) |
| Frontend | Vanilla JS + CSS (~21,000 lines) |
| iMessage | BlueBubbles API |
| AI | OpenAI GPT-4o |
| Signatures | SignNow API |
| Payments | SwipeSimple |
| Alerts | Slack SDK |
| Hosting | Hetzner VPS (Docker Compose) |
| CI/CD | GitHub Actions |
| Ops | Node-RED (39+ cron jobs) |

---

*Maintained by: Brendan / Shamrock Active Software LLC | `admin@shamrockbailbonds.biz`*
