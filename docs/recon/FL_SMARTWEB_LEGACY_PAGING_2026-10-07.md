# FL SmartWEB legacy paging + Putnam / Sumter (2026-10-07)

**Scope:** Florida SmartCOP SmartWEB JAIL View hosts still capped at the first page of cards, plus Putnam and Sumter, which were still on `curl_cffi` impersonation, `verify=False`, and a `%` last-name wildcard.
**Method:** plain HTTP(S) from the agent box (datacenter egress, Python `requests`). No TLS impersonation, stealth browser, proxy, Obscura, or CAPTCHA/WAF bypass.
**Privacy:** no personal data is recorded here. Booking-key formats are shown as patterns only.
**Write smoke:** `MONGODB_URI` was not available here, so these are read smokes only. Do **not** promote to `verified_public` until a Mac/VPS write smoke lands. Health stays **unverified**.

## Root cause: two `AddMoreResults` builds

The results page script shows which page-method contract the host speaks. Nothing is guessed.

| Build | Page JS | Request body | Reply | Hosts seen |
|---|---|---|---|---|
| modern | `JSON.stringify({ searchVals: SearchVals })`; SearchVals has `DateOfBirth` + `BookingNumber` | `{"searchVals": {...}}` | `{"d": {"data", "resultsReturned", "resultsAttempted"}}` | Suwannee, Hamilton, Escambia, Sumter |
| legacy | `JSON.stringify(SearchVals)`; no `DateOfBirth` / `BookingNumber` keys | bare `{...}` | `{"d": {"Data": {...}}}` | Putnam, Bradford, Dixie, Taylor, Santa Rosa |

`fl_smartweb` used to send the modern wrapper everywhere. Legacy hosts answered **HTTP 500**, so those counties emitted only the first page (10–50 cards). The helper now detects the build from the results page and sends and unwraps the matching shape.

## Read smoke (30-day booking window, box, 2026-10-07 ~14:40 EDT)

| County | Official source | Build | Rows before → after | Unique source Booking No | Pattern |
|---|---|---|---:|---:|---|
| **Putnam** | https://smartweb.pcso.us/smartwebclient/Jail.aspx | legacy | 20 → **123** | 123 | `PCSO<YY>JBN<NNNNNN>` |
| **Sumter** | https://portal.sumtercountysheriff.org/smartwebclient/Jail.aspx | modern | 20 → **118** | 118 | `SCSO<YY>JBN<NNNNNN>` |
| Bradford | http://smartweb.bradfordsheriff.org/smartwebclient/Jail.aspx | legacy | 10 → **36** | 36 | `BCSO<YY>JBN<NNNNNN>` |
| Dixie | https://smartcop.dixiecountysheriff.com/smartwebclient/Jail.aspx | legacy | 20 → **33** | 33 | `DCSO<YY>JBN<NNNNNN>` |
| Taylor | http://smartcop.taylorsheriff.org:8989/SmartWEBClient/Jail.aspx | legacy | 10 → **38** | 38 | `TCSO<YY>JBN<NNNNNN>` |
| Santa Rosa | https://jailview.srso.net/SmartWebClient/jail.aspx | legacy | 50 → **153** | 153 (1 card dropped: no matching Booking No) | `SRSO<YY>JBN<NNNNNN>` |
| Escambia | https://inmatelookup.myescambia.com/smartwebclient/jail.aspx | modern | unchanged path | 413 | `ECC<YY>JBN<NNNNNN>` |

Booking date and name are present on every emitted row. Charges and bond come from the card when the source publishes them. Bond is never synthesized: it stays `0` when the card has no amount.

- **Putnam:** the old `%` wildcard POST returned only the 20 most recent current inmates. Its AJAX loop sent the flat payload without the booking-date window, so it never paged. Putnam now uses the shared helper.
- **Sumter:** the old code POSTed a `%` wildcard (20 cards) and then sent the flat payload to a **modern** host, which answered HTTP 500 (re-checked 2026-10-07). Sumter now uses the shared helper. Its agency prefix is `SCSO`, the same as Suwannee's. Dedupe and Mongo keys are `county + booking_number`, so the two do not collide.
- Rows are kept only when the photo `bookno=` equals the visible `Booking No:` text. No name or date keys are created.

## Also checked (no code change)

| County | Finding | Outcome |
|---|---|---|
| St. Johns | sjso.org inmate-search page links `/smartwebclient/jail.aspx`; that path returns **403** (nginx behind Cloudflare) to plain access | **Hold**: no reachable public roster; no WAF bypass. Registry row corrected (was "Active / HTML table"; module is a no-row stub). |
| Hardee | hardeeso.com inmate search links only the OCV mobile app; OCV `inmates.json` S3/CDN paths for the app id return 403 | **Hold**: no public web roster. Registry row corrected (module is a no-row stub). |

## Holds / next steps

1. Run a Mac or VPS **write smoke** for Putnam and Sumter (and the #103 five, which now page past page 1). After it lands, set `SCRAPER_SOURCE_STATES` to `verified_public` and add `live_emitter_evidence.json` rows.
2. Hard holds from earlier passes are unchanged and were not reopened: Sarasota, Clay, Columbia, Okeechobee, Leon, Gadsden, JailTracker FL, and the stub/no-roster counties.
