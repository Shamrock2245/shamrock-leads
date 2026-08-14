# Texas County Scraper Registry

> **Last Updated:** 2026-08-14
> **Registered scheduler jobs:** 34
> **Package:** `scrapers/counties_tx/`
> **Job IDs:** `scraper_tx_<county>` · CLI example: `python main.py tx_randall`

`main.py` is the source of truth for scheduler registration. A county appearing in this registry is **registered in code**, not automatically proven to have made a successful production write or alert delivery. Source-level validation and dashboard telemetry must remain separate from registration status.

## Source posture

The Texas fleet contains county-specific sources, browser-rendered public pages, P2C wrappers, and other vendor families. Each scraper must preserve the immutable `County + Booking_Number` dedup boundary, fail closed when the source does not provide a safe identifier, and must never bypass authentication, CAPTCHAs, geofencing, WAFs, or other access controls.

| County | Module | Cadence | Source posture | Evidence boundary |
|---|---|---:|---|---|
| Harris | `harris.py` | 90 min | Browser-rendered HCSO public jail search | Registered; production telemetry required |
| Dallas | `dallas.py` | 90 min | County public jail lookup | Registered; production telemetry required |
| Bexar | `bexar.py` | 60 min | Central Magistrate public list | Registered; production telemetry required |
| Tarrant | `tarrant.py` | 60 min | Public inmate / docket endpoints | Registered; production telemetry required |
| Travis | `travis.py` | 60 min | SIPS public API | Registered; production telemetry required |
| Collin | `collin.py` | 90 min | Public search; WAF-sensitive | Registered; do not bypass controls |
| Denton | `denton.py` | 60 min | Athena public jail view | Registered; production telemetry required |
| Fort Bend | `fort_bend.py` | 90 min | Public jail query | Registered; production telemetry required |
| Montgomery | `montgomery.py` | 90 min | Sheriff public inmate inquiry | Registered; production telemetry required |
| Williamson | `williamson.py` | 90 min | Public jail view | Registered; production telemetry required |
| El Paso | `el_paso.py` | 90 min | Sheriff public source | Registered; production telemetry required |
| Hidalgo | `hidalgo.py` | 90 min | Sheriff public source | Registered; production telemetry required |
| Cameron | `cameron.py` | 90 min | Sheriff public roster | Registered; production telemetry required |
| Brazoria | `brazoria.py` | 120 min | Tyler Odyssey JailAccess | Registered; production telemetry required |
| Galveston | `galveston.py` | 90 min | P2C public grid | Registered; production telemetry required |
| Bell | `bell.py` | 90 min | Legacy P2C hostname | DNS failed during 2026-08-14 reconnaissance; source refresh required |
| Lubbock | `lubbock.py` | 90 min | County-specific public source | Registered; production telemetry required |
| Webb | `webb.py` | 90 min | County-specific public source | Registered; production telemetry required |
| Jefferson | `jefferson.py` | 90 min | Legacy P2C hostname / official current-inmates PDF observed | P2C DNS failed during 2026-08-14 reconnaissance; do not replace fail-closed keying until a durable source key is confirmed |
| McLennan | `mclennan.py` | 90 min | County-specific public source | Registered; production telemetry required |
| Nueces | `nueces.py` | 90 min | County-specific public source | Registered; production telemetry required |
| Brazos | `brazos.py` | 90 min | County-specific public source | Registered; production telemetry required |
| Hays | `hays.py` | 90 min | County-specific public source | Registered; production telemetry required |
| Ellis | `ellis.py` | 90 min | Legacy P2C hostname | DNS failed during 2026-08-14 reconnaissance; source refresh required |
| Johnson | `johnson.py` | 90 min | Legacy P2C hostname | TLS connection failed during 2026-08-14 reconnaissance; source refresh required |
| Ector | `ector.py` | 90 min | County-specific public source | Registered; production telemetry required |
| Midland | `midland.py` | 90 min | County-specific public source | Registered; production telemetry required |
| Potter | `potter.py` | 90 min | County-specific public source | Registered; production telemetry required |
| Bastrop | `bastrop.py` | 120 min | County-specific public source | Registered; production telemetry required |
| Guadalupe | `guadalupe.py` | 120 min | Legacy P2C hostname | DNS failed during 2026-08-14 reconnaissance; source refresh required |
| Comal | `comal.py` | 120 min | County-specific public source | Registered; production telemetry required |
| Victoria | `victoria.py` | 120 min | County-specific public source | Registered; production telemetry required |
| Walker | `walker.py` | 120 min | County-specific public source | Registered; production telemetry required |
| Randall | `randall.py` | 120 min | Official public OCV/Next.js jail roster | **Local two-page browser-rendered source smoke passed 2026-08-14**; per-scraper Mongo upsert and alert delivery remain unproven |

## Randall implementation notes

Randall County’s official public roster is exposed at `https://www.randallso.gov/inmateSearch` with documented public pagination (`?page=<n>`). The direct OCV S3 feed returned HTTP 403 during reconnaissance, so `randall.py` deliberately renders and parses only the official sheriff-site page. It does not attempt to bypass the denied feed or any other source control.

The public card exposes a source-issued **Inmate ID** and a booking timestamp, but it does not label any field as a booking number. The scraper therefore emits a deterministic key formed from those two public source values only when both are present, clearly labels its origin in internal metadata, and fails closed if either is absent. This keeps unrelated bookings from being merged while never representing the surrogate as a county-issued booking number.

A bounded, aggregate-only local smoke over the first two official pages parsed 10 records with non-empty dedup keys, Texas/Randall invariants, source-key provenance, and in-custody status. It is source validation only; it is **not** evidence of a production write, alert, payment, or bond action.

## Recon queue

| County | Observed public surface | Current decision |
|---|---|---|
| Rockwall | Official Tyler Jail Public Access landing page | Recon only; inspect public request contract before implementation. |
| Kaufman | Official public jail application; list route geofenced in the validation environment | Recon only; do not circumvent geographic controls. |
| Smith | Sheriff jurisdiction confirmed; roster contract not located | Recon only. |
| Parker | Official Tyler search requires name-known search behavior | Recon only; no blanket enumeration without a verified public roster contract. |
| Grayson | Official judicial search exposes Jail Records navigation | Recon only; inspect source identifier and public pagination contract before implementation. |
