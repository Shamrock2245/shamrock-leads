# Node-RED / External Automation Schedule Pack

> **Auth:** every call needs `X-API-Key: $GAS_API_KEY` (or `?api_key=`).  
> **Base:** `https://leads.shamrockbailbonds.biz`  
> **Machine JSON:** `GET /api/automation/schedule`

In-process FastAPI crons already run revenue automations (speed-to-contact, paperwork chase, intake recovery, POA low-stock, weekly surety reports). Node-RED is optional for **ops digests**, **surety report routing**, and **cross-system** hooks.

## Recommended Node-RED injects (America/New_York)

| When | Path | Body |
|------|------|------|
| Daily 08:00 | `POST /api/automation/lead-qualification` | `{"hours_back":24,"hot_threshold":70}` |
| Daily 08:30 | `POST /api/automation/ops-digest` | `{"hours_back":24,"post_slack":true}` |
| Daily 12:00 | `POST /api/automation/bond-lifecycle` | `{"stuck_days":3}` |
| Daily 17:00 | `POST /api/automation/risk-mitigation` | `{"court_hours":48}` |
| Every 15m | `POST /api/automation/court-email-scan` | `{"since_hours":1}` (optional; in-process also runs) |
| Mon 07:00 | `POST /api/automation/bond-report` | `{"surety":"OSI","store":true}` |
| Mon 07:05 | `POST /api/automation/bond-report` | `{"surety":"PALMETTO","store":true}` |
| Mon 07:15 | `POST /api/automation/discharge-report` | `{"surety":"ALL","days_back":7}` |

## Revenue modes (Super CRM → Automations)

| Key | Default | Safe client contact |
|-----|---------|---------------------|
| `speed_to_contact` | **ON** · `mode=review` | Queues outreach for approval — no free-send |
| `paperwork_chase` | **ON** · `mode=review` | Staff notifications; set `full_auto` to BB-nudge clients |
| `intake_recovery` | **ON** · `mode=review` | Staff notifications; set `full_auto` to recover via iMessage |
| `poa_low_stock` | **ON** | Slack when tier inventory ≤ 5 |
| `surety_weekly_reports` | **ON** | Stores XLSX in `generated_reports` + Slack |

Prime Directive #6: **no automated client contact without human approval** unless an operator flips `mode` to `full_auto`.

## Import tip (Node-RED)

1. HTTP Request node → Method POST → URL from table  
2. Headers: `X-API-Key` = `GAS_API_KEY` env, `Content-Type: application/json`  
3. Payload from inject JSON  
4. Optional: function node to format `counts` into Slack webhook  
