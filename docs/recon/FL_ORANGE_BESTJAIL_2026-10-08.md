# FL Orange — BestJail public JSON (2026-10-08)

**Scope:** Orange County (FL), volume rank 5 of 67 (Census 2024 estimate 1.53M). Ranked first in the FL next-PR queue in [`FL_67_STATUS_2026-10-08.md`](./FL_67_STATUS_2026-10-08.md) among dark counties with a plausible public contract.
**Method:** plain HTTPS from the agent box (datacenter egress, Python `requests`, TLS verification on). No TLS impersonation, stealth browser, proxy or CAPTCHA/WAF bypass. Every endpoint used here is one the county's own public page calls. Booking numbers come only from the public roster; nothing is enumerated.
**Privacy:** no personal data recorded here. Counts and formats only. Test fixtures are synthetic.
**Write smoke:** the box has no `MONGODB_URI`, so this is a read smoke only. Health stays **unverified** until a Leads Ops write smoke.

## Why it was dark

The #106 notes record that Orange "timed out at 400 s" from the box. The old module:

- fetched details **and** charges for every current-year inmate (~2,400 bookings × 2 calls, with pauses), so it could not finish;
- used curl_cffi Chrome impersonation, and swallowed every error (a failed letter silently gave a partial roster);
- wrote `$0.00` when no bond was published (an invented $0), and `"Not Available"` as a charge;
- stored `BIRTH`, which is an **age**, as the date of birth.

## Contract (live 2026-10-08, ~07:57–08:00 EDT)

| Step | Endpoint | Result |
|---|---|---|
| Landing | `GET https://netapps.ocfl.net/BestJail/Home/Inmates` | 200, "Inmates - OCFL". The page script calls the three JSON endpoints below |
| Roster | `GET /BestJail/Home/getInmates/<letter>` × 26 | all 200, ~1.2 s total; `[{bookingNumber, inmateName}]`; 2,863 distinct current bookings; every letter non-empty (rarest `x` = 2) |
| Detail | `GET /BestJail/Home/getInmateDetails/<bookingNumber>` | one-element list; `BOOKING` equals the requested number (317/317); `DATEBOOKED` `MM/DD/YYYY` + `TIMEBOOKED` `H:MMam/pm`; `BIRTH` is a 2-digit age |
| Charges | `GET /BestJail/Home/getCharges/<bookingNumber>` | one row per charge: `Charge`, `BondAmount` (`NNNN.NN` or blank), `ArrestingAgency`, `CourtCaseNumber`, `CaseStatus` |

- **Booking key:** source `bookingNumber`, 8 digits (`YY` + sequence), with prefixes `17`–`26` on the current roster.
- **Aliases:** the roster lists a booking once per name on file. Almost every booking appears under more than one name, so rows are deduped on the booking number, and the record name is the detail `NAME` (the booking's primary name).
- **Order:** booking numbers are sequential. Over the 317 newest, booking timestamps never increased as the number went down. The scraper walks newest first and stops after 15 consecutive bookings older than the 7-day window, or at 800 details.
- **Bond:** the sum of the published `BondAmount` cells. No published cell, no charges, or a failed charges fetch gives `Bond_Amount=""` (unknown). A published `0.00` stays `0.00`. Each `charge_details` row carries its own `bond_amount`, which is `None` when blank.
- **Guards:** any letter non-200/non-list, row shape drift, a malformed booking number, more than 3 empty letters, an empty roster, or every detail failing/mismatching raises `OrangeContractError` (no partial or empty write). A detail naming another booking, or one with no booking date, is skipped.

## Read smoke (box, 2026-10-08 ~08:00 EDT, 7-day window)

| Metric | Value |
|---|---:|
| Rows / unique source booking numbers | 303 / 303 (all `^\d{8}$`) |
| Booking date + time | 303 / 303 |
| Charges present | 287 (16 bookings had no charge rows yet) |
| Bond > 0 published | 200 |
| Bond `0.00` published | 70 |
| Bond unknown (blank) | 33 |
| Age set / DOB set | 303 / 0 |
| Runtime | 37 s (old module: >400 s timeout) |

One earlier run in the same window raised `empty roster for all 26 letters`. A rerun a minute later was normal. That is the guard working as intended: the run fails, and nothing empty or partial is written.

## Leads Ops

1. Write smoke: `python main.py Orange` with `MONGODB_URI` set. Check for 8-digit booking numbers, a blank `bond_amount_raw` where nothing was published, and charges filled in.
2. If it is clean, CoS sets `SCRAPER_SOURCE_STATES["Orange (FL)"] = "verified_public"` and adds a `live_emitter_evidence.json` row.
3. Older Orange prod rows may carry `$0.00` bonds and an age in DOB. Leave them; rescrapes of in-custody bookings in the window overwrite the scraped fields (staff edits survive, #123).
