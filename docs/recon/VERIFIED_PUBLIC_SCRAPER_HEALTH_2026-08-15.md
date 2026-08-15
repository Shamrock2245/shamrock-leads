# Verified-Public Scraper Health Check — 2026-08-15

> **Scope:** An isolated aggregate-only check of the ten paths currently marked `verified_public` in `dashboard/extensions.py`. Each check called `scrape()` directly in a disposable subprocess with no `run()` invocation, writers, scoring, alerts, broadcasts, or persistence. Only result counts, elapsed time, and whether an in-memory record was missing a booking key were retained. No person-level records, names, dates of birth, contact data, images, or booking values were printed or stored.

## Result

| Scraper | Aggregate check | Source availability follow-up | Classification | Implementation action |
|---|---:|---|---|---|
| Putnam (TN) | Timed out at 75 seconds and again at 180 seconds. | Configured source returned ordinary HTTP `200`. | **Source reachable; scraper timing inconclusive.** | No change. Monitor a production interval before any action. |
| Randall (TX) | Returned `0` records without an exception; no missing key condition. | Configured source returned ordinary HTTP `200` with booking, inmate, name, arrest, date, case, and bond field tokens. | **Source reachable; empty result may be legitimate.** | No change. Observe a staffed production interval if empty results persist. |
| Bossier (LA) | Returned **1,106** aggregate records after 148.61 seconds; no missing booking key condition. | Configured source returned ordinary HTTP `200`. | **Working in bounded check.** | No change. |
| Tangipahoa (LA) | Returned **726** aggregate records after 93.20 seconds; no missing booking key condition. | Configured source returned ordinary HTTP `200`. | **Working in bounded check.** | No change. |
| St. Mary (LA) | Returned **288** aggregate records after 53.60 seconds; no missing booking key condition. | Not separately probed because the bounded scraper result was conclusive. | **Working in bounded check.** | No change. |
| Lee (AL) | Returned **80** aggregate records after 20.92 seconds; no missing booking key condition. | Not separately probed because the bounded scraper result was conclusive. | **Working in bounded check.** | No change. |
| Marshall (AL) | Returned **362** aggregate records after 63.98 seconds; no missing booking key condition. | Not separately probed because the bounded scraper result was conclusive. | **Working in bounded check.** | No change. |
| Etowah (AL) | Returned **338** aggregate records after 23.73 seconds; no missing booking key condition. | Not separately probed because the bounded scraper result was conclusive. | **Working in bounded check.** | No change. |
| St. Clair (AL) | Returned `0` records without an exception; no missing key condition. | Ordinary direct metadata request received HTTP `403`. | **Inconclusive; may require the existing scraper’s normal request profile.** | No change. Do not bypass the control or alter the scraper without a staff-approved production observation. |
| Rankin (MS) | Returned **394** aggregate records after 7.71 seconds; no missing booking key condition. | Not separately probed because the bounded scraper result was conclusive. | **Working in bounded check.** | No change. |

## Guardrails

The pre-verification working tree was restored before this check. No scraper class, shared scraper base, dashboard source-state decision, scheduler registration, source endpoint, timeout setting, or deployment configuration was changed as part of this verification. The only unresolved items are **observability findings** for Putnam, Randall, and St. Clair; they are not authorization to disable, patch, or reclassify those paths.

> A runtime result count is an operational signal, not a claim about a particular individual, bond, payment, court matter, or signature state. Any repair or source-state change remains subject to county-specific evidence and the platform’s existing human and surety gates.
