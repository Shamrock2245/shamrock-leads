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

A new Lee (FL) upsert sets `indemnitor_discovery.status` to `queued`. The dashboard cron `lee_indemnitor_discovery` claims a few bookings per pass. Lee (GA), Lee (SC), and the other Lee counties are not queued. Discovery reads Shamrock rows already in Mongo (prior indemnitors, intakes, family links, same address). Public HTTP and skip-trace stay off unless env flags are set. Candidates are keyed on state + county + booking. A new contacts doc's `_id` is derived from that identity (`contact_doc_id`), so two runs for one arrest share one doc; older ObjectId docs are found by the same fields. County and state are canonicalized before the identity and `_id` are built (`lee`, `Lee County` → `Lee`; `Florida` → `FL`). A single legacy contacts doc keyed by booking only is updated in place; if that is ambiguous nothing is written and only the code `contacts_legacy_ambiguous` is logged. Prior-Shamrock and family rows count only when they carry the booking number and the arrest's county and state, all present and equal (an indemnitor's own home `state` is never compared, and a name alone never links a row). A failed read in any prior or same-address collection fails the run (`prior_read_failed`) and the booking is retried. A run that raises is retried with backoff (`status: retry`, 15/30/60 min) and becomes terminal `failed` after 3 attempts. The purge selects only docs with an expired unreviewed row and pages through all of them. Unreviewed-only docs expire 30 days after the newest candidate. They are not placed on an outreach queue, and outreach uses a discovery candidate only after staff mark it `reviewed` or `approved`.

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
- Unreviewed candidate documents expire after 30 days. Reviewed candidates are kept
