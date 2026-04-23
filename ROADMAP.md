# ROADMAP.md — ShamrockLeads Phase Progression

> **Purpose:** Define what exists vs what's coming. Every agent must check this before acting.

---

## Phase Overview

| Phase | Name | Status | Key Entities | Key Agents |
|-------|------|--------|--------------|------------|
| 1 | Scrape → Score → Alert | ✅ **Complete** | ArrestLead | The Clerk, The Analyst, The Watchdog |
| 2 | Defendant Normalization | 🔲 Planned | Defendant, AuditEvent | The Auditor |
| 3 | Intake Ingestion | 🔲 Planned | Indemnitor | — |
| 4 | Matching Engine | 🔲 Planned | Match | The Matcher |
| 5 | Bond Case + Surety + POA | 🔲 Planned | BondCase, Surety, POAInventory | — |
| 6 | Paperwork Generation | 🔲 Planned | DocumentPacket | The Paperwork Agent |
| 7 | Signature Orchestration | 🔲 Planned | SignNow integration | The Signature Agent |
| 8 | Payment Collection | 🔲 Planned | PaymentRequest | The Payment Agent |
| 9 | Contact Discovery (OSINT) | 🔲 Planned | ContactLead | The Finder |
| 10 | Outreach Sequencing | 🔲 Planned | DripCampaign | The Closer |

---

## Phase 1: Scrape → Score → Alert ✅ COMPLETE

**What exists in code today.**

### Capabilities
- 20 county scrapers (Lee, Collier, Charlotte, Hendry, DeSoto, Manatee, Hillsborough, Sarasota, Polk, Brevard, Escambia, Orange, Osceola, Seminole, Palm Beach, Broward, Duval, Volusia, Pasco, Pinellas)
- Self-healing BaseScraper with retry, auto-disable, error classification
- Lead scoring (0–100) with Hot/Warm/Cold/Disqualified classification
- MongoDB Atlas storage (upsert by County + Booking_Number)
- Legacy Google Sheets writer
- Real-time Slack alerts for hot leads, new arrests, errors, health
- APScheduler with per-county intervals and staggered first-runs
- Flask dashboard with arrest viewer
- Docker deployment on Hetzner VPS

### Key Files
- `main.py` — Entry point
- `core/models.py` — ArrestRecord dataclass
- `core/scheduler.py` — APScheduler setup
- `core/dedup.py` — Deduplication engine
- `scrapers/base_scraper.py` — Abstract base with self-healing
- `scrapers/counties/*.py` — 20 county implementations
- `scoring/lead_scorer.py` — Scoring rules
- `writers/mongo_writer.py` — MongoDB upsert
- `writers/sheets_writer.py` — Legacy Sheets
- `writers/slack_notifier.py` — Slack alerts

---

## Phase 2: Defendant Normalization 🔲 PLANNED

### Goal
Collapse multiple arrest records for the same physical person into a single Defendant record.

### Why It Matters
One person can be arrested multiple times across multiple counties. Without a Defendant entity, we treat each booking as a separate lead — missing the pattern.

### What Gets Built
- `Defendant` model in `core/models.py`
- Person-matching logic (name + DOB + county fuzzy match)
- `defendants` collection in MongoDB
- `AuditEvent` model and `audit_events` collection
- Every Defendant creation/update emits an audit event

### Dependencies
- None (builds on Phase 1 output)

---

## Phase 3: Intake Ingestion 🔲 PLANNED

### Goal
Receive indemnitor (co-signer) information from external channels and store as Indemnitor records.

### Intake Sources
- Wix Portal (via GAS → HTTP)
- Telegram Bot (via webhook)
- Shannon voice AI (via ElevenLabs → Netlify → GAS)
- Direct phone/SMS (via Twilio)

### What Gets Built
- `Indemnitor` model
- `indemnitors` collection in MongoDB
- Intake API endpoint (receive from GAS/Wix/Telegram)
- Verification status tracking

### Dependencies
- Phase 2 (Defendant must exist to match against)

---

## Phase 4: Matching Engine 🔲 PLANNED

### Goal
Link an Indemnitor to a Defendant with confidence scoring and human validation.

### What Gets Built
- `Match` model
- `matches` collection in MongoDB
- Matching logic (defendant name/DOB vs intake data)
- Confidence scoring (0.0–1.0)
- Low-confidence → `needs_review` status
- Human validation UI in dashboard

### Dependencies
- Phase 2 (Defendant)
- Phase 3 (Indemnitor)

### Policy
- See `docs/policies/matching-policy.md`

---

## Phase 5: Bond Case + Surety + POA 🔲 PLANNED

### Goal
Create the operational bonded-case record with surety selection and POA assignment from physical inventory.

### What Gets Built
- `BondCase` model
- `Surety` configuration (OSI and Palmetto)
- `POAInventory` model and collection
- POA inventory ingestion (scan surety receipt → OCR/manual entry → populate inventory)
- Surety selection logic
- POA assignment from correct surety's available inventory
- Premium calculation (10% standard FL rate)
- Premium split calculation:
  - OSI: $7.50 per $100 premium to surety + $5.00 per $100 to BUF
  - Palmetto: $10.00 per $100 premium to surety + $5.00 per $100 to BUF

### Dependencies
- Phase 4 (validated Match required)

### Policy
- See `docs/policies/surety-policy.md`

---

## Phase 6: Paperwork Generation 🔲 PLANNED

### Goal
Generate surety-specific paperwork packets using the correct template set.

### What Gets Built
- `DocumentPacket` model
- Template selection by `Surety_ID`
- Field hydration from BondCase + Defendant + Indemnitor
- SignNow template copy + field fill
- Packet versioning (regenerate, never mutate)

### Dependencies
- Phase 5 (BondCase with Surety + POA)

### Policy
- See `docs/policies/signature-policy.md`

---

## Phase 7: Signature Orchestration 🔲 PLANNED

### Goal
Send packets for e-signature via SignNow and track completion.

### What Gets Built
- SignNow embedded invite link generation
- Delivery via SMS/Telegram/WhatsApp
- `document.complete` webhook handler
- Signed PDF auto-save to Google Drive
- Slack alert on completion

### Dependencies
- Phase 6 (DocumentPacket generated)

---

## Phase 8: Payment Collection 🔲 PLANNED

### Goal
Collect bond premium from the validated indemnitor.

### What Gets Built
- `PaymentRequest` model
- SwipeSimple payment link generation
- Payment status tracking
- Delinquency flagging (>30 days)
- Payment plan management

### Dependencies
- Phase 5 (BondCase with premium calculated)

---

## Phase 9: Contact Discovery (OSINT) 🔲 PLANNED

### Goal
Find potential indemnitors for defendants who haven't had intake yet.

### What Gets Built
- Public records search (voter registration, property records)
- Social media profile discovery
- Confidence-scored contact leads
- Human review before any outreach

### Dependencies
- Phase 2 (Defendant records)
- Legal review required before implementation

---

## Phase 10: Outreach Sequencing 🔲 PLANNED

### Goal
Automated but human-approved drip campaigns to potential indemnitors.

### What Gets Built
- SMS/WhatsApp drip sequences
- Opt-out management
- Campaign performance tracking
- 10DLC compliance

### Dependencies
- Phase 9 (Contact discovery)
- Human approval gate for each campaign
