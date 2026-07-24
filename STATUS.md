# ShamrockLeads — True Status

> **Last verified:** 2026-07-24  
> **Repo:** `Shamrock2245/shamrock-leads` · branch `main`  
> **Product URL:** `https://leads.shamrockbailbonds.biz`  
> **Role:** Bond **Auto-CRM** pillar of **Shamrock’s Platform** (not Bail School LMS)  
> **Platform:** `docs/PLATFORM.md` · **Prod checklist:** `docs/ECOSYSTEM_PROD_CHECKLIST.md`  
> **Multi-state plan:** `docs/MULTI_STATE_SCRAPER_ROADMAP.md`  
> **Proxy stack:** `docs/APE_INTEGRATION_GUIDE.md` · `docs/SELF_HOSTED_PROXY_ARCHITECTURE.md`

---

## What “Auto-CRM” means here

After a **phone number** (and usually defendant/county) enters the system, the bond lifecycle should run with **minimal human intervention**, except risk/match gates:

```
Phone / arrest lead → outreach sequences → intake → match (human on ambiguity)
  → paperwork → payment → active bond → court/GPS/FTA → close
```

**BlueBubbles (iMessage)** is the preferred consumer rail for outreach; full office reliability is an ops task (tunnel + env), not a missing dashboard tab.

**Bail School** is a **separate P&L** (`shamrock-bail-school`). Leads may share brand, Slack, and secrets hygiene — not course progress state.

---

## Scale (authoritative — 2026-07-24)

| State | Registered scrapers | Code path | Notes |
|-------|--------------------:|-----------|-------|
| **FL** | **51** | `scrapers/counties/` | OSI home market; legacy `scraper_<county>` IDs |
| **GA** | **74** | `scrapers/counties_ga/` | + EAS batch runner for rural cluster |
| **SC** | **46** | `scrapers/counties_sc/` | All counties registered |
| **NC** | **27** | `scrapers/counties_nc/` | Wave-1 (Southern SW, Zuercher, P2C, Meck/Durham…) |
| **TN** | **6** | `scrapers/counties_tn/` | Davidson, Knox, Shelby, Hamilton, Rutherford, TnCIS |
| **TX** | **12** | `scrapers/counties_tx/` | Bexar, Dallas, Harris, Tarrant, Travis, Collin, Denton, Fort Bend, Montgomery, Williamson, El Paso, Hidalgo |
| **LA** | **4** | `scrapers/counties_la/` | Orleans, Lafayette, Jefferson, East Baton Rouge |
| **AL** | **3** | `scrapers/counties_al/` | Jefferson, Madison, Mobile |
| **CT** | **2** | `scrapers/counties_ct/` | Statewide Criminal Dockets + CT DOC Inmate Roster |
| **MS** | **2** | `scrapers/counties_ms/` | Hinds, Jackson |
| **Total** | **247** | `dashboard/extensions.py` → `REGISTERED_COUNTIES` | Labels: `County (ST)` |

**Identity rule:** non-FL job IDs are `scraper_<st>_<county>` (e.g. `scraper_nc_mecklenburg`, `scraper_tn_davidson`). FL keeps `scraper_lee` for dashboard compatibility. CLI: `python main.py tn_davidson` / `tx_bexar` / `la_orleans`.

**Scaffold packages (no counties yet):** `counties_ct/`, `counties_ms/`, `counties_al/`.

---

## Code on `main` (recent, implemented)

| Area | Status |
|------|--------|
| **198** registered county scrapers (FL/GA/SC/NC), scoring, Slack, Mongo | ✅ |
| Multi-state `BaseScraper.state` + scheduler `_resolve_job_id` | ✅ July 14 |
| Platform bases: Zuercher, Southern SW, P2C, JailTracker, New World, Kologik, Odyssey, … | ✅ |
| FastAPI Super CRM (tabs, lifecycle, intake, etc.) | ✅ |
| **Multi-State Ops** tab + `/api/ops/*` (registry, state KPIs, live feed) | ✅ · **4 states** |
| **Bond Intelligence** tab + `/api/bond-intelligence`, multi-state stats | ✅ |
| Lead Explorer **state** column + filter (FL/GA/SC/NC/TN/TX/LA) | ✅ July 16 |
| Lead Explorer live sort (`scraped_at`) + auto-refresh + county labels | ✅ July 16 |
| Lead Explorer API: `sort_map` includes `scraped_at` + `activity.scraped_last_hour` | ✅ July 19 |
| Scraper status multi-state join (`County (ST)` ↔ bare names) | ✅ July 16 |
| **Autonomous Proxy Engine (APE)** Warren + S5W2C + Stormsia | ✅ code · hub live |
| Hub APIs: `/api/crm/health`, `/overview`, `/pipeline`, `/search` | ✅ July 2026 |
| Omnibar → CRM search | ✅ |
| Mongo index script expanded for CRM collections | ✅ |
| Webhooks fail-closed without secrets | ✅ |
| Ecosystem secrets checklist | `scripts/check_ecosystem_secrets.py` |
| Super CRM docs | `docs/SUPER_CRM.md`, `docs/ECOSYSTEM.md` |
| SC registries / recon | `docs/SC_COUNTY_REGISTRY.md`, `docs/SC_RECON_RESULTS.md` |
| NC registries / recon | `docs/NC_COUNTY_REGISTRY.md`, `docs/NC_RECON_RESULTS.md` |
| **Surety realignment (July 2026)** | ✅ |
| &nbsp;&nbsp;`bonds.py` — `surety_id` + `insuranceCompany` both forwarded to GAS | ✅ |
| &nbsp;&nbsp;Agent constants (Brendan O'Neal / P139768) in GAS + SignNow | ✅ |
| &nbsp;&nbsp;`intake.py` — `surety_id` persisted to MongoDB `intake_queue` | ✅ |
| **Bond check-in A+C (July 2026)** — transparent portal GPS + condition policy | ✅ code |
| **Traccar GPS (B)** continuous via in-stack Traccar Client / OsmAnd | ✅ rewired |

---

## Live prod verification (2026-07-23)
### Session follow-up (2026-07-23)

| Fix | Result |
|-----|--------|
| Bradford URL → `smartweb.bradfordsheriff.org` + direct-first | ✅ 3 records |
| Dixie URL → HTTPS SmartCOP + direct-first | ✅ 3 records |
| Taylor URL → `:8989/SmartWEBClient` | ✅ 3 records |
| SmartCOP base: direct before proxy | ✅ |
| Defendants `normalize/batch` (Lee/Collier + 300) | ✅ **0 → 594** defendants |
| Gilchrist | ⏳ no public DNS/host found |
| SignNow token | checked this session (see logs) |

### Session follow-up (2026-07-24 — Manus prod-hardening)

| Fix | Result |
|-----|--------|
| Monroe v2: rewrote against `data.keysso.net/api/arrests` JSON API (old ASP.NET dead) | ✅ **80 records** (no captcha/proxy) |
| Hillsborough: direct-first egress + form drift (SearchSortType + new fields) | ✅ **7 records** (direct HTTP, no proxy) |
| Lake: added SolveCaptcha reCAPTCHA v2 solver (token bypass dead) | ✅ code shipped (needs `SOLVECAPTCHA_KEY` run) |
| Marion: switched `btnSearch` → `btnRecentBookings` | ⚠️ AWS WAF blocks VPS IP intermittently |
| Bay: UniGUI session HandleEvent returns 401 | ⏳ needs deeper UniGUI reverse-engineering |
| Okeechobee: `/inmate-search` page is Wix shell, no public data source found | 🔴 blocked on upstream (no roster URL) |
| Gadsden: SmartWEB iframe → `69.21.72.195` server dead (empty reply) | 🔴 blocked on upstream |
| Gilchrist: DNS `smartcop.gilchristsheriff.com` NXDOMAIN | 🔴 blocked on upstream |
| Suwannee: SmartCOP server 500 on any search POST (upstream crash) | 🔴 blocked on upstream |
| Defendants `normalize/batch` × 7 runs | ✅ **594 → 3,211** defendants |

### Stage 2 hardening session (2026-07-24 cont.)

| Investigation | Result |
|---------------|--------|
| Bay County UniGUI: IIS 401 on HandleEvent (POST blocked, anti-scraping) | 🔴 blocked — server rejects all AJAX event requests from non-browser clients |
| Lake reCAPTCHA: `SOLVECAPTCHA_KEY` IS set (Hillsborough uses it), token solved but API rejects (server-side verify fails) | ⚠️ SolveCaptcha token rejected by LCSO API (domain/score mismatch) |
| Marion: AWS WAF still blocking VPS IP (403) | ⚠️ needs residential proxy egress |
| SignNow B5: `/api/paperwork/signnow/validate-templates` | ✅ **19 valid templates, 0 invalid** — token works |
| Defendants `normalize/batch` × 5 more runs | ✅ **3,211 → 4,580** defendants (108 repeat offenders) |

| Check | Result |
|-------|--------|
| `GET /health` | ✅ ok · **130,489 arrests** |
| `GET /api/crm/health` | ✅ **ok** |
| Integrations (GAS, Wix, SignNow, Twilio, Slack, BB, PIN, SECRET_KEY) | ✅ all true |
| GAS `?action=health` | ✅ `success` · version V409 |
| BlueBubbles frp `:12434` + `/api/imessage/status` | ✅ connected · private_api · 1.9.9 |
| Monroe one-shot scrape (post-deploy) | ✅ 80 records |
| Hillsborough one-shot (post-deploy) | ✅ 7 records |
| SignNow template validation | ✅ 19/19 accessible |
| Scraper fleet | ✅ **233 ok · 7 error** (FL: Bay, Gadsden, Gilchrist, Lake, Marion, Okeechobee, Suwannee) |
| Defendants collection | ✅ **4,580** (was 3,211) · 3.7% coverage |

**Bugfix shipped:** `init_bluebubbles()` re-bound `BB_SERVERS = {}`, so every `from … import BB_SERVERS` kept an empty dict and iMessage looked “unconfigured” even with env set. Now mutates in place (`clear` + `update`). Tests: `tests/test_bb_servers_init.py`.

## Honest gaps / ops

Track live cutover in **`docs/ECOSYSTEM_PROD_CHECKLIST.md`** (P0/P1). Summary:

| Item | Status |
|------|--------|
| NC wave-1 scrapers **registered** but many still need first successful production scrape | ⏳ Run via Multi-State Ops / scheduler |
| SC production depth (CAPTCHA/Cloudflare/proxy for Greenville family, etc.) | ⏳ Harden per `SC_COUNTY_REGISTRY` |
| GA remaining counties beyond registered set | ⏳ Recon + wrappers |
| TN wave-1 (Davidson/Knox live; Shelby TLS) | ⏳ Deepen + Hamilton/Rutherford |
| TX wave-1 (Bexar/Dallas live; Harris browser) | ⏳ Tarrant/Travis + top-25 |
| LA wave-1 (Orleans partial; Lafayette captcha) | ⏳ 365Labs captcha + EBR/Jefferson |
| CT / AL / MS | 🔲 Scaffold only — recon waves next |
| BlueBubbles production reliability (office Mac + tunnel) | ✅ Live 2026-07-23 (frp + BB 1.9.9); keep watchdog |
| `ENV=production` + strong `SECRET_KEY` + `DASHBOARD_PIN` on VPS | ✅ Set on VPS 2026-07-23 |
| Atlas network restriction / rotated Mongo password if ever leaked | Ops |
| Gmail discharge / GCal / Drive OAuth | Env-gated (tokens present; exercise live paths) |
| FL error scrapers (7 remaining) | ⏳ 5 blocked upstream (Bay/Gadsden/Gilchrist/Okeechobee/Suwannee), 1 WAF (Marion), 1 captcha-service (Lake) |
| Defendants collection backfill | ✅ **4,580** defendants (3.7% of 130k arrests normalized) |
| Local PDF stitcher full blank packet | ✅ 2026-07-10 (`paperwork_pdf_service`) — SignNow remains primary |
| Auto-CRM “phone only → fully autopilot” with explicit human gates | Product next (Phase 18) |
| Hetzner deploy after each `main` push | GitHub Action `Deploy to Hetzner` |

---

## Related repos

| Repo | Role |
|------|------|
| `shamrock-bail-portal-site` | Public site + GAS bond factory + school payment unlock |
| `shamrock-bail-school` | Student LMS education funnel |
| `shamrock-node-red` | **Automation fabric** — crons, webhooks, Watchdog, cross-service routing |

```bash
python scripts/check_ecosystem_secrets.py
python scripts/check_ecosystem_secrets.py --strict
```

## Super-admin + court automation (July 2026)

- Super-admin: `admin@shamrockbailbonds.biz` (see `dashboard/auth/super_admin.py`)
- Automation API (GAS_API_KEY): `/api/automation/lead-qualification|bond-lifecycle|risk-mitigation|court-email-scan|bond-report|discharge-report|ops-digest|schedule`
- Official OSI/Palmetto XLSX bond & discharge reports (`dashboard/services/bond_report_xlsx.py`)
- Court email: Calendar + client email + BlueBubbles (`court_email_scheduler`)

## Revenue automations (July 2026 — review-first)

| Cron | Default mode | Client contact? |
|------|--------------|-----------------|
| `speed_to_contact` | `review` | Queues outreach for staff approval |
| `paperwork_chase` | `review` | Staff notifications; `full_auto` to BB-nudge |
| `intake_recovery` | `review` | Staff notifications; `full_auto` to iMessage |
| `poa_low_stock` | on | Slack when POA tier ≤ threshold |
| `surety_weekly_reports` | on | XLSX → `generated_reports` + Slack |

Node-RED pack: `GET /api/automation/schedule` · docs `docs/automation/NODE_RED_SCHEDULE.md`

## Lifecycle suite (July 2026 — on the clock)

| Cron | Interval | Behavior |
|------|----------|----------|
| `forfeiture_scan` | 4h | Score active bonds; tasks + Slack for high/critical |
| `signnow_poller` | 30m | Poll SignNow open packets → signed/void |
| `compliance_backfill` | 6h | Missing check-in/court tasks → `TaskEngine` |
| `matching_backlog` | 1h | `MatchingEngine.batch_match`; Slack digest for human review |
