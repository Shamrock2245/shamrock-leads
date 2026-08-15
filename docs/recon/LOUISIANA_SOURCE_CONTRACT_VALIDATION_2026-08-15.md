# Louisiana Source-Contract Validation — 2026-08-15

> **Scope:** Bounded public source-contract review only. No person-level booking records, images, profile pages, dates of birth, addresses, or contact information were retained. This review did not run a scraper, write MongoDB records, send alerts, or change runtime source states.

## Validated public listing contracts

| Parish | Official listing | Listing-level required fields observed | Access posture | Decision |
|---|---|---|---|---|
| Beauregard | [Sheriff inmate roster](https://www.beauregardparishsheriff.org/roster.php) | Complete displayed name, labelled **Booking #**, and labelled **Booking Date** were present on ordinary public listing cards. | Public HTML listing; details and images were deliberately not used. | **Candidate productive** — a parser may be built only with listing cards, bounded pagination, source-issued booking numbers, and no profile/image collection. |
| Calcasieu | [Sheriff inmate roster](https://www.cpso.com/inmateRoster) | Listing output exposed a labelled **Inmate ID** and **Booked Date**. | Ordinary public listing output; no person data retained. | **Candidate productive** — implementation requires a county-specific parser test proving complete displayed names, source key, booking time, and safe pagination before registration. |
| St. Mary | [Sheriff inmate roster](https://www.stmaryso.com/inmate-roster/filters/current/booking_time=desc/1) | Complete displayed name, labelled **Booking #**, and labelled **Booking Date** were present on public cards. | Public HTML listing with a visible current-roster pagination path; profile links and images were not used. | **Already registered; candidate verified-public source contract**. Preserve listing-only behavior and require first-run persistence/alert telemetry before any production success claim. |

## Guardrails

A county is not production-proven merely because its source contract is public. Any implementation must require a complete displayed name, a source-issued immutable booking/inmate identifier, and a booking or arrest date/time from the broad listing. It must fail closed when a required field is missing, avoid profile pages and source-control workarounds, preserve `State + County + Booking_Number` uniqueness, and update the state registry, `SCRAPER_SOURCE_STATES` where applicable, regression tests, and the reconnaissance matrix.

## References

1. [Beauregard Parish Sheriff — Inmate Roster](https://www.beauregardparishsheriff.org/roster.php)
2. [Calcasieu Parish Sheriff — Inmate Roster](https://www.cpso.com/inmateRoster)
3. [St. Mary Parish Sheriff — Inmate Roster](https://www.stmaryso.com/inmate-roster/filters/current/booking_time=desc/1)
