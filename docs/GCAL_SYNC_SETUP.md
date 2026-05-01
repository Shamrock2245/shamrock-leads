# 📅 Google Calendar Sync — Setup Guide

> **Feature:** `calendar.py` `/sync-gcal` endpoint — Pushes upcoming court dates from MongoDB to a Google Calendar so agents see court appearances in their personal calendars.

---

## What It Does

When triggered (manually or on a schedule), the GCal sync:

1. Queries MongoDB for all active bonds with court dates in the next N days
2. Creates or updates Google Calendar events with defendant name, booking number, county, bond amount, and case number
3. Sets event reminders at 48h and 24h before the court date
4. Color-codes events by urgency (red = today/tomorrow, orange = this week, blue = upcoming)
5. Returns a count of synced events

---

## Prerequisites

- A Google Calendar (can be a dedicated "Court Dates" calendar)
- Google Cloud project with Google Calendar API enabled
- OAuth 2.0 credentials (same project as Gmail if already set up)

---

## Step-by-Step Setup

### 1. Enable Google Calendar API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Navigate to **APIs & Services → Library**
3. Search for **Google Calendar API** and click **Enable**

### 2. Create or Reuse OAuth Credentials

If you already set up Gmail credentials, you can reuse the same `credentials/gmail_credentials.json` file — the Calendar API uses the same OAuth flow.

Otherwise, follow the same steps as [GMAIL_DISCHARGE_SETUP.md](./GMAIL_DISCHARGE_SETUP.md) Step 2.

### 3. Run First-Time Authorization

```bash
python3 -c "
from google_auth_oauthlib.flow import InstalledAppFlow
import json, os

SCOPES = ['https://www.googleapis.com/auth/calendar']
flow = InstalledAppFlow.from_client_secrets_file('credentials/gmail_credentials.json', SCOPES)
creds = flow.run_local_server(port=0)
with open('credentials/gcal_token.json', 'w') as f:
    f.write(creds.to_json())
print('Authorization complete')
"
```

### 4. Find Your Calendar ID

1. Go to [Google Calendar](https://calendar.google.com/)
2. Create a new calendar named **"Shamrock Court Dates"** (recommended)
3. Click the three dots next to the calendar → **Settings and sharing**
4. Scroll to **Integrate calendar** → copy the **Calendar ID** (looks like `abc123@group.calendar.google.com`)

### 5. Set Environment Variables

Add to your `.env` file:

```env
GCAL_CREDENTIALS_PATH=credentials/gmail_credentials.json
GCAL_TOKEN_PATH=credentials/gcal_token.json
GCAL_CALENDAR_ID=your-calendar-id@group.calendar.google.com
GCAL_DAYS_AHEAD=30                        # How many days ahead to sync
```

### 6. Test the Sync

```bash
curl -X POST http://localhost:5000/api/calendar/sync-gcal \
  -H "Content-Type: application/json" \
  -d '{"days_ahead": 30}'
```

Expected response:
```json
{
  "success": true,
  "synced": 12,
  "message": "Synced 12 court dates to Google Calendar"
}
```

---

## Event Format

Each synced event looks like this in Google Calendar:

```
Title:       [COURT] John Smith — Lee County
Description: Booking #: 2024-LEE-001234
             Case #: 2024-CF-005678
             Bond Amount: $15,000
             Surety: OSI
             Risk Score: 72
             Indemnitor: Jane Smith (239-555-0100)
Start:       [court_date]
End:         [court_date + 2 hours]
Color:       Red (≤1 day), Orange (≤7 days), Blue (>7 days)
Reminders:   48h email, 24h popup
```

---

## Manual Trigger

From the Court Calendar tab, click **📅 Sync to Google Cal** to run an on-demand sync.

---

## Scheduled Sync

Add to crontab for nightly sync:

```cron
0 22 * * * curl -s -X POST http://localhost:5000/api/calendar/sync-gcal -H "Content-Type: application/json" -d '{"days_ahead":30}' >> /var/log/gcal_sync.log
```

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `GCAL_NOT_CONFIGURED` | Check that `GCAL_CALENDAR_ID` and `GCAL_TOKEN_PATH` env vars are set |
| `Token expired` | Delete `gcal_token.json` and re-run the authorization script |
| `Calendar not found` | Verify the Calendar ID is correct and the calendar is shared with the OAuth account |
| `Quota exceeded` | Google Calendar API has a 1M requests/day limit — reduce `GCAL_DAYS_AHEAD` |

---

*Last updated: May 2026 — ShamrockLeads v2.0*
