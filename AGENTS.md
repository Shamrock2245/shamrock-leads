# 🤖 ShamrockLeads — Agent Handbook

> **Last Updated:** April 27, 2026
> **Repo:** `Shamrock2245/shamrock-leads`
> **Mission:** Scrape every arrest in every Florida county. Score every lead. Write every bond.

---

## 1. What This Repo Does

ShamrockLeads is a **statewide arrest intelligence and bonded-case management platform** that:

1. **Scrapes** all 67 Florida county jail rosters on scheduled intervals `[IMPLEMENTED]`
2. **Normalizes** arrest data into a 39-column `ArrestRecord` schema `[IMPLEMENTED]`
3. **Scores** every arrestee as a bail bond lead (0–100, Hot/Warm/Cold/Disqualified) `[IMPLEMENTED]`
4. **Alerts** bondsmen via Slack with real-time hot lead notifications `[IMPLEMENTED]`
5. **Stores** everything in MongoDB Atlas with Google Sheets as a legacy fallback `[IMPLEMENTED]`
6. **Matches** indemnitor intake to the correct defendant `[PLANNED — Phase 4]`
7. **Creates bonded cases** with surety selection and POA assignment `[PLANNED — Phase 5]`
8. **Generates paperwork** (surety-specific template packets) `[PLANNED — Phase 6]`
9. **Orchestrates signatures** via SignNow `[PLANNED — Phase 7]`
10. **Collects payments** via SwipeSimple `[PLANNED — Phase 8]`

### Pipeline Flow (Current — Phase 1)

```
County Jail Roster → Scraper → ArrestRecord → Lead Scorer → Writer(s) → Slack Alert
```

### Pipeline Flow (Target — Full Lifecycle)

```
ArrestLead → Defendant → Indemnitor Intake → Match(validated) →
  BondCase(Surety + POA + Case#) → DocumentPacket → Signature → Payment
```

See `ROADMAP.md` for phase definitions and status.

---

## 2. System Goal

Move records safely through this lifecycle:

1. Scrape arrest/booking data `[Phase 1 — IMPLEMENTED]`
2. Normalize and deduplicate records `[Phase 1 — IMPLEMENTED]`
3. Score every record for lead qualification `[Phase 1 — IMPLEMENTED]`
4. Alert on hot leads via Slack `[Phase 1 — IMPLEMENTED]`
5. Create or update defendant records `[Phase 2 — PLANNED]`
6. Collect indemnitor intake from approved channels `[Phase 3 — PLANNED]`
7. Match indemnitor to defendant `[Phase 4 — PLANNED]`
8. Create bonded case only after match validation `[Phase 5 — PLANNED]`
9. Select surety (OSI or Palmetto) and assign POA from inventory `[Phase 5 — PLANNED]`
10. Generate surety-specific paperwork packet `[Phase 6 — PLANNED]`
11. Send packet for signature `[Phase 7 — PLANNED]`
12. Collect payment `[Phase 8 — PLANNED]`
13. Maintain immutable audit history for every state change `[Phase 2+ — PLANNED]`

---

## 3. Digital Workforce

| Agent | Role | Status | Where It Runs | Key File |
|-------|------|--------|---------------|----------|
| **The Clerk** | Jail roster parsing, HTML→JSON, anti-bot evasion | ✅ `IMPLEMENTED` | `scrapers/counties/*.py` | `base_scraper.py` |
| **The Analyst** | Lead scoring (0–100), risk classification | ✅ `IMPLEMENTED` | `scoring/lead_scorer.py` | `lead_scorer.py` |
| **The Watchdog** | Scraper health monitoring, failure alerts | ✅ `IMPLEMENTED` | `writers/slack_notifier.py` | `slack_notifier.py` |
| **The Matcher** | Link indemnitor intake to correct defendant | 🔲 `Phase 4` | `matching/` | — |
| **The Paperwork Agent** | Generate surety-specific bond paperwork | 🔲 `Phase 6` | `paperwork/` | — |
| **The Signature Agent** | Send and track SignNow packets | 🔲 `Phase 7` | `signing/` | — |
| **The Payment Agent** | Collect premium via SwipeSimple | 🔲 `Phase 8` | `payments/` | — |
| **The Auditor** | Immutable event logging for all state changes | 🔲 `Phase 2+` | `audit/` | — |
| **The Finder** | OSINT: family/friend contact discovery | 🔲 `Phase 9` | `discovery/` | — |
| **The Closer** | Outreach sequencing: SMS/WhatsApp drip | 🔲 `Phase 10` | `outreach/` | — |

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
│  │  20 County Scrapers  │  │  Slack relay               │ │
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
| **Old Southern Indemnity (OSI)** | `osi` | Primary Florida surety |
| **Palmetto Surety Corporation** | `palmetto` | Multi-state: FL, SC, NC, TN, TX, CT, LA, MS |

The surety determines: POA book assignment, SignNow template set, commission rate, build-up fund %, monthly reporting format, and compliance requirements.

See `docs/policies/surety-policy.md` for selection rules.

---

## 7. Record Identity Model

Every workflow step must attach to the correct record boundary. **Never collapse these into one record.**

| Entity | Primary Key | Natural Key | Status |
|--------|-------------|-------------|--------|
| **Arrest Lead** | `ArrestLead_ID` | `County + Booking_Number` | ✅ Implemented |
| **Defendant** | `Defendant_ID` | Internal UUID | 🔲 Phase 2 |
| **Indemnitor** | `Indemnitor_ID` | Internal UUID | 🔲 Phase 3 |
| **Match** | `Match_ID` | Internal UUID | 🔲 Phase 4 |
| **Bonded Case** | `Bond_Case_ID` | `POA_Number + Case_Number` | 🔲 Phase 5 |
| **Document Packet** | `Packet_ID` | Internal UUID | 🔲 Phase 6 |
| **Payment Request** | `Payment_Request_ID` | Internal UUID | 🔲 Phase 8 |
| **Audit Event** | `Event_ID` | Immutable UUID | 🔲 Phase 2+ |
| **Surety** | `Surety_ID` | `osi` or `palmetto` | 🔲 Phase 5 |
| **POA Inventory** | `POA_ID` | `POA_Number` (unique) | 🔲 Phase 5 |

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

---

## 11. Required Read Order for Agents

1. `AGENTS.md` (this file)
2. `DATA_MODEL.md`
3. `ROADMAP.md` — know what's implemented vs planned
4. `docs/agents/scraper-agent.md` — if doing scraper work `[IMPLEMENTED]`
5. `docs/policies/surety-policy.md` — if doing bond-writing work `[Phase 5+]`
6. `docs/policies/matching-policy.md` — if doing matching work `[Phase 4+]`
7. `docs/policies/signature-policy.md` — if doing signing work `[Phase 7+]`

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
