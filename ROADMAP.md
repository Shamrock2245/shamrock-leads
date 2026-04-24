# ROADMAP.md — ShamrockLeads Phase Progression

> **Purpose:** Define what exists vs what is coming. Every agent must check this before checking in.

## Phase Overview

| Phase | Name | Status |
|-------|------|--------|
| 1 | Scrape → Score → Alert (20 counties) | ✅ Complete |
| 1b | County Expansion (36 active / 67 total) | ✅ Complete |
| 2 | Defendant Normalization | 🔲 Planned |
| 3 | Intake Ingestion | 🔲 Planned |
| 4 | Matching Engine | 🔲 Planned |
| 5 | Bond Case + Surety + POA | 🔲 Planned |
| 6 | Paperwork Generation | 🔲 Planned |
| 7 | Signature Orchestration | 🔲 Planned |
| 8 | Payment Collection | 🔲 Planned |
| 9 | Contact Discovery (OSINT) | 🔲 Planned |
| 10 | Outreach Sequencing | 🔲 Planned |
| 11 | Bond Tracker — Location Intelligence | ✅ Complete (separate repo) |

---

## Phase 1: Scrape → Score → Alert ✅ COMPLETE
20 county scrapers running on APScheduler with self-healing BaseScraper (retry, auto-disable, error classification), lead scoring (0–100, Hot/Warm/Cold/Disqualified), MongoDB Atlas storage (upsert by County + Booking_Number), real-time Slack alerts for hot leads, and Docker deployment on Hetzner VPS.

---

## Phase 1b: County Expansion ✅ COMPLETE
Expanded from 20 to **36 active scrapers** across 7 regional tiers covering all of Florida. All 67 county scrapers have been built (solver + runner). The remaining 31 counties are flagged `🟡 Needs Recon` in the dashboard and require URL/API discovery before activation.

New scrapers added: Alachua, Bay, Citrus, Dixie, Glades, Hernando, Highlands, Indian River, Lake, Leon, Marion, Martin, Okaloosa, Putnam, St. Johns, St. Lucie, Sumter, Taylor.

Dashboard (`dashboard/index.html` + `dashboard/mobile.html`) added showing all 67 counties with live status, risk badges, and source links.

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

## Remaining County Coverage (31 counties — Needs Recon)

These counties have solver/runner files but require URL verification or API discovery before activation:

| Tier | Counties |
|---|---|
| 3 — South FL | Miami-Dade, Broward, Palm Beach |
| 4 — North Central | Alachua (needs recon), Levy, Gilchrist, Columbia, Suwannee |
| 5 — Panhandle | Escambia, Santa Rosa, Walton, Holmes, Washington, Jackson, Calhoun, Liberty, Franklin, Gadsden, Wakulla |
| 6 — NE FL | Duval, Nassau, Baker, Clay, Putnam (needs recon), Flagler, Volusia |
| 7 — Rural North | Hamilton, Madison, Jefferson, Lafayette, Union, Bradford, Gilchrist, Dixie (needs recon) |

Next step for each: run `python3 counties/<name>/solver.py --test` and verify the source URL returns data.
