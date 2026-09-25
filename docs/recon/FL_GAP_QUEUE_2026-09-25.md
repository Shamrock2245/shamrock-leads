# FL gap queue — Bay, Lake, Leon, Suwannee, Gadsden (2026-09-25)

**Scope:** the five registered Florida counties that were not emitting. Order worked: Bay → Lake → Leon → Suwannee → Gadsden.
**Method:** plain HTTP(S) from the agent box (datacenter egress, Python `requests` / `curl`; no TLS impersonation, stealth browser, proxy, Obscura routing, or CAPTCHA/WAF bypass). Where the box was refused, the public page was cross-checked with an ordinary external page fetch only to tell "egress blocked" apart from "site changed".
**Privacy:** no personal data is recorded here. Booking-key formats are shown as patterns only.
**Write smoke:** `MONGODB_URI` was **not** available in the agent environment, so no Mongo write smoke was run. Everything below is **read-smoke evidence only**; the write smoke for Bay and Suwannee is pending.

## Summary

| County | Official source | Box access | Rows parsed (read smoke) | Source booking key | Status |
|---|---|---|---:|---|---|
| Bay | https://www.baysomobile.org/is/ (uniGUI inmate search, linked from bayso.org) | 200, plain HTTPS | 940 / 940 unique | `Booking #` → `YYYY-NNNNNN` | **Fixed** — parser rewritten; `candidate_productive`; write smoke pending |
| Lake | https://www.lcso.org/inmate-search/ | page 200; API 400 without challenge token | 0 | — | **Held** — `fail_closed` (Turnstile/reCAPTCHA token required) |
| Leon | https://www.leoncountyso.com/About-us/Departments/Detention-Facility/Inmate-search | 403 Akamai (whole domain) | 0 | none on listing | **Held** — `fail_closed` (datacenter egress blocked + no listing booking #) |
| Suwannee | https://smartcop.suwanneesheriff.com/smartwebclient/jail.aspx | 200, plain HTTPS | 44 / 44 unique (30-day window); 128 current with 10-yr window | `Booking No` → `SCSO<YY>JBN<NNNNNN>` | **Fixed** — search + paging fixed; `candidate_productive`; write smoke pending |
| Gadsden | https://gadsdensheriff.com/inmate-lookup/ → iframe `http://69.21.72.195/smartwebclient/` | page 200; SmartWEB host silent | 0 | not verified | **Held** — `fail_closed` (embedded roster host unreachable) |

`SCRAPER_SOURCE_STATES`: Lake/Leon/Gadsden → `fail_closed` (mirrored in code with `SOURCE_CONTRACT_VALIDATED=False`, plus `live_emitter_evidence.json` holds). Bay/Suwannee stay `unverified` in the runtime registry until a write smoke is documented; the matrix shows them as `candidate_productive` from the passive evidence rows.

## Bay (FL 005) — fixed, read smoke only

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

## Suwannee (FL 121) — fixed, read smoke only

- **Registry before:** registered, runtime `unverified`, matrix `recon_only`.
- **Why silent:** the old code searched `txbLastName="%"`, which is not a supported criterion. The server answers "Please fill in at least one search criteria" with 0 rows. It also POSTed a flat JSON body to `AddMoreResults`, but the endpoint expects `{"searchVals": {...}}`. Separately, the card parser took the first `JailViewCharges` table, which is the HOLDS table on some cards, so those cards lost their charges. The site had not moved and the box is not blocked.
- **Contract:** SmartCOP SmartWEB "JAIL View" v3.7, ASP.NET WebForms over plain HTTPS. The page has a reCAPTCHA hook, but its sitekey is empty and it is not enforced. The supported broad criterion is **Begin/End Booking Date** + **Current Inmates Only**, sorted by Booking Date descending. Page 1 comes from the form POST; later pages come from `Jail.aspx/AddMoreResults` (10 per call). A card is kept only when the image `bookno` equals the visible `Booking No:` text.
- **Read smoke (2026-09-25 ~09:10 EDT):** 30-day window gave 44 rows / 44 unique, all matching `^SCSO\d{2}JBN\d{6}$`. Booking date+time, name and charges are present on 44/44 rows. A 10-year window returned 128 current inmates, the whole current roster.
- **Scraper default:** 30-day booking window (`LOOKBACK_DAYS`). Plain `requests` with TLS verification on (previously `curl_cffi` impersonation with `verify=False`).

## Gadsden (FL 039) — held (`fail_closed`)

- **Registry before:** registered, runtime `unverified`, matrix `recon_only`. Code scraped the WordPress page for tables, then fell back to a browser.
- **Finding:** `https://gadsdensheriff.com/inmate-lookup/` → 200 with **no roster table**. It only iframes `http://69.21.72.195/smartwebclient/`, a SmartCOP SmartWEB client on a bare IP over plain HTTP. From the box, that host accepts TCP but returns **0 bytes** (HTTP timeout at 25 s; HTTPS → TLS unexpected EOF). An external page fetch cannot load bare-IP URLs, so from here it is unknown whether the host is down or filtering datacenter ranges. No rows, criteria or booking-number format could be verified.
- **Decision:** `fail_closed`. If the SmartWEB host becomes reachable, it is likely the same JAIL View contract as Suwannee (booking-date window + `Booking No`). Re-verify before reopening.

## Needs owner (Brendan)

1. **Write smoke for Bay and Suwannee:** run from an environment with `MONGODB_URI` set (for example the prod host or Mac, as in prior smokes). Then document it and, if clean, promote both to `verified_public` in `SCRAPER_SOURCE_STATES`.
2. **Leon and Gadsden reachability:** both refuse or ignore the datacenter egress. A one-off plain-HTTP check from a sanctioned non-datacenter machine (for example the office Mac, not a proxy) would show whether Gadsden's SmartWEB host is up and whether Leon's listing exposes a booking ID. Neither may be reopened through residential/mobile proxies or Obscura.
3. **Bay walk volume:** confirm that 676 initials searches per 120-minute run is acceptable.
4. **Lake:** the `SOLVECAPTCHA_KEY` dependency is gone for Lake. Broward still uses it, which is unchanged here.
