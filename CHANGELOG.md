# ShamrockLeads — Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
