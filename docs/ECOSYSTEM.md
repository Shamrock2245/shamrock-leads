# Shamrock’s Platform — Ecosystem Harmony

> How **leads**, **portal**, **bail-school**, **node-red**, and **telegram** form **one platform**.  
> **Last Updated:** 2026-07-10 · Per-repo truth: each repo’s `STATUS.md`  
> **Platform thesis:** [`PLATFORM.md`](./PLATFORM.md)  
> **Production checklist:** [`ECOSYSTEM_PROD_CHECKLIST.md`](./ECOSYSTEM_PROD_CHECKLIST.md)  
> **GAS URL policy:** [`docs/policies/gas-url-policy.md`](./policies/gas-url-policy.md)

---

## Two businesses

| Business | Definition | Primary repos |
|----------|------------|----------------|
| **Bond Auto-CRM** | Phone/lead → outreach → intake → bond lifecycle (minimal human touch except risk gates) | `shamrock-leads` + portal GAS + **node-red** orchestration |
| **Bail School** | Pre-licensing: 20hr **$199**, 120hr **$649**; future CE; online + in-person | `shamrock-bail-school` + portal payment unlock |

---

## Roles

| Repo | Role | Runtime | Primary URL / port |
|------|------|---------|---------------------|
| **shamrock-leads** | Auto-CRM + scrapers + Super CRM dashboard | Hetzner Docker | `leads.shamrockbailbonds.biz` |
| **shamrock-bail-portal-site** | Public clipboard + GAS factory + school Gmail unlock | Wix + GAS | `shamrockbailbonds.biz` |
| **shamrock-bail-school** | Student LMS education funnel | Netlify Next.js | `school.shamrockbailbonds.biz` |
| **shamrock-node-red** | **Open-source n8n/Zapier** — visual automation, crons, webhooks, cross-service glue | Hetzner Docker (often compose profile `ops`) | `:1880` editor/dashboard |

```
County jails ──scrape──► shamrock-leads (Mongo + Slack)
                              │
              ┌───────────────┼───────────────┐
              │               │               │
              ▼               ▼               ▼
     shamrock-node-red   portal GAS      Slack / Twilio
     (schedules,         (SignNow,       Telegram, etc.
      webhooks,           packets)
      Watchdog)
              │
              └──────► staff + clients
                              │
              (separate P&L) shamrock-bail-school
                    progress + certs → school GAS / Sheets
```

**Node-RED** is the central nervous system for time-based jobs and multi-system routing—not a second CRM UI and not the school LMS.  
Bail School is **adjacent**, not on the critical arrest→bond path. FLDFS **bond** compliance lives in leads; FLDFS **education** compliance lives in bail-school.

---

## Super admin identity

**`admin@shamrockbailbonds.biz` is always full admin** when logged into any Shamrock surface.

| Repo | Mechanism |
|------|-----------|
| portal | `src/backend/super-admin.js` + `portal-auth.jsw` elevation |
| school | `lib/auth.ts` `SUPER_ADMIN_EMAILS` (hardcoded ∪ `ADMIN_EMAILS`) |
| leads | `dashboard/auth/super_admin.py` + session claims in PIN middleware |
| node-red | Ops editor credentials; data admin via leads/portal as admin@ |

Optional allowlist for additional admins: `ADMIN_EMAILS=admin@shamrockbailbonds.biz,...`

## Automation (work smarter)

Node-RED orchestrates three pillars (see `shamrock-node-red/docs/SUPER_ADMIN.md`):

1. **Lead qualification** — score ≥ 70 hot, warm follow-up, high-value bonds  
2. **Bond / relationship lifecycle** — stuck stages, missing court dates, closer drips  
3. **Risk mitigation** — flight risk loop, court proximity, check-ins, forfeiture alerts  

Leads machine API (auth: `GAS_API_KEY`):

- `POST /api/automation/lead-qualification`
- `POST /api/automation/bond-lifecycle`
- `POST /api/automation/risk-mitigation`

## Shared contracts (must stay aligned)

### 1. API keys / secrets (same value across systems)

| Secret | leads | portal (GAS/Wix) | bail-school |
|--------|-------|------------------|-------------|
| `GAS_API_KEY` | Webhooks, scraper events, GAS calls | Script Property + Wix Secrets | `GAS_API_KEY` Netlify + GAS Script Property |
| `GAS_WEB_APP_URL` / `GAS_WEBHOOK_URL` | Forward bond/paperwork events to GAS | Deployed web app URL | Auth + progress + certs |
| `WIX_WEBHOOK_SECRET` | `/api/webhooks/wix-intake` | Wix → leads intake | — |
| `MONGODB_URI` | Primary DB | Optional MongoLogger / proxy | — |
| SignNow / Twilio / Slack / ElevenLabs | Ops + packets | Same vendor accounts | School payments (SwipeSimple) |

**Rule:** After any secret rotation, update **VPS `.env`**, **Wix Secrets**, **GAS Script Properties**, and **Netlify** in the same change window.

### 1b. GAS Web App URL stability (**non-negotiable**)

> Full policy: [`docs/policies/gas-url-policy.md`](./policies/gas-url-policy.md)

| Do | Do not |
|----|--------|
| `clasp push` + **`clasp deploy -i <EXISTING_ID>`** (URL unchanged) | Create a **new** Web App deployment that mints a new `/macros/s/…/exec` URL |
| Edit GAS source, re-deploy the **same** deployment | Silently rewrite `GAS_WEBHOOK_URL` / `GAS_WEB_APP_URL` across repos |
| If a URL change is unavoidable: **stop and tell the human** | Assume Wix Secrets Manager was updated by deploy tooling |

**Why:** Wix Secrets Manager (`GAS_WEB_APP_URL` / `GAS_WEBHOOK_URL`) is **outside** the agent-managed ecosystem. A new GAS URL breaks portal → GAS until the human updates Wix (and Netlify / VPS / Node-RED).

### 2. Data handoffs

| From | To | Mechanism |
|------|-----|-----------|
| Leads scrapers | Slack / Mongo `arrests` | Internal writers |
| Leads dashboard | Wix CMS (`IntakeQueue`, Cases) | `wix/sync.py` + `WIX_BLOG_API_KEY` |
| Wix portal intake | Leads | `POST /api/webhooks/wix-intake` + `WIX_WEBHOOK_SECRET` |
| Leads | GAS | `GAS_WEB_APP_URL` + `GAS_API_KEY` (paperwork / status) |
| School Next.js | School GAS | `GAS_WEBHOOK_URL` + `GAS_API_KEY` |
| School | Portal monorepo | Historical: `BailSchool_Progress.js` / payments in portal GAS — prefer single school GAS deploy |

### 3. Client URLs

| Purpose | Env | Default |
|---------|-----|---------|
| Public brand / indemnitor portal | `PORTAL_BASE_URL` / `CLIENT_BRAND_URL` | `https://shamrockbailbonds.biz` |
| Ops dashboard (staff only) | `DASHBOARD_PUBLIC_URL` | `https://leads.shamrockbailbonds.biz` |
| Bail school | Netlify | `https://school.shamrockbailbonds.biz` |

Never put PII-bearing staff URLs in client-facing SMS; use brand domain + magic links.

---

## Production readiness checklist (leads)

- [ ] `DASHBOARD_PIN` and strong `SECRET_KEY` set on VPS (no empty PIN open access)
- [ ] `GAS_API_KEY` and `WIX_WEBHOOK_SECRET` set (webhooks fail closed if missing)
- [ ] MongoDB Atlas user password rotated if ever committed; network access restricted beyond `0.0.0.0/0` when possible
- [ ] BlueBubbles passwords only in env / LaunchAgents — not in shell scripts
- [ ] Deploy workflow secrets: `HETZNER_SSH_KEY`, Firebase base64 secrets
- [ ] Nginx TLS for `leads.shamrockbailbonds.biz`

---

## Harmony tests (smoke)

1. **Scrape → alert:** run one county scraper → Mongo arrest + Slack (if hot).
2. **Portal → leads:** submit test intake on Wix → webhook creates intake on leads.
3. **Leads → GAS:** trigger paperwork path with `GAS_WEB_APP_URL` configured.
4. **School:** magic link login → progress POST → GAS sheet row (separate stack).

---

## Secrets checklist (all three repos)

From any of the three repos (or leads directly):

```bash
# From shamrock-leads
python scripts/check_ecosystem_secrets.py
python scripts/check_ecosystem_secrets.py --strict

# From portal or school (wrapper finds sibling leads repo)
python scripts/check_ecosystem_secrets.py --strict
```

Never prints secret values — only presence + fingerprints for shared-key alignment.

## Docs index

- **Platform north star:** `docs/PLATFORM.md`
- **Production checklist (all repos):** `docs/ECOSYSTEM_PROD_CHECKLIST.md`
- **GAS URL stability (all repos):** `docs/policies/gas-url-policy.md`
- Leads: `docs/ARCHITECTURE.md`, `docs/DEPLOYMENT.md`, `docs/SUPER_CRM.md`, `SECURITY.md`
- Portal: `SYSTEM.md`, `docs/DEPLOYMENT_CHECKLIST.md`, `SECRETS_ROTATION_GUIDE.md`, `backend-gas/GAS_DEVELOPMENT_RULES.md`
- School: `docs/ARCHITECTURE.md`, `docs/DEPLOYMENT.md`, `docs/SECURITY.md`
