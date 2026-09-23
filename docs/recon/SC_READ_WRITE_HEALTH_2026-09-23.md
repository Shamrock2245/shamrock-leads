# South Carolina Read+Write Scraper Health Matrix

> Generated: **2026-09-23 11:40 EDT** (America/New_York)  
> Repo branch: `fix/lee-cooldown-status-pinellas-source`  
> Scope: all **46** Census SC counties (Palmetto `licensed_states` includes SC).  
> No booking keys invented · no fail_closed reopened · no BondCases/outreach created.  
> Mongo: connected `ShamrockBailDB.arrests` + `scraper_status` (aggregates only; no PII).

## 1. Summary counts by status

| Status | Count | Meaning |
|--------|------:|---------|
| `live_write` | **2** | Contract path open, registered, recent successful writes or clear live path |
| `fail_closed` | **19** | Intentional source-contract guard (no fetch/write) |
| `broken` | **9** | Registered + contract default/open but scrape/write path failing or unsafe |
| `missing` | **0** | No scraper / not registered |
| `unknown` | **16** | Scaffold or unverified wrapper; insufficient evidence |

**Registered in `main.py` scheduler:** 46/46. **Modules under `scrapers/counties_sc/`:** 46/46. **Missing modules:** 0.

**Dashboard `SCRAPER_SOURCE_STATES` SC `fail_closed`:** 14 (Anderson, Bamberg, Beaufort, Berkeley, Greenville, Horry, Jasper, Kershaw, Laurens, Lee, Marion, Saluda, Union, York).

**Code-level fail_closed (incl. JailTracker base + Zuercher/P2C flags):** 19 (the 14 above **plus** Cherokee, Colleton, Lexington, Chester, Greenwood).

**CoS-named families — all accounted for:**
- P2C: Lee, Lexington → fail_closed
- Zuercher audited five: Anderson, Cherokee, Colleton, Kershaw, Laurens → fail_closed
- JailTracker: Chester, Greenwood → fail_closed (shared base)
- Related holds also fail_closed: Bamberg, Beaufort, Berkeley, Greenville, Horry, Jasper, Marion, Saluda, Union, York

**Census scope:** All 46 SC counties are in Palmetto/SC registry scope. None excluded.

## 2. Full matrix

| County | Module | Contract | Health (`SCRAPER_SOURCE_STATES`) | Field gaps (name/key/datetime/pagination) | Last write evidence | Status | Recommended action |
|--------|--------|----------|----------------------------------|---------------------------------------------|---------------------|--------|--------------------|
| Abbeville | `counties_sc/abbeville.py` (Scaffold/Custom) | True (default — scaffold risk) | unverified (default) | scaffold — no portal/parser (name/key/datetime/pagination all absent) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears |
| Aiken | `counties_sc/aiken.py` (Custom) | True (default) | unverified (default) | ok/partial/missing/iframe — TLS fragile | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `broken` | Revalidate TLS path / curl_cffi; contract check before reopen claims |
| Allendale | `counties_sc/allendale.py` (Scaffold/Custom) | True (default — scaffold risk) | unverified (default) | scaffold — no portal/parser (name/key/datetime/pagination all absent) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears |
| Anderson | `counties_sc/anderson.py` (Zuercher) | False | fail_closed | Zuercher thin wrap — gated; ID/time contract unproven | 47 arrests; last scraped 2026-08-14 15:51 EDT (pre-guard); status empty since 2026-08-15 | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination) |
| Bamberg | `counties_sc/bamberg.py` (Custom) | False | fail_closed | incomplete/blocked — fail_closed | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination) |
| Barnwell | `counties_sc/barnwell.py` (Scaffold/Custom) | True (default — scaffold risk) | unverified (default) | scaffold — no portal/parser (name/key/datetime/pagination all absent) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears |
| Beaufort | `counties_sc/beaufort.py` (XML) | False | fail_closed | XML parser has name/key/datetime; path fail_closed | 63 arrests; last scraped 2026-08-02 03:38 EDT; now fail_closed | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination) |
| Berkeley | `counties_sc/berkeley.py` (Custom) | False | fail_closed | incomplete/blocked — fail_closed | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination) |
| Calhoun | `counties_sc/calhoun.py` (Scaffold/Custom) | True (default — scaffold risk) | unverified (default) | scaffold — no portal/parser (name/key/datetime/pagination all absent) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears |
| Charleston | `counties_sc/charleston.py` (Custom) | True (default) | unverified (default) | ok/ok/ok/7-day search | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `broken` | Non-writing smoke of inmatesearch.charlestoncounty.gov; repair if contract intact |
| Cherokee | `counties_sc/cherokee.py` (Zuercher) | False | unverified (default) | Zuercher thin wrap — gated; ID/time contract unproven | 47 arrests; last scraped 2026-08-14 15:53 EDT (pre-guard) | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination); ALSO add `Cherokee (SC)` to SCRAPER_SOURCE_STATES=fail_closed for Health UI parity |
| Chester | `counties_sc/chester.py` (JailTracker) | False | unverified (default) | JailTracker thin wrap — gated; CAPTCHA | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination); ALSO add `Chester (SC)` to SCRAPER_SOURCE_STATES=fail_closed for Health UI parity |
| Chesterfield | `counties_sc/chesterfield.py` (SouthernSW) | True (default via SouthernSW; card-level ID required) | unverified (default) | via SSW base — card must supply name/ID/booked | 30 arrests; last scraped 2026-08-14 15:53 EDT; status empty today | `broken` | Non-writing aggregate SSW card smoke; keep card-level drop; no synthetic keys |
| Clarendon | `counties_sc/clarendon.py` (Scaffold/Custom) | True (default — scaffold risk) | unverified (default) | scaffold — no portal/parser (name/key/datetime/pagination all absent) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears |
| Colleton | `counties_sc/colleton.py` (Zuercher) | False | unverified (default) | Zuercher thin wrap — gated; ID/time contract unproven | 8 arrests; last scraped 2026-08-14 15:54 EDT (pre-guard) | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination); ALSO add `Colleton (SC)` to SCRAPER_SOURCE_STATES=fail_closed for Health UI parity |
| Darlington | `counties_sc/darlington.py` (Custom) | True (default) | unverified (default) | ok/partial/missing/unknown | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `broken` | Source-contract recon; gate False if incomplete |
| Dillon | `counties_sc/dillon.py` (Scaffold/Custom) | True (default — scaffold risk) | unverified (default) | scaffold — no portal/parser (name/key/datetime/pagination all absent) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears |
| Dorchester | `counties_sc/dorchester.py` (SouthernSW) | True (default via SouthernSW; card-level ID required) | unverified (default) | via SSW base — card must supply name/ID/booked | 95 arrests; last scraped 2026-08-14 15:55 EDT; status empty today | `broken` | Non-writing aggregate SSW card smoke; keep card-level drop; no synthetic keys |
| Edgefield | `counties_sc/edgefield.py` (Scaffold/Custom) | True (default — scaffold risk) | unverified (default) | scaffold — no portal/parser (name/key/datetime/pagination all absent) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears |
| Fairfield | `counties_sc/fairfield.py` (Scaffold/Custom) | True (default — scaffold risk) | unverified (default) | scaffold — no portal/parser (name/key/datetime/pagination all absent) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears |
| Florence | `counties_sc/florence.py` (Custom) | True (default; productive) | unverified (default) | ok/ok/ok/letter-walk | 624 arrests; max scraped_at 2026-09-23 09:45 EDT; status ok/623 same day | `live_write` | Keep live; optionally promote SCRAPER_SOURCE_STATES→verified_public after formal contract note; monitor |
| Georgetown | `counties_sc/georgetown.py` (Scaffold/Custom) | True (default — scaffold risk) | unverified (default) | scaffold — no portal/parser (name/key/datetime/pagination all absent) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears |
| Greenville | `counties_sc/greenville.py` (Custom) | False | fail_closed | parser present; 403/Incapsula fail_closed | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination) |
| Greenwood | `counties_sc/greenwood.py` (JailTracker) | False | unverified (default) | JailTracker thin wrap — gated; CAPTCHA | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination); ALSO add `Greenwood (SC)` to SCRAPER_SOURCE_STATES=fail_closed for Health UI parity |
| Hampton | `counties_sc/hampton.py` (Custom) | True (default) | unverified (default) | ok/partial/missing/none — 403/residential | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `broken` | HOLD on residential bypass experiments until contract proven on ordinary access; else fail_closed |
| Horry | `counties_sc/horry.py` (Custom) | False | fail_closed | JSON path name/key/datetime; contract fail_closed | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination) |
| Jasper | `counties_sc/jasper.py` (Custom) | False | fail_closed | WP cards name/key/datetime; contract fail_closed | 0 SC arrests; status ok/45 on 2026-08-15 then fail_closed | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination) |
| Kershaw | `counties_sc/kershaw.py` (Zuercher) | False | fail_closed | Zuercher thin wrap — gated; ID/time contract unproven | 9 arrests; last scraped 2026-08-14 15:57 EDT (pre-guard) | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination) |
| Lancaster | `counties_sc/lancaster.py` (NewWorld) | True (default via NewWorld) | unverified (default) | via NewWorld base — unverified | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Source-contract recon only; set SOURCE_CONTRACT_VALIDATED=False until proven |
| Laurens | `counties_sc/laurens.py` (Zuercher) | False | fail_closed | Zuercher thin wrap — gated; ID/time contract unproven | 41 arrests; last scraped 2026-08-14 15:58 EDT (pre-guard) | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination) |
| Lee | `counties_sc/lee.py` (P2C) | False | fail_closed | P2C thin wrap — gated; broad roster unproven | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination) |
| Lexington | `counties_sc/lexington.py` (P2C) | False | unverified (default) | P2C thin wrap — gated; broad roster unproven | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination); ALSO add `Lexington (SC)` to SCRAPER_SOURCE_STATES=fail_closed for Health UI parity |
| Marion | `counties_sc/marion.py` (Custom) | False | fail_closed | incomplete/blocked — fail_closed | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination) |
| Marlboro | `counties_sc/marlboro.py` (Custom) | True (default) | unverified (default) | ok/partial/missing/none — CF/403 | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `broken` | Same as Hampton — no WAF bypass; fail_closed until ordinary public roster |
| McCormick | `counties_sc/mccormick.py` (Scaffold/Custom) | True (default — scaffold risk) | unverified (default) | scaffold — no portal/parser (name/key/datetime/pagination all absent) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears |
| Newberry | `counties_sc/newberry.py` (PDF) | True (default; productive PDF) | unverified (default) | ok/ok(SO#)/ok/PDF single-doc | 19 arrests; max scraped_at 2026-09-23 07:47 EDT; later status empty/0 (flaky PDF) | `live_write` | Smoke PDF discovery stability; add scheduler telemetry; consider verified_public after contract doc |
| Oconee | `counties_sc/oconee.py` (Zuercher) | True (inherits ZuercherBase default — NOT in audited fail_closed five) | unverified (default) | via Zuercher base — no county override; contract unset (inherits True) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Metadata-only Zuercher contract audit; prefer explicit fail_closed until broad roster+ID+time proven |
| Orangeburg | `counties_sc/orangeburg.py` (Scaffold/Custom) | True (default — scaffold risk) | unverified (default) | scaffold — no portal/parser (name/key/datetime/pagination all absent) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears |
| Pickens | `counties_sc/pickens.py` (Zuercher) | True (inherits ZuercherBase default — NOT in audited fail_closed five) | unverified (default) | via Zuercher base — no county override; contract unset (inherits True) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Metadata-only Zuercher contract audit; prefer explicit fail_closed until broad roster+ID+time proven |
| Richland | `counties_sc/richland.py` (Custom) | True (default) | unverified (default) | ok/ok/ok/digraph-pager (captcha token path) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `broken` | Non-writing smoke of JMSOnline captcha+digraph walk; fix writer if contract still holds — do not invent keys |
| Saluda | `counties_sc/saluda.py` (Scaffold/Custom) | False | fail_closed | incomplete/blocked — fail_closed | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination) |
| Spartanburg | `counties_sc/spartanburg.py` (Scaffold/Custom) | True (default — scaffold risk) | unverified (default) | scaffold — no portal/parser (name/key/datetime/pagination all absent) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears |
| Sumter | `counties_sc/sumter.py` (SmartCOP) | True (default via SmartCOP) | unverified (default) | via SmartCOP — SYNTHETIC booking_number from name+date (policy gap) | 0 SC arrests persisted; status ok/1 today (no Mongo write evidence) | `broken` | FAIL CLOSED or repair to source-issued key only; do not treat as live_write |
| Union | `counties_sc/union.py` (Zuercher) | False | fail_closed | Zuercher thin wrap — gated; ID/time contract unproven | 30 arrests; last scraped 2026-08-14 16:01 EDT (pre-guard) | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination) |
| Williamsburg | `counties_sc/williamsburg.py` (Scaffold/Custom) | True (default — scaffold risk) | unverified (default) | scaffold — no portal/parser (name/key/datetime/pagination all absent) | 0 SC arrests in Mongo; status empty (or fail_closed idle) | `unknown` | Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears |
| York | `counties_sc/york.py` (Custom) | False | fail_closed | name/key/datetime mapped in parser; pagination unknown; access fail_closed | 0 SC arrests; status ok/15 on 2026-08-15 then fail_closed | `fail_closed` | HOLD — do not reopen without proven broad-list contract (name+source key+datetime+bounded pagination) |

## 3. Gap list (ranked by severity / payoff)

### A. Broken writers first (contract-allowed / default-True)

1. **Charleston** — ASP.NET 7-day search parser present; status empty; 0 Mongo → Non-writing smoke of inmatesearch.charlestoncounty.gov; repair if contract intact
2. **Richland** — Full parser (name/key/datetime/pager) but status empty today; 0 Mongo; captcha/token path likely failing → Non-writing smoke of JMSOnline captcha+digraph walk; fix writer if contract still holds — do not invent keys
3. **Dorchester** — Historical Aug-14 writes; status empty today — SSW cards may lack source ID or portal changed → Non-writing aggregate SSW card smoke; keep card-level drop; no synthetic keys
4. **Sumter** — SmartCOP synthesizes booking_number from name+date; status ok/1 but 0 SC arrests persisted → FAIL CLOSED or repair to source-issued key only; do not treat as live_write
5. **Aiken** — TLS/iframe fragility; empty → Revalidate TLS path / curl_cffi; contract check before reopen claims
6. **Chesterfield** — Historical Aug-14 writes; status empty today — SSW cards may lack source ID or portal changed → Non-writing aggregate SSW card smoke; keep card-level drop; no synthetic keys
7. **Darlington** — DCN-like portal; empty → Source-contract recon; gate False if incomplete
8. **Hampton** — 403 from datacenter; code mentions SOCKS/residential → HOLD on residential bypass experiments until contract proven on ordinary access; else fail_closed
9. **Marlboro** — Cloudflare/403; needs residential/browser → Same as Hampton — no WAF bypass; fail_closed until ordinary public roster

### B. High-pop / strategic unknowns (still no reopen of fail_closed)

1. **Georgetown** — Scaffold module registered; returns empty; no portal → Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears
2. **Lancaster** — Thin NewWorld wrapper; empty status; no Mongo; contract never formally validated → Source-contract recon only; set SOURCE_CONTRACT_VALIDATED=False until proven
3. **Oconee** — Thin Zuercher wrappers still network-eligible; empty status; no Mongo; unlike Anderson/Cherokee/Colleton/Kershaw/Laurens → Metadata-only Zuercher contract audit; prefer explicit fail_closed until broad roster+ID+time proven
4. **Orangeburg** — Scaffold module registered; returns empty; no portal → Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears
5. **Pickens** — Thin Zuercher wrappers still network-eligible; empty status; no Mongo; unlike Anderson/Cherokee/Colleton/Kershaw/Laurens → Metadata-only Zuercher contract audit; prefer explicit fail_closed until broad roster+ID+time proven
6. **Spartanburg** — Scaffold module registered; returns empty; no portal → Set SOURCE_CONTRACT_VALIDATED=False for truthful Health; recon only if public roster appears

### C. Fail_closed holds (listed; do not reopen without proven source contract)

1. **Anderson** [Zuercher] — HOLD. Zuercher search-only; SOURCE_CONTRACT_VALIDATED=False
2. **Beaufort** [XML] — HOLD. XML path unavailable at 2026-08-15 validation; SOURCE_CONTRACT_VALIDATED=False
3. **Berkeley** [Custom] — HOLD. Incomplete listing contract; SOURCE_CONTRACT_VALIDATED=False
4. **Greenville** [Custom] — HOLD. Official paths HTTP 403 / Incapsula; SOURCE_CONTRACT_VALIDATED=False
5. **Horry** [Custom] — HOLD. Bookings path timeout at validation; SOURCE_CONTRACT_VALIDATED=False
6. **Jasper** [Custom] — HOLD. Incomplete contract + prior unsafe ID path; SOURCE_CONTRACT_VALIDATED=False
7. **Lexington** [P2C] — HOLD. P2C search-only; SOURCE_CONTRACT_VALIDATED=False
8. **York** [Custom] — HOLD. Roster timeout at validation; SOURCE_CONTRACT_VALIDATED=False (parser source-faithful but access unproven)
9. **Bamberg** [Custom] — HOLD. HTTP 403 ordinary access; SOURCE_CONTRACT_VALIDATED=False
10. **Cherokee** [Zuercher] — HOLD. Zuercher no validated broad roster; SOURCE_CONTRACT_VALIDATED=False
11. **Chester** [JailTracker] — HOLD. JailTracker shared guard SOURCE_CONTRACT_VALIDATED=False (CAPTCHA)
12. **Colleton** [Zuercher] — HOLD. Zuercher roster lacks source-issued ID + booking timestamp; SOURCE_CONTRACT_VALIDATED=False
13. **Greenwood** [JailTracker] — HOLD. JailTracker shared guard SOURCE_CONTRACT_VALIDATED=False (CAPTCHA)
14. **Kershaw** [Zuercher] — HOLD. Zuercher lacks source-issued ID + booking timestamp; SOURCE_CONTRACT_VALIDATED=False
15. **Laurens** [Zuercher] — HOLD. Zuercher no validated broad roster; SOURCE_CONTRACT_VALIDATED=False
16. **Lee** [P2C] — HOLD. P2C/CentralSquare no validated broad roster; SOURCE_CONTRACT_VALIDATED=False
17. **Marion** [Custom] — HOLD. Jail path HTTP 403; SOURCE_CONTRACT_VALIDATED=False
18. **Saluda** [Scaffold/Custom] — HOLD. No configured public roster URL; SOURCE_CONTRACT_VALIDATED=False
19. **Union** [Zuercher] — HOLD. No configured public roster URL for inherited path; SOURCE_CONTRACT_VALIDATED=False

## 4. Concrete next smokes / PRs

1. **Richland smoke (non-writing):** `JMSOnline` captcha=`hidStrRandom` + digraph last-name walk; confirm name + source booking # + booked datetime + pager; then fix empty-run path only if contract holds. PR: parser/runtime fix + tests; no BondCases.
2. **Charleston smoke (non-writing):** 7-day ASP.NET booking search; same four contract facts; repair empty writer if proven.
3. **Newberry stability:** PDF discovery on Sheriff page; explain empty afternoon runs vs morning writes; add per-scraper telemetry.
4. **Florence hygiene:** Document formal source contract; optionally set `SCRAPER_SOURCE_STATES["Florence (SC)"]="verified_public"`.
5. **Sumter SmartCOP:** Gate `SOURCE_CONTRACT_VALIDATED=False` **or** stop synthetic `name_date` booking keys — policy-breaking. Prefer fail_closed until source-issued ID proven.
6. **Health UI parity PR (docs/map only):** Add Cherokee, Colleton, Lexington, Chester, Greenwood to `SCRAPER_SOURCE_STATES` as `fail_closed` (already gated in code).
7. **Scaffold honesty PR:** Set `SOURCE_CONTRACT_VALIDATED=False` on stub counties (Abbeville, Allendale, Barnwell, Calhoun, Clarendon, Dillon, Edgefield, Fairfield, Georgetown, McCormick, Orangeburg, Spartanburg, Williamsburg) so Health is not a silent default-True lie.
8. **Oconee/Pickens Zuercher audit:** Metadata-only; if same gaps as audited five → explicit fail_closed (do not invent keys).
9. **Dorchester/Chesterfield SSW:** Non-writing card aggregate; confirm source-issued ID still present on public cards.
10. **No speculative reopen** of Lee/Lexington P2C, Zuercher five, JailTracker Chester/Greenwood, Greenville Incapsula, Bamberg/Hampton/Marion 403 family.

## 5. Egress / proxy notes (from docs + code comments)

| County / family | Note |
|----------------|------|
| Hampton | Code comments: 403/Cloudflare from datacenter; `HAMPTON_SOCKS_PROXY` / `SOCKS_PROXY` / `RESIDENTIAL_SOCKS` mentioned — **do not use proxy to bypass access controls** for contract revalidation; ordinary public access required |
| Marlboro | Cloudflare/403 from datacenter; residential/browser noted in comments — same policy: no WAF bypass for reopen |
| Bamberg, Marion | 403 from datacenter at validation — fail_closed |
| Greenville | Incapsula / access-restricted; registry explicitly forbids proxy workarounds |
| Aiken | TLS/SSLEOF to `lookups.aikencountysc.gov` from some hosts; curl_cffi impersonation attempted |
| Beaufort XML | Ordinary access failed at 2026-08-15 validation (not necessarily proxy) |
| Horry, York | Timeouts through ordinary access at validation |
| Fleet general | `docs/PROXY_PROVIDER_ANALYSIS.md` recommends residential for CF-heavy sites, but SC reopen policy requires ordinary public broad-list proof without access-control bypass |

## Appendix — evidence sources

- `docs/SC_COUNTY_REGISTRY.md`
- `docs/recon/SOUTH_CAROLINA_SOURCE_CONTRACT_VALIDATION_2026-08-15.md`
- `docs/SC_ZUERCHER_SOURCE_SAFETY.md`, `docs/LEGACY_P2C_SOURCE_SAFETY.md`, `docs/JAILTRACKER_SOURCE_SAFETY.md`, `docs/SOUTHERN_SW_SOURCE_SAFETY.md`
- `STATUS.md` SC row (46 registered; Zuercher/P2C/JT guards)
- `dashboard/extensions.py` `SCRAPER_SOURCE_STATES` + `REGISTERED_COUNTIES`
- `main.py` `sched.register_scraper(SC_*)` ×46
- `scrapers/counties_sc/*.py` + platform bases
- Mongo `ShamrockBailDB.arrests` / `scraper_status` aggregates (ET)
- `core/models.py` Palmetto `licensed_states` includes SC (surety note only; no BondCases this task)

## Appendix — live_write detail

- **Florence:** only clear daily productive SC writer (623 records status; 624 Mongo).
- **Newberry:** productive but flaky (morning writes, later empty).
- No other SC county has post-guard (after ~2026-08-15) sustained write evidence.

