# Louisiana Parish Scraper Registry

> **Last updated:** 2026-08-14
> **Registered scheduler jobs:** 13
> **Package:** `scrapers/counties_la/`
> **Job IDs:** `scraper_la_<parish>` · CLI example: `python main.py la_tangipahoa`

Louisiana uses **parishes** rather than counties; the parish name is stored in the canonical `County` field. `main.py` is the source of truth for registration. A registered job is not proof of a production database write, alert, payment, or bond action.

| Parish | Module | Cadence | Evidence boundary |
|---|---|---:|---|
| Ascension | `ascension.py` | 120 min | Registered; source and production telemetry require validation. |
| Bossier | `bossier.py` | 120 min | **Deployed 2026-08-15.** Official paginated listing cards expose complete source names, source-issued Inmate IDs, and booked date/times. A bounded two-page aggregate-only smoke parsed 20 unique records with state, parish, source-key, booking date/time, deduplication, and listing-only invariants passing. Deployment run `31852987527` succeeded; public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy. No profile or image retrieval; parish-specific persistence and alert telemetry remain unproven. |
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
| Rapides | Sheriff-linked NewWorld Inmate Inquiry | **Revalidated 2026-08-15 — recon only; no registration.** Normal public broad results expose complete names, a Subject Number, custody status, and facility, but do not expose a source-issued booking key or booking date/time. The public form’s Booking Number and date fields are search controls, not broad-listing data. Do not retrieve profiles or infer identifiers; retain no scraper until the listing itself supplies the required booking-safe fields. |
| St. Landry | Sheriff-branded LAVNS roster | **Revalidated 2026-08-15 — recon only; no registration.** The visible public Show All listing exposes complete name, DOB, race, gender, and an empty arrest-date column. It does not expose a source-issued booking/inmate identifier or usable booking/arrest date/time on broad rows. Do not collect DOB or infer keys; retain no scraper unless the listing itself supplies the required booking-safe fields. |
| Terrebonne | Sheriff-published CentralSquare public portal | **Revalidated 2026-08-15 — recon only; no registration.** Normal public Inmates results expose mugshot, complete name, race, sex, arrest date, held-for agency, age, and charge/bond text, but no labelled source-issued booking or inmate identifier. The public arrest date cannot be used to manufacture an identity key. Do not collect profile data or infer keys; retain no scraper until broad results supply the required booking-safe fields. |
| Grant, Union | LA VINE roster directory | Recon only: official directory marks these roster endpoints offline. |
