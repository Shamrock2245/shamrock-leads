# South Carolina Source-Contract Validation — 2026-08-15

> **Scope:** Metadata-only checks of configured public paths. No roster values, images, profile pages, date-of-birth data, addresses, contact data, blank searches, CAPTCHA workarounds, proxy use, or browser workflows were retained or used. These checks did not write arrest records, send alerts, or change any surety, payment, signing, or bond state.

## Validation results

| County | Configured path outcome | Required broad-listing contract | Runtime decision |
|---|---|---|---|
| Anderson | The Zuercher public origin responded, but the inspection did not establish complete displayed name, source-issued booking identifier, booking time, and bounded pagination together. | Not proven. | **Fail closed** — existing guard remains appropriate. |
| Bamberg | The configured inmate-search path returned HTTP `403`. | Not proven. | **Fail closed** — no source fetch is permitted. |
| Beaufort | The configured roster XML path was unavailable through ordinary access. | Not proven. | **Fail closed** — no source fetch is permitted. |
| Berkeley | The public lookup page responded but did not establish the complete listing contract. | Not proven. | **Fail closed** — no source fetch is permitted. |
| Greenville | Both configured public paths returned HTTP `403`. | Not proven. | **Fail closed** — no source fetch is permitted. |
| Horry | The configured bookings path timed out through ordinary access. | Not proven. | **Fail closed** — no source fetch is permitted. |
| Jasper | The public roster page responded but did not establish the complete listing contract and the prior parser had an unsafe identifier path. | Not proven. | **Fail closed** — no source fetch is permitted. |
| Kershaw | The configured Zuercher host had a DNS error. | Not proven. | **Fail closed** — existing guard remains appropriate. |
| Laurens | The configured Zuercher public origin responded, but the inspection did not establish the complete listing contract. | Not proven. | **Fail closed** — existing guard remains appropriate. |
| Lee | The configured CentralSquare public origin responded but did not establish the complete listing contract. | Not proven. | **Fail closed** — existing guard remains appropriate. |
| Marion | The configured jail path returned HTTP `403`. | Not proven. | **Fail closed** — no source fetch is permitted. |
| Saluda | No configured public roster URL is documented. | Not proven. | **Fail closed** — source retrieval is blocked. |
| Union | No configured public roster URL is documented for the inherited source path. | Not proven. | **Fail closed** — inherited source retrieval is blocked. |
| York | The configured inmate listing path timed out through ordinary access. | Not proven. | **Fail closed** — no source fetch is permitted. |

## Enforcement

`BaseScraper.run()` checks `SOURCE_CONTRACT_VALIDATED` before disk checks, source access, scoring, persistence, broadcast, or alerts. A county module with `SOURCE_CONTRACT_VALIDATED = False` returns an empty, explicitly fail-closed result before calling `scrape()`. Re-enablement requires a county-specific source validation that records the official listing URL, complete public name field, source-issued immutable booking/inmate key, booking or arrest date/time, permitted bounded pagination, and no access-control workaround. The change must then include parser tests, matrix and `SCRAPER_SOURCE_STATES` updates, and first-run persistence/alert telemetry.
