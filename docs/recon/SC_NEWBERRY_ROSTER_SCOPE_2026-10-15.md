# Newberry SC bookings PDF roster scope (2026-10-15)

**Question:** is the Newberry County bookings PDF, linked from the official inmate-search page, custody-only, or does the source publish a current-only label or view?
**Answer:** **no, it's mixed.** A published `Released MM/DD/YYYY` line gives `Status="Released"` + `Release_Date` (`extra_data.status_source="published_release_line"`). Every other booking is `""`. Presence never sets "In Custody".
**Method:** two honest plain-HTTPS GETs (`scrapers.plain_http`, certificate checks on): the inmate-search page and the one Sheriff PDF it links. Only counts and line shapes were recorded; no names or DOBs.

## Evidence
1. **Probe:** page HTTP **200** (97,829 bytes); PDF HTTP **200** (109,698 bytes, `%PDF`), file `10-05-2026-to-10-12-2026.pdf`.
2. **Page wording:** "Due to technical difficulties the inmate search is temporary unavailable. However, a PDF of our current information is available below ... View the latest bookings (PDF)". It calls the PDF "current information" and "the latest bookings", not a current-inmate list. There's no Current/Released view like Florence's.
3. **PDF title:** every page reads **"Prisoners/Charges Booked In by Date Range"**. It's a booking report for a date range (sections 10/05–10/11/2026), not a roster.
4. **Released people are listed:** 32 booking headers and **21 `Released <date> - <reason>` lines** (reasons include POSTED BOND, PR BOND, TIME SERVED, DISMISSED, RECALLED). A large share of the listed people were already released.
5. **No population figure** is published on the page.

So a booking without a `Released` line may still be in custody, or may have been released after the report was cut. It stays `""`.

## Also fixed
The old parser used the release date as `Booking_Date` when no section date preceded a booking. `Booking_Date` is now the PDF section date only.
