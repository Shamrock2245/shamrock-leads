# North Carolina Source-Contract Validation — 2026-08-15

> **Scope:** Metadata-only checks of configured public paths. No roster values, images, profile pages, date-of-birth data, addresses, contact data, blank searches, CAPTCHA workarounds, proxy use, or browser workflows were retained or used. These checks did not write arrest records, send alerts, or change any surety, payment, signing, or bond state.

## Validation results

| County | Configured path outcome | Required broad-listing contract | Runtime decision |
|---|---|---|---|
| Caldwell | The configured daily-inmate PDF route redirected and the public origin did not establish a complete listing contract; the previous parser used TLS bypass. | Not proven. | **Fail closed** — no PDF or source fetch is permitted. |
| Chatham | The public page and OCV JSON route responded, but the inspection did not establish complete displayed name, source-issued booking identifier, booking time, and bounded pagination together. | Not proven. | **Fail closed** — no OCV or source fetch is permitted. |
| Cumberland | Both configured inmate paths were unavailable through ordinary access. | Not proven. | **Fail closed** — no P2C or source fallback is permitted. |
| Davidson | The configured XML and public paths responded, but the complete booking-safe listing contract was not established and the prior path included an unsafe identifier fallback. | Not proven. | **Fail closed** — no source fetch is permitted. |
| Guilford | The configured inmate-lookup path returned HTTP `404`. | Not proven. | **Fail closed** — no source fetch is permitted. |
| Halifax | The configured DCN path was unavailable through ordinary access. | Not proven. | **Fail closed** — inherited source retrieval is blocked. |
| Randolph | The configured legacy roster was unavailable through ordinary access. | Not proven. | **Fail closed** — no source fetch is permitted. |
| Scotland | No configured public roster URL is documented for the inherited source path. | Not proven. | **Fail closed** — inherited source retrieval is blocked. |
| Union | The configured P2C public path returned HTTP `403`; its existing explicit source guard remains appropriate. | Not proven. | **Fail closed** — source retrieval remains blocked. |
| Wake | Both configured public paths were unavailable through ordinary access. | Not proven. | **Fail closed** — no P2C or source fallback is permitted. |

## Enforcement

`BaseScraper.run()` checks `SOURCE_CONTRACT_VALIDATED` before disk checks, source access, scoring, persistence, broadcast, or alerts. A county module with `SOURCE_CONTRACT_VALIDATED = False` returns an empty, explicitly fail-closed result before calling `scrape()`. Re-enablement requires a county-specific source validation that records the official listing URL, complete public name field, source-issued immutable booking/inmate key, booking or arrest date/time, permitted bounded pagination, and no access-control workaround. The change must then include parser tests, matrix and `SCRAPER_SOURCE_STATES` updates, and first-run persistence/alert telemetry.
