# 🤖 ShamrockLeads — Agent Handbook

> **Last Updated:** 2026-05-15
> **Repo:** `Shamrock2245/shamrock-leads`
> **Mission:** Scrape every arrest in every Florida county. Score every lead. Write every bond.
> **Read `BRAND.md` first** — it defines who we are, what we're building, and the non-negotiable standards every agent must follow.

---

## 1. What This Repo Does

ShamrockLeads is a **statewide arrest intelligence and bonded-case management platform** that:

1. **Scrapes** 51 Florida county jail rosters on scheduled intervals `[IMPLEMENTED]`
2. **Normalizes** arrest data into a 39-column `ArrestRecord` schema `[IMPLEMENTED]`
3. **Scores** every arrestee as a bail bond lead (0–100, Hot/Warm/Cold/Disqualified) `[IMPLEMENTED]`
4. **Alerts** bondsmen via Slack with real-time hot lead notifications `[IMPLEMENTED]`
5. **Stores** everything in MongoDB Atlas (`ShamrockBailDB`) `[IMPLEMENTED]`
6. **Matches** indemnitor intake to the correct defendant `[IMPLEMENTED]`
7. **Creates bonded cases** with surety selection and POA assignment `[IMPLEMENTED]`
8. **Generates paperwork** (surety-specific template packets) `[IMPLEMENTED]`
9. **Orchestrates signatures** via SignNow `[IMPLEMENTED]`
10. **Collects payments** via SwipeSimple `[IMPLEMENTED]`
11. **Manages** the 7-status bond lifecycle via drag-and-drop Kanban `[IMPLEMENTED]`
12. **Automates** iMessage outreach via BlueBubbles bridge `[IMPLEMENTED]`
13. **Detects** re-arrests of defendants on active bonds `[IMPLEMENTED]`
14. **Monitors** Gmail for court discharge/exoneration emails `[IMPLEMENTED]`
15. **Syncs** court dates to Google Calendar `[IMPLEMENTED]`

### Pipeline Flow (Full Lifecycle)

```
County Jail Roster → Scraper → ArrestRecord → Lead Scorer → MongoDB + Slack Alert
  ↓
Defendant Normalization → Contact Discovery
  ↓
Indemnitor Intake (Wix / Telegram / Walk-in / Phone / Bookmarklet)
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
Active Bond Management (7-status Kanban lifecycle)
  ↓
Court Reminders → Discharge Monitoring → Exoneration
```

---

## 2. System Goal

Move records safely through this lifecycle:

1. Scrape arrest/booking data `[IMPLEMENTED]`
2. Normalize and deduplicate records `[IMPLEMENTED]`
3. Score every record for lead qualification `[IMPLEMENTED]`
4. Alert on hot leads via Slack `[IMPLEMENTED]`
5. Create or update defendant records `[IMPLEMENTED]`
6. Collect indemnitor intake from approved channels `[IMPLEMENTED]`
7. Match indemnitor to defendant `[IMPLEMENTED]`
8. Create bonded case only after match validation `[IMPLEMENTED]`
9. Select surety (OSI or Palmetto) and assign POA from inventory `[IMPLEMENTED]`
10. Generate surety-specific paperwork packet `[IMPLEMENTED]`
11. Send packet for signature `[IMPLEMENTED]`
12. Collect payment `[IMPLEMENTED]`
13. Manage bond through 7-status lifecycle (Kanban) `[IMPLEMENTED]`
14. Auto-detect re-arrests on active bonds `[IMPLEMENTED]`
15. Monitor for court discharges/exonerations `[IMPLEMENTED]`
16. Maintain immutable audit history for every state change `[IMPLEMENTED]`

---

## 3. Digital Workforce

| Agent | Role | Status | Key File(s) |
|-------|------|--------|-------------|
| **The Clerk** | Jail roster parsing, anti-bot evasion | ✅ Live | `scrapers/counties/*.py`, `base_scraper.py` |
| **The Analyst** | Lead scoring (0–100), risk classification | ✅ Live | `scoring/lead_scorer.py` |
| **The Watchdog** | Scraper health monitoring, failure alerts | ✅ Live | `writers/slack_notifier.py` |
| **The Matcher** | Link indemnitor intake to correct defendant | ✅ Live | `dashboard/api/matching.py`, `services/matching_engine.py` |
| **The Paperwork Agent** | Generate surety-specific bond paperwork | ✅ Live | `dashboard/api/paperwork.py`, `services/signnow_packet_service.py` |
| **The Signature Agent** | Send and track SignNow packets | ✅ Live | `services/signnow_service.py`, `api/bond_lifecycle.py` |
| **The Payment Agent** | Log and track premium payments | ✅ Live | `dashboard/api/payments.py`, `api/payment_plans.py` |
| **The Auditor** | Immutable event logging for all state changes | ✅ Live | `dashboard/api/events.py` |
| **The Finder** | OSINT: family/friend contact discovery | ✅ Live | `services/contact_discovery.py`, `api/contacts.py` |
| **The Closer** | Outreach sequencing via iMessage | ✅ Live | `dashboard/api/outreach.py`, `services/outreach_sequencer.py` |
| **The Court Clerk** | Auto-scan court dates, schedule Twilio SMS | ✅ Live | `services/court_reminder_service.py`, `api/court_reminders.py` |
| **The Discharge Monitor** | Scan Gmail for exonerations, auto-discharge | ✅ Live | `dashboard/api/discharge_monitor.py` |
| **Shannon** | AI iMessage auto-reply agent | ✅ Live | `dashboard/api/agent_brain.py`, `api/agent_brain_api.py` |
| **Re-Arrest Detector** | Cross-reference new arrests against active bonds | ✅ Live | `dashboard/api/rearrest_detector.py`, `api/rearrest_notifier.py` |
| **Data Retention** | Tiered purge policies for M0 512MB limit | ✅ Live | `dashboard/api/data_retention.py` |

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
│  │  51 County Scrapers  │  │  Slack relay               │ │
│  │  (Self-Healing)      │  │                            │ │
│  │    ↓                 │  └─────────┬────────────────┘ │
│  │  Lead Scorer         │            │                   │
│  │    ↓                 │            │ HTTP              │
│  │  Writers             │            │                   │
│  │   ├── MongoDB        │            ▼                   │
│  │   ├── Google Sheets  │  ┌──────────────────────────┐ │
│  │   └── Slack          │  │  Dashboard (Quart :5050   │ │
│  └──────────────────────┘  │   → Nginx :443            │ │
│                            │   → external :8088)       │ │
│  ┌──────────────────────┐  │                            │ │
│  │  Nginx Reverse Proxy │  │  61 API modules            │ │
│  │  leads.shamrock      │  │  36 service modules        │ │
│  │  bailbonds.biz       │  │  42 frontend JS modules    │ │
│  └──────────────────────┘  └──────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
         │                            │
    ┌────┴────────┐          ┌────────┴────────┐
    │  MongoDB    │          │  BlueBubbles    │
    │  Atlas      │          │  iMessage Bridge│
    │  (Central)  │          │  (ngrok Tunnel) │
    └─────────────┘          └─────────────────┘
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

**Rule:** OSI is always preferred. Use Palmetto when OSI inventory is depleted for the needed tier, or when the case is outside Florida.

See `docs/policies/surety-policy.md` for selection rules.

---

## 7. Record Identity Model

Every workflow step must attach to the correct record boundary. **Never collapse these into one record.**

| Entity | Primary Key | Natural Key | MongoDB Collection |
|--------|-------------|-------------|-------------------|
| **Arrest Lead** | `_id` (ObjectId) | `County + Booking_Number` | `arrests` |
| **Defendant** | `Defendant_ID` (UUID) | Internal | `defendants` |
| **Indemnitor** | `Indemnitor_ID` (UUID) | Internal | `indemnitors` |
| **Match** | `Match_ID` (UUID) | Internal | `matches` |
| **Bonded Case** | `Bond_Case_ID` (UUID) | `POA_Number + Case_Number` | `active_bonds` |
| **Document Packet** | `Packet_ID` (UUID) | Internal | `paperwork_packets` |
| **Payment** | `Payment_ID` (UUID) | Internal | `payments` |
| **Audit Event** | `Event_ID` (UUID) | Immutable | `audit_events` |
| **POA Inventory** | `POA_ID` | `POA_Number` (unique) | `poa_inventory` |
| **Prospective Bond** | `_id` (ObjectId) | Internal | `prospective_bonds` |
| **Intake Record** | `_id` (ObjectId) | Internal | `intake_queue` |
| **Defendant Note** | `_id` (ObjectId) | Internal | `defendant_notes` |
| **Court Reminder** | `_id` (ObjectId) | Internal | `court_reminders` |

### Identity Rules

- Scraping never directly controls paperwork
- Intake never directly controls paperwork
- Paperwork only starts after a validated match AND a bonded case record
- Once a `POA_Number` is assigned, that bonded case becomes the operational anchor
- `POA_Number + Case_Number` must point to exactly one bonded case

---

## 8. Bond Lifecycle (7-Status Kanban)

Active bonds move through these statuses via drag-and-drop Kanban board:

| Status | Description | POA Action |
|--------|-------------|------------|
| `active` | Bond is posted and current | — |
| `monitoring` | Elevated attention required | — |
| `alert` | Immediate agent intervention | — |
| `exonerated` | Court discharged the bond | **Auto-release POA** |
| `forfeited` | Defendant FTA, bond forfeited | **Auto-release POA** (confirmation required) |
| `surrendered` | Defendant surrendered | **Auto-release POA** (confirmation required) |
| `reinstated` | Previously forfeited/surrendered, reinstated | — |

**Every status transition** is:
1. Logged to `status_history[]` on the bond document
2. Written as an immutable `audit_events` record
3. Optionally annotated with actor + note

---

## 9. Self-Healing Infrastructure

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

## 10. Non-Negotiable Safety Rules

> **Brand Identity:** We are Shamrock Bail Bonds. We operate with speed, precision, and absolute compliance.
> **Compliance Standard:** All systems must be built with SOC II compliance principles in mind.

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

## 11. Prime Directives

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
11. **Shamrock Exclusive** — Never reference or use any resources, emails, or repos related to 'WTF' or non-Shamrock entities.
12. **End-to-End Integration** — All systems must integrate seamlessly across SignNow, Twilio, iMessage, and Google Drive.

---

## 12. Required Read Order for Agents

1. `BRAND.md` — Identity, vision, design standards, non-negotiables
2. `AGENTS.md` (this file) — Digital workforce, scoring, safety rules
3. `DATA_MODEL.md` — Entity definitions, MongoDB collections
4. `ROADMAP.md` — What's implemented vs planned
5. `docs/policies/surety-policy.md` — if doing bond-writing work
6. `docs/policies/matching-policy.md` — if doing matching work
7. `docs/policies/signature-policy.md` — if doing signing work

---

## 13. Escalation Conditions

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

## 14. Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `MONGODB_URI` | ✅ | MongoDB Atlas connection string |
| `MONGODB_DB_NAME` | ✅ | Database name (default: `ShamrockBailDB`) |
| `DASHBOARD_PIN` | ✅ | Dashboard authentication PIN |
| `SECRET_KEY` | ✅ | Session encryption key (prevents invalidation on restart) |
| `SLACK_WEBHOOK_ARRESTS` | ✅ | #new-arrests channel |
| `SLACK_WEBHOOK_LEADS` | ✅ | #leads channel (hot leads) |
| `SLACK_WEBHOOK_ERRORS` | ✅ | #scraper-errors channel |
| `BLUEBUBBLES_URL_0178` | ✅ | ngrok permanent tunnel URL (office iMac) |
| `BLUEBUBBLES_PASSWORD_0178` | ✅ | BlueBubbles API password |
| `SIGNNOW_API_TOKEN` | ✅ | SignNow bearer token |
| `SIGNNOW_BASIC_AUTH` | ✅ | Base64 client_id:client_secret for ROPC flow |
| `SIGNNOW_USERNAME` | ✅ | `admin@shamrockbailbonds.biz` |
| `SIGNNOW_PASSWORD` | ✅ | SignNow account password |
| `TWILIO_ACCOUNT_SID` | Optional | Twilio SID for SMS court reminders |
| `TWILIO_AUTH_TOKEN` | Optional | Twilio auth token |
| `TWILIO_FROM_NUMBER` | Optional | Twilio sender number |
| `OPENAI_API_KEY` | Optional | AI-powered enrichment + auto-reply |
| `DEFAULT_SURETY` | Optional | Default surety ID (`osi` or `palmetto`) |
| `DASHBOARD_PUBLIC_URL` | Optional | `https://leads.shamrockbailbonds.biz` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Optional | GCP service account JSON path |
| `FIREBASE_ADMINSDK_PATH` | Optional | Firebase admin SDK for BB URL auto-sync |

---

## 15. Deployment

```bash
# Build and deploy to Hetzner (Docker Compose v2 — use space, NOT hyphen)
docker compose build --no-cache
docker compose up -d

# Check health
docker compose ps
docker logs shamrock-leads --tail 50

# Dashboard URL: http://178.156.179.237:8088/
# Public URL: https://leads.shamrockbailbonds.biz
# (Quart listens internally on 5050, Docker maps external 8088 → internal 5050)

# Run one-shot for a specific county
docker exec shamrock-leads python main.py lee
```

### Development Patterns

- **Adding a county scraper**: See `.agent/skills/scraper-builder/SKILL.md`
- **Debugging a broken scraper**: See `.agent/skills/scraper-debugger/SKILL.md`
- **Tuning lead scores**: See `.agent/skills/lead-scoring-tuning/SKILL.md`
- **iMessage integration**: See `.agent/skills/bluebubbles-integration/SKILL.md`
- **Frontend UI work**: See `.agent/skills/frontend-design/SKILL.md`
- **Docker ops**: See `.agent/skills/docker-ops/SKILL.md`
