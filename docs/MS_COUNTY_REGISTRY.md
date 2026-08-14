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
| Adams | `https://adamscosheriff.net/portal/jail` | Recon only | Public ISOMS listing exposes names and intake timestamps, but lacks a verified bulk source-issued booking/inmate key and intermittently returns a Cloudflare challenge to unattended retrieval. |
| Lafayette | `https://lafayettems.com/public-safety/sheriffs-department/` | Recon only | Official sheriff page does not publish a documented broad public roster or booking fields. |
| Lowndes | `https://portalprod.lowndescounty.com/PublicAccess/JailingSearch.aspx?ID=400` | Recon only | Official Tyler portal is name-known-or-booking-number search oriented; no broad current roster or bulk booking-time contract is exposed. |
| Oktibbeha | `https://www.sheriff.oktibbeha.ms.us/inmateRosterFeed` | Recon only | Broad roster is public and paginated, but its source identifier and booked timestamp are exposed only through individual detail records; no supported bulk contract is verified. |
| Warren | `https://www.co.warren.ms.us/elected-officials/sheriff/` | Recon only | Official sheriff pages do not publish a broad current roster or record-level booking fields. |

These sources must not be registered until a supported public list or export provides a complete identity and source-level booking boundary. No CAPTCHA, WAF, TLS, or search-gate workaround was attempted.
