# Okaloosa FL: honest probe and custody scope (2026-10-15)

Probes were minimal and honest: `scrapers.plain_http` with the honest UA, certificate checks on, no impersonation, no proxy and no CAPTCHA. No names or DOBs were logged; only counts, keys and labels.

## Requests
1. `GET /InmateLocatorAPI/api/Inmates/search?page=1&pageSize=100` returned HTTP 200 (24,544 bytes), `total` 781.
   - The row keys are bookingNo, custodyDate, dobDttm, fullName, height1, nameID, race, sex, spnNo, status, totalBondAmt, weight1.
   - `status` was "1" for 91 rows and "0" for 9.
2. `GET /InmateLocator/` returned 200, and it loads `main.js`.
3. `GET /InmateLocator/main.js` returned 200 (409 KB). The app's labels are Custody Date, **Released Date** (from detail `releaseDate`), Bail / Fine, Cell Location and so on. The app never reads or displays `status`, and it has no "In Custody" or current-only label. Its titles are "Inmate Locator" and "Inmate Search".

## Finding
- The roster `status` code is not a published custody field (the public UI never shows it). The search isn't a proven custody-only view either: 9 of the first 100 rows had status "0". Status is therefore **""** for every record. The `status == "1"` emission filter (CoS 2026-10-08) is kept, but it no longer sets "In Custody".
- The detail `releaseDate`, which the app shows as "Released Date", is recorded as Release_Date when published.
- Bond is unchanged under the CoS rule: a $0 or null `bailAmt` is unpublished, and the total is computed only when every charge publishes a positive amount, else "". Charges are published `chargeDesc`/`charge` only, "" when none.
- Booking keys are the source `bookingNo` (10 digits) only.
- Okaloosa does not go fail_closed: the API answered honestly.
