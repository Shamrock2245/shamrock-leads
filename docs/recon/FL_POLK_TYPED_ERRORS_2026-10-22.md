# Polk FL: honest read for the typed-error change (2026-10-22)

**Source:** Polk County Sheriff's Office Jail Inquiry, `https://www.polksheriff.org/detention/jail-inquiry` (Kendo UI JSON API).
**Result:** reachable over honest plain HTTPS. Polk stays **unverified** (matrix `recon_only`, Health `unverified`) until a Leads Ops write smoke. It is not fail_closed.
**Method:** from the agent box at 2026-10-22 08:24 EDT, through `scrapers.plain_http` (honest User-Agent, certificate checks on, no proxy, browser, impersonation or CAPTCHA). Two SearchJail POSTs, one profile GET and one GetCharges POST. Only HTTP codes, content types, field names, counts and number shapes were printed. No names, DOBs or other person values were printed or stored.

## Evidence
| Step | Result |
|---|---|
| `POST SearchJail` (BookingDate = 2026-10-22, early morning) | 200 `application/json`, empty list |
| `POST SearchJail` (BookingDate = 2026-10-21) | 200 `application/json`, list of 22 objects |
| `GET /inmate-profile/<booking>` (one booking) | 200, `__RequestVerificationToken` present |
| `POST /inmate/GetCharges` (same booking) | 200 `application/json`, list of 3 objects |

- **SearchJail fields:** `BookNo`, `BookNumberDisplay`, `BookYr`, `BookingNum`, `DOB`, `EntryDate`, `FullName`, `IsAka`, `Location`, `RS`, `ReleaseDate`. There is no status or custody field.
- **SearchJail values:** `BookingNum` is 7 digits, 22 unique out of 22. `ReleaseDate` is set on 1 of 22. `Location` is CCJ 17, SCJ 4, blank 1.
- **GetCharges fields:** `ArrestAgencyDesc`, `ArrestDocketCharge`, `BondAmountDue`, `BondAmountPaid`, `BondExp`, `BondPurgeNumber`, `BondType`, `Cash`, `ChargeDesc`, `ChgNum`, `Disposition`, `Statute`, `Surety`. `BondAmountDue` was published on all 3 charges.

## What this confirms
- The search is by booking date and includes released bookings, so presence is not custody. Status is Released from a published `ReleaseDate`, otherwise `""` (`status_source` recorded).
- An empty list is a legitimate empty day (seen live), not drift. A non-list or non-object payload is drift.
- The typed-error paths (block, TLS, `PolkHTTPError`, `ParseDriftError`, counted detail misses) are covered by `tests/test_fl_polk_typed_errors.py`. None were triggered live.
