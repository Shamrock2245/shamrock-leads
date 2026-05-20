# BRAND.md — Shamrock Bail Bonds: Who We Are & What We're Building

> **Every AI agent working in this repository must read this document first.**
> This is the strategic, cultural, and technical north star for the entire platform.

---

## 1. Who We Are

**Shamrock Bail Bonds** is a Florida-licensed bail bond agency headquartered in Southwest Florida, operated by **Brendan**. We are building the most intelligent, automated, and compliant bail bond operation in the state of Florida — and eventually, the nation.

We currently write **$3–5 million per year** in bonds, primarily in Lee County, with active operations in Hendry, Charlotte, and Collier counties. Our strategic goal is to scale to a **67-county statewide operation** with a writing capacity of **$20–50 million per year**.

We are not a software company. We are a bail bond agency that has built its own software. That distinction matters: every system we build must serve the bondsman first, the technology second.

---

## 2. Our Sureties

We write bonds under two surety companies. Every agent must know these cold.

| Surety | ID | License Scope | Agent Retention (per $10K) | Notes |
|--------|----|---------------|---------------------------|-------|
| **O'Shaughnahill Surety Inc. (OSI)** | `osi` | Florida only | $875 | Preferred when available |
| **Palmetto Surety Corporation** | `palmetto` | FL, SC, NC, TN, TX, CT, LA, MS | $850 | Required for out-of-state cases |

**Rule:** OSI is always preferred. Use Palmetto when OSI inventory is depleted for the needed tier, or when the case is outside Florida.

---

## 3. Our Identity — Non-Negotiable

| Identity Element | Value |
|-----------------|-------|
| **GitHub Account** | `Shamrock2245` |
| **Primary Email** | `admin@shamrockbailbonds.biz` |
| **Production VPS** | `178.156.179.237` (Hetzner, root) |
| **Dashboard URL** | `http://178.156.179.237:8088/` |
| **Public Dashboard** | `https://leads.shamrockbailbonds.biz` (Nginx reverse proxy) |
| **iMessage Bridge** | ngrok permanent tunnel → office iMac BlueBubbles |
| **Primary Repo** | `Shamrock2245/shamrock-leads` |
| **Portal Repo** | `Shamrock2245/shamrock-bail-portal-site` |
| **Ops Repo** | `Shamrock2245/shamrock-node-red` |
| **Tracker Repo** | `Shamrock2245/shamrock-bond-tracker` |
| **iMessage Number** | `239-955-0178` (office iMac) |
| **iMessage Account** | `shamrockbailoffice@gmail.com` |

**Absolute Rule:** No code, no commits, no API calls, no emails may reference or use any identity outside the Shamrock family above. Any reference to "WTF" or other non-Shamrock entities is a hard violation.

---

## 4. Our Strategic Vision

We are building a platform that will surpass **Captira** and **Bail Books** — the current industry leaders — in both functionality and user experience. Our competitive advantages are:

1. **Speed** — We know about an arrest before the family does. Our scrapers run every 10 minutes on core counties.
2. **Intelligence** — Every arrest is scored 0–100 before a human sees it. Hot leads get immediate Slack alerts.
3. **Automation** — From first outreach to signed paperwork to payment collection, the system handles the workflow.
4. **Compliance** — Every state change is audited. Every PII access is logged. We are building toward SOC II readiness.
5. **Reach** — 50 active county scrapers today. 67 counties is the target. Statewide coverage is the moat.

---

## 5. Our Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Scraping** | Python 3.12, DrissionPage, Patchright, curl_cffi | County jail roster ingestion |
| **Scheduling** | APScheduler | Per-county cron with staggered intervals |
| **Database** | MongoDB Atlas (motor, async) | Primary data store for all entities |
| **Dashboard** | FastAPI + Uvicorn on port 5050 | 15-tab intelligence dashboard |
| **Frontend** | Vanilla JS + CSS (~25,700 lines, 32 modules) | Premium dark-theme UI |
| **iMessage** | BlueBubbles API (office iMac bridge) | Human-feel outreach automation |
| **AI** | OpenAI GPT-4o | Auto-reply agent, lead enrichment |
| **Signatures** | SignNow API | E-signature orchestration |
| **Payments** | SwipeSimple | Bond premium collection |
| **Alerts** | Slack SDK (webhook blocks) | Real-time operational alerts |
| **GAS** | Google Apps Script | Sheets integration, write-bond forwarding |
| **Hosting** | Hetzner VPS (Docker Compose) | Production infrastructure |
| **CI/CD** | GitHub Actions | Automated deployments |
| **Ops** | Node-RED | 39+ cron jobs, ops dashboard |
| **Location** | MaxMind GeoLite2 + Twilio SMS | Bond tracker / flight risk scoring |

---

## 6. Our Design Standard

The dashboard is a **premium, Fortune 50-level intelligence platform**. Every UI decision must reflect this.

- **Color palette:** Dark theme. `#0f172a` background, `#10b981` accent (Shamrock green), `#f59e0b` warning amber, `#ef4444` danger red.
- **Typography:** System font stack. Clean, readable, no decorative fonts.
- **Components:** Design tokens via CSS custom properties (`--accent`, `--bg-card`, `--text-primary`, etc.). Never hardcode colors.
- **Animations:** Subtle. Count-up counters on KPI cards, hover lift on cards (2px translateY), smooth tab transitions.
- **Mobile:** PWA-ready. Touch targets ≥ 44px. No horizontal scroll on mobile.
- **Competitor benchmark:** Captira and Bail Books are the floor, not the ceiling.

---

## 7. Our Data Chain — The Law

No shortcuts. No exceptions. Every bond follows this exact chain:

```
ArrestLead (scraped)
  → Defendant (normalized, deduplicated)
    → Indemnitor Intake (from Wix, GAS, Telegram, or voice)
      → Match (validated, confidence-scored)
        → BondCase (Surety + POA + Case Number)
          → DocumentPacket (SignNow template, hydrated)
            → Signature (SignNow webhook confirms)
              → Payment (SwipeSimple, premium collected)
                → Posted Bond (court-ready)
```

**Dedup key:** `County + Booking_Number` for all arrest records. This is immutable.

---

## 8. Our Compliance Standards

We are building toward **SOC II Type II** compliance. All agents must build with this in mind.

| Principle | Implementation |
|-----------|---------------|
| **Availability** | Docker health checks, APScheduler self-healing, Slack alerts on failure |
| **Confidentiality** | PII never logged to Slack/console. MongoDB Atlas encryption at rest. |
| **Integrity** | Immutable audit events (`audit_events` collection). No record mutation without audit trail. |
| **Security** | Secrets via environment variables only. No hardcoded credentials. `WIX_WEBHOOK_SECRET` for webhook auth. |
| **Processing Integrity** | Dedup engine prevents duplicate records. Match validation gates paperwork generation. |

**Reference implementations:** [strongdm/comply](https://github.com/strongdm/comply), [getprobo/probo](https://github.com/getprobo/probo), [getprobo/awesome-compliance](https://github.com/getprobo/awesome-compliance).

---

## 9. Our Agent Workforce

| Agent | Nickname | Status | Primary Files |
|-------|----------|--------|--------------|
| Arrest Scraper | "The Clerk" | ✅ Live | `scrapers/counties/*.py` |
| Lead Scorer | "The Analyst" | ✅ Live | `scoring/lead_scorer.py` |
| Health Monitor | "The Watchdog" | ✅ Live | `writers/slack_notifier.py` |
| Intake Matcher | "The Matcher" | ✅ Live | `dashboard/api/matching.py` |
| Paperwork Generator | "The Notary" | ✅ Live | `dashboard/api/paperwork.py` |
| Signature Orchestrator | "The Closer" | ✅ Live | `dashboard/services/signnow_packet_service.py` |
| iMessage Agent | "Shannon" | ✅ Live | `dashboard/api/agent_brain.py` |
| Court Reminder | "The Court Clerk" | ✅ Live | `dashboard/services/court_reminder_service.py` |
| Discharge Monitor | "The Discharge Monitor" | ✅ Live | `dashboard/api/discharge_monitor.py` |
| OSINT Finder | "The Finder" | ✅ Live | `dashboard/services/contact_discovery.py` |
| Payment Collector | "The Treasurer" | ✅ Live | `dashboard/api/payments.py` |
| Audit Logger | "The Auditor" | ✅ Live | `dashboard/api/events.py` |
| Re-Arrest Detector | "The Sentinel" | ✅ Live | `dashboard/api/rearrest_detector.py` |
| Data Retention | "The Janitor" | ✅ Live | `dashboard/api/data_retention.py` |

---

## 10. Required Read Order for New Agents

Every AI agent onboarding to this repository must read these files in order before writing a single line of code:

1. **`BRAND.md`** (this file) — Who we are, what we're building, non-negotiables
2. **`AGENTS.md`** — Digital workforce roles, safety rules, scoring system, prime directives
3. **`DATA_MODEL.md`** — Entity definitions, identity boundaries, phase markers
4. **`ROADMAP.md`** — What is implemented vs. planned; never build against a `[PLANNED]` schema
5. **`docs/policies/surety-policy.md`** — POA inventory, surety selection, premium splits
6. **`docs/policies/matching-policy.md`** — Match confidence scoring, validation gates
7. **`docs/policies/signature-policy.md`** — Packet binding rules, signing workflow
8. **`docs/runbooks/intake-to-signature.md`** — End-to-end workflow walkthrough
9. **`.agent/skills/`** — Specialized skills for scraping, debugging, deployment, and more

---

## 11. The Shamrock Standard

Every pull request, every commit, every feature must meet this bar:

- **Zero silent failures.** Every error fires a Slack alert.
- **Zero PII leaks.** Phone numbers, SSNs, and addresses never appear in logs.
- **Zero duplicate records.** The dedup engine is always consulted before any write.
- **Zero hardcoded secrets.** Environment variables only.
- **Zero broken chains.** The data chain is law. No paperwork before a validated match.
- **Zero identity confusion.** ArrestLead ≠ Defendant ≠ Indemnitor ≠ BondCase. These are separate entities.
- **Zero non-Shamrock resources.** We are `Shamrock2245`. We use `admin@shamrockbailbonds.biz`. Full stop.

---

*Last updated: 2026-05-08 | Maintained by: Brendan / Shamrock Active Software LLC*
