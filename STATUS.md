# ShamrockLeads — True Status

> **Last verified:** 2026-08-13  
> **VPS:** Hetzner **CCX33** (8 dedicated vCPU / 32 GB RAM) as of 2026-08-13 — compose ceilings raised (`docs/runbooks/vps-ccx33-resize.md`). Root disk was **not** grown with the type change (still ~38 GB); grow to 160–240 GB in the Cloud Console.
> **Repo:** `Shamrock2245/shamrock-leads` · branch `main`  
> **Product URL:** `https://leads.shamrockbailbonds.biz`  
> **Role:** Bond **Auto-CRM** pillar of **Shamrock’s Platform** (not Bail School LMS)  
> **Platform:** `docs/PLATFORM.md` · **Prod checklist:** `docs/ECOSYSTEM_PROD_CHECKLIST.md`  
> **Multi-state plan:** `docs/MULTI_STATE_SCRAPER_ROADMAP.md`  
> **Proxy stack:** `docs/APE_INTEGRATION_GUIDE.md` · `docs/SELF_HOSTED_PROXY_ARCHITECTURE.md`  
> **BlueBubbles versions:** `docs/BLUEBUBBLES_VERSIONING.md` (App v2 ≠ Server; Server latest = 1.9.9)  
> **DocuSeal Server:** `https://sign.shamrockbailbonds.biz` (Template ID 1 OSI · 16/16 tests passing)  
> **Postiz Social & MCP:** `https://social.shamrockbailbonds.biz` (`/auth` 200 · `/api/mcp` 401 without key — backend repaired 2026-08-12 after Mastra 1600-col crash)  
> **OpenCut Editor:** `https://edit.shamrockbailbonds.biz` (VPS Docker `shamrock-opencut` · nginx → `:5320`)  
> **All hosts:** [`docs/SUBDOMAINS.md`](./docs/SUBDOMAINS.md) · `config/subdomains.py` · `python scripts/check_subdomains.py --live`

---

## VPS resize (2026-08-13)

## Paperwork gap inventory (code audit 2026-08-13)

| Spec slice | Repository truth |
|---|---|
| Write Bond → DocuSeal | Implemented in the finalize path with `send_email=false`, surety-specific template resolution, multi-submitter records, and per-party branded links; a real validated-case staff smoke remains required. |
| Party portal | PIN lookup, ID OCR, address review UI, and role-specific DocuSeal launch exist. The unsafe ID-scan → unassigned packet shortcut is now fail-closed; packets must originate from a validated BondCase. Selfie enforcement and the full staff exception ceremony remain unfinished. |
| Completion truth | DocuSeal webhook and enabled 30-minute DocuSeal poller update packet state; completed PDF Drive archival exists but still requires production OAuth/folder verification. |
| Chase | Review-mode queue and staff resend/status endpoints exist. Client nudges remain human-gated; `full_auto` was not enabled. |
| E-sign provider | DocuSeal-only for active paperwork. SignNow is retired from the workflow; historical fields remain read-only for old records only. |
| Remaining locked-spec gaps | Multi-indemnitor production walkthrough, staff second-PIN exception modal/audit, office kiosk walkthrough, dual-role FAQ initials, and collateral receipt serial OCR. No serial is inferred or invented. |

Hetzner type is now **CCX33** (8 dedicated vCPU / 32 GB RAM). Compose ceilings for the scraper, dashboard, Obscura, OSINT, Postiz, Traccar, and OpenCut were raised in-repo and applied live (`docs/runbooks/vps-ccx33-resize.md`). `SCRAPER_MAX_CONCURRENT` stays **8** until one full cycle is green. **Root disk is still ~38 GB** — grow it to 160–240 GB in the Cloud Console (CPU/RAM resize does not grow the volume). Chromium launchers now share lean flags (`scrapers/chromium_flags.py`). Paperwork MVP: staff copy/send **indemnitor + defendant** branded links (`/sign/{packet}/{role}`). OSINT worker key is minted and live (worker `/status` 200); Toutatis has an Instagram session. SPECTRA uses Hudson Rock (free), not HIBP. Hunter.io is wired for attorney email finder.

## Production audit update (2026-08-12)

| Surface / gate | Verified state |
|---|---|
| Public hosts | Direct bounded probes returned `200` for leads `/health`, school `/`, DocuSeal `/`, paperwork `/`, Postiz `/auth`, and OpenCut `/`; the canonical portal GAS `?action=health` returned `success:true`, `V409`. |
| Public Bail School pricing (C2) | **Verified**: current JSON-LD lists the 120-hour course at `$649`; the retired course title and `$699` were absent from the fetched page source. |
| Deployment integrity | Latest `Deploy to Hetzner` run for `df24815` timed out at the 30-minute SSH command budget after the core image build. The workflow time budget is corrected in the pending commit; it is not yet a live deployment result. |
| Human-gated bond / outreach evidence | **Still required**: one staff-confirmed write-bond → paperwork event (B3) and one staff-approved outbound dashboard iMessage (D2). No synthetic cases, paperwork, or client messages were created for this audit. |
| Historical secret rotation (C3) | **Still required**: the portal rotation guide confirms prior credentials existed in git history. No vendor key was rotated without Brendan’s approval. |

## What “Auto-CRM” means here

After a **phone number** (and usually defendant/county) enters the system, the bond lifecycle should run with **minimal human intervention**, except risk/match gates:

```
Phone / arrest lead → outreach sequences → intake → match (human on ambiguity)
  → paperwork → payment → active bond → court/GPS/FTA → close
```

**BlueBubbles (iMessage)** is the preferred consumer rail for outreach. Office Mac runs **Server v1.9.9** (latest). Desktop reply visibility (webhook parse + `message/query` poll + thread hydrate) fixed **2026-07-26** — see `CHANGELOG` 2.17.0. App **v2.0.0+89** is the consumer *client* only; do not confuse with server upgrades (`docs/BLUEBUBBLES_VERSIONING.md`).

**Bail School** is a **separate P&L** (`shamrock-bail-school`). Leads may share brand, Slack, and secrets hygiene — not course progress state.

---

## Scale (authoritative — 2026-08-14)

| State | Registered scrapers | Code path | Notes |
|-------|--------------------:|-----------|-------|
| **GA** | **85** | `scrapers/counties_ga/` | Gwinnett and Fulton fail-closed guards are deployed; six audited legacy P2C paths also fail closed under the deployed `0de5f79` shared guard because official sources are restricted or lack a booking-safe bulk identity boundary. See `docs/LEGACY_P2C_SOURCE_SAFETY.md`. |
| **FL** | **67** | `scrapers/counties/` | Miami-Dade ArcGIS repair and Broward fail-closed guard deployed 2026-08-14; public hosts are healthy and per-source production telemetry remains pending. |
| **NC** | **60** | `scrapers/counties_nc/` | Durham fail-closed guard and Lincoln’s official OCV repair are deployed; seven audited legacy P2C paths also fail closed under the deployed `0de5f79` shared guard pending a source-safe broad roster. See `docs/LEGACY_P2C_SOURCE_SAFETY.md`. Production persistence and alert telemetry remain pending. |
| **SC** | **46** | `scrapers/counties_sc/` | York source-faithful parser repair is deployed; Lee and Lexington legacy P2C paths fail closed under `0de5f79`. Anderson, Cherokee, Colleton, Kershaw, and Laurens Zuercher hardening is locally validated and pending deployment; see `docs/SC_ZUERCHER_SOURCE_SAFETY.md`. Per-county persistence and alert telemetry remain pending. |
| **TX** | **34** | `scrapers/counties_tx/` | Randall is source-validated; Bell, Ellis, Guadalupe, and Jefferson fail-closed guards deployed 2026-08-14 with public hosts healthy. |
| **TN** | **22** | `scrapers/counties_tn/` | + Putnam (deployed 2026-08-12 EDT; public ISOMS source locally validated; Mongo upsert/alert telemetry still pending) |
| **AL** | **15** | `scrapers/counties_al/` | + Marshall (deployed 2026-08-14; official public roster locally validated); Lee remains deployed; per-scraper Mongo/alert evidence pending |
| **LA** | **12** | `scrapers/counties_la/` | Tangipahoa and St. Mary deployed 2026-08-14 with public host checks green; per-parish Mongo/alert evidence pending |
| **MS** | **9** | `scrapers/counties_ms/` | Registry reconciled 2026-08-14; 9 jobs registered. Adams, Lafayette, Lowndes, Oktibbeha, and Warren remain recon-only pending safe public booking boundaries. |
| **CT** | **6** | `scrapers/counties_ct/` | CT DOC fail-closed guard deployed 2026-08-14 after official BITS BOT rejection; public hosts are healthy and Statewide dockets plus municipal paths remain registered. |
| **Total** | **356** | `dashboard/extensions.py` → `REGISTERED_COUNTIES` | Labels: `County (ST)` · drives Scraper Health + Multi-State Ops UI |

**Identity rule:** non-FL job IDs are `scraper_<st>_<county>` (e.g. `scraper_nc_mecklenburg`, `scraper_tn_davidson`). FL keeps `scraper_lee` for dashboard compatibility. CLI: `python main.py tn_davidson` / `tx_bexar` / `la_orleans` / `ct_doc`.

**Shared bases (recent):** `scrapers/dcn_base.py` (DevExpress), `scrapers/ocv_inmates_base.py` (OCV S3 inmates.json).

---

## Code on `main` (recent, implemented)

| Area | Status |
|------|--------|
| **356** registered scrapers (10 states), scoring, Slack, Mongo | ✅ Lee and Marshall AL, Tangipahoa and St. Mary LA, and Miami-Dade FL deployments with public host checks passed; per-scraper Mongo/Slack evidence remains pending |
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
| DocuSeal API/templates | must verify `DOCUSEAL_API_KEY`, `DOCUSEAL_TEMPLATE_ID_OSI`, and `DOCUSEAL_TEMPLATE_ID_PALMETTO` in production |

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
| DocuSeal B5: `/api/paperwork/docuseal/templates` | ⏳ verify the two live DocuSeal templates from the production account |
| Defendants `normalize/batch` × 5 more runs | ✅ **3,211 → 4,580** defendants (108 repeat offenders) |

| Check | Result |
|-------|--------|
| `GET /health` | ✅ ok · **130,489 arrests** |
| `GET /api/crm/health` | ✅ **ok** |
| Integrations (GAS, Wix, DocuSeal, Twilio, Slack, BB, PIN, SECRET_KEY) | ✅ all true |
| GAS `?action=health` | ✅ `success` · version V409 |
| BlueBubbles frp `:12434` + `/api/imessage/status` | ✅ connected · private_api · 1.9.9 |
| Monroe one-shot scrape (post-deploy) | ✅ 80 records |
| Hillsborough one-shot (post-deploy) | ✅ 7 records |
| DocuSeal template validation | ⏳ confirm OSI + Palmetto templates in production |
| Scraper fleet | ✅ **233 ok · 7 error** (FL: Bay, Gadsden, Gilchrist, Lake, Marion, Okeechobee, Suwannee) |
| Defendants collection | ✅ **4,580** (was 3,211) · 3.7% coverage |

**Bugfix shipped:** `init_bluebubbles()` re-bound `BB_SERVERS = {}`, so every `from … import BB_SERVERS` kept an empty dict and iMessage looked “unconfigured” even with env set. Now mutates in place (`clear` + `update`). Tests: `tests/test_bb_servers_init.py`.

## Honest gaps / ops

Track live cutover in **`docs/ECOSYSTEM_PROD_CHECKLIST.md`** (P0/P1). Summary:

| Item | Status |
|------|--------|
| NC **60 registered** / 100 goal — many still need first successful production scrape | ⏳ Multi-State Ops / scheduler; Durham fail-closed guard is deployed pending a public identity-safe source contract; DCN list partial (≤100/page); WAF metros (Wake/Guilford/Forsyth); more OCV app_ids |
| SC production depth (CAPTCHA/Cloudflare/proxy for Greenville family, etc.) | ⏳ Harden per `SC_COUNTY_REGISTRY` |
| GA remaining counties beyond registered set (85/159) | ⏳ Recon + wrappers. Gwinnett is intentionally fail closed pending a supported complete-identity bulk source. |
| TN (22 registered; Putnam deployed with public health green; Davidson/Knox historic success; Shelby TLS sensitivity) | ⏳ Deepen and obtain per-source Mongo/Slack telemetry; Sullivan remains recon-only |
| TX (34 registered; Randall deployed; legacy P2C wrappers need source refresh) | ⏳ Obtain per-source Mongo/Slack telemetry and refresh unreachable legacy P2C sources |
| AL (15 registered; Lee and Marshall deployed with public host checks green) | ⏳ Obtain per-scraper Mongo/Slack telemetry and validate source health for existing Alabama jobs |
| LA (12 registered; Tangipahoa and St. Mary deployed with public host checks green; Lafayette remains CAPTCHA-sensitive) | ⏳ Obtain parish-specific Mongo/Slack telemetry and validate existing parish source health |
| MS (9 registered; registry reconciled; five assessed counties remain recon-only) | ⏳ Validate existing source telemetry and wait for a supported public roster/export before adding uncovered counties |
| CT dockets + DOC | ⏳ CT DOC fail-closed guard deployed pending a supported booking-safe public source; do not claim CT DOC production writes or alerts. |
| BlueBubbles production reliability (office Mac + tunnel) | ✅ Live (frp + BB 1.9.9); keep watchdog |
| `ENV=production` + strong `SECRET_KEY` + `DASHBOARD_PIN` on VPS | ✅ |
| Atlas M0 512MB cap — oldest-first retention + hygiene tools | ✅ code 2026-08-04 · monitor growth |
| Gmail discharge / GCal / Drive OAuth | Env-gated (tokens present; exercise live paths) |
| FL error scrapers (upstream / WAF / captcha) | ⏳ Bay/Gadsden/Gilchrist/Okeechobee/Suwannee blocked; Marion WAF; Lake captcha-service. Miami-Dade ArcGIS repair is deployed but awaits Miami-specific production telemetry. |
| Defendants collection backfill | ⏳ ongoing normalize/batch |
| Local PDF stitcher full blank packet | ✅ folders: `surety-agnostic-shamrock/` + `osi/` + `palmetto/` · DocuSeal primary |
| Auto-CRM “phone only → fully autopilot” with explicit human gates | Product next (Phase 21) |
| `edit.shamrockbailbonds.biz` (OpenCut) | ✅ Live on VPS Docker (`shamrock-opencut` → `:5320`) · nginx no longer Tailscale |
| Hetzner deploy after each `main` push | GitHub Action `Deploy to Hetzner` |

### Session note (2026-08-04)

| Deliverable | Result |
|-------------|--------|
| NC waves 4–7 + shared `dcn_base` / `ocv_inmates_base` | ✅ NC **47** registered |
| CT DOC + Statewide docket harden | ⏳ CT DOC list-first path retired after public BITS BOT rejection; statewide docket remains separately registered. |
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
| `docuseal_poller` | 30m | Poll DocuSeal open submissions → signed/void |
| `compliance_backfill` | 6h | Missing check-in/court tasks → `TaskEngine` |
| `matching_backlog` | 1h | `MatchingEngine.batch_match`; Slack digest for human review |
