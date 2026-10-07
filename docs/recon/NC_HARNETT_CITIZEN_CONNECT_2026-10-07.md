# Harnett County (NC) — Southern Software Citizen Connect

**Date:** 2026-10-07  
**Scope:** Prove the ordinary public Citizen Connect roster for `AgencyID=HarnettCoNC` with source-issued `BookingID`. Thin wrapper already uses `SouthernSWBaseScraper` (no invented keys). Document contract + matrix lift from passive `fail_closed` / unverified inventory.  
**Health:** stays **unverified** until a write smoke — do **not** set `verified_public` in this PR.

## Official source

| Piece | Value |
| --- | --- |
| Portal | https://cc.southernsoftware.com/bookingsearch/index.php?AgencyID=HarnettCoNC |
| JMSAgencyID | `NC0430000` (hidden input on index) |
| Roster | `POST /bookingsearch/fetchesforajax/fetch_current_confinements.php` (`JMSAgencyID`, `IDX` pages) |
| Access | Ordinary public HTTPS from datacenter egress (box recon 200). No login, CAPTCHA, residential proxy, or stealth. |

## Fields (source-published only)

| Shamrock field | Source | Notes |
| --- | --- | --- |
| `Booking_Number` | card `BookingID` | From `View Full Details` href `?BookingID=`, `data-bookingid`, or mugshot debug `BookingID=`. **Never** invent. **Never** use `NameID` alone (person-level). |
| `Full_Name` / parts | card `<h5>` | As published. |
| `Booking_Date` | `Booked:` | Required; cards without Booked are skipped. |
| `Charges` | charge-item / card charge lines | Joined with ` \| `; only as published. |
| `Bond_Amount` | `Bond Total:` or summed `Bond:` lines | Digits only; if absent → `"0"` (not invented dollars). |

## Box recon evidence (2026-10-07)

- Index `AgencyID=HarnettCoNC` → 200; `JMSAgencyID=NC0430000`.
- `fetch_current_confinements` IDX=1 → 20 booking-cards; every card carries `BookingID` in href (samples `1381`, `2394`, `2628`) distinct from `NameID` (e.g. `1242` vs `1381`).
- Live `HarnettScraper.scrape()` → **306** records, **306** unique `Booking_Number` values; charges/bond present when the card publishes them (e.g. Bond Total `$25,000.00`).
- Same Citizen Connect pattern as SC Dorchester / Chesterfield (`BookingID`), which already write-smoked to `verified_public` — Harnett waits on NC write smoke before Health promotion.

## Same-day NC queue notes (no fluff PR this turn)

| County | Finding |
| --- | --- |
| **Henderson / Sampson / Stokes / Surry / Edgecombe** | Same Citizen Connect contract; live cards + source `BookingID` (196 / 275 / … / 261 records). Next candidates after Harnett write smoke — not bundled here. |
| **Robeson / Rockingham / Vance / Warren / Wilkes / Granville / Nash** | Citizen Connect `fetch_current_confinements` returned empty/invalid (~92 bytes) from box — not proven. |
| **Brunswick** | Zuercher `brunswick-so-nc.zuercherportal.com` roster live (~294) but API rows lack `booking_number` / `inmate_id` / `booking_date` — **hold** (same class as SC Oconee/Pickens). Do not invent keys. |
| Durham / Wayne / P2C·Odyssey·DCN / Wake / Forsyth / Cumberland / Onslow / Rowan | Untouched prior holds. Davidson (#99) / Mecklenburg (#97) untouched. |

## Out of scope

- Promoting Harnett to `verified_public` before write smoke.
- Bundling the other proven NC SSW counties into this PR.
- Reopening Brunswick Zuercher without a source booking/inmate ID.
