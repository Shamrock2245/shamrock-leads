# Gaston NC New World InmateInquiry roster scope (2026-10-15)

**Question:** does `https://tepsweb.cityofgastonia.com/NewWorld.InmateInquiry/GastonCounty` publish a current-only label or view, or a per-person custody field?
**Answer:** **yes, it publishes a per-person field.** Each listing row has an **"In Custody"** column, and the search form has the source's own "In Custody" filter (`InCustody=True`, which the scraper uses). A row reading `Yes` gives that person's unreleased booking `Status="In Custody"` (`extra_data.status_source="published_in_custody_column"`). Any other or missing value, or a booking with a published Release Date, gives `""`.
**Method:** one honest plain-HTTPS GET (`scrapers.plain_http`, certificate checks on) of the search for today's booking date with `InCustody=True`. Only headers, counts and column values were recorded; no names or DOBs.

## Evidence
1. **Probe:** HTTP **200** (13,494 bytes), page title "Inmate Search".
2. **Source filter:** a checkbox `InCustody` (value `True`), labelled **"In Custody"**.
3. **Listing columns:** Photo, Name, Subject Number, **In Custody**, Scheduled Release Date, Race, Gender, Date of Birth, Height, Weight, Multiple Bookings, Housing Facility. The cell class is `td.InCustody`.
4. **Values:** 14 rows for today, **14/14 "Yes"**.
5. **Booking-level release:** each detail `div.Booking` publishes a Release Date. Released bookings were already skipped and still are, and a released booking never gets In Custody even when the person's row says Yes (a person can have several bookings).

## Booking keys (250 legacy wrong-key rows deleted 2026-09-25)
Unchanged and tested: the only key is the detail heading `Booking YYYY-NNNNNNNN` (`BOOKING_RE`). Subject Number (a person id), bare digits, prefixes or blanks are never emitted. A run where no detail yields a source key raises `ParseDriftError`.
