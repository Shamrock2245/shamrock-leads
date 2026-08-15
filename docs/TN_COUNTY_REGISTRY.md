# Tennessee County Scraper Registry

> **Last Updated:** 2026-08-12
> **Registered scheduler jobs:** 22
> **Package:** `scrapers/counties_tn/`
> **Job IDs:** `scraper_tn_<county>` · CLI: `python main.py tn_davidson`

`main.py` is the implementation source of truth for scheduler registration. A county listed below is **registered in code**; that designation does not, by itself, prove a successful production write or freshness of the source. The Scraper Health view and production telemetry remain the evidence for live operation.

## Registered Tennessee inventory

| County / source label | Scraper module | Cadence | Source family | Verification posture |
|---|---|---:|---|---|
| Davidson | `davidson.py` | 60 min | Custom Justice Integration | Registered; inspect live telemetry before operational reliance |
| Shelby | `shelby.py` | 90 min | Custom IML | Registered; prior TLS sensitivity remains a monitoring concern |
| Knox | `knox.py` | 90 min | Custom sheriff roster | Registered; inspect live telemetry before operational reliance |
| TnCIS | `tncis.py` | 180 min | Statewide TnCIS adapter | Registered; source-specific telemetry required |
| Hamilton | `hamilton.py` | 60 min | Custom JSON API | Registered; inspect live telemetry before operational reliance |
| Rutherford | `rutherford.py` | 90 min | JailTracker | Registered; source-specific telemetry required |
| Williamson | `williamson.py` | 90 min | JailTracker | Registered; source-specific telemetry required |
| Montgomery | `montgomery.py` | 60 min | Embedded roster JSON | Registered; source-specific telemetry required |
| Sumner | `sumner.py` | 90 min | Custom OCV/S3 handling | Registered; source-specific telemetry required |
| Wilson | `wilson.py` | 90 min | JailTracker | Registered; source-specific telemetry required |
| Bradley | `bradley.py` | 90 min | Southern Software | **Fail-closed safeguard deployed 2026-08-15.** Configured Citizen Connect agency `BradleyCoTN` resolves to the generic agency directory rather than a Bradley booking roster. The county guard emits no records and does not invoke the shared parser until an official broad roster supplies a source-issued booking/inmate ID and booking time. Deployment run `31860512979` completed successfully; Bradley-specific persistence and alert telemetry remain unproven. |
| Blount | `blount.py` | 90 min | JailTracker | **Inherited JailTracker fail-closed safeguard deployed.** Revalidated 2026-08-15: official landing page requires image-character human verification. The shared guard makes no CAPTCHA attempt, proxy request, profile request, sensitive-field collection, or synthetic key; it emits no records until a separately verified listing-only contract exists. |
| Sevier | `sevier.py` | 90 min | Zuercher | **Fail-closed safeguard deployed 2026-08-15.** The configured Zuercher hostname no longer resolves normally, and the currently accessible official ISOMS listing has no verified source-issued booking/inmate identity contract. The county guard emits no records until a compliant broad roster is validated. Deployment run `31861048094` completed successfully; Sevier-specific persistence and alert telemetry remain unproven. |
| Washington | `washington.py` | 90 min | Southern Software | **Fail-closed safeguard deployed 2026-08-15.** Configured Citizen Connect agency `WashingtonCoTN` resolves to the generic agency directory rather than a Washington booking roster. The county guard emits no records and does not invoke the shared parser until an official broad roster supplies a source-issued booking/inmate ID and booking time. Deployment run `31861360339` completed successfully; Washington-specific persistence and alert telemetry remain unproven. |
| Maury | `maury.py` | 90 min | JailTracker | **Inherited JailTracker fail-closed safeguard deployed.** Revalidated 2026-08-15: normal public access to the official landing page returned service-unavailable. The shared guard makes no CAPTCHA attempt, proxy request, profile request, sensitive-field collection, or synthetic key and emits no records until a separately verified listing-only contract exists. |
| Robertson | `robertson.py` | 90 min | Southern Software | **Fail-closed safeguard deployed 2026-08-15.** Configured Citizen Connect agency `RobertsonCoTN` resolves to the generic agency directory rather than a Robertson booking roster. The county guard emits no records and does not invoke the shared parser until an official broad roster supplies a source-issued booking/inmate ID and booking time. Deployment run `31861791340` completed successfully; Robertson-specific persistence and alert telemetry remain unproven. |
| Hamblen | `hamblen.py` | 90 min | Zuercher | **Fail-closed safeguard deployed 2026-08-15.** The configured Zuercher hostname no longer resolves normally, and the currently accessible official ISOMS listing exposes intake timing but no verified source-issued booking/inmate identity contract. The county guard emits no records until a compliant broad roster is validated. Deployment run `31862118252` completed successfully; Hamblen-specific persistence and alert telemetry remain unproven. |
| Bedford | `bedford.py` | 120 min | Southern Software | **Locally validated fail-closed safeguard pending deployment.** Configured Citizen Connect agency `BedfordCoTN` resolves to the generic agency directory rather than a Bedford booking roster. The county guard emits no records and does not invoke the shared parser until an official broad roster supplies a source-issued booking/inmate ID and booking time. |
| Coffee | `coffee.py` | 120 min | Southern Software | Registered; source-specific telemetry required |
| Lincoln | `lincoln.py` | 120 min | Southern Software | Registered; source-specific telemetry required |
| Giles | `giles.py` | 120 min | Southern Software | Registered; source-specific telemetry required |
| Putnam | `putnam.py` | 120 min | Public ISOMS roster | **Deployed 2026-08-12 EDT**; public service health green and local source smoke passed; no per-scraper Mongo write or Slack delivery has been claimed |

## Putnam implementation notes

The official public ISOMS roster is paginated under `https://isoms.putnamcountytnsheriff.gov:8001/Jail`. The source supplies identity, intake time, custody/release status, charges, and per-charge bond figures, but it does **not** expose a county-issued booking number in the roster view. `putnam.py` therefore uses a deterministic surrogate derived from the public full name and intake time solely for the immutable `County + Booking_Number` dedup key. The record explicitly labels that origin in internal metadata and never represents the surrogate as a county-issued booking number.

The parser uses the public current-inmate view (`hours=0`), honours server pagination, pauses between page requests, and makes no attempt to bypass authentication, CAPTCHAs, rate limits, or other access controls. A local source smoke on 2026-08-12 parsed 482 records with non-empty dedup keys and valid Tennessee/county/status fields. The committed implementation deployed successfully on 2026-08-12 EDT, after which the public CRM health endpoint and approved public hosts returned healthy responses. Neither result is evidence of a Putnam-specific Mongo write or alert delivery; those telemetry checks remain pending.

## Recon queue

| County | Public surface observed | Current decision |
|---|---|---|
| Sullivan | Sheriff-hosted OCV public inmate roster at `https://www.scsotn.com/inmateRoster` | **Recon only, revalidated 2026-08-15:** normal public broad cards expose custody/booked time but no visible source-issued booking or inmate identifier, while also exposing sensitive address, demographic/physical, image, and detail-link content. Do not use cards, profile links, app, or denied feed; register only if a booking-safe broad contract becomes available. |
| Remaining high-volume TN counties | To be reconned one official source at a time | Prioritize sources that expose a durable booking identifier, public custody status, charges, and bond data without elevated request volume. |

## Identity and safety

- `ArrestRecord.State = "TN"`.
- `scraper_id = scraper_tn_<county>`.
- Never collapse same-name counties across states; state is part of the dashboard and scheduler identity.
- A roster source is not a bond case. Scrapers must not create defendants, paperwork, POAs, payments, or outbound contact.
- Keep sources rate-limited and fail closed when a required public identity field is unavailable.
