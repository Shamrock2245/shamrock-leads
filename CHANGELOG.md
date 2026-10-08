# ShamrockLeads — Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased] — 2026-10-07 (Telegram and Shannon intake tags)

### Added
- `POST /api/intake/submit` keeps `telegram_miniapp` and `shannon_voice` as their own source tags. Older `telegram`, `telegram_mini_app`, `shannon`, and `elevenlabs_voice` values stay as they were. `shannon_voice` uses the same voice match skip as Shannon (the matcher runs from the desk, not inside the call) and the website pay-by-card link. `telegram_miniapp` still uses the Telegram pay link and still runs match-review on submit.

## [Unreleased] — 2026-10-08 (start bond packet key)

### Fixed
- **Start bond packet (`SAAS_MULTI_TENANT` still default off).** The `paperwork_packets` upsert is keyed on `packet_id` (`idx_pkt_packet_id`), with `created_at` only in `$setOnInsert`. A signed or voided packet that shares the bond case is left in place.

## [Unreleased] — 2026-10-08 (start bond packet upsert)

### Fixed
- **Start bond packet (`SAAS_MULTI_TENANT` still default off).** The `paperwork_packets` write is one `update_one` upsert, so a second submission for the same packet updates that row instead of inserting another.

## [Unreleased] — 2026-10-08 (lead fan-out Motor retry)

### Fixed
- **Lead fan-out retry (`SAAS_MULTI_TENANT` still default off).** The cron sweep runs on the Motor database. Tenant lookup and the lead pointer write are awaited, so a due outbox row is written before it is marked delivered. The arrest writer still uses the sync helpers.

## [Unreleased] — 2026-10-08 (lead fan-out retry job)

### Fixed
- **Lead fan-out outbox (`SAAS_MULTI_TENANT` still default off).** `lead_fanout_retry` on the dashboard cron retries due `lead_fanout_outbox` rows with backoff. After five attempts a row is `dead` and is not retried. Dead letters, and an open outbox that is too deep or too old, post to `SLACK_WEBHOOK_ALERTS`. The job does nothing when the flag is off.

## [Unreleased] — 2026-10-07 (start bond packet review)

### Fixed
- **Start bond packet (`SAAS_MULTI_TENANT` still default off).** Send passes the bond's binding fields into `create_submission_for_packet`, uses the chosen surety's published template for the current agency, and refuses a power that does not match preflight. After submission it creates or updates `paperwork_packets` and reads signer URLs from `submitters[].sign_url`. The screen shows the sign and pay links. It still does not text, charge, or mark the power used.

## [Unreleased] — 2026-10-07 (lead subscription review)

### Fixed
- **Lead subscriptions (`SAAS_MULTI_TENANT` still default off).** A tenant lead write that fails after the arrest upsert is queued on `lead_fanout_outbox` (booking pointer only, no defendant name) and retried; the arrest row stays. Exclusive county assignment uses a unique `(state, county)` claim, and a duplicate-key race returns `exclusive_taken`. Subscription saves write an `audit_events` row with the actor, reason, and old and new list. The console keeps a configured `price_cents` on save.

## [Unreleased] — 2026-10-07 (Relay-only Manatee + Charlotte)

### Changed
- **Manatee + Charlotte (FL) are relay-only** (`config/relay_only.py`). The VPS/Hetzner scheduler keeps them registered but gives them no interval job. A dashboard run-now or custody-recheck trigger for either county is marked `relay_only` and is not run on the VPS. The new relay entry point `python main.py --relay-only` runs both once and exits non-zero on any failure; `python main.py <County>` still works. Ops: `docs/ops/REVIZE_RELAY_RUN.md`.
- **Charlotte (FL):** got the same cleanup as Manatee #121. The APE/Warren + office SOCKS resolver (`CHARLOTTE_EGRESS_MODE=auto`, now a config error) and the Patchright stealth launcher / stealth context are removed. Charlotte now runs stock headless Playwright with `--no-proxy-server` and proxy env vars stripped. The exit check uses `trust_env=False`; an unknown exit is refused; egress blocks fail loud with nothing written. `tests/test_charlotte_no_proxy_path.py` proves no proxy or stealth path is reachable.

### Removed
- Dead code with no users left: `cf_browser.launch_cf_browser`, `new_stealth_context`, `wait_past_cloudflare`, `_launch_sync_playwright`, `require_residential_exit`, and `socks_proxy.to_playwright_proxy`, `to_httpx_proxy`, `require_socks_or_raise`. The shared resolver (`resolve_residential_proxy`, `validate_residential_proxy`, `curl_cffi_proxies`) stays for Marion and Hillsborough. `check_exit_ip` stays.
- `docs/COUNTY_REGISTRY.md`: Manatee/Charlotte no longer list the APE/office SOCKS path.

## [Unreleased] — 2026-10-07 (Staff edits survive rescrapes)

### Fixed
- **All counties:** a staff-set bond (update-bond-amount, update-charge-bonds, update-lead-details, admin patch) and staff-edited charge rows (per-charge amounts, case numbers, POAs, added or removed charges) now survive every rescrape. One module, `core/staff_edits.py`, protects each write path that puts source data on an existing arrest: the shared MongoWriter (every county, including the SSW and SmartWEB helpers), the First Appearance watcher, the custody recheck, the Lee jail refresh, refresh-from-source, the bookmarklet merge and the confirmed-booking refresh. Source values that disagree are kept in `scraped_bond_amount` / `scraped_bond_type` / `scraped_charges` / `scraped_charge_details`. A scraped charge that staff have not seen, removed or replaced is still added. A staff-entered $0 is stored in `staff_edits.bond` and hydrates as a known $0. Existing records are read under their `bond_override` / `MANUAL_CHARGE_BONDS` flags; there is no backfill. In the Write Bond modal a blank per-charge amount stays unknown (it used to be saved as $0), and a POA the modal does not send is kept. Two related fixes. First, the custody recheck read every live field as blank, because `ArrestRecord` has no `to_dict()`, so a recheck could wipe status, bond and charges. It now compares against `to_mongo_doc()` and never writes a value the source did not publish. Second, refresh-from-source no longer writes an unparsed bond as $0. Design note: `docs/STAFF_EDITS_SURVIVE_RESCRAPE.md`.

## [Unreleased] — 2026-10-07 (surety entitlement review)

### Fixed
- **Surety checklist (`SAAS_MULTI_TENANT` still default off).** `POST /api/paperwork/generate/{intake_id}`, the paperwork preview, DocuSeal prefill and push, booking hydrate, and the appearance-bond print routes refuse a surety the agency is not enabled for before any carrier PDF is rendered. Changing the checklist writes an `audit_events` row with the actor, reason, and old and new enabled set.

## [Unreleased] — 2026-10-07 (agency billing review)

### Fixed
- **Stripe test-mode billing (`SAAS_MULTI_TENANT` still default off).** Recurring Checkout puts `tenant_id` on `subscription_data[metadata]` so later invoice and subscription events resolve the agency. One Stripe Customer is reused. County, state, usage, and any later recurring plan are added as items on that subscription instead of a second Customer or subscription. An add-on is refused while the agency is past due, suspended, or canceled. A webhook for a different subscription does not change status or MRR, and deleting it does not cancel the agency. `livemode: true` events are rejected unless `STRIPE_ALLOW_LIVEMODE=1`, which stays unset. Comps and billing transitions write an `audit_events` row with the actor, reason, and old and new state. Suspension is enforced in the BlueBubbles client on every outbound send path, including a direct `send_human_like`.

## [Unreleased] — 2026-10-07 (agency onboarding review)

### Fixed
- **Agency onboarding (`SAAS_MULTI_TENANT` still default off).** `tenants.tenant_id` and `tenants.slug` each have a unique index, and a duplicate-key error from a concurrent insert is `slug_taken`. Approve and reject are a single compare-and-set on `status: pending_approval`; a second decision returns `not_pending`. The declared owner stays `owner` when that email is also submitted as a coworker. The platform queue has Approve and Reject controls plus a rejection reason. Each decision writes an `audit_events` row with the actor, reason, and old and new state.

## [Unreleased] — 2026-10-07 (tenant host membership and backfill)

### Fixed
- **Multi-tenant foundation (`SAAS_MULTI_TENANT` still default off).** A customer host `{slug}.app.shamrockbailbonds.biz` is routing only. The request binds that agency only when the signed-in email has an active `tenant_memberships` row for that slug. A signed-in caller on a Shamrock host must be a member of the tenant that host selects. Anonymous webhook and machine calls on Shamrock hosts still bind Shamrock. Flag off does not check membership and does not change filters.
- **Backfill.** `scripts/backfill_tenant_id.py` stamps every tenant-owned collection the app opens, including `family_relationships`, `persons`, `osint_scans`, `docket_events`, and `intake_fanout_outbox`. A connected run also stamps any other present collection that is not on the global or platform allowlist. `tests/test_tenant_scope.py` fails if application code uses a collection that is neither tenant-owned nor allowlisted.
- **Startup.** POA seeding and core index creation run inside a Shamrock tenant context, so a flag-on boot does not call the tenant proxy with no context. Flag off still writes the same unstamped seed.

## [Unreleased] — 2026-10-07 (multi-tenant foundation through start bond packet)

### Added
- **`SAAS_MULTI_TENANT` flag, default off.** `get_collection()` is the tenant chokepoint: tenant-owned collections are scoped by `tenant_id`, and an explicit global allowlist (jail rosters and scraper health) stays shared. A request or job with no tenant fails closed only when the flag is on. Shamrock routes are unchanged while the flag is off. The offline backfill script and the tenant index specs are in the repo and are not applied to production. The 90-day audit TTL is unchanged.
- **Agency onboarding** (`/platform`, `/signup`). A super-admin records the agency, Florida license numbers, branding, staff invites, and `env:` secret refs. Invites are stored and are not emailed. Self-serve signup stays pending until that super-admin approves it.
- **Stripe test-mode billing** (`/platform/billing`). Checkout refuses a live key and refuses a price that is not configured. MRR is the sum of stored cents from signed `invoice.paid` events on active agencies. The second failed invoice suspends packet send and texting. Shamrock is not billed. No card number is stored.
- **Surety checklist** (`/platform/sureties`). OSI and Palmetto stay on for Shamrock. Inactive carriers cannot be enabled. Private templates are labels only. When the flag is on, packet finalize refuses a surety the agency is not enabled for.
- **Lead subscriptions** (`/platform/leads`). Fail-closed counties, including the Ohio pilot, are not sold. Shared is the default. An exclusive county rejects a second subscriber. New arrests fan out only when the flag is on, as booking pointers without the defendant name. Shamrock's unsaved list is the key Florida desk.
- **Start bond packet** (`/bond-packet`), four steps. It hydrates the roster, suggests an available power, and runs the existing write-bond preflight and DocuSeal with email off. It does not text, charge a card, or mark the power used. `POST /api/write-bond` stays retired (HTTP 410).

## [Unreleased] — 2026-10-07 (Charlotte FL Revize hardening)

### Fixed
- **Charlotte (FL):** moved onto the shared Revize roster contract (`scrapers/revize_roster.py`, same as Manatee #113). Columns are mapped by header (Booking # / Last / First / Middle / Charge / Arrest Date / Released); the source `Booking #` is checked against the row's `/bookings/<id>` link; a row with a blank or unrecognised booking number (or too few cells) and a booking that names two people both fail closed. Every charge row for a booking is kept (`Charges` joined with ` | `, plus `extra_data.charge_details`). `Bond_Amount`/`Bond_Type` are `""` (the roster publishes no bond), no longer `"0"`. `Released` is required and an unrecognised value fails closed. Paging fails closed on a missing table or column, an empty first page, an empty or repeated page, hitting `MAX_PAGES` with a next page still offered, or a walked count that differs from a published total. A Cloudflare challenge/block, or no verified US residential exit, raises `EgressBlocked` and writes nothing (`CHARLOTTE_EGRESS_MODE=direct` for Leads Ops residential runs; default `auto` keeps the existing resolver). Live check from the box: `/`, `/bookings` and `?page=2` → 403 CF challenge (`docs/recon/FL_CHARLOTTE_REVIZE_2026-10-07.md`). Health stays `unverified`.
- **Hydrate:** legacy Charlotte/Manatee docs that carry a scraped `"0"` for a bond the roster never published now hydrate as unknown (blank), not `$0`. A staff `bond_override` / `MANUAL_CHARGE_BONDS` flag left behind a rescrape that rewrote `bond_amount` to `0.0` also stays unknown; a staff-set positive amount still wins.

## [Unreleased] — 2026-10-07 (Sarasota / Manatee FL audit)

### Fixed
- **Manatee (FL):** the roster parser maps columns by header (Booking # / Last / First / Middle / Charge / Arrest Date / Released) and keys on the source `Booking #`, checked against the row's `/bookings/<id>` link; one booking number naming two people raises `ParseDriftError`. Charge rows for the same booking are grouped into one record (`Charges` joined with ` | `, plus `extra_data.charge_details` for hydrate) instead of keeping only the first charge. `Bond_Amount`/`Bond_Type` are `""` (the roster publishes no bond), no longer `"0"`. `Released` is a required column, and an unrecognised value fails closed. Paging fails closed on a missing table or column, an empty first page, an empty or repeated page, hitting `MAX_PAGES` with a next page still offered, or a walked count that differs from a published total. Previously a repeated page or a Cloudflare page ended the walk silently and wrote what it had.
- **Manatee egress:** a Cloudflare challenge/block page, or no usable residential exit, raises the new `EgressBlocked` (classified `anti_bot` with `egress_block=True`, never retried) instead of returning an empty or partial roster. New `MANATEE_EGRESS_MODE=direct` runs from the Leads Ops Mac / home ISP / iPhone hotspot without any proxy and refuses a non-residential host. `auto` (the default) keeps the existing resolver. No proxy, CAPTCHA or stealth path added. Ops note: `docs/ops/MANATEE_RESIDENTIAL_RUN.md`; read smoke: `scripts/manatee_residential_smoke.py`.
- **Residential preflight (`scrapers/cf_browser.check_exit_ip`):** an exit whose org/country lookup came back empty (IP-info APIs rate-limited) was treated as residential. A datacenter box passed this check on 2026-10-07. Unknown exits are now `exit_unverified` and not residential.
- **One-click hydrate (`packet_builder_service`):** an arrest saved with an unknown bond (`bond_amount_raw=""`, numeric `bond_amount=0.0`) no longer hydrates as a $0 bond. The context carries `bond_amount_known=False` and per-charge rows carry `bond_amount=None` (blank in Write Bond). A source-published `0` stays `0.0`. Staff bond edits (`bond_override` from update-bond-amount, `MANUAL_CHARGE_BONDS` from update-charge-bonds) win over the stale blank `bond_amount_raw`, and top-level `charge_details` (the writer's copy or the staff-edited rows) now takes precedence over the scraped `extra.charge_details`, so staff amounts, case numbers and POAs survive rehydrate.
- **Sarasota (FL):** stays fail closed. The 2026-10-07 live check found the official current-inmate listing (1,081 entries) carries only an opaque per-person link id, name and date of birth, with no booking number or booking timestamp; detail and search pages return a Cloudflare challenge. The reopen gate is encoded in `scrapers/counties/sarasota_contract.py`: every listing entry must itself carry a source booking number and a booking date/time (page-wide labels do not count). Evidence, matrix and registry updated (`docs/recon/FL_SARASOTA_MANATEE_AUDIT_2026-10-07.md`). Health: Sarasota `fail_closed`, Manatee `unverified`.

### Removed (Manatee follow-up, owner decision via CoS)
- **Manatee (FL):** removed the APE/Warren + office SOCKS proxy resolver (`MANATEE_EGRESS_MODE=auto`, now a config error) and the Patchright stealth launcher / stealth context. Manatee runs only on the Leads Ops home relay's own residential exit (`MANATEE_EGRESS_MODE=direct`, the default and only mode) with stock Playwright Chromium (`--no-proxy-server`, proxy env vars stripped). The exit-IP check ignores proxy env vars (`check_exit_ip(..., trust_env=False)`, a new backward-compatible option). Egress blocks still fail loud with nothing written. `tests/test_manatee_no_proxy_path.py` proves no proxy or stealth path is reachable. Ops note `docs/ops/MANATEE_RESIDENTIAL_RUN.md` updated, including the detail-page fixture capture request. Also, a roster row with a blank or unrecognised booking number (or too few cells) now fails closed instead of being dropped. Charlotte and the shared resolver/launcher are unchanged (still used by Charlotte, Marion and Hillsborough).

## [Unreleased] — 2026-10-07 (Bond workflow fail-closed)

### Fixed
- **Intake match:** a name and date of birth with no county stays in staff review. The matcher no longer auto-links the first arrest in the file that shares that name and date, including when only one arrest matches.
- **Sheets ledger:** fan-out reloads the intake after the match, so a matched row keeps status, county, state, strategy, and timestamp.
- **Promote:** a blank or $0 bond amount returns 422 and does not allocate a power.
- **Forfeiture:** the power is still released, and one recovery case opens at `pending_review` for staff to confirm. Recovery agents do not see it until that confirm, and no message is sent. If that insert fails, repeating the forfeited status opens the missing review.
- **Bond Desk:** a name and date of birth with no county shows each possible arrest and links one only after staff confirm. A suggested booking is not treated as a match.

### Changed
- **Workflow map:** Telegram and Shannon are a gap. `shamrock-telegram-app` does not call `/api/intake/submit`. The map lists the Telegram and Shannon callers that exist in this repo.

## [Unreleased] — 2026-10-07 (Production uptime watchdog)

### Added
- **Production uptime** GitHub Action (`.github/workflows/prod-uptime.yml`). Every 15 minutes at :07/:22/:37/:52 UTC, plus `workflow_dispatch`, it probes the public `/health` and `/health/live` routes on `leads` and `paperwork`. A failure opens one `prod-down` issue (or comments on the open one). Recovery closes it. Disable from the Actions tab.
- **Workflow lint** (actionlint on PRs that touch `.github/`), a **changelog** check when `scrapers/` or `dashboard/` change (skip with the `skip-changelog` label), and a **weekly Python CodeQL** scan (Monday 05:41 UTC).
- **Brand-contact guard** (`scripts/check_brand_contacts.py`, in the CI pytest list). Fails on the known-wrong lookalike hostnames and on phones shaped like the canonical Shamrock numbers (239-332-2245, 727-295-2245, 239-955-0178) that are not those numbers.

### Fixed
- Obvious brand typos in the overdue check-in email, social help, and the ops manual now use the canonical office line 239-332-2245. Prospecting's fallback intake URL uses the `.biz` apex. The active-bond payment SMS fallback uses that office line instead of the dashboard PIN.

## [Unreleased] — 2026-10-07 (York SC source Booking Number)

### Changed
- **York (SC):** the official Inmates in Jail roster (`inmatesinjail.yorkcountygov.com`) answers plain HTTPS again. The earlier timeout hold no longer reproduces. The scraper now walks every `dgJackets` page (29 pages, 435 rows, matching `Results Count`). `Booking_Number` is the published Booking Number (`DC<YYYY><NNNNN>`) and is cross-checked against the photo key. Booking date/time, `*In Jail` status, `Total Bond`, and charges come from source fields only. An incomplete walk fails closed with `ParseDriftError` instead of writing partial records: a unique row count that differs from `Results Count`, a repeated page or postback, or hitting `MAX_PAGES` before the last page. The old parser read the facility header as the person's name and saw page 1 only; both are fixed. `SOURCE_CONTRACT_VALIDATED=True` and Health `fail_closed` is lifted, but Health stays **unverified** until a write smoke. Evidence and matrix are updated (`docs/recon/SC_YORK_INMATES_IN_JAIL_2026-10-07.md`). Richland, Sumter, Hampton, Marlboro, Oconee, and Pickens are untouched.

## [Unreleased] — 2026-10-07 (Alachua FL fail closed)

### Fixed
- **Alachua (FL):** the View All roster publishes no booking number. The old parser read the FirstName column as `Booking_Number` and the Full Name column as `Booking_Date`, so ~984 rows collapsed onto ~618 first-name keys. The only other identifier is the person-level `MNI #`, and person IDs are not booking keys (same policy as Durham NC and Clay FL). This resolves the owner-decision note from #106. `alachua.py` is now `SOURCE_CONTRACT_VALIDATED=False` with no source fetch, Health `fail_closed`, and the matrix, registry and evidence are updated (`docs/recon/FL_ALACHUA_FAIL_CLOSED_2026-10-07.md`). Existing prod rows keyed on first names need a Leads Ops cleanup. Nothing was deleted here.

## [Unreleased] — 2026-10-07 (FL Brevard + New World booking keys)

### Fixed
- **Brevard (FL):** the results parser read column 0 as the name, so it stored the source `Booking #` as the name, the name as the booking number and the DOB as the booking date. Columns are now mapped by header. The plain-requests path posts the public form to `/?handler=Search` with its antiforgery token and the form's `max` date, and bonds/charges come from the detail page for in-custody rows only. `curl_cffi`, `verify=False` and the DrissionPage fallback are retired. Health stays unverified.
- **Brevard / Walton / Flagler (FL) review fixes:** unknown bond stays unknown. A released Brevard row, or a detail fetch that fails, times out, returns non-200 or names another booking, now emits `Bond_Amount=""` instead of an invented `0`, so the scorer applies no $0 penalty; New World does the same for a blank Total Bond Amount. `0` is emitted only when the source publishes $0. Brevard `Bond_Type` comes from bond rows with a positive amount, so a `$2,500 Surety` row is no longer masked by a `$0 No Bond` row (previously scored NO BOND -50). `Released` is now a required Brevard results column and only `Yes`/`No` are accepted; header or value drift raises. `tests/test_fl_brevard_newworld.py` added to CI.
- **Walton / Flagler (FL):** the New World detail parsers took the `Booking History` heading as the booking number, so every row shared one key. New shared `scrapers/fl_newworld.py` emits the newest open booking (`YYYY-NNNNNNNN`, empty Release Date) with its source Total Bond Amount and charges. Walton no longer walks `InCustody=False`. Health stays unverified.

## [Unreleased] — 2026-10-07 (post-merge cleanup)

### Fixed
- **SmartWEB JAIL View (`scrapers/fl_smartweb.py`):** status is a bounded token (`In Jail` / `Released` / `Out of Jail`). The old greedy capture stored `Released Booking No` and classified `Out of Jail` as in custody. Charge-grid cells like `$2,500.00 SURETY` keep the published dollars. A photo whose `bookno` does not match `Booking No` no longer consumes that booking key, so a later matching card can still emit.
- **OSI appearance bond PDF:** an empty `CaseNum` (booking number rejected as a court case) now clears the template sample `26MM020844`. PyMuPDF was leaving the stored `/V` when the fill wrote `""`.
- **Hardee (FL) / St. Johns (FL):** #104 documented both as holds, but Health stayed the unverified default and the matrix still said generic `recon_only`. Both are now `SOURCE_CONTRACT_VALIDATED=False` and Health `fail_closed` (403 SmartWEB path / OCV app only). Matrix regenerated.

### Changed
- Restored Unreleased notes that never landed for the 2026-10-07 merges below. `curl_cffi` stays in `requirements.txt` because Lee, Marion, and the stealth stack still import it; the SmartWEB counties no longer do.
- **#104 Putnam / Sumter:** legacy vs modern `AddMoreResults` is chosen from the results page. Putnam and Sumter use shared `fl_smartweb` and source Booking No. Health stays unverified.
- **#103 Bradford / Dixie / Taylor / Escambia / Santa Rosa:** SmartWEB JAIL View Booking No contracts. Invented name/date keys and the county `curl_cffi` paths are retired. Health stays unverified.
- **#95 Citrus / Gilchrist / Hamilton / Madison / Okaloosa:** public contracts revived, Health unverified. Clay, Columbia, and Okeechobee stay fail_closed.
- **#93 Pinellas / Marion / Charlotte / Manatee / Miami-Dade:** bond and charges hydrate from published source fields only.
- **BailSafe P0:** Book Watch review (A1), OSI/Palmetto powers packs (C1), fail-closed Recovery role (B1), staff missed check-in evidence pack (A2). No automatic indemnitor text.

## [Unreleased] — 2026-10-07 (NC SSW ADPST Citizen Connect BookingID)

### Changed
- **Anson / Duplin / Polk / Scotland / Transylvania (NC):** proved ordinary public Southern Software Citizen Connect rosters (`AgencyID=AnsonCoNC` / `DuplinCoNC` / `PolkCoNC` / `ScotlandCoNC` / `TransylvaniaCoNC`). `Booking_Number` is source **BookingID** (href / `data-bookingid` / mugshot debug) — never invent keys; never use person `NameID` alone. Live scrapes 82 / 161 / 43 / 157 / 79 unique bookings; charges/bond only when the card publishes them. `SOURCE_CONTRACT_VALIDATED=True`; Scotland lifted from Health `fail_closed`. Health stays **unverified** until write smoke. Evidence + matrix updated (`docs/recon/NC_SSW_ADPST_CITIZEN_CONNECT_2026-10-07.md`). Empty CC agencies and Brunswick Zuercher / Durham / Wayne / P2C holds untouched.

## [Unreleased] — 2026-10-07 (NC SSW five Citizen Connect BookingID)

### Changed
- **Henderson / Sampson / Stokes / Surry / Edgecombe (NC):** proved ordinary public Southern Software Citizen Connect rosters (`AgencyID=HendersonCoNC` / `SampsonCoNC` / `StokesCoNC` / `SurryCoNC` / `EdgecombeCoNC`). `Booking_Number` is source **BookingID** (href / `data-bookingid` / mugshot debug) — never invent keys; never use person `NameID` alone. Live scrapes 196 / 275 / 106 / 317 / 261 unique bookings; charges/bond only when the card publishes them. `SOURCE_CONTRACT_VALIDATED=True`; Health stays **unverified** until write smoke. Evidence + matrix updated (`docs/recon/NC_SSW_FIVE_CITIZEN_CONNECT_2026-10-07.md`). Empty CC agencies and Brunswick Zuercher / Durham / Wayne / P2C holds untouched.

## [Unreleased] — 2026-10-07 (NC Harnett Citizen Connect BookingID)

### Changed
- **Harnett (NC):** proved ordinary public Southern Software Citizen Connect roster (`AgencyID=HarnettCoNC`, JMS `NC0430000`). `Booking_Number` is source **BookingID** (href / `data-bookingid` / mugshot debug) — never invent keys; never use person `NameID` alone. Live scrape 306 unique bookings; charges/bond only when the card publishes them. `SOURCE_CONTRACT_VALIDATED=True`; Health stays **unverified** until write smoke. Evidence + matrix updated (`docs/recon/NC_HARNETT_CITIZEN_CONNECT_2026-10-07.md`). Brunswick Zuercher (no booking/inmate ID) and empty Citizen Connect agencies (Robeson/Rockingham/Vance/Warren/Wilkes/Granville/Nash) stay held; other proven NC SSW counties (Henderson/Sampson/Stokes/Surry/Edgecombe) deferred to follow-on PRs.

## [Unreleased] — 2026-10-07 (NC Davidson DCInmates ashx)

### Fixed
- **Davidson (NC):** lifted fail_closed after proving ordinary public `inmate_data.ashx` roster (`total_count≈311`) with source booking **YY-######** in cell[11]; detail `inmate_details.ashx` supplies Incarceration Date, offenses, and Bail Bonds Remaining. Removed historical `DAV_` invented-key fallback. `SOURCE_CONTRACT_VALIDATED=True`; Health stays **unverified** until write smoke. Evidence + matrix updated (`docs/recon/NC_DAVIDSON_DCINMATES_2026-10-07.md`). Durham (person offender ID only, no booking ID) and Wayne (Citizen Connect AgencyID missing) stay held — no fluff PR.

## [Unreleased] — 2026-10-07 (SC Oconee + Pickens Zuercher fail_closed)

### Changed
- **Oconee (SC) / Pickens (SC):** explicit `SOURCE_CONTRACT_VALIDATED=False` guards on the Zuercher thin wrappers (public roster, **no** source booking/inmate ID — same hold as Colleton/Kershaw). `SCRAPER_SOURCE_STATES` → `fail_closed`. Evidence + matrix + `docs/recon/SC_OCONEE_PICKENS_ZUERCHER_2026-10-07.md`. No invented keys.

## [Unreleased] — 2026-10-07 (NC Mecklenburg Inmate Inquiry JSON)

### Changed
- **Mecklenburg (NC):** replaced HTML letter-walk + invented `MECK_` MD5 booking keys with ordinary public Knockout JSON (`GET /Inmate/_Search?activeOnly=true`, `_Summary`, `_GetCharges`). `Booking_Number` is source **JID** (`YY-######`); `Charges` / `Bond_Amount` from published `ActualBailAmount` sum only. `SOURCE_CONTRACT_VALIDATED=True`; Health stays **unverified** until write smoke. Evidence + matrix updated (`docs/recon/NC_MECKLENBURG_INMATE_API_2026-10-07.md`). Option A (promote Buncombe/Carteret/Catawba/Craven/Johnston/Lee/Lincoln/Moore/Richmond/Stanly to `verified_public`) deferred — those ten appear as Palmetto `live_write` labels but lack `live_emitter_evidence.json` write-smoke rows. Durham/Onslow/Rowan/Wayne and P2C holds untouched.

## [Unreleased] — 2026-10-07 (SC Lancaster NewWorld contract)

### Changed
- **Lancaster (SC):** replaced thin `NewWorldBaseScraper` wrapper with a plain-HTTPS InmateInquiry scraper (`SC0290000`). `Booking_Number` is the detail **Booking** `YYYY-########` (URL Detail ids and invented `NW_` keys rejected). `Charges` from the BookingCharges grid; `Bond_Amount` from **Total Bond Amount** only (per-charge Bond cells may be reference ids, not dollars). `SOURCE_CONTRACT_VALIDATED=True`; Health stays **unverified** until write smoke (not `verified_public`). Evidence + matrix updated (`docs/recon/SC_LANCASTER_NEWWORLD_2026-10-07.md`). SC fail_closed holds (Richland/Sumter/Hampton/Marlboro/Oconee/Pickens/…) untouched.

## [Unreleased] — 2026-10-07 (SC Florence + Newberry field completeness)

### Changed
- **Florence (SC):** letter-walk roster now enriches each inmate from `inmate-details`; `Booking_Number` is the source **Name ID** (no `FLO_` keys); `Charges` / `Bond_Amount` / `Bond_Type` come from the detail Charge grid only.
- **Newberry (SC):** current Sheriff bookings PDF parser extracts charge lines; accepts source ids `SO`/`NP`/`HP`/`PP`/`HA`/`SL`/`GS`; `Bond_Amount` only when an explicit `Bond $…` line is printed (statute `$` text and `BOND POSTED` are not bonds).
- Promoted both to Health `verified_public` with live_write evidence from 2026-09-23 plus this field probe (`docs/recon/SC_FLORENCE_NEWBERRY_FIELDS_2026-10-07.md`). Fail_closed SC counties untouched.

## [Unreleased] — 2026-10-07 (NC Pitt listing-only hydrate note)

### Changed
- **Pitt County (NC 147):** Select-detail probe confirmed the public Detainee Search does not publish charges or bond amounts (detail has demographics/date confined only; Charge/Sentence/Print bounce to search; bond link is instructions PDF). Scraper stays `verified_public` on source 6-digit Booking Number, keeps Charges=`Unknown` / Bond_Amount=`0` (no invented amounts), and records an honest `extra.hydrate_limitation` / `listing_only_for_hydrate` flag for Write Bond / DocuSeal. Matrix + gap-queue evidence updated. NC fail_closed holds unchanged; optional Buncombe/Carteret/… verified_public promotions deferred (matrix still `unverified`).

## [Unreleased] — 2026-10-05 (Dependency updates)

### Changed
- Bumped `uvicorn` from >=0.30.0 to >=0.54.0 (#79).
- Bumped `firebase-admin` from >=6.4.0 to >=7.7.0 (#78).
- Bumped `openpyxl` from >=3.1.0 to >=3.1.5 (#77).
- Bumped `httpx` from >=0.27.0 to >=0.28.1 (#76).
- Bumped `jellyfish` from >=1.0.0 to >=1.2.1 (#75).

## [Unreleased] — 2026-10-01 (Crawl hygiene, robots.txt & noindex)

### Added
- Public `robots.txt` endpoint served directly with `User-agent: * Disallow: /` for GET and HEAD requests, exempt from PIN authentication allowlist.
- Added `<meta name="robots" content="noindex, nofollow">` to `/` and `/login` headers to prevent search crawlers from indexing staff CRM pages.
- Regression test suite `tests/test_crawl_hygiene.py` verifying public accessibility of `/robots.txt` and noindex meta tags on CRM surfaces.

## [Unreleased] — 2026-09-30 (Tennessee scrapers wave 2 expansion)

### Added
- **Washington County (TN 179):** Official 30-day rolling booking sheet PDF parser (`scrapers/counties_tn/washington.py`) extracting official 5–10 digit booking numbers via `pypdf`/`pdfplumber`. Plain HTTPS, 509+ live records.
- **Hamilton County (TN 065):** HCSO Daily Booking API + Inmates Roster (`scrapers/counties_tn/hamilton.py`) extracting official Record GUID (`R_ID`) and SPN. Plain HTTPS, 101+ live records.
- **Sevier County (TN 155):** SCSO Next.js / MyOCV public roster (`scrapers/counties_tn/sevier.py`) extracting numeric Inmate ID. Plain HTTPS, 100+ live records.
- **Hamblen County (TN 063):** ISOMS public portal (`scrapers/counties_tn/hamblen.py`) with deterministic surrogate key. Plain HTTPS, 351+ live records.
- Promoted all four to `verified_public` in `dashboard/extensions.py` (`SCRAPER_SOURCE_STATES`); added contract evidence to `docs/recon/county_source_contract_evidence.json` and `docs/recon/live_emitter_evidence.json`. Documentation in `docs/recon/TENNESSEE_SCRAPERS_EXPANSION_2026-09-30.md`.
- Expanded test suite `tests/test_tennessee_scrapers.py` with synthetic fixtures for Hamilton, Sevier, Washington, and Hamblen.

## [Unreleased] — 2026-09-29 (Tennessee scrapers wave 1 promotion)

### Added
- Promoted four high-volume Tennessee scrapers from `fail_closed` to `verified_public` with direct official county portal integrations without synthetic keys, CAPTCHA bypasses, or TLS circumvention:
  - **Davidson County (TN 037):** DCSO RecentBookings + Details with official 7-digit DCSO JMS number.
  - **Knox County (TN 093):** Knox Sheriff 24h arrests + inmate population with official 7-digit Knox IDN#.
  - **Sumner County (TN 165):** MyOCV `inmatesV3` real-time S3 feed with official 6-digit Inmate ID.
  - **Shelby County (TN 157):** Memphis 201 Poplar IML portal with official 8-digit booking number.
- Documentation in `docs/recon/TENNESSEE_SCRAPERS_PROMOTION_2026-09-29.md`.
- Test suite in `tests/test_tennessee_scrapers.py`.

## [Unreleased] — 2026-09-27 (Wix intake, kiosk, surety registry, pay links — Merged in PR #72)

### Added
- `POST /api/webhooks/wix-intake` now accepts the Wix wizard payload via `wix_wizard_adapter` (no county/state/surety defaults), is idempotent on `clientNonce`, auto-matches, and returns `payment_link`.
- `intake_fanout`: after the Mongo save, a non-blocking, retried copy to the GAS "Intake Ledger" sheet and Slack (`intake_fanout_outbox`, cron `intake_fanout_retry`).
- `surety_registry` (OSI + Palmetto active; Lexington/Roche/Universal/Bankers inactive) and `GET /api/paperwork/sureties`; greyed-out "coming soon" pills in Write Bond.
- `payment_links` (one place for pay-by-card links) and `GET /api/paperwork/payment-links`.
- Kiosk: `/kiosk`, `POST /api/portal/kiosk-id-confirm`, idle wipe, `/done?kiosk=1` reset.

### Fixed
- Co-indemnitor kiosk ID scan overwrote the primary indemnitor's CRM record.
- Unknown sureties silently became OSI in PDF/DocuSeal/Drive paths (now fail closed).
- Applicant data was kept in the browser's localStorage on the shared tablet.

## [Unreleased] — 2026-09-25 (TnCIS fail closed)

### Changed
- **TnCIS (TN): Obscura fallback OFF, fail closed (owner decision).** The Cloudflare-protected statewide portal has no proven public contract. The curl_cffi + residential/mobile proxy, Patchright stealth, and Obscura fallback chain was removed from `tennessee_tncis_v2_ape.py`. The scope is now `SOURCE_CONTRACT_VALIDATED = False` and `fail_closed` in `SCRAPER_SOURCE_STATES`, is added to `OBSCURA_HARD_DENY_LABELS`, and has a `hold` row in the live emitter evidence. A Cloudflare or anti-bot answer raises `AntiBotBlocked` (`anti_bot`, never retried). Rows without a source identifier are never emitted. The matrix was regenerated (TN unverified → fail_closed for the TnCIS scope). Tests: `tests/test_tncis_fail_closed.py`.

## [Unreleased] — 2026-09-25 (Scraper self-heal / fail-loud)

### Added
- **BaseScraper resilience** (`scrapers/scraper_resilience.py`): transient `network` retry with 2s/4s/8s backoff (never 429, anti-bot, or an active per-county cooldown; Lee opts out), fixed error classes (`network`, `anti_bot`, `url_changed`, `parse_drift`, `unknown`), and immediate `#scraper-errors` schema-drift alerts.
- **Auto-disable** after 5 consecutive failures, stored on `scraper_status`. Health shows ⛔ Auto-disabled. Re-enable happens through a successful canary (≥1 record), the Health button / `POST /api/scraper/enable`, or `scripts/scraper_reenable.py`. KEY FL counties alert but are never skipped.
- **Obscura routing policy:** `OBSCURA_ROUTE_COUNTIES` opt-in, verified_public only; hard deny list for holds and fail_closed scopes. No county is routed by default.
- **Matrix drift gate:** `scripts/build_recon_matrix.py --check`, `docs/recon/live_emitter_evidence.json`, and `tests/test_source_state_drift.py`, which run in CI through a bridge test in `test_source_contract_run_guard.py` until `ci.yml` lists them directly.
- Runbook `docs/ops/SCRAPER_SELF_HEALING.md`.

### Fixed
- The Slack webhook URL could leak into logs through `requests` exception strings (`SlackNotifier`, `ErrorTracker`). Only the exception class is logged now. ErrorTracker no longer double-posts failures.
- **Health registry drift:** 30 code-guarded scrapers added as `fail_closed`, and Broward (FL) added as `verified_public`.
- **Hampton / Marlboro (SC):** now actually fail closed (they were fetching through proxy paths into a 403 and synthesizing keys).
- `COUNTY_SOURCE_CONTRACT_MATRIX.md` regenerated from versioned evidence (SC verified_public 1 → 5).
- Self-healing docs (AGENTS, README, ARCHITECTURE, Watchdog) now describe what exists. The URL pre-flight check, failure history, and `force_enable()` never existed.

## [Unreleased] — 2026-09-23

### Fixed
- **Broward County, FL** — Turnstile Arrest Search path live after Mac write smoke (30 new). Pass `action=arrest_search` to SolveCaptcha; Health fail_closed lifted; requires `SOLVECAPTCHA_KEY`.
- **Charleston County, SC** — ListView parser uses the source Inmate # (no `CHS_` hash keys). reCAPTCHA or navigation failure is `status=error`; only a loaded results page with zero Inmate # rows is empty. Health `verified_public`.
- **Richland County, SC** and **Sumter County, SC** — fail closed. Richland JMSOnline is on a maintenance page and its list view has no source booking key. Sumter SmartCOP still synthesizes a booking number from name+date.
- **Southern Software roster cards** — keep going when the index page is non-200 (agency id fallback), accept hyphen-less booking attributes and `BookingID=` comments, and drop agency text that is really the next field label.

## [Unreleased] — 2026-08-28 (P0 gate update)

### Changed
- **D2** marked staff-confirmed working (dashboard iMessage).
- **C3** recorded as owner-deferred, not a formal Stage 2 blocker; rotation still pending.
- **B3** remains open until a real BondCase smoke is logged. Operator reports the path has worked.
- D3 7-day `review` clock starts 2026-08-28. Do not enable `full_auto`.

## [Unreleased] — 2026-08-28 (Clipboard / docs alignment)

### Changed
- **Docs:** `SECURITY.md` now lists DocuSeal (not SignNow) as the active e-sign secret and auth path. Public blog copy no longer names SignNow or the retired (239) 552-1349 CTA; voice NAP is (239) 332-2245.
- **Platform truth:** `docs/PLATFORM.md` and `docs/ECOSYSTEM_PROD_CHECKLIST.md` C4 record the sibling portal factory as **V468 / @468**. Wix remains a non-issuing clipboard; Super CRM is the only packet authority.
- This does **not** close B3/B5, C3, or D2, or mark Stage 2 production-hardened.

## [Unreleased] — 2026-08-21 (Confirmed Lee booking intake)

### Added
- **Staff-confirmed Lee booking intake** — Palantir now accepts one official HTTPS Lee County booking URL, projects a minimized public booking preview, and requires staff acknowledgement plus exact booking-number re-entry before it creates or refreshes an **ArrestLead only**.
- **Fail-closed source and write controls** — the route allows only the official Lee host and numeric booking ID, expires previews after 15 minutes, uses a server-side preview ID, protects the canonical booking/county/state identity, stops cross-jurisdiction collisions, and refuses protected downstream records.
- **Data-minimization and audit controls** — the booking path removes address, DOB, phone, email, relative, household, raw-response, and enrichment data. It records only non-PII confirmation metadata and never starts an OSINT, bond, paperwork, signature, payment, outreach, surety, or POA flow.

### Verified
- Commits [`f1a151c`](https://github.com/Shamrock2245/shamrock-leads/commit/f1a151c1a1e297ddfecfb6cf41023213d96a4b01) and cache-safe asset revision [`bb1a1a8`](https://github.com/Shamrock2245/shamrock-leads/commit/bb1a1a83b0e478ee1773e774f306b04f3e15aef0) deployed successfully through Hetzner workflows [`32487310626`](https://github.com/Shamrock2245/shamrock-leads/actions/runs/32487310626) and [`32487698386`](https://github.com/Shamrock2245/shamrock-leads/actions/runs/32487698386).
- Focused confirmed-booking and Palantir fail-closed tests passed (**11**); JavaScript syntax and diff checks passed. Final probes returned `200` for Auto-CRM `/health`, cache-safe Palantir v6 CSS/JavaScript, DocuSeal, Bail School, paperwork, and Postiz `/auth`.
- The stable GAS URL is unavailable in this clean checkout, so GAS health was not re-probed. The strict local secrets check remains red without production environment files and sibling repositories; it is not treated as green. No real booking record, CRM record, bond, packet, signature, payment, or client message was created during implementation or validation.
- This release does **not** close B3/B5, C3, or D2, enable `full_auto` outreach, or mark the platform production-hardened.

## [Unreleased] — 2026-08-20 (Palantir Command HUD)

### Added
- **Palantir reactor HUD** — rebuilt the intelligence workspace as an interactive, read-only holographic command surface with exact CRM entity resolution, selectable relationship nodes, provenance and confidence inspection, and visual-layer filtering.
- **Operational intelligence controls** — added OSIRIS county filtering, feed refresh, and stream-to-map focus; SPECTRA scan state rendering that distinguishes no result, provider unavailability, and absent verified geotags; and a CRM-bounded dossier command surface.

### Verified
- Commit `913d4ce` deployed successfully through Hetzner workflow [`32397812988`](https://github.com/Shamrock2245/shamrock-leads/actions/runs/32397812988). The v5 Palantir CSS and JavaScript assets returned `200`; final `/health`, DocuSeal, Bail School, paperwork, and Postiz `/auth` probes returned `200`.
- The focused Palantir fail-closed suite passed (**6**); JavaScript syntax and diff checks passed. The stable GAS URL is unavailable in this clean checkout, so GAS health was not re-probed. The strict local secrets check remains red without production environment files and sibling repositories; it is not treated as green.
- This user-interface release does **not** close B3/B5, C3, or D2, enable `full_auto` outreach, or mark the platform production-hardened. No person-level record, intake, bond, packet, signature, payment, or client contact was created during implementation or validation.

## [Unreleased] — 2026-08-19 (Ohio source-contract guard pilot)

### Added
- **Ohio guarded pilot scopes** — registered Clermont, Clinton, and Huron under `scrapers/counties_oh/` with state-qualified scheduler IDs and dashboard labels. Each module is explicitly `fail_closed` and returns before source retrieval, scoring, persistence, alerts, outreach, matching, paperwork, signatures, payments, or bond-writing activity.
- **Ohio contract and regression coverage** — added a non-PII source-contract record plus runtime, scheduler-key, registry, dashboard-state, and documentation tests. The separate Ohio guard inventory does not expand the existing ten-state 947-scope reconnaissance matrix or claim an OSI/Palmetto writing footprint.

### Verified
- Commit `3b3bee0` deployed successfully through Hetzner workflow `32290521634`.
- Focused source-contract, scheduler, registry, source-key, and documentation tests passed (**32**, plus six subtests). Post-deploy public checks returned `200` for Auto-CRM `/health`, DocuSeal, Bail School, paperwork portal, and Postiz `/auth`.
- The stable GAS URL was not available in the clean checkout, and the strict local secrets check remains red without production `.env` files and sibling repositories. Neither limitation is treated as green; no secret, person-level record, bond, packet, payment, signature, or client contact was created.
- This release does **not** close B3/B5, C3, or D2, enable `full_auto` outreach, or mark the platform production-hardened.

## [Unreleased] — 2026-08-19 (Dashboard completeness audit)

### Fixed
- **Client Portal check-in KPI completed** — the staff portal now renders the backed seven-day completed check-in count from `checkins_7d`; it no longer targets a nonexistent DOM node or leaves the card at a permanent placeholder value.
- **FTA surrender guidance corrected** — the Level 3 surrender confirmation and result message no longer promise a retired SignNow authorization. They now state the actual path: `surrender_pending`, no e-sign packet, manual staff document review, and conditional iMessage delivery outcome.

### Added
- **Dashboard regression contracts** — focused API and source-contract coverage protects the seven-day portal metric and prevents the retired signature-provider copy from returning.

### Verified
- Commit `3c46234` deployed successfully through Hetzner workflow `32268562642`.
- Focused dashboard, portal, and source-contract tests passed (**23**); updated JavaScript parsed successfully.
- Post-deploy public checks returned `200` for Auto-CRM health, DocuSeal, Bail School, paperwork portal, and Postiz `/auth`.
- This dashboard correction does **not** satisfy the still-open B3/B5, C3, or D2 human production gates.

## [Unreleased] — 2026-08-18 (DocuSeal initial iMessage delivery gate)

### Changed
- **Packet delivery hardened** — commit `cd74b00` requires trusted direct self-hosted DocuSeal signer URLs, exact packet/role/external-ID bindings, and one evaluation per packet. Manual iMessage delivery now requires an authenticated staff session, active DocuSeal submission, and the exact role-bound signer; it returns no signing link and retains no phone or URL in delivery audit details.
- **Defendant delivery gated** — the approved defendant template is staged, but `include_defendant=false` remains live. A future defendant automatic notice also requires an immutable packet snapshot of a staff-recorded `verified_opt_in` authorization bound to the exact `Defendant_ID`; phone or generic-packet data cannot satisfy this gate.
- **Indemnitor/co-indemnitor delivery enabled** — the approved initial DocuSeal iMessage template is active for explicitly packet-bound indemnitors and co-indemnitors. Defendant delivery is explicitly disabled; its approved template is staged but cannot send while the role switch is off. The change was made through the protected Automations editor; it did not send a client message.
- **Protected editor delivered** — the Initial DocuSeal Notice card now exposes a role-specific configuration modal rather than a generic toggle. Activation is rejected without an approved `{signing_link}` template, and defendant inclusion is rejected without separately approved defendant copy.

### Added
- **Narrow initial DocuSeal delivery exception** — after a valid packet is persisted, the platform can send one **iMessage-only** signing notice to each explicitly DocuSeal-bound indemnitor or co-indemnitor. The exception is disabled by default and requires approved recipient-specific copy containing `{signing_link}`. A defendant requires a separate opt-in and separate approved copy.
- **Fail-closed delivery binding** — the sender requires a non-voided `pending_signature` packet, active DocuSeal submission, exact packet metadata plus external-ID binding, a signer-specific link, and a validated signer phone. It never falls back to generic packet phones, SMS, retries, or chase automation.
- **Auditable control path** — authorized staff can configure the narrowly allowed settings through the automation control surface; unsupported fields and templates without `{signing_link}` are rejected.

### Verified
- Commits `cda89f9`, `97d3f17`, and `8819625` deployed successfully through Hetzner workflows `32146659635`, `32174712655`, and `32174980615`.
- Focused DocuSeal, authorization, automation-control, paperwork, and portal tests passed (**62**). Public Auto-CRM health, DocuSeal, school, paperwork, and Postiz `/auth` each returned `200`; the established GAS deployment returned `success:true`, `V409`.
- **No client message was sent** during configuration, hardening, or defendant-template staging. This is not a replacement for the staff-confirmed write-bond → paperwork (B3/B5) or outbound dashboard iMessage (D2) production smokes.

## [Unreleased] — 2026-08-16 (Legacy e-sign retirement and DocuSeal binding gate)

### Changed
- **Legacy SignNow execution retired** — removed the Auto-CRM service modules, direct callbacks, direct signing-link delivery, release-stage generation, lifecycle poller/scheduler, and Node-RED tracking flows. Historical provider fields remain read-only for existing records.
- **DocuSeal packet creation is fail closed** — a new submission now requires validated Match, bound BondCase, explicit OSI/Palmetto surety, assigned POA in the matching inventory tier, canonical recipient name/email, and a new packet ID. Packet-time identity, recipient, case, POA, and financial overrides are rejected.
- **Signed-record safety** — packet ID collisions now return a conflict instead of replacing an existing packet version.

### Verified
- Focused DocuSeal service tests passed (**19**); commit `00c112c` deployed successfully through Hetzner workflow `31976473879`.
- Public probes returned `200` for Auto-CRM, stable factory health, DocuSeal, school, paperwork, and Postiz `/auth`.
- The platform is **not** marked production-hardened: staff workflow smoke tests and historical secret rotation remain open, and the strict local secrets check has no production environment files to inspect.

## [Unreleased] — 2026-08-16 (August wave leftover guards)

### Changed
- **East Baton Rouge and Jefferson Parish, LA fail-closed** — removed residential stealth, Cloudflare/disclaimer browser walks, TLS fingerprinting, and name-derived `EBR_` / `JEF_` booking fallbacks.
- **Lafayette Parish, LA fail-closed** — captcha-gated 365Labs path no longer probes Azure with TLS off, opens a browser, or invents `LAF_` keys.
- **Ascension, Caddo, Livingston, and Ouachita Parish fail-closed** — speculative `/api/...` inmate endpoints are no longer fetched.
- **Dashboard source-state registry** — those Louisiana jobs plus already-gated Forsyth (NC), Madison (AL), and Mobile (AL) are explicit `fail_closed` labels.
- **Indemnitor save** — an unknown or ambiguous booking number no longer inserts a stub `prospective_bonds` card. The indemnitor is saved `unlinked`. Save & Do Paperwork does not open DocuSeal from an unlinked save.
- **OCV inmate parser** — Lincoln and other OCV counties now require a source `inmateID` and booked date/time. A Mongo `_id` is not accepted as a booking number.

## [Unreleased] — 2026-08-15 (Verified-public scraper health review)

### Documented
- **No working scraper implementation changed** — aggregate-only checks covered every `verified_public` path without writers, scoring, alerts, persistence, or PII output. Bossier, Tangipahoa, St. Mary, Lee (AL), Marshall, Etowah, and Rankin emitted records with source booking keys in the bounded checks. Putnam remained source-reachable but exceeded the bounded budget; Randall and St. Clair emitted zero records without exceptions, with St. Clair’s ordinary direct source check receiving HTTP `403`. These are observability findings only; no scraper was disabled, patched, or reclassified.

## [Unreleased] — 2026-08-15 (Connecticut judicial-docket guard)

### Changed
- **Connecticut court dockets fail-closed** — the Statewide, Bridgeport, Hartford, New Haven, and Stamford judicial-docket jobs now stop before source access. A criminal docket number and hearing date are not a source-issued arrest booking identifier or arrest-time contract, so court cases cannot be emitted as arrest intelligence.

## [Unreleased] — 2026-08-15 (South Carolina source-contract guard batch)

### Changed
- **Fourteen South Carolina county paths fail-closed** — Anderson, Bamberg, Beaufort, Berkeley, Greenville, Horry, Jasper, Kershaw, Laurens, Lee, Marion, Saluda, Union, and York now make no source request and emit no records while their complete public broad-listing contracts remain unproven. Existing guards are reflected in the dashboard source-state registry; the remaining modules use the shared pre-scrape gate.

## [Unreleased] — 2026-08-15 (North Carolina source-contract guard batch)

### Changed
- **Ten North Carolina county paths fail-closed** — Caldwell, Chatham, Cumberland, Davidson, Guilford, Halifax, Randolph, Scotland, Union, and Wake now make no source request and emit no records while their complete public broad-listing contracts remain unproven. Union’s existing P2C guard is now represented explicitly in the dashboard source-state registry; the other nine paths use the shared pre-scrape gate.

## [Unreleased] — 2026-08-15 (Tennessee source-contract guard batch)

### Changed
- **Shared pre-scrape source-contract gate** — `BaseScraper.run()` now exits before any source fetch, scoring, persistence, broadcast, or alert when a county explicitly declares `SOURCE_CONTRACT_VALIDATED = False`.
- **Nine Tennessee county paths fail-closed** — Davidson, Hamilton, Knox, Montgomery, Rutherford, Shelby, Sumner, Williamson, and Wilson now make no source request and emit no records while their complete public broad-listing contracts remain unproven. The decision is documented in the 947-scope matrix and Tennessee validation note.

## [Unreleased] — 2026-08-15 (Orleans and St. Tammany source-contract guards)

### Changed
- **Orleans Parish, LA fail-closed** — removed speculative endpoint probing, browser navigation, TLS-bypass behavior, and name-derived booking fallbacks after the reachable OPSO public origin did not establish a compliant broad booking roster through ordinary access.
- **St. Tammany Parish, LA fail-closed** — retired the prior `/api/inmates/recent` path after it returned public HTTP `403`. The registered scraper now makes no source request and emits no records until a booking-safe broad roster contract is revalidated.

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
