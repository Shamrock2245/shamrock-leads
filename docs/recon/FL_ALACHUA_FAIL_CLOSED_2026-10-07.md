# Alachua (FL) — fail closed, no source booking ID (2026-10-07)

**Scope:** Alachua was emitting rows keyed on each person's first name (984 rows collapsed to ~618 `Booking_Number` values).
**Method:** one plain HTTPS GET plus one "View All" form POST from the agent box (Python stdlib; no TLS impersonation, proxy, Obscura, or CAPTCHA/WAF bypass). No person-level detail pages were opened.
**Privacy:** no personal data recorded. The raw response was deleted after counting.
**Write smoke:** `MONGODB_URI` is not available on the box.

## Source

| Item | Finding |
|---|---|
| Official URL | https://asosite.alachuasheriff.org/ASOInmateLookup.aspx |
| Access | 200, ordinary public ASP.NET form; "View All" POST returns `GridView1` |
| Rows observed | 987 |
| Listing columns | Last Name, FirstName, Full Name, Book Date, Race, Sex, Age, POD, Arrest Agency |
| Source booking number on listing | **None** |
| Row link | `ASOInmateLookup.aspx?lname=…&fname=…` (a name query, not a booking record) |
| Other identifier | Person-level MNI only. It identifies a person, not a booking |

## Root cause

`alachua.py` assumed `Name, Booking#, Booking Date, Charges, Bond` and read column 2 as the booking number. Column 2 is **FirstName**. In this observation there were 620 distinct first names across 987 rows, so `County + Booking_Number` dedup merged unrelated people. Booking Date landed in Charges, and Race landed in Bond.

## Decision

- Person IDs (MNI) and names are not booking keys. This is the same policy as Durham NC and Clay FL.
- `AlachuaCountyScraper.SOURCE_CONTRACT_VALIDATED = False`. `BaseScraper.run()` stops before any source fetch, score, write, or alert. `scrape()` also returns `[]`.
- `SCRAPER_SOURCE_STATES["Alachua (FL)"] = "fail_closed"`. Evidence (FIPS 001) and the matrix are regenerated, and the registry row is updated.
- The form's booking-number search box is not a listing. Do not enumerate booking numbers through it, because sequential identifier probing is prohibited.

## Follow-ups

1. **Prod cleanup (Leads Ops / Mac with Mongo):** existing `county=Alachua` rows whose `booking_number` is a first name (alphabetic, no digits) are invented keys and need review and cleanup. Nothing was deleted from this box.
2. Reopen only when the Sheriff publishes a source booking number on the public listing, or on an ordinary public detail page reachable from it.
