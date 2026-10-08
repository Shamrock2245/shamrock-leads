# FL Indian River — IRCSO booking search + booking details (2026-10-08)

**Scope:** Indian River County (FL), volume rank 31 of 67 (Census 2024 estimate 172K). From the FL next-PR queue in `FL_67_STATUS_2026-10-08.md`: rows were saved with no booking date.
**Method:** plain HTTPS from the agent box (datacenter egress, Python `requests`, TLS verification on). No TLS impersonation, stealth browser, proxy or CAPTCHA/WAF bypass. Only the site's own public search form and the detail links it returns are used. Nothing is enumerated.
**Privacy:** no personal data recorded. Counts and formats only. Test fixtures are synthetic.
**Write smoke:** the box has no `MONGODB_URI`, so this is a read smoke only. Health stays **unverified**.

## What was wrong

The old module read the `/inmate-search` and `/todays-bookings` cards (about 11 and 6 links). It then:

- used the **portal id** in `/booking-details/<id>` (9–10 digits) as `Booking_Number`. The source booking number is a different value, `YYYY-NNNNNNNN`, and appears only on the detail page;
- saved no booking date;
- wrote bond `0` when no bond was shown;
- used curl_cffi impersonation with `verify=False` (TLS now verifies) and a DrissionPage fallback.

## Contract (live 2026-10-08, ~08:05–08:06 EDT)

| Step | Request | Result |
|---|---|---|
| Landing | `GET /inmate-search` | 200; `form#searchform` (`POST booking-search/search`) with `_token`, `lname`, `fname`, `booking_date` (`MM/DD/YYYY`), `release_date`, `booking_number` (`YYYY-########`), `dob` |
| Search | `POST /booking-search/search` with `_token` + `booking_date` | 200; up to 10 `/booking-details/<id>` links per page |
| Paging | `GET /booking-search/search?booking_date=…&page=N` | further pages (10/06: 15 bookings over 2 pages) |
| Detail | `GET /booking-details/<id>` | `Booking Info` rows: `Booking Date`, `Arrest Date`, `Arresting Agency`, `Booking Number` (`YYYY-NNNNNNNN`), `Case Number`, `Bond`; `Release Date` once released; `Charges` cards |

- On 10/06, 15 of 15 details had a `Booking Date` equal to the searched date. The scraper drops any row whose detail date differs.
- `Bond` was published as an amount on 5 of 15, `No Bond` on 4, and was absent on 6. An amount is used as published. `No Bond` gives `Bond_Type=NO BOND` with an empty amount. Absent means unknown (empty). Nothing becomes `0`.
- Guards: a missing `_token`/form field raises. A day still paging at 30 pages raises. Details fetched but none with a source Booking Number raises. A detail without a well-formed Booking Number or a parseable Booking Date is dropped.
- Custody recheck (`_fetch_single_booking`) returns a record only when the stored detail URL is on `www.ircsheriff.org` and the page still shows the same source Booking Number. Legacy rows keyed on the portal id are reported as not found.

## Read smoke (box, 2026-10-08 ~08:06 EDT, 7 booking dates)

| Metric | Value |
|---|---:|
| Rows / unique source Booking Numbers | 59 / 59 (all `^\d{4}-\d{8}$`) |
| Booking date + time | 59 / 59 |
| Charges | 59 / 59 |
| Bond amount published | 15 |
| No Bond | 15 |
| Bond unknown (absent) | 29 |
| Released (Release Date present) | 23 |
| Dropped details | 0 |
| Runtime | ~51 s |

## Leads Ops

1. Write smoke: `python main.py "Indian River"` with `MONGODB_URI`. Check for `YYYY-NNNNNNNN` keys, booking dates filled in, and a blank `bond_amount_raw` where nothing was published.
2. If it is clean, CoS sets `SCRAPER_SOURCE_STATES["Indian River (FL)"] = "verified_public"` and adds a `live_emitter_evidence.json` row.
3. Older prod rows keyed on the portal id stay as they are (nothing deleted). They will not be refreshed under the new key, so Leads Ops may want to archive them.

## Legacy portal-id rows and the custody recheck (traced 2026-10-08)

Rows written before this PR are keyed on the portal id (`/booking-details/<id>`). `_fetch_single_booking` returns `None` for them because the page's Booking Number differs. Every caller treats `None` as unknown. None of them mark the booking released or delete it:

- `core/scheduler.py` `_handle_custody_recheck` counts the row as `not_found` and inserts a `custody_rechecks` note (`source_found: False`, proposed change "Not Found on Roster"). It then `continue`s with **no write to `arrests`**: status, bond, charges and the record are unchanged. A test now pins this: `test_custody_recheck_of_legacy_portal_id_row_changes_nothing`.
- `dashboard/routers/scraper_control.py` (`/custody-recheck/results`) and `legacy.py` (`/leads/refresh-from-source/status`) only read the trigger and `custody_rechecks`. `sl-features.js` shows a "🚪 Not on Roster" badge on the card. The badge is display only, but staff could read it as released for these legacy rows. This behavior predates this PR and applies to every county whose single fetch returns `None`.
- `core/first_appearance_watcher.py` `_refetch_record`: on `None` it falls back to the generic GET of the stored `detail_url`, which only raises a positive bond parsed from that same booking's page and keeps the stored key. If that also fails, it writes just `last_checked`/`last_checked_mode` (`_checked_update`) and skips.
- `refresh-from-source` parses the stored page with the generic parser and queues the same scheduler recheck; it never calls `_fetch_single_booking`.
- No release sweep or purge acts on an empty recheck. The retention and hygiene routers work on age and explicit admin actions only.

## Codex follow-up (2026-10-08)

- **Empty pages:** a live search for a date with no bookings (10/20/2026, 08:33 EDT) returns HTTP 200 with "No results found!". A day with bookings shows `inmate inmate-list clearfix` cards. The first results page must show one or the other, or the run raises `IndianRiverContractError`.
- **Detail failures:** a detail GET that times out or returns an HTTP error now escapes, so BaseScraper retries, classifies and alerts. Rows are still dropped, and counted, only for content reasons: no key or date, a wrong day, or a duplicate.
- Read smoke at 08:35 EDT: 60/60 unique rows, 59 with charges, 15 with a bond, 15 No Bond, 23 released, 50 s.

