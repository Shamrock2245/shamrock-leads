# Confirmed Booking-URL Intake Design

> **Status:** Approved implementation design — 2026-08-21
> **Scope:** A staff-confirmed, Lee County booking-page path that creates or refreshes one **arrest lead** only.
> **Out of scope:** Person enrichment, address/contact discovery, relatives, household inference, outbound outreach, bond writing, paperwork, signatures, payments, surety, POA, or automated promotion.

## 1. Objective

This workflow turns a supported public booking URL into a **reviewable, exact booking preview** and, only after a staff member deliberately confirms the exact booking number, into a CRM arrest lead. It is not an investigation tool and it is not a shortcut around the established bond chain.

> The confirmation path ends at **ArrestLead**. It never creates a Defendant, Indemnitor, Match, BondCase, Packet, Signature, or Payment record. Any later workflow must continue through the normal validation and human-gated chain.

## 2. Supported source boundary

The initial release accepts only HTTPS URLs for the Lee County Sheriff booking surface: `sheriffleefl.org` and `www.sheriffleefl.org`, with one positive numeric `id` query parameter. The server treats the host—not a charge narrative or inferred court reference—as the authoritative booking county: **Lee, FL**.

This closed allowlist is intentionally narrower than the legacy generic URL parser. It prevents the confirmed endpoint from becoming an arbitrary server-side URL fetcher, and it avoids silently accepting an unsupported jail site.

| Accepted input | Required rule | Rejection reason |
|---|---|---|
| `https://www.sheriffleefl.org/booking/?id=<positive digits>` | HTTPS, exact allowlisted host, exactly one booking identifier | Any missing or malformed booking identifier fails before a fetch. |
| Canonical Lee booking API URL | HTTPS, allowlisted host, numeric booking identifier | The server extracts the same booking identifier and normalizes the external URL to a canonical source. |
| Any other host, IP address, localhost, private network, redirect destination, file URL, or arbitrary query format | Not supported | No external request is made. Staff must use the established manual intake path. |

## 3. Two-step staff confirmation contract

| Step | Endpoint | Staff requirement | Result |
|---|---|---|---|
| **Preview** | `POST /api/palantir/booking-intake/preview` | Authenticated staff session or existing dashboard admin key; paste a supported URL. | The server extracts a minimized booking preview, stores a short-lived server-side preview, and returns a random preview ID. No arrest lead is written. |
| **Confirm** | `POST /api/palantir/booking-intake/confirm` | Authenticated staff session or existing dashboard admin key; tick the exact-match acknowledgement and re-enter the booking number shown in the preview. | The server verifies the active preview and matching booking number, then creates or safely refreshes a single Lee, FL arrest lead. |

A preview expires after **15 minutes**. Only the minimized approved fields and source metadata are stored during that period; no page HTML, API response body, address, DOB, contact data, or raw payload is persisted. The preview is deleted after a successful confirmation and is covered by a TTL index as a backup cleanup mechanism.

## 4. Permitted and prohibited fields

The legacy `url_ingest_service` can parse more data than this feature is allowed to display or store. The confirmed workflow applies an explicit server-side projection after parsing and before its preview record is created.

| Permitted public booking facts | Purpose |
|---|---|
| Booking number, booking county, state, booking facility, source URL, parser method, and retrieval timestamp | Exact source identification and deduplication. |
| Defendant name exactly as published | Staff comparison only; no fuzzy identity matching. |
| Custody status, booking/arrest date when present, listed charges, bond amount, bond type, case number, court date/time/location | Public booking context required for a staff arrest-lead review. |

| Never returned, stored by this workflow, or sent to a provider | Reason |
|---|---|
| Street/residential address, city, ZIP, address history, cohabitants, household links, or property data | The workflow is a booking intake, not residence discovery. |
| Phone, email, username, social profile, employment, reference, relative, or second-degree relationship data | No person enrichment, contact discovery, or family mapping is permitted. |
| DOB, full demographic profile, raw HTML, raw external API response, hidden metadata, browser query history, or external lookup results | Data minimization and no unnecessary persistence. |
| Any inferred identity, likely address, match, relationship, score, or recommendation | The interface fails closed where a recorded source fact is missing. |

## 5. Idempotency and non-mutation rules

The CRM arrest-lead key is **Booking Number + County + State**. The implementation normalizes this first-release workflow to `booking_number=<id>`, `county=Lee`, `state=FL` and stores a canonical `booking_dedup_key` such as `FL|LEE|<id>`.

The existing repository index declaration currently contains a legacy unique index on `booking_number` alone. The new route checks for a conflicting booking number under a different county/state before writing. If such a conflict exists, it returns `requires_staff_review` and does not alter either record. This preserves the rule that same-named counties in different states must never collapse. A later index migration may replace the legacy index with a compound unique index only after a separately approved production data migration.

Existing leads marked as bonded, signed, or otherwise protected are not updated by this path. They return `requires_staff_review`. For an unprotected existing Lee arrest lead, the route only refreshes the whitelisted booking facts and confirmation provenance; it never copies an address, DOB, contacts, surety, POA, indemnitor, or paperwork state.

## 6. Authorization, audit, and data handling

The confirmed routes use the existing staff-session / dashboard-admin-key access pattern. Confirmation cannot be performed by resubmitting arbitrary browser fields: the server loads the previously generated, unexpired preview by random ID and compares the typed booking number to the stored exact identifier.

The server records a minimal audit event containing the internal booking-dedup key, event type, source host, parser method, timestamp, and whether the result was created, refreshed, or required review. Logs must not include a defendant name, street address, contact information, raw URL query, or raw page/API response.

## 7. Dashboard behavior

The Palantir dashboard receives a new **Confirmed Booking Intake** surface. It presents a booking-link field, an explicit Lee-only source boundary, a minimized preview, provenance, expiry timer, and a confirmation field. The action button remains disabled until the typed booking number exactly matches the preview and staff affirms that the source record is the intended booking.

The dashboard does not pre-populate an Appearance Bond, generate packet material, contact a client, or open an OSINT search. On completion it shows only the result class: arrest lead created, existing unprotected arrest lead refreshed, or staff review required.

## 8. Test plan

| Test | Expected result |
|---|---|
| Unsupported host, HTTP scheme, private/IP host, missing ID, non-numeric ID, or multiple IDs | `400`/`422`; no fetch, preview, or arrest-lead write. |
| Parser returns no exact name or a booking number different from the URL ID | Fail closed; no preview. |
| Valid minimized preview | Returns only the permitted field projection and a short-lived random preview ID. |
| Confirm with no acknowledgement, an expired/missing preview, or a non-matching typed booking number | `409`/`422`; no arrest-lead write. |
| Confirm new Lee booking | Creates one arrest lead with canonical booking/county/state identity and provenance. |
| Repeat confirmation of same preview | First may create/refresh; subsequent confirmation fails because preview is consumed. |
| Existing unprotected Lee arrest | Refreshes only permitted booking facts and confirmation provenance. |
| Existing bonded/protected lead or same booking number under another county/state | `requires_staff_review`; no mutation. |
| Existing Palantir, secrets, host, and booking-intake tests | Continue to pass. |

## 9. Release boundary

No deployment, secret change, source-provider addition, or data migration is authorized by this design. The route remains scoped to the existing Lee parser and the existing authenticated dashboard. Live release requires the standard review, checks, commit, main-branch push, and post-deploy verification process.


## 10. Implementation and validation evidence

The implementation is contained in `dashboard/services/confirmed_booking_intake.py`, typed request models in `dashboard/models/palantir.py`, two staff-gated endpoints in `dashboard/routers/palantir_intel.py`, and the scoped Palantir dashboard controls in `dashboard/index.html`, `dashboard/sl-palantir.js`, and `dashboard/sl-palantir.css`. The service creates a TTL index for the short-lived preview and a sparse unique index over the canonical `booking_dedup_key` before an upsert. If that deduplication guard is unavailable, confirmation fails closed with no write.

| Verification | Result | Boundary confirmed |
|---|---|---|
| `pytest -q tests/test_confirmed_booking_intake.py tests/test_palantir_intel.py` | Passed — 11 tests | Allowlisted-source rejection, projection stripping, preview expiry, acknowledgement requirement, exact-number comparison, arrest-lead creation, cross-jurisdiction refusal, and existing Palantir fail-closed behavior. |
| `node --check dashboard/sl-palantir.js` | Passed | The client controller parses successfully. |
| `git diff --check` | Passed | No whitespace errors in the scoped change set. |
| Controlled local dashboard workflow | Passed | A synthetic Lee booking preview omitted address, contacts, relatives, household data, DOB, and enrichment. Acknowledgement alone did not enable confirmation; exact booking-number re-entry did. The controlled result stated that only an ArrestLead was created. |
| `python3 scripts/check_subdomains.py --live` | Script had a transient apex `URLError`; independent browser request passed | `https://shamrockbailbonds.biz/` reached the live site and redirected to the expected `www` host. Standard service hosts returned `200`; on-demand `trape` returned `URLError`. |
| `python3 scripts/check_ecosystem_secrets.py --strict` | Not clean in this checkout | The clean checkout has no production `.env` files or sibling secret stores; it reported 10 critical local gaps and 33 recommended unset values. No key, secret, vendor configuration, or GAS URL was changed. |

The local UI fixture was synthetic, browser-local, and removed after testing. No production booking record, CRM record, client contact channel, external enrichment source, or user-supplied person data was accessed during validation.

## 11. Current release state

At the time of this documentation update, the change has passed focused source and controlled UI validation and is pending the owner-authorized commit and `main` push. The strict local secrets check remains an environment limitation and is not treated as production green. Live status records must only be updated after the deployment workflow and public endpoint probes complete.
