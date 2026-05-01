# 📧 Gmail Discharge Monitor — Setup Guide

> **Feature:** `discharge_monitor.py` — Scans Gmail for court-issued discharge/exoneration emails and automatically marks bonds as exonerated in MongoDB.

---

## What It Does

When a Florida court clerk sends a discharge email (e.g., "Bond Exonerated", "Order of Discharge", "Case Dismissed"), the discharge monitor:

1. Searches the configured Gmail inbox for matching subject lines
2. Extracts the booking number or defendant name from the email body
3. Matches it to an active bond in MongoDB
4. Queues the bond for exoneration (status → `exonerated`, tracking stops)
5. Returns a summary of processed discharges

---

## Prerequisites

- A Gmail account that receives discharge emails from Florida court clerks
- Google Cloud project with Gmail API enabled
- OAuth 2.0 credentials (Desktop App type)

---

## Step-by-Step Setup

### 1. Enable Gmail API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use an existing one)
3. Navigate to **APIs & Services → Library**
4. Search for **Gmail API** and click **Enable**

### 2. Create OAuth 2.0 Credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. Application type: **Desktop app**
4. Name: `ShamrockLeads Discharge Monitor`
5. Download the JSON file — save it as `credentials/gmail_credentials.json`

### 3. Run First-Time Authorization

```bash
cd /path/to/shamrock-leads
python3 dashboard/api/discharge_monitor.py --authorize
```

This opens a browser window. Log in with the Gmail account that receives discharge emails. A `credentials/gmail_token.json` file will be created.

### 4. Set Environment Variables

Add to your `.env` file:

```env
GMAIL_CREDENTIALS_PATH=credentials/gmail_credentials.json
GMAIL_TOKEN_PATH=credentials/gmail_token.json
GMAIL_DISCHARGE_LABEL=discharge          # Optional: Gmail label to filter
GMAIL_DISCHARGE_DAYS_BACK=7              # How many days back to scan
```

### 5. Test the Connection

```bash
curl -X POST http://localhost:5000/api/discharge/scan
```

Expected response:
```json
{
  "success": true,
  "found": 0,
  "processed": 0,
  "message": "Scan complete"
}
```

---

## Email Subject Patterns Recognized

The monitor searches for emails matching these patterns (case-insensitive):

| Pattern | Example |
|---------|---------|
| `bond exonerated` | "Bond Exonerated — Case 2024-CF-001234" |
| `order of discharge` | "Order of Discharge — John Smith" |
| `case dismissed` | "Case Dismissed — Booking #2024-LEE-001" |
| `bond released` | "Bond Released — Lee County" |
| `surety discharged` | "Surety Discharged from Liability" |
| `exoneration` | "Exoneration Notice — Circuit Court" |

---

## Manual Trigger

From the Court Calendar tab, click **📧 Check Discharge Emails** to run an on-demand scan.

The endpoint is also available directly:

```bash
curl -X POST http://localhost:5000/api/discharge/scan
```

---

## Scheduled Scanning

Add to crontab for automatic daily scanning:

```cron
0 8 * * * curl -s -X POST http://localhost:5000/api/discharge/scan >> /var/log/discharge_scan.log
```

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `GMAIL_NOT_CONFIGURED` | Check that `GMAIL_CREDENTIALS_PATH` and `GMAIL_TOKEN_PATH` env vars are set |
| `Token expired` | Delete `gmail_token.json` and re-run `--authorize` |
| `No discharges found` | Check email subject patterns — court clerks vary by county |
| `Booking number not matched` | Verify the booking number format in the email matches MongoDB |

---

*Last updated: May 2026 — ShamrockLeads v2.0*
