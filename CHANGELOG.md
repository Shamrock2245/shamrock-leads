# ShamrockLeads — Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — 2026-08-15 (Calcasieu source-contract guard)

### Changed
- **Calcasieu Parish, LA fail-closed** — retired the prior `/api/inmates/roster` request path after it returned public HTTP `404`. The registered scraper now performs no source fetch and emits no records until the current public API, complete displayed name, source-issued identifier, booking time, and bounded pagination are revalidated. Scraper Health and the 947-scope matrix now state `fail_closed`.

## [Unreleased] — 2026-08-15 (complete source-contract reconnaissance)

### Added
- **Runtime-inclusive source-contract matrix** — added `docs/recon/COUNTY_SOURCE_CONTRACT_MATRIX.md` covering **947 scopes**: all 942 Census county-equivalents across the ten repository states plus five registered non-county scopes. The matrix separates repository registration from source posture: `verified_public`, `candidate_productive`, `recon_only`, `unverified`, and deployed `fail_closed` guards.
- **Versioned non-PII reconnaissance evidence** — added the Census-based inventory, 942-row evidence file, reproducible inventory/evidence/matrix scripts, and contract tests. Evidence contains source-contract posture and public source references only; it does not contain arrest records, images, profiles, or contact data.
- **Louisiana bounded validation note** — recorded ordinary-public-listing contract findings for Beauregard, Calcasieu, and St. Mary. These findings do not create new runtime parsers or claim persistence, alert, payment, or bond telemetry.

### Changed
- **Active coverage documentation synchronized** — README, AGENTS, and the multi-state roadmap now reflect the canonical **358** registered labels and active surety policy: OSI in Florida; Palmetto in FL, SC, NC, TN, TX, CT, LA, and MS. Georgia and Alabama are correctly distinguished as adjacent repository coverage rather than Palmetto license assertions.

## [Unreleased] — 2026-08-15 (scraper registry integrity)

### Added
- **Registered-county scaffold contract test** — statically validates all **358** canonical `County (ST)` labels (67 FL, 85 GA, 60 NC, 46 SC, 34 TX, 22 TN, 16 AL, 13 LA, 9 MS, and 6 CT) have a local scraper module and a corresponding `main.register_scrapers` entry. The test does not import modules or contact county sources.
- **Hendry source-key regression tests** — verify that an OCV row without the source-issued inmate identifier is skipped and that a present source identifier is preserved as the immutable `County + Booking_Number` key.

### Fixed
- **Hendry and Monroe booking-key safety** — removed name, date, and document-derived booking fallbacks. Hendry now requires the official OCV `inmateID`; Monroe requires an MNI from the source mugshot filename, the official offense number, or the official CAD number. Rows that lack these identifiers fail closed before a record is returned.
- **Florida coverage documentation and regression expectation** — corrected the stale “not yet scraped” wording to distinguish full 67-county scaffold/scheduler coverage from source-contract validation, and aligned the legacy total-fleet test with the current 358 registered labels.

## [Unreleased] — 2026-08-15 (Paperwork from recorded bond)

### Added
- **Do Paperwork** on Edit Bond, Active Bonds rows/cards, and New Indemnitor (`Save & Do Paperwork`). Opens the OSI/Palmetto DocuSeal packet builder with booking, POA, case #, and names filled — for cases re-added after a scraper purge.

## [Unreleased] — 2026-08-14 (OpenCut overlay)

### Added
- **OpenCut editor overlay** — transitions, stylize/glow effects, text presets, timeline drag types, and an Auto/AI assets tab. Copied onto `opencut-classic` at image build (`opencut/overlay/`).

## [Unreleased] — 2026-08-14 (Bond Intelligence)

### Changed
- **Bond Intelligence tab rebuilt** as a work desk: estimated statutory premium, in-custody writable count, hot leads, capture rate, a 48-hour write queue, state money map, and counties ranked by premium. Old stacked KPI dump and white filter pills are gone. Stylesheet is now actually linked.

## [Unreleased] — 2026-08-14 (Record Bond)

### Fixed
- **Record Bond premium** — $100 minimum per criminal charge; 10% of penal when a charge is above $1,000. A $500 single-charge bond is $100, not $50.
- **Lee URL auto-fill** — DOB and defendant address now populate from the sheriff booking API (`dob` / composed street+city+state+zip). The modal was only reading `date_of_birth` and never set the address field.

## [Unreleased] — 2026-08-14 (OSINT)

### Added
- **Holehe** OSINT chip — email → registered accounts on 120+ sites (same pattern as Ignorant for phones). Auto-selects when an email is entered. Single-engine test accepts `user@domain`. Does not notify the target.
- **HIBF** OSINT chip — license plate → public Flock LE *search audit* logs via Have I Been Flocked (`POST /api/search/text` with SHA-256 plate prefixes). Incomplete FOIA data, not a live camera hit. Does not log the raw plate.

### Changed
- Removed unused **Snoop** from the OSINT matrix, worker probe, and valid-engine list (package was never installed). Not adding OpenOSINT / bbot / social-analyzer — they overlap Maigret/Sherlock or are too heavy for this VPS.

## [Unreleased] — 2026-08-14 (disk)

### Added
- **St. Mary Parish, LA public-roster scraper** — parses the official sheriff-site current roster with source-issued booking numbers, booking timestamps, public-card charges and bond amounts, bounded pagination, and no profile-page collection or access-control workaround. Registered as `scraper_la_st_mary` every 120 minutes and added to `REGISTERED_COUNTIES`.
- **St. Mary Parish parser regression tests** — verify public-card mapping, source-issued booking-key handling, fail-closed missing fields, and explicit pagination. A bounded local two-page official-source smoke parsed 40 records on 2026-08-14. The `4a6fe7f` rollout subsequently completed successfully and public host checks passed; St. Mary-specific Mongo upsert and alert delivery remain unclaimed until telemetry is observed.
- **Bossier Parish, LA public-roster scraper** — added a bounded parser for the official Sheriff public listing. It reads only public Flight-card fields: complete source name, source-issued Inmate ID, and Booked Date/time. It requires all source fields, preserves booking date/time, uses listing-only pagination, omits images and profile access, and stops on empty or duplicate-only pages. Registered as `scraper_la_bossier` every 120 minutes with a state-qualified dashboard label. Deterministic tests passed and a bounded two-page aggregate-only official-source smoke parsed 20 unique records with state, parish, source-key, booking date/time, deduplication, and listing-only invariants passing. Deployment run `31852987527` completed successfully; public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy. Parish-specific persistence and alert delivery remain unproven.
- **Rapides Parish, LA source revalidation** — rechecked the Sheriff-linked NewWorld public inmate inquiry through normal access. Broad results still expose only complete name, Subject Number, custody, and facility; they do not provide a source-issued booking key or booking date/time. Booking-related form fields are search controls rather than bulk listing data. Rapides remains recon-only and unregistered; no profile access, synthetic identity, source scraper, scheduler job, dashboard label, write, or alert was added.
- **St. Landry Parish, LA source revalidation** — verified the Sheriff-branded public Show All roster through normal access. Its broad rows expose complete name, DOB, race, gender, and an empty arrest-date column, but no source-issued booking/inmate identifier or usable booking/arrest timestamp. St. Landry remains recon-only and unregistered; no DOB collection, profile access, inferred identity, source scraper, scheduler job, dashboard label, write, or alert was added.
- **Terrebonne Parish, LA source revalidation** — verified the Sheriff-published CentralSquare public Inmates portal through normal access. Broad results expose mugshot, complete name, race, sex, arrest date, held-for agency, age, and charge/bond text, but no labelled source-issued booking or inmate identifier. The arrest date cannot be used to manufacture an identity key. Terrebonne remains recon-only and unregistered; no profile collection, inferred identity, source scraper, scheduler job, dashboard label, write, or alert was added.
- **Grant and Union Parish, LA source revalidation** — verified the current official LCLE LA VINE parish roster directory and each parish sheriff public site. Neither parish is listed in the directory, and the sheriff public pages expose no alternate broad roster or booking-safe field contract. Both remain recon-only and unregistered; no source scraper, scheduler job, dashboard label, inferred identity, write, or alert was added.
- **Adams County, MS source revalidation** — rechecked the official public ISOMS portal through normal access. It continues to expose identity and intake timing but no verified broad source-issued booking or inmate identifier. Intake timing cannot be used to manufacture an identity key. Adams remains recon-only and unregistered; no profile collection, inferred identity, source scraper, scheduler job, dashboard label, write, or alert was added.
- **Lafayette County, MS source revalidation** — rechecked the official sheriff public page through normal access. It confirms jail administration but publishes no broad inmate or booking roster link and no booking-safe fields. A single integrated-AI classification was run only on non-PII source-contract facts and independently returned `recon_only`; it did not retrieve or infer any person data. Lafayette remains recon-only and unregistered; no source scraper, scheduler job, dashboard label, write, or alert was added.
- **Lowndes County, MS source revalidation** — verified the official Tyler Jail Records page through normal access. The portal requires a known Defendant or Booking Number and offers DOB, booking-date, and release-date filters, but no broad current roster. A blank search was not submitted. Lowndes remains recon-only and unregistered; no source scraper, scheduler job, dashboard label, inferred identity, write, or alert was added.
- **Oktibbeha County, MS source revalidation** — verified the official paginated roster through normal access. Listing rows expose names plus View Charges and notification actions, but not a source-issued booking/inmate identifier or booking timestamp. Those individual actions were not used to construct a bulk contract. Oktibbeha remains recon-only and unregistered; no source scraper, scheduler job, dashboard label, inferred identity, write, or alert was added.
- **Warren County, MS source revalidation** — verified the official sheriff page through normal access. It publishes office information only and exposes no current inmate roster, booking list, or booking-safe public fields. Warren remains recon-only and unregistered; no source scraper, scheduler job, dashboard label, inferred identity, write, or alert was added.
- **St. Clair County, AL public-roster scraper** — added a source-faithful parser for the official sheriff current roster using complete public names, source-issued Booking # values, and booking timestamps. The scraper uses bounded public pagination, does not collect profile details or mugshot URLs, and fails closed when a required source field is absent. Registered as `scraper_al_st_clair` every 120 minutes and added to the state-qualified dashboard registry. A bounded two-page local smoke parsed 40 unique records with state/county, booking-number, booking-date, and deduplication invariants passing. The `445edba` rollout completed successfully and all required public host checks returned 200; St. Clair persistence and alert delivery remain unproven until telemetry is observed.
- **Etowah County, AL official-roster repair** — replaced the unsupported CAPTCHA-solving JailTracker path with the official sheriff current-roster parser. The repaired path uses only roster-card data, source-issued Booking # values, booking timestamps, bounded `grp` pagination, and no profile or image collection. A two-page aggregate smoke parsed 20 unique records with state/county, booking-number, booking-date, and deduplication invariants passing. The `2389a78` rollout completed successfully and required public host checks returned 200; production persistence and alert delivery remain unproven.
- **Sarasota County, FL source-safety repair** — retired the third-party mirror, residential-proxy, CAPTCHA/JailTracker, Revize profile, DOB, mugshot, and synthetic-identifier paths because no official booking-safe broad roster contract is verified through normal access. `scraper_sarasota` now fails closed without making a network request. Added deterministic no-network regression tests. Deployment run `31843789326` completed successfully; public leads `/health`, sign, school, paperwork, and social `/auth` probes returned healthy responses. Sarasota-specific persistence and alert delivery remain unproven, and the guard intentionally emits no records.

### Changed
- **South Carolina Zuercher source-safety hardening** — hardened `ZuercherBaseScraper` to reject synthetic name-and-arrest-date booking keys, require source-issued booking/inmate IDs plus source booking dates, and preserve custody as unknown unless explicitly supplied. Anderson, Cherokee, Colleton, Kershaw, and Laurens now fail closed before any network request because their official portals are search-only, unavailable, or lack a booking-safe source boundary. Added deterministic no-network and source-issued mapping tests; see `docs/SC_ZUERCHER_SOURCE_SAFETY.md`. The `7718bf8` rollout completed successfully and all required public host checks returned 200; per-scraper persistence and alert telemetry remain unproven.
- **Southern Software Citizen Connect source-safety hardening** — removed the shared parser’s synthetic `SSW_*` booking-key construction, requires complete identity plus a source-issued booking/inmate ID and `Booked` value, and preserves custody as unknown unless the official source supplies it. All 51 dependent wrappers now fail closed per-card when those source boundaries are absent. Baldwin AL’s county-linked Citizen Connect route currently exposes an unsupported warning response instead of a usable booking-card contract, so its aggregate smoke returned zero records without generating a synthetic identity. Added deterministic source-key, labelled-key, and fail-closed regression tests; see `docs/SOUTHERN_SW_SOURCE_SAFETY.md`. The `4d58f29` rollout completed successfully and public host checks passed; production persistence and alert delivery remain unproven.
- **JailTracker source-safety hardening** — removed shared CAPTCHA solving, OCR, paid solver use, browser/API harvesting, sensitive-field collection, and unsourced DOM/API identity fallback from the JailTracker base. Twenty-four inherited wrappers across FL, AL, GA, MS, SC, and TN now fail closed before any network access pending a county-specific public roster contract with complete identity, source-issued booking/inmate ID, and booking date/time. Sarasota’s separate `scrape()` override is excluded for a dedicated source audit. Added deterministic no-network coverage; see `docs/JAILTRACKER_SOURCE_SAFETY.md`. The `0a75169` rollout completed successfully and required public host checks returned 200; production persistence and alert delivery remain unproven.
- **Madison County, AL source-safety repair** — removed residential-proxy discovery, broad endpoint harvesting, and synthetic `MAD_*` booking IDs from the registered path. The official inmate-information surface has not established a supported public booking-safe broad roster through normal access, so `scraper_al_madison` now fails closed without making a network request. Added deterministic no-network regression tests. The `b975c3c` rollout completed successfully and required public host checks returned 200; no Madison writes or alerts are expected from the guard.
- **Mobile County, AL source-safety repair** — removed residential-proxy access, DOB retention, and synthetic `MOB_*` booking IDs from the registered path. The official current-inmates portal has not established a supported public booking-safe broad roster through normal access, so `scraper_al_mobile` now fails closed without making a network request. Added deterministic no-network regression tests. The `931eb77` rollout completed successfully and required public host checks returned 200; no Mobile writes or alerts are expected from the guard.
- **Montgomery County, AL source-safety repair** — removed unverified direct API ingestion after the official public inmates endpoint returned HTTP 403 through normal access. `scraper_al_montgomery` now fails closed without making a network request until a supported booking-safe broad roster is verified. Added deterministic no-network regression tests. The `962587d` rollout completed successfully and required public host checks returned 200; no Montgomery writes or alerts are expected from the guard.
- **Tuscaloosa County, AL source-safety repair** — removed a legacy path that targeted Tulsa County, Oklahoma, fell back to an unsourced `id`, and constructed records with non-canonical schema fields. Tuscaloosa’s official Sheriff ‘Who’s in Jail’ route requires human verification and does not establish a broad booking-safe public roster through normal access. `scraper_al_tuscaloosa` now fails closed without making a network request. Added deterministic no-network regression tests. Deployment run `31851444734` completed successfully; public leads `/health`, sign, school, paperwork, and social `/auth` probes returned healthy responses. The guard intentionally emits no records; county-specific persistence and alert delivery remain unproven.
- **Cross-state legacy P2C source-safety hardening** — added a shared `SOURCE_CONTRACT_VALIDATED` guard to `P2CBaseScraper` and marked fifteen audited GA, NC, and SC wrappers fail closed before any HTTP request or form submission. Their official sources are restricted, search-only, or lack a booking-safe identity boundary. Added deterministic no-network coverage for every guarded wrapper and documented all decisions in `docs/LEGACY_P2C_SOURCE_SAFETY.md`. Lincoln NC is excluded because its verified OCV repair is productive; Johnson TX remains recon-only without code changes. The `0de5f79` rollout completed successfully; the initial Leads `/health` probe returned a transient 502, then recovered to 200 while all other required public hosts returned 200. Per-scraper persistence and alert telemetry remain unproven.
- **Gwinnett County, GA source-safety repair** — removed the unsafe empty SmartWEB form submission and record construction that omitted source booking numbers and complete identities. The official public last-24-hours view abbreviates given names, so `scraper_ga_gwinnett` now fails closed with zero emitted records until a supported bulk identity contract is available. Added deterministic no-network regression tests. The `482939f` rollout subsequently completed successfully and public host checks passed; no Gwinnett writes or alerts are expected from the safety guard.
- **Mississippi registry reconciliation** — added `docs/MS_COUNTY_REGISTRY.md` as the state source of truth for the nine registered jobs. Official-source validation of Adams, Lafayette, Lowndes, Oktibbeha, and Warren found no safe broad booking-identity contract; all five remain recon-only, with no scraper or scheduler registration added.
- **Lincoln County, NC OCV source repair** — replaced the stale P2C wrapper with the official sheriff-linked OCV roster (`a46428092`). The public feed exposes complete names, source-issued Inmate IDs, and Booked Dates; the bounded aggregate smoke parsed 175 records with unique source IDs and booking timestamps. Added deterministic mapping and fail-closed identity tests. The `a7c3fd8` rollout completed successfully and public host checks passed; production persistence and alert delivery remain unproven until observed.
- **Jefferson County, TX source-safety repair** — replaced the stale P2C wrapper with the official sheriff inmate-search reference. The public Next.js page exposes opaque detail routes and PDF links but no verified booking-safe broad roster contract, so the registered path fails closed instead of collecting detail records or relying on unknown source keys. It remains silent until a supported bulk contract exposes complete identity, source-issued booking or inmate ID, and booking date or timestamp. Added deterministic no-network regression tests. The `e0fc47e` rollout completed successfully and public host checks passed; no Jefferson writes or alerts are expected from the safety guard.
- **Guadalupe County, TX source-safety repair** — replaced the stale P2C wrapper with the official Tyler Public Access Jail Records route. The route requires human verification before any jail search or roster data is available, so the registered path fails closed rather than bypassing the control. It remains silent until the county provides a supported public bulk contract with complete identity, a source-issued booking or inmate ID, and booking date or timestamp. Added deterministic no-network regression tests. The `c32b1cf` rollout completed successfully and public host checks passed; no Guadalupe writes or alerts are expected from the safety guard.
- **Ellis County, TX source-safety repair** — replaced the stale P2C wrapper with the county-linked LL Hosting inmate-search reference. The public page did not expose a server-rendered broad roster or supported API configuration, then returned a 403 challenge during normal validation. The registered path therefore fails closed until a stable official bulk contract exposes complete identity, source-issued booking or inmate ID, and booking date or timestamp. Added deterministic no-network regression tests. The `0b481e0` rollout completed successfully and public host checks passed; no Ellis writes or alerts are expected from the safety guard.
- **Bell County, TX source-safety repair** — replaced the stale P2C wrapper with the current official Tyler New World portal reference. The public landing page exposes only a search form, so the registered path does not submit blank or fabricated criteria and fails closed until an official broad roster contract exposes complete identity, a source-issued booking ID, and booking date or timestamp. Added deterministic no-network regression tests. The `769e8ce` rollout completed successfully and public host checks passed; no Bell writes or alerts are expected from the safety guard.
- **Broward County, FL source-safety repair** — retired sequential inmate-detail ID probing, browser impersonation, disabled-TLS requests, DOB retention, and assumed custody status. The official BSO arrest application is Turnstile-protected and its bulk booking contract is unverified, so the existing registered path now fails closed until a supported public roster exposes complete identity, source-issued booking ID, and booking date or timestamp. Added deterministic no-network regression tests. The `16004c6` rollout completed successfully and public host checks passed; no Broward writes or alerts are expected from the safety guard.
- **Connecticut DOC source-safety repair** — retired the legacy statewide A–Z broad-search path after the official CT inmate portal returned BITS BOT access rejection. The prior implementation disabled TLS verification, retained DOB, and could emit records without a booking/admission date. `scraper_ct_doc` remains registered but now fails closed until a supported public bulk contract exposes complete identity, source-issued ID, and booking/admission date. Added deterministic no-network regression tests. The `9c01489` rollout completed successfully and public host checks passed; no CT DOC writes or alerts are expected from the safety guard.
- **York County, SC source-faithful roster repair** — replaced the generic JSON/table parser, disabled-TLS request, assumed custody status, and synthesized booking-key fallback with a public ASP.NET card parser. It maps only the source-issued Booking Number and booking timestamp, preserves unknown custody status, parses public charge descriptions, and fails closed on missing identity fields. Deterministic parser tests passed; a bounded official one-page smoke parsed 15 records with unique source booking keys. The `7e1b561` rollout completed successfully and public host checks passed; York-specific persistence and alert telemetry remain unclaimed until observed.
- **Durham County, NC source-safety repair** — replaced the stale legacy ASP.NET A–Z broad-search path, which disabled TLS verification and could emit records without a booking-date boundary, with a fail-closed guard. `scraper_nc_durham` remains registered but emits no records until a supported public bulk roster exposes complete identity plus source-issued booking fields. Added deterministic no-network regression tests. The rebased `7d07d29` rollout completed successfully and public host checks passed; no Durham writes or alerts are expected from the safety guard.
- **Fulton, GA Socrata source-safety repair** — hardened the shared Socrata parser to reject incomplete identities, missing source-issued booking identifiers, and missing booking dates; removed synthetic name/time booking keys and stopped assuming custody status. Fulton’s official Socrata endpoint returned HTTP 403 during aggregate-only validation, so the registered path now produces a clean zero-record fail-closed result until access is restored. Deterministic shared-base tests passed. The `569441c` rollout completed successfully; after one transient Paperwork 502 recovered, all required public production probes returned 200. No Fulton writes or alerts are expected while the source remains inaccessible.
- **Miami-Dade, FL ArcGIS scraper repair** — replaced broad `outFields=*` retrieval with the minimum official public fields (`ObjectId`, `GlobalID`, `BookDate`, `Defendant`, `Charge1`, `Charge3`), removed address/ZIP/DOB collection, fails closed without complete identity, source key, or booking date, and records custody as unknown instead of assuming it. Deterministic parser tests passed and a bounded official-source smoke parsed 426 records on 2026-08-14. The `7b10bda` rollout subsequently completed successfully and public host checks passed; Miami-Dade-specific Mongo upsert and alert delivery remain unclaimed until telemetry is observed.
- Dashboard image installs `requirements-dashboard.txt` (no Playwright/Selenium/Chromium stack). Daily `docker-prune.sh` now clears unused BuildKit cache (`-a`, older than 24h).

## [Unreleased] — 2026-08-13 (CCX33)

### Added
- **Per-party paperwork links** — staff get Indemnitor + Defendant copy/send cards after finalize. Branded URLs `https://paperwork.shamrockbailbonds.biz/sign/{packet}/{role}` redirect to the live DocuSeal slug. Deliver uses the party’s stored phone, fail-closes on BlueBubbles failure, and refuses unknown numbers.

### Changed
- **Paperwork chain fail-closed** — ID scan no longer creates an unassigned DocuSeal packet; cached clients receive `409 validated_bond_case_required` and continue through the staff-created packet + PIN flow. Super CRM/signature docs now identify DocuSeal as the only provider for new packets.
- **ID scan** now extracts 50-state DL/ID, US/foreign passports, organ donor, height/eyes/hair, dates, REAL ID/veteran flags, and a cropped portrait. Bondsman can scan defendant and indemnitor IDs separately; indemnitor + portal forms hydrate the extra fields.
- **SPECTRA** uses Hudson Rock’s free infostealer OSINT API (email/username). Paid Have I Been Pwned is no longer a SPECTRA dependency. Fake Ft. Myers/Naples geotag clusters were removed.
- **Hetzner CCX33 resource ceilings** — `shamrock-leads` 4g/2cpu → 8g/4cpu, dashboard 2g/1.5 → 3g/2.0, Obscura 512m → 1g, OSINT 1g → 2g, Postiz 2g → 3g, Traccar 512m → 768m, OpenCut 2g → 1.5g. Runbook: `docs/runbooks/vps-ccx33-resize.md`. `SCRAPER_MAX_CONCURRENT` stays 8 until a full cycle is green.
- Daily `maintenance/docker-prune.sh` now ages BuildKit cache at 24h and vacuums runner diag / journal / apt cache (root disk was not grown with the RAM resize).
- **Chromium RAM** — shared `scrapers/chromium_flags.py` (low-end mode, renderer limit 2, site-isolation off) wired into DrissionPage, Playwright/Patchright, JailTracker, Seminole. Scraper image sets `MALLOC_ARENA_MAX=2`.
- Deploy no longer recreates Postiz on a compose-wide mem_limit tweak (Mastra 1600-col crash). Recreate only when `social/` or the repair script changes.

### Fixed
- **OSINT Intel “not hooked up”** — `osint-worker` engines were installed (Maigret, Tookie, Sherlock, Blackbird, SpiderFoot, Ignorant, Instaloader, ExifTool) but `OSINT_WORKER_KEY` was empty, so `/status` 503’d and the UI painted every engine UNAVAILABLE. Shared key is now minted once (`scripts/ensure_osint_worker_key.py`), persisted on the VPS `.env`, and recreated into worker + dashboard. Status probe distinguishes worker-down vs auth-fail vs engine-missing. Trape lure uses dashboard `/track/{session}` (open prefix) with `TRAPE_SERVER_URL=https://leads.shamrockbailbonds.biz`. Toutatis stays gated until `INSTAGRAM_SESSION_ID` is set.
- **Add Indemnitor ID scan 413** — live HTTPS nginx had no `client_max_body_size` (default 1MB), so phone DL photos never reached OCR. Limit raised to 50MB; the modal now EXIF-orients, compresses, and retries OCR at 0/90/180/270° before hydrating name, address, DOB, and DL #.

## [Unreleased] — 2026-08-12 (production closeout)

### Added
- **Marshall County, AL public-roster scraper** — parses the official sheriff-site current roster with source-issued booking numbers, booking timestamps, explicit Next-page handling, and no detail-page collection or access-control workaround. Registered as `scraper_al_marshall` every 120 minutes and added to `REGISTERED_COUNTIES`.
- **Marshall Alabama parser regression tests** — verify public-card mapping, source-issued booking-key handling, fail-closed missing fields, and pagination guards. A bounded local two-page official-source smoke parsed 40 records on 2026-08-14. The `9ed467e` rollout subsequently completed successfully and public host checks passed; Marshall-specific Mongo upsert and alert delivery remain unclaimed until telemetry is observed.
- **Tangipahoa Parish, LA public-roster scraper** — parses the official sheriff-linked, paginated public roster without detail-page collection or access-control workarounds. The roster ID is not labelled as a booking number, so the scraper uses a clearly labelled deterministic key composed only of its public numeric roster ID plus booking timestamp and fails closed when either is absent. Registered as `scraper_la_tangipahoa` every 120 minutes and added to `REGISTERED_COUNTIES`.
- **Tangipahoa Parish parser regression tests** — verify public-table mapping, source-key stability, fail-closed identity handling, and pagination. A bounded local two-page official-source smoke parsed 20 records on 2026-08-14. The `f456205` rollout subsequently completed successfully and public host checks passed; Tangipahoa-specific Mongo upsert and alert delivery remain unclaimed until telemetry is observed.
- **Lee County, AL public-roster scraper** — parses the official Lee County Sheriff Next.js roster response and documented public pagination without browser-control workarounds or generic OCV-feed access. The source does not label a booking number, so the scraper uses only a clearly labelled deterministic key composed of its public `NameID` plus booking timestamp, failing closed when either is absent. Registered as `scraper_al_lee` every 120 minutes and added to `REGISTERED_COUNTIES`; its `Lee (AL)` label remains distinct from Lee jobs in GA, NC, and SC.
- **Lee Alabama parser regression tests** — verify public-card mapping, state-scoped source-key stability, fail-closed identity behavior, and pagination. A bounded local two-page official-source smoke parsed 20 records on 2026-08-14. The `6109410` rollout subsequently completed successfully and public host checks passed; Lee-specific Mongo upsert and alert delivery remain unclaimed until their telemetry is observed.
- **Randall County, TX public-roster scraper** — renders and parses the official public OCV/Next.js jail roster with bounded `?page=<n>` pagination. It deliberately does not use the direct OCV S3 feed after that feed returned HTTP 403. The source does not label a booking number, so the scraper emits a clearly labelled deterministic key only from its public Inmate ID plus booking timestamp and fails closed if either value is absent. Registered as `scraper_tx_randall` every 120 minutes and added to `REGISTERED_COUNTIES`.
- **Randall parser regression tests** — verify official-card mapping, fail-closed identifier/date handling, source-key stability, and pagination. A bounded local two-page source smoke parsed 10 records on 2026-08-14; production Mongo upsert and alert delivery remain unclaimed until their telemetry is observed.
- **Putnam County, TN public-roster scraper** — paginated ISOMS parser with source-faithful custody, release, charge, and per-charge bond mapping; uses a clearly labeled deterministic surrogate only where the public roster does not expose a booking number. Registered as `scraper_tn_putnam` every 120 minutes and added to `REGISTERED_COUNTIES`.
- **Putnam parser regression tests** — verify public-field mapping, custody status, surrogate-key stability, and pagination. A local public-source smoke parsed 482 records on 2026-08-12. The committed rollout subsequently succeeded and public CRM/host health checks passed; Putnam-specific Mongo upsert and alert delivery remain unclaimed until their telemetry is observed.

### Fixed
- **SC/NC source identity hardening:** added a shared pre-scoring guard that rejects known historical synthetic booking-number formats before writes, alerts, or health metrics. Buncombe and Johnston, NC now require source-provided identifiers rather than name-hash fallbacks. Rebuilt Newberry, SC to discover the current official Sheriff bookings PDF dynamically and retain only its visible `SO`-prefixed source identifiers. Focused source-key and PDF-parser tests cover the new safeguards. Source validation is documented in `docs/recon/SC_NC_SOURCE_VALIDATION_2026-08-14.md`.
- **Deploy timeout budget** — increased the Hetzner job limit to 45 minutes and SSH command limit to 40 minutes after the verified 30-minute cutoff interrupted the `df24815` cold build before restart verification. This is a pending-code fix until its deployment run succeeds.
- **Production checklist truth** — C2 is now live-verified at `$649` from public JSON-LD; B3 write-bond → paperwork and D2 outbound iMessage are explicitly open human-gated smokes rather than partial checkmarks.
- **Scraper identity safeguards:** removed the stale Georgia EAS batch from scheduler registration; it is now an explicit manual reconnaissance utility pending endpoint and source booking-ID revalidation. `P2CBaseScraper` now drops rows that lack a source-provided booking identifier instead of synthesizing a name-derived key, preserving the immutable `County + Booking_Number` deduplication rule. Added focused EAS and P2C identity regression tests, plus Georgia source-validation documentation.
- **Postiz `/auth` 500** — Mastra `mastra_ai_spans` hit Postgres 1600-column limit after every deploy force-recreated `:latest` (postiz-app#1473). Dropped bloated telemetry tables; backend listening on `:3000` again (`/auth` 200, `/api/mcp` 401 without key).
- Deploy no longer `pull` + `--force-recreate` Postiz on every CRM push. Recreate only when `docker-compose.yml` / `social/` change; otherwise repair if `:3000` is down (`scripts/repair_postiz_mastra.sh`).
- Paperwork nginx in repo now matches live: dashboard `:8088` host-aware PIN portal (was a stale `:5310` origin that `setup_nginx_vhosts.sh` would have overwritten).
- DocuSeal healthcheck uses Ruby TCP (image has no `bash`).
- Paperwork portal routes accept HEAD so probes are not 404.
- SwipeSimple link unit test no longer requires a live `MONGODB_URI`.

## [2.20.0] — 2026-08-12 (official host inventory + edit on VPS)

### Added
- Canonical hostname registry `config/subdomains.py` + `docs/SUBDOMAINS.md`
- OpenCut on the VPS: `opencut/Dockerfile` + compose profile `edit` (postgres / redis / web)
- `nginx/edit.shamrockbailbonds.biz.conf` → `127.0.0.1:5320` (full HTTPS, Docker, not laptop Tailscale)
- `setup_nginx_vhosts.sh` now replaces TLS vhosts when the source already has cert paths (unblocks Tailscale → Docker origin cutover)
- OpenCut Docker build patches archived `opencut-classic` (`isShortcutKey` + TS ignore) so `next build` succeeds
- OpenCut runtime now supplies Marble + Freesound env strings so `/editor/*` does not 500 on Zod validation
- `nginx/` holds every VPS vhost (`leads` `sign` `paperwork` `social` `edit` `trape` `bb`)
- `scripts/check_subdomains.py` · `scripts/setup_nginx_vhosts.sh` · `tests/test_subdomains.py`

---

## [2.19.1] — 2026-08-04 (Mem0 long-term memory for Shannon)

### Added
- `dashboard/services/mem0_service.py` — Mem0 REST integration (httpx), GAS-compatible
  - Env: **`MEMO_API_KEY`** (same as GAS Script Property) or alias `MEM0_API_KEY`
  - `user_id` = last 10 phone digits (shares memories with voice Shannon / ElevenLabs)
  - Fail-open when key missing or API errors
- Agent Brain: search Mem0 before auto-reply; inject KNOWN FACTS; store exchange after reply
- `GET /api/agent-brain/memory/status` · suggest/summary prompts enriched with Mem0 facts
- Tests: `tests/test_mem0_service.py`

### Ops
- Copy `MEMO_API_KEY` from portal GAS Script Properties into leads production env (not in repo)

---

## [2.19.0] — 2026-08-04 (NC waves 4–7 · CT harden · fleet 269 · data hygiene)

### Added
- **NC waves 4–7** → **47** NC scrapers: Pitt, DCN cluster (Moore/Lee/Halifax/Richmond/Carteret), Craven, Randolph, Catawba, Caldwell PDF, Chatham/Stanly OCV, Orange PDF
- Shared bases: `scrapers/dcn_base.py` (DevExpress), `scrapers/ocv_inmates_base.py` (OCV S3 inmates.json)
- Superadmin **Data Hygiene** API + UI (`/api/admin/hygiene/*`) — purge test junk, repair mismatches
- Mongo M0 oldest-first retention + upsert validation (`last_seen` / `scraped_at`)

### Hardened
- **CT Statewide dockets** + **CT DOC** (`curl_cffi`, list-first DOC A–Z, record caps)
- Multi-State Ops / Scraper Health / stats **registry-first** live KPIs (all 10 states)

### Docs
- Authoritative scale **269** (GA74 · FL67 · SC46 · NC47 · TX15 · TN9 · LA4 · AL3 · CT2 · MS2) in `STATUS.md`, `AGENTS.md`, `ROADMAP.md`, `README.md`, registries, `MULTI_STATE_SCRAPER_ROADMAP.md`

### Scale
- `REGISTERED_COUNTIES` → **269** (was 256 at 2.18.0)

---

## [2.18.0] — 2026-07-26 (Wave-3 NC/TN/TX scrapers → 256)

### Added — 9 county scrapers (registered + scheduled)
| County | State | Platform | Smoke (one-shot) |
|--------|-------|----------|-----------------:|
| **Johnston** | NC | ColdFusion jailsearch | ~296 |
| **Buncombe** | NC | Police-to-Citizen SPA | browser/SPA (strict parse) |
| **Onslow** | NC | P2C + FingerprintJS | 0 when portal dead/sinkhole (fail closed) |
| **Montgomery** | TN | MCSO JSON inmates | ~600 |
| **Sumner** | TN | MyOCV `SumnerInmates.json` | ~702 (charges+bond) |
| **Williamson** | TN | JailTracker | CAPTCHA/browser on VPS |
| **Cameron** | TX | CCSO HTML roster | ~963 |
| **Galveston** | TX | P2C jqGrid `jqHandler.ashx?op=s` `t=ii` | ~1155 |
| **Brazoria** | TX | Tyler Odyssey JailAccess `*/*` wildcard | ~198 |

- `main.py` registers `scraper_nc_*` / `scraper_tn_*` / `scraper_tx_*` jobs
- `REGISTERED_COUNTIES` labels for Scraper Health / Multi-State Ops (**256** total)
- Registry docs: NC / TN / TX · regression test wave-3 set

### Fixed
- **Galveston**: was thin P2C HTML wrapper (0 rows); now paginated jqGrid JSON POST
- **Sumner**: HTML list was only ~10/page; now full OCV S3 JSON with charge/bond parse
- **Brazoria**: Odyssey requires first+last; `*/*` wildcard + parse name from sibling cells

---

## [2.17.1] — 2026-07-26 (FL Wave-2 counties on registry + frontend)

### Fixed — Finished FL scrapers missing from dashboard fleet UI
Sixteen Wave-2 FL counties were **scheduled in `main.py`** but absent from
`REGISTERED_COUNTIES`, so Scraper Health / Multi-State Ops / dropdowns never
listed them (ops could not Run / Pause / see health).

**Added to registry (now FL 67/67 · total 247):** Baker, Bradford, Calhoun,
Franklin, Gilchrist, Gulf, Hamilton, Holmes, Jefferson, Lafayette, Levy,
Liberty, Madison, Union, Wakulla, Washington.

Also:
- Normalize `St. Johns` / `St. Lucie` slugs to `st_johns` / `st_lucie` (strip
  periods) in `scraper_id`, trigger keys, and `_resolve_job_id` so Run-Now
  matches the scheduler job id.
- Regression test: `tests/test_registered_counties_coverage.py`.

---

## [2.17.0] — 2026-07-26 (iMessage replies + Family Tree + scraper Run JSON)

### Fixed — iMessage replies not showing on desktop
Root causes (verified against live BlueBubbles **Server v1.9.9**):
- Inbox poller used **`GET /api/v1/message`** which returns **404** on modern BB Server.
  Official path is **`POST /api/v1/message/query`** (same as BB client / community guides).
- Webhook handler expected nested `data.message`; BB posts the message **as** `data`.
- Background poller only ran when auto-reply `enabled=true` (default **false**), so inbound
  was never ingested when AI auto-reply was off.
- Optional `BB_WEBHOOK_SECRET` rejected unsigned BB webhooks (BB does not send HMAC by default).

Fixes:
- `bb_private_api.py` — `get_messages` / `get_chats` / `get_chat_messages` use POST query APIs.
- `bb_webhook_receiver.py` — `_extract_bb_message()`, soft HMAC, better handle/phone parsing.
- `imessage_automation.py` — always poll inbound; thread endpoint **hydrates from BB** into Mongo.
- `sl-imessage.js` — debounced SSE refresh; cache bump `?v=7`.

**Versioning note:** BlueBubbles **App** `v2.0.0+89` is the phone/desktop *client* rewrite —
it is **not** a server upgrade. Mac server latest remains **1.9.9** (already deployed). See
`docs/BLUEBUBBLES_VERSIONING.md`.

### Added — Family / Relationship Network (1st–2nd degree)
- Models + service + API: `family_tree.py`, `family_tree_service.py`, `family_tree_api.py`
  (`GET /api/family-tree/graph/{name}`, `POST/DELETE /relationship`, list relationships).
- Frontend: Intelligence → **Family Tree** tab (`sl-family-tree.js`); Active Bonds edit drawer
  panel + open-tree actions. Soft-delete, session dismiss, bond co-indemnitor discovery.

### Fixed — Scraper Health / Multi-State **Run** button JSON errors
- `/api/scraper/run-now` and `run-all` always return JSON on failure (no plain-text 500).
- County+state matching for bare names (`Lee` + `FL` → `Lee (FL)`).
- Global unhandled exception handler returns JSON for API routes.
- `sl-health.js` / `sl-multi-state.js` safe JSON parse; pass `state` on Run.

### Fixed / improved — OSINT + collateral + health tab wiring
- OSINT: optional subject ID (ad-hoc scans), always-visible phone/email, PDF export per scan row.
- `collateral_api.py` — FastAPI `Request` typing so the router imports (was silent-missing).
- Scraper Health tab now loads `SLHealth` fleet table + Service Control.

---

## [2.16.1] — 2026-07-20 (SSE publisher coverage — dead listeners wired)

### Fixed — Frontend SSE listeners with no backend publisher
The dashboard subscribed to several named events that no backend code ever published,
leaving those real-time flows dark even after the 2.16.0 named-dispatch fix:
- **`message_received` + `new_reply`** — published from `bb_webhook_receiver.py` on inbound
  BlueBubbles messages (matched replies fire both; unmatched inbound fires
  `message_received` for triage). Live iMessage inbox refresh + prospect badge now work.
- **`bond_written`** — published from `bonds.py` on `/bonds/record` (retro entry) and on
  successful `/write-bond` GAS forwarding.
- **`new_intake`** — published from the Wix intake webhook after `_normalize_intake`.
- **`rearrest_detected`** — published from `rearrest_detector.py` per new alert.
- **`court_reminder_sent`** — published from `court_reminder_service.py` per delivered
  reminder.
- **`bond_fta_detected`** — published from `fta_alert_service.py` per new FTA.
- `tests/test_sse_publisher_coverage.py` — contract test failing the build if any frontend
  listener ever loses its backend publisher again (documented exemptions for the scraper
  webhook relay and the `payment_confirmed` legacy alias).

### Fixed — PII + misc
- Court reminder logs and the bond-recorded log line now mask phone numbers (last-4).
- `bond_lifecycle.py` deprecated naive `datetime.utcnow()` → timezone-aware UTC.
- `STATUS.md` last-verified refreshed.

---

## [2.16.0] — 2026-07-20 (Ecosystem data-flow hardening)

### Fixed — Critical scraper crashes (38 GA/SC counties)
- `scrapers/interopweb_base.py` + `scrapers/smartcop_base.py` built `ArrestRecord` with
  lowercase kwargs the dataclass rejects (`TypeError`), silently zeroing out ~35 InteropWeb
  GA counties and 3 SmartCOP counties every run. Now use canonical PascalCase schema with
  `Full_Name`, `Status`, `Detail_URL`, and stringified `Bond_Amount`.
- `BaseScraper.__init__` now provides `self.logger` — both bases called `self.logger.*`
  which raised `AttributeError` even inside their own error handlers.
- `tests/test_platform_base_parsers.py` — raw-HTML payload regression tests for both bases.

### Fixed — Real-time SSE layer was silently dead
- `dashboard/routers/events.py` published every domain event as generic SSE `event: message`,
  but `sl-core.js` subscribes with named listeners (`addEventListener('new_arrest')` …) which
  only fire on a matching event name — so no toasts/badges/activity ever reached the UI.
  Events now dispatch under their domain name; keep-alive renamed `ping` → `heartbeat` to
  match the frontend listener. `tests/test_sse_named_events.py` locks the contract.

### Changed — Uniform scraper → dashboard event flow
- Promoted Lee County's private webhook broadcast into `BaseScraper.run()`:
  every scraper now emits `new_arrest` / `hot_lead` SSE events for genuinely NEW records
  (writer-confirmed upserts, capped per run) and `scraper_error` on failure, with `state` +
  `county_label` for multi-state display. Removed the Lee-only `_broadcast_new_arrests`.
- `MongoWriter.write_records` returns `new_record_indexes` so only fresh bookings broadcast.

### Changed — State-aware idempotency (Idempotent Writes axiom)
- Arrests natural key is now `(state, county, booking_number)` — the legacy
  `(county, booking_number)` unique index let Lee (GA)/Lee (SC) records collide with
  Lee (FL). Legacy index dropped automatically; docs missing `state` backfilled to FL.
- `scraper_status` unique index now `(state, county)` — the bare-county unique index threw
  `DuplicateKeyError` for GA/SC counties sharing an FL name, dropping their status writes.
- State propagation fixed in `eas_base` (30 GA counties defaulted to FL), `jailtracker_base`
  (hardcoded FL for SC Chester/Greenwood + GA Dawson/Pickens), `socrata_base`,
  `xml_feed_base`, and `counties_ga/eas_batch_runner` (`state="GA"`).
- `tests/test_state_aware_dedup.py` locks filter shape, FL default, and upsert reporting.

### Fixed — Router registration observability
- `dashboard/routers/__init__.py` records import failures in `FAILED_ROUTER_MODULES` and
  logs CRITICAL (a failed module silently removes its whole endpoint group from the API).
- `/health` returns 503 `degraded` with the failed-module map — previously `geo`, `tracking`,
  and `wix_cms` could vanish without any signal (missing `aiohttp`/`python-multipart` deps).
- Missing `JSONResponse` imports fixed in `geo.py`, `tracking.py`, `wix_cms.py` (error paths
  crashed with `NameError` instead of returning their intended 4xx/5xx JSON).

### Fixed — SOC II / PII logging + misc
- New `mask_phone()` helper (`dashboard/routers/helpers.py`); full phone numbers no longer
  written to logs in `bonds.py` (release flow), `paperwork.py` (packet delivery), and
  `bb_client.py` (9 log sites now last-4-digits only).
- `accounting.py` SwipeSimple→ledger mirror used a fragile `locals()` probe for
  `booking_number` that could bind an unrelated variable — now uses the txn document value.
- Removed duplicate `publish_event` import in `webhooks.py` (F811).

## [2.15.0] — 2026-07-16 (APE + Lead Explorer / scraper-status harmony)

### Added — Autonomous Proxy Engine (APE)
- `scrapers/proxy_engine.py` — Warren residential, S5W2C mobile, Stormsia free fallback with health gating.
- `scrapers/proxy_validator.py` — proxy validation helpers.
- `deployment/warren_hub_deploy.sh` / `warren_node_enroll.sh` — real Warren v0.4.x tarball assets + correct CLI.
- `docs/APE_INTEGRATION_GUIDE.md`, `docs/SELF_HOSTED_PROXY_ARCHITECTURE.md` — production VPS `178.156.179.237`.
- `.env.example` APE vars (`WARREN_*`, `S5W2C_*`, `STORMSIA_*`).
- `tests/test_ape_integration.py` — 29 unit tests (Warren/S5W2C/Stormsia/BaseScraper).

### Fixed — Dashboard reflects live scrapers
- `/api/status` + `/api/scraper-health` join bare Mongo keys (`Lee`) to `County (ST)` labels without cross-state collisions.
- Multi-State Ops KPIs use the same multi-key status index (FL/GA/SC/NC/TN/TX/LA).
- Lead Explorer defaults to `scraped_at` desc, shows scrape freshness, auto-refreshes, county options as labels.
- `MongoWriter.upsert_scraper_status` stores `state` + `county_label` + `scraper_id` (BaseScraper passes them).

## [2.14.0] — 2026-07-15 (TN / TX / LA wave-1 scrapers)

### Added — Scrapers
- **TN wave-1:** Davidson (DCSO live ~2.8k), Knox (letter roster), Shelby (IML TLS-hardened stub).
- **TX wave-1:** Bexar (Central Magistrate 24h), Dallas (official jaillookup grid), Harris (browser A–Z).
- **LA wave-1:** Orleans (OPSO partial), Lafayette (365Labs captcha-aware scaffold).
- Registries: `docs/TN_COUNTY_REGISTRY.md`, `docs/TX_COUNTY_REGISTRY.md`, `docs/LA_COUNTY_REGISTRY.md`.

### Changed — Core / Dashboard
- `main.py` registers TN/TX/LA scrapers (`tn_*`, `tx_*`, `la_*` CLI keys).
- `REGISTERED_COUNTIES` → **206** (was 198); Multi-State Ops `ACTIVE_STATES` includes TN/TX/LA.
- Lead Explorer + Bond Intel + Multi-State UI surface TN/TX/LA filters and colors.

## [2.13.0] — 2026-07-14 (Docs truth sync + multi-state harmony)

### Changed — Documentation
- **Authoritative scale:** 198 registered scrapers (51 FL, 74 GA, 46 SC, 27 NC) in `STATUS.md`, `AGENTS.md`, `ROADMAP.md`, `README.md`, `DATA_MODEL.md`, `GEMINI.md`.
- Roadmap phases **1d–1g** (SC full register, NC wave-1, Multi-State Ops, remaining Palmetto scaffolds).
- Multi-state identity rules documented for agents (`scraper_<st>_<county>`, CLI prefixes).

## [2.12.0] — 2026-07-14 (Dashboard multi-state + NC visibility)

### Added — Dashboard
- **North Carolina** on Multi-State Ops (KPI cards, registry filter, charts) and Bond Intelligence.
- Lead Explorer **state** filter + column (FL/GA/SC/NC).
- `REGISTERED_COUNTIES` expanded to full SC (46) + NC wave-1 (27); `parse_registered_county` / trigger-key helpers.
- `/api/ops` registry scans `counties_nc/`; state summary always surfaces FL/GA/SC/NC.

### Changed — Core
- Scraper run-now/run-all emit state-prefixed trigger keys (`nc_mecklenburg`, `sc_lee`).
- Scheduler `_resolve_job_id` accepts `County (ST)` labels.
- Leads query parses `Mecklenburg (NC)` → `{county, state}`.

## [2.11.0] — 2026-07-14 (SC full coverage + NC wave-1 + state-prefixed IDs)

### Added — Scrapers
- **SC:** remaining counties → **46/46** modules; hardened Charleston, Greenville, Richland, Florence, York, Horry, etc.
- **NC wave-1:** 27 counties (Southern SW, Zuercher, P2C, Mecklenburg/Durham scaffolds).
- Platform bases: `kologik_base.py`, `new_world_base.py`, `odyssey_base.py`; Southern SW / Zuercher / P2C hardening.
- Scaffold packages: `counties_tn/`, `counties_tx/`, `counties_ct/`, `counties_la/`, `counties_ms/`.
- Docs: `MULTI_STATE_SCRAPER_ROADMAP.md`, `SC_COUNTY_REGISTRY.md`, `NC_COUNTY_REGISTRY.md`, `NC_RECON_RESULTS.md`.

### Changed — Core
- `BaseScraper.state` + non-FL `scraper_id` form `scraper_<st>_<county>` (FL legacy preserved).
- `main.py` state-aliased imports (GA_/SC_/NC_); scheduler multi-state `run_now` resolution.

### Added — Dashboard (prior same week)
- Multi-State Ops tab + `/api/ops/*`; Bond Intelligence tab; Beaufort XML live path; defendants `booking_number` TypeError fix.

## [2.10.0] — 2026-07-11 (South Carolina Expansion Phase 1e)
### Added — Scrapers
- **SC Custom HTML Stubs**: Built and registered 15 custom HTML scraper stubs for the remaining confirmed SC portals:
  - Tier 1 (High Value): Greenville, Charleston, Richland, Horry, York, Beaufort, Aiken
  - Tier 2: Florence, Darlington, Marion, Newberry, Berkeley, Bamberg, Hampton, Jasper
### Changed — Core
- **Scheduler**: Registered 15 new SC scrapers in `main.py` (now tracking 31 SC counties total).
### Changed — Documentation
- **Scale Update**: System now tracks 191 total active scrapers (52 FL, 108 GA, 31 SC).

## [2.9.0] — 2026-07-11 (South Carolina Expansion Phase 1d)
### Added — Scrapers
- **South Carolina Recon**: Executed parallel recon across all 46 SC counties.
- **Base Class Reuse (SC)**: Built 16 new South Carolina scrapers leveraging existing base classes:
  - Zuercher (8): Anderson, Cherokee, Colleton, Kershaw, Laurens, Oconee, Pickens, Union
  - JailTracker (2): Chester, Greenwood
  - Southern Software (2): Chesterfield, Dorchester
  - P2C (2): Lee, Lexington
  - New World (1): Lancaster
  - SmartCOP (1): Sumter
### Changed — Core
- **Scheduler**: Registered 16 new SC scrapers in `main.py`.
### Changed — Documentation
- **Scale Update**: System now tracks 176 total active scrapers (52 FL, 108 GA, 16 SC).
- **Registry**: Added `SC_RECON_RESULTS.md` documenting the portal status of all 46 counties.

## [2.8.0] — 2026-07-11 (Georgia Expansion Phase 1c - Track C)
### Added — Scrapers
- **Track C (Deep Recon)**: Built 60 new Georgia county scrapers based on parallel recon results.
- **InteropWeb Base Class**: Created `interopweb_base.py` to handle the standard HTML table format used by 35 rural Georgia counties.
- **SmartCOP Base Class**: Created `smartcop_base.py` to handle ASP.NET ViewState POSTs for Putnam, Sumter, and Taylor counties.
- **EAS Batch Expansion**: Added McDuffie, Meriwether, and Warren to `eas_batch_runner.py` (now 30 counties).
- **Base Class Reuse**: Added 10 Tyler/New World counties, 3 Zuercher counties, 4 P2C counties, and 2 JailTracker counties.
### Changed — Core
- **Scheduler**: Registered 57 new standalone scrapers in `main.py`.
### Changed — Documentation
- **Scale Update**: System now tracks 160 total active scrapers (52 FL, 108 GA).
- **Registry**: Updated `GEORGIA_COUNTY_REGISTRY.md` and added `GEORGIA_RECON_TRACK_C.md` with full discovery results.

## [2.7.0] — 2026-07-11 (Georgia Expansion Phase 1c - Track A & B)
### Added — Scrapers
- **Track A (Base Class Reuse)**: Added 6 new Georgia counties using existing base classes:
  - Houston, Floyd, Catoosa (Zuercher)
  - Decatur, Lee, Oglethorpe (Southern Software)
- **Track B (Custom HTML)**: Added 4 high-value Georgia county custom parsers:
  - Gwinnett (SmartWebClient ASP.NET ViewState POST)
  - Richmond (ColdFusion POST)
  - Glynn (Custom HTML)
  - Cobb (Stubbed pending backend recovery)
### Changed — Core
- **Scheduler**: Added `run_eas_batch` as a standalone APScheduler job for the 27 EAS counties. Registered all 10 new Track A & B scrapers with tier-appropriate intervals (30-120 mins).
### Changed — Documentation
- **Registry**: Updated `GEORGIA_COUNTY_REGISTRY.md` to mark 10 new counties as Active.
- **Core Docs**: Updated `README.md`, `ROADMAP.md`, `STATUS.md`, `AGENTS.md`, `DATA_MODEL.md`, and `GEMINI.md` to reflect the new scale: 100 total scrapers (52 FL, 48 GA).
## [2.7.0] — 2026-07-08 (Super CRM hub + security hygiene + docs truth)

### Added
- `/api/crm/health`, `/api/crm/overview`, `/api/crm/pipeline`, `/api/crm/search` — Super CRM hub.
- Omnibar uses CRM search (fallback to match-manager).
- `scripts/check_ecosystem_secrets.py` — cross-repo env presence + shared key fingerprints.
- `docs/SUPER_CRM.md`, `docs/ECOSYSTEM.md`, `STATUS.md`.
- Expanded Mongo indexes for active_bonds, intake, indemnitors, tasks, payments, matches.

### Security
- Scrubbed hardcoded Mongo URIs and BlueBubbles passwords from one-off scripts.
- Removed tracked session cookie dumps; tightened `.gitignore`.
- Wix intake + scraper event webhooks **fail closed** if secrets missing.
- Production can require `DASHBOARD_PIN` / `SECRET_KEY` (no open dashboard by default when configured).

### Docs
- Clarified product boundary: this repo is bond Auto-CRM; school LMS is separate.
- Phase 18 roadmap: true phone→autopilot with explicit human gates (next).

### Ops still required
- VPS deploy of this release; BlueBubbles office reliability; rotate any previously leaked credentials.

---

## [2.6.0] — 2026-05-27 (Dashboard Nesting Fix + Surety Normalization + Doc Refresh)

### Fixed — Dashboard

- **Tab nesting bug (Command Center)** — Removed 4 orphan HTML lines (duplicate "No repeat offender alerts" block) that prematurely closed `#tabCommand`, causing the Bond-Ready Queue, In-Custody by County, and Activity Feed to render on every tab instead of just Command Center.
- **Tab nesting bug (Analytics)** — Removed extra `</div>` in the Analytics tab's County Performance Table panel that prematurely closed `.container`, causing the ApexCharts row (Sparkline, Treemap, Risk Heatmap) to float outside the tab. This also threw off div depth for all 11 subsequent tabs (Intelligence through Enrichment), rendering them at depth 0 instead of 1.
- **Surety normalization** — Fixed `$switch` + `$regexMatch` aggregation in `analytics.py` and `reports.py` to map all "OSI" and "Palmetto" variants (case-insensitive) to canonical surety names. Prevents "osi" and "OSI" from appearing as separate sureties in analytics.

### Fixed — Scrapers

- **Sarasota County scraper** — Fixed `scrape()` to properly navigate to the "Current Inmates" tab, handle AJAX-loaded table data, parse the detail-page link structure, and extract all 39 ArrestRecord fields.

### Changed — Documentation

- **`README.md`** — Complete rewrite with accurate metrics: 52 scrapers, 66 API modules, 45 services, 45 JS modules, 9 CSS files, 21 dashboard tabs, 36 agent skills. Updated architecture diagram, project structure, codebase metrics table, and all tab descriptions.
- **`GEMINI.md`** — Updated all codebase metrics to current counts.
- **`ROADMAP.md`** — Updated scraper count (51→52), corrected remaining counties (17→15).
- **`CHANGELOG.md`** — Added v2.6.0 entry (this).

### Verified

- HTML nesting: all 21 `tab-content` divs open at depth 1, final depth 0, zero negative-depth lines.
- Zero duplicate HTML IDs across 718 unique IDs.
- All 42 local script references resolve to existing files.
- All 66 router files + 45 service files compile cleanly (zero syntax errors).
- All 9 CSS files have balanced braces.

### Metrics Standardized

All documentation now consistently references: 52 scrapers · 66 API modules · 45 services · 45 JS modules · 21 dashboard tabs · 36 agent skills · 16 MongoDB collections

---

## [2.5.0] — 2026-05-15 (Documentation Suite Standardization)

### Added — Documentation

- **`CONTRIBUTING.md`** — Development workflow, code conventions (Python/JS/CSS), commit format, PR process, deployment guide
- **`docs/README.md`** — Documentation index mapping all 30+ docs to purpose and audience
- **`docs/ARCHITECTURE.md`** — System architecture: Docker services, data flow diagrams, external integrations, security model, codebase metrics
- **`docs/API_REFERENCE.md`** — REST API reference covering 200+ endpoints across 61 API modules
- **`docs/DEPLOYMENT.md`** — Production operations: 3 deploy methods, Docker ops, Nginx, health checks, troubleshooting, backup/recovery
- **Agent docs (6 new):** `analyst-agent.md`, `watchdog-agent.md`, `discharge-monitor-agent.md`, `outreach-agent.md`, `court-clerk-agent.md`, `shannon-agent.md`, `rearrest-detector-agent.md`, `contact-finder-agent.md`, `data-retention-agent.md` — all 15 agents now have dedicated documentation

### Changed — Documentation

- **`README.md`** — Complete rewrite: accurate metrics (51 scrapers, 61 API modules, 36 services, 42 JS modules, 15 tabs), updated architecture diagram with Traccar GPS, comprehensive project structure tree
- **`SECURITY.md`** — Complete rewrite: secrets management, PII protection, authentication, network security, audit trails, scraping ethics, data retention, incident response
- **`AGENTS.md`** — Updated metrics (49→61 API, 21→36 services, 32→42 JS), restored architecture diagram, added Traccar
- **`GEMINI.md`** — Updated all metrics, added Traccar Docker service row
- **`ROADMAP.md`** — Updated scraper count (50→51), updated timestamp
- **`DATA_MODEL.md`** — Updated timestamp

### Archived

- Moved stale root-level docs to `docs/archive/2026-05/`: `Antigravity_Handoff_May06.md`, `BlueBubblesApp_Recommendations.md`, `DEPLOY_COMMANDS.md`, `DEPLOY_NOTES.md`

### Metrics Standardized

All documentation now consistently references: 51 scrapers · 61 API modules · 36 services · 42 JS modules · 15 dashboard tabs · 34 agent skills · 16 MongoDB collections

---

## [2.4.0] — 2026-05-08 (Documentation Overhaul + POA Modal Fix)

### Fixed

- **POA Inventory Modal** (`styles.css`) — Fixed CSS specificity conflict where `.inv-overlay:not(.active)` forced `display: none !important`, but JS uses `.show` class. Changed selector to `:not(.show)`. The "Click to manage POA inventory" banner now correctly opens the modal.
- **`.env.example`** — Corrected `BLUEBUBBLES_URL_0178` to actual ngrok permanent tunnel URL (`pseudospherical-etta-untactually.ngrok-free.dev`). Removed incorrect Cloudflare Tunnel references.

### Added — Frontend (via Manus commit `9881188`)

- **Destructive drop confirmation** (`sl-active-bonds.js`) — FORFEITED / SURRENDERED Kanban drops now show a confirmation modal before the API call. Optimistic update reverts on cancel or API failure.
- **Kanban CSS animations** (`sl-overhaul.css`) — Card enter animation with 0.04s per-child stagger, dragging card rotates -1deg, drop zone pulses, alert cards pulse left border, column count badge pops on update.
- **Mobile Kanban** (`sl-overhaul.css`) — Scroll-snap (85vw per column, touch-friendly).
- **Post-save Kanban re-render** (`sl-record-bond.js`) — `SLKanban.render()` called after successful bond save.

### Changed — Documentation

Comprehensive audit and rewrite of all 7 coordination documents to reflect actual codebase state:

- **`GEMINI.md`** — Updated all counts (50 scrapers, 49 API modules, 32 frontend modules, 34 skills, ~25,700 frontend LOC), corrected BlueBubbles to ngrok tunnel.
- **`AGENTS.md`** — Added 3 new agents (Shannon, Re-Arrest Detector, Data Retention), updated all statuses to Live, corrected architecture diagram (Quart not Flask), expanded env vars table.
- **`ROADMAP.md`** — Added Phases 13–15 (Kanban, Court Automation, Dashboard Overhaul), updated all phase descriptions with current file references.
- **`DATA_MODEL.md`** — Complete rewrite with 16 MongoDB collections, full schema definitions, key indexes, and data flow rules.
- **`BRAND.md`** — Updated agent table (all 14 agents Live), added public URL + ngrok tunnel to identity table, corrected frontend LOC.
- **`README.md`** — Major rewrite: 15 tabs (was 10), 32 JS modules (was 11), ~25,700 frontend LOC (was 17,600), 49 API modules (was 30+), 34 skills (was 16), 15 phases all complete, expanded project structure tree.
- **`CHANGELOG.md`** — Added v2.4.0 entry (this).

---

## [2.3.0] — 2026-05-08 (Kanban Board + POA Inline Edit + Status Audit Trail)

### Added — Frontend

- **Bond Kanban Board** (`sl-active-bonds.js` → `SLKanban` IIFE module) — full drag-and-drop view with 6 status columns (Active, Monitoring, Alert, Exonerated, Surrendered, Forfeited). Drag a card to change status; touch-device fallback via tap-and-hold. Toggle between Table and Kanban via the new `☰ Table / ⬛ Kanban` button group in the Active Bonds toolbar.
- **POA Inline Edit** — new `POA` column in the table view shows the current POA number with a `⇄` swap button. Kanban cards also display the POA badge with a swap button.
- **POA Quick-Swap Modal** (`SLKanban.openPoaSwap`) — fetches available POA inventory for the bond's surety, displays a scrollable list of available POAs, and calls `PATCH /api/poa/reassign` on confirm.
- **Status History Modal** (`SLKanban.loadStatusHistory`) — fetches `GET /api/active-bonds/<booking>/status-history` and renders a timeline of all status transitions with timestamp, actor, and optional note.
- **Reinstated status** — added to the status dropdown in the table row and as a Kanban column.
- **View toggle buttons** (`☰ Table` / `⬛ Kanban`) added to the Active Bonds toolbar.
- **Status History button** (`📋 History`) added to each table row's action group.
- **Kanban CSS** appended to `sl-overhaul.css` — columns, cards, drag-over indicators, POA badge, score pills, risk badges, touch-drag fallback, and responsive scroll.

### Added — Backend (`app.py`)

- **`PATCH /api/active-bonds/<booking>/status`** — now appends to `status_history` array (timestamp, old status, new status, actor, note), auto-releases POA inventory on `exonerated`/`surrendered`/`forfeited`, and accepts optional `note` and `actor` fields.
- **`GET /api/active-bonds/<booking>/status-history`** — new endpoint returning the full `status_history` array for a bond.
- **`PATCH /api/poa/reassign`** — enhanced to also clear `poa_number` on the old bond when `old_booking_number` is provided.

### Fixed

- Table `colspan` updated from 13 to 14 to account for the new POA column.
- `SLKanban.setView()` wired to the view toggle buttons for explicit table/kanban switching.
- `SLKanban` public API now exports `setView` in addition to `render`, `toggle`, `openPoaSwap`, `_confirmPoaSwap`, `loadStatusHistory`, and `init`.

---

## [2.2.0] — 2026-05-08 (BlueBubbles Tunnel Fix)

### Fixed

- **ngrok tunnel** — corrected port from 1880 (Node-RED) to 1234 (BlueBubbles). Configured permanent ngrok static domain (`pseudospherical-etta-untactually.ngrok-free.dev`). iMessage tab now shows Online.
- **`docker-compose.yml`** — added `dns: [8.8.8.8, 1.1.1.1]` to both services to ensure external DNS resolution.
- **`TUNNEL_FIX.md`** — updated to document the ngrok permanent domain setup.
- **`.env.example`** — updated `BLUEBUBBLES_URL_0178` to use the permanent ngrok tunnel domain.

---

## [2.1.0] — 2026-05-01 (Antigravity Tier 1-3 + Library Upgrade Sprint)

### Added — Backend

- **`dashboard/api/discharge_monitor.py`** — Gmail OAuth2 discharge email parser. Scans inbox for court-issued exoneration notices, matches to active bonds by booking number, queues for discharge. Returns `501` stub when Gmail credentials are not configured. See `docs/GMAIL_DISCHARGE_SETUP.md`.
- **`dashboard/api/bonds.py`** — `POST /api/bonds/bulk-exonerate` endpoint. Accepts an array of booking numbers, exonerates all in a single transaction, optionally notifies indemnitors, cancels pending reminders, and releases POA inventory.
- **`dashboard/api/calendar.py`** — `POST /api/calendar/sync-gcal` endpoint. Pushes upcoming court dates to Google Calendar with color-coding, 48h/24h reminders, and full defendant metadata. Returns `501` stub when GCal credentials are not configured. See `docs/GCAL_SYNC_SETUP.md`.
- **`dashboard/api/court_reminders.py`** — `POST /api/court-reminders/auto-scan` endpoint. Scans all active bonds, schedules SMS reminders for court dates within the configured window, skips already-scheduled bonds.
- **`dashboard/services/court_reminder_service.py`** — `auto_scan_and_schedule()` method. Iterates active bonds, calculates days-to-court, schedules Twilio SMS at 7d/3d/1d intervals. Skips bonds already scheduled or with no court date.
- **`scripts/create_indexes.py`** — MongoDB index creation script. Creates compound indexes on `court_date + status`, `booking_number` (unique), `defendant_name`, and `risk_score` across all relevant collections for query performance.

### Added — Frontend

- **`dashboard/sl-active-bonds-ext.js`** — Extended Active Bonds module:
  - Court countdown column with color-coded badges (TODAY/red/orange/yellow/neutral)
  - Column sort on all headers (defendant, county, bond amount, court date, days to court, risk score)
  - CSV export with 14 columns including indemnitor phone and days-to-court
  - Bulk Exonerate modal with select-all, per-bond countdown badges, note field, and notify-indemnitor checkbox
  - Has Indemnitor filter chip (injected into filter bar)
  - Duplicate indemnitor phone detection (alert dialog)
  - Indemnitor cross-link (`openIndemInDefendants`) — navigates to Defendants tab and pre-fills search
- **`dashboard/sl-calendar-ext.js`** — Extended Court Calendar module:
  - Vanilla Calendar Pro mini date-picker sidebar (jump to any date)
  - GCal Sync button → `POST /api/calendar/sync-gcal`
  - Auto-Scan Reminders button → `POST /api/court-reminders/auto-scan`
  - Check Discharge Emails button → `POST /api/discharge/scan`
- **`dashboard/sl-analytics-apex.js`** — ApexCharts advanced analytics (3 new charts):
  - ⚡ Live Revenue Sparkline (30-second auto-refresh, daily average annotation)
  - 🌳 Bond Amount Treemap by county (drill-down: click county → jumps to Calendar tab filtered by county)
  - 🗺️ Risk Score Heatmap by county (4 risk buckets × top 10 counties)
- **`dashboard/sl-lifecycle.js`** — Bond lifecycle timeline panel (slide-in from any tab). Shows full journey: Arrest → Contact → Negotiate → Paperwork → Bond → Court → Discharge.
- **`dashboard/api/lifecycle_timeline.py`** — `GET /api/lifecycle/<booking_number>`. Aggregates all MongoDB collections into a unified chronological event list with stage progression.

### Changed — Frontend

- **`dashboard/sl-defendant-lifecycle.js`** — Fixed iOS Safari touch bug in `openShamrockNotes()`. Added `requestAnimationFrame` + `setTimeout(0)` double-flush before adding `.active` class to prevent touch events being swallowed on first tap.
- **`dashboard/styles.css`** — Added `will-change:opacity`, `isolation:isolate`, `-webkit-transform:translateZ(0)`, `transform:translateZ(0)` to `.slc-modal-overlay` for GPU compositing layer on iOS. Ensures modal opens reliably on touchscreen devices.
- **`dashboard/sl-inventory.js`** — Added `_checkLowStockBanner()`. Shows a fixed-position banner (red for critical ≤2, orange for low ≤5) when any POA tier is running low. Auto-dismisses after 12 seconds. Clicking the banner opens POA Inventory modal.
- **`dashboard/index.html`** — Added CDN links for ApexCharts 3.49.2 and Vanilla Calendar Pro 2.9.10. Added court countdown column header and CSV/Bulk Exonerate toolbar buttons to Active Bonds table. Added ApexCharts row (3 panels) to Analytics tab. Added Bulk Exonerate modal HTML.
- **`dashboard/__init__.py`** — Registered `discharge_monitor_bp` at `/api`.

### Added — Documentation

- **`docs/GMAIL_DISCHARGE_SETUP.md`** — Step-by-step Gmail OAuth2 setup for discharge monitor
- **`docs/GCAL_SYNC_SETUP.md`** — Step-by-step Google Calendar API setup for court date sync
- **`CHANGELOG.md`** — This file
- **`.env.example`** — Updated with all new environment variables

---

## [2.0.0] — 2026-04-27 (Lifecycle Panel + iOS Touch Fixes)

### Added

- `sl-lifecycle.js` — Bond lifecycle timeline panel
- `api/lifecycle_timeline.py` — Lifecycle event aggregation API
- iOS Safari touch fixes across all modal overlays
- Lifecycle button on every defendant card in Active Bonds

---

## [1.x.x] — Prior Releases

See git log for full history of Phase 1 (scraper), Phase 2 (lead scoring), Phase 3 (dashboard MVP), and Phase 4 (bonded case management).

- **Hinds County, MS source-safety repair** — replaced the unverified profile-enriching parser with a registered fail-closed guard after aggregate normal-access validation returned no current listing rows or source IDs. The removed path collected DOB, address, and individual detail pages; the guard emits no records until a listing-only public contract provides complete identity, a source-issued booking/inmate key, and booking date/time. Deterministic no-network safety tests passed locally. Deployment run `31855121732` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Hinds-specific persistence and alert delivery remain unproven.

- **DeSoto County, MS JailTracker source revalidation** — verified the official landing page still requires image-character human verification. The deployed shared JailTracker guard attempts no CAPTCHA solving, proxying, profile retrieval, sensitive-field collection, or synthetic identity construction and emits no records until a listing-only booking-safe contract is separately proven.

- **Forrest County, MS source-safety repair** — replaced the configured official API parser after its endpoint returned HTTP 404 and its implementation used an unsafe `id` fallback without a verified booking timestamp. The registered fail-closed guard emits no records until an official source-issued booking-safe API contract is revalidated. Deterministic no-network safety tests passed locally. Deployment run `31855583732` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Forrest-specific persistence and alert delivery remain unproven.

- **Harrison County, MS source-safety repair** — replaced the opaque configured API parser after its HTTP 200 response exposed no parseable public field contract and its implementation used an unsafe `id` fallback without a verified booking timestamp. The registered fail-closed guard emits no records until an official source-issued booking-safe API contract is revalidated. Deterministic no-network safety tests passed locally. Deployment run `31855845579` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Harrison-specific persistence and alert delivery remain unproven.

- **Jackson County, MS source-safety repair** — retired the prohibited residential-proxy stealth session, CAPTCHA path, profile routes, DOB collection, and synthetic identifiers after direct normal access to the configured official list returned HTTP 403 with access-control markers. The registered fail-closed guard emits no records until an official booking-safe public contract is verified without bypassing controls. Deterministic no-network safety tests passed locally. Deployment run `31856142991` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Jackson-specific persistence and alert delivery remain unproven.

- **Jones County, MS JailTracker source revalidation** — verified the official landing page still requires image-character human verification. The deployed shared JailTracker guard attempts no CAPTCHA solving, proxying, profile retrieval, sensitive-field collection, or synthetic identity construction and emits no records until a listing-only booking-safe contract is separately proven.

- **Lauderdale County, MS JailTracker source revalidation** — verified the official landing page still requires image-character human verification. The deployed shared JailTracker guard attempts no CAPTCHA solving, proxying, profile retrieval, sensitive-field collection, or synthetic identity construction and emits no records until a listing-only booking-safe contract is separately proven.

- **Madison County, MS JailTracker source revalidation** — verified the official landing page still requires image-character human verification. The deployed shared JailTracker guard attempts no CAPTCHA solving, proxying, profile retrieval, sensitive-field collection, or synthetic identity construction and emits no records until a listing-only booking-safe contract is separately proven.

- **Rankin County, MS official-roster repair** — replaced the stale configured API parser, whose domain failed DNS and whose implementation used an unsafe `id` fallback, with a listing-only parser for the official current roster. It requires complete public name, labelled source-issued `ID`, and `Intake` timestamp; it discards age, charges, bond, and profiles. Deterministic tests passed and an aggregate normal-access smoke validated 389 unique booking identities. Deployment run `31857115459` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Rankin-specific persistence and alert delivery remain unproven.

- **DeKalb County, AL source-safety repair** — added a county-level fail-closed guard after the configured Citizen Connect agency `DeKalbCoAL` resolved to the generic directory rather than a DeKalb booking roster. The guard emits no records and does not invoke the shared parser until an official broad roster supplies complete identity, a source-issued booking/inmate identifier, and booking time. Deterministic no-network safety tests passed locally. Deployment run `31857452207` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; DeKalb-specific persistence and alert delivery remain unproven.

- **Houston County, AL source-safety repair** — added a county-level fail-closed guard after direct normal access to configured Citizen Connect agency `HoustonCoAL` returned HTTP 403 and exposed no booking-safe broad roster. The guard emits no records and does not invoke the shared parser until an official broad roster supplies complete identity, a source-issued booking/inmate identifier, and booking time. Deterministic no-network safety tests passed locally. Deployment run `31857743667` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Houston-specific persistence and alert delivery remain unproven.

- **Jackson County, AL source-safety repair** — added a county-level fail-closed guard after configured Citizen Connect agency `JacksonCoAL` resolved to the generic directory rather than a Jackson booking roster. The guard emits no records and does not invoke the shared parser until an official broad roster supplies complete identity, a source-issued booking/inmate identifier, and booking time. Deterministic no-network safety tests passed locally. Deployment run `31857994743` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Jackson-specific persistence and alert delivery remain unproven.

- **Jefferson County, AL source-safety repair** — retired the declared residential-proxy stealth path after direct normal access to the configured official New World portal returned HTTP 403. The registered fail-closed guard emits no records until an official broad roster supplies complete identity, a source-issued booking/inmate identifier, and booking time without bypassing controls. Deterministic no-network safety tests passed locally. Deployment run `31858284205` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Jefferson-specific persistence and alert delivery remain unproven.

- **Shelby County, AL JailTracker source revalidation** — verified the official landing page still requires image-character human verification. The deployed shared JailTracker guard attempts no CAPTCHA solving, proxying, profile retrieval, sensitive-field collection, or synthetic identity construction and emits no records until a listing-only booking-safe contract is separately proven.

- **Baldwin County, AL source-safety repair** — added a county-level fail-closed guard after configured Citizen Connect agency `BaldwinCoAL` resolved to the generic directory rather than a Baldwin booking roster. The guard emits no records and does not invoke the shared parser until an official broad roster supplies complete identity, a source-issued booking/inmate identifier, and booking time. Deterministic no-network safety tests passed locally. Deployment run `31858750433` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Baldwin-specific persistence and alert delivery remain unproven.

- **Cullman County, AL source-safety repair** — added a county-level fail-closed guard after configured Citizen Connect agency `CullmanCoAL` resolved to the generic directory rather than a Cullman booking roster. The guard emits no records and does not invoke the shared parser until an official broad roster supplies complete identity, a source-issued booking/inmate identifier, and booking time. Deterministic no-network safety tests passed locally. Deployment run `31859015946` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Cullman-specific persistence and alert delivery remain unproven.

- **Morgan County, AL source-safety repair** — added a county-level fail-closed guard after configured Citizen Connect agency `MorganCoAL` resolved to the generic directory rather than a Morgan booking roster. The guard emits no records and does not invoke the shared parser until an official broad roster supplies complete identity, a source-issued booking/inmate identifier, and booking time. Deterministic no-network safety tests passed locally. Deployment run `31859289675` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Morgan-specific persistence and alert delivery remain unproven.

- **Talladega County, AL source revalidation** — verified the Sheriff-branded public Citizen Connect Current Confinements listing through normal access. It exposes booking timing but no visible source-issued booking or inmate identifier on broad cards. No profile route was opened; Talladega remains recon-only and unregistered until a booking-safe listing contract is published.

- **Autauga County, AL source revalidation** — confirmed through the Sheriff’s official site and app announcement that the current Jail Roster is provided through the mobile app, while normal public web access exposes no booking-safe broad roster contract. Autauga remains recon-only and unregistered; no app reverse engineering, proxying, or inferred identity work was performed.

- **Limestone County, AL source revalidation** — verified the Sheriff-linked public Zuercher roster through normal access. Its broad table exposes sensitive fields and arrest timing but no visible source-issued booking or inmate identifier. No profile route was opened; Limestone remains recon-only and unregistered until a booking-safe listing contract is published.

- **Sullivan County, TN source revalidation** — verified the Sheriff-hosted OCV public roster through normal access. Broad cards expose booking time but no visible source-issued booking or inmate identifier and include sensitive content. No detail route, app, or denied feed was used; Sullivan remains recon-only and unregistered until a booking-safe listing contract is published.

- **Bradley County, TN source-safety repair** — added a county-level fail-closed guard after configured Citizen Connect agency `BradleyCoTN` resolved to the generic directory rather than a Bradley booking roster. The guard emits no records and does not invoke the shared parser until an official broad roster supplies complete identity, a source-issued booking/inmate identifier, and booking time. Deterministic no-network safety tests passed locally. Deployment run `31860512979` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Bradley-specific persistence and alert delivery remain unproven.

- **Blount County, TN JailTracker source revalidation** — verified the official landing page still requires image-character human verification. The deployed shared JailTracker guard attempts no CAPTCHA solving, proxying, profile retrieval, sensitive-field collection, or synthetic identity construction and emits no records until a listing-only booking-safe contract is separately proven.

- **Sevier County, TN source-safety repair** — added a county-level fail-closed guard after the configured Zuercher hostname failed normal DNS resolution and the currently accessible official ISOMS listing did not expose a verified source-issued booking or inmate identity contract. The guard emits no records until a compliant broad roster is validated. Deterministic no-network safety tests passed locally. Deployment run `31861048094` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Sevier-specific persistence and alert delivery remain unproven.

- **Washington County, TN source-safety repair** — added a county-level fail-closed guard after configured Citizen Connect agency `WashingtonCoTN` resolved to the generic directory rather than a Washington booking roster. The guard emits no records and does not invoke the shared parser until an official broad roster supplies complete identity, a source-issued booking/inmate identifier, and booking time. Deterministic no-network safety tests passed locally. Deployment run `31861360339` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Washington-specific persistence and alert delivery remain unproven.

- **Maury County, TN JailTracker source revalidation** — normal public access to the configured official JailTracker landing page returned service-unavailable. The deployed shared guard attempts no CAPTCHA solving, proxying, profile retrieval, sensitive-field collection, or synthetic identity construction and emits no records until a listing-only booking-safe contract is separately proven.

- **Robertson County, TN source-safety repair** — added a county-level fail-closed guard after configured Citizen Connect agency `RobertsonCoTN` resolved to the generic directory rather than a Robertson booking roster. The guard emits no records and does not invoke the shared parser until an official broad roster supplies complete identity, a source-issued booking/inmate identifier, and booking time. Deterministic no-network safety tests passed locally. Deployment run `31861791340` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Robertson-specific persistence and alert delivery remain unproven.

- **Hamblen County, TN source-safety repair** — added a county-level fail-closed guard after the configured Zuercher hostname failed normal DNS resolution and the currently accessible official ISOMS listing exposed intake timing but no verified source-issued booking or inmate identity contract. The guard emits no records until a compliant broad roster is validated. Deterministic no-network safety tests passed locally. Deployment run `31862118252` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Hamblen-specific persistence and alert delivery remain unproven.

- **Bedford County, TN source-safety repair** — added a county-level fail-closed guard after configured Citizen Connect agency `BedfordCoTN` resolved to the generic directory rather than a Bedford booking roster. The guard emits no records and does not invoke the shared parser until an official broad roster supplies complete identity, a source-issued booking/inmate identifier, and booking time. Deterministic no-network safety tests passed locally. Deployment run `31862395762` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Bedford-specific persistence and alert delivery remain unproven.

- **Coffee County, TN source-safety repair** — added a county-level fail-closed guard after configured Citizen Connect agency `CoffeeCoTN` resolved to the generic directory rather than a Coffee booking roster. The guard emits no records and does not invoke the shared parser until an official broad roster supplies complete identity, a source-issued booking/inmate identifier, and booking time. Deterministic no-network safety tests passed locally. Deployment run `31862690586` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Coffee-specific persistence and alert delivery remain unproven.

- **Lincoln County, TN source-safety repair** — added a county-level fail-closed guard after configured Citizen Connect agency `LincolnCoTN` resolved to the generic directory rather than a Lincoln booking roster. The guard emits no records and does not invoke the shared parser until an official broad roster supplies complete identity, a source-issued booking/inmate identifier, and booking time. Deterministic no-network safety tests passed locally. Deployment run `31862982383` completed successfully and public leads `/health`, sign, school, paperwork, and social `/auth` checks were healthy; Lincoln-specific persistence and alert delivery remain unproven.

- **Giles County, TN source-safety repair** — deployed a county-level fail-closed guard after configured Citizen Connect agency `GilesCoTN` resolved to the generic directory rather than a Giles booking roster. The guard emits no records and does not invoke the shared parser until an official broad roster supplies complete identity, a source-issued booking/inmate identifier, and booking time. Deterministic no-network safety tests passed; deployment run `31885691719` completed successfully and public host checks returned 200. Giles-specific persistence and alert delivery remain unproven.

- **Scraper Health dashboard truthfulness** — deployed a source-contract indicator independent of run health. The dashboard now labels explicitly mapped county jobs as **Verified public**, **Fail closed**, **Unverified**, or **History only**; guarded jobs display zero-output intent and no manual-run action. The `/api/scraper-health` response now carries the non-PII `source_state` field. Deployment run `31886059597` succeeded, followed by asset-cache refresh run `31886146568`.
