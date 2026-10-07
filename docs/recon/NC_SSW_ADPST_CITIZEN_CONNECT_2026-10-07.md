# NC SSW ADPST — Southern Software Citizen Connect

**Date:** 2026-10-07  
**Scope:** Prove ordinary public Citizen Connect rosters for the five remaining deferred NC SSW counties after #101 (Anson, Duplin, Polk, Scotland, Transylvania) with source-issued `BookingID`. Thin wrappers already use `SouthernSWBaseScraper` (no invented keys). Document contracts + matrix lift from passive unverified / Scotland fail_closed inventory.  
**Health:** stays **unverified** until write smoke — do **not** set `verified_public` in this PR.

## Counties (all ordinary public HTTPS)

| County | FIPS | AgencyID | JMSAgencyID | Live scrape (unique BookingID) |
| --- | --- | --- | --- | ---: |
| Anson | 007 | `AnsonCoNC` | `NC0040000` | 82 |
| Duplin | 061 | `DuplinCoNC` | `NC0310000` | 161 |
| Polk | 149 | `PolkCoNC` | `NC0750000` | 43 |
| Scotland | 165 | `ScotlandCoNC` | `NC0830000` | 157 |
| Transylvania | 175 | `TransylvaniaCoNC` | `NC0880000` | 79 |

Portal pattern (each): `https://cc.southernsoftware.com/bookingsearch/index.php?AgencyID={AgencyID}`  
Roster: `POST /bookingsearch/fetchesforajax/fetch_current_confinements.php` (`JMSAgencyID`, `IDX` pages).  
Access: ordinary public HTTPS from datacenter egress (box recon 200). No login, CAPTCHA, residential proxy, or stealth.

## Fields (source-published only)

| Shamrock field | Source | Notes |
| --- | --- | --- |
| `Booking_Number` | card `BookingID` | From `View Full Details` href `?BookingID=`, `data-bookingid`, or mugshot debug `BookingID=`. **Never** invent. **Never** use `NameID` alone (person-level). |
| `Full_Name` / parts | card `<h5>` | As published. |
| `Booking_Date` | `Booked:` | Required; cards without Booked are skipped. |
| `Charges` | charge-item / card charge lines | Joined with ` \| `; only as published. |
| `Bond_Amount` | `Bond Total:` or summed `Bond:` lines | Digits only; if absent → `"0"` (not invented dollars). |

## Box recon evidence (2026-10-07)

- Each index `AgencyID=…` → 200 with the JMS values above.
- `fetch_current_confinements` IDX=1 → booking-cards; every card carries `BookingID` distinct from `NameID` (samples: Anson `22231`≠`8045`, Duplin `48682`≠`5802`, Polk `10041335`≠`47340`, Scotland `10059982`≠`94943`, Transylvania `56134`≠`74860`).
- Live `*Scraper.scrape()` → counts in the table; unique `Booking_Number` equals record count for each county; charges/bond present when the card publishes them.
- Scotland lifted from `SOURCE_CONTRACT_VALIDATED=False` / `SCRAPER_SOURCE_STATES` `fail_closed` (prior “no configured public roster URL” guard) after the Citizen Connect BookingID contract was proven.
- Same Citizen Connect pattern as Harnett (#100) and SSW five (#101) — wait on NC write smoke before Health promotion.

## Out of scope / holds

- Promoting any of these five to `verified_public` before write smoke.
- Empty / unproven Citizen Connect agencies (Robeson / Rockingham / Vance / Warren / Wilkes / Granville / Nash) — still redirect to the CC directory with no roster; not in this PR.
- Brunswick Zuercher (no booking/inmate ID), Durham (person ID only), Wayne, P2C/Odyssey metros, Buncombe…Stanly without live_emitter — untouched holds.
