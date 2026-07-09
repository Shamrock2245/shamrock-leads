# ShamrockLeads — True Status

> **Last verified:** 2026-07-08  
> **Repo:** `Shamrock2245/shamrock-leads` · branch `main`  
> **Product URL:** `https://leads.shamrockbailbonds.biz`  
> **Role:** Bond **Auto-CRM** + arrest intelligence (not Bail School LMS)

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
| 52 county scrapers, scoring, Slack, Mongo | ✅ |
| FastAPI dashboard Super CRM (tabs, lifecycle, intake, etc.) | ✅ |
| Hub APIs: `/api/crm/health`, `/overview`, `/pipeline`, `/search` | ✅ July 2026 |
| Omnibar → CRM search | ✅ |
| Mongo index script expanded for CRM collections | ✅ |
| Webhooks fail-closed without secrets | ✅ |
| Hardcoded Mongo/BB passwords scrubbed from scripts | ✅ |
| Ecosystem secrets checklist | `scripts/check_ecosystem_secrets.py` |
| Super CRM docs | `docs/SUPER_CRM.md`, `docs/ECOSYSTEM.md` |

---

## Honest gaps / ops

| Item | Status |
|------|--------|
| BlueBubbles production reliability (office Mac + tunnel) | ⏳ Ops (scheduled separately) |
| `ENV=production` + strong `SECRET_KEY` + `DASHBOARD_PIN` on VPS | Verify on host |
| Atlas network restriction / rotated Mongo password if ever leaked | Ops |
| Auto-CRM “phone only → fully autopilot” with explicit human gates | Product next (not claimed complete) |
| Hetzner deploy after each `main` push | Depends on GitHub Action + VPS health |

---

## Related repos

| Repo | Role |
|------|------|
| `shamrock-bail-portal-site` | Public site + GAS bond factory + school payment unlock |
| `shamrock-bail-school` | Student LMS education funnel |

Run from this repo:

```bash
python scripts/check_ecosystem_secrets.py
python scripts/check_ecosystem_secrets.py --strict
```
