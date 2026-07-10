# ROADMAP.md — ShamrockLeads Phase Progression

> **Purpose:** Define what exists vs what is coming. Every agent must check this before writing code.  
> **Last Updated:** 2026-07-10 · Authoritative truth: [`STATUS.md`](./STATUS.md)  
> **Read `BRAND.md` first.** Platform: [`docs/PLATFORM.md`](./docs/PLATFORM.md) · Prod: [`docs/ECOSYSTEM_PROD_CHECKLIST.md`](./docs/ECOSYSTEM_PROD_CHECKLIST.md)

## Phase Overview

| Phase | Name | Status |
|-------|------|--------|
| 1 | Scrape → Score → Alert | ✅ Complete |
| 1b | County Expansion (52 scraper files / 67 total) | ✅ Complete |
| 2 | Defendant Normalization + Contact Discovery | ✅ Complete |
| 3 | Intake Ingestion (all sources) | ✅ Complete |
| 4 | Matching Engine | ✅ Complete |
| 5 | Bond Case + Surety + POA | ✅ Complete |
| 6 | Paperwork Generation | ✅ Complete |
| 7 | Signature Orchestration (SignNow) | ✅ Complete |
| 8 | Payment Collection (SwipeSimple) | ✅ Complete |
| 9 | Contact Discovery (OSINT) | ✅ Complete |
| 10 | Outreach Sequencing (iMessage / BlueBubbles) | ✅ Code · ⏳ BB office reliability ops |
| 11 | Bond Tracker — Location Intelligence | ✅ Complete (separate repo) |
| 12 | BlueBubbles Enhancement Suite | ✅ Code · ⏳ production tunnel ops |
| 13 | Bond Lifecycle Kanban + POA Automation | ✅ Complete |
| 14 | Court Automation + Discharge Monitoring | ✅ Complete |
| 15 | Intelligence Dashboard Overhaul | ✅ Complete |
| 16 | Social Media Command Center (Postiz) | ✅ Complete |
| 17 | Super CRM hub APIs + secrets hygiene | ✅ Complete (July 2026) |
| 18 | True phone→autopilot state machine (explicit human gates) | 🔲 Next product focus |

---

## Phase 1: Scrape → Score → Alert ✅ COMPLETE

20 county scrapers running on APScheduler with self-healing `BaseScraper` (retry, auto-disable, error classification), lead scoring (0–100, Hot/Warm/Cold/Disqualified), MongoDB Atlas storage (upsert by County + Booking_Number), real-time Slack alerts for hot leads, and Docker deployment on Hetzner VPS.

---

## Phase 1b: County Expansion ✅ COMPLETE

Expanded from 20 to **52 county scraper files** across Florida. All scrapers are registered in `main.py` with per-county intervals.

**Scraper file count:** 52 files in `scrapers/counties/` (excluding `__init__.py`).

**Scraper strategies:**
| Strategy | Library | Counties |
|----------|---------|----------|
| Browser Automation | DrissionPage | Charlotte, Hillsborough, Manatee, Pinellas, Volusia, Pasco + more |
| Stealth Browser | Patchright | Sarasota |
| TLS Fingerprint | curl_cffi | Collier, Hendry |
| Standard HTTP | requests + BS4 | Lee, DeSoto, Brevard, Escambia, Orange, Polk + more |

**Shared base classes:**
| Base Class | Platform | Counties |
|-----------|----------|----------|
| `P2CBaseScraper` | Police-to-Citizen (CentralSquare) | Clay, Marion, Alachua, Putnam |
| `SmartCOPBaseScraper` | SmartCOP Solutions | Columbia, Dixie, Gadsden, Glades, Hardee, Jackson, Suwannee, Taylor + more |

**Remaining 15 counties:** Small rural counties (🟡 Needs URL recon). See `docs/COUNTY_REGISTRY.md`.

---

## Phase 2: Defendant Normalization + Contact Discovery ✅ COMPLETE

- `dashboard/services/defendant_normalizer.py` — Dedup, fuzzy matching, merging
- `dashboard/api/defendants.py` — Defendants API (CRUD, search, normalization, merge)
- `writers/mongo_writer.py` — Auto-normalization hook on new arrest
- `dashboard/api/contacts.py` — Contact discovery API
- `dashboard/services/contact_discovery.py` — OSINT contact discovery

---

## Phase 3: Intake Ingestion ✅ COMPLETE

All intake sources handled natively in the FastAPI dashboard.

- `dashboard/api/intake.py` — 7 endpoints (submit, queue, hydrate, archive, process, manual, stats)
- `dashboard/api/webhooks.py` — Wix intake webhook
- `dashboard/sl-intake.js` — Frontend module

**Intake Sources:** Wix Portal, Telegram Mini App, Walk-In/Phone, Bookmarklet

---

## Phase 4: Matching Engine ✅ COMPLETE

- `dashboard/services/matching_engine.py` — 4-strategy pipeline (exact booking, fuzzy name+DOB, county+name, defendant_id)
- `dashboard/api/matching.py` — Match, confirm, override, candidate endpoints
- Auto-match fires on every intake submission

---

## Phase 5: Bond Case + Surety + POA ✅ COMPLETE

- `dashboard/api/bonds.py` — Write Bond, active bonds management, 7-status PATCH, status history
- `dashboard/api/poa.py` — POA inventory, next available, assign, reassign
- `dashboard/services/poa_service.py` — POA tier logic
- `dashboard/api/bond_lifecycle.py` — Lifecycle hooks, SignNow webhook, court email processing

---

## Phase 6: Paperwork Generation ✅ COMPLETE

- `dashboard/api/paperwork.py` — Generate, deliver, list packets
- `dashboard/services/signnow_packet_service.py` — PDF field hydration + SignNow delivery
- BlueBubbles delivery — sends PDF link to indemnitor phone via iMessage

---

## Phase 7: Signature Orchestration (SignNow) ✅ COMPLETE

- `dashboard/services/signnow_service.py` — SignNow API wrapper
- `dashboard/services/signnow_packet_service.py` — Packet creation, status polling
- `dashboard/api/bond_lifecycle.py` — `/api/bond-lifecycle/initiate-signing`, `/api/bond-lifecycle/signnow-webhook`

---

## Phase 8: Payment Collection ✅ COMPLETE

- `dashboard/api/payments.py` — Payment log + history
- `dashboard/api/payment_plans.py` — Payment plan management
- SwipeSimple integration for one-click payment links

---

## Phase 9: Contact Discovery (OSINT) ✅ COMPLETE

- `dashboard/services/contact_discovery.py` — OSINT contact discovery
- `dashboard/api/contacts.py` — `/api/contacts/discover`

---

## Phase 10: Outreach Sequencing (iMessage / BlueBubbles) ✅ COMPLETE

- Multi-server BlueBubbles config (`BB_SERVERS`, `_BB_URL_OVERRIDES`)
- `dashboard/services/bb_client.py` — BlueBubbles REST client
- `dashboard/api/legacy.py` — iMessage send/status/history/templates
- `dashboard/api/imessage_automation.py` — AI-powered message automation
- `dashboard/api/agent_brain.py` — Shannon auto-reply agent
- `dashboard/services/outreach_sequencer.py` — Drip campaign sequencing
- `dashboard/services/twilio_service.py` — Twilio SMS fallback

---

## Phase 11: Bond Tracker — Location Intelligence ✅ COMPLETE

Separate repo: [`shamrock-bond-tracker`](https://github.com/Shamrock2245/shamrock-bond-tracker)

IP-based location tracking, MaxMind GeoLite2, risk scoring (0–100), Twilio SMS webhook, Leaflet dashboard.

---

## Phase 12: BlueBubbles Enhancement Suite ✅ COMPLETE

- `dashboard/api/bb_private_api.py` — Extended webhook, group chats, diagnostics
- `dashboard/api/bb_webhook_receiver.py` — Real-time event receiver (replaces polling)
- `dashboard/api/bb_prospecting.py` — iMessage-first prospecting
- `dashboard/api/bb_scheduled_messages.py` — Court/payment reminders via BB
- `dashboard/api/bb_document_delivery.py` — PDFs and signing links via iMessage
- `dashboard/api/bb_contact_sync.py` — Sync Mac Contacts with MongoDB
- `dashboard/api/bb_health_monitor.py` — Server health + Slack alerts
- `dashboard/api/bb_firebase_sync.py` — Firebase Firestore URL auto-sync
- `dashboard/sl-imessage.js` + `sl-imessage.css` — Full iMessage dashboard tab
- ngrok permanent tunnel (static domain) for stable connectivity

---

## Phase 13: Bond Lifecycle Kanban + POA Automation ✅ COMPLETE

- `sl-active-bonds.js` — `SLKanban` module: 7-status drag-and-drop columns
- Destructive confirmation modals for Forfeited/Surrendered transitions
- Automatic POA release on Exonerated/Forfeited/Surrendered
- `status_history[]` tracking + `audit_events` logging on every transition
- POA Quick-Swap modal for reassigning POAs between bonds
- Table/Kanban view toggle
- Mobile scroll-snap for Kanban columns

---

## Phase 14: Court Automation + Discharge Monitoring ✅ COMPLETE

- `dashboard/services/court_reminder_service.py` — Auto-scan + Twilio SMS at 7d/3d/1d
- `dashboard/services/court_email_processor.py` — Court email parsing
- `dashboard/api/discharge_monitor.py` — Gmail OAuth2 discharge scanner
- `dashboard/api/calendar.py` — Google Calendar court date sync
- `dashboard/api/rearrest_detector.py` — Cross-reference new arrests vs active bonds
- `dashboard/api/rearrest_notifier.py` — Alert on re-arrests
- `dashboard/api/data_retention.py` — Tiered purge for M0 512MB limit

---

## Phase 15: Intelligence Dashboard Overhaul ✅ COMPLETE

- `sl-overhaul.js` + `sl-overhaul.css` — Command Palette (Ctrl+K), Toast system, County badges, KPI animations
- `sl-analytics.js` + `sl-analytics-apex.js` — ApexCharts (revenue sparkline, treemap, risk heatmap)
- `sl-calendar.js` + `sl-calendar-ext.js` — Court calendar with Vanilla Calendar Pro
- `sl-reports.js` + `sl-reports-ui.js` — Reports module
- `sl-notifications.js` — Notification center with bell icon
- `sl-design-system.css` — Unified design tokens
- `sl-tab-polish.js` — Tab transition polish
- `sl-animations.js` — Micro-animations
- `sl-refinements.js` — UX refinements

---

## Phase 16: Social Media Command Center (Postiz) ✅ COMPLETE

- Self-hosted Postiz instance (`social.shamrockbailbonds.biz`) running on Docker
- Social Engine API (port 5060) for AI-powered content repurposing
- SSL / reverse proxy configured for secure social integrations
- Integration with Temporal, Postgres, and Redis for workflow orchestration
- Frontend command center via `sl-social.js`

---

## 🛡️ Compliance & Brand Standards

- **SOC II Readiness**: All data flows (MongoDB, SignNow, Twilio) must meet SOC II standards.
- **Brand Exclusivity**: All work is exclusively `Shamrock2245`. Never reference WTF.
- **Strategic Goal**: Scale from $3–5M/year (Lee County) to $20–50M/year (67 counties statewide).
- **Competitor Benchmark**: Captira and Bail Books are the floor, not the ceiling.

---

## Known Gaps / Next Actions

| Item | Priority | Notes |
|------|----------|-------|
| Marion County scraper | Low | File exists — needs validation |
| Miami-Dade scraper | Low | reCAPTCHA blocks; use ArcGIS daily dataset |
| 16 rural counties | Low | Needs URL recon before scraper can be built |
| WhatsApp Business | Medium | Twilio WhatsApp sandbox → Node-RED relay |
| TTL index on audit_events | Medium | Add `expireAfterSeconds: 7776000` (90 days) |
| Nginx SSL cert auto-renewal | Low | Certbot cron for `leads.shamrockbailbonds.biz` |
