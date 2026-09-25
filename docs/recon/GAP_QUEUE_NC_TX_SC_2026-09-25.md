# NC / TX / SC gap queue: Gaston, Pitt, Orange (NC), Denton (TX), Darlington pages 2+ (2026-09-25)

**Scope:** four registered counties that were not emitting usable source-keyed records, plus Darlington SC, which wrote only page 1 (100 of ~233). Order worked: Gaston → Pitt → Orange NC → Denton → Darlington.
**Method:** plain HTTP(S) from the agent box (datacenter egress, Python `requests` / `curl`). No TLS impersonation, stealth browser, proxy, Obscura routing, or CAPTCHA/WAF bypass. None of the five sources blocked the box, so none of the problems were egress blocks. Every one was a code-side contract bug.
**Privacy:** no personal data is recorded here. Booking-key formats are shown as patterns only. Probe files were deleted after the smokes.
**Write smoke:** `MONGODB_URI` is not on the box. The Mongo write smokes ran on Brendan's Mac (residential ISP, plain HTTPS, repo `.env` Mongo config; env var names only: `MONGODB_URI`, `MONGODB_DB_NAME`) on 2026-09-25, 10:54–11:00 EDT. All five are clean, so all five are `verified_public`.

## Summary

| County | Official source | Box access | Read smoke (rows / unique keys) | Source booking key | Status |
|---|---|---|---:|---|---|
| Gaston NC | https://tepsweb.cityofgastonia.com/NewWorld.InmateInquiry/GastonCounty (New World InmateInquiry) | 200, plain HTTPS | 45 / 45 (3-day booking window, in custody) | detail `Booking` heading → `YYYY-NNNNNNNN` | **Fixed**: write smoke 45 new, ok; `verified_public` |
| Pitt NC | https://apps.pittcountync.gov/apps/detention/detainee/ (ASP.NET Detainee Search) | 200, plain HTTPS | 477 / 477 (48 pages, full roster) | `Booking Number` → 6 digits | **Fixed**: write smoke 477 (164 new / 313 updated), ok; `verified_public` |
| Orange NC | https://www.ocsonc.com/detention/current-detainees → daily "Detainees In Confinement" PDF | 200, plain HTTPS | 101 / 101 (report 09/25/2026 07:53) | `Bk #` → 5 digits | **Fixed**: write smoke 101 new, ok; `verified_public` |
| Denton TX | https://athena.dentonpolice.com/JailView/ (Denton PD city jail custody report) | 200, plain HTTPS | 8 / 8 | `bookno` → 8 digits (`YYNNNNNN`) | **Fixed (city jail only)**: write smoke 8 new, ok; `verified_public` |
| Darlington SC | http://bookings.darlingtonsheriff.org/dcn/inmates (DCN DevExpress grid) | 200, plain HTTP | 233 / 233 (3 pages) | URL `bid` (opaque DCN id, stable across sessions) | **Fixed**: pages 2+ via pager callback; write smoke 233 (133 new / 100 updated), ok; stays `verified_public` |

## Gaston (NC 071): fixed

- **Registry before:** registered, runtime `unverified`, matrix "no official source verified".
- **Why it wasn't usable:** it had been emitting, but not on a source key. Mongo held 250 Gaston docs, 249 of them keyed on a 7-digit number, which is the internal `/Inmate/Detail/<id>`. The old `NewWorldBaseScraper` detail parser looks for a "booking number" table row. New World renders the booking number in a `span#BookingNumberHeading`, so the parser fell back to the Detail id, and its listing fallback synthesizes `NW_…` keys. It also routed through the APE StealthSession (residential proxy preferred). The site had not changed and the box is not blocked.
- **Contract:** the search form is a GET: `Name`, `SubjectNumber`, `BookingNumber`, `InCustody=True`, `BookingFromDate` / `BookingToDate` (ISO dates), `Facility`. The listing is 100 rows per page with `Page=N` "Next" links. Its columns are Name / Subject Number / In Custody / Scheduled Release / demographics / Housing, with **no booking number**. Subject Number identifies the person, not the booking. Each detail page has `#BookingHistory` with one `div.Booking` per booking: a heading `Booking YYYY-NNNNNNNN`, Booking Date, Release Date, Total Bond, a bonds grid and a charges grid.
- **New scraper:** plain `requests`. It lists in-custody people booked in the last 3 days (`LOOKBACK_DAYS`, America/New_York) and reads each detail page (`MAX_DETAILS=250`, 0.3 s pacing). It emits one record per booking with a source booking number and **no release date**. Released or unkeyed bookings are skipped, never synthesized. Drift (form missing, or rows with no keyed booking) raises `ParseDriftError`. The full in-custody roster is ~700 people (7 pages), so it is not walked every hour.
- **Read smoke (box, ~10:50 EDT):** 45 people → 45 bookings, 45 unique, all `^\d{4}-\d{8}$`. Booking date and charges are on 45/45 and bond > 0 on 21. 22 s.

## Pitt (NC 147): fixed

- **Registry before:** registered, runtime `unverified`. Mongo held 316 source-format keys, so it was partially emitting.
- **Why partial:** the old code ran an A–Z + digraph last-name walk, but the GridView shows 10 rows per page and the code never followed the pager. Every saturated search was cut at 10 rows. Its docstring claimed page postbacks "error out", which is not true today.
- **Contract:** a blank "Get Detainee" POST returns all current detainees. Columns: Last / Suffix / First / Middle / DOB / **Booking Number** / Gender / Race. Paging is a standard GridView postback (`__EVENTTARGET=ctl00$mainContent$GridView1`, `__EVENTARGUMENT=Page$N`) carrying the prior `__VIEWSTATE`/`__EVENTVALIDATION`. The listing has no charges or bond (Charges=`Unknown`, as before). "Select" detail postbacks were not used.
- **New scraper:** one blank search, then `Page$2..N` while the pager offers the next page and new keys keep appearing (hard stop 120 pages). A header check raises `ParseDriftError`.
- **Read smoke (box):** 48 pages, 477 rows / 477 unique, all `^\d{6}$`. 21 s.

## Orange (NC 135): fixed

- **Registry before:** registered, runtime `unverified`. Only 5 docs in Mongo, none in source format.
- **Why silent:** (1) page discovery only matched `ocsonc.com/_files/ugd/*.pdf`. The current report is linked on the Wix file host `https://<uuid>.usrfiles.com/ugd/<id>.pdf`, so the code fell back to hard-coded PDFs from March and August 2026. (2) The row regex expected the booking number glued to a race letter (`12345W`), while pdfplumber yields `… A W M 12345 …`, so 0 rows parsed. It also used `verify=False`.
- **Contract:** the OCSO page links the daily "Detainees In Confinement Report by Facility" PDF, regenerated each morning (report 09/25/2026 07:53:44). Row: `LAST, FIRST MIDDLE  A/J  R  S  <Bk #>  <charge> / … / <docket> / $<bond> / …  MM/DD/YYYY HHMM  <days>`. Continuation lines carry further charges.
- **New scraper:** discovers links on both hosts and picks the newest report by its printed timestamp. It refuses a report older than 48 h (`ParseDriftError`) or one without a `Bk #` column. TLS verification is on and there are no hard-coded fallbacks.
- **Read smoke (box):** 101 rows / 101 unique, all `^\d{5}$`. Booking date+time and charges on 101/101, bond > 0 on 79.

## Denton (TX 121): fixed, city jail only

- **Registry before:** registered, runtime `unverified`. Mongo held 32 docs keyed `DEN_<bookno>`, and the last `scraper_status` was `empty`.
- **Why silent:** the code went through `make_stealth_request` (stealth stack) and prefixed the source key with `DEN_`. The endpoint itself answers a plain POST.
- **Contract:** `POST https://athena.dentonpolice.com/JailView/JailView.aspx/GetInmates` with body `{}`, the page's own jQuery call. It returns `{"d": "<JSON array>"}` with `bookno` (8 digits), `bookhandle`, `datetimebooked`, `name`, `charges`, `outstandingbonds`, `detainers`, `amount` and an inline mugshot (not stored).
- **Scope caveat:** this is the **Denton Police Department city jail** ("CITY JAIL CUSTODY REPORT", 8 detainees), not the Denton County Sheriff's jail. The county jail's public lookup is Tyler Odyssey PublicAccess "Jail Records" (`https://justice1.dentoncounty.gov/PublicAccess/JailingSearch.aspx?ID=400`). Its client validation requires a **last and first name** for every search, and a date-only POST returns the Odyssey error page. It has no broad listing, so it stays out of scope. No name enumeration was attempted.
- **New scraper:** plain `requests`. Source `bookno` is used verbatim (no prefix), and rows without an 8-digit `bookno` are dropped.
- **Read smoke (box):** 8 rows / 8 unique, all `^\d{8}$`, charges on 7/8.

## Darlington (SC 031): pages 2+ fixed

- **Before:** `verified_public` since the 2026-09-24 write smoke, but only the 100 server-rendered rows of ~233 were written. `dcn_base` said DevExpress callbacks were "unreliable from datacenter clients". Today they answer the box fine.
- **Contract (the site's own request):** `ASPx.GVPagerOnClick('gvInmates','PN<n>')` → `WebForm_DoCallback`. It POSTs to `/dcn/inmates` with the form fields (`__VIEWSTATE`, …), plus:
  - `gvInmates` = the grid's client `stateObject` as JSON (`callbackState`, `keys`, …), taken from the page and updated from each reply;
  - `__CALLBACKID=gvInmates`;
  - `__CALLBACKPARAM=c0:KV|<len>;<keys json>;GB|20;12|PAGERONCLICK3|PN<n>;` (`FormatCallbackState` / `SerializeCallbackArgs` from the DevExpress client script; `n` is 0-based).
  
  The reply is `s/*DX*/({'result':{'html':'<grid html>','stateObject':{…}},'id':0})`. The html is a JS-escaped string holding the same `DXDataRow` markup as page 1. `pageCount` comes from the grid's init script (3 on 2026-09-25).
- **Implementation:** `DCNBaseScraper.paginate_callbacks` (opt-in, **Darlington only**; the NC DCN counties are unchanged). Pages 2..`pageCount` are fetched at 0.5 s pacing with `max_callback_pages=10`, stopping early if a page adds no new `bid`. A reply that is not a success raises. `require_source_bid` stays on, and `max_detail_fetches` went from 120 to 400 so every row gets its detail (charges/bond).
- **Read smoke (box):** 3 pages (100 + 100 + 33), 233 rows / 233 unique `bid`. Charges on 232, bond > 0 on 134. About 114 s, mostly the paced detail pages. `bid` values are identical across two fresh sessions.

## Write smoke 2026-09-25 (Mac)

- **Where:** Brendan's Mac, repo checked out at branch `fix/gap-queue-nc-tx-sc-2026-09-25` @ `7075e01`, `.venv`, repo `.env`. The Mac was returned to `main` afterwards (fast-forward to `c3858f4`).
- **How:** `python main.py nc_gaston | nc_pitt | nc_orange | tx_denton | sc_darlington` (`ScraperScheduler.run_now` → `BaseScraper.run(writers=[MongoWriter])`). State-prefixed keys avoid the FL Orange collision.
- **Key check:** read-only counts on `arrests` (state + county), with docs touched since the smoke start matched against the source pattern.

| County | Scraped | New | Updated | Skipped invalid | Status | Stored keys touched by smoke matching source pattern | Legacy non-source keys still in Mongo |
|---|---:|---:|---:|---:|---|---|---:|
| Gaston NC | 45 | 45 | 0 | 0 | ok | 45 / 45 `^\d{4}-\d{8}$` | 250 (internal Detail id) |
| Pitt NC | 477 | 164 | 313 | 0 | ok | 477 / 477 `^\d{6}$` | 0 |
| Orange NC | 101 | 101 | 0 | 0 | ok | 101 / 101 `^\d{5}$` | 5 (8-digit, origin unknown) |
| Denton TX | 8 | 8 | 0 | 0 | ok | 8 / 8 `^\d{8}$` | 32 (`DEN_<bookno>`) |
| Darlington SC | 233 | 133 | 100 | 0 | ok | 233 / 233 source `bid` | 0 |

Lead scoring on the same runs: Gaston 3 hot / 12 warm, Orange 25 hot / 46 warm, Darlington 95 hot / 16 warm. Pitt and Denton were all disqualified because the Pitt listing has no charges/bond and the Denton feed has no bond amounts.

## Registry / evidence changes

- `SCRAPER_SOURCE_STATES`: Gaston (NC), Pitt (NC), Orange (NC), Denton (TX) → `verified_public`. Darlington (SC) stays `verified_public` with an updated note.
- `county_source_contract_evidence.json` rows NC 071/147/135, TX 121 and SC 031 were updated. `live_emitter_evidence.json` gained `live_write` rows, with Darlington's row refreshed. `COUNTY_SOURCE_CONTRACT_MATRIX.md` was regenerated (`--check` ok).
- Tests: `tests/test_gap_queue_nc_tx_sc.py` (synthetic fixtures, no network) runs through the CI bridge in `tests/test_source_contract_run_guard.py`.

## Needs owner (Brendan)

1. **Legacy keys:** 250 Gaston docs keyed on the internal Detail id and 32 Denton docs keyed `DEN_<bookno>` stay in `arrests`. New runs write source keys, so the same people can appear twice until the legacy docs age out or are cleaned up. Nothing was deleted, so the cleanup is your call.
2. **Denton coverage:** only the City of Denton jail is covered. The Denton County Sheriff's jail has no broad public listing (Odyssey name search only). Say whether the city-jail-only scope is acceptable under the `Denton (TX)` label.
3. **Load:** Darlington now reads about 233 detail pages every 120 min (was 100). Gaston reads about 45 detail pages every 60 min (the 3-day in-custody window). Intervals are unchanged.
