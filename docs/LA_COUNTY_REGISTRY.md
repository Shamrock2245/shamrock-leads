# Louisiana Parish Scraper Registry

> **Last updated:** 2026-08-14
> **Registered scheduler jobs:** 12
> **Package:** `scrapers/counties_la/`
> **Job IDs:** `scraper_la_<parish>` · CLI example: `python main.py la_tangipahoa`

Louisiana uses **parishes** rather than counties; the parish name is stored in the canonical `County` field. `main.py` is the source of truth for registration. A registered job is not proof of a production database write, alert, payment, or bond action.

| Parish | Module | Cadence | Evidence boundary |
|---|---|---:|---|
| Ascension | `ascension.py` | 120 min | Registered; source and production telemetry require validation. |
| Caddo | `caddo.py` | 90 min | Registered; source and production telemetry require validation. |
| Calcasieu | `calcasieu.py` | 90 min | Registered; source and production telemetry require validation. |
| East Baton Rouge | `east_baton_rouge.py` | 90 min | Registered; source and production telemetry require validation. |
| Jefferson | `jefferson.py` | 90 min | Registered; source and production telemetry require validation. |
| Lafayette | `lafayette.py` | 90 min | Registered; CAPTCHA-sensitive source; do not bypass controls. |
| Livingston | `livingston.py` | 120 min | Registered; source and production telemetry require validation. |
| Orleans | `orleans.py` | 90 min | Registered; source and production telemetry require validation. |
| Ouachita | `ouachita.py` | 90 min | Registered; source and production telemetry require validation. |
| St. Tammany | `st_tammany.py` | 90 min | Registered; source and production telemetry require validation. |
| St. Mary | `st_mary.py` | 120 min | **Deployed 2026-08-14** after a local two-page official-source smoke; public production hosts are healthy. Mongo upsert and alert delivery remain unproven. |
| Tangipahoa | `tangipahoa.py` | 120 min | **Deployed 2026-08-14** after a local two-page official-source smoke; public production hosts are healthy. Mongo upsert and alert delivery remain unproven. |

## Tangipahoa Parish implementation notes

Tangipahoa Parish Sheriff's Office publicly links a broad, paginated current roster at `https://tbs-web.com/jail/TangipahoaJail/roster`. The listing displays ten records per page and a source-issued numeric roster identifier alongside a booking timestamp. Because the source does not label that identifier as a booking number, the scraper generates a clearly labelled deterministic per-booking key from **both** values and fails closed if either value is absent. It reads only the public roster rows and does not retrieve individual detail pages.

The bounded local smoke over the first two official pages parsed 20 records with Tangipahoa/Louisiana state invariants, non-empty dedup keys, and source-key provenance. The `f456205` code rollout completed successfully on 2026-08-14, followed by public `leads`, `sign`, `school`, `paperwork`, and `social /auth` health checks. This is not evidence of a Tangipahoa-specific production database write or notification.

## St. Mary Parish implementation notes

St. Mary Parish Sheriff's Office publishes a broad, paginated current roster at `https://www.stmaryso.com/inmate-roster/filters/current/booking_time=desc/1`. Each public card presents a complete name, source-issued `Booking #`, booking timestamp, charges, and bond amount. The scraper maps the booking number directly, reads only public listing cards, and stops on an empty or duplicate-only page rather than retrieving individual profiles.

The bounded local smoke over the first two official pages parsed 40 records with St. Mary/Louisiana state invariants and source-issued booking numbers. The `4a6fe7f` rollout completed successfully on 2026-08-14, followed by public `leads`, `sign`, `school`, `paperwork`, and `social /auth` health checks. This is not evidence of a St. Mary-specific production database write or notification.

## Recon queue

| Parish | Official public surface | Current decision |
|---|---|---|
| Bossier | Official OCV roster | Recon only: broad cards lack a booking timestamp and alternate P2C routes are WAF-rejected. |
| Rapides | Sheriff-linked NewWorld Inmate Inquiry | Recon only: broad roster lacks a verified source-issued booking key and booking timestamp. |
| St. Landry | Sheriff-branded LAVNS roster | Recon only: TLS instability and dynamic loading prevented verification of a safe public booking boundary. |
| Terrebonne | CentralSquare public portal | Recon only: current portal did not expose a verifiable broad roster or booking fields. |
| Grant, Union | LA VINE roster directory | Recon only: official directory marks these roster endpoints offline. |
