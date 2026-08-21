# Palantir OSINT Command-Center Improvement Plan

> **Status:** Implementation brief — 2026-08-20
> **Scope:** Provenance-first Palantir improvements plus one staff-confirmed, minimized public booking intake path.
> **Owner:** Shamrock Bail Bonds — `shamrock-leads`
> **Decision:** External projects are reviewed as **interface and architectural inspiration only**. No third-party source code, dependencies, credentials, sidecars, agents, enrichment providers, or autonomous data collection are adopted. The sole documented network path is the existing official Lee booking parser, now surfaced behind a staff-confirmed, minimized preview.

## 1. Purpose and operating boundary

The Palantir workspace is an internal staff interface for **recorded Shamrock CRM intelligence**. It must make the provenance, availability, and limits of its existing data easier to understand without becoming an unbounded OSINT console. Its approved functions remain exact-subject graph resolution, operational lead-map review, provider-gated breach lookup, a CRM-bounded dossier, and one constrained Lee booking URL path that ends at an ArrestLead after staff confirms the exact booking number.

> **Core rule:** A visual relationship, correlation, score, or status card is not evidence unless its source and confidence are present. When no CRM record, provider result, verified map point, or supported capability exists, the interface must state that absence rather than create a plausible substitute.

The improvement pass is constrained by the platform chain: `ArrestLead → Defendant → Indemnitor → Match → BondCase → Packet → Signature → Payment`. The confirmed-booking exception may create or refresh an **ArrestLead only** after exact staff confirmation; it cannot create any downstream record. Nothing in Palantir may initiate outreach, select a surety, issue paperwork, send a payment or signing link, change bond status, or modify a signed record. Those actions remain elsewhere in the CRM and retain their validation and human gates.

## 2. Current Shamrock contract

| Existing surface | Current route or source | Required interface truth |
|---|---|---|
| **Entity Reactor** | `GET /api/palantir/graph/{subject_id}` | Resolves an exact defendant or indemnitor from Mongo. Nodes and edges are CRM-backed; an unresolved subject returns an empty graph and warning. |
| **OSIRIS Field Grid** | `GET /api/palantir/situation-room/feeds` | Shows recent CRM lead signals plus clearly marked map-reference pins. Reference pins are not incidents. |
| **SPECTRA Scan** | `POST /api/palantir/spectra/breach-lookup` | Uses the configured Hudson Rock provider for email or username. Phone-only input has an explicit capability limit. Provider failures are unavailable, not negative findings. |
| **Intelligence Brief** | `POST /api/palantir/dossier/generate` | Produces a CRM-bounded summary only after subject resolution; an unresolved subject produces no invented finding. |
| **Confirmed Booking Intake** | `POST /api/palantir/booking-intake/preview` then `POST /api/palantir/booking-intake/confirm` | Accepts only an official HTTPS Lee booking URL, projects permitted published facts into a 15-minute preview, and requires staff acknowledgement plus exact booking-number re-entry before an ArrestLead-only create/refresh. |

The router preserves masked phone/email display, a controlled metadata allowlist, exact subject matching, explicit `verified` flags, source labels, and fail-closed error copy. The UI pass may clarify these signals but may not weaken them.

## 3. External inspiration review

The five projects below were reviewed from their public GitHub documentation on 2026-08-20. Star counts are an observation at that time, not a quality or suitability guarantee.

| Project | Observed position | Transferable pattern | Deliberately excluded from Shamrock |
|---|---:|---|---|
| [OSINT-Terminal][1] | 31 stars · MIT | Tool discovery, named investigation context, explicit cached/history state, and map/globe-style spatial orientation. | Its 438-tool registry, public-source calls, batch lookup API, local persistence, external history, and globe data feeds. |
| [OSIF v2][2] | 203 stars · no asserted SPDX license | Graph workspace, case-style evidence framing, module-state visibility, correlation/provenance vocabulary, and clear asynchronous status treatment. | Docker stack, PostgreSQL/Redis/MinIO, task workers, WebSockets, manual graph authoring, case storage, and all modules. |
| [SpiderFoot][3] | 21,241 stars · MIT | Compact module/correlation status summaries, capability disclosure, non-silent partial/failure states, and report/export discipline. | Its 200+ module engine, crawling, network scanning, TOR, third-party data-source calls, correlation rules, and target expansion. |
| [OSINT Toolkit][4] | 21 stars · no declared license detected | Plain-language input guidance, concise capability boundaries, provider-labelled result cards, and service-oriented FastAPI organization. | Its integrations, environment keys, GitHub analysis, DNS recon, WHOIS history, DoxBin search, and phone/email lookups. |
| [OpenOSINT][5] | 1,432 stars · MIT | Visible tool-run ledger, source cards, explicit tool availability, and reports that distinguish executed tools from unavailable capabilities. | Natural-language autonomous tool chaining, MCP exposure, LLM providers, public search/dorking, bypass-oriented retrieval, provider credentials, and saved reports. |

The most applicable common theme is **operator clarity**: dense but readable command surfaces should show what data source ran, what it returned, what was excluded, and when the user must stop. Shamrock will implement that idea only over its existing trusted routes.

## 4. Approved improvement set

### 4.1 Evidence and provenance matrix

The Entity Reactor will gain a compact **evidence matrix** computed only from the graph response already loaded in the browser. It will summarize node categories, visible versus hidden signals, verified versus unverified nodes, edge count, and provenance labels. It will not create a new graph, score a subject, or query any third party.

### 4.2 Relationship-focused navigation

The Reactor will gain a typed relationship filter and accessible focus state. Staff will be able to narrow the currently rendered graph to recorded link types and return to the unfiltered CRM view. This is a visual filter over the in-memory API response only; it does not suppress, mutate, or delete Mongo relationships.

### 4.3 Session-local operator ledger

The workspace will show a compact **operation ledger** for events performed during the current browser session, such as graph resolution, map refresh, SPECTRA result state, or brief compilation. A ledger entry will expose operation type, outcome, timestamp, and source boundary—without placing full emails, phones, addresses, external query values, raw payloads, or client contact data into browser console output, storage, Slack, or a commit. Reloading the page clears the ledger.

### 4.4 OSIRIS source split and field telemetry

OSIRIS will expose a source split derived from each feed's existing `demo` and `severity` values: CRM signals, map-reference pins, alert-level signals, selected county, and current focus state. This borrows the status-matrix notion from OSINT workstations while making no location claim beyond the actual feed payload.

### 4.5 SPECTRA provider contract and result ledger

SPECTRA will display the active provider boundary, accepted input classes, response state, and source timestamp within the existing card. It will retain the existing no-result, provider-unavailable, and phone-capability-limit behavior. It will not add a provider, transform a phone lookup into another lookup, save the query, or imply verified geotags.

### 4.6 Brief evidence manifest

The Intelligence Brief will include a **source manifest** that enumerates the brief's CRM graph mode, node/edge totals, verified link count, and whether the SPECTRA result has been explicitly incorporated. The current backend does not incorporate a SPECTRA scan into a dossier, so the UI must show that it is **not attached**, not imply correlation.

### 4.7 Confirmed booking intake

The dashboard adds a dedicated **Confirmed Booking Intake** workspace for one official Lee County booking URL. The preview endpoint accepts an allowlisted HTTPS host and one numeric ID, uses the existing Lee parser, and projects only booking number, Lee/FL jurisdiction, published defendant name, facility, custody status, charges, bond/case/court facts, source host, and parser method. It strips address, DOB, demographics, phone, email, relatives, household data, raw HTML, raw API payloads, and any enrichment data before the preview is stored.

The preview expires after 15 minutes, is server-side only, and must be consumed by an authenticated staff confirmation. The UI requires a staff acknowledgement and exact booking-number re-entry. The confirmed route protects booking-number-plus-county-plus-state identity through a canonical key, detects cross-jurisdiction collisions, refuses protected downstream records, and writes an ArrestLead only. Its complete contract, field allowlist, retention, test plan, and release boundary are in [Confirmed Booking-URL Intake Design](./CONFIRMED_BOOKING_URL_INTAKE.md).

## 5. Explicit non-goals

| Not in this implementation | Reason |
|---|---|
| New OSINT providers, SpiderFoot sidecar, OSIF deployment, OSINT-Terminal installation, OpenOSINT agent, or external API keys | These are infrastructure and data-processing changes requiring separate legal, privacy, security, rate-limit, licensing, and production review. |
| Full-name, phone, username, email, domain, social, dork, port-scan, or batch recon expansion | The request is a dashboard-improvement pass; new person-level lookup capability requires a separate approved data-source contract. |
| Automatic correlation, risk escalation, or natural-language investigation recommendations | The data chain forbids guessed identity and unsupported inference. Existing dossiers remain conservative and CRM bounded. |
| Case creation, evidence persistence, subject attachment, report storage, or export of raw OSINT data | These are record-write or retention-policy changes and are out of scope. The sole exception is the separately documented confirmed Booking URL path, which writes a minimized ArrestLead only. |
| Changes to documents, signatures, payments, surety/POA selection, client outreach, or `full_auto` behavior | These are protected operational workflows with separate validation and human gates. |

## 6. Interface acceptance criteria

| Area | Acceptance criterion |
|---|---|
| **Data truth** | Every new count, badge, filter, and source label is derived from an existing Palantir response or a documented session-local UI state. |
| **Provenance** | New UI surfaces visibly distinguish CRM-backed, map-reference, verified, unverified, unavailable, empty, and filtered states. |
| **PII** | No new console logging, persistent browser storage, API request field, external request, commit data, or chat output includes unmasked phones, emails, addresses, SSNs, or raw result payloads. |
| **Accessibility** | New controls have labels, keyboard focus, pressed/selected state, and readable text contrast. |
| **Functionality** | Existing exact resolver, layer controls, node inspection, map focus, county refresh, SPECTRA scan state, dossier generation, and printing continue to work. |
| **Operational safety** | The change makes no API contract, route, schema, provider, secret, deploy, GAS URL, or host change. |

## 7. Validation plan

The implementation will use controlled browser-local fixtures containing only synthetic, masked labels to test graph filters, the evidence matrix, session ledger, OSIRIS telemetry, SPECTRA state handling, and the brief manifest. It will not query production CRM subjects or external providers during visual validation.

The source checks are: JavaScript syntax validation, the existing `tests/test_palantir_intel.py` fail-closed suite, a clean `git diff --check`, `python3 scripts/check_ecosystem_secrets.py --strict`, and `python3 scripts/check_subdomains.py --live`. The strict check is expected to remain red in a clean checkout without production `.env` files or sibling repositories; that limitation must be recorded honestly and never reported as green.

## 8. Implementation evidence

### 8.1 Changed surfaces

| File | Implemented change | Operational impact |
|---|---|---|
| `dashboard/index.html` | Added the confirmed-booking workspace, source boundary, minimized preview surface, acknowledgement, and exact-number re-entry control alongside the prior evidence-oriented HUD improvements. | The UI exposes no address, contact, relative, household, enrichment, bond, or paperwork action. |
| `dashboard/sl-palantir.js` | Added in-memory relationship focus, evidence-matrix calculations, an ephemeral operation ledger, OSIRIS source-split telemetry, dossier evidence manifest, and booking preview/confirmation controller. | The booking controller calls two staff-gated endpoints and sends only URL, random preview ID, exact booking number, and acknowledgement. |
| `dashboard/sl-palantir.css` | Added responsive, scoped styling for provenance, telemetry, and staff-confirmed booking surfaces. | Visual only; all rules remain scoped to `#tabPalantir`. |
| `dashboard/services/confirmed_booking_intake.py` | Added allowlisted Lee URL normalization, explicit field projection, expiring preview storage, canonical dedup key protection, protected-record refusal, and minimal audit handling. | No external enrichment, contact, relative, address, household, DOB, provider credential, surety, or downstream bond-chain write is allowed. |
| `dashboard/routers/palantir_intel.py` and `dashboard/models/palantir.py` | Added typed preview/confirm contracts and staff-session/admin-key gates. | Confirmation cannot write arbitrary submitted fields; it consumes a live server-side preview. |

### 8.2 Controlled interaction validation

A local static dashboard instance was validated with a browser-local fetch interceptor. The fixture was synthetic and clearly labelled; it contained a local subject token, a masked email label, and a redacted address label. It did not access a real CRM record, external provider, or client contact channel. The interceptor was removed after the test.

| Workflow | Controlled result | Safety observation |
|---|---|---|
| Exact Entity Reactor resolve | Rendered 6 nodes and 5 CRM-shaped links. The matrix reported 5 verified and 1 unverified signal plus `MONGO` provenance. | The interface retained its exact-subject prompt and did not create an identity. |
| Relationship focus | Selecting `active_bond` reduced the visible links from 5 to 1, changed the matrix focus label, and added a session-local ledger entry. | Nodes remained visible, no record was mutated, and the ledger stored only operation metadata. |
| OSIRIS feed review | Displayed 1 CRM signal, 1 map-reference pin, 1 alert-level signal, and a `Focused` state after map selection. | The map-reference pin remained visibly distinct from the CRM signal. |
| SPECTRA scan | Displayed the provider contract, a controlled no-signal result, and the existing no-verified-geotag state. | The result remained transient and did not claim a location or attach to the dossier. |
| Intelligence Brief | Rendered a manifest containing graph mode, CRM node/link totals, verified-link count, and `SPECTRA scope: NOT ATTACHED`. | No external scan result was represented as CRM evidence or underwriting input. |
| Confirmed Booking Intake preview | A browser-local fixture rendered a synthetic booking number, facility, custody status, bond, case, court, and charge in the minimized review panel. | The panel explicitly omitted address, contact, relative, household, DOB, and enrichment data. No real booking page or CRM record was queried. |
| Confirmed Booking Intake confirmation | The action remained disabled after acknowledgement alone, became enabled only after exact booking-number re-entry, then displayed an ArrestLead-only created state. | The controlled completion copy explicitly excluded bond, paperwork, signature, payment, outreach, and enrichment. The fixture interceptor was removed afterward. |

The local browser console showed no errors from `sl-palantir.js` during the controlled workflows. Static-server warnings for unrelated dashboard APIs were expected because no backend was running locally.

### 8.3 Source and ecosystem verification

| Check | Result | Notes |
|---|---|---|
| `node --check dashboard/sl-palantir.js` | Passed | The enhanced controller parsed successfully. |
| `pytest -q tests/test_confirmed_booking_intake.py tests/test_palantir_intel.py` | Passed — 11 tests | New preview, field-minimization, exact confirmation, create, and cross-jurisdiction refusal checks pass alongside the existing Palantir fail-closed contract. |
| `git diff --check` | Passed | No whitespace errors were introduced. |
| `python3 scripts/check_subdomains.py --live` | Passed | All official hosts have live DNS. HTTP returned `200` for the standard public hosts; on-demand `trape` returned `URLError`, consistent with its non-always-on designation. |
| `python3 scripts/check_ecosystem_secrets.py --strict` | Not clean in this checkout | The clean local checkout has no production `.env` files and no sibling school or Wix/GAS secret stores. The script reported 10 critical local gaps and 33 recommended unset values. No secret, key, or provider configuration was changed. |

### 8.4 Release boundary and handoff

This improvement is a release candidate in the local working tree at the time of this document update. It has passed focused source and controlled browser validation, but its strict local secrets check is not clean because this checkout does not contain production environment files or sibling secret stores. It remains uncommitted and undeployed until the owner-authorized main-branch release is completed. A live record will be added to `STATUS.md` and `CHANGELOG.md` only after the deployment workflow and production probes succeed.

## References

[1]: https://github.com/RojanSapkota/OSINT-Terminal "RojanSapkota/OSINT-Terminal"
[2]: https://github.com/fr4nc1stein/osint-framework "fr4nc1stein/osint-framework"
[3]: https://github.com/smicallef/spiderfoot "smicallef/spiderfoot"
[4]: https://github.com/ayxkaddd/Osint-ToolKit "ayxkaddd/Osint-ToolKit"
[5]: https://github.com/OpenOSINT/OpenOSINT "OpenOSINT/OpenOSINT"
