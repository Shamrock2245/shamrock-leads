# Volusia FL: honest read and custody source (2026-10-22)

**Source:** Volusia County Division of Corrections Inmate Inquiry, `https://volusiamug.vcgov.org/` (ASP.NET WebForms).
**Result:** reachable over honest plain HTTPS. The listing publishes a per-booking `InCustody` column, so status comes from it. The county stays **unverified** (matrix `recon_only`, Health `unverified`) until a Leads Ops write smoke. It is not fail_closed.
**Method:** one run of the module's own flow from the agent box at 2026-10-22 08:15 EDT, through `scrapers.plain_http` (honest User-Agent, certificate checks on, no proxy, browser, impersonation or CAPTCHA). Only HTTP codes, column headers, value counts and booking-number shapes were recorded. No names, DOBs or other person fields were printed or stored. No detail pages were fetched.

## Evidence
| Step | Result |
|---|---|
| `GET /` (disclaimer) | HTTP 200, certificate verified |
| `POST /Disclaimer.aspx` (`ButtonAccept=Accept`) | HTTP 200 |
| `POST /Default.aspx` (`btnRecentBookings=Recent`) | HTTP 200, "Recent Bookings" |

- **Columns, as published:** `Booking #`, `Photo`, `Inmate ID`, `Last Name`, `First Name`, `Middle`, `Suffix`, `Sex`, `Race`, `Booking Date`, `Release Date`, `InCustody`.
- **Rows:** 12 bookings with 12 cells each, plus one 1-cell pager row.
- **Booking #:** 7 digits, 12 unique out of 12. It is the source booking number; `Inmate ID` is a person id and is kept only as `Person_ID`.
- **InCustody:** 11 rows `Y` with a blank Release Date, and 1 row `N` with a Release Date. The column and the release date agreed on every row.

## Rules applied
- `InCustody = Y` gives In Custody and `N` gives Released, with `status_source = published_in_custody_column`. A blank or unrecognised cell falls back to a published Release Date (Released, `published_release_date`), else `""`. The old rule (`"Released" if release_date or in_custody == 'N' else "In Custody"`) read a blank cell as custody, and that rule is gone.
- Bond: the sum of the detail grid's per-charge `Bond Amount` only when every charge row publishes one (a published `$0` is kept), else `""`. Charges are verbatim `statute - description`, else `""`.
- Failures raise typed errors (`egress_block`, `tls_cert_invalid`, `VolusiaHTTPError`, `ParseDriftError`). Nothing is caught and skipped.

## Open
- The Recent Bookings view is a recent window with mixed custody (one released row), so it is not a custody-only roster. That's why status relies on the per-row column, not on presence.
- A write smoke from Leads Ops is still needed before `verified_public`.
