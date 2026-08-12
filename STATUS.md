# ShamrockLeads — True Status

> **Last verified:** 2026-08-09  
> **Repo:** `Shamrock2245/shamrock-leads` · branch `main`  
> **Product URL:** `https://leads.shamrockbailbonds.biz`  
> **Role:** Bond **Auto-CRM** pillar of **Shamrock’s Platform** (not Bail School LMS)  
> **Platform:** `docs/PLATFORM.md` · **Prod checklist:** `docs/ECOSYSTEM_PROD_CHECKLIST.md`  
> **Multi-state plan:** `docs/MULTI_STATE_SCRAPER_ROADMAP.md`  
> **Proxy stack:** `docs/APE_INTEGRATION_GUIDE.md` · `docs/SELF_HOSTED_PROXY_ARCHITECTURE.md`  
> **BlueBubbles versions:** `docs/BLUEBUBBLES_VERSIONING.md` (App v2 ≠ Server; Server latest = 1.9.9)  
> **DocuSeal Server:** `https://sign.shamrockbailbonds.biz` (Template ID 1 OSI · 16/16 tests passing)  
> **Postiz Social & MCP:** `https://social.shamrockbailbonds.biz` (`/api/mcp` 200 OK SSE stream · 5 channels live)  
> **OpenCut Editor:** `https://edit.shamrockbailbonds.biz` (VPS nginx → Tailscale `brendans-macbook-pro-4:3000`)  
> **All hosts:** [`docs/SUBDOMAINS.md`](./docs/SUBDOMAINS.md) · `config/subdomains.py` · `python scripts/check_subdomains.py --live`

---

## What “Auto-CRM” means here

After a **phone number** (and usually defendant/county) enters the system, the bond lifecycle should run with **minimal human intervention**, except risk/match gates:

```
Phone / arrest lead → outreach sequences → intake → match (human on ambiguity)
  → paperwork → payment → active bond → court/GPS/FTA → close
```

**BlueBubbles (iMessage)** is the preferred consumer rail for outreach. Office Mac runs **Server v1.9.9** (latest). Desktop reply visibility (webhook parse + `message/query` poll + thread hydrate) fixed **2026-07-26** — see `CHANGELOG` 2.17.0. App **v2.0.0+89** is the consumer *client* only; do not confuse with server upgrades (`docs/BLUEBUBBLES_VERSIONING.md`).

**Bail School** is a **separate P&L** (`shamrock-bail-school`). Leads may share brand, Slack, and secrets hygiene — not course progress state.

---

## Scale (authoritative — 2026-08-11)

| State | Registered scrapers | Code path | Notes |
|-------|--------------------:|-----------|-------|
| **GA** | **85** | `scrapers/counties_ga/` | + Gordon, Walker, Whitfield, Tift, Ware, Coffee, Appling, Bleckley, Crisp, Laurens, Effingham + EAS batch runner |
| **FL** | **67** | `scrapers/counties/` | **All 67 FL counties** on registry + scheduler; legacy `scraper_<county>` IDs |
| **NC** | **60** | `scrapers/counties_nc/` | + Nash, Vance, Rockingham, Granville, Person, Warren, Caswell, Chowan, Perquimans + DCN, Pitt, Craven, Randolph, Catawba, Carteret, Caldwell, Chatham/Stanly, Rowan, Robeson, Wayne, Wilkes |
| **SC** | **46** | `scrapers/counties_sc/` | All 46 counties registered |
| **TX** | **33** | `scrapers/counties_tx/` | + Ellis, Johnson, Ector, Midland, Potter, Bastrop, Guadalupe, Comal, Victoria, Walker + Bell, Lubbock, Webb, Jefferson, McLennan, Nueces, Brazos, Hays |
| **TN** | **21** | `scrapers/counties_tn/` | + Maury, Robertson, Hamblen, Bedford, Coffee, Lincoln, Giles + Wilson, Bradley, Blount, Sevier, Washington |
| **AL** | **13** | `scrapers/counties_al/` | + Houston, Morgan, Etowah, Cullman, DeKalb, Jackson + Baldwin, Tuscaloosa, Shelby, Montgomery |
| **LA** | **10** | `scrapers/counties_la/` | + Ascension, Livingston + Caddo, Calcasieu, Ouachita, St. Tammany |
| **MS** | **9** | `scrapers/counties_ms/` | + Lauderdale, Forrest, Jones, Madison + Harrison, DeSoto, Rankin |
| **CT** | **6** | `scrapers/counties_ct/` | Statewide dockets, DOC, Hartford, Bridgeport, New Haven, Stamford |
| **Total** | **350** | `dashboard/extensions.py` → `REGISTERED_COUNTIES` | Labels: `County (ST)` · drives Scraper Health + Multi-State Ops UI |

**Identity rule:** non-FL job IDs are `scraper_<st>_<county>` (e.g. `scraper_nc_mecklenburg`, `scraper_tn_davidson`). FL keeps `scraper_lee` for dashboard compatibility. CLI: `python main.py tn_davidson` / `tx_bexar` / `la_orleans` / `ct_doc`.

**Shared bases (recent):** `scrapers/dcn_base.py` (DevExpress), `scrapers/ocv_inmates_base.py` (OCV S3 inmates.json).

---

## Code on `main` (recent, implemented)

| Area | Status |
|------|--------|
| **350** registered scrapers (10 states), scoring, Slack, Mongo | ✅ 2026-08-11 |
| Multi-state `BaseScraper.state` + scheduler `_resolve_job_id` | ✅ |
| Platform bases: Zuercher, Southern SW, P2C, JailTracker, New World, Kologik, Odyssey, **DCN**, **OCV** | ✅ |
| FastAPI Super CRM (tabs, lifecycle, intake, etc.) | ✅ |
| **Multi-State Ops** tab + `/api/ops/*` (registry-first KPIs, live feed, all 10 states) | ✅ · live registry |
| **Bond Intelligence** tab + `/api/bond-intelligence`, multi-state stats | ✅ |
| Lead Explorer **state** column + filter (all 10 states) | ✅ |
| Lead Explorer live sort (`scraped_at`) + auto-refresh + county labels | ✅ |
| Scraper status multi-state join (`County (ST)` ↔ bare names) | ✅ |
| **Autonomous Proxy Engine (APE)** Warren + S5W2C + Stormsia | ✅ code · hub live |
| Hub APIs: `/api/crm/health`, `/overview`, `/pipeline`, `/search` | ✅ |
| Omnibar → CRM search | ✅ |
| Mongo upsert validation + `last_seen`/`scraped_at` + M0 oldest-first retention | ✅ 2026-08-04 |
| Superadmin **Data Hygiene** (`/api/admin/hygiene/*` + UI) — purge test junk, repair mismatches | ✅ 2026-08-04 |
| Webhooks fail-closed without secrets | ✅ |
| Ecosystem secrets checklist | `scripts/check_ecosystem_secrets.py` |
| Super CRM docs | `docs/SUPER_CRM.md`, `docs/ECOSYSTEM.md` |
| SC / NC / CT registries | `docs/SC_COUNTY_REGISTRY.md`, `docs/NC_COUNTY_REGISTRY.md`, `docs/CT_COUNTY_REGISTRY.md` |
| **Surety realignment (July 2026)** | ✅ |
| **Bond check-in A+C** — transparent portal GPS + condition policy | ✅ code |
| **Traccar GPS (B)** continuous via in-stack Traccar Client / OsmAnd | ✅ rewired |
| **Family Tree** tab + `/api/family-tree/*` | ✅ code |
| **NC waves 4–7** (Pitt, DCN Moore/Lee/Halifax/Richmond, Craven, Randolph, Catawba, Carteret, Caldwell, Chatham/Stanly OCV, Orange PDF) | ✅ code 2026-08-04 · NC **47** |
| **CT harden** (curl_cffi dockets + DOC A–Z list-first) | ✅ code 2026-08-04 |
| **Mem0 long-term memory** for Shannon iMessage (GAS-compatible `MEMO_API_KEY`) | ✅ code 2026-08-04 · set env on VPS |
| **iMessage inbound replies** on desktop (webhook + poll + hydrate) | ✅ code · BB ops ongoing |
| Scraper **Run** always JSON + county/state matching | ✅ |

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
| NC **47 registered** / 100 goal — many still need first successful production scrape | ⏳ Multi-State Ops / scheduler; DCN list partial (≤100/page); WAF metros (Wake/Guilford/Forsyth); more OCV app_ids |
| SC production depth (CAPTCHA/Cloudflare/proxy for Greenville family, etc.) | ⏳ Harden per `SC_COUNTY_REGISTRY` |
| GA remaining counties beyond registered set (74/159) | ⏳ Recon + wrappers |
| TN (9 registered; Davidson/Knox live; Shelby TLS) | ⏳ Deepen + remaining metros |
| TX (15 registered; Bexar/Dallas live; Harris browser) | ⏳ Expand top-25 |
| LA (4 registered; Orleans partial; Lafayette captcha) | ⏳ 365Labs captcha + harden EBR/Jefferson |
| CT dockets + DOC | ✅ Hardened 2026-08-04 — keep production scrapes scheduled |
| BlueBubbles production reliability (office Mac + tunnel) | ✅ Live (frp + BB 1.9.9); keep watchdog |
| `ENV=production` + strong `SECRET_KEY` + `DASHBOARD_PIN` on VPS | ✅ |
| Atlas M0 512MB cap — oldest-first retention + hygiene tools | ✅ code 2026-08-04 · monitor growth |
| Gmail discharge / GCal / Drive OAuth | Env-gated (tokens present; exercise live paths) |
| FL error scrapers (upstream / WAF / captcha) | ⏳ Bay/Gadsden/Gilchrist/Okeechobee/Suwannee blocked; Marion WAF; Lake captcha-service |
| Defendants collection backfill | ⏳ ongoing normalize/batch |
| Local PDF stitcher full blank packet | ✅ folders: `surety-agnostic-shamrock/` + `osi/` + `palmetto/` · SignNow primary |
| Auto-CRM “phone only → fully autopilot” with explicit human gates | Product next (Phase 21) |
| `edit.shamrockbailbonds.biz` (OpenCut) | ⏳ **Repo ready** (`nginx/edit…conf`) · **live DNS missing** — Wix `A edit → 178.156.179.237`, then `setup_nginx_vhosts.sh --certbot edit` + PM2 on laptop |
| Hetzner deploy after each `main` push | GitHub Action `Deploy to Hetzner` |

### Session note (2026-08-04)

| Deliverable | Result |
|-------------|--------|
| NC waves 4–7 + shared `dcn_base` / `ocv_inmates_base` | ✅ NC **47** registered |
| CT DOC + Statewide docket harden (`curl_cffi`, list-first DOC) | ✅ |
| Multi-State Ops / Health / stats **registry-first** live KPIs | ✅ |
| Mongo data-flow gaps + M0 oldest-first retention | ✅ |
| Superadmin Data Hygiene (Jon Doe / test purge + mismatch repair) | ✅ |
| Docs aligned to **269** fleet | ✅ this commit |

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
