# ShamrockLeads — True Status

> **Last verified:** 2026-08-28
> **VPS:** Hetzner **CCX33** (8 dedicated vCPU / 32 GB RAM) as of 2026-08-13 — compose ceilings raised (`docs/runbooks/vps-ccx33-resize.md`). Root disk was **not** grown with the type change (still ~38 GB); grow to 160–240 GB in the Cloud Console.
> **Repo:** `Shamrock2245/shamrock-leads` · branch `main`  
> **Product URL:** `https://leads.shamrockbailbonds.biz`  
> **Role:** Bond **Auto-CRM** pillar of **Shamrock’s Platform** (not Bail School LMS)  
> **Platform:** `docs/PLATFORM.md` · **Prod checklist:** `docs/ECOSYSTEM_PROD_CHECKLIST.md`  
> **Multi-state plan:** `docs/MULTI_STATE_SCRAPER_ROADMAP.md`  
> **Proxy stack:** `docs/APE_INTEGRATION_GUIDE.md` · `docs/SELF_HOSTED_PROXY_ARCHITECTURE.md`  
> **BlueBubbles versions:** `docs/BLUEBUBBLES_VERSIONING.md` (App v2 ≠ Server; Server latest = 1.9.9)  
> **DocuSeal Server:** `https://sign.shamrockbailbonds.biz` (Template ID 1 OSI · 16/16 tests passing)  
> **Postiz Social & MCP:** `https://social.shamrockbailbonds.biz` (`/auth` 200 · `/api/mcp` 401 without key — backend repaired 2026-08-12 after Mastra 1600-col crash)  
> **OpenCut Editor:** `https://edit.shamrockbailbonds.biz` (VPS Docker `shamrock-opencut` · nginx → `:5320`)  
> **All hosts:** [`docs/SUBDOMAINS.md`](./docs/SUBDOMAINS.md) · `config/subdomains.py` · `python scripts/check_subdomains.py --live`

---

## Gate update (2026-08-28, operator)

- **D2 closed:** Brendan confirmed Super CRM dashboard iMessage send is working.
- **C3 deferred:** historical secret rotation is not being treated as a formal blocker; rotation will happen shortly. Still `[ ]` until keys actually turn.
- **B3 open:** path is believed to work; no real BondCase is available yet for the documented smoke. Re-run when the next validated case exists.
- **B5 still open.** Stage 2 production-hardened is **not** claimed. Automations stay `review` for 7 days after D2 (clock starts 2026-08-28).

## Clipboard / docs alignment (2026-08-28)

Sibling `shamrock-bail-portal-site` staff case lightbox no longer calls retired Wix packet-create. Super CRM remains the only DocuSeal issuer. `SECURITY.md` and public blog copy no longer describe SignNow as the active signing path. `docs/ECOSYSTEM_PROD_CHECKLIST.md` C4 now records the portal factory as **V468 / @468**.

This does **not** close B3/B5, C3, or D2 and does not mark Stage 2 production-hardened.

## Confirmed Lee booking intake deployment (2026-08-21)

Commits [`f1a151c`](https://github.com/Shamrock2245/shamrock-leads/commit/f1a151c1a1e297ddfecfb6cf41023213d96a4b01) and cache-safe follow-up [`bb1a1a8`](https://github.com/Shamrock2245/shamrock-leads/commit/bb1a1a83b0e478ee1773e774f306b04f3e15aef0) deployed successfully through Hetzner workflows [`32487310626`](https://github.com/Shamrock2245/shamrock-leads/actions/runs/32487310626) and [`32487698386`](https://github.com/Shamrock2245/shamrock-leads/actions/runs/32487698386). Palantir now has a staff-gated **Confirmed Booking Intake** workspace for one official HTTPS Lee County booking URL. It projects only published booking facts into a 15-minute server-side preview, requires staff acknowledgement plus exact booking-number re-entry, and then creates or refreshes an **ArrestLead only**.

The endpoint accepts only the official Lee host and numeric booking ID, strips address, DOB, contact, relative, household, raw-response, and enrichment data, and fails closed for unsupported URLs, mismatched source booking numbers, expired/consumed previews, cross-jurisdiction booking collisions, unavailable canonical deduplication guards, and protected downstream records. It does not create a Defendant, Indemnitor, Match, BondCase, Packet, Signature, Payment, surety/POA action, client contact, or `full_auto` behavior.

Focused booking-intake and existing Palantir tests passed (**11**); JavaScript syntax and diff checks passed. Final probes returned `200` for Auto-CRM `/health`, Palantir JavaScript and CSS with cache-safe `v=6` references, DocuSeal, Bail School, paperwork, and Postiz `/auth`. The stable GAS URL remains unavailable in this clean checkout, so `?action=health` was not re-probed. The strict local secrets check remains red because production environment files and sibling repositories are intentionally absent; it is not treated as green. No real booking record, CRM record, bond, paperwork, signature, payment, or client message was created during implementation or verification.

This release does **not** close B3/B5, C3, or D2, does not mark the platform production-hardened, and does not alter the production checklist's human-gated items.

## Palantir Command HUD deployment (2026-08-20)

Commit `913d4ce` deployed successfully through the Hetzner workflow [`32397812988`](https://github.com/Shamrock2245/shamrock-leads/actions/runs/32397812988). The Palantir workspace now serves a reactor-style, read-only command HUD: exact CRM entity resolution with node inspection and visual layer filters; OSIRIS county-filtered feed refresh and stream-to-map focus; SPECTRA provider-gated scan states; and a CRM-bounded dossier view. The existing Palantir endpoints and their fail-closed server behavior were retained; no bond, paperwork, signature, payment, outreach, surety, or GAS workflow changed.

Focused Palantir fail-closed tests passed (**6**); the controller parsed successfully; and `git diff --check` was clean. A post-deployment `/health` probe returned `{"status":"ok","engine":"fastapi","total_arrests":31802}`. The deployed v5 Palantir CSS and JavaScript assets returned `200`; DocuSeal, Bail School, paperwork, and Postiz `/auth` also returned `200`. The stable GAS URL remains intentionally unavailable in this clean checkout, so `?action=health` was not re-probed. The strict local secrets check remains red because production environment files and sibling repositories are intentionally absent; it is not treated as green.

This release does **not** close B3/B5, C3, or D2, does not enable `full_auto` outreach, and does not mark the platform production-hardened. No person-level record, intake, bond, packet, signature, payment, or client contact was created during implementation or verification.

## VPS resize (2026-08-13)

## Paperwork gap inventory (code audit 2026-08-13)

| Spec slice | Repository truth |
|---|---|
| Write Bond → DocuSeal | Implemented in the finalize path with `send_email=false`, surety-specific template resolution, multi-submitter records, and per-party branded links; a real validated-case staff smoke remains required. |
| Party portal | PIN lookup, ID OCR, address review UI, and role-specific DocuSeal launch exist. The unsafe ID-scan → unassigned packet shortcut is now fail-closed; packets must originate from a validated BondCase. Selfie enforcement and the full staff exception ceremony remain unfinished. |
| Completion truth | DocuSeal webhook and enabled 30-minute DocuSeal poller update packet state; completed PDF Drive archival exists but still requires production OAuth/folder verification. |
| Chase | Review-mode queue and staff resend/status endpoints exist. Client nudges remain human-gated; `full_auto` was not enabled. |
| E-sign provider | DocuSeal-only for active paperwork. SignNow is retired from the workflow; historical fields remain read-only for old records only. |
| Remaining locked-spec gaps | Multi-indemnitor production walkthrough, staff second-PIN exception modal/audit, office kiosk walkthrough, dual-role FAQ initials, and collateral receipt serial OCR. No serial is inferred or invented. |

Hetzner type is now **CCX33** (8 dedicated vCPU / 32 GB RAM). Compose ceilings for the scraper, dashboard, Obscura, OSINT, Postiz, Traccar, and OpenCut were raised in-repo and applied live (`docs/runbooks/vps-ccx33-resize.md`). `SCRAPER_MAX_CONCURRENT` stays **8** until one full cycle is green. **Root disk is still ~38 GB** — grow it to 160–240 GB in the Cloud Console (CPU/RAM resize does not grow the volume). Chromium launchers now share lean flags (`scrapers/chromium_flags.py`). Paperwork MVP: staff copy/send **indemnitor + defendant** branded links (`/sign/{packet}/{role}`). OSINT worker key is minted and live (worker `/status` 200); Toutatis has an Instagram session. SPECTRA uses Hudson Rock (free), not HIBP. Hunter.io is wired for attorney email finder.

## Initial DocuSeal iMessage delivery configuration (2026-08-18)

The narrowly scoped initial DocuSeal BlueBubbles iMessage exception is **enabled for indemnitors and co-indemnitors only**. The approved indemnitor/co-indemnitor and defendant templates are stored through the protected Automations editor and require `{signing_link}`. Defendant delivery remains **disabled** (`include_defendant=false`); staging defendant copy does not contact any client.

Commit `cd74b00` deployed the hardening controls through Hetzner workflow `32177618588`. Automatic delivery is now packet-bound, iMessage-only, one-time, and fail-closed: it accepts only direct HTTPS DocuSeal signer links on `sign.shamrockbailbonds.biz`, exact role/external-ID metadata, and one delivery evaluation per packet. For a defendant, a future packet must also include an exact `Defendant_ID`-bound, staff-recorded contact-verification and iMessage-opt-in authorization snapshot. Manual delivery is now staff-session-only and requires an exact role-bound active DocuSeal signer; it does not fall back to generic packet phones or return signing links in responses. No client message was sent during hardening or template staging.

Focused service, automation, paperwork, and portal tests passed (**62**). Public Auto-CRM, DocuSeal, school, paperwork, Postiz `/auth`, and stable GAS health checks returned `200`; the first shell GAS redirect timed out, but the same unchanged stable endpoint returned `success:true`, `V409` through the browser. The strict local secrets check remains unable to pass in this clean checkout because production environment files are intentionally unavailable; no secrets were changed. This work does not satisfy the required B3/B5 or D2 human production smokes.

## Ohio source-contract guard deployment (2026-08-19)

Commit `3b3bee0` deployed successfully through Hetzner workflow `32290521634`. Clermont, Clinton, and Huron County, Ohio are now visible as `fail_closed` source-contract guards in the dashboard registry. Each job stops before source access, scoring, persistence, alerts, outreach, matching, paperwork, signatures, payments, or bond-writing activity. Ohio is not asserted as an OSI or Palmetto writing footprint.

Focused source-contract, scheduler, registry, source-key, and documentation tests passed (**32**, plus six subtests). Post-deploy public probes returned `200` for Auto-CRM `/health`, DocuSeal, Bail School, paperwork, and Postiz `/auth`. The stable GAS URL is intentionally unavailable in this clean checkout, so its `?action=health` check was not re-run locally. The strict local secrets check remains red because production `.env` files and sibling repositories are unavailable; no secrets were added, exposed, or changed. No person-level record, intake, bond, packet, signature, payment, or client contact was created.

This release does not close B3/B5, C3, or D2, does not enable `full_auto` outreach, and does not mark the platform production-hardened.

## Dashboard completeness audit deployment (2026-08-19)

Commit `3c46234` deployed successfully through Hetzner workflow `32268562642`. The staff Client Portal seven-day check-in card now uses the live `checkins_7d` metric and its rendered DOM target; it no longer remains blank because of the former identifier mismatch. The FTA Level 3 surrender interface no longer names the retired SignNow workflow and now truthfully states that no e-sign packet is created, staff documentation review is required, and an indemnitor iMessage is reported only when delivery succeeds. Focused dashboard/portal/source-contract tests passed (**23**) and updated JavaScript parsed cleanly. Post-deploy public checks returned `200` for Auto-CRM health, DocuSeal, Bail School, paperwork, and Postiz `/auth`.

The required strict local secrets check remains unavailable in this clean checkout because production environment files and sibling production repositories are intentionally absent; it was not treated as green. No synthetic intake, bond, packet, signature, payment, or outbound client message was created. This dashboard correction does not close B3/B5, C3, or D2 and does not mark the platform production-hardened.

## Production audit update (2026-08-12)

| Surface / gate | Verified state |
|---|---|
| Public hosts | Direct bounded probes returned `200` for leads `/health`, school `/`, DocuSeal `/`, paperwork `/`, Postiz `/auth`, and OpenCut `/`; the canonical portal GAS `?action=health` returned `success:true`, `V409`. |
| Public Bail School pricing (C2) | **Verified**: current JSON-LD lists the 120-hour course at `$649`; the retired course title and `$699` were absent from the fetched page source. |
| Deployment integrity | Latest `Deploy to Hetzner` run for `df24815` timed out at the 30-minute SSH command budget after the core image build. The workflow time budget is corrected in the pending commit; it is not yet a live deployment result. |
| Human-gated bond / outreach evidence | **Still required**: one staff-confirmed write-bond → paperwork event (B3) and one staff-approved outbound dashboard iMessage (D2). No synthetic cases, paperwork, or client messages were created for this audit. |
| Historical secret rotation (C3) | **Still required**: the portal rotation guide confirms prior credentials existed in git history. No vendor key was rotated without Brendan’s approval. |

## August 14–16 source-safety wave review (2026-08-16)

The 2026-08-14 through 2026-08-16 Shamrock2245 commit wave (fail-close campaign, recon matrix, and verified-public parsers) does **not** need a wholesale rollback. Keepers: Bossier, Tangipahoa, St. Mary, Lee/Marshall/Etowah/St. Clair AL, Rankin. York’s parser is kept but remains `fail_closed` until ordinary access is revalidated. Lincoln NC and Miami-Dade still emit and stay `unverified` until their source-state rows are promoted on purpose.

Leftover Louisiana jobs from that window that were still scheduled without a contract gate are now fail-closed:

| Parish | Residual risk | Action |
|---|---|---|
| East Baton Rouge | Residential stealth + disclaimer browser walk + `EBR_` name-hash keys | Fail-closed 2026-08-16 |
| Jefferson | Stealth TLS fingerprinting + browser fallback + `JEF_` name-hash keys | Fail-closed 2026-08-16 |
| Lafayette | Captcha portal + TLS-disabled probes + `LAF_` name-hash keys | Fail-closed 2026-08-16 |
| Ascension, Caddo, Livingston, Ouachita | Speculative `/api/...` endpoints, no booking-time contract | Fail-closed 2026-08-16 |

Dashboard `SCRAPER_SOURCE_STATES` now also marks already-gated **Forsyth (NC)**, **Madison (AL)**, and **Mobile (AL)** as `fail_closed`. Unknown booking # on New Indemnitor no longer invents a stub prospective bond; Save & Do Paperwork only proceeds when the indemnitor linked to an existing arrest or bond.

OCV listing parsers (Lincoln NC and siblings) now require a source `inmateID` and booked date/time; a Mongo `_id` is not a booking number.

Bond Intelligence write-desk, Florida statutory premium, OpenCut overlay, and Holehe/HIBF OSINT chips from the same dates stay. They are not rolled back.

## Verified-public scraper health review (2026-08-15)

All ten paths currently marked `verified_public` were checked in disposable aggregate-only subprocesses with no writer, scoring, alert, broadcast, persistence, or PII output. Bossier, Tangipahoa, St. Mary, Lee (AL), Marshall, Etowah, and Rankin emitted records with non-empty source booking keys. Putnam’s configured source returned ordinary HTTP `200`, but its scraper exceeded both bounded time budgets; Randall returned an empty result while its configured source returned HTTP `200`; St. Clair returned an empty result and its ordinary direct source request returned HTTP `403`. These three observations are **monitoring findings only**. No working scraper class, shared base, scheduler registration, source-state label, endpoint, timeout, or deployment configuration was modified.

## Connecticut judicial-docket guard deployment (2026-08-15)

Commit `d8295ea` deployed successfully through **Deploy to Hetzner** run `31903650843`. The Statewide, Bridgeport, Hartford, New Haven, and Stamford Connecticut judicial-docket jobs now stop before any source request. The court workflow previously converted judicial docket numbers and hearing dates into arrest-record fields; those are not source-issued arrest booking identifiers or arrest-time fields. Scraper Health now reports the five court-docket scopes as `fail_closed`.

The Connecticut validation retained no case or person data and confirmed that the ordinary metadata request terminated at TLS transport; more importantly, the source category itself is court-docket—not arrest-listing—data. The focused source-contract, source-key, registry, evidence, and documentation suite passed **33 tests**. Public post-deploy probes returned `200` for leads `/health`, DocuSeal, Bail School, Paperwork, and Postiz `/auth`. The stable factory URL and production local environment files remain unavailable in this checkout, so the GAS health probe and strict local secrets check were not locally proven.

## South Carolina source-contract guard deployment (2026-08-15)

Commit `d1578b8` deployed successfully through **Deploy to Hetzner** run `31903358690`. Anderson, Bamberg, Beaufort, Berkeley, Greenville, Horry, Jasper, Kershaw, Laurens, Lee, Marion, Saluda, Union, and York are explicitly `fail_closed`; each emits no records before source retrieval until its county-specific broad-listing contract is revalidated. Existing guard modules are now represented in Scraper Health; the remaining ten modules use the deployed shared pre-scrape contract gate.

The South Carolina metadata-only validation documented that none of the fourteen county paths proved all required broad-listing facts through ordinary access: complete displayed name, source-issued immutable booking/inmate key, booking or arrest date/time, and bounded pagination. The focused source-contract, source-key, registry, evidence, and documentation suite passed **32 tests**. Public post-deploy probes returned `200` for leads `/health`, DocuSeal, Bail School, Paperwork, and Postiz `/auth`. The stable factory URL and production local environment files remain unavailable in this checkout, so the GAS health probe and strict local secrets check were not locally proven.

## North Carolina source-contract guard deployment (2026-08-15)

Commit `9fa5e74` deployed successfully through **Deploy to Hetzner** run `31902868124`. Caldwell, Chatham, Cumberland, Davidson, Guilford, Halifax, Randolph, Scotland, Union, and Wake are now explicitly `fail_closed`; each returns no records before source retrieval until a county-specific broad-listing contract is revalidated. Union’s existing P2C guard is now explicitly represented in Scraper Health. The other nine county modules use the deployed shared pre-scrape contract gate.

The North Carolina metadata-only validation documented that none of the ten county paths proved all required broad-listing facts through ordinary access: complete displayed name, source-issued immutable booking/inmate key, booking or arrest date/time, and bounded pagination. The focused source-contract, source-key, registry, evidence, and documentation suite passed **31 tests**. Public post-deploy probes returned `200` for leads `/health`, DocuSeal, Bail School, Paperwork, and Postiz `/auth`. The stable factory URL and production local environment files remain unavailable in this checkout, so the GAS health probe and strict local secrets check were not locally proven.

## Tennessee source-contract gate deployment (2026-08-15)

Commit `65dcb37` deployed successfully through **Deploy to Hetzner** run `31902407069`. `BaseScraper.run()` now stops every explicitly unvalidated county before disk checks, source access, scoring, persistence, broadcasts, or alerts. Davidson, Hamilton, Knox, Montgomery, Rutherford, Shelby, Sumner, Williamson, and Wilson are now `fail_closed`; together with the prior Tennessee guards, **20 Tennessee county paths** are non-emitting pending county-specific public contract validation. Putnam remains `verified_public`; the non-county TnCIS scope remains `unverified`.

The Tennessee metadata-only validation documented that none of the nine county paths proved the complete broad-listing contract: complete displayed name, source-issued immutable booking/inmate key, booking or arrest date/time, and bounded pagination through ordinary access. The focused source-contract, source-key, registry, evidence, and documentation suite passed **30 tests**. Public post-deploy probes returned `200` for leads `/health`, DocuSeal, Bail School, Paperwork, and Postiz `/auth`. The stable factory URL and production local environment files remain unavailable in this checkout, so the GAS health probe and strict local secrets check were not locally proven.

## Orleans and St. Tammany source-contract guard deployment (2026-08-15)

Commit `7c0dd10` deployed successfully through **Deploy to Hetzner** run `31901849486`. Orleans now makes no speculative endpoint, browser, TLS-bypass, or name-derived booking request after the reachable OPSO origin did not establish a compliant booking-safe roster. St. Tammany now makes no source request after its previous `/api/inmates/recent` endpoint returned public HTTP `403`. Both registered jobs are explicitly `fail_closed`, emit no records, and appear as guarded in Scraper Health until their county-specific broad-listing contracts are revalidated.

The focused Louisiana, source-key, registry, evidence, and documentation suite passed **28 tests**. Public post-deploy probes returned `200` for leads `/health`, DocuSeal, Bail School, Paperwork, and Postiz `/auth`. The stable factory URL and production local environment files remain unavailable in this clean checkout, so GAS `?action=health` and strict secrets verification were not locally proven; neither limitation changes the deployed source-guard result.

## Calcasieu source-contract guard deployment (2026-08-15)

Commit `7fb306e` deployed successfully through **Deploy to Hetzner** run `31901602875`. The previous Calcasieu `/api/inmates/roster` path returned public HTTP `404`; the registered job is now explicitly `fail_closed`, performs no source request, and emits no records until the current public roster API and booking-safe broad-listing fields are revalidated. Scraper Health and the 947-scope matrix now show this runtime source decision. Beauregard remains a non-registered candidate only: its ordinary TLS transport was not reproducibly available from this environment, so it was not scaffolded or promoted.

The Calcasieu guard, source-contract inspector, and focused registry/evidence suite passed **26 tests**. Public post-deploy probes returned `200` for leads `/health`, DocuSeal, Bail School, Paperwork, and Postiz `/auth`. The stable factory URL was not present in this clean checkout, so GAS `?action=health` was not re-probed; the strict local secrets check likewise remains unavailable without production `.env` files and sibling repositories.

## Complete source-contract reconnaissance deployment (2026-08-15)

Commit `2ebcc01` deployed successfully through **Deploy to Hetzner** run `31898840660`. It adds a versioned, non-PII source-contract matrix for **947 scopes**: all 942 Census county-equivalents in the ten-state repository footprint plus five registered non-county scopes. The matrix makes the distinction explicit: only 10 rows retain existing deployed `verified_public` state, 29 retain existing deployed `fail_closed` guards, 2 are candidate-public listings that still require county-specific implementation validation, and the remainder remain `recon_only` or `unverified`. No source state, scheduler registration, parser, writer, alert, payment, or bond action was promoted by this documentation deployment.

The repository now includes reproducible inventory, evidence, matrix-generation, and documentation-contract tests. The focused recon/registry/source-key suite passed **23 tests**. The broader suite collected after its missing declared sandbox dependencies were installed and reported **626 passing**, but retained **13 failures and 1 error** in unrelated Google Drive, paperwork-route, Sarasota helper, and instant-indemnitor paths; those were not changed by this scoped work and are not represented as green.

Post-deploy public probes returned `200` for leads `/health`, DocuSeal, Bail School, Paperwork, and Postiz `/auth`. The stable factory URL remains intentionally unavailable in this clean checkout, so GAS `?action=health` was not re-probed. The strict secrets script remains unable to pass locally because production `.env` files and sibling repositories are absent; neither local limitation is evidence of a production secret or factory regression.

## Scraper registry integrity deployment (2026-08-15)

Commit `99547b7` deployed successfully through **Deploy to Hetzner** run `31897337465`. The canonical registry remains **358 state-qualified labels**, and static contract coverage now verifies that every registered label has both a local scraper module and a `main.register_scrapers` entry. The guard is intentionally source- and network-free; it does not claim that every registered county is producing records.

Hendry now drops OCV rows without the source-issued `inmateID`. Monroe now drops rows without an MNI, official offense number, or official CAD number, rather than hashing a name or date into a booking key. This keeps source rows that lack a valid immutable identifier out of the `County + Booking_Number` write path.

Post-deploy public probes returned `200` for leads `/health`, DocuSeal, Bail School, Paperwork, and Postiz `/auth`. The stable factory URL is intentionally redacted from this checkout and no local `GAS_WEB_APP_URL` was present, so GAS `?action=health` was **not** re-probed here. Likewise, `scripts/check_ecosystem_secrets.py --strict` cannot pass in this clean clone because production `.env` files and sibling repositories are absent; that local result is not evidence of a production secret regression.

## What “Auto-CRM” means here

After a **phone number** (and usually defendant/county) enters the system, the bond lifecycle should run with **minimal human intervention**, except risk/match gates:

```
Phone / arrest lead → outreach sequences → intake → match (human on ambiguity)
  → paperwork → payment → active bond → court/GPS/FTA → close
```

**BlueBubbles (iMessage)** is the preferred consumer rail for outreach. Office Mac runs **Server v1.9.9** (latest). Desktop reply visibility (webhook parse + `message/query` poll + thread hydrate) fixed **2026-07-26** — see `CHANGELOG` 2.17.0. App **v2.0.0+89** is the consumer *client* only; do not confuse with server upgrades (`docs/BLUEBUBBLES_VERSIONING.md`).

**Bail School** is a **separate P&L** (`shamrock-bail-school`). Leads may share brand, Slack, and secrets hygiene — not course progress state.

---

## Scale (authoritative — 2026-08-14)

| State | Registered scrapers | Code path | Notes |
|-------|--------------------:|-----------|-------|
| **GA** | **85** | `scrapers/counties_ga/` | Gwinnett and Fulton fail-closed guards are deployed; six audited legacy P2C paths also fail closed under `0de5f79`. Five JailTracker wrappers are deployed fail closed under `0a75169`; see `docs/LEGACY_P2C_SOURCE_SAFETY.md` and `docs/JAILTRACKER_SOURCE_SAFETY.md`. |
| **FL** | **67** | `scrapers/counties/` | Miami-Dade ArcGIS repair, Broward guard, and Sarasota’s third-party/proxy/CAPTCHA source-safety override are deployed 2026-08-14. Sarasota run `31843789326` succeeded; its public leads `/health`, sign, school, paperwork, and social `/auth` probes were healthy. Seven inherited JailTracker wrappers are deployed fail closed under `0a75169`; see `docs/JAILTRACKER_SOURCE_SAFETY.md`. Per-source persistence and alert telemetry remain pending. |
| **NC** | **60** | `scrapers/counties_nc/` | Durham fail-closed guard and Lincoln’s official OCV repair are deployed; seven audited legacy P2C paths also fail closed under `0de5f79`. Production persistence and alert telemetry remain pending. |
| **SC** | **46** | `scrapers/counties_sc/` | York source-faithful parser repair is deployed; Lee and Lexington legacy P2C paths fail closed under `0de5f79`. Anderson, Cherokee, Colleton, Kershaw, and Laurens Zuercher guards are deployed in `7718bf8`; Chester and Greenwood JailTracker wrappers are deployed fail closed under `0a75169`. See `docs/SC_ZUERCHER_SOURCE_SAFETY.md` and `docs/JAILTRACKER_SOURCE_SAFETY.md`. Per-county persistence and alert telemetry remain pending. |
| **TX** | **34** | `scrapers/counties_tx/` | Randall is source-validated; Bell, Ellis, Guadalupe, and Jefferson fail-closed guards deployed 2026-08-14 with public hosts healthy. |
| **TN** | **22** | `scrapers/counties_tn/` | Putnam remains source-validated. Blount, Bradley, Sevier, Washington, Maury, Robertson, Hamblen, Bedford, Coffee, Lincoln, and Giles are deployed fail-closed guards pending compliant source contracts; public service checks were healthy. Per-source Mongo upsert and alert telemetry remain pending. |
| **AL** | **16** | `scrapers/counties_al/` | Lee, Marshall, St. Clair, and Etowah are deployed after bounded official-roster smokes. Baldwin, Cullman, DeKalb, Houston, Jackson, Jefferson, Morgan, Shelby, Tuscaloosa, Madison, Mobile, and Montgomery are deployed fail-closed guards where no compliant source contract exists; public service checks were healthy. Per-scraper Mongo/alert evidence remains pending. |
| **LA** | **13** | `scrapers/counties_la/` | Tangipahoa, St. Mary, and Bossier remain `verified_public`. Calcasieu, Orleans, and St. Tammany fail-closed 2026-08-15. East Baton Rouge, Jefferson, Lafayette, Ascension, Caddo, Livingston, and Ouachita fail-closed 2026-08-16. Per-parish Mongo/alert evidence remains pending. |
| **MS** | **9** | `scrapers/counties_ms/` | Rankin is source-validated and deployed. DeSoto, Forrest, Harrison, Hinds, Jackson, Jones, Lauderdale, and Madison are deployed fail-closed guards; Adams, Lafayette, Lowndes, Oktibbeha, and Warren remain recon-only. Public service checks were healthy; per-county Mongo/alert evidence remains pending. |
| **CT** | **6** | `scrapers/counties_ct/` | CT DOC fail-closed guard deployed 2026-08-14 after official BITS BOT rejection; public hosts are healthy and Statewide dockets plus municipal paths remain registered. |
| **OH** | **3** | `scrapers/counties_oh/` | Clermont, Clinton, and Huron are deployed `fail_closed` source-contract guards. They emit no records and make no OSI/Palmetto or bond-writing assertion. |
| **Total** | **361** | `dashboard/extensions.py` → `REGISTERED_COUNTIES` | Labels: `County (ST)` · drives Scraper Health + Multi-State Ops UI |

**Identity rule:** non-FL job IDs are `scraper_<st>_<county>` (e.g. `scraper_nc_mecklenburg`, `scraper_tn_davidson`). FL keeps `scraper_lee` for dashboard compatibility. CLI: `python main.py tn_davidson` / `tx_bexar` / `la_orleans` / `ct_doc`.

**Shared bases (recent):** `scrapers/dcn_base.py` (DevExpress), `scrapers/ocv_inmates_base.py` (OCV S3 inmates.json).

---

## Code on `main` (recent, implemented)

| Area | Status |
|------|--------|
| **361** registered scopes (10 established states + 3 guarded OH pilots), scoring, Slack, Mongo | ✅ Ohio guards are deployed no-network/non-emitting; existing source-specific Mongo/Slack evidence remains pending and no Ohio source is claimed productive |
| Multi-state `BaseScraper.state` + scheduler `_resolve_job_id` | ✅ |
| Platform bases: Zuercher, Southern SW, P2C, JailTracker, New World, Kologik, Odyssey, **DCN**, **OCV** | ✅; shared Southern Software source-issued identity safeguard is deployed and public hosts are healthy. |
| FastAPI Super CRM (tabs, lifecycle, intake, etc.) | ✅ |
| **Multi-State Ops** tab + `/api/ops/*` (registry-first KPIs, live feed, 10 established states + guarded OH labels) | ✅ · live registry |
| **Bond Intelligence** tab + `/api/bond-intelligence`, multi-state stats | ✅ |
| Lead Explorer **state** column + filter (10 established states + guarded OH labels) | ✅ |
| Lead Explorer live sort (`scraped_at`) + auto-refresh + county labels | ✅ |
| Scraper status multi-state join (`County (ST)` ↔ bare names) | ✅ |
| **Scraper Health source-contract state** (`Verified public` / `Fail closed` / `Unverified` / `History only`) | ✅ deployed 2026-08-15 · independent of run health; guarded sources show no manual-run action |
| **Autonomous Proxy Engine (APE)** Warren + S5W2C + Stormsia | ✅ code · hub live |
| Hub APIs: `/api/crm/health`, `/overview`, `/pipeline`, `/search` | ✅ |
| Omnibar → CRM search | ✅ |
| Mongo upsert validation + `last_seen`/`scraped_at` + M0 oldest-first retention | ✅ 2026-08-04 |
| Superadmin **Data Hygiene** (`/api/admin/hygiene/*` + UI) — purge test junk, repair mismatches | ✅ 2026-08-04 |
| Webhooks fail-closed without secrets | ✅ |
| Ecosystem secrets checklist | `scripts/check_ecosystem_secrets.py` |
| Super CRM docs | `docs/SUPER_CRM.md`, `docs/ECOSYSTEM.md` |
| SC / NC / CT registries | `docs/SC_COUNTY_REGISTRY.md`, `docs/NC_COUNTY_REGISTRY.md`, `docs/CT_COUNTY_REGISTRY.md` |
| **Surety realignment (July 2026)** | ✅ |
| **Bond check-in A+C** — transparent portal GPS + condition policy | ✅ code |
| **Traccar GPS (B)** continuous via in-stack Traccar Client / OsmAnd | ✅ rewired |
| **Family Tree** tab + `/api/family-tree/*` | ✅ code |
| **NC waves 4–7** (Pitt, DCN Moore/Lee/Halifax/Richmond, Craven, Randolph, Catawba, Carteret, Caldwell, Chatham/Stanly OCV, Orange PDF) | ✅ code 2026-08-04 · NC **47** |
| **CT harden** (curl_cffi dockets + DOC A–Z list-first) | ✅ code 2026-08-04 |
| **Mem0 long-term memory** for Shannon iMessage (GAS-compatible `MEMO_API_KEY`) | ✅ code 2026-08-04 · set env on VPS |
| **iMessage inbound replies** on desktop (webhook + poll + hydrate) | ✅ code · BB ops ongoing |
| Scraper **Run** always JSON + county/state matching | ✅ |

---

## Live prod verification (2026-07-23)
### Session follow-up (2026-07-23)

| Fix | Result |
|-----|--------|
| Bradford URL → `smartweb.bradfordsheriff.org` + direct-first | ✅ 3 records |
| Dixie URL → HTTPS SmartCOP + direct-first | ✅ 3 records |
| Taylor URL → `:8989/SmartWEBClient` | ✅ 3 records |
| SmartCOP base: direct before proxy | ✅ |
| Defendants `normalize/batch` (Lee/Collier + 300) | ✅ **0 → 594** defendants |
| Gilchrist | ⏳ no public DNS/host found |
| DocuSeal API/templates | must verify `DOCUSEAL_API_KEY`, `DOCUSEAL_TEMPLATE_ID_OSI`, and `DOCUSEAL_TEMPLATE_ID_PALMETTO` in production |

### Session follow-up (2026-07-24 — Manus prod-hardening)

| Fix | Result |
|-----|--------|
| Monroe v2: rewrote against `data.keysso.net/api/arrests` JSON API (old ASP.NET dead) | ✅ **80 records** (no captcha/proxy) |
| Hillsborough: direct-first egress + form drift (SearchSortType + new fields) | ✅ **7 records** (direct HTTP, no proxy) |
| Lake: added SolveCaptcha reCAPTCHA v2 solver (token bypass dead) | ✅ code shipped (needs `SOLVECAPTCHA_KEY` run) |
| Marion: switched `btnSearch` → `btnRecentBookings` | ⚠️ AWS WAF blocks VPS IP intermittently |
| Bay: UniGUI session HandleEvent returns 401 | ⏳ needs deeper UniGUI reverse-engineering |
| Okeechobee: `/inmate-search` page is Wix shell, no public data source found | 🔴 blocked on upstream (no roster URL) |
| Gadsden: SmartWEB iframe → `69.21.72.195` server dead (empty reply) | 🔴 blocked on upstream |
| Gilchrist: DNS `smartcop.gilchristsheriff.com` NXDOMAIN | 🔴 blocked on upstream |
| Suwannee: SmartCOP server 500 on any search POST (upstream crash) | 🔴 blocked on upstream |
| Defendants `normalize/batch` × 7 runs | ✅ **594 → 3,211** defendants |

### Stage 2 hardening session (2026-07-24 cont.)

| Investigation | Result |
|---------------|--------|
| Bay County UniGUI: IIS 401 on HandleEvent (POST blocked, anti-scraping) | 🔴 blocked — server rejects all AJAX event requests from non-browser clients |
| Lake reCAPTCHA: `SOLVECAPTCHA_KEY` IS set (Hillsborough uses it), token solved but API rejects (server-side verify fails) | ⚠️ SolveCaptcha token rejected by LCSO API (domain/score mismatch) |
| Marion: AWS WAF still blocking VPS IP (403) | ⚠️ needs residential proxy egress |
| DocuSeal B5: `/api/paperwork/docuseal/templates` | ⏳ verify the two live DocuSeal templates from the production account |
| Defendants `normalize/batch` × 5 more runs | ✅ **3,211 → 4,580** defendants (108 repeat offenders) |

| Check | Result |
|-------|--------|
| `GET /health` | ✅ ok · **130,489 arrests** |
| `GET /api/crm/health` | ✅ **ok** |
| Integrations (GAS, Wix, DocuSeal, Twilio, Slack, BB, PIN, SECRET_KEY) | ✅ all true |
| GAS `?action=health` | ✅ `success` · version V409 |
| BlueBubbles Tailscale `100.102.10.86:1234` + `/api/imessage/status` | ✅ connected · `path_in_use: tailscale` · private_api · 1.9.9 · frp `:12434` backup |
| Monroe one-shot scrape (post-deploy) | ✅ 80 records |
| Hillsborough one-shot (post-deploy) | ✅ 7 records |
| DocuSeal template validation | ⏳ confirm OSI + Palmetto templates in production |
| Scraper fleet | ✅ **233 ok · 7 error** (FL: Bay, Gadsden, Gilchrist, Lake, Marion, Okeechobee, Suwannee) |
| Defendants collection | ✅ **4,580** (was 3,211) · 3.7% coverage |

**Bugfix shipped:** `init_bluebubbles()` re-bound `BB_SERVERS = {}`, so every `from … import BB_SERVERS` kept an empty dict and iMessage looked “unconfigured” even with env set. Now mutates in place (`clear` + `update`). Tests: `tests/test_bb_servers_init.py`.

## Honest gaps / ops

Track live cutover in **`docs/ECOSYSTEM_PROD_CHECKLIST.md`** (P0/P1). Summary:

| Item | Status |
|------|--------|
| NC **60 registered** / 100 goal — many still need first successful production scrape | ⏳ Multi-State Ops / scheduler; Durham fail-closed guard is deployed pending a public identity-safe source contract; DCN list partial (≤100/page); WAF metros (Wake/Guilford/Forsyth); more OCV app_ids |
| SC production depth (CAPTCHA/Cloudflare/proxy for Greenville family, etc.) | ⏳ Harden per `SC_COUNTY_REGISTRY` |
| GA remaining counties beyond registered set (85/159) | ⏳ Recon + wrappers. Gwinnett is intentionally fail closed pending a supported complete-identity bulk source. |
| TN (22 registered; Putnam deployed with public health green; Davidson/Knox historic success; Shelby TLS sensitivity) | ⏳ Deepen and obtain per-source Mongo/Slack telemetry; Sullivan remains recon-only |
| TX (34 registered; Randall deployed; legacy P2C wrappers need source refresh) | ⏳ Obtain per-source Mongo/Slack telemetry and refresh unreachable legacy P2C sources |
| AL (16 registered; Lee, Marshall, and St. Clair deployed with public host checks green) | ⏳ Obtain per-scraper Mongo/Slack telemetry and validate source health for existing Alabama jobs |
| LA (13 registered; Tangipahoa, St. Mary, Bossier `verified_public`; ten other registered parishes `fail_closed`) | ⏳ Obtain parish-specific Mongo/Slack telemetry for the three verified-public jobs |
| MS (9 registered; registry reconciled; five assessed counties remain recon-only) | ⏳ Validate existing source telemetry and wait for a supported public roster/export before adding uncovered counties |
| CT dockets + DOC | ⏳ CT DOC fail-closed guard deployed pending a supported booking-safe public source; do not claim CT DOC production writes or alerts. |
| Ohio pilot | ⏳ Clermont, Clinton, and Huron are deployed `fail_closed` only. Obtain county-specific source authorization, field-retention approval, and a booking-safe broad-listing contract before considering a no-write observation; do not claim Ohio writes, alerts, surety, POA, or bond activity. |
| BlueBubbles production reliability (office Mac + mesh) | ✅ Live (Tailscale primary, frp backup, BB 1.9.9, watchdog) |
| `ENV=production` + strong `SECRET_KEY` + `DASHBOARD_PIN` on VPS | ✅ |
| Atlas M0 512MB cap — oldest-first retention + hygiene tools | ✅ code 2026-08-04 · monitor growth |
| Gmail discharge / GCal / Drive OAuth | Env-gated (tokens present; exercise live paths) |
| FL error scrapers (upstream / WAF / captcha) | ⏳ Bay/Gadsden/Gilchrist/Okeechobee/Suwannee blocked; Marion WAF; Lake captcha-service. Miami-Dade ArcGIS repair is deployed but awaits Miami-specific production telemetry. |
| Defendants collection backfill | ⏳ ongoing normalize/batch |
| Local PDF stitcher full blank packet | ✅ folders: `surety-agnostic-shamrock/` + `osi/` + `palmetto/` · DocuSeal primary |
| Auto-CRM “phone only → fully autopilot” with explicit human gates | Product next (Phase 21) |
| `edit.shamrockbailbonds.biz` (OpenCut) | ✅ Live on VPS Docker (`shamrock-opencut` → `:5320`) · nginx no longer Tailscale |
| Hetzner deploy after each `main` push | GitHub Action `Deploy to Hetzner` |

### Session note (2026-08-04)

| Deliverable | Result |
|-------------|--------|
| NC waves 4–7 + shared `dcn_base` / `ocv_inmates_base` | ✅ NC **47** registered |
| CT DOC + Statewide docket harden | ⏳ CT DOC list-first path retired after public BITS BOT rejection; statewide docket remains separately registered. |
| Multi-State Ops / Health / stats **registry-first** live KPIs | ✅ |
| Mongo data-flow gaps + M0 oldest-first retention | ✅ |
| Superadmin Data Hygiene (Jon Doe / test purge + mismatch repair) | ✅ |
| Docs aligned to **269** fleet | ✅ this commit |

---

## Related repos

| Repo | Role |
|------|------|
| `shamrock-bail-portal-site` | Public site + GAS bond factory + school payment unlock |
| `shamrock-bail-school` | Student LMS education funnel |
| `shamrock-node-red` | **Automation fabric** — crons, webhooks, Watchdog, cross-service routing |

```bash
python scripts/check_ecosystem_secrets.py
python scripts/check_ecosystem_secrets.py --strict
```

## Super-admin + court automation (July 2026)

- Super-admin: `admin@shamrockbailbonds.biz` (see `dashboard/auth/super_admin.py`)
- Automation API (GAS_API_KEY): `/api/automation/lead-qualification|bond-lifecycle|risk-mitigation|court-email-scan|bond-report|discharge-report|ops-digest|schedule`
- Official OSI/Palmetto XLSX bond & discharge reports (`dashboard/services/bond_report_xlsx.py`)
- Court email: Calendar + client email + BlueBubbles (`court_email_scheduler`)

## Revenue automations (July 2026 — review-first)

| Cron | Default mode | Client contact? |
|------|--------------|-----------------|
| `speed_to_contact` | `review` | Queues outreach for staff approval |
| `paperwork_chase` | `review` | Staff notifications; `full_auto` to BB-nudge |
| `intake_recovery` | `review` | Staff notifications; `full_auto` to iMessage |
| `poa_low_stock` | on | Slack when POA tier ≤ threshold |
| `surety_weekly_reports` | on | XLSX → `generated_reports` + Slack |

Node-RED pack: `GET /api/automation/schedule` · docs `docs/automation/NODE_RED_SCHEDULE.md`

## Lifecycle suite (July 2026 — on the clock)

| Cron | Interval | Behavior |
|------|----------|----------|
| `forfeiture_scan` | 4h | Score active bonds; tasks + Slack for high/critical |
| `docuseal_poller` | 30m | Poll DocuSeal open submissions → signed/void |
| `compliance_backfill` | 6h | Missing check-in/court tasks → `TaskEngine` |
| `matching_backlog` | 1h | `MatchingEngine.batch_match`; Slack digest for human review |
