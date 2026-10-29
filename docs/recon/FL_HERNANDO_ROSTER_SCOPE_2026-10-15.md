# Hernando FL JailSearch roster scope (2026-10-15)

**Question:** is the JailSearch results roster (`https://www.hernandosheriff.org/jail/Applications/JailSearch/`) custody-only?
**Answer:** **no, it's mixed.** Roster rows start with `Status=""`. Custody comes only from each booking's detail page, where the jail publishes `Release Date/Time` (`-` → In Custody, a date → Released, anything else → skipped). That's the #149 contract, unchanged.
**Method:** one honest plain-HTTPS GET of the search page (`scrapers.plain_http`, certificate checks on), plus code and tests. No names or DOBs were recorded.

## Evidence
1. **Probe:** HTTP **200** (22,505 bytes). Honest identity works; no block.
2. **The form offers released bookings.** The search form has a `Release Date` field and an **"Include Released?"** checkbox (`ctl00$ContentPlaceHolder1$cbShowReleased`).
3. **The scraper turns it on.** `hernando.py` posts `cbShowReleased=on` over a 7-day booking-date range, so the results deliberately include people already released.
4. **The detail page is the published custody field.** `lblReleaseDateTime` on `JailSearchDetails.aspx` publishes `-` for not released or a release date/time. #149's live read found it reliable (and 29 of 30 all-$0.00 bookings still in custody). This PR keeps that mapping; it's the source's own field, not a presence default.

No current-population figure was needed: the form's own "Include Released?" option and the scraper's use of it settle the question.
