# Louisiana Source-Contract Validation — 2026-08-15

> **Scope:** Bounded public source-contract review only. No person-level booking records, images, profile pages, dates of birth, addresses, or contact information were retained. This review did not run a scraper, write MongoDB records, send alerts, or change runtime source states.

## Validated public listing contracts

| Parish | Official listing | Listing-level required fields observed | Access posture | Decision |
|---|---|---|---|---|
| Beauregard | [Sheriff inmate roster](https://www.beauregardparishsheriff.org/roster.php) | Complete displayed name, labelled **Booking #**, and labelled **Booking Date** were present on ordinary public listing cards. | Public HTML listing; details and images were deliberately not used. | **Candidate productive** — a parser may be built only with listing cards, bounded pagination, source-issued booking numbers, and no profile/image collection. |
| Calcasieu | [Sheriff inmate roster](https://www.cpso.com/inmateRoster) | Listing output exposed a labelled **Inmate ID** and **Booked Date** during the passive review. | Current public page returned `200`, but the previously configured `/api/inmates/roster` endpoint returned `404` through ordinary public access; no person data was retained. | **Fail closed** — the registered scraper emits no records until the current public API, complete listing-name field, source key, booking time, and bounded pagination are revalidated. |
| St. Mary | [Sheriff inmate roster](https://www.stmaryso.com/inmate-roster/filters/current/booking_time=desc/1) | Complete displayed name, labelled **Booking #**, and labelled **Booking Date** were present on public cards. | Public HTML listing with a visible current-roster pagination path; profile links and images were not used. | **Already registered; candidate verified-public source contract**. Preserve listing-only behavior and require first-run persistence/alert telemetry before any production success claim. |
| Orleans | [OPSO public origin](https://www.opso.gov) | The public origin returned `200`, but a booking-safe roster, source-issued booking identifier, booking time, and pagination contract were not established from ordinary public access. | No source rows, browser flow, or speculative endpoint request was retained or promoted. | **Fail closed** — the prior speculative endpoint/browser flow and synthetic booking fallbacks were removed; re-enable only after a source-faithful listing contract is validated. |
| St. Tammany | [Sheriff inmate search](https://www.stpso.com/inmate-search) | The previous `/api/inmates/recent` endpoint did not expose a contract through ordinary access. | The configured endpoint returned public HTTP `403`; no person data was retained. | **Fail closed** — the registered scraper emits no records until a booking-safe broad roster is revalidated. |
| East Baton Rouge | [EBRSO prison inmate list](https://www.ebrso.org/resources/prison-inmate-list/) | Ordinary public access did not establish a booking-safe listing contract. The prior registered path used residential stealth, a Cloudflare/disclaimer browser walk, and name-derived `EBR_` booking keys. | No person-level rows were retained. Stealth, browser, and synthetic-identifier work is not permitted. | **Fail closed 2026-08-16** — the registered scraper emits no records until an official listing supplies a complete name, source-issued booking identifier, and booking date/time through ordinary access. |
| Jefferson | [JPSO InmateSearch](https://apps.jpso.com/inmatesearch/) | Ordinary public access did not establish a booking-safe listing contract. The prior registered path used stealth TLS fingerprinting, a browser fallback, and name-derived `JEF_` booking keys. | No person-level rows were retained. Stealth, TLS-bypass, browser, and synthetic-identifier work is not permitted. | **Fail closed 2026-08-16** — the registered scraper emits no records until an official listing supplies a complete name, source-issued booking identifier, and booking date/time through ordinary access. |

## Guardrails

A county is not production-proven merely because its source contract is public. Any implementation or re-enablement must require a complete displayed name, a source-issued immutable booking/inmate identifier, and a booking or arrest date/time from the broad listing. It must fail closed when a required field is missing, avoid profile pages and source-control workarounds, preserve `State + County + Booking_Number` uniqueness, and update the state registry, `SCRAPER_SOURCE_STATES` where applicable, regression tests, and the reconnaissance matrix.

## References

1. [Beauregard Parish Sheriff — Inmate Roster](https://www.beauregardparishsheriff.org/roster.php)
2. [Calcasieu Parish Sheriff — Inmate Roster](https://www.cpso.com/inmateRoster)
3. [St. Mary Parish Sheriff — Inmate Roster](https://www.stmaryso.com/inmate-roster/filters/current/booking_time=desc/1)
4. [East Baton Rouge Parish Sheriff — Prison Inmate List](https://www.ebrso.org/resources/prison-inmate-list/)
5. [Jefferson Parish Sheriff — InmateSearch](https://apps.jpso.com/inmatesearch/)
