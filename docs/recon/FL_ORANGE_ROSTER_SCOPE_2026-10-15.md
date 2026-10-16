# Orange FL BestJail roster scope (2026-10-15)

**Question:** does `https://netapps.ocfl.net/BestJail/Home/Inmates` (and its `getInmates/<letter>` JSON) list only people currently in custody?
**Answer:** yes. Orange records built from the roster carry `Status="In Custody"` from roster presence, unless a published custody field says otherwise.
**Method:** honest plain HTTPS (`scrapers.plain_http`, certificate checks on, no stealth or proxy). Reads only: the portal page once (plus one re-read for the notice text), the county population PDF once, and the 26 `getInmates` letters with 0.5 s pauses (the same calls the scraper makes every run). No details or charges were fetched. No names or DOBs were recorded.

## Evidence
1. **Page labels (county's own wording).** Heading "Current Inmate Database": "This database lists people currently in jail and includes information on their charges, bond amount, and booking photo."
2. **Release notice.** "This information is updated every 30 minutes. ... When a person is released from jail, they will no longer appear in these listings."
3. **No release fields.** `getInmates/<letter>` rows have exactly two keys, `bookingNumber` and `inmateName`. There's no release date, release flag or status. The code and fixtures (`tests/test_fl_orange_bestjail.py`) match this.
4. **Count matches the published population.** The county's "Daily Inmate Population Statistics" (`/BestJail/PDF/ethnicity_values.pdf`, stamped 10/15/2026 1:08 AM ET) gives a total of **2,976** (430 female, 2,546 male). The roster read at about 12:40 PM ET the same day had **2,919** distinct booking numbers (98.1%). The ~2% gap fits roughly 11 hours of churn between the two snapshots. If released people were on the roster, the count would be well above the population, not just under it. The 2026-10-08 recon count (2,863) is in the same range.
5. **Separate history report.** The page links the Daily Booking List (`/BestJail/PDF/bookings.pdf`) separately for "all inmates booked during a 24-hour period". So booking history isn't mixed into the search roster.

## Limits
- The roster updates every 30 minutes, so someone released inside that window can show as In Custody until the next refresh and rescrape.
- Status is a scrape-time snapshot. A later release is caught when a later run no longer sees the booking, not by this scraper.
