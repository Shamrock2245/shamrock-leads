# FL gap queue — Bay, Lake, Leon, Suwannee, Gadsden (2026-09-25)

**Scope:** the five registered Florida counties that were not emitting. Order worked: Bay → Lake → Leon → Suwannee → Gadsden.
**Method:** plain HTTP(S) from the agent box (datacenter egress, Python `requests` / `curl`; no TLS impersonation, stealth browser, proxy, Obscura routing, or CAPTCHA/WAF bypass). Where the box was refused, the public page was cross-checked with an ordinary external page fetch only to tell "egress blocked" apart from "site changed".
**Privacy:** no personal data is recorded here. Booking-key formats are shown as patterns only.
**Write smoke:** `MONGODB_URI` was not available in the agent environment, so the read smokes below ran on the box and the **Mongo write smokes ran on Brendan's Mac** (residential ISP, plain HTTPS, repo `.env` Mongo config) on 2026-09-25. See [Write smoke](#write-smoke-2026-09-25-mac). Bay and Suwannee are now `verified_public`.

## Summary

| County | Official source | Box access | Rows parsed (read smoke) | Source booking key | Status |
|---|---|---|---:|---|---|
| Bay | https://www.baysomobile.org/is/ (uniGUI inmate search, linked from bayso.org) | 200, plain HTTPS | 940 / 940 unique | `Booking #` → `YYYY-NNNNNN` | **Fixed** — parser rewritten; write smoke 940 new, status ok; `verified_public` |
| Lake | https://www.lcso.org/inmate-search/ | page 200; API 400 without challenge token | 0 | — | **Held** — `fail_closed` (Turnstile/reCAPTCHA token required) |
| Leon | https://www.leoncountyso.com/About-us/Departments/Detention-Facility/Inmate-search | 403 Akamai (whole domain), **also 403 from residential Mac** | 0 | none on listing | **Held** — `fail_closed` (403 to box and residential egress + no listing booking #) |
| Suwannee | https://smartcop.suwanneesheriff.com/smartwebclient/jail.aspx | 200, plain HTTPS | 44 / 44 unique (30-day window); 128 current with 10-yr window | `Booking No` → `SCSO<YY>JBN<NNNNNN>` | **Fixed** — search + paging fixed; write smoke 44 new, status ok; `verified_public` |
| Gadsden | https://gadsdensheriff.com/inmate-lookup/ → iframe `http://69.21.72.195/smartwebclient/` | page 200; SmartWEB host silent (box **and** residential Mac: connect timeout) | 0 | not verified | **Held** — `fail_closed` (embedded roster host unreachable) |

`SCRAPER_SOURCE_STATES`: Lake/Leon/Gadsden → `fail_closed` (mirrored in code with `SOURCE_CONTRACT_VALIDATED=False`, plus `live_emitter_evidence.json` holds). Bay/Suwannee → `verified_public` after the 2026-09-25 Mac write smoke (source booking keys confirmed in Mongo), plus `live_write` rows in `live_emitter_evidence.json`.

## Bay (FL 005) — fixed, write smoke clean

- **Registry before:** registered, runtime `unverified`, matrix `recon_only`, "not verified".
- **Why silent:** the old code POSTed the Search click with a hard-coded `_seq_=3` and no form values. uniGUI rejects an out-of-sequence request with **HTTP 401**, and with the right sequence but empty names it just moves focus to First Name. The fallback then parsed the empty landing shell → 0 rows → `RuntimeError` every run. The site had not moved and the box is not blocked.
- **Contract:** uniGUI/Ext JS 7 app. `GET /is/` gives `_S_ID`; events go to `POST /is/hyb.dll/HandleEvent` (`Obj=O8 afterrender` then `Obj=O68 click` with `_fp_` form values and an incrementing `_seq_`). The grid rows come from `GET /is/hyb.dll/HandleEvent?Obj=O25&Evt=data&start=&limit=` (JSON with JS `\xHH` escapes). The server needs **both** a last-name and a first-name prefix, but one letter of each works. Blank and `%`/`*` wildcards are rejected.
- **Enumeration:** bounded A–Z × A–Z initials walk (676 searches) in one session, paced at 0.15 s. Each run takes about 200 s. Only rows whose `Booking #` matches the source pattern are kept. Landing-page component IDs and labels are checked before searching, and drift raises `BaySourceContractError` (parse_drift/url_changed path).
- **Read smoke (2026-09-25 ~09:05 EDT):** 940 rows, 940 unique booking numbers, all matching `^\d{4}-\d{3,8}$`. Booking year mix: 2026 = 810, 2025 = 107, older = 23. Date In, name and charges are present on 940/940 rows. The newest Date In is today.
- **Listing fields used:** Booking #, Date In (date + time), name, race, sex, charges, per-charge bond (summed). Photos and commissary numbers are not stored.
- **Note for owner:** this is 676 name-initial searches per run against the public search, the same pattern as Aiken's A–Z walk but 26× the request count. The interval stays at 120 min.

## Lake (FL 069) — held (`fail_closed`)

- **Registry before:** registered, runtime `unverified`, matrix `recon_only`. Code used a **third-party CAPTCHA-solving service** (env `SOLVECAPTCHA_KEY`) and soft-failed to `[]` when that did not work.
- **Finding:** `GET /inmate-search/` → 200 (the page loads Cloudflare Turnstile in reCAPTCHA-compat mode). `POST /inmate-search/api/inmates` without a token → **HTTP 400** `{"required":{"token":"(string) reCAPTCHA token from client-side"}}`. `GET` → 405.
- **Decision:** challenge-walled. The CAPTCHA-solver path is a bypass and has been removed. The module now refuses every fetch (`SOURCE_CONTRACT_VALIDATED=False`). It reopens only if LCSO publishes a challenge-free public roster.

## Leon (FL 073) — held (`fail_closed`)

- **Registry before:** registered, runtime `unverified`, matrix `recon_only`. Code did a DNN A–Z name POST loop with a browser fallback.
- **Finding:** every path on `www.leoncountyso.com`, including `/`, returns **403 Access Denied, `Server: AkamaiGHost`** from the box. An ordinary external fetch renders the same page, so this is an **edge/IP block of the datacenter egress, not a site change**. The public results page (`…/Inmate-search/Search-Results/...`) is a name search. Its listing columns are Photo / Full Name / Last Arrest Date / Last Release Date / In Jail? / Charge(s) / Arresting Agency, with **no source booking number on the listing**. The booking ID sits only behind per-person detail pages, which were not probed.
- **Decision:** `fail_closed`. No WAF bypass or proxy. Reopening needs (a) access from a non-blocked, sanctioned egress and (b) a listing with a source booking identifier.

## Suwannee (FL 121) — fixed, write smoke clean

- **Registry before:** registered, runtime `unverified`, matrix `recon_only`.
- **Why silent:** the old code searched `txbLastName="%"`, which is not a supported criterion. The server answers "Please fill in at least one search criteria" with 0 rows. It also POSTed a flat JSON body to `AddMoreResults`, but the endpoint expects `{"searchVals": {...}}`. Separately, the card parser took the first `JailViewCharges` table, which is the HOLDS table on some cards, so those cards lost their charges. The site had not moved and the box is not blocked.
- **Contract:** SmartCOP SmartWEB "JAIL View" v3.7, ASP.NET WebForms over plain HTTPS. The page has a reCAPTCHA hook, but its sitekey is empty and it is not enforced. The supported broad criterion is **Begin/End Booking Date** + **Current Inmates Only**, sorted by Booking Date descending. Page 1 comes from the form POST; later pages come from `Jail.aspx/AddMoreResults` (10 per call). A card is kept only when the image `bookno` equals the visible `Booking No:` text.
- **Read smoke (2026-09-25 ~09:10 EDT):** 30-day window gave 44 rows / 44 unique, all matching `^SCSO\d{2}JBN\d{6}$`. Booking date+time, name and charges are present on 44/44 rows. A 10-year window returned 128 current inmates, the whole current roster.
- **Scraper default:** 30-day booking window (`LOOKBACK_DAYS`). Plain `requests` with TLS verification on (previously `curl_cffi` impersonation with `verify=False`).

## Gadsden (FL 039) — held (`fail_closed`)

- **Registry before:** registered, runtime `unverified`, matrix `recon_only`. Code scraped the WordPress page for tables, then fell back to a browser.
- **Finding:** `https://gadsdensheriff.com/inmate-lookup/` → 200 with **no roster table**. It only iframes `http://69.21.72.195/smartwebclient/`, a SmartCOP SmartWEB client on a bare IP over plain HTTP. From the box, that host accepts TCP but returns **0 bytes** (HTTP timeout at 25 s; HTTPS → TLS unexpected EOF). An external page fetch cannot load bare-IP URLs, so from here it is unknown whether the host is down or filtering datacenter ranges. No rows, criteria or booking-number format could be verified.
- **Decision:** `fail_closed`. If the SmartWEB host becomes reachable, it is likely the same JAIL View contract as Suwannee (booking-date window + `Booking No`). Re-verify before reopening.

## Write smoke 2026-09-25 (Mac)

- **Where:** Brendan's Mac (residential Comcast), repo checked out at PR #65 head `f710f17` (`fix/fl-gap-queue-2026-09-25`), `.venv`, repo `.env` Mongo config (env var names only: `MONGODB_URI`, `MONGODB_DB_NAME`). No proxy, no stealth, plain HTTPS.
- **How:** the scheduler's one-shot entry point, `python main.py bay` then `python main.py suwannee` (`ScraperScheduler.run_now` → `BaseScraper.run(writers=[MongoWriter])`), the same single-county path used for earlier write smokes.
- **Key check:** read-only Mongo count on `arrests` filtered by `state=FL` + county, regex on `booking_number`. No keys were synthesized.

| County | Scraped | New | Updated | Skipped invalid | Status | Elapsed | Stored keys matching source pattern |
|---|---:|---:|---:|---:|---|---:|---|
| Bay | 940 | 940 | 0 | 0 | ok (`scraper_status.status=ok`) | 189 s | 940 / 940 `^\d{4}-\d{6}$` (`Booking #`) |
| Suwannee | 44 | 44 | 0 | 0 | ok (`scraper_status.status=ok`) | 4.7 s | 44 / 44 `^SCSO\d{2}JBN\d{6}$` (`Booking No`) |

Lead scoring on the same runs: Bay 225 hot / 386 warm / 287 disqualified. Suwannee 11 hot / 16 warm / 15 disqualified. Both counties are promoted to `verified_public` in `SCRAPER_SOURCE_STATES`.

## Leon / Gadsden residential re-check 2026-09-25 (Mac, findings only)

One plain `curl -L` per URL from the Mac (residential Comcast, normal desktop Chrome UA, no proxy, 25 s timeout). Nothing else was tried.

| URL | Result |
|---|---|
| `https://www.leoncountyso.com/` | **HTTP 403** `Access Denied` (372 bytes, Akamai-style denial page) |
| `https://www.leoncountyso.com/About-us/Departments/Detention-Facility/Inmate-search` | **HTTP 403** `Access Denied` ("You don't have permission to access …/Inmate-search on this server") |
| `http://69.21.72.195/smartwebclient/` | **No response**: TCP connect timed out after 25 s (HTTP 000) |
| `http://69.21.72.195/smartwebclient/jail.aspx` | **No response**: TCP connect timed out after 25 s (HTTP 000) |

- **Leon:** the Akamai denial is not limited to datacenter egress. Ordinary residential access gets the same 403, so from this network there is no public listing to inspect and no booking number/ID could be confirmed on the listing or detail pages. The earlier finding still holds: the listing columns carry no booking number. Stays `fail_closed`.
- **Gadsden:** the bare-IP SmartWEB host did not answer the residential Mac either (the box got a TCP accept with 0 bytes; the Mac got no TCP connect at all). It could not be compared against Suwannee's SmartWEB JAIL View, and no roster or booking-number format was seen. Stays `fail_closed`. Before reopening, verify when the host answers ordinary public access.

## Needs owner (Brendan)

1. ~~Write smoke for Bay and Suwannee~~: **done 2026-09-25 on the Mac.** Both are clean and promoted to `verified_public` (see above).
2. **Leon and Gadsden reachability:** re-checked from the Mac on 2026-09-25. Leon returns 403 to residential access too, and Gadsden's SmartWEB host does not answer. Both stay `fail_closed`. Reopen only if Leon serves ordinary public access with a listing booking ID, or if Gadsden's host answers with a SmartWEB roster exposing `Booking No`. Never through residential/mobile proxies or Obscura.
3. **Bay walk volume:** confirm that 676 initials searches per 120-minute run is acceptable.
4. **Lake:** the `SOLVECAPTCHA_KEY` dependency is gone for Lake. Broward still uses it, which is unchanged here.
