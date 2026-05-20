# API_REFERENCE.md — ShamrockLeads REST API

> **Last Updated:** 2026-05-15
> **Base URL:** `https://leads.shamrockbailbonds.biz` (production) / `http://localhost:5050` (dev)
> **Auth:** Dashboard PIN via session cookie
> **Framework:** FastAPI with APIRouter modules

---

## Overview

The dashboard exposes **200+ REST endpoints** across **61 API modules** and **36 service modules**. All endpoints are async and use MongoDB Atlas (motor) for data access.

---

## Core Data APIs

### Arrests (`dashboard/api/arrests.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/arrests` | Search/filter arrests with pagination |
| GET | `/api/arrests/<booking>` | Get single arrest by booking number |
| GET | `/api/arrests/export` | Export filtered arrests as CSV |

### Leads (`dashboard/api/leads.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/leads` | Lead queries with score filtering |
| GET | `/api/leads/hot` | Hot leads only (score ≥70) |
| GET | `/api/leads/export` | Export leads as CSV |

### Defendants (`dashboard/api/defendants.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/defendants` | Search defendants (name, county, DOB) |
| GET | `/api/defendants/<id>` | Get defendant profile |
| POST | `/api/defendants` | Create defendant record |
| PATCH | `/api/defendants/<id>` | Update defendant |
| POST | `/api/defendants/merge` | Merge duplicate defendants |

### Defendant Lifecycle (`dashboard/api/defendant_lifecycle.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/defendant-notes/<id>` | Get notes for defendant |
| POST | `/api/defendant-notes/<id>` | Add note |
| PATCH | `/api/defendant-notes/<id>/dnb` | Toggle Do Not Bond flag |
| PATCH | `/api/defendant-notes/<id>/dnc` | Toggle Do Not Contact flag |
| POST | `/api/defendant-notes/<id>/promote` | Promote to pipeline |

### Stats (`dashboard/api/stats.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/stats` | KPI dashboard data |
| GET | `/api/stats/county-breakdown` | Per-county arrest counts |

---

## Bond Management APIs

### Active Bonds (`dashboard/api/bonds.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/active-bonds` | List all active bonds |
| POST | `/api/bonds/record` | Create new bonded case |
| PATCH | `/api/active-bonds/<booking>/status` | Change bond status (7-status Kanban) |
| GET | `/api/active-bonds/<booking>/status-history` | Full status transition history |
| POST | `/api/bonds/bulk-exonerate` | Bulk exonerate bonds |

### Bond Lifecycle (`dashboard/api/bond_lifecycle.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/bond-lifecycle/initiate-signing` | Start SignNow signing flow |
| POST | `/api/bond-lifecycle/signnow-webhook` | SignNow `document.complete` webhook |
| POST | `/api/bond-lifecycle/process-court-email` | Process court discharge email |

### Prospective Bonds (`dashboard/api/prospective_bonds.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/prospective` | List pipeline records |
| POST | `/api/prospective` | Create prospective bond |
| PATCH | `/api/prospective/<id>/stage` | Update pipeline stage |
| DELETE | `/api/prospective/<id>` | Remove from pipeline |

### POA Inventory (`dashboard/api/poa.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/poa/inventory` | List all POAs by surety |
| POST | `/api/poa/add` | Add POA to inventory |
| POST | `/api/poa/assign` | Assign POA to bond |
| PATCH | `/api/poa/release` | Release POA from bond |
| PATCH | `/api/poa/reassign` | Swap POA between bonds |
| GET | `/api/poa/next-available` | Get next available POA for tier |

---

## Intake & Matching APIs

### Intake Queue (`dashboard/api/intake.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/intake/submit` | Submit new intake |
| GET | `/api/intake/queue` | List pending intakes |
| POST | `/api/intake/hydrate/<id>` | Hydrate intake with arrest data |
| POST | `/api/intake/process/<id>` | Process intake → create indemnitor |
| POST | `/api/intake/archive/<id>` | Archive intake |
| GET | `/api/intake/stats` | Intake queue statistics |

### Matching (`dashboard/api/matching.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/matching/match` | Run matching engine |
| GET | `/api/matching/candidates/<intake_id>` | Get match candidates |
| POST | `/api/matching/confirm` | Confirm a match |
| POST | `/api/matching/reject` | Reject a match |
| POST | `/api/matching/override` | Manual match override |

---

## Paperwork & Signatures

### Paperwork (`dashboard/api/paperwork.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/paperwork/generate` | Generate SignNow packet |
| POST | `/api/paperwork/deliver` | Send packet to indemnitor |
| GET | `/api/paperwork/packets/<bond_id>` | List packets for bond |
| GET | `/api/paperwork/status/<packet_id>` | Check packet signing status |

### Payments (`dashboard/api/payments.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/payments/log` | Log a payment |
| GET | `/api/payments/<bond_id>` | Get payments for bond |
| GET | `/api/payments/delinquent` | List delinquent payment plans |

### Payment Plans (`dashboard/api/payment_plans.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/payment-plans` | Create payment plan |
| GET | `/api/payment-plans/<bond_id>` | Get plan for bond |
| PATCH | `/api/payment-plans/<id>` | Update plan |

---

## Communication APIs

### iMessage / BlueBubbles (8 modules)
| Module | Prefix | Purpose |
|--------|--------|---------|
| `bb_health_monitor.py` | `/api/bb/health` | Server health, connectivity status |
| `bb_webhook_receiver.py` | `/api/bb/webhook` | Real-time message event receiver |
| `bb_prospecting.py` | `/api/bb/prospect` | iMessage-first outreach |
| `bb_scheduled_messages.py` | `/api/bb/schedule` | Scheduled court/payment reminders |
| `bb_document_delivery.py` | `/api/bb/documents` | PDF and signing link delivery |
| `bb_contact_sync.py` | `/api/bb/contacts` | Mac Contacts ↔ MongoDB sync |
| `bb_private_api.py` | `/api/bb/private` | Extended features (unsend, edit, typing) |
| `bb_firebase_sync.py` | `/api/bb/firebase` | Firebase Firestore URL auto-sync |

### Outreach (`dashboard/api/outreach.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/outreach/start` | Start drip campaign sequence |
| GET | `/api/outreach/active` | List active campaigns |
| PATCH | `/api/outreach/<id>/pause` | Pause campaign |
| POST | `/api/outreach/send-imessage` | Send single iMessage |

### Agent Brain (`dashboard/api/agent_brain.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/agent/reply` | Generate AI auto-reply (Shannon) |
| GET | `/api/agent/config` | Get agent configuration |
| PATCH | `/api/agent/config` | Update agent settings |

---

## Court & Compliance APIs

### Court Reminders (`dashboard/api/court_reminders.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/court-reminders/auto-scan` | Scan bonds, schedule SMS reminders |
| GET | `/api/court-reminders/scheduled` | List scheduled reminders |

### Calendar (`dashboard/api/calendar.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/calendar/sync-gcal` | Push court dates to Google Calendar |
| GET | `/api/calendar/events` | Get calendar events |

### Discharge Monitor (`dashboard/api/discharge_monitor.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/discharge/scan` | Scan Gmail for discharge emails |
| GET | `/api/discharge/pending` | List pending discharges |

### Re-Arrest Detector (`dashboard/api/rearrest_detector.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/rearrest/scan` | Cross-reference new arrests vs active bonds |
| GET | `/api/rearrest/alerts` | List re-arrest alerts |

### OSINT Contacts (`dashboard/api/contacts.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/contacts/discover` | Run OSINT contact discovery |
| GET | `/api/contacts/<defendant_id>` | Get discovered contacts |

---

## Operations APIs

### Scraper Control (`dashboard/api/scraper_control.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/scraper/status` | Fleet status for all scrapers |
| POST | `/api/scraper/trigger/<county>` | Manual scraper trigger |
| POST | `/api/scraper/enable/<county>` | Force-enable disabled scraper |

### Events / Audit (`dashboard/api/events.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/events` | Query audit events |
| GET | `/api/events/<entity_id>` | Events for specific entity |

### Data Retention (`dashboard/api/data_retention.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/retention/purge` | Run tiered data purge |
| GET | `/api/retention/stats` | Storage usage statistics |

### Analytics (`dashboard/api/analytics.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/analytics/revenue` | Revenue data for sparkline |
| GET | `/api/analytics/treemap` | Bond amount treemap by county |
| GET | `/api/analytics/risk-heatmap` | Risk score heatmap |

### Reports (`dashboard/api/reports.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/reports/liability` | Current liability report |
| GET | `/api/reports/commissions` | Agent commission report |
| GET | `/api/reports/reconciliation` | Monthly reconciliation |

### Tracking (`dashboard/api/tracking.py`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/tracking/devices` | List tracked devices |
| GET | `/api/tracking/positions/<device_id>` | Position history |
| POST | `/api/tracking/geofence` | Create geofence |

---

## Health & Webhooks

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Service health check |
| POST | `/api/webhooks/wix` | Wix intake webhook |
| POST | `/api/webhooks/signnow` | SignNow document completion |

---

*Maintained by: Brendan / Shamrock Active Software LLC*
