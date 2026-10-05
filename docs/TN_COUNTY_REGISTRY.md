# Tennessee County Scraper Registry

> **Last Updated:** 2026-10-05
> **Registered scheduler jobs:** 22 (9 `verified_public`, 13 `fail_closed` guards / holds)
> **Package:** `scrapers/counties_tn/`
> **Job IDs:** `scraper_tn_<county>` · CLI: `python main.py tn_davidson`

`main.py` is the implementation source of truth for scheduler registration. A county listed below is **registered in code**; that designation does not, by itself, prove a successful production write or freshness of the source. The Scraper Health view and production telemetry remain the evidence for live operation.

## Registered Tennessee inventory

| County / source label | Scraper module | Cadence | Source family | Verification posture |
|---|---|---:|---|---|
| Davidson | `davidson.py` | 60 min | DCSO RecentBookings + Details | ✅ **`verified_public` (2026-09-29)** — Official DCSO JMS Number (7 digits). Plain HTTPS. Smoke: 121+ records. |
| Shelby | `shelby.py` | 90 min | Memphis 201 Poplar IML Portal | ✅ **`verified_public` (2026-09-29)** — Official Booking Number (8 digits). Plain HTTPS. Smoke: 150+ records. |
| Knox | `knox.py` | 90 min | Knox Sheriff 24h Arrests + Inmates | ✅ **`verified_public` (2026-09-29)** — Official Knox IDN# (7 digits). Plain HTTPS. Smoke: 51+ records. |
| TnCIS | `tncis.py` | 180 min | Statewide TnCIS adapter | `fail_closed` — Cloudflare-protected, no proven public contract; Obscura/proxy/stealth fallback removed 2026-09-25 |
| Hamilton | `hamilton.py` | 60 min | HCSO Daily Booking API + Inmates | ✅ **`verified_public` (2026-09-30)** — Official Record GUID (`R_ID`) / SPN. Plain HTTPS. Smoke: 101+ records. |
| Rutherford | `rutherford.py` | 90 min | JailTracker | `fail_closed` — pending compliant broad-listing source contract |
| Williamson | `williamson.py` | 90 min | JailTracker | `fail_closed` — pending compliant broad-listing source contract |
| Montgomery | `montgomery.py` | 60 min | Embedded roster JSON | `fail_closed` — pending compliant broad-listing source contract |
| Sumner | `sumner.py` | 90 min | MyOCV `inmatesV3` S3 Feed | ✅ **`verified_public` (2026-09-29)** — Official Inmate ID (6 digits). S3 JSON. Smoke: 816+ records. |
| Wilson | `wilson.py` | 90 min | JailTracker | `fail_closed` — pending compliant broad-listing source contract |
| Bradley | `bradley.py` | 90 min | Southern Software | **Fail-closed safeguard deployed 2026-08-15.** Configured Citizen Connect agency `BradleyCoTN` resolves to the generic agency directory rather than a Bradley booking roster. The county guard emits no records and does not invoke the shared parser until an official broad roster supplies a source-issued booking/inmate ID and booking time. |
| Blount | `blount.py` | 90 min | JailTracker | **Inherited JailTracker fail-closed safeguard deployed.** Revalidated 2026-08-15: official landing page requires image-character human verification. Emits no records. |
| Sevier | `sevier.py` | 90 min | SCSO Next.js / MyOCV Public Roster | ✅ **`verified_public` (2026-09-30)** — Official Numeric Inmate ID (6 digits). Smoke: 100+ records. |
| Washington | `washington.py` | 90 min | WCSO 30-Day Rolling PDF Sheet | ✅ **`verified_public` (2026-09-30)** — Official Booking # (5–10 digits) parsed via `pypdf`/`pdfplumber`. Smoke: 509+ records. |
| Maury | `maury.py` | 90 min | JailTracker | **Inherited JailTracker fail-closed safeguard deployed.** Service-unavailable through normal public access; emits no records. |
| Robertson | `robertson.py` | 90 min | Southern Software | **Fail-closed safeguard deployed 2026-08-15.** Generic agency directory; emits no records. |
| Hamblen | `hamblen.py` | 90 min | Hamblen Sheriff ISOMS Portal | ✅ **`verified_public` (2026-09-30)** — Deterministic surrogate derived from public name + intake time. Smoke: 351+ records. |
| Bedford | `bedford.py` | 120 min | Southern Software | **Fail-closed safeguard deployed 2026-08-15.** Generic agency directory; emits no records. |
| Coffee | `coffee.py` | 120 min | Southern Software | **Fail-closed safeguard deployed 2026-08-15.** Generic agency directory; emits no records. |
| Lincoln | `lincoln.py` | 120 min | Southern Software | **Fail-closed safeguard deployed 2026-08-15.** Generic agency directory; emits no records. |
| Giles | `giles.py` | 120 min | Southern Software | **Fail-closed safeguard deployed 2026-08-15.** Generic agency directory; emits no records. |
| Putnam | `putnam.py` | 120 min | Public ISOMS roster | ✅ **`verified_public` (2026-08-12)** — Deterministic surrogate derived from public name + intake time. Smoke: 540+ records. |

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
