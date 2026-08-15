# Mississippi County Scraper Registry

> **Last updated:** 2026-08-14
> **Registered scheduler jobs:** 9
> **Package:** `scrapers/counties_ms/`

`main.py` is the source of truth for scheduler registration. A listed county is registered in code; this does **not** prove that its public source is currently healthy, that Mongo accepted a write, or that a downstream alert was delivered. County and state remain part of the immutable booking identity boundary.

## Registered Mississippi jobs

| County | Module | Interval | Evidence boundary |
|---|---|---:|---|
| DeSoto | `desoto.py` | 120 min | **Inherited JailTracker fail-closed safeguard deployed.** Revalidated 2026-08-15: official landing page requires image-character human verification. The shared guard makes no CAPTCHA attempt, proxy request, profile request, sensitive-field collection, or synthetic key; it emits no records until a separately verified listing-only contract exists. |
| Forrest | `forrest.py` | 120 min | **Locally validated fail-closed safeguard pending deployment.** The configured official API returned HTTP 404 and the prior parser used an unsafe `id` fallback without a verified booking timestamp. The registered guard emits no records until a source-issued booking-safe API contract is revalidated. |
| Harrison | `harrison.py` | 120 min | Registered; source and production telemetry require validation. |
| Hinds | `hinds.py` | 90 min | **Fail-closed safeguard deployed 2026-08-15.** The prior profile-enriching path collected DOB and address and relied on an unverified listing contract. Aggregate normal-access validation returned no current listing rows or source IDs; the registered guard now emits no records until a listing-only booking-safe contract is revalidated. Deployment run `31855121732` completed successfully; Hinds-specific persistence and alert telemetry remain unproven. |
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
| Oktibbeha | `https://www.sheriff.oktibbeha.ms.us/inmateRosterFeed` | **Revalidated 2026-08-15 — recon only; no registration.** | Normal public pages expose a paginated name-only roster with View Charges and notification actions. No source-issued booking/inmate identifier or booking timestamp is available on listing rows; those actions must not be used to build a bulk contract. Retain no scraper unless required fields appear on the public listing itself. |
| Warren | `https://www.co.warren.ms.us/elected-officials/sheriff/` | **Revalidated 2026-08-15 — recon only; no registration.** | Normal official sheriff page publishes office information only; no current inmate roster, booking list, or booking-safe public fields are exposed. Retain no scraper until a compliant broad listing appears. |

These sources must not be registered until a supported public list or export provides a complete identity and source-level booking boundary. No CAPTCHA, WAF, TLS, or search-gate workaround was attempted.
