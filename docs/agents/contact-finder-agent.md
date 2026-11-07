# Contact Finder — "The Finder"

> **Status:** `[IMPLEMENTED]`
> **Implementation:** `dashboard/services/contact_discovery.py`, `dashboard/routers/contacts.py`

---

## Role

The Finder performs OSINT-style contact discovery on arrestees, identifying potential indemnitors (family members, friends, co-workers) from public records. Discovered contacts are surfaced for human-approved outreach.

---

## How It Works

```
New Lee (FL) arrest write
    → queue marker in the same upsert (does not block the scraper)
    → dashboard cron claims the booking
    → name plus phone, DOB, or address+ZIP
    → store candidates on contacts
    → lead drawer for staff review
```

A new Lee (FL) upsert sets `indemnitor_discovery.status` to `queued`. The dashboard cron `lee_indemnitor_discovery` claims a few bookings per pass. Lee (GA), Lee (SC), and the other Lee counties are not queued. Discovery reads Shamrock rows already in Mongo (prior indemnitors, intakes, family links, same address). Public HTTP and skip-trace stay off unless env flags are set. Candidates are keyed on state + county + booking. A new contacts doc's `_id` is derived from that identity (`contact_doc_id`), so two runs for one arrest share one doc; older ObjectId docs are found by the same fields. County and state are canonicalized before the identity and `_id` are built (`lee`, `Lee County` → `Lee`; `Florida` → `FL`). A single legacy contacts doc keyed by booking only is updated in place; if that is ambiguous nothing is written and only the code `contacts_legacy_ambiguous` is logged. Prior-Shamrock and family rows count only when they carry the booking number and the arrest's county and state, all present and equal (an indemnitor's own home `state` is never compared, and a name alone never links a row). A failed read in any prior or same-address collection fails the run (`prior_read_failed`) and the booking is retried. A run that raises is retried with backoff (`status: retry`, 15/30/60 min) and becomes terminal `failed` after 3 attempts. The purge selects only docs with an expired unreviewed row and pages through all of them. It removes those rows with an atomic `$pull` on `review_status: unreviewed` and the cutoff, so rows a refresh adds in the meantime are kept and reviewed rows are never touched. Every array write bumps `discovery_rev`; a refresh writes only if the doc is still at the revision it read, and otherwise re-reads and re-merges (3 tries, then `contacts_write_conflict` and a retry). Unreviewed-only docs expire 30 days after the newest candidate. They are not placed on an outreach queue, and outreach uses a discovery candidate only after staff mark it `reviewed` or `approved`.

### Public-record links and place checks (Codex 10/30)

A person found on a property-appraiser or clerk page (`lee_property_appraiser`, `lee_clerk_parties`) is a candidate only when their published address is the defendant's own address: the same house number and street and the same ZIP as the booking. Such rows carry `defendant_link: "shared_address_zip"`. Their own phone or address is not a link, and without a defendant address and ZIP these providers return nothing. A public-record row without the link is never stored, shown, approved (409 `candidate_unlinked`) or used for outreach. Rejecting it is still allowed.

County and state must name one place. A county label with its own state that differs from `state` (`Lee (GA)` with `FL`) is `county_state_conflict`. A Florida county outside the 67 (`Manatee Clerk`) is `county_not_in_registry`. Either way discovery does not run and writes nothing, the contacts routes return 400 with the code, and the cron stops retrying the booking. Nothing is guessed.

### Arrest identity, transient failures and prior reads (Codex 11/2)

When county and state resolve a stored arrest, the arrest's own name, address, DOB and ZIP are used. Caller input only fills a blank field, for example a `MANUAL-*` lead with no arrest row. A differing caller value is ignored, and only the code `caller_identity_ignored` and the field names are logged. Blank caller values never clear a field.

A public provider (property appraiser, clerk, skip-trace) reports `source_timeout`, `source_connection_error`, `source_http_5xx` or `source_http_4xx`. A timeout, connection error or 5xx makes the run `failed` with `public_source_transient`. An unexpected provider error gives `public_source_failed`. Either way the booking goes to the bounded retry (15/30 min, terminal after 3 attempts), keeps its earlier rows, and is not cached as complete. A 4xx, `egress_block` or `tls_cert_invalid` is shown in `providers_run` and is not retried.

Prior-Shamrock collections are read in two bounded queries: exact-booking clauses first, then name clauses (`PRIOR_READ_LIMIT` = 25 each). A common name can't push the booking-linked co-signer out.

### Drawer identity (Codex 11/5)

- **The lead drawer carries the selected arrest's full identity.** Pipeline cards set `data-county` / `data-state`. Opening a card stores `{booking, county, state}` as `_currentScope` and candidate GETs / discovery refreshes / review posts use that place, not a booking-only lookup. Two bonds that share a booking across counties can no longer cross-load or approve each other's co-signers. A blank `state` matches an explicit Florida request only when the county is one of the 67 Florida counties. Lee, GA does not open a state-less Lee row, and the drawer does not use that row's defendant name for discovery.
- **The Lee clerk lookup is Lee, FL only.** `lee_clerk_parties` uses the same place guard as `lee_property_appraiser`. A case number from another county or state is not sent to the Lee clerk. A blank state is the legacy Florida default. The provider returns `skipped` and stores no parties.

### Retention, same-address cap and failure reporting (Codex 11/4)

- **Retention is independent of discovery.** The `contact_candidate_retention` cron purges expired unreviewed, rejected and status-less candidate rows every hour. It is always on: switching off `lee_indemnitor_discovery` (Service Control or `INDEMNITOR_DISCOVERY`) does not stop it.
- **Same-address matches first, then caps.** The provider scans the defendant's ZIP (bounded at 5,000 rows, `_id` order), keeps rows with the same normalized address, and stops at 25 matches.
- **Failures are not "finished".** `POST /api/contacts/discover` returns 503 with `{"error": <code>, "discovery_status": "failed"}` when discovery fails (e.g. `ARREST_READ_FAILED`, a transient public source). The response and the log carry the code only.

### Failed arrest reads and legacy retention (Codex 11/3)

- **A failed arrests read fails closed.** If the stored arrest cannot be read (the read raises), discovery returns `failed` with `discovery_error: ARREST_READ_FAILED`. No provider runs and nothing is written, and the log line carries only the code. Caller identity drives a run only when the read succeeds and finds no arrest (a `MANUAL-*` lead). The cron passes the claimed arrest itself, so the Lee queue never takes this path and Lee arrest writes are untouched.
- **Legacy candidate rows without `review_status` expire.** A row with a missing, `null` or `""` status was never reviewed, so it is treated as `unreviewed`. It is purged 30 days after `discovered_at` and counts toward the TTL. Reviewed and approved rows are still never touched.

### Staff review

`POST /api/contacts/{booking}/candidates/{candidate_id}/review` with `{county, state, decision, rev}` (`decision` is `approve` or `reject`; `rev` is the `discovery_rev` from `GET /api/contacts/{booking}`). It sits behind the dashboard auth (not an open prefix) and needs a staff cookie session (`god_admin`, `admin`, `staff`); otherwise 401/403. The write is guarded on `discovery_rev` like a refresh, so a stale `rev` gets 409 `stale_revision` and nothing changes. A decision stores `review_pin`: a SHA-256 digest per field of the normalized phone, email, address and anchors (no values). It appends `{audit_id, candidate_id, actor, actor_role, at, old_status, new_status, rev_before, rev_after}` to the doc's `review_log`. The contacts update and an immutable `audit_events` record (`event_type: contact_candidate_reviewed`, `_id` = `audit_id`, also stored on the row as `review_audit_id`) are written in one multi-document transaction, with the revision re-checked inside it. If the audit insert fails, the transaction aborts, the status is unchanged and the call returns 503 `audit_failed`. Without a replica set or Atlas it returns 503 `audit_unavailable` and writes nothing (fail closed). `actor` is `staff:` plus a hash of the session email. No names, phones or addresses are logged.

On refresh, a reviewed, approved or rejected row keeps its status only when the pin still matches. Any change resets it to `unreviewed` with `review_reset = {reason: contact_changed, fields, previous_status, at}`. Outreach also checks the pin, so a changed number is never texted in any mode. Rejected rows are never used and expire after 30 days like unreviewed rows.

`GET /api/contacts/{booking}` with county and state uses the same legacy fallback as discovery: one booking-only doc and one arrest with that booking. Otherwise it returns 409 `legacy_contacts_ambiguous`. Add Lead sends the defendant name with the booking (a `MANUAL-*` booking has no arrest record to read it from); without a name it does not run. The name is never logged. It runs discovery only with a state: the form's state, or `FL` when the county is an FL registry county. Otherwise it shows a message and does not run. Errors, including 409, are shown to staff.

---

## Key Files

| File | Purpose |
|------|---------|
| `dashboard/services/contact_discovery.py` | OSINT discovery engine |
| `dashboard/routers/contacts.py` | Contact API endpoints |
| `dashboard/sl-prospective.js` | Contact display in pipeline |

---

## Constraints

- **Human-in-the-loop required** — discovery does not call, text, or email a candidate
- A name alone is never a match. Confidence comes only from phone, DOB, and address+ZIP weights
- Public HTTP (`INDEMNITOR_DISCOVERY_HTTP`) and skip-trace (`INDEMNITOR_SKIPTRACE_URL` + `INDEMNITOR_SKIPTRACE_API_KEY`) default off
- Logs store counts and error types, not names, phones, or addresses
- Unreviewed, rejected and status-less (legacy) candidates expire after 30 days. Reviewed and approved candidates are kept
- An approval holds only while the candidate's phone, email, address and anchors are unchanged
