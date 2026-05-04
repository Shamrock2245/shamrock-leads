# 🤖 ShamrockLeads — Agent Handbook

> **Last Updated:** 2026-05-04
> **Repo:** `Shamrock2245/shamrock-leads`
> **Mission:** Scrape every arrest in every Florida county. Score every lead. Write every bond.
> **Read `BRAND.md` first** — it defines who we are, what we're building, and the non-negotiable standards every agent must follow.

---

## 1. What This Repo Does

ShamrockLeads is a **statewide arrest intelligence and bonded-case management platform** that:

1. **Scrapes** all 67 Florida county jail rosters on scheduled intervals `[IMPLEMENTED]`
2. **Normalizes** arrest data into a 39-column `ArrestRecord` schema `[IMPLEMENTED]`
3. **Scores** every arrestee as a bail bond lead (0–100, Hot/Warm/Cold/Disqualified) `[IMPLEMENTED]`
4. **Alerts** bondsmen via Slack with real-time hot lead notifications `[IMPLEMENTED]`
5. **Stores** everything in MongoDB Atlas with Google Sheets as a legacy fallback `[IMPLEMENTED]`
6. **Matches** indemnitor intake to the correct defendant `[IMPLEMENTED]`
7. **Creates bonded cases** with surety selection and POA assignment `[IMPLEMENTED]`
8. **Generates paperwork** (surety-specific template packets) `[IMPLEMENTED]`
9. **Orchestrates signatures** via SignNow `[IMPLEMENTED]`
10. **Collects payments** via SwipeSimple `[IMPLEMENTED — log + history API]`

### Pipeline Flow (Current — Full Lifecycle IMPLEMENTED)

```
County Jail Roster → Scraper → ArrestRecord → Lead Scorer → MongoDB + Slack Alert
  ↓
Defendant Normalization → Contact Discovery
  ↓
Indemnitor Intake (Wix / GAS / Telegram / Walk-in)
  ↓
Matching Engine (confidence-scored, human-gated)
  ↓
BondCase (Surety + POA + Case#)
  ↓
DocumentPacket (SignNow templates, hydrated)
  ↓
Signature (SignNow webhook confirms)
  ↓
Payment (SwipeSimple premium collection)
  ↓
Posted Bond (court-ready)
```

See `ROADMAP.md` for phase definitions and status.

---

## 2. System Goal

Move records safely through this lifecycle:

1. Scrape arrest/booking data `[Phase 1 — IMPLEMENTED]`
2. Normalize and deduplicate records `[Phase 1 — IMPLEMENTED]`
3. Score every record for lead qualification `[Phase 1 — IMPLEMENTED]`
4. Alert on hot leads via Slack `[Phase 1 — IMPLEMENTED]`
5. Create or update defendant records `[Phase 2 — IMPLEMENTED]`
6. Collect indemnitor intake from approved channels `[Phase 3 — IMPLEMENTED]`
7. Match indemnitor to defendant `[Phase 4 — IMPLEMENTED]`
8. Create bonded case only after match validation `[Phase 5 — IMPLEMENTED]`
9. Select surety (OSI or Palmetto) and assign POA from inventory `[Phase 5 — IMPLEMENTED]`
10. Generate surety-specific paperwork packet `[Phase 6 — IMPLEMENTED]`
11. Send packet for signature `[Phase 7 — IMPLEMENTED]`
12. Collect payment `[Phase 8 — IMPLEMENTED]`
13. Maintain immutable audit history for every state change `[Phase 2+ — IMPLEMENTED]`

---

## 3. Digital Workforce

| Agent | Role | Status | Where It Runs | Key File |
|-------|------|--------|---------------|----------|
| **The Clerk** | Jail roster parsing, HTML→JSON, anti-bot evasion | ✅ `IMPLEMENTED` | `scrapers/counties/*.py` | `base_scraper.py` |
| **The Analyst** | Lead scoring (0–100), risk classification | ✅ `IMPLEMENTED` | `scoring/lead_scorer.py` | `lead_scorer.py` |
| **The Watchdog** | Scraper health monitoring, failure alerts | ✅ `IMPLEMENTED` | `writers/slack_notifier.py` | `slack_notifier.py` |
| **The Matcher** | Link indemnitor intake to correct defendant | ✅ `IMPLEMENTED` | `dashboard/api/matching.py` | `matching.py` |
| **The Paperwork Agent** | Generate surety-specific bond paperwork | ✅ `IMPLEMENTED` | `dashboard/api/paperwork.py` | `paperwork.py` |
| **The Signature Agent** | Send and track SignNow packets | ✅ `IMPLEMENTED` | `dashboard/api/paperwork.py` | `paperwork.py` |
| **The Payment Agent** | Collect premium via SwipeSimple | 🔲 `Phase 8 — Next` | `payments/` | — |
| **The Auditor** | Immutable event logging for all state changes | ✅ `IMPLEMENTED` | `dashboard/api/events.py` | `events.py` |
| **The Finder** | OSINT: family/friend contact discovery | ✅ `IMPLEMENTED` | `discovery/` | — |
| **The Closer** | Outreach sequencing: SMS/WhatsApp drip | ✅ `IMPLEMENTED` | `dashboard/api/outreach.py` | `outreach.py` |
| **The Court Clerk** | Auto-scan court dates, schedule Twilio SMS | ✅ `IMPLEMENTED` | `dashboard/services/court_reminder_service.py` | `court_reminder_service.py` |
| **The Discharge Monitor** | Scan Gmail for exonerations, auto-discharge | ✅ `IMPLEMENTED` | `dashboard/api/discharge_monitor.py` | `discharge_monitor.py` |

---

## 4. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Hetzner VPS (Docker)                  │
│                                                         │
│  ┌──────────────────────┐  ┌──────────────────────────┐ │
│  │   shamrock-leads     │  │       node-red            │ │
│  │   (Python 3.12)      │  │   (Ops Dashboard)         │ │
│  │                      │  │                            │ │
│  │  APScheduler         │  │  7 dashboard pages         │ │
│  │    ↓                 │  │  39+ cron queries          │ │
│  │  50 County Scrapers  │  │  Slack relay               │ │
│  │  (Self-Healing)      │  │                            │ │
│  │    ↓                 │  │                            │ │
│  │  Lead Scorer         │  └─────────┬────────────────┘ │
│  │    ↓                 │            │                   │
│  │  Writers             │            │ HTTP              │
│  │   ├── MongoDB        │            │                   │
│  │   ├── Google Sheets  │            ▼                   │
│  │   └── Slack          │  ┌──────────────────────────┐ │
│  └──────────────────────┘  │  Dashboard (Flask :5050   │ │
│                            │   → external :8088)       │ │
│                            └──────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
                       │
              ┌────────┴────────┐
              │  MongoDB Atlas  │
              │  (Central DB)   │
              └─────────────────┘
```

---

## 5. The Scoring System ("Underwriting")

Every arrest is scored 0–100 with classification:

| Score | Status | Action |
|-------|--------|--------|
| 80+ | 🔥 **Hot** | Immediate Slack alert + outreach queue |
| 50–79 | 🟡 **Warm** | Logged, low-priority follow-up |
| 30–49 | ❄️ **Cold** | Stored only, no action |
| <30 | ❌ **Disqualified** | No bond / released / $0 bond |

### Scoring Factors

| Factor | Signal | Points |
|--------|--------|--------|
| Bond Amount | $500–$50K | +30 |
| Bond Amount | $50K–$100K | +20 |
| Bond Amount | >$100K | +10 |
| Bond Amount | <$500 | -10 |
| Bond Amount | $0 | -50 |
| Bond Type | Cash/Surety | +25 |
| Bond Type | No Bond/Hold | -50 |
| Bond Type | ROR | -30 |
| Custody Status | In Custody | +20 |
| Custody Status | Released | -30 |
| Data Completeness | All required fields | +15 |
| Data Completeness | Missing fields | -10 |
| Disqualifier | Capital/Murder/Federal charges | -100 |

---

## 6. Surety Companies

Shamrock represents **two** insurance/surety companies. Every bonded case must specify which surety is backing it.

| Surety | ID | Key Difference |
|--------|-----|----------------|
| **O'Shaughnahill Surety & Insurance (OSI)** | `osi` | Primary Florida surety (West Palm Beach, FL) |
| **Palmetto Surety Corporation** | `palmetto` | Multi-state: FL, SC, NC, TN, TX, CT, LA, MS |

The surety determines: POA book assignment, SignNow template set, commission rate, build-up fund %, monthly reporting format, and compliance requirements.

See `docs/policies/surety-policy.md` for selection rules.

---

## 7. Record Identity Model

Every workflow step must attach to the correct record boundary. **Never collapse these into one record.**

| Entity | Primary Key | Natural Key | Status |
|--------|-------------|-------------|--------|
| **Arrest Lead** | `ArrestLead_ID` | `County + Booking_Number` | ✅ Implemented |
| **Defendant** | `Defendant_ID` | Internal UUID | ✅ Implemented |
| **Indemnitor** | `Indemnitor_ID` | Internal UUID | ✅ Implemented |
| **Match** | `Match_ID` | Internal UUID | ✅ Implemented |
| **Bonded Case** | `Bond_Case_ID` | `POA_Number + Case_Number` | ✅ Implemented |
| **Document Packet** | `Packet_ID` | Internal UUID | ✅ Implemented |
| **Payment Request** | `Payment_Request_ID` | Internal UUID | ✅ Implemented |
| **Audit Event** | `Event_ID` | Immutable UUID | ✅ Implemented |
| **Surety** | `Surety_ID` | `osi` or `palmetto` | ✅ Implemented |
| **POA Inventory** | `POA_ID` | `POA_Number` (unique) | ✅ Implemented |

### Identity Rules

- Scraping never directly controls paperwork
- Intake never directly controls paperwork
- Paperwork only starts after a validated match AND a bonded case record
- Once a `POA_Number` is assigned, that bonded case becomes the operational anchor
- `POA_Number + Case_Number` must point to exactly one bonded case

See `DATA_MODEL.md` for full entity definitions.

---

## 8. Self-Healing Infrastructure

| Feature | Description |
|---------|-------------|
| **Pre-flight URL check** | HEAD request to roster URL before scraping — detects 404/403/SSL early |
| **Retry with backoff** | 3 attempts with exponential backoff (2s, 4s, 8s) |
| **Error classification** | Auto-classifies: `network`, `anti_bot`, `url_changed`, `parse_error`, `ssl_error`, `rate_limited` |
| **Auto-disable** | Scraper disabled after 5 consecutive failures |
| **Auto-re-enable** | Disabled scraper tries one recovery per interval — re-enables on success |
| **Failure history** | Last 10 failures stored with timestamps + error types |
| **Force re-enable** | `scraper.force_enable()` for human override |

---

## 9. Non-Negotiable Safety Rules

> **Brand Identity:** We are Shamrock Bail Bonds. We operate with speed, precision, and absolute compliance. Our agents must reflect this professional, high-autonomy, and zero-defect culture.
>
> **Compliance Standard:** All systems must be built with SOC II compliance principles in mind. Reference: [strongdm/comply](https://github.com/strongdm/comply), [getprobo/probo](https://github.com/getprobo/probo).

1. **No guessing** — Never guess identity, legal facts, case numbers, POA numbers, court data, payment/signature status.
2. **Fail closed** — If identity, match confidence, record ownership, or workflow state is unclear, stop and escalate.
3. **No paperwork before validated case** — Requires: defendant exists, indemnitor exists, match validated, bonded case exists with surety, case number present, POA assigned from correct surety inventory.
4. **No sending to wrong person** — Signing/payment links only to validated indemnitor on current bonded case.
5. **No mutating signed records** — Create new version, audit the replacement.
6. **Audit everything** — Every state change: timestamp, actor, agent name, record IDs, old→new state, confidence, reason.
7. **Minimize PII** — Never log phone numbers, SSNs, addresses in Slack/console/debug output.
8. **Source-of-truth hierarchy** — Arrest facts: county source → MongoDB. Case workflow: MongoDB. Signatures: SignNow. Payments: SwipeSimple. GAS/Sheets: downstream only.
9. **Surety-aware ops** — Every bond-writing action specifies the surety. Never assume OSI or Palmetto.

---

## 10. Prime Directives

1. **Scrape Respectfully** — Rate-limit. Rotate user agents. Never DDoS a county server.
2. **Idempotent Writes** — `Booking_Number + County` is the dedup key. Always check before insert.
3. **Score Everything** — No record enters the DB without a lead score.
4. **Fail Loudly** — Every scraper error fires a Slack alert. Silent failures are unacceptable.
5. **Self-Heal First** — BaseScraper retries 3x, classifies errors, auto-disables. Fix root causes.
6. **Human-in-the-Loop for Outreach** — No automated client contact without human approval.
7. **PII is Sacred** — Never log PII to Slack or console in production.
8. **Document Everything** — Every fix updates COUNTY_REGISTRY.md. No silent fixes.
9. **Know Your Surety** — Every bond case carries a `Surety_ID`. POAs come from surety-specific inventory.
10. **The Chain Is Law** — ArrestLead → Defendant → Indemnitor → Match → BondCase → Packet → Signature → Payment. No shortcuts.
11. **Shamrock Exclusive** — Never reference or use any resources, emails, or repos related to 'WTF' or non-Shamrock entities. We are exclusively `Shamrock2245` and `admin@shamrockbailbonds.biz`.
12. **End-to-End Integration** — All systems must integrate seamlessly across GAS, SignNow, Twilio, and Google Drive.

---

## 11. Required Read Order for Agents

1. `BRAND.md` — Identity, vision, design standards, non-negotiables
2. `AGENTS.md` (this file)
3. `DATA_MODEL.md`
4. `ROADMAP.md` — know what's implemented vs planned
5. `docs/agents/scraper-agent.md` — if doing scraper work `[IMPLEMENTED]`
6. `docs/policies/surety-policy.md` — if doing bond-writing work `[IMPLEMENTED]`
7. `docs/policies/matching-policy.md` — if doing matching work `[IMPLEMENTED]`
8. `docs/policies/signature-policy.md` — if doing signing work `[IMPLEMENTED]`

---

## 12. Escalation Conditions

Escalate immediately if:
- Two+ defendants could match one intake
- Indemnitor linked to wrong defendant
- Case number conflicts across sources
- POA already on another active bonded case
- POA from wrong surety's inventory
- Packet references mismatched defendant/indemnitor/case/POA/surety
- Signing/payment recipient ≠ validated indemnitor
- Signed packet needs correction
- Source data stale, conflicting, or corrupted

---

## 13. Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `MONGODB_URI` | ✅ | MongoDB Atlas connection string |
| `MONGODB_DB_NAME` | ✅ | Database name (default: `shamrock_leads`) |
| `SLACK_WEBHOOK_ARRESTS` | ✅ | #new-arrests channel |
| `SLACK_WEBHOOK_LEADS` | ✅ | #leads channel (hot leads) |
| `SLACK_WEBHOOK_ERRORS` | ✅ | #scraper-errors channel |
| `DEFAULT_SURETY` | Optional | Default surety ID (`osi` or `palmetto`) |
| `GAS_WEB_APP_URL` | Optional | GAS integration endpoint |
| `GOOGLE_SPREADSHEET_ID` | Optional | Legacy Sheets writer |
| `GOOGLE_APPLICATION_CREDENTIALS` | Optional | GCP service account JSON path |
| `OPENAI_API_KEY` | Optional | AI-powered enrichment |

---

## 14. Deployment

```bash
# Build and deploy to Hetzner (Docker Compose v2 — use space, NOT hyphen)
docker compose build --no-cache
docker compose up -d

# Check health
docker compose ps
docker logs shamrock-leads --tail 50

# Dashboard URL: http://178.156.179.237:8088/
# (Flask listens internally on 5050, Docker maps external 8088 → internal 5050)

# Run one-shot for a specific county
docker exec shamrock-leads python main.py lee
```

### Development Patterns

- **Adding a county scraper**: See `.agent/skills/scraper-builder/SKILL.md`
- **Debugging a broken scraper**: See `.agent/skills/scraper-debugger/SKILL.md`
- **Tuning lead scores**: See `.agent/skills/lead-scoring-tuning/SKILL.md`
