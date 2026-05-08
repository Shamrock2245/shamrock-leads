# Antigravity Handoff — ShamrockLeads Bond Lifecycle Migration
**Repo:** `Shamrock2245/shamrock-leads` · **Branch:** `main` · **Latest commit:** `564214b`
**Date:** 2026-04-27

---

## What Manus Just Shipped (Your Starting Point)

All of the following files are committed and on `main`. Pull before you start.

### New API Blueprints (`dashboard/api/`)

| File | Blueprint Name | URL Prefix | Key Endpoints |
|---|---|---|---|
| `bond_lifecycle.py` | `bond_lifecycle_bp` | `/api/bond-lifecycle` | `POST /phase1/trigger`, `POST /phase2/trigger`, `POST /webhook/signnow/complete`, `POST /court-email/process` |
| `events.py` | `events_bp` | `/api` | `GET /events/stream` (SSE) |
| `payments.py` | `payments_bp` | `/api` | `POST /payments/log`, `GET /payments/<booking>` |
| `webhooks.py` | `webhooks_bp` | `/api` | `POST /webhooks/signnow`, `/twilio`, `/payment` |
| `tracking.py` | `tracking_bp` | `/api` | `GET /tracking/map-data`, `GET /tracking/history/<booking>`, `POST /geofence/<booking>` |
| `court_reminders.py` | `court_reminders_bp` | `/api` | `POST /court-reminders/schedule`, `POST /process`, `GET /<booking>` |
| `contacts.py` | `contacts_bp` | `/api` | `POST /contacts/discover`, `GET /contacts/<booking>` |

All blueprints are **already registered** in `dashboard/__init__.py` inside `create_app()`.

### New Services (`dashboard/services/`)

| File | Replaces GAS | Purpose |
|---|---|---|
| `signnow_packet_service.py` | `SignNow_SendPaperwork.js`, `Telegram_Documents.js` | Two-phase packet assembly: TEMPLATE_MAP (13 docs), multiplication rules, prefill, group invite, embedded link |
| `signnow_service.py` | `SignNow_Integration_Complete.js` | Low-level SignNow API client: copy template, prefill fields, create invite, get embedded link, download PDF |
| `court_email_processor.py` | `CourtEmailProcessor.js` | Gmail scan, email classification (hearing/forfeiture/discharge/BCA), case# + defendant name + datetime extraction, trusted FL county clerk sender whitelist |
| `google_calendar_service.py` | `CourtReminderSystem.js` (calendar portion) | Google Calendar event creation, strong `Case#+Date` dedup key, color coding (court=blue, forfeiture=red, discharge=green), guest sharing |
| `court_reminder_service.py` | `CourtReminderSystem.js` (SMS portion) | 4-touch Twilio SMS scheduler (7d, 3d, 1d, morning-of), reads/writes `court_reminders` MongoDB collection |
| `twilio_service.py` | `TwilioCampaignMonitor.js` | Async Twilio SMS client, send_sms(), schedule_court_reminders() |
| `contact_discovery.py` | *(new)* | OSINT pipeline stub — social media URL construction, address-based relative search |

### Frontend Changes (`dashboard/sl-features.js`)

The **Write Bond modal** now has a **📝 SignNow Packet** section between the POA section and the Text Outreach section:

- **"Send Phase 1 (Indemnitor)"** button → calls `POST /api/bond-lifecycle/phase1/trigger`
- **"Send Phase 2 (Post-Approval)"** button → disabled until Phase 1 succeeds, then calls `POST /api/bond-lifecycle/phase2/trigger` (requires POA number to be filled in)
- Live status badge shows: `Not Sent` → `Phase 1 Sent` → `Phase 2 Sent`
- Both `triggerSignNowPhase1` and `triggerSignNowPhase2` are exported on `window.SL`

---

## What You Need to Wire Up

### 1. `extensions.py` — `get_collection()` Helper

The new services call `from dashboard.extensions import get_collection`. You need to create this or alias it to your existing Motor helper. Expected signature:

```python
# dashboard/extensions.py
from quart import current_app

def get_collection(name: str):
    """Return an async Motor collection from the app's db."""
    return current_app.db[name]
```

If you already have this under a different name, just add the alias. The services use these collection names:
- `court_reminders`
- `contacts`
- `bond_lifecycle_events`
- `audit_events`

### 2. Google Calendar — Service Account

`google_calendar_service.py` uses `google-api-python-client` with a service account JSON. Add to `.env`:

```
GOOGLE_CALENDAR_ID=admin@shamrockbailbonds.biz
GOOGLE_APPLICATION_CREDENTIALS=/app/creds/service-account-key.json
```

The service account needs **"Make changes to events"** permission on the Shamrock Google Calendar. Share the calendar with the service account email in Google Calendar settings.

Install the dependency:
```bash
pip install google-api-python-client google-auth
```

### 3. Court Email Processor — Gmail OAuth

`court_email_processor.py` uses `google-api-python-client` with OAuth2 (not service account — Gmail requires user OAuth). The `CourtEmailProcessor` class takes a `credentials` object. You need to:

1. Create a Google OAuth2 client for `admin@shamrockbailbonds.biz`
2. Store the refresh token in `.env` as `GOOGLE_GMAIL_REFRESH_TOKEN`
3. Wire the processor into a scheduled job (APScheduler, every 15 min) that calls:
   ```python
   processor = CourtEmailProcessor(credentials=build_gmail_credentials())
   results = await processor.process_unread_court_emails()
   ```

Install the dependency:
```bash
pip install google-api-python-client google-auth-oauthlib
```

### 4. SignNow Template IDs — Verify Against Live Account

`signnow_packet_service.py` has a `TEMPLATE_MAP` dict with 13 document template IDs extracted from the old GAS project. **Before going live**, verify each template ID is still valid in the SignNow account (`admin@shamrockbailbonds.biz`). The credentials are in the project knowledge base.

The Phase 1 manifest (indemnitor-facing) sends:
- `defendant_application`, `indemnitor_agreement`, `ssa_release`, `receipt`, `payment_plan` (if applicable)

The Phase 2 manifest (post-approval, agent-internal) sends:
- `appearance_bond` (one per charge — multiplied automatically), `power_of_attorney`, `bail_bond_certificate`, `discharge_of_liability`

### 5. APScheduler Jobs to Add

Add these to your scheduler setup in `app.py` or `main.py`:

```python
# Court email scan — every 15 minutes
scheduler.add_job(
    process_court_emails_job,
    'interval', minutes=15, id='court_email_scan'
)

# Court reminder SMS flush — daily at 8 AM
scheduler.add_job(
    flush_court_reminders_job,
    'cron', hour=8, minute=0, id='court_reminder_flush'
)
```

Where `flush_court_reminders_job` calls `POST /api/court-reminders/process` internally.

### 6. SignNow Webhook Registration

Register the completion webhook in SignNow's dashboard:
- **URL:** `https://your-vps-domain.com/api/webhooks/signnow`
- **Event:** `document.complete`
- **Secret:** value of `SIGNNOW_WEBHOOK_SECRET` in `.env`

The handler in `webhooks.py` will automatically:
1. Verify the HMAC signature
2. Download the completed PDF
3. File it to Google Drive (stub — wire `google_drive_service.py` when ready)
4. Publish a `bond_signed` SSE event to the dashboard
5. Log to `audit_events` collection

---

## Environment Variables Checklist

All new vars are in `.env.example`. The ones that need real values from Brendan:

| Variable | Where to Get It |
|---|---|
| `SIGNNOW_API_TOKEN` | SignNow account → API credentials (or run OAuth flow) |
| `SIGNNOW_WEBHOOK_SECRET` | Create a random secret, register it in SignNow webhook settings |
| `TWILIO_ACCOUNT_SID` | Twilio Console |
| `TWILIO_AUTH_TOKEN` | Twilio Console |
| `TWILIO_FROM_NUMBER` | Twilio Console → Phone Numbers |
| `TWILIO_MESSAGING_SERVICE_SID` | Twilio Console → Messaging Services |
| `GOOGLE_CALENDAR_ID` | `admin@shamrockbailbonds.biz` (already set in `.env.example`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to service account JSON on VPS |
| `GOOGLE_GMAIL_REFRESH_TOKEN` | Run OAuth2 flow for `admin@shamrockbailbonds.biz` |

---

## GAS Files That Are Now Fully Replaced

These GAS scripts are **safe to disable/archive** once you verify the Python equivalents are working in production:

| GAS File | Replaced By |
|---|---|
| `CourtEmailProcessor.js` | `services/court_email_processor.py` + `api/bond_lifecycle.py` |
| `CourtReminderSystem.js` | `services/court_reminder_service.py` + `services/google_calendar_service.py` |
| `SignNow_SendPaperwork.js` | `services/signnow_packet_service.py` |
| `SignNow_Integration_Complete.js` | `services/signnow_service.py` |
| `TwilioCampaignMonitor.js` | `services/twilio_service.py` |
| `Telegram_Documents.js` (calendar + email portions) | `services/google_calendar_service.py` + `services/court_email_processor.py` |

**Do not disable** `Telegram_Documents.js` SignNow template dispatch portion until Phase 2 is verified end-to-end.

---

## Charlotte County Scraper Note

The Charlotte County scraper (`scrapers/counties/charlotte.py`) was rewritten to use **patchright + xvfb-run** to bypass Cloudflare Turnstile. It works on the Hetzner VPS but not in any datacenter/cloud sandbox. The one-time VPS setup is:

```bash
sudo apt-get install -y xvfb
pip install patchright
patchright install chromium
```

---

## Quick Sanity Test After Deploy

```bash
# 1. Health check
curl https://your-vps/health

# 2. Phase 1 trigger (replace with real booking + email)
curl -X POST https://your-vps/api/bond-lifecycle/phase1/trigger \
  -H "Content-Type: application/json" \
  -d '{"signer_email":"test@test.com","signer_name":"Test User","form_data":{"booking_number":"TEST001","bond_amount":5000}}'

# 3. Court reminder schedule
curl -X POST https://your-vps/api/court-reminders/schedule \
  -H "Content-Type: application/json" \
  -d '{"booking_number":"TEST001","defendant_name":"John Doe","phone":"+12395550000","court_datetime":"2026-05-15T09:00:00","court_location":"Lee County Courthouse","case_number":"24-CF-001"}'

# 4. SSE stream (should stay open and emit heartbeats)
curl -N https://your-vps/api/events/stream
```

---

*Handoff generated by Manus · `Shamrock2245/shamrock-leads` · commit `564214b`*
