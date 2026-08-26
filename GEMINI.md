# GEMINI.md — ShamrockLeads Intelligence Platform

> This file configures AI coding assistants (Gemini, Antigravity, Manus, etc.).
> **Read `BRAND.md` first.** It defines who we are and what we're building.
> **Last Updated:** 2026-08-09  
> **Authoritative status:** [`STATUS.md`](./STATUS.md) · Agent handbook: [`AGENTS.md`](./AGENTS.md)

---

## What This Is

ShamrockLeads is the core intelligence engine for **Shamrock Bail Bonds** — multi-state arrest intelligence
and bond Auto-CRM (scrape → score → outreach → intake → match → paperwork → pay → active bond lifecycle).

**Strategic goal:** Scale from $3–5M/year (Lee County) to $50M+/year across the **Palmetto surety footprint**
plus **Georgia** — **357 registered scrapers** across 10 states (see `STATUS.md`).

---

## Identity & Brand (Non-Negotiable)

- **GitHub:** `Shamrock2245` only. Never reference WTF or other non-Shamrock identities.
- **Email:** `admin@shamrockbailbonds.biz` for all integrations.
- **Sureties:** OSI (`osi`) and Palmetto (`palmetto`). Every bond specifies which.
- **Production VPS:** `178.156.179.237` (Hetzner, root access)
- **Dashboard URL:** `http://178.156.179.237:8088/` (internal: FastAPI on `:5050`)
- **Public Domain:** `https://leads.shamrockbailbonds.biz` (Nginx reverse proxy → `:8088`)
- **iMessage Bridge:** Tailscale `http://100.102.10.86:1234` (primary) · frp `:12434` (backup) · office iMac BlueBubbles. Warren is scraper egress only.
- **DocuSeal Portal:** `https://sign.shamrockbailbonds.biz` (Primary e-signature engine · Template 1 OSI)
- **Postiz Social & MCP:** `https://social.shamrockbailbonds.biz` (`/api/mcp` SSE stream · 5 channels live)
- **OpenCut Editor:** `https://edit.shamrockbailbonds.biz` (VPS Docker `shamrock-opencut` · nginx → `:5320` — not Postiz, not the laptop)
- **Host inventory:** `docs/SUBDOMAINS.md` · `config/subdomains.py`

---

## Architecture

```
Hetzner VPS (Docker Compose)
  shamrock-leads     → Python 3.12 scraper engine + APScheduler (FL + GA counties)
  shamrock-dashboard → FastAPI async API (port 5050 → Nginx :443 → external :8088)
  node-red           → Ops dashboard (port 1880)

MongoDB Atlas        → Primary database: ShamrockBailDB (all entities)
BlueBubbles          → iMessage bridge (office iMac via Tailscale, frp backup)
DocuSeal             → E-signature backbone (https://sign.shamrockbailbonds.biz · 14-doc OSI/Palmetto)
SwipeSimple          → Bond premium payment collection
Slack                → Real-time operational alerts (12+ channels)
Google Workspace     → Gmail discharge scanner, GCal court sync, Drive case files
Twilio               → SMS court reminders, 10DLC compliant
Postiz               → Social media scheduling & MCP server (social.shamrockbailbonds.biz)
```

### Docker Services

| Service | Container | Internal | External | Purpose |
|---------|-----------|----------|----------|---------|
| `shamrock-leads` | `shamrock-leads` | — | — | Scraper engine + APScheduler |
| `dashboard` | `shamrock-dashboard` | 5050 | 8088 | FastAPI dashboard (61 API modules, 36 services) |
| `traccar` | `shamrock-traccar` | 8082 | 8082 | GPS tracking (OsmAnd, vehicle trackers) |
| `node-red` | `shamrock-node-red` | 1880 | 1880 | Ops dashboard + 39 cron jobs (profile: ops) |
| `social` | `shamrock-social` | 5060 | 8089 | Social Engine API (profile: social) |
| `postiz` | `shamrock-postiz` | 5000 | 5200 | Postiz Social UI/API (profile: social) |
| `opencut` | `shamrock-opencut` | 3000 | 5320 | OpenCut editor (profile: `edit` → edit.shamrockbailbonds.biz) |

---

## The Data Chain (The Law)

```
ArrestLead -> Defendant -> Indemnitor -> Match(validated) ->
  BondCase(Surety + POA + Case#) -> DocumentPacket -> Signature -> Payment
```

**Dedup key:** `County + Booking_Number`. Always check before insert.
**Identity rule:** ArrestLead ≠ Defendant ≠ Indemnitor ≠ Match ≠ BondCase. Never collapse.

---

## Active Bond Lifecycle (7-Status Kanban)

```
Active → Monitoring → Alert → Exonerated / Forfeited / Surrendered → Reinstated
```

- **Destructive transitions** (Forfeited, Surrendered): require confirmation modal
- **POA auto-release**: triggered on Exonerated, Forfeited, Surrendered
- **Audit trail**: every transition logged to `status_history[]` + `audit_events` collection

---

## Codebase Metrics (Current)

| Metric | Count |
|--------|-------|
| Registered scrapers | **357** (85 GA · **67 FL** · 60 NC · 46 SC · 34 TX · 22 TN · 16 AL · 12 LA · 9 MS · 6 CT) — see `STATUS.md` |
| Scraper paths | `counties/` (FL), `counties_ga/`, `counties_sc/`, `counties_nc/`, `counties_tx/`, `counties_tn/`, `counties_la/`, `counties_al/`, `counties_ct/`, `counties_ms/` |
| Job ID form | FL: `scraper_<county>` · other: `scraper_<st>_<county>` |
| API blueprint modules | 66+ (in `dashboard/routers/`) incl. `multi_state_ops.py` |
| Service modules | 45+ (in `dashboard/services/`) |
| Frontend JS modules | 45+ (`sl-*.js` + Multi-State Ops + Bond Intelligence) |
| Frontend CSS files | 11+ (incl. `sl-multi-state.css`, `sl-bond-intelligence.css`) |
| Agent skills | 36 (in `.agent/skills/`) |
| Dashboard tabs | Super CRM + **Multi-State Ops** + **Bond Intelligence** + lifecycle/ops tabs |

---

## Prime Directives

1. **Scrape Respectfully** — Rate-limit. Rotate user agents. Never DDoS a county server.
2. **Idempotent Writes** — `Booking_Number + County` is the dedup key. Always check before insert.
3. **Score Everything** — No record enters the DB without a lead score (0–100).
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

## Key Skills (34 total in `.agent/skills/`)

| Skill | Purpose |
|-------|---------|
| `scraper-builder` | Add a new county scraper |
| `scraper-debugger` | Debug a broken scraper |
| `lead-scoring-tuning` | Adjust scoring weights |
| `frontend-design` | Premium dashboard UI/UX |
| `docker-ops` | Container management on Hetzner |
| `git-sync-deploy` | Multi-environment sync workflow |
| `bluebubbles-integration` | iMessage automation (9-module Python layer) |
| `systematic-debugging` | Root-cause-first debugging |
| `contact-discovery` | OSINT family/friend contacts |
| `county-jms-patterns` | JMS vendor reverse-engineering |
| `mongodb-*` (3 skills) | Query optimization, schema design, natural language querying |
| `gws-*` (4 skills) | Google Workspace: shared auth, Calendar, Gmail, Drive |
| `cloudflare-*` (3 skills) | Platform, deploy, email |
| `sentry-*` (2 skills) | Error monitoring + fix workflow |
| `wix-*` (3 skills) | App builder, REST management, design system |
| `elevenlabs-agents` | Shannon voice agent |
| `openai-agents-sdk` | Multi-agent orchestration |
| `pdf-processing` | Bond paperwork PDF manipulation |
| `programmatic-seo` | County-specific landing pages |
| `mcp-builder` | MCP server development |
| `seo-audit` | Page performance & SEO |
| `skill-creator` | Create new agent skills |
| `self-improving-agent` | Session learning |
| `verification-before-completion` | No claims without evidence |

---

## Key Workflows

| Workflow | Trigger |
|----------|---------|
| `add-county-scraper` | "Add [county] scraper" |
| `deploy-to-hetzner` | "Deploy", "push to production" |
| `debug-scraper-failure` | Slack error alert, stale data |

---

## Key Reference Docs

| Doc | Purpose |
|-----|---------|
| `BRAND.md` | Identity, vision, design standards, non-negotiables |
| `AGENTS.md` | Digital workforce roles, scoring rules, safety rules |
| `DATA_MODEL.md` | Entity definitions, MongoDB collections |
| `ROADMAP.md` | Phase-by-phase implementation status |
| `docs/SCHEMAS.md` | Full MongoDB collection schemas |
| `docs/COUNTY_REGISTRY.md` | All 67 FL counties with JMS vendors and scraper status |
| `docs/GEORGIA_COUNTY_REGISTRY.md` | All 159 GA counties with JMS vendors and scraper status |
| `docs/policies/surety-policy.md` | POA inventory, surety selection, premium splits |
| `docs/policies/matching-policy.md` | Match confidence scoring and validation gates |
| `docs/policies/signature-policy.md` | Packet binding rules, signing workflow |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12 |
| Scheduling | APScheduler |
| Database | MongoDB Atlas (motor, async) — DB: `ShamrockBailDB` |
| Dashboard API | FastAPI — 61 API modules, 36 services |
| Frontend | Vanilla JS + CSS — 32 modules, ~25,700 LOC |
| iMessage | BlueBubbles API via Tailscale (primary) / frp (backup) |
| AI | OpenAI GPT-4o (auto-reply, lead enrichment) |
| Signatures | SignNow API (14-doc packet orchestration) |
| Payments | SwipeSimple (premium collection) |
| SMS | Twilio (court reminders, 10DLC compliant) |
| Alerts | Slack SDK (12+ webhook channels) |
| Hosting | Hetzner VPS (Docker Compose) |
| Proxy | Nginx reverse proxy → `leads.shamrockbailbonds.biz` |
| CI/CD | GitHub Actions |
| Ops | Node-RED (39+ cron jobs) |

---

*Maintained by: Brendan / Shamrock Active Software LLC | `admin@shamrockbailbonds.biz`*
