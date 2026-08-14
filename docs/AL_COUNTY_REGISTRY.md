# Alabama County Scraper Registry

> **Last updated:** 2026-08-14
> **Registered scheduler jobs:** 16
> **Package:** `scrapers/counties_al/`

`main.py` is the source of truth for scheduler registration. A county listed below is registered in code; that status does **not** prove a successful production write, downstream alert, payment, or bond action. All state-scoped county labels are kept distinct, including `Lee (AL)` versus Lee jobs in Georgia, North Carolina, and South Carolina.

| County | Module | Cadence | Evidence boundary |
|---|---|---:|---|
| Baldwin | `baldwin.py` | 120 min | Registered; shared Citizen Connect parser is **fail closed** for the current unsupported Baldwin route and cannot generate synthetic booking identities. See `docs/SOUTHERN_SW_SOURCE_SAFETY.md`. |
| Cullman | `cullman.py` | 120 min | Registered; county-specific Citizen Connect route redirects to the general agency directory, so the shared parser is **fail closed** pending a supported booking-safe public roster. See `docs/SOUTHERN_SW_SOURCE_SAFETY.md`. |
| DeKalb | `dekalb.py` | 120 min | Registered; source and production telemetry require validation. |
| Etowah | `etowah.py` | 120 min | Repaired to the official public current-roster page; bounded two-page local smoke parsed 20 unique records with source-issued booking numbers and booking dates. Deployed 2026-08-14 with public hosts healthy; county-specific persistence and alert telemetry remain unproven. |
| Houston | `houston.py` | 120 min | Registered; source and production telemetry require validation. |
| Jackson | `jackson.py` | 120 min | Registered; source and production telemetry require validation. |
| Jefferson | `jefferson.py` | 120 min | Registered; source and production telemetry require validation. |
| Lee | `lee.py` | 120 min | **Deployed 2026-08-14** after a local two-page official-source smoke; public production hosts are healthy. Mongo upsert and alert delivery remain unproven. |
| Madison | `madison.py` | 120 min | Registered; legacy proxy discovery and synthetic booking IDs removed. The path is **fail closed** until the official inmate-information page exposes a supported booking-safe broad roster. Deployed 2026-08-14 with public hosts healthy; no Madison writes or alerts are expected from the guard. |
| Marshall | `marshall.py` | 120 min | **Deployed 2026-08-14** after a local two-page official-source smoke; public production hosts are healthy. Mongo upsert and alert delivery remain unproven. |
| Mobile | `mobile.py` | 120 min | Registered; legacy residential-proxy access, DOB retention, and synthetic booking IDs removed. The path is **fail closed** until the official current-inmates portal exposes a supported booking-safe broad roster. Deployed 2026-08-14 with public hosts healthy; no Mobile writes or alerts are expected from the guard. |
| Montgomery | `montgomery.py` | 120 min | Registered; source and production telemetry require validation. |
| Morgan | `morgan.py` | 120 min | Registered; county-specific Citizen Connect route redirects to the general agency directory, so the shared parser is **fail closed** pending a supported booking-safe public roster. See `docs/SOUTHERN_SW_SOURCE_SAFETY.md`. |
| Shelby | `shelby.py` | 120 min | Registered; source and production telemetry require validation. |
| St. Clair | `st_clair.py` | 120 min | **Deployed 2026-08-14** after a bounded local two-page official-roster smoke; public production hosts are healthy. Mongo upsert and alert delivery remain unproven. |
| Tuscaloosa | `tuscaloosa.py` | 120 min | Registered; source and production telemetry require validation. |

## Lee County implementation notes

Lee County Sheriff's Office publishes a public current-roster page at `https://www.leecosheriffal.gov/inmateSearch`. Its official public page exposes numbered pagination, a `NameID`, and a booking timestamp, without a source field labelled as a booking number. The scraper therefore creates a clearly labelled deterministic per-booking key from **both** public values, and fails closed if either is missing. It parses the official sheriff-domain Next.js response payload directly because card details are server-provided there; it neither calls a denied generic OCV feed nor bypasses a login, CAPTCHA, WAF, or other control.

The bounded local smoke over the first two official pages parsed 20 records with Alabama/Lee state invariants, non-empty dedup keys, and source-key provenance. The `6109410` code rollout completed successfully on 2026-08-14, followed by public `leads`, `sign`, `school`, `paperwork`, and `social /auth` health checks. This is not evidence of a Lee-specific production database write or notification.

## Marshall County implementation notes

Marshall County Sheriff's Office publishes a broad current-inmate roster at `https://www.marshallso.org/inmate-roster/filters/current/booking_time=desc/1`. The public roster provides a source-issued `Booking #` and a booking timestamp on each card, so the scraper maps that source identifier directly and does not retrieve individual profile pages. It uses explicit Next-page detection, a bounded page cap, and stops if it encounters an empty or duplicate-only page.

The bounded local smoke over the first two official pages parsed 40 records with Alabama/Marshall state invariants and source-issued booking numbers. The `9ed467e` code rollout completed successfully on 2026-08-14, followed by public `leads`, `sign`, `school`, `paperwork`, and `social /auth` health checks. This is not evidence of a Marshall-specific production database write or notification.

## Recon queue

| County | Official public surface | Current decision |
|---|---|---|
| Limestone | Public Zuercher roster | Recon only: public roster lacks a verified safe per-booking identity boundary. |
| Calhoun | Public sheriff roster | Recon only: public table lacks a source-issued booking or inmate identifier. |
| Autauga | Sheriff app announcement | Recon only: no accessible public web roster contract. |
| Talladega | Southern Software Citizen Connect | Recon only: identifier and booking-time contract need validation before implementation. |
