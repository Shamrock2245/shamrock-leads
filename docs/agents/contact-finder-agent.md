# Contact Finder — "The Finder"

> **Status:** `[IMPLEMENTED]`
> **Implementation:** `dashboard/services/contact_discovery.py`, `dashboard/api/contacts.py`

---

## Role

The Finder performs OSINT-style contact discovery on arrestees, identifying potential indemnitors (family members, friends, co-workers) from public records. Discovered contacts are surfaced for human-approved outreach.

---

## How It Works

```
Defendant Record
    → Public records search (name + location)
    → Identify associated persons
    → Score relationship confidence
    → Store discovered contacts
    → Surface for human review
    → Approved contacts → Outreach queue
```

---

## Key Files

| File | Purpose |
|------|---------|
| `dashboard/services/contact_discovery.py` | OSINT discovery engine |
| `dashboard/api/contacts.py` | Contact API endpoints |
| `dashboard/sl-prospective.js` | Contact display in pipeline |

---

## Constraints

- **Human-in-the-loop required** — discovered contacts must be reviewed before outreach
- Only public records and publicly available information
- PII minimized in storage — only name, phone, relationship stored
- DNB/DNC flags respected across all discovered contacts
- All discovery attempts logged for compliance
