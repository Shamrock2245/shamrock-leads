# Tennessee Source-Contract Validation — 2026-08-15

> **Scope:** Metadata-only checks of configured public paths. No roster values, images, profile pages, date-of-birth data, addresses, contact data, blank searches, CAPTCHA workarounds, proxy use, or browser workflows were retained or used. These checks did not write arrest records, send alerts, or change any surety, payment, signing, or bond state.

## Validation results

| County | Configured path outcome | Required broad-listing contract | Runtime decision |
|---|---|---|---|
| Davidson | The configured recent-bookings and search paths did not establish a usable ordinary-access contract; the prior implementation also used TLS-bypass and speculative search flow. | Not proven. | **Fail closed** — no source fetch or synthetic fallback is permitted. |
| Hamilton | The configured list endpoint responded, but the inspection did not prove complete displayed name, source-issued booking identifier, booking time, and bounded pagination together. | Not proven. | **Fail closed** — no roster or detail request is permitted. |
| Knox | Public pages responded but did not establish all required listing fields and bounded pagination; the prior path used TLS-bypass letter walking. | Not proven. | **Fail closed** — no source fetch is permitted. |
| Montgomery | Configured public surfaces responded, but the complete booking-safe listing contract was not proven and the prior parser had an unsafe identifier path. | Not proven. | **Fail closed** — no source fetch is permitted. |
| Rutherford | No configured public roster URL is documented for the inherited JailTracker path. | Not proven. | **Fail closed** — inherited CAPTCHA and roster flow is blocked. |
| Shelby | The custody portals timed out and the official information page did not establish a complete listing contract. | Not proven. | **Fail closed** — multi-portal fallback is blocked. |
| Sumner | The configured OCV URL responded with JSON, but the inspection did not establish the complete required listing contract and bounded pagination. | Not proven. | **Fail closed** — OCV and HTML fallback paths are blocked. |
| Williamson | No configured public roster URL is documented for the inherited JailTracker path. | Not proven. | **Fail closed** — inherited source retrieval is blocked. |
| Wilson | The configured JailTracker portal timed out through ordinary access. | Not proven. | **Fail closed** — inherited source retrieval is blocked. |

## Enforcement

`BaseScraper.run()` now checks `SOURCE_CONTRACT_VALIDATED` before disk checks, source access, scoring, persistence, broadcast, or alerts. A county module with `SOURCE_CONTRACT_VALIDATED = False` returns an empty, explicitly fail-closed result before calling `scrape()`. Any re-enablement requires a county-specific source validation that records the official listing URL, complete public name field, source-issued immutable booking/inmate key, booking or arrest date/time, permitted bounded pagination, and no access-control workaround. The change must then include parser tests, matrix and `SCRAPER_SOURCE_STATES` updates, and first-run persistence/alert telemetry.
