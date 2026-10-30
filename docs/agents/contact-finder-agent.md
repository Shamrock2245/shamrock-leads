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
- Unreviewed and rejected candidates expire after 30 days. Reviewed and approved candidates are kept
- An approval holds only while the candidate's phone, email, address and anchors are unchanged
