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
| Bradley | `bradley.py` | 90 min | Southern Software | Registered; source-specific telemetry required |
| Blount | `blount.py` | 90 min | Southern Software | Registered; source-specific telemetry required |
| Sevier | `sevier.py` | 90 min | Zuercher | Registered; source-specific telemetry required |
| Washington | `washington.py` | 90 min | Southern Software | Registered; source-specific telemetry required |
| Maury | `maury.py` | 90 min | Southern Software | Registered; source-specific telemetry required |
| Robertson | `robertson.py` | 90 min | Southern Software | Registered; source-specific telemetry required |
| Hamblen | `hamblen.py` | 90 min | Zuercher | Registered; source-specific telemetry required |
| Bedford | `bedford.py` | 120 min | Southern Software | Registered; source-specific telemetry required |
| Coffee | `coffee.py` | 120 min | Southern Software | Registered; source-specific telemetry required |
| Lincoln | `lincoln.py` | 120 min | Southern Software | Registered; source-specific telemetry required |
| Giles | `giles.py` | 120 min | Southern Software | Registered; source-specific telemetry required |
| Putnam | `putnam.py` | 120 min | Public ISOMS roster | **Local public-source smoke passed 2026-08-12**; no production write has been claimed |

## Putnam implementation notes

The official public ISOMS roster is paginated under `https://isoms.putnamcountytnsheriff.gov:8001/Jail`. The source supplies identity, intake time, custody/release status, charges, and per-charge bond figures, but it does **not** expose a county-issued booking number in the roster view. `putnam.py` therefore uses a deterministic surrogate derived from the public full name and intake time solely for the immutable `County + Booking_Number` dedup key. The record explicitly labels that origin in internal metadata and never represents the surrogate as a county-issued booking number.

The parser uses the public current-inmate view (`hours=0`), honours server pagination, pauses between page requests, and makes no attempt to bypass authentication, CAPTCHAs, rate limits, or other access controls. A local source smoke on 2026-08-12 parsed 482 records with non-empty dedup keys and valid Tennessee/county/status fields; this is source validation, **not** evidence of a deployed Mongo write or alert delivery.

## Recon queue

| County | Public surface observed | Current decision |
|---|---|---|
| Sullivan | OCV-hosted public inmate roster at `https://www.scsotn.com/inmateRoster` | Do not register yet. The public browser page renders roster data, but direct S3-feed retrieval returned access-denied in this environment. Complete source-contract reconnaissance through an approved public route before implementation; do not circumvent access controls. |
| Remaining high-volume TN counties | To be reconned one official source at a time | Prioritize sources that expose a durable booking identifier, public custody status, charges, and bond data without elevated request volume. |

## Identity and safety

- `ArrestRecord.State = "TN"`.
- `scraper_id = scraper_tn_<county>`.
- Never collapse same-name counties across states; state is part of the dashboard and scheduler identity.
- A roster source is not a bond case. Scrapers must not create defendants, paperwork, POAs, payments, or outbound contact.
- Keep sources rate-limited and fail closed when a required public identity field is unavailable.
