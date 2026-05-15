# Discharge Monitor — "The Discharge Monitor"

> **Status:** `[IMPLEMENTED]`
> **Implementation:** `dashboard/api/discharge_monitor.py`, `dashboard/services/gmail_reader.py`

---

## Role

The Discharge Monitor scans the agency's Gmail inbox for court-issued discharge and exoneration notices. When detected, it auto-transitions the matching active bond to `exonerated` status, releases the POA, and logs the event.

---

## How It Works

```
Gmail Inbox (OAuth2 authenticated)
    → Scan for unread emails matching discharge patterns
    → Parse email: extract case number, defendant name, court, date
    → Match to active bond in MongoDB (by case number + defendant)
    → If match found:
        → Transition bond status to "exonerated"
        → Auto-release assigned POA
        → Log audit event
        → Create dashboard notification
        → Mark email as read
    → If no match:
        → Log for manual review
```

---

## Key Files

| File | Purpose |
|------|---------|
| `dashboard/api/discharge_monitor.py` | Scan endpoint + email parsing |
| `dashboard/services/gmail_reader.py` | Gmail OAuth2 wrapper (read, mark, search) |
| `dashboard/services/google_calendar_service.py` | Optional: remove court event on discharge |
| `dashboard/sl-active-bonds.js` | Reflects status change in Kanban |

---

## Email Pattern Matching

The monitor searches for emails containing:
- Keywords: "discharge", "exoneration", "bond released", "surety released", "obligation terminated"
- Structured court notices with case numbers
- Sender patterns from known county clerk addresses

---

## Constraints

- **Human verification** — Auto-matched discharges are flagged for review if confidence < 0.9
- **Never auto-forfeit** — Only handles discharges/exonerations (positive outcomes)
- **Audit trail** — Every status change logged to `audit_events`
- **POA auto-release** — Freed POA returns to available inventory
- **Gmail OAuth2** — Requires `GOOGLE_APPLICATION_CREDENTIALS` or refresh token
- **Idempotent** — Processed emails marked as read; won't re-process
