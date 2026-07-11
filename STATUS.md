# ShamrockLeads — True Status

> **Last verified:** 2026-07-10  
> **Repo:** `Shamrock2245/shamrock-leads` · branch `main`  
> **Product URL:** `https://leads.shamrockbailbonds.biz`  
> **Role:** Bond **Auto-CRM** pillar of **Shamrock’s Platform** (not Bail School LMS)  
> **Platform:** `docs/PLATFORM.md` · **Prod checklist:** `docs/ECOSYSTEM_PROD_CHECKLIST.md`

---

## What “Auto-CRM” means here

After a **phone number** (and usually defendant/county) enters the system, the bond lifecycle should run with **minimal human intervention**, except risk/match gates:

```
Phone / arrest lead → outreach sequences → intake → match (human on ambiguity)
  → paperwork → payment → active bond → court/GPS/FTA → close
```

**BlueBubbles (iMessage)** is the preferred consumer rail for outreach; full office reliability is an ops task (tunnel + env), not a missing dashboard tab.

**Bail School** is a **separate P&L** (`shamrock-bail-school`). Leads may share brand, Slack, and secrets hygiene — not course progress state.

---

## Code on `main` (recent, implemented)

| Area | Status |
|------|--------|
| 90 county scrapers (52 FL, 38 GA), scoring, Slack, Mongo | ✅ |
| FastAPI dashboard Super CRM (tabs, lifecycle, intake, etc.) | ✅ |
| Hub APIs: `/api/crm/health`, `/overview`, `/pipeline`, `/search` | ✅ July 2026 |
| Omnibar → CRM search | ✅ |
| Mongo index script expanded for CRM collections | ✅ |
| Webhooks fail-closed without secrets | ✅ |
| Hardcoded Mongo/BB passwords scrubbed from scripts | ✅ |
| Ecosystem secrets checklist | `scripts/check_ecosystem_secrets.py` |
| Super CRM docs | `docs/SUPER_CRM.md`, `docs/ECOSYSTEM.md` |
| **Surety realignment (July 2026)** | ✅ |
| &nbsp;&nbsp;`bonds.py` — `surety_id` + `insuranceCompany` both forwarded to GAS | ✅ |
| &nbsp;&nbsp;`bonds.py` — agent constants (Brendan O'Neal / P139768) injected in every GAS payload | ✅ |
| &nbsp;&nbsp;`bond_lifecycle.py` — Drive filing uses `OSI/PALMETTO` surety subfolder | ✅ |
| &nbsp;&nbsp;`bond_lifecycle.py` — `packet_id` undefined variable fixed | ✅ |
| &nbsp;&nbsp;`signnow_packet_service.py` — agent name/license locked to Brendan O'Neal / P139768 | ✅ |
| &nbsp;&nbsp;`intake.py` — `surety_id` persisted to MongoDB `intake_queue` | ✅ |
| **Bond check-in A+C (July 2026)** — transparent portal GPS + condition policy | ✅ code |
| &nbsp;&nbsp;Policy `docs/policies/monitoring-checkin-policy.md` | ✅ |
| &nbsp;&nbsp;Post-SignNow → enable `check_in_required` + staff task (no auto-text) | ✅ |
| &nbsp;&nbsp;Portal consent UI + `POST /api/portal/{token}/checkin` consent gate | ✅ |
| &nbsp;&nbsp;Staff enable/send: `/api/active-bonds/{bk}/enable-checkin`, `send-checkin-link` | ✅ |
| **Traccar GPS (B)** continuous via in-stack Traccar Client / OsmAnd | ✅ rewired (not external vendor) |
| Portal check-in → Traccar OsmAnd inject + `/api/geo-intel/*` routes fixed | ✅ |

---

## Honest gaps / ops

Track live cutover in **`docs/ECOSYSTEM_PROD_CHECKLIST.md`** (P0/P1). Summary:

| Item | Status |
|------|--------|
| BlueBubbles production reliability (office Mac + tunnel) | ⏳ Ops (checklist D1–D2) |
| `ENV=production` + strong `SECRET_KEY` + `DASHBOARD_PIN` on VPS | Verify on host (checklist B1) |
| Atlas network restriction / rotated Mongo password if ever leaked | Ops |
| Gmail discharge / GCal / Drive OAuth | Env-gated (501/dry-run until configured) |
| Local PDF stitcher full blank packet | ✅ 2026-07-10 (`paperwork_pdf_service`) — SignNow remains primary |
| Auto-CRM “phone only → fully autopilot” with explicit human gates | Product next (Phase 18 / checklist P2.5) |
| Hetzner deploy after each `main` push | Depends on GitHub Action + VPS health |

---

## Related repos

| Repo | Role |
|------|------|
| `shamrock-bail-portal-site` | Public site + GAS bond factory + school payment unlock |
| `shamrock-bail-school` | Student LMS education funnel |
| `shamrock-node-red` | **Automation fabric (n8n/Zapier analog)** — crons, webhooks, Watchdog, cross-service routing |

Run from this repo:

```bash
python scripts/check_ecosystem_secrets.py
python scripts/check_ecosystem_secrets.py --strict
```

## Super-admin + court automation (July 2026)

- Super-admin: `admin@shamrockbailbonds.biz` (see `dashboard/auth/super_admin.py`)
- Automation API (GAS_API_KEY): `/api/automation/lead-qualification|bond-lifecycle|risk-mitigation|court-email-scan|bond-report|discharge-report|ops-digest|schedule`
- Official OSI/Palmetto XLSX bond & discharge reports (`dashboard/services/bond_report_xlsx.py`)
- Court email: Calendar + client email + BlueBubbles (`court_email_scheduler`)

## Revenue automations (July 2026 — review-first)

Enabled by default in **review** mode (migration `_revenue_automations_v1`):

| Cron | Default mode | Client contact? |
|------|--------------|-----------------|
| `speed_to_contact` | `review` | Queues outreach for staff approval |
| `paperwork_chase` | `review` | Staff notifications; `full_auto` to BB-nudge |
| `intake_recovery` | `review` | Staff notifications; `full_auto` to iMessage |
| `poa_low_stock` | on | Slack when POA tier ≤ threshold |
| `surety_weekly_reports` | on | XLSX → `generated_reports` + Slack |

Node-RED pack: `GET /api/automation/schedule` · docs `docs/automation/NODE_RED_SCHEDULE.md`

## Lifecycle suite (July 2026 — on the clock)

| Cron | Interval | Behavior |
|------|----------|----------|
| `forfeiture_scan` | 4h | Score active bonds; write risk fields; tasks + Slack for high/critical |
| `signnow_poller` | 30m | Poll SignNow open packets → signed/void; create `collect_payment` tasks |
| `compliance_backfill` | 6h | Active bonds missing check-in/court tasks → `TaskEngine` suite |
| `matching_backlog` | 1h | `MatchingEngine.batch_match`; Slack digest for human review |

Migration: `_lifecycle_automations_v1` (enable once on config load).

