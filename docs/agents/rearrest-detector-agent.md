# Re-Arrest Detector — "The Sentinel"

> **Status:** `[IMPLEMENTED]`
> **Implementation:** `dashboard/api/rearrest_detector.py`, `dashboard/api/rearrest_notifier.py`

---

## Role

The Sentinel cross-references every new arrest against the active bonds database. When a defendant on an active bond is re-arrested, it fires immediate alerts via Slack and dashboard notifications.

---

## How It Works

```
New Arrest Scraped
    → Normalize name (first, last, DOB)
    → Query active_bonds by name + DOB fuzzy match
    → If match found:
        → Create notification in notifications collection
        → Fire Slack alert to #rearrest-alerts
        → Flag bond as "monitoring" or "alert"
        → Log audit event
```

---

## Key Files

| File | Purpose |
|------|---------|
| `dashboard/api/rearrest_detector.py` | Detection logic and scan endpoint |
| `dashboard/api/rearrest_notifier.py` | Notification delivery (Slack + dashboard) |
| `dashboard/sl-rearrest.js` | Re-arrest alerts frontend |
| `dashboard/sl-notifications.js` | Notification center |

---

## Constraints

- Fuzzy name matching with configurable threshold (default: 0.85)
- DOB must match exactly when available
- Only scans against `active` and `monitoring` status bonds
- Never auto-changes bond status — human reviews and decides
- All detections logged in `audit_events`
