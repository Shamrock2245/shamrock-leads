# Shamrock Platform — Ecosystem Production Checklist

> **Purpose:** Single source of “are we production?” across the whole platform.  
> **Platform thesis:** [`PLATFORM.md`](./PLATFORM.md)  
> **Last Updated:** 2026-07-10  
> **Owner:** Brendan · Super-admin: `admin@shamrockbailbonds.biz`

Mark items `[x]` only when **live** is proven (not merely code on `main`).

---

## How to use

1. Work top → bottom by **P0** then **P1**.  
2. Prefer **ops verification** over new features until P0 is green.  
3. After any GAS change: re-deploy **existing** deployment ID only (`docs/policies/gas-url-policy.md`).  
4. Run secrets hygiene:

```bash
# From shamrock-leads
python scripts/check_ecosystem_secrets.py --strict
```

---

## P0 — Money & trust paths (do first)

### A. Bail School (education funnel)

| # | Item | Owner | Done |
|---|------|-------|------|
| A1 | Netlify prod env: `GAS_WEBHOOK_URL`, `SESSION_SECRET`, `GAS_API_KEY` set | Ops | [ ] |
| A2 | `GAS_WEBHOOK_URL` matches **stable** portal factory `/exec` (not a one-off deploy) | Ops | [ ] |
| A3 | SwipeSimple **20hr = $199.00**, **120hr = $649.00** in admin (matches `lib/courses.ts`) | Ops | [ ] |
| A4 | Portal `setupSwipeSimpleTrigger()` / Gmail poller firing every ~5 min | Ops | [ ] |
| A5 | E2E: real/test pay → `Student_Auth` Unlocked → magic-link email → dashboard modules | Ops | [ ] |
| A6 | Certificate: `CERTIFICATE_TEMPLATE_ID` + `CERTIFICATE_FOLDER_ID` Script Properties set (no mock cert) | Ops | [ ] |
| A7 | Integrity ack + progress write to LMS sheet verified | Ops | [ ] |
| A8 | Super-admin `admin@` can open both courses | Ops | [ ] |

**Smoke**

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://school.shamrockbailbonds.biz/
curl -sSL "https://script.google.com/macros/s/<STABLE_ID>/exec?action=health"
# expect {"success":true,...}
```

### B. Bond Auto-CRM (leads)

| # | Item | Owner | Done |
|---|------|-------|------|
| B1 | VPS: `ENV=production`, strong `SECRET_KEY`, `DASHBOARD_PIN` set | Ops | [ ] |
| B2 | `MONGODB_URI` / `MONGODB_DB_NAME` healthy; dashboard loads | Ops | [ ] |
| B3 | `GAS_WEB_APP_URL` + `GAS_API_KEY` forward write-bond / paperwork events | Ops | [ ] |
| B4 | `WIX_WEBHOOK_SECRET` set; portal intake → leads intake fails closed without it | Ops | [ ] |
| B5 | SignNow token env (`SIGNNOW_*`) valid; Phase 1 packet can be sent on a test case | Ops | [ ] |
| B6 | SwipeSimple pay-link path works for indemnitor premium | Ops | [ ] |
| B7 | Hot lead Slack webhooks (`SLACK_WEBHOOK_*`) deliver | Ops | [ ] |
| B8 | Core county scrapers healthy (no mass auto-disable) | Ops | [ ] |

### C. Portal / brand / Wix

| # | Item | Owner | Done |
|---|------|-------|------|
| C1 | Wix Secrets: `GAS_WEB_APP_URL` / `GAS_WEBHOOK_URL` = **same** stable factory URL | Human | [ ] |
| C2 | Public Bail School marketing shows **$649** (not $699) after embed redeploy + Wix publish | Ops | [ ] |
| C3 | Secret rotation complete if any keys ever lived in git (`SECRETS_ROTATION_GUIDE.md`) | Ops | [ ] |
| C4 | GAS health `?action=health` success on production deployment | Ops | [ ] |

### D. BlueBubbles / iMessage (preferred consumer rail)

| # | Item | Owner | Done |
|---|------|-------|------|
| D1 | Office Mac BlueBubbles running; tunnel URL in `BLUEBUBBLES_URL_0178` | Ops | [ ] |
| D2 | Dashboard iMessage send succeeds to a test number | Ops | [ ] |
| D3 | Revenue automations stay **`review`** until D1–D2 green for 7 days | Product | [ ] |

### E. Telegram mini-apps

| # | Item | Owner | Done |
|---|------|-------|------|
| E1 | Netlify env: `SEND_PAPERWORK_SECRET`, Twilio, ElevenLabs tool secrets | Ops | [ ] |
| E2 | Mini-app `GAS_ENDPOINT` = stable factory URL | Ops | [ ] |
| E3 | Palmetto SignNow template IDs match leads `SignNowPacketService.TEMPLATE_MAP` | Code+Ops | [x] *synced 2026-07-10* |
| E4 | Shannon “Send Paperwork” tool accepts optional `surety_id` | Ops | [ ] |

---

## P1 — Compliance & intelligence (same week as go-live if claimed)

| # | Item | Repo | Done |
|---|------|------|------|
| P1.1 | Gmail OAuth for discharge monitor (`GOOGLE_*` / refresh token) — not 501 | leads | [ ] |
| P1.2 | Google Calendar OAuth for court sync — not 501 / not dry-run only | leads | [ ] |
| P1.3 | Google Drive OAuth for completed-bond filing | leads | [ ] |
| P1.4 | OSINT worker healthy if using `/api/osint/*` (Maigret path) | leads | [ ] |
| P1.5 | Node-RED: `GAS_WEBHOOK_URL`, `LEADS_PUBLIC_URL`, `GAS_API_KEY`; SYSTEM_SHUTDOWN off | node-red | [ ] |
| P1.6 | Automation schedule visible: `GET /api/automation/schedule` (auth) | leads | [ ] |
| P1.7 | Re-arrest detector + Slack path exercised on a test booking | leads | [ ] |

---

## P2 — Code gaps closed in repo (engineering)

| # | Item | Status |
|---|------|--------|
| P2.1 | Local PDF stitcher (`paperwork_pdf_service`) includes full blank packet order | ✅ 2026-07-10 |
| P2.2 | School certificate **fails closed** when template Script Properties missing (no mock success) | ✅ 2026-07-10 |
| P2.3 | Telegram `appearance-bond-palmetto` ID aligned with leads TEMPLATE_MAP | ✅ 2026-07-10 |
| P2.4 | GAS URL stability policy + update-gas-url gate | ✅ 2026-07-10 |
| P2.5 | Phase 18 phone→autopilot state machine | 🔲 Product next |
| P2.6 | Jackson / rural county recon | 🔲 Expansion (non-blocking for SWFL) |
| P2.7 | CE catalog + in-person cohort tooling | 🔲 After school P0 |

**Primary production paperwork path remains SignNow** (`signnow_packet_service` / GAS). Local PDF stitcher is secondary/offline assist.

---

## P3 — Platform authority (after P0 green)

| Theme | Outcome |
|-------|---------|
| **Statewide quality** | Scraper uptime + COUNTY_REGISTRY accuracy over new vanity features |
| **Education brand** | First paid 20hr/120hr cohort + FLDFS-aligned completion records |
| **Automation trust** | Flip selected jobs `review` → `full_auto` only with metrics |
| **Multi-state (Palmetto)** | Real templates + playbooks for SC/NC/etc. when writing OOS |
| **Thought leadership** | Postiz social + consistent brand voice; no unproven claims |
| **SOC-minded ops** | Audit trails, least privilege secrets, retention policies live |

---

## Harmony smoke (run after deploys)

| # | Test | Pass criteria |
|---|------|----------------|
| H1 | Scrape one core county | Mongo upsert + optional Slack if hot |
| H2 | Portal/Wix test intake | Leads `intake_queue` row |
| H3 | Write-bond / paperwork path | GAS receives payload; SignNow Phase 1 or dry-run logged |
| H4 | School magic link | Login cookie + enrollment API |
| H5 | Node-RED health / Command Center | No SYSTEM_SHUTDOWN; leads URL OK |
| H6 | Telegram intake (optional) | GAS IntakeQueue row + surety_id present |

---

## Explicitly not production blockers

- Continuing education (CE) product line  
- Full phone-only autopilot (Phase 18)  
- Every FL rural county scraper  
- Local PDF stitcher parity with SignNow field extraction  
- Revenue automations in `full_auto` on day one  

---

## Definition of “production” (Shamrock Platform)

**Minimum bar for public trust:**

1. School pay → unlock → learn works for a real student.  
2. Bond staff can run intake → match → SignNow → pay → active bond on Super CRM.  
3. GAS URL is stable and known in **Wix Secrets**.  
4. Secrets are not in git; checklist secrets script is clean.  
5. BB or Twilio can reach a client when staff approves outreach.

When A1–A5, B1–B7, C1–C4, and D1–D2 are `[x]`, call Stage 2 **production-hardened** in [`PLATFORM.md`](./PLATFORM.md).

---

## Cross-repo pointers

| Repo | Status / go-live |
|------|------------------|
| `shamrock-leads` | `STATUS.md`, this file, `PLATFORM.md` |
| `shamrock-bail-school` | `STATUS.md`, `docs/GO_LIVE.md` |
| `shamrock-bail-portal-site` | `STATUS.md`, `SECRETS_ROTATION_GUIDE.md` |
| `shamrock-node-red` | `STATUS.md` |
| `shamrock-telegram-app` | `STATUS.md` |
