# FL New World (Flagler, Walton): honest probe and custody scope (2026-10-15)

Module: `scrapers/fl_newworld.py`. Probes were minimal and honest: `scrapers.plain_http` with the honest UA, certificate checks on, no impersonation, no proxy and no CAPTCHA. There was one listing GET per county (`?InCustody=True`, page 1). Only counts and labels were recorded; no names or DOBs were logged.

| County | HTTP | Bytes | Title | Source "In Custody" filter checkbox | Listing columns | In Custody column | Detail links |
|---|---|---|---|---|---|---|---|
| Walton | 200 | 65,803 | Inmate Search | echoed checked | Photo, Name, Subject Number, **In Custody**, Scheduled Release Date, Gender, Date of Birth, Height, Weight, Multiple Bookings, Housing Facility | Yes 100/100 | 100 |
| Flagler | 200 | 47,331 | Inmate Search | echoed checked | Name, Subject Number, Scheduled Release Date, Race, Gender, Date of Birth, Height, Weight, Multiple Bookings | **absent** | 100 |

## Finding
- **Walton** publishes custody per person. Status is "In Custody" when the row's In Custody cell reads Yes and the newest booking has no Release Date, with `status_source = "published_in_custody_column"`. If the cell reads No, Status is "".
- **Flagler** has no per-row column. Its listing is the source's own result for the "In Custody" filter, which the page echoes back as checked. For an open booking (no Release Date), Status is "In Custody" with `status_source = "published_in_custody_filter"`. Without the echoed filter, Status is "".
- Booking keys, bond and charges are unchanged: source `Booking YYYY-NNNNNNNN` of the open booking only, the published Total Bond Amount ("" when blank, published $0.00 kept), and published charge descriptions.
- Both counties answered honestly, so neither goes fail_closed. Nassau also uses this module but stays fail_closed (owner hold, incomplete certificate chain).
