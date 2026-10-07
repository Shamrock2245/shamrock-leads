# FL Sarasota + Manatee end-to-end audit (2026-10-07)

**Owner priority:** Brendan, via Chief of Staff. Get Sarasota and Manatee into top working order.
**Method:** live plain-HTTPS fetches from the agent box (datacenter egress, `curl`, ~18:42–18:50 EDT), plus one run of the Manatee scraper itself in `direct` mode from the box. No proxy, CAPTCHA solving, stealth tooling or WAF workaround was added or used to get past a block.
**Privacy:** no personal data is recorded here. Counts and key formats only. Test fixtures use synthetic names.
**Write smoke:** the box has no `MONGODB_URI`. Health stays as is (Sarasota `fail_closed`, Manatee `unverified`) until a Leads Ops prod write smoke.

## Live evidence

| URL | Result |
|---|---|
| `https://manatee-sheriff.revize.com/`, `/bookings`, `/bookings?page=2` | **403**, `server: cloudflare`, `cf-mitigated: challenge`, title "Just a moment..." |
| `https://cms.revize.com/revize/apps/sarasota/` | **200**, "Current Inmate Population" dropdown with **1,081** `viewInmate.php?id=<10 digits>` entries (1,081 unique), each labelled `LAST,FIRST MIDDLE - MM/DD/YYYY` (name + date of birth). No booking number, no booking date/time. |
| `https://cms.revize.com/revize/apps/sarasota/index.php` (the URL `sarasotasheriff.org/arrest-reports/` links to), `viewInmate.php?id=…`, `personSearch.php?type=date|name` | **403** `cf-mitigated: challenge` |
| `https://www.manateesheriff.com/arrest_inquiries/` | 200 informational page; no roster |
| Manatee scraper, `MANATEE_EGRESS_MODE=direct`, box | Run 1 (before the preflight fix): the exit check passed because ipinfo/ipapi were rate-limited and org/country came back empty. Page 1 then sat on the Cloudflare challenge, and the new code raised `egress_block: Manatee page 1 stuck on a Cloudflare challenge/block (HTTP 403)`, exit 2, nothing written. Run 2 (after the fix): `egress_block: … this host's exit is not US residential (org='')` before any page load. |

## Manatee: item by item

| # | Audit item | State | Evidence / change |
|---|---|---|---|
| 1 | Source contract (URL, method, shape, WAF) | **blocked from box; code fixed** | Revize `/bookings` HTML table behind Cloudflare; box egress gets 403 challenge. The roster loads only from residential egress, so the live column set could not be re-read today. The parser now asserts the documented header contract and fails closed if it differs. |
| 2 | Unique real booking keys | **fixed** | `Booking_Number` = source `Booking #` cell, checked against the row's `/bookings/<id>` link (mismatch = column shift → `ParseDriftError`). Name-like or malformed cells are dropped; a page of only those fails closed. One booking number naming two people → `ParseDriftError`. Duplicate rows of one booking are merged, not dropped. |
| 3 | Bond amount / type per charge | **fixed (unknown)** | The roster publishes no bond and detail pages are CF-blocked. `Bond_Amount=""` and `Bond_Type=""` (previously `"0"`, which triggered the scorer's $0 −50). No mixed types exist to derive. |
| 4 | Charges, statute, degree | **fixed / blocked** | All charge rows for a booking are kept (`Charges` pipe-joined, plus `extra_data.charge_details`). Previously only the first row's charge survived. Statute and degree are not on the roster (detail pages only): **blocked**. |
| 5 | Booking date/time, release, released flag, mugshot | **fixed** | `Arrest Date` (+ time when present) → `Arrest_*`/`Booking_*` (`booking_date_origin="roster Arrest Date"`; no separate booking timestamp on the roster). `Released` is a required column: blank / In Custody / No / N/A means in custody, Yes / Released / a date means released (date → `Release_Date`), anything else → `ParseDriftError`. Mugshot only when the row has an `<img>`. |
| 6 | Address / DOB | **blocked (not published)** | Not on the roster; detail pages CF-blocked. Nothing invented. |
| 7 | Paging completeness + drift guards | **fixed** | Walks the pager's next link or numbered pages. Fails closed on: no table, missing column, empty page 1, empty page the pager offered, repeated page (same booking sequence) or a pager loop, `MAX_PAGES` with a next page still offered, and a walked row/booking count that differs from a published "Showing x to y of N" / "of N bookings" total. Previously a repeat or a CF page ended the walk silently and wrote partial data. |
| 8 | One-click hydrate for PDFs | **fixed** | Traced arrests doc → `resolve_case_context` → `charge_details_from_sources` → `build_adaptive_field_map` / DocuSeal prefill / Write Bond modal. `to_mongo_doc` stores `bond_amount=0.0` for an unknown bond, and hydrate read that as a known $0. Hydrate now prefers `bond_amount_raw`, sets `bond_amount_known`, and gives per-charge rows `bond_amount=None` when unknown (the Write Bond modal shows blank, not `$0.00`). A published `0` stays `0.0`. Booking number, names and offense_1..n land in the right keys. |
| 9 | Health + matrix | **ok** | Health stays default `unverified` (not in `SCRAPER_SOURCE_STATES`). Evidence row FL/081 updated; matrix regenerated, `--check` passes. |
| — | Residential coverage | **fixed in code; run pending** | `EgressBlocked` (`anti_bot`, `egress_block=True`, not retried) on a CF challenge/block or no residential exit; never an empty run. `MANATEE_EGRESS_MODE=direct` for Mac/hotspot runs (no proxy; host must be US residential). `check_exit_ip` no longer treats an exit with an unknown org/country as residential. This was a live fail-open: the box passed it. Ops note `docs/ops/MANATEE_RESIDENTIAL_RUN.md`; read smoke `scripts/manatee_residential_smoke.py`. |

## Sarasota: item by item

| # | Audit item | State | Evidence |
|---|---|---|---|
| 1 | Source contract | **blocked (source)** | The only official public listing is the current-inmate dropdown above. Sheriff-linked index, detail and search pages are CF-challenged from the box. |
| 2 | Booking keys | **blocked** | Listing key is an opaque per-person link id (`viewInmate.php?id=`), not a published booking number: the same block as Alachua (#107) and Clay. Whether it equals a booking number is only visible on the CF-challenged detail page. **Not reopened.** |
| 3–6 | Bond, charges, dates, released, mugshot, address/DOB | **blocked** | Not on the listing (name + date of birth only); detail pages CF-challenged. |
| 7 | Paging | n/a | Single listing page; no scraping while closed. |
| 8 | Hydrate | n/a | No records emitted. |
| 9 | Health + matrix | **ok** | `SOURCE_CONTRACT_VALIDATED=False`, Health `fail_closed`, `scrape()` returns `[]` without network. Reason text updated with today's evidence. Evidence row FL/115 now names the official URL; matrix regenerated. Reopen gate encoded in `scrapers/counties/sarasota_contract.py` (`assess_listing`): needs a source booking number **and** a booking date/time on an ordinary-access page. |

## Tests

`tests/test_fl_sarasota_manatee_audit.py` (added to CI, with `test_sarasota_safety.py`, `test_packet_builder_service.py`, `test_cf_browser.py` and `test_residential_proxy.py`) covers: header mapping, charge grouping, bond empty vs 0 and the scorer, malformed/name keys, link mismatch, key collision, Released values plus drift and the missing column, mugshot, published-total match/mismatch, page-size text not read as a total, repeated page, `MAX_PAGES`, missing table / empty pages, numbered pager, the live CF challenge classified as an egress block, direct/auto egress modes, scrape raising `EgressBlocked` instead of `[]`, the rate-limited exit check, hydrate unknown-bond vs published-0 mapping (including `resolve_case_context`), and the Sarasota reopen gate and fail-closed state.

## Remaining

| Item | Owner |
|---|---|
| Manatee residential **read** smoke (`MANATEE_EGRESS_MODE=direct python scripts/manatee_residential_smoke.py`) to confirm the live header set, Released values and pager text against the new guards | Leads Ops (Mac / iPhone hotspot) |
| Manatee prod **write** smoke, then Health → `verified_public` decision | Leads Ops → Chief of Staff |
| Manatee bond / statute / degree / DOB / address: only on `/bookings/<id>` detail pages (CF-blocked). Check from residential egress whether they load; if they do, add a detail fetch in a follow-up | Leads Ops check, then Scraper Watch |
| Sarasota: reopen needs a booking number + booking timestamp on an ordinary-access official page | Source-side block (Sarasota SO) |
| Charlotte shares Manatee's Revize design and still emits `Bond_Amount="0"` and has none of these guards. Not changed here, to keep this PR to the two owner-priority counties | Scraper Watch follow-up (CoS to schedule) |
| The existing `auto` resolver and `cf_browser` still use APE/Warren proxies and Patchright + init-script patches. Not touched here; whether to keep them is a policy call | Chief of Staff / Brendan |
