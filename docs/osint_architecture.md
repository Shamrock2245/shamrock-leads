# OSINT Intelligence Module v2 — Architecture

## Overview

Expansion of the OSINT Intelligence tab from 2-engine (Maigret + Blackbird) to a 4-engine workstation adding **Sherlock** and **SpiderFoot**. Full end-to-end wiring: schema → worker → router → exports → UI.

## Engine Matrix

| Engine     | Input Types              | Output Focus                  | Isolation     |
|-----------|--------------------------|-------------------------------|---------------|
| Maigret   | Usernames                | Social accounts (500+ sites)  | CLI in worker |
| Blackbird | Username, Email          | Social accounts (WhatsMyName) | CLI in worker |
| Sherlock  | Usernames                | Social accounts (400+ sites)  | Docker sidecar|
| SpiderFoot| Email, Phone, Name, Domain| Entities (addresses, emails, phones, social, DNS) | Docker sidecar|

## Mongo Schema (`osint_scans` collection — replaces `osint_profiles`)

```json
{
  "_id": ObjectId,
  "subject_type": "defendant" | "indemnitor",
  "subject_id": "ObjectId string",
  "full_name": "string | null",
  "scan_requested_by": "admin",
  "engines_requested": ["maigret", "sherlock", "blackbird", "spiderfoot"],
  "scan_params": {
    "usernames": [],
    "email": "string | null",
    "phone": "string | null",
    "full_name": "string | null",
    "deep_scan": false,
    "second_opinion": false
  },
  "status": "queued" | "running" | "completed" | "partial" | "failed",
  "progress": {
    "maigret": "pending" | "running" | "completed" | "failed" | "skipped",
    "sherlock": "...",
    "blackbird": "...",
    "spiderfoot": "..."
  },
  "results": {
    "accounts": [
      {
        "platform": "string",
        "url": "string",
        "username": "string | null",
        "profile_data": {},
        "source": "maigret | sherlock | blackbird | spiderfoot",
        "confidence": "found | likely | uncertain",
        "category": "social | forum | dating | professional | other",
        "relevance": "relevant | irrelevant | unreviewed"
      }
    ],
    "entities": [
      {
        "type": "email | phone | address | name | domain | ip",
        "value": "string",
        "source": "spiderfoot",
        "module": "sfp_xxx",
        "confidence": "high | medium | low"
      }
    ],
    "total_accounts": 0,
    "total_entities": 0,
    "platforms_found": []
  },
  "risk_signals": [],
  "osint_risk_score": 0,
  "ai_summary": "string | null",
  "raw_outputs": {
    "maigret": {},
    "sherlock": {},
    "blackbird": {},
    "spiderfoot": {}
  },
  "tool_results": {},
  "warnings": [],
  "error": "string | null",
  "notes": "string | null",
  "created_at": "datetime",
  "started_at": "datetime | null",
  "completed_at": "datetime | null"
}
```

## API Endpoints (FastAPI Router)

| Method | Path                              | Purpose                          |
|--------|-----------------------------------|----------------------------------|
| POST   | /api/osint/scan                   | Initiate multi-engine scan       |
| GET    | /api/osint/scan/{id}              | Get scan status + results        |
| GET    | /api/osint/scan/{id}/raw          | Full report with raw outputs     |
| GET    | /api/osint/scans                  | List scans (filter/sort/paginate)|
| PATCH  | /api/osint/scan/{id}/findings     | Mark findings relevant/irrelevant|
| POST   | /api/osint/scan/{id}/attach       | Copy summary to subject record   |
| GET    | /api/osint/scan/{id}/export/json  | Export full JSON                 |
| GET    | /api/osint/scan/{id}/export/csv   | Export flat CSV                  |
| GET    | /api/osint/scan/{id}/export/pdf   | Export PDF summary               |
| GET    | /api/osint/status                 | Tool availability + queue info   |
| POST   | /api/osint/trape/session          | (existing) Trape session         |

## Worker API (osint-worker)

Extended `/v1/scan` request body:
```json
{
  "usernames": [],
  "full_name": "string | null",
  "email": "string | null",
  "phone": "string | null",
  "deep_scan": false,
  "engines": ["maigret", "sherlock", "blackbird", "spiderfoot"],
  "second_opinion": false
}
```

Worker runs engines concurrently where possible, returns unified result.

## Docker Architecture

```
osint-worker (existing container, extended)
├── Maigret (pip install)
├── Blackbird (git clone)
├── Sherlock (pip install sherlock-project)
└── SpiderFoot (pip install spiderfoot OR Docker sidecar)
```

All tools installed in the single osint-worker container for simplicity.

## UI Wireframe

```
┌─────────────────────────────────────────────────────────────────────┐
│ OSINT Intelligence Workstation                    [Admin] [Status]  │
├─────────────────────────────────────────────────────────────────────┤
│ ┌─── New Scan ────────────────────────────────────────────────────┐ │
│ │ Subject Type: [Defendant ▼]  Subject ID: [____________]        │ │
│ │                                                                  │ │
│ │ Engines: [■ Maigret] [■ Sherlock] [□ Blackbird] [□ SpiderFoot] │ │
│ │                                                                  │ │
│ │ Usernames: [________________]  (Maigret, Sherlock, Blackbird)   │ │
│ │ Email:     [________________]  (Blackbird, SpiderFoot)          │ │
│ │ Phone:     [________________]  (SpiderFoot)                     │ │
│ │ Full Name: [________________]  (SpiderFoot, username derivation)│ │
│ │                                                                  │ │
│ │ [□ Deep Scan] [□ Second Opinion]  Notes: [________________]    │ │
│ │                                                                  │ │
│ │ [════════════ Run OSINT Scan ════════════]                      │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│ ┌─── Scan History ────────────────────────────────────────────────┐ │
│ │ [Search...] [Filter: All ▼] [Sort: Newest ▼]                   │ │
│ │ ┌──────────────────────────────────────────────────────────────┐│ │
│ │ │ John Smith · Defendant · Maigret+Sherlock · 23 accounts      ││ │
│ │ │ ● Completed · Jul 23, 2026 2:15 PM    [View] [Export ▼]     ││ │
│ │ └──────────────────────────────────────────────────────────────┘│ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│ ┌─── Report Detail ───────────────────────────────────────────────┐ │
│ │ [Summary] [Accounts] [Entities] [Risk] [Raw]     [JSON][CSV][PDF]│ │
│ │                                                                  │ │
│ │ ┌─ Summary ──────────────────────────────────────────────────┐  │ │
│ │ │ 23 Accounts · 5 Entities · Risk +18 (advisory)            │  │ │
│ │ │ Engines: Maigret ✓ · Sherlock ✓ · Blackbird ✗ skipped     │  │ │
│ │ └───────────────────────────────────────────────────────────-┘  │ │
│ │                                                                  │ │
│ │ ┌─ Accounts ─────────────────────────────────────────────────┐  │ │
│ │ │ [Filter: All ▼] [Source: All ▼]                            │  │ │
│ │ │ ┌─────────────────────────────────────────────────────────┐│  │ │
│ │ │ │ 🐦 Twitter · @jsmith · https://...  [Open] [✓ Relevant]││  │ │
│ │ │ │ 📘 Facebook · john.smith · https://...  [Open] [? Unrev]││  │ │
│ │ │ └─────────────────────────────────────────────────────────┘│  │ │
│ │ └────────────────────────────────────────────────────────────┘  │ │
│ └──────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```
