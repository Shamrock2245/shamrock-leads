# ROADMAP.md — ShamrockLeads Phase Progression

> **Purpose:** Define what exists vs what is coming. Every agent must check this before checking in.
> **Last Updated:** 2026-04-27

## Phase Overview

| Phase | Name | Status |
|-------|------|--------|
| 1 | Scrape → Score → Alert (20 counties) | ✅ Complete |
| 1b | County Expansion (49 active / 67 total) | ✅ Complete |
| 2 | Defendant Normalization + Contact Discovery | ✅ Complete |
| 3 | Intake Ingestion (all sources) | ✅ Complete |
| 4 | Matching Engine | 🔲 Planned |
| 5 | Bond Case + Surety + POA | ✅ Complete (API + service layer) |
| 6 | Paperwork Generation | 🔲 Planned |
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
- `dashboard/api/defendants.py` — Defendants API blueprint (CRUD, search, normalization)
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

## Phase 4: Matching Engine 🔲 PLANNED

Match incoming intake records against existing `ArrestLead` records in MongoDB using County + Booking Number + name fuzzy match. Auto-link indemnitor to defendant record when confidence > threshold.

**Planned components:**
- `dashboard/services/matching_engine.py` — fuzzy match + confidence scoring
- `dashboard/api/matching.py` — `/api/match/intake/<id>` endpoint
- UI: "Match" button in Intake Queue process modal

---

## Phase 5: Bond Case + Surety + POA ✅ COMPLETE

**Implemented:**
- `dashboard/api/bonds.py` — Write Bond, active bonds, bond search (full indemnitor schema)
- `dashboard/api/poa.py` — POA inventory (`/api/poa/next`, `/api/poa/assign`, `/api/poa/inventory`)
- `dashboard/services/poa_service.py` — POA tier logic
- `dashboard/api/bond_lifecycle.py` — Bond lifecycle (Phase 1 indemnitor signing, Phase 2 agent approval, SignNow webhook, court email processing)

---

## Phase 6: Paperwork Generation 🔲 PLANNED

Auto-generate bail bond application PDF, indemnitor agreement, and receipt from bond record. Merge with SignNow template for e-signature.

**Planned components:**
- `dashboard/services/paperwork_generator.py` — PDF generation (ReportLab / WeasyPrint)
- `dashboard/api/paperwork.py` — `/api/paperwork/generate/<bond_id>`

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

## Known Gaps / Next Actions

| Item | Priority | Notes |
|------|----------|-------|
| Phase 4 Matching Engine | High | Link intake records to ArrestLeads automatically |
| Phase 6 Paperwork Generation | Medium | PDF generation for bond application + indemnitor agreement |
| Marion County scraper | Medium | File exists, commented out — needs validation |
| Miami-Dade scraper | Low | reCAPTCHA blocks form scraping; use ArcGIS daily dataset |
| 16 rural counties | Low | Needs URL recon before scraper can be built |
| `GAS_WEB_APP_URL` env var | High | Must be set on VPS for write-bond to forward to GAS |
| `WIX_WEBHOOK_SECRET` env var | High | Must be set on VPS + Velo for intake webhook auth |
| Wix Velo webhook config | High | Point CMS webhook to `https://[vps]/api/webhooks/wix-intake` |
