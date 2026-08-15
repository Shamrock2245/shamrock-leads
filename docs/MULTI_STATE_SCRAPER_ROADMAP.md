# Multi-State Scraper Expansion Roadmap

> Palmetto Surety licensed states: **FL, SC, NC, TN, TX, CT, LA, MS**  
> Plus **GA** (adjacent market / existing build) and **AL** (adjacent).  
> Last updated: 2026-08-14
> **Registered (dashboard):** 67 FL · 85 GA · 60 NC · 46 SC · 34 TX · 22 TN · 16 AL · 13 LA · 9 MS · 6 CT = **358** — see root `STATUS.md`. Registration is not proof of a successful production scrape. The complete source-contract inventory is `docs/recon/COUNTY_SOURCE_CONTRACT_MATRIX.md` (942 Census county-equivalents plus five registered non-county scopes; 947 rows total across all repository states).

## Why this order

1. **FL** — OSI home market + densest scrapers already live ✅  
2. **SC** — Palmetto HQ-adjacent; **46/46 registered**. Fourteen county paths are explicitly `fail_closed` pending compliant source contracts; production depth and telemetry remain ongoing. ✅ registry
3. **GA** — large existing Track A/B/C investment 🔄 (85/159)  
4. **NC** — **60 registered** / 100 goal; ten county paths are explicitly `fail_closed` pending compliant source contracts, while production telemetry remains required for every enabled source. 🔄
5. **TN** — **22 registered**: Putnam is `verified_public`; 20 county paths are explicitly `fail_closed` pending compliant source contracts; the non-county TnCIS scope remains `unverified`. Source-specific production telemetry is still required. 🔄
6. **TX** — **34 registered** (including Randall’s validated public roster; source-specific production telemetry remains required) 🔄
7. **LA → MS** — **13 LA** + **9 MS** registered; Tangipahoa and St. Mary are deployed, and Bossier is registered; per-source production telemetry remains required 🔄
8. **CT** — **6 registered**. The five judicial-docket scopes (Statewide, Bridgeport, Hartford, New Haven, Stamford) are explicitly `fail_closed`: court docket numbers and hearing dates are not arrest booking identifiers or arrest times. CT DOC remains a separate source requiring its own source-contract proof and telemetry. 🔄
9. **AL** — **16 registered** (Lee, Marshall, and St. Clair deployed; Etowah locally source-validated and pending deployment; per-source production telemetry remains required) 🔄

## Shared platform bases (leverage first)

| Base | File | States using today |
|------|------|--------------------|
| Zuercher | `scrapers/zuercher_base.py` | GA, SC, NC |
| JailTracker | `scrapers/jailtracker_base.py` | FL, SC, GA |
| Southern Software | `scrapers/southern_sw_base.py` | GA, SC, NC |
| P2C | `scrapers/p2c_base.py` | FL, GA, SC, NC |
| SmartCOP | `scrapers/smartcop_base.py` | FL, GA, SC |
| New World | `scrapers/new_world_base.py` | FL, GA, SC |
| Kologik | `scrapers/kologik_base.py` | FL (Calhoun FL); reusable |
| Odyssey-style | `scrapers/odyssey_base.py` | GA stubs |
| EAS | `scrapers/eas_base.py` | GA batch |
| XML feed | `scrapers/xml_feed_base.py` | GA |
| DCN DevExpress | `scrapers/dcn_base.py` | NC (Moore, Lee, Halifax, Richmond, Carteret, …) |
| OCV inmates.json | `scrapers/ocv_inmates_base.py` | NC (Chatham, Stanly, …) |

**Rule:** before writing a custom county scraper, check if the roster is one of the above platforms. Thin wrappers are preferred.

## Identity rules (non-negotiable)

- `scraper_id` includes state for non-FL: `scraper_sc_lee`, `scraper_ga_lee`  
- FL keeps legacy `scraper_lee` for dashboard compatibility  
- One-shot CLI: `python main.py sc_jasper`  
- Every `ArrestRecord.State` must match the scraper state  
- Never collapse multi-state counties with the same name into one job  

## Per-state playbook

### SC (registered — harden production)
1. **All 46 modules registered** — see `docs/SC_COUNTY_REGISTRY.md`  
2. Live custom paths: Beaufort XML, Charleston, York, Florence, Horry, Richland, Jasper, …  
3. **Greenville** — retain fail-closed behavior until a supported public bulk roster is available; do not use access-control workarounds.
4. Revalidate 403 jailroster.org-family paths only when their official public source contracts are directly accessible; scaffold quiet no-portal counties.

### NC (60 registered — deepen + expand to 100)
1. **NC recon ✅** + **60 scrapers registered** — `docs/NC_COUNTY_REGISTRY.md`

2. Platforms: Southern SW + Zuercher + classic P2C + DCN DevExpress + OCV inmates.json + custody PDFs + custom HTML  
3. Dashboard Multi-State Ops filters NC; run-now uses `nc_*` keys  
4. Cloud P2C (Wake/Guilford/Forsyth) remains fail closed until directly accessible through a supported public bulk-roster contract.

5. Next: DCN pagination beyond first 100 · more OCV app_ids · Rowan/Robeson · remaining rural  

### TN (22 registered — deepen and validate)
1. **Putnam** — public ISOMS parser added and local source smoke passed on 2026-08-12; production write/alert evidence is still required.
2. **Davidson** and **Knox** — historically successful custom paths; re-check current source telemetry before operational reliance.
3. **Shelby** — IML TLS sensitivity; retain curl_cffi-first monitoring.
4. **Sullivan** — recon-only. The public OCV page is observable, but direct public-feed retrieval was access-denied in the validation environment; do not circumvent controls.
5. Full scheduler inventory and source posture: `docs/TN_COUNTY_REGISTRY.md`.

### TX (34 registered — validate and harden)
1. **Randall** — official public OCV/Next.js roster parser added; local two-page browser-rendered source smoke passed. Mongo upsert and alert evidence remain required.
2. **Bexar**, **Dallas**, **Tarrant**, and **Travis** remain core high-yield paths; refresh source telemetry before operational reliance.
3. **Bell**, **Ellis**, **Guadalupe**, and **Jefferson** stale P2C wrappers are deployed fail closed pending supported public bulk-roster contracts; do not rely on stale sources or bypass controls.
4. **Collin** remains WAF-sensitive; do not bypass access controls.
5. Full scheduler inventory, source posture, and recon queue: `docs/TX_COUNTY_REGISTRY.md`.

### LA (12 registered — harden)
1. **Tangipahoa** deployed after a bounded public-source smoke; parish-specific Mongo/alert evidence remains required.
2. **Lafayette** remains CAPTCHA-sensitive; validate all existing parish sources before adding coverage.
3. Registry `docs/LA_COUNTY_REGISTRY.md`.

### CT (6 registered — validate source access)
1. **Statewide dockets** and **CT DOC** are registered; city modules cover Hartford, Bridgeport, New Haven, and Stamford.
2. Registry reconciliation and access-method validation remain required; do not automate rejected or name-known-only sources.
3. Registry `docs/CT_COUNTY_REGISTRY.md`.

### MS / AL
1. **MS:** 9 jobs registered. `docs/MS_COUNTY_REGISTRY.md` records the current modules and recon-only blockers for Adams, Lafayette, Lowndes, Oktibbeha, and Warren.
2. **AL:** 16 jobs registered. Lee, Marshall, and St. Clair are deployed; Etowah is locally source-validated and pending deployment. Validate existing source telemetry before adding overlapping coverage.

## Directory layout

```
scrapers/
  counties/          # FL (67)
  counties_ga/       # GA (85)
  counties_sc/       # SC (46)
  counties_nc/       # NC (60)
  counties_tn/       # TN (22)
  counties_tx/       # TX (34)
  counties_ct/       # CT (6)
  counties_la/       # LA (13)
  counties_ms/       # MS (9)
  counties_al/       # AL (16)
  dcn_base.py        # NC DevExpress
  ocv_inmates_base.py
  *_base.py          # shared platforms
```

## Definition of done (per county)

- [ ] Roster URL + vendor documented in state registry  
- [ ] Scraper returns `ArrestRecord` with County, State, Booking_Number, Full_Name, Charges  
- [ ] Registered in `main.register_scrapers`  
- [ ] One-shot scrape returns ≥0 without exception (empty OK if documented)  
- [ ] `state` property set correctly  
- [ ] No PII in logs  
