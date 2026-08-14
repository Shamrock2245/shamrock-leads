# Court Clerk Agent — "The Court Clerk"

> **Status:** `[IMPLEMENTED]`
> **Implementation:** `dashboard/services/court_reminder_service.py`, `dashboard/api/court_reminders.py`, `dashboard/api/calendar.py`

---

## Role

The Court Clerk automates court date management: scanning active bonds for upcoming court dates, scheduling Twilio SMS reminders at 7d/3d/1d intervals, syncing court dates to Google Calendar, and monitoring for discharge/exoneration emails.

---

## Pipeline

```
Active Bond with Court Date
    → Auto-Scan (periodic)
        → 7 days before: SMS reminder
        → 3 days before: SMS reminder
        → 1 day before: SMS reminder
        → Day of: Final SMS
    → Google Calendar event created (color-coded)
    → Post-court: Monitor Gmail for discharge notice
```

---

## Key Files

| File | Purpose |
|------|---------|
| `dashboard/services/court_reminder_service.py` | Auto-scan + Twilio SMS scheduling |
| `dashboard/api/court_reminders.py` | Court reminder API endpoints |
| `dashboard/api/calendar.py` | Google Calendar sync |
| `dashboard/services/court_email_processor.py` | Court email parsing |
| `dashboard/api/discharge_monitor.py` | Gmail OAuth2 discharge scanner |
| `dashboard/services/gmail_reader.py` | Gmail API wrapper |
| `dashboard/services/google_calendar_service.py` | GCal API wrapper |
| `dashboard/services/twilio_service.py` | Twilio SMS wrapper |
| `dashboard/sl-calendar.js` | Court calendar frontend |
| `dashboard/sl-calendar-ext.js` | Calendar extensions (GCal sync, auto-scan) |

---

## SMS Reminder Schedule

| Interval | Message Type |
|----------|-------------|
| 7 days | Early reminder with date, time, location |
| 3 days | Mid reminder with preparation instructions |
| 1 day | Urgent reminder with next-day details |
| Day of | Morning-of final reminder |

---

## Constraints

- 10DLC compliant — all SMS via verified Twilio number
- Only contacts validated indemnitors/defendants on active bonds
- Skips bonds with no court date set
- Skips already-scheduled reminders (idempotent)
- All reminders logged in `court_reminders` collection

## Gmail Pub/Sub Push Authentication

The Gmail notification endpoint is
`https://leads.shamrockbailbonds.biz/api/webhooks/gmail`. Configure its Pub/Sub
push subscription with **Enable authentication** selected and a dedicated
Google service account that is used only to invoke this endpoint. Grant the
Pub/Sub service agent permission to mint identity tokens for that account, and
set the push OIDC audience to the exact HTTPS endpoint above.

Production requires all four variables below in `/opt/shamrock-leads/.env`:

| Variable | Required value |
|---|---|
| `GMAIL_PUBSUB_AUDIENCE` | Exact OIDC audience configured on the push subscription (normally the endpoint URL above) |
| `GMAIL_PUBSUB_SERVICE_ACCOUNT_EMAIL` | Email of the dedicated Pub/Sub push service account |
| `GMAIL_PUBSUB_SUBSCRIPTION` | Full resource name: `projects/<project>/subscriptions/<subscription>` |
| `GMAIL_MONITORED_MAILBOX` | Exact mailbox registered by the Gmail watch |

The endpoint verifies Google's token signature, issuer, audience, expiry, and
verified service-account email before reading the body. It then requires both
the subscription resource and Gmail `emailAddress` to match configuration.
Missing configuration returns `503`; missing/invalid identity tokens return
`401`; authenticated pushes for the wrong service account, subscription, or
mailbox return `403`. Rejected pushes are not audited or processed. Do not add a
PIN, GAS key, or query-string secret as a fallback, and do not log the token,
mailbox, or raw payload.

After changing the subscription, restart the dashboard and send a Pub/Sub test
message. Confirm a `200` response and a `gmail_pubsub_webhook` audit event, then
confirm an unauthenticated POST receives `401` with no audit event.
