# ShamrockLeads Super CRM

> The ops brain for Shamrock Bail Bonds — arrest intelligence through bond lifecycle.

**URL:** `https://leads.shamrockbailbonds.biz`  
**Stack:** FastAPI + MongoDB Atlas + Docker on Hetzner  
**IA design:** `docs/specs/crm-ops-ia-revamp.md` (Twenty-inspired Bond Desk first)

---

## Canonical ops path

```
Hot Leads → contact (iMessage / sequence) → Bond Desk (intake + match + packet)
  → paperwork complete → Active Bonds (lifecycle Kanban)
```

**Bond Desk** (`tabIntake`) is where the bond is written.  
**Active Bonds** is for posted cases only — after indemnitor pairing and paperwork completion.

**Lead Pipeline** (`tabProspective`, formerly “Outreach”) is demoted: pre-desk contact only. Do not use it as a second home for writing bonds.

---

## CRM capability map

| Module | Sidebar | Primary APIs | Mongo collections |
|--------|---------|--------------|-------------------|
| Today / Command | tabCommand | `/api/crm/overview`, stats | arrests, scraper_status |
| Hot Leads | tabLeads | `/api/leads`, `/api/arrests` | arrests |
| **Bond Desk** | **tabIntake** | `/api/intake/*`, match, SignNow | intake_queue, matches |
| Active Bonds Kanban | tabActiveBonds | `/api/bonds/*`, bond lifecycle | active_bonds, poa_inventory |
| Court Calendar | tabCalendar | court / calendar APIs | court_reminders |
| Defendants | tabDefendants | `/api/defendants/*` | defendants, defendant_notes |
| Indemnitors | tabIndemnitor | `/api/indemnitors/*` | indemnitors, matches |
| Lead Pipeline (legacy) | tabProspective | `/api/prospective/*`, outreach | prospective_bonds |
| Matching | (Bond Desk + match manager) | `/api/bonds/match`, `/api/match-manager/*` | matches, active_bonds |
| Paperwork Config | tabPaperwork | SignNow services | paperwork_packets |
| Payments | Accounting / plans | `/api/payments/*` | payments, payment_plans |
| Tasks | Today / tasks API | `/api/tasks/*` | tasks |
| Tracking | tabTracking | Traccar webhooks | locations |
| iMessage | tabImessage | BlueBubbles routers | imessage_* |
| FTA | tabFTA | court_* routers | court_reminders |
| Compliance / Reports | tabReports | `/api/compliance/*` | active_bonds, payments |
| OSINT / Intelligence | collapsed folder | `/api/osint/*`, etc. | contacts |
| Automations | tabAutomations | automation control | automation config |

### Super CRM hub

| Endpoint | Purpose |
|----------|---------|
| `GET /api/crm/health` | Collection + integration readiness |
| `GET /api/crm/overview` | Pipeline counts (hot, intake, bonds, tasks) |
| `GET /api/crm/pipeline` | Funnel stages for widgets |
| `GET /api/crm/search?q=` | Omnibar unified search |

Omnibar (**⌘K**) uses `/api/crm/search` with fallback to match-manager.

---

## End-to-end CRM flows

### 1. Arrest → Hot lead
Scraper → `arrests` → LeadScorer → Slack `#leads` / `#new-arrests` → Hot Leads

### 2. Contact → Bond Desk
iMessage / Shannon / portal magic link → Wix/Telegram webhook → `intake_queue` (Bond Desk)

### 3. Match → Paperwork → Active Bond
Bond Desk: match (human-gated) → surety + POA → SignNow packet → payment → **promote to Active Bonds**

### 4. Active bond lifecycle
Kanban: Active → Monitoring → Alert → Exonerated / Forfeited / Surrendered  
GPS tracking, court reminders, FTA alerts, rearrest detector

---

## Paperwork autofill (Bond Desk)

Hydration source: `dashboard/services/signnow_packet_service.py` (`_build_prefill_fields`).  
Blanks: `templates/blanks/` (OSI + Palmetto variants).

Must collect before send: defendant identity & descriptors, booking/charges/court, bond $, indemnitor PII (address, DL, SSN for SSA, refs, employment, vehicle), surety, POA (phase 2), agency constants.

Full field contract: `docs/specs/crm-ops-ia-revamp.md` §6.

---

## Making it “fully functioning”

### Ops checklist

1. `python scripts/check_ecosystem_secrets.py --strict`
2. Set VPS `.env`: `SECRET_KEY`, `DASHBOARD_PIN`, `GAS_API_KEY`, `WIX_WEBHOOK_SECRET`, Mongo, SignNow, Twilio, Slack, BB
3. `ENV=production` (or `REQUIRE_DASHBOARD_PIN=true`) so empty PIN does not open the CRM
4. `python scripts/mongo_indexes.py` after deploy
5. `curl -s https://leads.shamrockbailbonds.biz/api/crm/health` (authenticated session or via SSH localhost)
6. Smoke: scrape → Slack; Wix intake → Bond Desk; create bond match; open omnibar search

### Harmony with portal + school

See `docs/ECOSYSTEM.md`. Shared `GAS_API_KEY` must match portal GAS and school Netlify when those systems call each other.

---

## Local / Docker

```bash
# Dashboard only
docker compose up -d dashboard

# Full scrapers + dashboard
docker compose up -d

# Indexes
docker compose exec dashboard python scripts/mongo_indexes.py
# or from host with .env
python scripts/mongo_indexes.py
```
