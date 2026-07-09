# ShamrockLeads Super CRM

> The ops brain for Shamrock Bail Bonds — arrest intelligence through bond lifecycle.

**URL:** `https://leads.shamrockbailbonds.biz`  
**Stack:** FastAPI + MongoDB Atlas + Docker on Hetzner

---

## CRM capability map

| Module | Sidebar tab | Primary APIs | Mongo collections |
|--------|-------------|--------------|-------------------|
| Command Center | tabCommand | `/api/crm/overview`, stats | arrests, scraper_status |
| Lead Explorer | tabLeads | `/api/leads`, `/api/arrests` | arrests |
| Defendants | tabDefendants | `/api/defendants/*` | defendants, defendant_notes |
| Active Bonds Kanban | tabActiveBonds | `/api/bonds/*`, bond lifecycle | active_bonds, poa_inventory |
| Intake Queue | tabIntake | `/api/intake/*`, Wix webhook | intake_queue |
| Indemnitors | tabIndemnitor | `/api/indemnitors/*` | indemnitors, matches |
| Outreach Pipeline | tabProspective | `/api/prospective/*` | prospective_bonds |
| Matching | (match manager) | `/api/bonds/match`, `/api/match-manager/*` | matches, active_bonds |
| Paperwork | tabPaperwork | SignNow services | paperwork_packets |
| Payments | Accounting / plans | `/api/payments/*` | payments, payment_plans |
| Tasks | Command / tasks API | `/api/tasks/*` | tasks |
| Tracking | tabTracking | Traccar webhooks | locations |
| iMessage | tabImessage | BlueBubbles routers | imessage_* |
| FTA / Court | tabFTA, Calendar | court_* routers | court_reminders |
| Compliance | Reports | `/api/compliance/*` | active_bonds, payments |
| OSINT | tabOSINT | `/api/osint/*`, contacts | contacts |
| Automations | tabAutomations | automation control | automation config |

### Super CRM hub (new)

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
Scraper → `arrests` → LeadScorer → Slack `#leads` / `#new-arrests` → Lead Explorer

### 2. Outreach → Intake
Prospective stage work → iMessage/Twilio sequences → client opens portal magic link → Wix webhook → `intake_queue`

### 3. Match → Bond
Intake + defendant match (human-gated) → `matches` → Active bond + POA assign → SignNow packet → payment

### 4. Active bond lifecycle
Kanban: Active → Monitoring → Alert → Exonerated / Forfeited / Surrendered  
GPS tracking, court reminders, FTA alerts, rearrest detector

---

## Making it “fully functioning”

### Ops checklist

1. `python scripts/check_ecosystem_secrets.py --strict`
2. Set VPS `.env`: `SECRET_KEY`, `DASHBOARD_PIN`, `GAS_API_KEY`, `WIX_WEBHOOK_SECRET`, Mongo, SignNow, Twilio, Slack, BB
3. `ENV=production` (or `REQUIRE_DASHBOARD_PIN=true`) so empty PIN does not open the CRM
4. `python scripts/mongo_indexes.py` after deploy
5. `curl -s https://leads.shamrockbailbonds.biz/api/crm/health` (authenticated session or via SSH localhost)
6. Smoke: scrape → Slack; Wix intake → queue; create bond match; open omnibar search

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
