# Southern Software Citizen Connect Source-Safety Registry

**Last reconciled:** 2026-08-14 EDT

The shared `SouthernSWBaseScraper` serves **51 registered county wrappers** in Alabama, Georgia, North Carolina, South Carolina, and Tennessee. A public Citizen Connect roster may be ingested only when the public card provides complete identity, a **source-issued booking or inmate identifier**, and a `Booked` date or timestamp. The parser no longer fabricates an arrest identity from name fragments, age, and a date. It also records custody as unknown unless an official source explicitly supplies it.

| Contract condition | Parser behavior |
|---|---|
| Complete identity, source-issued booking/inmate ID, and `Booked` value are present | Emit an `ArrestRecord` with the source ID and an explicit provenance marker. |
| Any required identity boundary is missing | Fail closed for that card; emit no record. |
| Source route returns an unsupported warning, search-only surface, challenge, or no cards | Return no records for that run; do not use blank-search, synthetic-ID, or access-control workarounds. |

## Baldwin County, Alabama

Baldwin’s existing `BaldwinCoAL` Citizen Connect wrapper was validated against the county-linked official portal. The current normal index route returns a public response, but the current-confinements request contains only an unsupported warning container and no parseable booking-card contract. A browser visit to the former agency route redirects to the general Citizen Connect directory rather than a Baldwin roster. The hardened scraper returned zero records in a non-writing aggregate smoke; it did not generate a synthetic booking identity.

Baldwin therefore remains **registered but non-productive** until the county’s official public source exposes a directly accessible, supported broad roster with complete identity, a source-issued booking/inmate ID, and booking date/time. The shared `4d58f29` safety rollout completed successfully on 2026-08-14 and the required public hosts returned 200. This decision does not prove or imply production persistence or alert delivery for Baldwin or any Southern Software-dependent county.

## Cullman County, Alabama

Cullman’s `CullmanCoAL` Citizen Connect route was validated in a normal browser. It redirects from the county-specific booking-search URL to the general Citizen Connect agency directory, without presenting a Cullman current-confinements roster or a booking-safe broad-list contract. Cullman therefore remains **registered but non-productive** under the shared fail-closed parser. No blank search, parameter guess, profile lookup, or access-control workaround was attempted.

## Morgan County, Alabama

Morgan’s `MorganCoAL` Citizen Connect route was also validated in a normal browser. It redirects from the county-specific booking-search URL to the same general Citizen Connect agency directory, without a Morgan current-confinements roster or a booking-safe broad-list contract. Morgan therefore remains **registered but non-productive** under the shared fail-closed parser. No blank search, parameter guess, profile lookup, or access-control workaround was attempted.

## Revalidation criteria

A dependent wrapper may produce records only after its own official public card contract is observed to contain all required source fields. A source change must be accompanied by deterministic parser tests, a bounded aggregate-only source smoke, normal deployment verification, and separately observed persistence and alert telemetry before the county is marked productive.
