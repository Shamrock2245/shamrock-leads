# Mississippi County Scraper Registry

> **Last updated:** 2026-08-14
> **Registered scheduler jobs:** 9
> **Package:** `scrapers/counties_ms/`

`main.py` is the source of truth for scheduler registration. A listed county is registered in code; this does **not** prove that its public source is currently healthy, that Mongo accepted a write, or that a downstream alert was delivered. County and state remain part of the immutable booking identity boundary.

## Registered Mississippi jobs

| County | Module | Interval | Evidence boundary |
|---|---|---:|---|
| DeSoto | `desoto.py` | 120 min | Registered; source and production telemetry require validation. |
| Forrest | `forrest.py` | 120 min | Registered; source and production telemetry require validation. |
| Harrison | `harrison.py` | 120 min | Registered; source and production telemetry require validation. |
| Hinds | `hinds.py` | 90 min | Registered; source and production telemetry require validation. |
| Jackson | `jackson.py` | 120 min | Registered; source and production telemetry require validation. |
| Jones | `jones.py` | 120 min | Registered; source and production telemetry require validation. |
| Lauderdale | `lauderdale.py` | 120 min | Registered; source and production telemetry require validation. |
| Madison | `madison.py` | 120 min | Registered; source and production telemetry require validation. |
| Rankin | `rankin.py` | 120 min | Registered; source and production telemetry require validation. |

## Recon queue — no registration added

| County | Official public surface | Decision | Reason |
|---|---|---|---|
| Adams | `https://adamscosheriff.net/portal/jail` | **Revalidated 2026-08-15 — recon only; no registration.** | Normal public portal access continues to expose identity and intake timing but no verified broad source-issued booking/inmate identifier. An intake date cannot be used to manufacture an identity key; retain no scraper unless a bulk listing supplies the required booking-safe fields. |
| Lafayette | `https://lafayettems.com/public-safety/sheriffs-department/` | **Revalidated 2026-08-15 — recon only; no registration.** | Official sheriff page confirms jail administration but exposes no broad inmate/booking roster link or booking-safe fields. A single non-PII integrated-AI policy classification independently returned `recon_only`, identifying the missing complete broad identity, source-issued booking/inmate key, and booking date/time. Retain no scraper unless a compliant public listing appears. |
| Lowndes | `https://portalprod.lowndescounty.com/PublicAccess/JailingSearch.aspx?ID=400` | **Revalidated 2026-08-15 — recon only; no registration.** | Official Tyler Jail Records page requires a known Defendant or Booking Number query and supplies filters for DOB, booking date, and release date. It exposes no broad current roster. No blank search was submitted; retain no scraper until a compliant broad listing appears. |
| Oktibbeha | `https://www.sheriff.oktibbeha.ms.us/inmateRosterFeed` | Recon only | Broad roster is public and paginated, but its source identifier and booked timestamp are exposed only through individual detail records; no supported bulk contract is verified. |
| Warren | `https://www.co.warren.ms.us/elected-officials/sheriff/` | Recon only | Official sheriff pages do not publish a broad current roster or record-level booking fields. |

These sources must not be registered until a supported public list or export provides a complete identity and source-level booking boundary. No CAPTCHA, WAF, TLS, or search-gate workaround was attempted.
