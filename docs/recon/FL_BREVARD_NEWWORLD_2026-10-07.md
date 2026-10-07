# FL Brevard + New World (Walton / Flagler) booking keys (2026-10-07)

**Scope:** registered Florida scrapers that had not been reviewed since April. Each was read-smoked from the box and the emitted `Booking_Number` values were checked against the source.
**Method:** plain HTTP(S) from the agent box (datacenter egress, Python `requests`, TLS verification on). No TLS impersonation, stealth browser, proxy, Obscura, or CAPTCHA/WAF bypass.
**Privacy:** no personal data is recorded here. Booking-key formats are shown as patterns only.
**Write smoke:** `MONGODB_URI` was not available here, so these are read smokes only. Do **not** promote to `verified_public` until a Mac/VPS write smoke lands. Health stays **unverified**.

## Summary

| County | Official source | Old emitted key | Read smoke after fix | Source booking key | Outcome |
|---|---|---|---:|---|---|
| **Brevard** | https://inmatesearch.brevardsheriff.org/ | the person's **name** (columns shifted; DOB stored as booking date) | 321 rows / 321 unique (8-day window; 121 in custody) | `Booking #` → `YYYY-NNNNNNNN` | **Fixed** |
| **Walton** | https://nwscorrections.waltonso.org/NewWorld.InMateInquiry/WaltonCounty/ | one shared key, `History`, on every row | 316 in custody / 316 unique | detail `Booking` → `YYYY-NNNNNNNN` | **Fixed** (shared `fl_newworld`) |
| **Flagler** | https://nwwebcad.fcpsn.org/NewWorld.InmateInquiry/FL0180000 | one shared key, `History`, on every row | 215 in custody / 215 unique | detail `Booking` → `YYYY-NNNNNNNN` | **Fixed** (shared `fl_newworld`) |
| Nassau | https://dssinmate.nassauso.com/NewWorld.InmateInquiry/nassau | one shared key, `History`, on every row | not migrated | same New World contract | **Blocked**: the portal sends only its leaf certificate (GoDaddy G2 intermediate missing), so TLS verification fails |
| Alachua | https://asosite.alachuasheriff.org/ASOInmateLookup.aspx | the person's **first name** (full name stored as booking date) | not changed | none on listing; per-person page shows `MNI #` (`ASO<YY>MNI<NNNNNN>`, a person ID) | **Needs owner decision** |

## Brevard (FL 009)

- **Why wrong:** the old requests POST to `/Results` returned HTTP 400 (no antiforgery token), so every run fell back to DrissionPage. The table parser then assumed `Name | Booking # | Date | Charges | Bond`. The real header is `Booking # | Name | DOB | Booking Date | Released`, so the name was stored as `Booking_Number` and the DOB as `Booking_Date`. Dedupe and Mongo keys were the person's name.
- **Contract:** `GET /` gives `__RequestVerificationToken` and the `max` the date inputs allow (the source publishes through the previous day). `POST /?handler=Search` with From/To dates → 302 → `/Results?FromDate=&ToDate=` with one table (no paging; 321 rows for 8 days). Columns are mapped by header text, and header drift raises `BrevardContractError`.
- **Detail:** `/Details/-<id>` has a `Booking Details - YYYY-NNNNNNNN` header, which must equal the listing `Booking #` or the detail is ignored. It also has a `Bonds` card (`Bond Amount`) and a `Charges` card (`Charge`), followed by Booking History cards that are never read. Details are fetched only for `Released = No` rows (121 of 321). Bond is the sum of the published `Bond Amount` cells and stays `0` when none are published.
- **Read smoke (box, 2026-10-07 ~15:10 EDT):** 321 rows, 321 unique, all `^\d{4}-\d{8}$`. Booking date and time are on 321/321. Charges are on 121/121 in-custody rows, and 81 rows have bond > 0. The run took 180 s.

## Walton (FL 131) / Flagler (FL 035) — shared `scrapers/fl_newworld.py`

- **Why wrong:** both parsers took the first `h2`/`h3` starting with "Booking" as the booking number. That is the `Booking History` section heading, so all 100 rows on page 1 got the key `History` (box read smoke of the old code: 100 rows / 1 unique for each county). Walton also walked `InCustody=False`.
- **Contract:** the `InCustody=True` roster, paged with `Page=N` (up to 100 rows per page), links to `/Inmate/Detail/-<id>` (a portal row id, never used as a key). Each detail `div.Booking` block (newest first) has `<h3><label>Booking</label><span>YYYY-NNNNNNNN</span>`, `li.BookingDate`, `li.ReleaseDate`, `li.TotalBondAmount` and a `div.BookingCharges` grid. The emitted booking is the newest block with an empty Release Date. A page with no open booking, or with a malformed one, is dropped. A roster with no detail links raises (layout drift).
- **Read smoke (box, 2026-10-07 ~15:10 EDT):**
  - Walton: 316 rows / 316 unique `^\d{4}-\d{8}$`, booking date on all 316, charges on 315, bond > 0 on 132. 123 s.
  - Flagler: 215 rows / 215 unique, booking date on all 215, charges on 215, bond > 0 on 93. 112 s.

## Blocked / needs decision (no code change)

| County | Finding | Needed to ship |
|---|---|---|
| Nassau | Same New World contract and the same `History` key bug. `dssinmate.nassauso.com` sends only the leaf (`*.nassauso.com`, GoDaddy G2). Plain `requests` fails with `CERTIFICATE_VERIFY_FAILED`. The old module uses `curl_cffi` with `verify=False`. | Either the SO fixes its chain, or an owner decision to vendor the public GoDaddy G2 intermediate so verification stays on. Then it is a 30-line `fl_newworld` wrapper. |
| Alachua | The "View All" GridView has `Last Name | FirstName | Full Name | Book Date | Race | Sex | Age | POD | Arrest Agency`, with no booking number. The old parser stores the first name as `Booking_Number` (984 rows → 618 "unique"). The per-person page publishes `MNI #` (`ASO<YY>MNI<NNNNNN>`), which is a person ID, not a booking ID. | Owner decision: fail-close Alachua (Clay precedent) or accept MNI as the source key (one detail fetch per inmate, about 980 per run). |

## Also read-smoked (no change in this PR)

These emitted plausible source keys from the box: Collier, Hendry, Hernando, Highlands, Indian River (no booking date on rows), Martin, Miami-Dade, Monroe, Osceola, Pasco, Pinellas, Polk, St. Lucie, Volusia, Okaloosa (#95; no booking date on rows). Seminole returned 0 rows from the box (it has 2026-09-23 live-write evidence, so this is likely egress). Orange timed out at 400 s. DeSoto, Duval and Palm Beach (DrissionPage), Hillsborough (credentials + SolveCaptcha) and Marion (residential egress) were not run. Glades already emits source `GCSO<YY>JBN<NNNNNN>` cards (20 current) on its own legacy AddMoreResults path, so it was left alone so this PR would not touch `fl_smartweb`.

## Next steps

1. Mac/VPS **write smoke** for Brevard, Walton and Flagler. After it lands, set `SCRAPER_SOURCE_STATES` to `verified_public` and add `live_emitter_evidence.json` rows.
2. Owner decisions for Nassau (TLS chain) and Alachua (MNI vs fail_closed).
3. Hard holds from earlier passes are unchanged.
