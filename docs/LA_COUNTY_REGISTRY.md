# Louisiana Parish Scraper Registry

> **Last updated:** 2026-08-14
> **Registered scheduler jobs:** 11
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
| Tangipahoa | `tangipahoa.py` | 120 min | **Local two-page official-source smoke passed 2026-08-14**; production Mongo upsert and alert delivery remain unproven. |

## Tangipahoa Parish implementation notes

Tangipahoa Parish Sheriff's Office publicly links a broad, paginated current roster at `https://tbs-web.com/jail/TangipahoaJail/roster`. The listing displays ten records per page and a source-issued numeric roster identifier alongside a booking timestamp. Because the source does not label that identifier as a booking number, the scraper generates a clearly labelled deterministic per-booking key from **both** values and fails closed if either value is absent. It reads only the public roster rows and does not retrieve individual detail pages.

The bounded local smoke over the first two official pages parsed 20 records with Tangipahoa/Louisiana state invariants, non-empty dedup keys, and source-key provenance. This is local source validation only, not evidence of a production database write or notification.

## Recon queue

| Parish | Official public surface | Current decision |
|---|---|---|
| Rapides | Sheriff-linked NewWorld Inmate Inquiry | Recon only: broad roster lacks a verified source-issued booking key and booking timestamp. |
| St. Landry | State roster directory / legacy LA VINE | Recon only: current public rendering and safe identity fields need confirmation. |
| Grant, Terrebonne, Union | LA VINE roster directory | Recon only: official directory marks these roster endpoints offline. |
