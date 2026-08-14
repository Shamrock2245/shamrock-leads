# Alabama County Scraper Registry

> **Last updated:** 2026-08-14
> **Registered scheduler jobs:** 14
> **Package:** `scrapers/counties_al/`

`main.py` is the source of truth for scheduler registration. A county listed below is registered in code; that status does **not** prove a successful production write, downstream alert, payment, or bond action. All state-scoped county labels are kept distinct, including `Lee (AL)` versus Lee jobs in Georgia, North Carolina, and South Carolina.

| County | Module | Cadence | Evidence boundary |
|---|---|---:|---|
| Baldwin | `baldwin.py` | 120 min | Registered; source and production telemetry require validation. |
| Cullman | `cullman.py` | 120 min | Registered; source and production telemetry require validation. |
| DeKalb | `dekalb.py` | 120 min | Registered; source and production telemetry require validation. |
| Etowah | `etowah.py` | 120 min | Registered; source and production telemetry require validation. |
| Houston | `houston.py` | 120 min | Registered; source and production telemetry require validation. |
| Jackson | `jackson.py` | 120 min | Registered; source and production telemetry require validation. |
| Jefferson | `jefferson.py` | 120 min | Registered; source and production telemetry require validation. |
| Lee | `lee.py` | 120 min | **Deployed 2026-08-14** after a local two-page official-source smoke; public production hosts are healthy. Mongo upsert and alert delivery remain unproven. |
| Madison | `madison.py` | 120 min | Registered; source and production telemetry require validation. |
| Mobile | `mobile.py` | 120 min | Registered; source and production telemetry require validation. |
| Montgomery | `montgomery.py` | 120 min | Registered; source and production telemetry require validation. |
| Morgan | `morgan.py` | 120 min | Registered; source and production telemetry require validation. |
| Shelby | `shelby.py` | 120 min | Registered; source and production telemetry require validation. |
| Tuscaloosa | `tuscaloosa.py` | 120 min | Registered; source and production telemetry require validation. |

## Lee County implementation notes

Lee County Sheriff's Office publishes a public current-roster page at `https://www.leecosheriffal.gov/inmateSearch`. Its official public page exposes numbered pagination, a `NameID`, and a booking timestamp, without a source field labelled as a booking number. The scraper therefore creates a clearly labelled deterministic per-booking key from **both** public values, and fails closed if either is missing. It parses the official sheriff-domain Next.js response payload directly because card details are server-provided there; it neither calls a denied generic OCV feed nor bypasses a login, CAPTCHA, WAF, or other control.

The bounded local smoke over the first two official pages parsed 20 records with Alabama/Lee state invariants, non-empty dedup keys, and source-key provenance. The `6109410` code rollout completed successfully on 2026-08-14, followed by public `leads`, `sign`, `school`, `paperwork`, and `social /auth` health checks. This is not evidence of a Lee-specific production database write or notification.

## Recon queue

| County | Official public surface | Current decision |
|---|---|---|
| Limestone | Public Zuercher roster | Recon only: public roster lacks a verified safe per-booking identity boundary. |
| Calhoun | Public sheriff roster | Recon only: public table lacks a source-issued booking or inmate identifier. |
| St. Clair | Public sheriff roster | Recon only: direct retrieval encountered a Cloudflare challenge; do not bypass controls. |
| Autauga | Sheriff app announcement | Recon only: no accessible public web roster contract. |
| Talladega | Southern Software Citizen Connect | Recon only: identifier and booking-time contract need validation before implementation. |
