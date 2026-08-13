# ROADMAP.md — ShamrockLeads Phase Progression

> **Purpose:** Define what exists vs what is coming. Every agent must check this before writing code.  
> **Last Updated:** 2026-08-04 · Authoritative truth: [`STATUS.md`](./STATUS.md)  
> **Read `BRAND.md` first.** Platform: [`docs/PLATFORM.md`](./docs/PLATFORM.md) · Prod: [`docs/ECOSYSTEM_PROD_CHECKLIST.md`](./docs/ECOSYSTEM_PROD_CHECKLIST.md) · Multi-state: [`docs/MULTI_STATE_SCRAPER_ROADMAP.md`](./docs/MULTI_STATE_SCRAPER_ROADMAP.md)

## Phase Overview

| Phase | Name | Status |
|-------|------|--------|
| 1 | Scrape → Score → Alert | ✅ Complete |
| 1b | FL County Expansion (67 registered / 67 goal) | ✅ Complete (core market) |
| 1c | GA County Expansion (74 registered / 159 goal) | 🔄 In Progress |
| 1d | SC County Expansion (46/46 registered) | ✅ Registered · ⏳ production depth |
| 1e | NC Expansion (47 registered / 100 goal) | ✅ Waves 1–7 code · ⏳ production depth + remaining counties |
| 1f | Multi-State Ops dashboard (FL/GA/SC/NC/TN/TX/LA/CT/AL/MS) | ✅ Complete · registry-first KPIs |
| 1g | TN / TX / CT / LA / MS / AL | ✅ **35** registered (TN9 · TX15 · LA4 · CT2 · AL3 · MS2) |
| 2 | Defendant Normalization + Contact Discovery | ✅ Complete |
| 3 | Intake Ingestion (all sources) | ✅ Complete |
| 4 | Matching Engine | ✅ Complete |
| 5 | Bond Case + Surety + POA | ✅ Complete |
| 6 | Paperwork Generation | ✅ Complete |
| 7 | Signature Orchestration (DocuSeal) | ✅ Complete |
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
| 19 | Automated First Appearance Bond Fill | ✅ Complete (July 2026) |
| 20 | Per-Charge Bond Breakdown & Multi-State Query Engine | ✅ Complete (July 2026) |
| 21 | True phone→autopilot state machine (explicit human gates) | 🔲 Next product focus |

---

## Phase 1: Scrape → Score → Alert ✅ COMPLETE

20 county scrapers running on APScheduler with self-healing `BaseScraper` (retry, auto-disable, error classification), lead scoring (0–100, Hot/Warm/Cold/Disqualified), MongoDB Atlas storage (upsert by County + Booking_Number), real-time Slack alerts for hot leads, and Docker deployment on Hetzner VPS.

---

## Phase 1b: FL County Expansion ✅ COMPLETE (core market)

**Registered:** **67 FL** scrapers in `scrapers/counties/` (legacy job IDs `scraper_<county>`) — full FL coverage on dashboard registry.

**Shared bases:** P2C, SmartCOP, JailTracker, New World, Kologik, Southern SW, etc.

**Remaining FL counties:** rural / no public roster — see `docs/COUNTY_REGISTRY.md`.

---

## Phase 1c: GA County Expansion 🔄 IN PROGRESS

**Registered:** 74 GA scrapers in `scrapers/counties_ga/` (+ EAS batch for rural cluster).

**Bases:** EAS, Zuercher, Southern SW, InteropWeb, SmartCOP, Socrata, XML Feed, Odyssey stubs.

See `docs/GEORGIA_COUNTY_REGISTRY.md`. Goal: all 159 counties over time.

---

## Phase 1d: SC County Expansion ✅ REGISTERED (production depth ongoing)

**Registered:** **46/46** SC counties in `scrapers/counties_sc/` (`scraper_sc_*`).

- Platform wrappers: Zuercher, JailTracker, Southern SW, P2C, New World, SmartCOP
- Custom production paths: Beaufort (XML), Charleston, York, Florence, Horry, Richland, Jasper, Greenville (proxy/CAPTCHA), …
- Scaffolds for no-portal / broken recon URLs (Spartanburg 404, rural counties)

**Docs:** `docs/SC_COUNTY_REGISTRY.md`, `docs/SC_RECON_RESULTS.md`

---

## Phase 1e: NC Expansion ✅ 47 REGISTERED · ⏳ PROD DEPTH + 100-COUNTY GOAL

**Registered:** **47** NC scrapers in `scrapers/counties_nc/` (`scraper_nc_*`) as of 2026-08-04.

| Platform | Examples |
|----------|----------|
| Southern SW | Anson, Duplin, Edgecombe, Harnett, Henderson, … |
| Zuercher | Brunswick, Davie, Hoke, Pender, Rutherford |
| P2C classic | Alamance, Cabarrus, Cleveland, Iredell, Lincoln, … |
| DCN DevExpress | Moore, Lee, Halifax, Richmond, Carteret (`dcn_base.py`) |
| OCV inmates.json | Chatham, Stanly (`ocv_inmates_base.py`) |
| Daily custody PDF | Caldwell, Orange |
| Custom HTML/ASP | Mecklenburg, Durham, Davidson, Gaston, Pitt, Craven, Randolph, Catawba |

**Docs:** `docs/NC_COUNTY_REGISTRY.md`, `docs/NC_RECON_RESULTS.md`  
**Next:** production scrapes for new counties; WAF strategy for cloud P2C metros; DCN page-2; more OCV app_ids; Rowan/Robeson.

---

## Phase 1f: Multi-State Ops Dashboard ✅ COMPLETE

- **Multi-State Ops** tab: `/api/ops/*` registry-first KPIs, live arrest feed (all 10 states)
- **Bond Intelligence** tab: bond analytics by state
- Lead Explorer: state column + filter; `REGISTERED_COUNTIES` labels `County (ST)` (**269** total)
- Run-now triggers emit state-prefixed keys (`nc_mecklenburg`, `sc_lee`, `ct_doc`)
- Scraper Health / stats reflect live `REGISTERED_COUNTIES` counts

---

## Phase 1g: Remaining Palmetto states ✅ REGISTERED (depth ongoing)

| State | Registered | Path | Notes |
|-------|----------:|------|-------|
| TN | 9 | `counties_tn/` | Davidson/Knox live; Shelby TLS; + metros |
| TX | 15 | `counties_tx/` | Bexar/Dallas live; Harris browser; Gulf Coast |
| LA | 4 | `counties_la/` | Orleans/Lafayette/Jefferson/EBR |
| CT | 2 | `counties_ct/` | Statewide dockets + CT DOC (hardened 2026-08-04) |
| AL | 3 | `counties_al/` | Jefferson, Madison, Mobile |
| MS | 2 | `counties_ms/` | Hinds, Jackson |

Order of battle / deepen: see `docs/MULTI_STATE_SCRAPER_ROADMAP.md`.

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
- `dashboard/api/bond_lifecycle.py` — Lifecycle hooks, DocuSeal status handoff, court email processing

---

## Phase 6: Paperwork Generation ✅ COMPLETE

- `dashboard/api/paperwork.py` — Generate, deliver, list packets
- `dashboard/services/docuseal_service.py` — DocuSeal template prefill + submission delivery
- BlueBubbles delivery — sends PDF link to indemnitor phone via iMessage

---

## Phase 7: Signature Orchestration (DocuSeal) ✅ COMPLETE

- `dashboard/services/docuseal_service.py` — DocuSeal API wrapper, template resolution, status polling
- `dashboard/routers/paperwork.py` — `/api/paperwork/packet/finalize`, `/api/paperwork/docuseal/*`
- `dashboard/api/webhooks.py` — `/api/webhooks/docuseal`

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
---

## Phase 19: Automated First Appearance Bond Fill ✅ COMPLETE (July 2026)

- Continuous 24/7 background worker (`FirstAppearanceWatcher`) targeting "In Custody" defendants with unset/$0 bonds across key active counties (Lee, Collier, Charlotte, Sarasota, Manatee, Hendry, DeSoto)
- Runs every 30 mins to re-check county booking systems/APIs and auto-populate newly assigned bond amounts post-hearing
- Lee County API enrichment scale increased to 50 records per run
- Fires Slack notifications when no-bond records graduate to posted bonds

---

## Phase 20: Per-Charge Bond Breakdown & Multi-State Query Engine ✅ COMPLETE (July 2026)

- Structured `charge_details` data model (`[{"charge": "...", "bond_amount": 1500, "bond_type": "Surety", "case_number": "..."}, ...]`)
- `POST /api/leads/update-charge-bonds` endpoint to edit individual charge bonds and auto-recalculate total bond amount and lead score
- Interactive **⚖️ Per-Charge Bonds Modal** on Defendant cards in the UI
- Multi-state query builder supporting all 10 states (FL, GA, SC, NC, TN, TX, LA, CT, AL, MS) and **269** county labels (`County (ST)`)
- Dynamic county selector filtering dropdown options by selected state

---

## 🛡️ Compliance & Brand Standards

- **SOC II Readiness**: All data flows (MongoDB, DocuSeal, Twilio) must meet SOC II standards.
- **Brand Exclusivity**: All work is exclusively `Shamrock2245`. Never reference WTF.
- **Strategic Goal**: Scale from $3–5M/year (Lee County) to $50M+/year by dominating the Florida (67 counties) and Georgia (159 counties) markets.
- **Competitor Benchmark**: Captira and Bail Books are the floor, not the ceiling.

---

## Known Gaps / Next Actions

| Item | Priority | Notes |
|------|----------|-------|
| Marion County scraper | Low | File exists — needs validation |
| Miami-Dade scraper | Low | reCAPTCHA blocks; use ArcGIS daily dataset |
| 16 rural counties | Low | Needs URL recon before scraper can be built |
| WhatsApp Business | Medium | Twilio WhatsApp sandbox → Node-RED relay |
| TTL index on audit_events | ✅ Complete | Added `expireAfterSeconds: 7776000` (90 days) |
| Nginx SSL cert auto-renewal | Low | Certbot cron for `leads.shamrockbailbonds.biz` |
