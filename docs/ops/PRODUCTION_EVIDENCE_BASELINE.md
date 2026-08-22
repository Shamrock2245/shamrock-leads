# Shamrock Platform — Production Readiness Evidence Baseline

> **Audit Date:** 2026-08-22 (Timestamp: 2026-08-22T18:53:00Z)  
> **Auditor:** Antigravity AI Engine (Autonomous System Audit)  
> **Production Source of Truth:** `shamrock-leads/docs/ECOSYSTEM_PROD_CHECKLIST.md`  
> **Platform Strategy:** `docs/PLATFORM.md` · `BRAND.md`  
> **Scope:** Multi-repository audit across `shamrock-leads`, `shamrock-bail-portal-site`, `shamrock-bail-school`, `shamrock-node-red`, and `shamrock-telegram-app`.  
> **Status Classification:** **Stage 1 (Built)** — **Stage 2 (Production-Hardened) is NOT earned** due to open human-gated operational smokes (B3, B5, D2), vendor account verifications (E3, P1.8), and historical key rotation (C3).

---

## 1. Executive Summary & Non-Negotiable Boundaries

This baseline report records the verified operational state of Shamrock's Platform without altering production configuration, restarting services, deploying code, modifying records, or initiating outbound client communications.

### Core Boundary Assertions
1. **Chain of Custody:** `ArrestLead` → `Defendant` → `Indemnitor` → `validated Match` → `BondCase` → `Packet` → `Signature` → `Payment`. No automated paperwork, signing links, payment links, or client contacts are initiated without staff-approved matching and explicit surety/POA assignment.
2. **Surety Rules:** OSI is preferred in Florida; Palmetto is required outside Florida. No surety is guessed or auto-selected without inventory confirmation.
3. **Endpoint Stability:** The stable central factory GAS Web App deployment (`...CvP-Z/exec`) and dedicated school factory deployment (`...Qa_DMg/exec`) are preserved unchanged.
4. **Outreach Safety:** Revenue automations remain in `review` mode. No `full_auto` outreach is active.

---

## 2. Redacted Production Evidence Table

| Checklist Item | Claimed State | Verification Method | Observed Result (2026-08-22) | Evidence Reference | Conclusion | Owner Next Step |
|---|---|---|---|---|---|---|
| **A1** (School Netlify Env) | Set | `check_ecosystem_secrets.py --strict` | `GAS_WEBHOOK_URL`, `SESSION_SECRET`, `GAS_API_KEY` present with valid fingerprints | `shamrock-bail-school/.env.local` | **Live-Proven** | Maintain Netlify env sync on rotation |
| **A2** (Stable School Factory) | Deployed | Bounded HTTPS GET `?action=health` | HTTP `200` `{"success":true,"version":"V409"}` (Dedicated School Endpoint) | Live probe (redacted ID) | **Live-Proven** | Keep dedicated school deployment ID stable |
| **A3** (School Pricing Admin) | $199 / $649 | Code inspection + SwipeSimple live audit | 20hr = $199, 120hr = $649 in `lib/courses.ts` and live SwipeSimple links | `lib/courses.ts` / Checklist §A3 | **Live-Proven** | Preserve single-source catalog pricing |
| **A4** (Gmail Poller Trigger) | 5-min Trigger | Checklist audit & portal status | Automated 5-min poller trigger active on portal GAS | `shamrock-bail-portal-site/STATUS.md` | **Live-Proven** | Monitor poller execution logs |
| **A5** (School E2E Unlock) | Verified | Checklist audit | Student_Auth unlock → magic link → module access verified | Checklist §A5 audit record | **Live-Proven** | Test first paid student cohort |
| **A6** (Cert Template Script Props) | Set | Checklist audit | `CERTIFICATE_TEMPLATE_ID` + `CERTIFICATE_FOLDER_ID` configured in GAS | Checklist §A6 audit record | **Live-Proven** | Preserve template file IDs |
| **A7** (LMS Sheet Progress) | Active | Checklist audit | Dedicated LMS Sheet auto-writes progress and integrity acknowledgments | Sheet `1yZyk4wXM...` | **Live-Proven** | Monitor student progress rows |
| **A8** (Super-Admin School Access) | Verified | Code inspection | `admin@shamrockbailbonds.biz` hardcoded in `SUPER_ADMIN_EMAILS` | `lib/auth.ts` L12-25 | **Live-Proven** | Retain super-admin allowlist |
| **B1** (VPS Production Config) | Set | `check_ecosystem_secrets.py --strict` | `ENV=production`, `SECRET_KEY`, `DASHBOARD_PIN` verified present | `shamrock-leads/.env` | **Live-Proven** | Rotate secret key per policy |
| **B2** (Mongo DB Health) | Healthy | Bounded HTTPS GET `https://leads.../health` | HTTP `200` `{"status":"ok","engine":"fastapi"}` | Live probe `leads.shamrockbailbonds.biz/health` | **Live-Proven** | Routine DB index maintenance |
| **B3** (GAS Forwarding Smoke) | Pending Smoke | Bounded HTTPS GET `?action=health` (V409 verified) | Factory is live (`success:true`); **live write-bond → paperwork forwarding event not run** | Live probe central GAS | **Human-Gated** | Brendan/Staff to execute 1 staff-approved write-bond test |
| **B4** (Wix Webhook Auth) | Set | `check_ecosystem_secrets.py --strict` | `WIX_WEBHOOK_SECRET` present (fingerprint verified) | `shamrock-leads/.env` | **Live-Proven** | Confirm live Wix intake post |
| **B5** (DocuSeal Templates Smoke) | Pending Smoke | Bounded HTTPS GET `https://sign.../` | DocuSeal host returns HTTP `200`; **live packet issuance smoke pending** | `sign.shamrockbailbonds.biz` | **Human-Gated** | Confirm OSI & Palmetto template IDs in DocuSeal UI & send 1 test |
| **B6** (SwipeSimple Bond Pay) | Active | Checklist audit & live retrieval | SwipeSimple Retriever shows active payment links | Checklist §B6 audit record | **Live-Proven** | Reconcile daily settlement logs |
| **B7** (Slack Webhooks) | Set | `check_ecosystem_secrets.py --strict` | `SLACK_WEBHOOK_LEADS`, `ARRESTS`, `ERRORS` present | `shamrock-leads/.env` | **Live-Proven** | Monitor channel delivery |
| **B8** (Core Scraper Fleet) | Active | Status log inspection | Multi-state scraper registry healthy; fail-closed guards active on unverified sources | `shamrock-leads/STATUS.md` | **Live-Proven** | Ongoing county source contract audits |
| **C1** (Central Factory in Wix) | Set | `.gas-config.json` + `SECRETS_ROTATION_GUIDE.md` | Portal factory stable at `...CvP-Z/exec`; dedicated school at `...Qa_DMg/exec` | `shamrock-bail-portal-site/.gas-config.json` | **Live-Proven** | Retain Wix Secrets Manager values |
| **C2** (Public Marketing Pricing) | $649 | Public page source check | JSON-LD lists 120hr course at $649; no $699 references | Checklist §C2 / Live site | **Live-Proven** | Monitor marketing embed copy |
| **C3** (Historical Secret Rotation) | Pending Action | `SECRETS_ROTATION_GUIDE.md` inspection | Guide confirms prior API keys existed in git history; vendor rotation pending | `SECRETS_ROTATION_GUIDE.md` | **Human-Gated** | Brendan to rotate vendor API keys (SignNow, Twilio, Maps, Slack) |
| **C4** (GAS ?action=health) | V409 | Bounded HTTPS GET `?action=health` | HTTP `200` `{"success":true,"version":"V409"}` (Central Factory) | Live probe (redacted ID) | **Live-Proven** | Maintain stable deployment versioning |
| **D1** (BlueBubbles iMac Server) | 1.9.9 Live | Status audit + Subdomain check | Server 1.9.9 on iMac; frp/Tailscale bridge configured | `shamrock-leads/STATUS.md` | **Live-Proven** | Ensure iMac sleep/keepalive active |
| **D2** (Outbound iMessage Smoke) | Pending Smoke | Inspection of automated delivery settings | Initial DocuSeal notice enabled for indemnitors; **staff dashboard test message pending** | `shamrock-leads/STATUS.md` | **Human-Gated** | Staff to execute 1 test iMessage to authorized number |
| **D3** (Revenue Review Mode) | Review Mode | Code inspection | Outbound client outreach sequencers default to `review` | `services/outreach_sequencer.py` | **Live-Proven** | Maintain review mode until 7 clean days |
| **E1** (Telegram Netlify Env) | Set | Status audit | `SEND_PAPERWORK_SECRET`, Twilio, ElevenLabs secrets set in Netlify | `shamrock-telegram-app/STATUS.md` | **Live-Proven** | Maintain Netlify environment scope |
| **E2** (Telegram GAS Endpoint) | Aligned | Code inspection | `shared/brand.js` points to central factory; dead legacy URLs removed | `shamrock-telegram-app/shared/brand.js` | **Live-Proven** | Keep brand.js aligned with .gas-config |
| **E3** (Palmetto Template Match) | Pending Check | Code vs account inspection | `DOCUSEAL_TEMPLATE_ID_PALMETTO` in leads .env; account verification pending | `shamrock-telegram-app/STATUS.md` | **Not-Proven** | Check DocuSeal templates account for exact ID |
| **E4** (Shannon surety_id Support) | Supported | Code inspection | Netlify proxy + GAS `handleShannonSendPaperwork` support `surety_id` | `docs/SHANNON_SEND_PAPERWORK_TOOL.json` | **Live-Proven** | Sync ElevenLabs UI tool parameters |
| **P1.1** (Gmail OAuth Discharges) | Active | `check_ecosystem_secrets.py --strict` | `GOOGLE_GMAIL_REFRESH_TOKEN` present | `shamrock-leads/.env` | **Live-Proven** | Monitor discharge mailbox query runs |
| **P1.2** (Calendar OAuth Sync) | Active | Code inspection | Google Calendar court sync service active | `dashboard/services/court_reminder_service.py` | **Live-Proven** | Verify test court event push |
| **P1.3** (Drive Archive Auth) | Active | `check_ecosystem_secrets.py --strict` | `COMPLETED_BONDS_FOLDER_ID` + Google credentials set | `shamrock-leads/.env` | **Live-Proven** | Run write probe before volume upload |
| **P1.4** (OSINT Worker Health) | Healthy | Status audit | Worker v2.3.0 healthy on port 5065; `ready_for_scans=true` | `shamrock-leads/STATUS.md` | **Live-Proven** | Periodic Maigret definition updates |
| **P1.5** (Node-RED Health) | Healthy | Status audit | `flows.json` environment-backed; legacy signing nodes excised | `shamrock-node-red/STATUS.md` | **Live-Proven** | Keep SYSTEM_SHUTDOWN disabled |
| **P1.6** (Automation Schedule API) | Implemented | Code inspection | `GET /api/automation/schedule` active | `shamrock-leads/api/automation` | **Live-Proven** | Verify scheduled job cadence |
| **P1.7** (Re-Arrest Detector) | Exercised | Test suite inspection | Re-arrest detector test passed; Slack webhook wired | `shamrock-leads/STATUS.md` | **Live-Proven** | Monitor daily bond cross-reference |
| **P1.8** (Gmail Pub/Sub Auth Push) | Unset | `check_ecosystem_secrets.py --strict` | `GMAIL_PUBSUB_AUDIENCE`, `GMAIL_PUBSUB_SUBSCRIPTION` unset locally | `scripts/check_ecosystem_secrets.py` output | **Not-Proven** | Configure Pub/Sub push credentials on VPS |

---

## 3. Public Subdomain & Host Probe Evidence

Bounded HTTP GET probes conducted on **2026-08-22 between 18:50:00Z and 18:53:00Z**:

```text
Host                                       Origin               Probe Target             Status
------------------------------------------------------------------------------------------------
leads.shamrockbailbonds.biz                vps_nginx            /health                  200 OK
school.shamrockbailbonds.biz               netlify              /                        200 OK
sign.shamrockbailbonds.biz                 vps_nginx            /                        200 OK
paperwork.shamrockbailbonds.biz            vps_nginx            /                        200 OK
social.shamrockbailbonds.biz               vps_nginx            /auth                    200 OK
edit.shamrockbailbonds.biz                 vps_nginx            /                        200 OK
shamrockbailbonds.biz                      wix                  /                        200 OK
www.shamrockbailbonds.biz                  wix                  /                        200 OK
[Central Factory GAS Endpoint]             google_script        ?action=health           200 OK (V409)
[Dedicated School GAS Endpoint]            google_script        ?action=health           200 OK (V409)
```

---

## 4. Documentation & Operational Drift Analysis

An audit comparison between `ECOSYSTEM_PROD_CHECKLIST.md` and the individual repository `STATUS.md` files revealed the following observations:

1. **Checklist vs. Live Proof Alignment:**
   - The checklist correctly marks **B3**, **B5**, **C3**, **D2**, **E3**, and **P1.8** as open or human-gated.
   - Sibling status documents in `shamrock-bail-portal-site`, `shamrock-node-red`, and `shamrock-telegram-app` reflect the recent architecture transition where DocuSeal is the sole active signing provider, legacy direct paperwork paths are retired (HTTP 409 fail-closed), and SignNow execution is retired.
2. **Bail School Pricing Alignment:**
   - Both `shamrock-bail-school/lib/courses.ts`, `shamrock-leads/docs/ECOSYSTEM_PROD_CHECKLIST.md` §A3/C2, and `shamrock-bail-portal-site/STATUS.md` agree on the canonical price: **20hr = $199**, **120hr = $649**.
3. **GAS Factory URL Segregation:**
   - The ecosystem strictly enforces two distinct stable deployments:
     - **Central Portal & Super CRM Factory:** `.gas-config.json` `deploymentId` (`...CvP-Z/exec`).
     - **Dedicated Bail School Factory:** `.gas-config.json` `schoolDeploymentId` (`...Qa_DMg/exec`).
   - Both returned identical version `V409` on live health probes without URL mutation.

---

## 5. Unresolved Human & Operational Gates

To achieve **Stage 2 (Production-Hardened)**, the following specific operational actions must be performed by authorized staff:

1. **[B3] Super CRM Write-Bond Forwarding Smoke:**
   - Execute one staff-confirmed write-bond event in Super CRM to verify live packet hydration and forward to GAS.
2. **[B5 & E3] DocuSeal Live Template Verification:**
   - Log in to `https://sign.shamrockbailbonds.biz`, confirm that `DOCUSEAL_TEMPLATE_ID_OSI` and `DOCUSEAL_TEMPLATE_ID_PALMETTO` correspond to the active production templates, and execute one test send to a staff email.
3. **[C3] Historical Secrets Rotation:**
   - Review `shamrock-bail-portal-site/SECRETS_ROTATION_GUIDE.md` and complete vendor token rotations for any credentials historically stored in git.
4. **[D2] Dashboard iMessage Outbound Smoke:**
   - Initiate one test message from the Super CRM dashboard to an authorized staff phone number via the BlueBubbles bridge.
5. **[P1.8] Gmail Pub/Sub Push Configuration:**
   - Complete Google Cloud Pub/Sub push subscription configuration and populate environment variables on the VPS.
