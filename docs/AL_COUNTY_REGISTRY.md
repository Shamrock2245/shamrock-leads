# Alabama County Scraper Registry

> **Last updated:** 2026-08-14
> **Registered scheduler jobs:** 16
> **Package:** `scrapers/counties_al/`

`main.py` is the source of truth for scheduler registration. A county listed below is registered in code; that status does **not** prove a successful production write, downstream alert, payment, or bond action. All state-scoped county labels are kept distinct, including `Lee (AL)` versus Lee jobs in Georgia, North Carolina, and South Carolina.

| County | Module | Cadence | Evidence boundary |
|---|---|---:|---|
| Baldwin | `baldwin.py` | 120 min | **Fail-closed safeguard deployed 2026-08-15.** Configured Citizen Connect agency `BaldwinCoAL` resolves to the generic agency directory rather than a Baldwin booking roster. The county guard emits no records and does not invoke the shared parser until an official broad roster supplies a source-issued booking/inmate ID and booking time. Deployment run `31858750433` completed successfully; Baldwin-specific persistence and alert telemetry remain unproven. |
| Cullman | `cullman.py` | 120 min | **Fail-closed safeguard deployed 2026-08-15.** Configured Citizen Connect agency `CullmanCoAL` resolves to the generic agency directory rather than a Cullman booking roster. The county guard emits no records and does not invoke the shared parser until an official broad roster supplies a source-issued booking/inmate ID and booking time. Deployment run `31859015946` completed successfully; Cullman-specific persistence and alert telemetry remain unproven. |
| DeKalb | `dekalb.py` | 120 min | **Fail-closed safeguard deployed 2026-08-15.** Configured Citizen Connect agency `DeKalbCoAL` resolves to the generic agency directory rather than a DeKalb booking roster. The county guard emits no records and does not invoke the shared parser until an official broad roster supplies a source-issued booking/inmate ID and booking time. Deployment run `31857452207` completed successfully; DeKalb-specific persistence and alert telemetry remain unproven. |
| Etowah | `etowah.py` | 120 min | Repaired to the official public current-roster page; bounded two-page local smoke parsed 20 unique records with source-issued booking numbers and booking dates. Deployed 2026-08-14 with public hosts healthy; county-specific persistence and alert telemetry remain unproven. |
| Houston | `houston.py` | 120 min | **Fail-closed safeguard deployed 2026-08-15.** Direct normal access to configured Citizen Connect agency `HoustonCoAL` returned HTTP 403 and no listing cards or source-issued booking identity contract. The county guard emits no records and does not invoke the shared parser until an official broad roster supplies a source-issued booking/inmate ID and booking time. Deployment run `31857743667` completed successfully; Houston-specific persistence and alert telemetry remain unproven. |
| Jackson | `jackson.py` | 120 min | **Fail-closed safeguard deployed 2026-08-15.** Configured Citizen Connect agency `JacksonCoAL` resolves to the generic agency directory rather than a Jackson booking roster. The county guard emits no records and does not invoke the shared parser until an official broad roster supplies a source-issued booking/inmate ID and booking time. Deployment run `31857994743` completed successfully; Jackson-specific persistence and alert telemetry remain unproven. |
| Jefferson | `jefferson.py` | 120 min | **Fail-closed safeguard deployed 2026-08-15.** Direct normal access to the configured official New World portal returned HTTP 403. The prior path declared a residential-proxy stealth session; the county guard now emits no records until an official broad roster supplies a source-issued booking/inmate ID and booking time without bypassing controls. Deployment run `31858284205` completed successfully; Jefferson-specific persistence and alert telemetry remain unproven. |
| Lee | `lee.py` | 120 min | **Deployed 2026-08-14** after a local two-page official-source smoke; public production hosts are healthy. Mongo upsert and alert delivery remain unproven. |
| Madison | `madison.py` | 120 min | Registered; legacy proxy discovery and synthetic booking IDs removed. The path is **fail closed** until the official inmate-information page exposes a supported booking-safe broad roster. Deployed 2026-08-14 with public hosts healthy; no Madison writes or alerts are expected from the guard. |
| Marshall | `marshall.py` | 120 min | **Deployed 2026-08-14** after a local two-page official-source smoke; public production hosts are healthy. Mongo upsert and alert delivery remain unproven. |
| Mobile | `mobile.py` | 120 min | Registered; legacy residential-proxy access, DOB retention, and synthetic booking IDs removed. The path is **fail closed** until the official current-inmates portal exposes a supported booking-safe broad roster. Deployed 2026-08-14 with public hosts healthy; no Mobile writes or alerts are expected from the guard. |
| Montgomery | `montgomery.py` | 120 min | Registered; official public inmates API returned HTTP 403 through normal access. The path is **fail closed** pending a supported booking-safe broad roster. Deployed 2026-08-14 with public hosts healthy; no Montgomery writes or alerts are expected from the guard. |
| Morgan | `morgan.py` | 120 min | Registered; county-specific Citizen Connect route redirects to the general agency directory, so the shared parser is **fail closed** pending a supported booking-safe public roster. See `docs/SOUTHERN_SW_SOURCE_SAFETY.md`. |
| Shelby | `shelby.py` | 120 min | **Inherited JailTracker fail-closed safeguard deployed.** Revalidated 2026-08-15: official landing page requires image-character human verification. The shared guard makes no CAPTCHA attempt, proxy request, profile request, sensitive-field collection, or synthetic key; it emits no records until a separately verified listing-only contract exists. |
| St. Clair | `st_clair.py` | 120 min | **Deployed 2026-08-14** after a bounded local two-page official-roster smoke; public production hosts are healthy. Mongo upsert and alert delivery remain unproven. |
| Tuscaloosa | `tuscaloosa.py` | 120 min | Registered; legacy path targeted Tulsa County, Oklahoma and used an unsafe fallback identifier with invalid schema mapping. Tuscaloosa’s official ‘Who’s in Jail’ surface requires human verification, so this path is **fail closed** pending a normal-access broad roster with complete identity, a source-issued booking identifier, and booking date/time. Deployment run `31851444734` succeeded; deterministic tests plus public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy. The guard emits no records; county-specific persistence and alert telemetry remain unproven. |

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
