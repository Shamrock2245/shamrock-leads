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

A new Lee (FL) upsert sets `indemnitor_discovery.status` to `queued`. The dashboard cron `lee_indemnitor_discovery` claims a few bookings per pass. Lee (GA), Lee (SC), and the other Lee counties are not queued. Discovery reads Shamrock rows already in Mongo (prior indemnitors, intakes, family links, same address). Public HTTP and skip-trace stay off unless env flags are set. Candidates are keyed on state + county + booking. They are not placed on an outreach queue, and outreach uses a discovery candidate only after staff mark it `reviewed` or `approved`.

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
