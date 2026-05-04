# ROADMAP.md — ShamrockLeads Phase Progression

> **Purpose:** Define what exists vs what is coming. Every agent must check this before checking in.
> **Last Updated:** 2026-05-04
> **Read `BRAND.md` first** — it defines who we are, what we're building, and the non-negotiable standards every agent must follow.

## Phase Overview

| Phase | Name | Status |
|-------|------|--------|
| 1 | Scrape → Score → Alert (20 counties) | ✅ Complete |
| 1b | County Expansion (49 active / 67 total) | ✅ Complete |
| 2 | Defendant Normalization + Contact Discovery | ✅ Complete |
| 3 | Intake Ingestion (all sources) | ✅ Complete |
| 4 | Matching Engine | ✅ Complete |
| 5 | Bond Case + Surety + POA | ✅ Complete (API + service layer) |
| 6 | Paperwork Generation | ✅ Complete |
| 7 | Signature Orchestration (SignNow) | ✅ Complete (service layer + webhook) |
| 8 | Payment Collection | ✅ Complete (log + history API) |
| 9 | Contact Discovery (OSINT) | ✅ Complete (service layer) |
| 10 | Outreach Sequencing (iMessage / BlueBubbles) | ✅ Complete (multi-server BB integration) |
| 11 | Bond Tracker — Location Intelligence | ✅ Complete (separate repo) |

---

## Phase 1: Scrape → Score → Alert ✅ COMPLETE

20 county scrapers running on APScheduler with self-healing `BaseScraper` (retry, auto-disable, error classification), lead scoring (0–100, Hot/Warm/Cold/Disqualified), MongoDB Atlas storage (upsert by County + Booking_Number), real-time Slack alerts for hot leads, and Docker deployment on Hetzner VPS.

---

## Phase 1b: County Expansion ✅ COMPLETE

Expanded from 20 to **49 active scrapers** across 9 regional tiers covering Florida. All 50 implemented county scrapers are registered in `main.py`. Marion is the only file that exists but is currently commented out pending validation.

**Scrapers added since Phase 1:** Alachua, Bay, Citrus, Clay, Columbia, Dixie, Flagler, Gadsden, Glades, Hardee, Hernando, Highlands, Indian River, Jackson, Lake, Leon, Martin, Manatee (Revize), Monroe, Nassau, Okaloosa, Okeechobee, Osceola, Palm Beach, Pasco, Pinellas, Polk, Putnam, Santa Rosa, Sarasota (Revize), Seminole, St. Johns, St. Lucie, Sumter, Suwannee, Taylor, Walton.

**Remaining 17 counties** not yet scraped: Miami-Dade (🔴 Blocked — reCAPTCHA; ArcGIS dataset alternative identified), plus 16 small rural counties (🟡 Needs Recon). See `docs/COUNTY_REGISTRY.md` for full status.

Dashboard (`dashboard/index.html`) shows all 67 counties with live status, risk badges, and source links.

---

## Phase 2: Defendant Normalization + Contact Discovery ✅ COMPLETE

**Implemented:**
- `dashboard/services/defendant_normalizer.py` — Core normalization service (dedup, fuzzy matching, merging)
- `dashboard/api/defendants.py` — Defendants API blueprint (CRUD, search, normalization hooks, merge)
- `writers/mongo_writer.py` — Automatic normalization hook on new arrest ingestion
- `scripts/setup_defendant_indexes.py` — MongoDB index setup for `defendants` and `audit_events`
- `dashboard/api/contacts.py` — Contact discovery API (`/api/contacts/discover`)
- `dashboard/services/contact_discovery.py` — OSINT contact discovery service
- `dashboard/api/court_reminders.py` — Court reminder API
- `dashboard/services/court_reminder_service.py` — Court reminder scheduling service
- `dashboard/services/google_calendar_service.py` — Google Calendar integration

---

## Phase 3: Intake Ingestion ✅ COMPLETE

All indemnitor intake sources from the legacy GAS `Dashboard.html` are now handled natively in the Quart dashboard.

**Implemented:**
- `dashboard/api/intake.py` — Intake Queue blueprint (7 endpoints)
  - `POST /api/intake/submit` — accepts all sources (Wix portal, Telegram, walk-in, phone, manual, bookmarklet)
  - `GET /api/intake/queue` — paginated queue with source/status filters
  - `GET /api/intake/<id>` — hydrate single intake record
  - `POST /api/intake/<id>/archive` — mark done
  - `POST /api/intake/<id>/process` — mark in-progress
  - `POST /api/intake/manual` — staff manual entry
  - `GET /api/intake/stats` — counts by source + status
- `dashboard/api/webhooks.py` — Wix intake webhook (`/api/webhooks/wix-intake`, `/api/webhooks/wix-intake-update`)
- `dashboard/sl-intake.js` — Frontend `SLIntake` module (Intake Queue tab, process modal, manual entry modal)
- `dashboard/index.html` — Intake Queue tab with stats row, filterable table, process/archive/write-bond actions

**Intake Sources:**
| Source | Delivery Method |
|--------|----------------|
| Wix Portal | `POST /api/webhooks/wix-intake` (Velo push) |
| Telegram Mini App | `POST /api/intake/submit` with `source: telegram_mini_app` |
| Walk-In / Phone | Manual Entry modal in dashboard |
| Bookmarklet | `POST /api/intake/submit` with `source: bookmarklet` |

---

## Phase 4: Matching Engine ✅ COMPLETE

Matches incoming intake records against existing `ArrestLead` records in MongoDB using multi-strategy fuzzy matching. Auto-links indemnitor to defendant record when confidence ≥ 85. Triggers automatically on every new intake submission.

**Implemented:**
- `dashboard/services/matching_engine.py` — 4-strategy matching pipeline: (1) exact booking number + county, (2) fuzzy name + DOB (Levenshtein ≤ 2), (3) county + name only, (4) defendant_id direct link; confidence scoring 0–100; auto-link at ≥ 85
- `dashboard/api/matching.py` — `POST /api/match/intake/<id>`, `POST /api/match/intake/<id>/confirm`, `POST /api/match/intake/<id>/override`, `GET /api/match/candidates/<booking_number>/<county>`
- `dashboard/api/intake.py` — `POST /api/intake/<id>/match` endpoint; auto-match fires on every `POST /api/intake/submit`

---

## Phase 5: Bond Case + Surety + POA ✅ COMPLETE

**Implemented:**
- `dashboard/api/bonds.py` — Write Bond, active bonds, bond search (full indemnitor schema)
- `dashboard/api/poa.py` — POA inventory (`/api/poa/next`, `/api/poa/assign`, `/api/poa/inventory`)
- `dashboard/services/poa_service.py` — POA tier logic
- `dashboard/api/bond_lifecycle.py` — Bond lifecycle (Phase 1 indemnitor signing, Phase 2 agent approval, SignNow webhook, court email processing)

---

## Phase 6: Paperwork Generation ✅ COMPLETE

Auto-generates bail bond application PDF, indemnitor agreement, and receipt from intake/bond record. Delivers completed packet via BlueBubbles iMessage. Integrates with SignNow for e-signature.

**Implemented:**
- `dashboard/api/paperwork.py` — `POST /api/paperwork/generate/<intake_id>`, `GET /api/paperwork/packet/<packet_id>`, `POST /api/paperwork/deliver/<packet_id>`, `GET /api/paperwork/packets`
- `dashboard/services/bond_pdf_service.py` — PDF field hydration (existing)
- `paperwork_packets` MongoDB collection — full audit trail per packet
- BlueBubbles delivery — `send_attachment_url` sends PDF link to indemnitor phone
- SignNow integration — packet delivery triggers SignNow invite via `signnow_packet_service.py`

---

## Phase 7: Signature Orchestration (SignNow) ✅ COMPLETE

**Implemented:**
- `dashboard/services/signnow_service.py` — SignNow API wrapper
- `dashboard/services/signnow_packet_service.py` — Packet creation, status polling, webhook handling
- `dashboard/api/bond_lifecycle.py` — `/api/bond-lifecycle/initiate-signing`, `/api/bond-lifecycle/signnow-webhook`

---

## Phase 8: Payment Collection ✅ COMPLETE

**Implemented:**
- `dashboard/api/payments.py` — Payment log + history (`/api/payments/log`, `/api/payments/history`)
- Payments stored in MongoDB `payments` collection with SSE event on new payment

---

## Phase 9: Contact Discovery (OSINT) ✅ COMPLETE

**Implemented:**
- `dashboard/services/contact_discovery.py` — OSINT contact discovery service
- `dashboard/api/contacts.py` — `/api/contacts/discover` endpoint

---

## Phase 10: Outreach Sequencing (iMessage / BlueBubbles) ✅ COMPLETE

**Implemented:**
- `dashboard/extensions.py` — Multi-server BlueBubbles config (`BB_SERVERS`, `BB_CONFIG_API_KEY`, `_BB_URL_OVERRIDES`)
- `dashboard/api/legacy.py` — iMessage endpoints:
  - `POST /api/imessage/send` — send iMessage via BlueBubbles server
  - `GET /api/imessage/status` — server health check
  - `GET /api/imessage/history/<booking_number>` — message history
  - `GET /api/imessage/templates` — message templates
  - `POST /api/config/bluebubbles-url` — runtime URL sync (BlueBubbles integration)
- `dashboard/services/twilio_service.py` — Twilio SMS fallback

---

## Phase 11: Bond Tracker — Location Intelligence ✅ COMPLETE

Implemented in a separate repo: [`shamrock-bond-tracker`](https://github.com/Shamrock2245/shamrock-bond-tracker)

This system provides discreet IP-based location tracking for active bail bonds. When a defendant sends an SMS to the Twilio number, the system extracts the public IP from the message, geolocates it via MaxMind GeoLite2, scores it for flight risk (0–100), and stores the result in MongoDB. High-risk events trigger Slack alerts and generate RiskFlag records for agent review.

**Components:**
- `tracker/` — models, geolocator, risk engine, MongoDB writer, end-to-end processor
- `api/main.py` — FastAPI: Twilio webhook, bond CRUD, flags, stats
- `dashboard/index.html` — Leaflet map + timeline + risk flags (dark theme, auto-refresh)
- `gas/LocationSync.gs` — GAS script syncing location data to Google Sheets every 5 min
- `docker-compose.yml` — mongo + tracker API + MaxMind auto-updater

**Risk levels:** LOW (0–24) / MEDIUM (25–59) / HIGH (60–84) / CRITICAL (85–100)

**Automatic flags:** `TOR_DETECTED`, `VPN_DETECTED`, `PROXY_DETECTED`, `COUNTRY_JUMP`, `STATE_JUMP`, `DISTANCE_CRITICAL`, `RAPID_MOVEMENT_CRITICAL`

**Integration point:** `County + Booking_Number` links a `BondedCase` in the tracker back to an `ArrestLead` in shamrock-leads. See `DATA_MODEL.md` for the full entity spec.

---

## 🛡️ Compliance & Brand Standards

- **SOC II Readiness**: All data flows (MongoDB, SignNow, Twilio, GAS) must meet SOC II compliance standards. Reference: [strongdm/comply](https://github.com/strongdm/comply), [getprobo/probo](https://github.com/getprobo/probo).
- **Brand Exclusivity**: All work is exclusively in the `Shamrock2245` GitHub account. Never reference or use WTF or non-Shamrock identities.
- **Strategic Goal**: Scale from $3–5M/year (Lee County) to $20–50M/year (67 counties statewide).
- **Competitor Benchmark**: Captira and Bail Books are the floor, not the ceiling.

## Known Gaps / Next Actions

| Item | Priority | Notes |
|------|----------|-------|
| Phase 4 Matching Engine | ✅ Done | Implemented: matching_engine.py + matching.py + intake auto-match |
| Phase 6 Paperwork Generation | ✅ Done | Implemented: paperwork.py + BB delivery + SignNow integration |
| Marion County scraper | Medium | File exists, commented out — needs validation |
| Miami-Dade scraper | Low | reCAPTCHA blocks form scraping; use ArcGIS daily dataset |
| 16 rural counties | Low | Needs URL recon before scraper can be built |
| `GAS_WEB_APP_URL` env var | High | Must be set on VPS for write-bond to forward to GAS |
| `WIX_WEBHOOK_SECRET` env var | High | Must be set on VPS + Velo for intake webhook auth |
| Wix Velo webhook config | High | Point CMS webhook to `https://[vps]/api/webhooks/wix-intake` |


---

## Phase 11: BlueBubbles Enhancement Suite (2026-04-28)

This phase dramatically expands the BlueBubbles / iMessage automation capabilities
across the entire bail bond lifecycle — from first outreach to re-arrest follow-up.

### New Modules

| Module | Purpose |
|--------|---------|
| bb_private_api.py (extended) | Webhook CRUD, group chats, iMessage check, scheduled messages, attachments, contacts, diagnostics |
| bb_webhook_receiver.py | Real-time event receiver (replaces 30s polling loop) |
| rearrest_notifier.py | Re-arrest detection + indemnitor notification (The Loyalty Flow) |
| bb_prospecting.py | iMessage-first prospecting outreach (The First Mover) |
| bb_scheduled_messages.py | Court/payment reminders via BB server-side scheduling |
| bb_document_delivery.py | Send PDFs, signing links, receipts via iMessage |
| bb_contact_sync.py | Sync Mac Contacts.app with MongoDB |
| bb_health_monitor.py | Server health checks + Slack alerts |

### Architecture Upgrade: Polling to Webhooks
Before: VPS polls BB every 30s — ~15s average latency
After:  BB pushes to VPS instantly — <1s latency
Polling loop retained as fallback.

### New Environment Variables
BB_WEBHOOK_PUBLIC_URL — VPS public URL for webhook registration
BB_WEBHOOK_SECRET     — Optional HMAC secret for webhook verification
SLACK_WEBHOOK_URL     — Slack webhook for BB health alerts
SLACK_CHANNEL         — Slack channel (default: #shamrock-alerts)
