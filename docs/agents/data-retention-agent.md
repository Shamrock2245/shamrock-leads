# Data Retention Agent — "The Janitor"

> **Status:** `[IMPLEMENTED]`
> **Implementation:** `dashboard/api/data_retention.py`

---

## Role

The Janitor manages tiered data retention policies to keep MongoDB Atlas within the M0 free tier's 512MB storage limit. It purges old, low-value data while preserving active bonds, recent leads, and audit trails.

---

## Retention Tiers

| Collection | Retention | Purge Strategy |
|------------|-----------|----------------|
| `arrests` (Disqualified) | 7 days | Delete where `lead_status = "disqualified"` and `scraped_at` > 7 days |
| `arrests` (Cold) | 30 days | Delete where `lead_status = "cold"` and `scraped_at` > 30 days |
| `arrests` (Warm) | 90 days | Delete where `lead_status = "warm"` and `scraped_at` > 90 days |
| `arrests` (Hot) | 180 days | Delete where `lead_status = "hot"` and `scraped_at` > 180 days |
| `audit_events` | 90 days | TTL index (`expireAfterSeconds: 7776000`) |
| `notifications` | 30 days | Delete old read notifications |
| `court_reminders` | 30 days post-court | Delete completed/expired reminders |
| `active_bonds` | Never | Retained indefinitely (compliance) |
| `defendants` | Never | Retained indefinitely |

---

## Key Files

| File | Purpose |
|------|---------|
| `dashboard/api/data_retention.py` | Purge endpoints + storage stats |

---

## Constraints

- **Never delete active bonds** — compliance requirement
- **Never delete defendants** — may be referenced by future bonds
- Purge runs are logged in `audit_events` before the events themselves are purged
- Storage stats available via `/api/retention/stats`
- Manual purge trigger via `/api/retention/purge`
