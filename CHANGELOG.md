# ShamrockLeads — Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [2.7.0] — 2026-07-08 (Super CRM hub + security hygiene + docs truth)

### Added
- `/api/crm/health`, `/api/crm/overview`, `/api/crm/pipeline`, `/api/crm/search` — Super CRM hub.
- Omnibar uses CRM search (fallback to match-manager).
- `scripts/check_ecosystem_secrets.py` — cross-repo env presence + shared key fingerprints.
- `docs/SUPER_CRM.md`, `docs/ECOSYSTEM.md`, `STATUS.md`.
- Expanded Mongo indexes for active_bonds, intake, indemnitors, tasks, payments, matches.

### Security
- Scrubbed hardcoded Mongo URIs and BlueBubbles passwords from one-off scripts.
- Removed tracked session cookie dumps; tightened `.gitignore`.
- Wix intake + scraper event webhooks **fail closed** if secrets missing.
- Production can require `DASHBOARD_PIN` / `SECRET_KEY` (no open dashboard by default when configured).

### Docs
- Clarified product boundary: this repo is bond Auto-CRM; school LMS is separate.
- Phase 18 roadmap: true phone→autopilot with explicit human gates (next).

### Ops still required
- VPS deploy of this release; BlueBubbles office reliability; rotate any previously leaked credentials.

---

## [2.6.0] — 2026-05-27 (Dashboard Nesting Fix + Surety Normalization + Doc Refresh)

### Fixed — Dashboard

- **Tab nesting bug (Command Center)** — Removed 4 orphan HTML lines (duplicate "No repeat offender alerts" block) that prematurely closed `#tabCommand`, causing the Bond-Ready Queue, In-Custody by County, and Activity Feed to render on every tab instead of just Command Center.
- **Tab nesting bug (Analytics)** — Removed extra `</div>` in the Analytics tab's County Performance Table panel that prematurely closed `.container`, causing the ApexCharts row (Sparkline, Treemap, Risk Heatmap) to float outside the tab. This also threw off div depth for all 11 subsequent tabs (Intelligence through Enrichment), rendering them at depth 0 instead of 1.
- **Surety normalization** — Fixed `$switch` + `$regexMatch` aggregation in `analytics.py` and `reports.py` to map all "OSI" and "Palmetto" variants (case-insensitive) to canonical surety names. Prevents "osi" and "OSI" from appearing as separate sureties in analytics.

### Fixed — Scrapers

- **Sarasota County scraper** — Fixed `scrape()` to properly navigate to the "Current Inmates" tab, handle AJAX-loaded table data, parse the detail-page link structure, and extract all 39 ArrestRecord fields.

### Changed — Documentation

- **`README.md`** — Complete rewrite with accurate metrics: 52 scrapers, 66 API modules, 45 services, 45 JS modules, 9 CSS files, 21 dashboard tabs, 36 agent skills. Updated architecture diagram, project structure, codebase metrics table, and all tab descriptions.
- **`GEMINI.md`** — Updated all codebase metrics to current counts.
- **`ROADMAP.md`** — Updated scraper count (51→52), corrected remaining counties (17→15).
- **`CHANGELOG.md`** — Added v2.6.0 entry (this).

### Verified

- HTML nesting: all 21 `tab-content` divs open at depth 1, final depth 0, zero negative-depth lines.
- Zero duplicate HTML IDs across 718 unique IDs.
- All 42 local script references resolve to existing files.
- All 66 router files + 45 service files compile cleanly (zero syntax errors).
- All 9 CSS files have balanced braces.

### Metrics Standardized

All documentation now consistently references: 52 scrapers · 66 API modules · 45 services · 45 JS modules · 21 dashboard tabs · 36 agent skills · 16 MongoDB collections

---

## [2.5.0] — 2026-05-15 (Documentation Suite Standardization)

### Added — Documentation

- **`CONTRIBUTING.md`** — Development workflow, code conventions (Python/JS/CSS), commit format, PR process, deployment guide
- **`docs/README.md`** — Documentation index mapping all 30+ docs to purpose and audience
- **`docs/ARCHITECTURE.md`** — System architecture: Docker services, data flow diagrams, external integrations, security model, codebase metrics
- **`docs/API_REFERENCE.md`** — REST API reference covering 200+ endpoints across 61 API modules
- **`docs/DEPLOYMENT.md`** — Production operations: 3 deploy methods, Docker ops, Nginx, health checks, troubleshooting, backup/recovery
- **Agent docs (6 new):** `analyst-agent.md`, `watchdog-agent.md`, `discharge-monitor-agent.md`, `outreach-agent.md`, `court-clerk-agent.md`, `shannon-agent.md`, `rearrest-detector-agent.md`, `contact-finder-agent.md`, `data-retention-agent.md` — all 15 agents now have dedicated documentation

### Changed — Documentation

- **`README.md`** — Complete rewrite: accurate metrics (51 scrapers, 61 API modules, 36 services, 42 JS modules, 15 tabs), updated architecture diagram with Traccar GPS, comprehensive project structure tree
- **`SECURITY.md`** — Complete rewrite: secrets management, PII protection, authentication, network security, audit trails, scraping ethics, data retention, incident response
- **`AGENTS.md`** — Updated metrics (49→61 API, 21→36 services, 32→42 JS), restored architecture diagram, added Traccar
- **`GEMINI.md`** — Updated all metrics, added Traccar Docker service row
- **`ROADMAP.md`** — Updated scraper count (50→51), updated timestamp
- **`DATA_MODEL.md`** — Updated timestamp

### Archived

- Moved stale root-level docs to `docs/archive/2026-05/`: `Antigravity_Handoff_May06.md`, `BlueBubblesApp_Recommendations.md`, `DEPLOY_COMMANDS.md`, `DEPLOY_NOTES.md`

### Metrics Standardized

All documentation now consistently references: 51 scrapers · 61 API modules · 36 services · 42 JS modules · 15 dashboard tabs · 34 agent skills · 16 MongoDB collections

---

## [2.4.0] — 2026-05-08 (Documentation Overhaul + POA Modal Fix)

### Fixed

- **POA Inventory Modal** (`styles.css`) — Fixed CSS specificity conflict where `.inv-overlay:not(.active)` forced `display: none !important`, but JS uses `.show` class. Changed selector to `:not(.show)`. The "Click to manage POA inventory" banner now correctly opens the modal.
- **`.env.example`** — Corrected `BLUEBUBBLES_URL_0178` to actual ngrok permanent tunnel URL (`pseudospherical-etta-untactually.ngrok-free.dev`). Removed incorrect Cloudflare Tunnel references.

### Added — Frontend (via Manus commit `9881188`)

- **Destructive drop confirmation** (`sl-active-bonds.js`) — FORFEITED / SURRENDERED Kanban drops now show a confirmation modal before the API call. Optimistic update reverts on cancel or API failure.
- **Kanban CSS animations** (`sl-overhaul.css`) — Card enter animation with 0.04s per-child stagger, dragging card rotates -1deg, drop zone pulses, alert cards pulse left border, column count badge pops on update.
- **Mobile Kanban** (`sl-overhaul.css`) — Scroll-snap (85vw per column, touch-friendly).
- **Post-save Kanban re-render** (`sl-record-bond.js`) — `SLKanban.render()` called after successful bond save.

### Changed — Documentation

Comprehensive audit and rewrite of all 7 coordination documents to reflect actual codebase state:

- **`GEMINI.md`** — Updated all counts (50 scrapers, 49 API modules, 32 frontend modules, 34 skills, ~25,700 frontend LOC), corrected BlueBubbles to ngrok tunnel.
- **`AGENTS.md`** — Added 3 new agents (Shannon, Re-Arrest Detector, Data Retention), updated all statuses to Live, corrected architecture diagram (Quart not Flask), expanded env vars table.
- **`ROADMAP.md`** — Added Phases 13–15 (Kanban, Court Automation, Dashboard Overhaul), updated all phase descriptions with current file references.
- **`DATA_MODEL.md`** — Complete rewrite with 16 MongoDB collections, full schema definitions, key indexes, and data flow rules.
- **`BRAND.md`** — Updated agent table (all 14 agents Live), added public URL + ngrok tunnel to identity table, corrected frontend LOC.
- **`README.md`** — Major rewrite: 15 tabs (was 10), 32 JS modules (was 11), ~25,700 frontend LOC (was 17,600), 49 API modules (was 30+), 34 skills (was 16), 15 phases all complete, expanded project structure tree.
- **`CHANGELOG.md`** — Added v2.4.0 entry (this).

---

## [2.3.0] — 2026-05-08 (Kanban Board + POA Inline Edit + Status Audit Trail)

### Added — Frontend

- **Bond Kanban Board** (`sl-active-bonds.js` → `SLKanban` IIFE module) — full drag-and-drop view with 6 status columns (Active, Monitoring, Alert, Exonerated, Surrendered, Forfeited). Drag a card to change status; touch-device fallback via tap-and-hold. Toggle between Table and Kanban via the new `☰ Table / ⬛ Kanban` button group in the Active Bonds toolbar.
- **POA Inline Edit** — new `POA` column in the table view shows the current POA number with a `⇄` swap button. Kanban cards also display the POA badge with a swap button.
- **POA Quick-Swap Modal** (`SLKanban.openPoaSwap`) — fetches available POA inventory for the bond's surety, displays a scrollable list of available POAs, and calls `PATCH /api/poa/reassign` on confirm.
- **Status History Modal** (`SLKanban.loadStatusHistory`) — fetches `GET /api/active-bonds/<booking>/status-history` and renders a timeline of all status transitions with timestamp, actor, and optional note.
- **Reinstated status** — added to the status dropdown in the table row and as a Kanban column.
- **View toggle buttons** (`☰ Table` / `⬛ Kanban`) added to the Active Bonds toolbar.
- **Status History button** (`📋 History`) added to each table row's action group.
- **Kanban CSS** appended to `sl-overhaul.css` — columns, cards, drag-over indicators, POA badge, score pills, risk badges, touch-drag fallback, and responsive scroll.

### Added — Backend (`app.py`)

- **`PATCH /api/active-bonds/<booking>/status`** — now appends to `status_history` array (timestamp, old status, new status, actor, note), auto-releases POA inventory on `exonerated`/`surrendered`/`forfeited`, and accepts optional `note` and `actor` fields.
- **`GET /api/active-bonds/<booking>/status-history`** — new endpoint returning the full `status_history` array for a bond.
- **`PATCH /api/poa/reassign`** — enhanced to also clear `poa_number` on the old bond when `old_booking_number` is provided.

### Fixed

- Table `colspan` updated from 13 to 14 to account for the new POA column.
- `SLKanban.setView()` wired to the view toggle buttons for explicit table/kanban switching.
- `SLKanban` public API now exports `setView` in addition to `render`, `toggle`, `openPoaSwap`, `_confirmPoaSwap`, `loadStatusHistory`, and `init`.

---

## [2.2.0] — 2026-05-08 (BlueBubbles Tunnel Fix)

### Fixed

- **ngrok tunnel** — corrected port from 1880 (Node-RED) to 1234 (BlueBubbles). Configured permanent ngrok static domain (`pseudospherical-etta-untactually.ngrok-free.dev`). iMessage tab now shows Online.
- **`docker-compose.yml`** — added `dns: [8.8.8.8, 1.1.1.1]` to both services to ensure external DNS resolution.
- **`TUNNEL_FIX.md`** — updated to document the ngrok permanent domain setup.
- **`.env.example`** — updated `BLUEBUBBLES_URL_0178` to use the permanent ngrok tunnel domain.

---

## [2.1.0] — 2026-05-01 (Antigravity Tier 1-3 + Library Upgrade Sprint)

### Added — Backend

- **`dashboard/api/discharge_monitor.py`** — Gmail OAuth2 discharge email parser. Scans inbox for court-issued exoneration notices, matches to active bonds by booking number, queues for discharge. Returns `501` stub when Gmail credentials are not configured. See `docs/GMAIL_DISCHARGE_SETUP.md`.
- **`dashboard/api/bonds.py`** — `POST /api/bonds/bulk-exonerate` endpoint. Accepts an array of booking numbers, exonerates all in a single transaction, optionally notifies indemnitors, cancels pending reminders, and releases POA inventory.
- **`dashboard/api/calendar.py`** — `POST /api/calendar/sync-gcal` endpoint. Pushes upcoming court dates to Google Calendar with color-coding, 48h/24h reminders, and full defendant metadata. Returns `501` stub when GCal credentials are not configured. See `docs/GCAL_SYNC_SETUP.md`.
- **`dashboard/api/court_reminders.py`** — `POST /api/court-reminders/auto-scan` endpoint. Scans all active bonds, schedules SMS reminders for court dates within the configured window, skips already-scheduled bonds.
- **`dashboard/services/court_reminder_service.py`** — `auto_scan_and_schedule()` method. Iterates active bonds, calculates days-to-court, schedules Twilio SMS at 7d/3d/1d intervals. Skips bonds already scheduled or with no court date.
- **`scripts/create_indexes.py`** — MongoDB index creation script. Creates compound indexes on `court_date + status`, `booking_number` (unique), `defendant_name`, and `risk_score` across all relevant collections for query performance.

### Added — Frontend

- **`dashboard/sl-active-bonds-ext.js`** — Extended Active Bonds module:
  - Court countdown column with color-coded badges (TODAY/red/orange/yellow/neutral)
  - Column sort on all headers (defendant, county, bond amount, court date, days to court, risk score)
  - CSV export with 14 columns including indemnitor phone and days-to-court
  - Bulk Exonerate modal with select-all, per-bond countdown badges, note field, and notify-indemnitor checkbox
  - Has Indemnitor filter chip (injected into filter bar)
  - Duplicate indemnitor phone detection (alert dialog)
  - Indemnitor cross-link (`openIndemInDefendants`) — navigates to Defendants tab and pre-fills search
- **`dashboard/sl-calendar-ext.js`** — Extended Court Calendar module:
  - Vanilla Calendar Pro mini date-picker sidebar (jump to any date)
  - GCal Sync button → `POST /api/calendar/sync-gcal`
  - Auto-Scan Reminders button → `POST /api/court-reminders/auto-scan`
  - Check Discharge Emails button → `POST /api/discharge/scan`
- **`dashboard/sl-analytics-apex.js`** — ApexCharts advanced analytics (3 new charts):
  - ⚡ Live Revenue Sparkline (30-second auto-refresh, daily average annotation)
  - 🌳 Bond Amount Treemap by county (drill-down: click county → jumps to Calendar tab filtered by county)
  - 🗺️ Risk Score Heatmap by county (4 risk buckets × top 10 counties)
- **`dashboard/sl-lifecycle.js`** — Bond lifecycle timeline panel (slide-in from any tab). Shows full journey: Arrest → Contact → Negotiate → Paperwork → Bond → Court → Discharge.
- **`dashboard/api/lifecycle_timeline.py`** — `GET /api/lifecycle/<booking_number>`. Aggregates all MongoDB collections into a unified chronological event list with stage progression.

### Changed — Frontend

- **`dashboard/sl-defendant-lifecycle.js`** — Fixed iOS Safari touch bug in `openShamrockNotes()`. Added `requestAnimationFrame` + `setTimeout(0)` double-flush before adding `.active` class to prevent touch events being swallowed on first tap.
- **`dashboard/styles.css`** — Added `will-change:opacity`, `isolation:isolate`, `-webkit-transform:translateZ(0)`, `transform:translateZ(0)` to `.slc-modal-overlay` for GPU compositing layer on iOS. Ensures modal opens reliably on touchscreen devices.
- **`dashboard/sl-inventory.js`** — Added `_checkLowStockBanner()`. Shows a fixed-position banner (red for critical ≤2, orange for low ≤5) when any POA tier is running low. Auto-dismisses after 12 seconds. Clicking the banner opens POA Inventory modal.
- **`dashboard/index.html`** — Added CDN links for ApexCharts 3.49.2 and Vanilla Calendar Pro 2.9.10. Added court countdown column header and CSV/Bulk Exonerate toolbar buttons to Active Bonds table. Added ApexCharts row (3 panels) to Analytics tab. Added Bulk Exonerate modal HTML.
- **`dashboard/__init__.py`** — Registered `discharge_monitor_bp` at `/api`.

### Added — Documentation

- **`docs/GMAIL_DISCHARGE_SETUP.md`** — Step-by-step Gmail OAuth2 setup for discharge monitor
- **`docs/GCAL_SYNC_SETUP.md`** — Step-by-step Google Calendar API setup for court date sync
- **`CHANGELOG.md`** — This file
- **`.env.example`** — Updated with all new environment variables

---

## [2.0.0] — 2026-04-27 (Lifecycle Panel + iOS Touch Fixes)

### Added

- `sl-lifecycle.js` — Bond lifecycle timeline panel
- `api/lifecycle_timeline.py` — Lifecycle event aggregation API
- iOS Safari touch fixes across all modal overlays
- Lifecycle button on every defendant card in Active Bonds

---

## [1.x.x] — Prior Releases

See git log for full history of Phase 1 (scraper), Phase 2 (lead scoring), Phase 3 (dashboard MVP), and Phase 4 (bonded case management).
