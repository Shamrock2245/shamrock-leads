# NC SSW five — Southern Software Citizen Connect

**Date:** 2026-10-07  
**Scope:** Prove ordinary public Citizen Connect rosters for five deferred NC SSW counties (Henderson, Sampson, Stokes, Surry, Edgecombe) with source-issued `BookingID`. Thin wrappers already use `SouthernSWBaseScraper` (no invented keys). Document contracts + matrix lift from passive unverified inventory.  
**Health:** stays **unverified** until write smoke — do **not** set `verified_public` in this PR.

## Counties (all ordinary public HTTPS)

| County | FIPS | AgencyID | JMSAgencyID | Live scrape (unique BookingID) |
| --- | --- | --- | --- | ---: |
| Henderson | 089 | `HendersonCoNC` | `NC0450000` | 196 |
| Sampson | 163 | `SampsonCoNC` | `NC0820000` | 275 |
| Stokes | 169 | `StokesCoNC` | `NC0850000` | 106 |
| Surry | 171 | `SurryCoNC` | `NC0860000` | 317 |
| Edgecombe | 065 | `EdgecombeCoNC` | `NC0330000` | 261 |

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
- `fetch_current_confinements` IDX=1 → 20 booking-cards; every card carries `BookingID` distinct from `NameID` (samples: Henderson `97946`≠`27651`, Sampson `96480`≠`5060881`, Stokes `2704`≠`1682`, Surry `108431`≠`8033`, Edgecombe `94331`≠`33803`).
- Live `*Scraper.scrape()` → counts in the table; unique `Booking_Number` equals record count for each county; charges/bond present when the card publishes them.
- Same Citizen Connect pattern as Harnett (#100) and SC Dorchester / Chesterfield (`BookingID`) — these five wait on NC write smoke before Health promotion.

## Out of scope / holds

- Promoting any of these five to `verified_public` before write smoke.
- Empty / unproven Citizen Connect agencies (Robeson / Rockingham / Vance / Warren / Wilkes / Granville / Nash) — not in this PR.
- Brunswick Zuercher (no booking/inmate ID), Durham (person ID only), Wayne, P2C/Odyssey metros, empty CC counties — untouched holds.
