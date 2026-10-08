# FL Okaloosa — Inmate Locator public JSON API (2026-10-08)

**Scope:** Okaloosa County (FL), volume rank 26 of 67 (Census 2024 estimate 220K). From the FL next-PR queue in `FL_67_STATUS_2026-10-08.md`: rows were being saved with no booking date.
**Method:** plain HTTPS from the agent box (datacenter egress, Python `requests`, TLS verification on). No TLS impersonation, stealth browser, proxy or CAPTCHA/WAF bypass. Every endpoint used is one the official Inmate Locator app calls. Booking numbers come only from the public roster; nothing is enumerated.
**Privacy:** no personal data recorded. Counts and formats only. Test fixtures are synthetic.
**Write smoke:** the box has no `MONGODB_URI`, so this is a read smoke only. Health stays **unverified**.

## Why it was incomplete

#95 moved Okaloosa to the legacy `Default.aspx` Infragistics grid with an A–Z last-name sweep. That grid has no booking date, and the parser found names by walking flattened table cells. The sheriff's current Inmate Locator (`/InmateLocator/`) is an Angular app backed by a public JSON API that has the booking timestamp, bond and charges.

## Contract (live 2026-10-08, ~08:03–08:04 EDT)

| Step | Endpoint | Result |
|---|---|---|
| App config | `GET /InmateLocator/config.json` | 200, `apiBaseUrl` = `https://okaloosacountyjail.myokaloosa.com/InmateLocatorAPI` |
| Roster | `GET /InmateLocatorAPI/api/Inmates/search?page=N&pageSize=100` | 200, `{total: 783, page, pageSize, data}`; 8 pages in <1 s; 783 unique `bookingNo` (10 digits, `YYYY` + sequence); `custodyDate` `YYYY-MM-DDTHH:MM:SS` on all; `status` `1` on 781, `2` on 2 |
| Detail | `GET /InmateLocatorAPI/api/Inmates/<bookingNo>` | 200; `bookingNo` equals the request; `charges[]` with `charge` (statute), `chargeDesc`, `severity`, `bailAmt` (number or null), `bailType`, `caseNbr`, `courtDate` |

- **Bond:** over the 70 bookings in the last 7 days, the roster `totalBondAmt` equals the sum of the detail `bailAmt` values whenever any are published (24/24). When none are published, the roster shows `0` and the detail shows null (46/46). So `0` on the roster means **unknown** and is emitted as `Bond_Amount=""`, never `$0`.
- **Status:** only `status = "1"` is emitted (In Custody). Code `2` is undocumented (2 rows), so those rows are skipped and logged, not guessed.
- **Charges:** detail fetched for bookings in the 7-day window (newest first, cap 300). Each `charge_details` row carries statute, degree, per-charge bond (`None` when null), bond type and case number.
- **Guards:** the walk must reach `total` with unique, well-formed booking numbers, or `OkaloosaContractError` is raised. Shape drift, a failed page, a total that changes mid-walk, or no emitted rows also raise. A detail naming another booking is ignored, and charges then stay empty.

## Read smoke (box, 2026-10-08 ~08:04 EDT)

| Metric | Value |
|---|---:|
| Rows / unique source booking numbers | 781 / 781 (all `^\d{10}$`) |
| Booking date + time | 781 / 781 (was 0 with the Default.aspx grid) |
| Bond > 0 | 429 |
| Bond unknown (blank) | 352 |
| Charges (7-day window details) | 70 |
| Runtime | 11 s |

## Leads Ops

1. Write smoke: `python main.py Okaloosa` with `MONGODB_URI`. Check for 10-digit booking numbers, booking dates filled in, and a blank `bond_amount_raw` where nothing was published.
2. If it is clean, CoS sets `SCRAPER_SOURCE_STATES["Okaloosa (FL)"] = "verified_public"` and adds a `live_emitter_evidence.json` row.
3. Older Okaloosa prod rows with no booking date get the date on the next rescrape of the same booking key. Staff edits survive (#123).

## Total bond only when complete (CoS, 2026-10-08)

A blank `bailAmt` can be a hold, so the booking's `Bond_Amount` is the sum of the detail `bailAmt` only when **every** charge publishes one (a published 0 counts). Otherwise it is `""`. The roster `totalBondAmt` is only the sum of the published charges. In a box probe (08:20 EDT, 60 bookings) 11 were all known, 40 all blank and 9 mixed, and all 9 mixed bookings had a positive roster total that understates the bond. So the roster total is no longer used. Bookings without a detail (outside the 7-day window) carry an unknown bond, and the smoke figure above ("Bond > 0: 429") predates this rule and will now be lower. Per-charge known amounts stay in `charge_details`.

