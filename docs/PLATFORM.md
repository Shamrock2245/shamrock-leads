# Shamrock’s Platform

> **North star:** Become the **authoritative bail brand expert** in Florida — and the model operators elsewhere in the U.S. copy.  
> **Last Updated:** 2026-07-10  
> **Production cutover:** [`ECOSYSTEM_PROD_CHECKLIST.md`](./ECOSYSTEM_PROD_CHECKLIST.md)  
> **Repo harmony:** [`ECOSYSTEM.md`](./ECOSYSTEM.md) · **Brand:** [`../BRAND.md`](../BRAND.md)

---

## What “the Platform” is

Shamrock is **not** a pile of disconnected tools. It is one **operating platform** for two businesses under one brand:

| Pillar | Product | Public surface | Factory |
|--------|---------|----------------|--------|
| **Bond Auto-CRM** | Arrest → lead → intake → match → surety/POA → SignNow → pay → lifecycle | `leads.shamrockbailbonds.biz` | Mongo + portal GAS + Node-RED |
| **Bail School** | Pre-licensing education (20hr / 120hr) → cert path | `school.shamrockbailbonds.biz` | Next.js + portal GAS / Sheets |
| **Brand / Clipboard** | Everywhere intake (web, Telegram, voice) | `shamrockbailbonds.biz` | Wix + GAS |
| **Automation fabric** | Crons, Watchdog, cross-service glue | Node-RED `:1880` | `shamrock-node-red` |

Together these are **Shamrock’s Platform** — the digital workforce + human bondsman system that should make Shamrock the default expert brand for Florida bail.

---

## Brand authority thesis

Industry tools (Captira, Bail Books, generic CRMs) optimize **forms**. We optimize **speed + intelligence + compliance + education**:

1. **Know first** — Statewide jail intelligence before families call around.  
2. **Score everything** — Underwriting signal on every arrest, not gut feel alone.  
3. **Close on every channel** — Portal, iMessage, Telegram, Shannon voice, walk-in.  
4. **Fail closed** — No wrong-person paperwork, no silent GAS URL churn, audit every state change.  
5. **Train the next generation** — Bail School is brand moat + talent pipeline, not a side project.  
6. **Operate as one platform** — Shared secrets hygiene, surety rules, agent identity, super-admin.

When someone in Florida (and later multi-state via Palmetto) asks “who runs bail the right way?”, the answer should be **Shamrock** — because the platform *is* the proof.

---

## Platform product map

```
                    ┌─────────────────────────────────────┐
                    │     SHAMROCK’S PLATFORM (brand)     │
                    │  Fast · Frictionless · Everywhere   │
                    └─────────────────────────────────────┘
           ┌──────────────────┬──────────────────┬──────────────────┐
           ▼                  ▼                  ▼                  ▼
    Bond Auto-CRM      Bail School LMS    Brand Portal        Automation
    shamrock-leads     shamrock-bail-     shamrock-bail-      shamrock-node-red
                       school             portal-site         + telegram-app
           │                  │                  │                  │
           └──────────────────┴────────┬─────────┴──────────────────┘
                                       ▼
                              Portal GAS factory
                         (stable Web App URL — policy)
```

**Rule:** Agents improve the *platform*, not a single repo in isolation. Cross-cutting changes update checklist + `ECOSYSTEM.md`.

---

## Maturity model (honest)

| Stage | Meaning | Where we are |
|-------|---------|----------------|
| **1 · Built** | Code on `main` for core funnels | ✅ Bond chain Phases 1–17 · School LMS · Portal · NR · Telegram |
| **2 · Production-hardened** | Live E2E, secrets, no mock certs, BB reliable | 🔲 Checklist-driven (this week) |
| **3 · Authoritative FL brand** | Statewide coverage quality, education + ops reputation, SOC-minded | 🔲 After Stage 2 |
| **4 · U.S. reference model** | Palmetto multi-state depth, playbooks others adopt | 🔲 After Stage 3 |

Do **not** claim Stage 3/4 in marketing until Stage 2 checkboxes are green.

---

## Non-negotiables (platform law)

1. **GAS Web App URL stays stable** — `docs/policies/gas-url-policy.md`  
2. **The chain is law** — Arrest → Defendant → Indemnitor → Match → BondCase → Packet → Signature → Payment  
3. **Surety-aware** — OSI preferred; Palmetto when required; never assume  
4. **Human gates on risk** — Match ambiguity, destructive bond statuses, full_auto outreach  
5. **PII is sacred** — No phones/SSNs in Slack or logs  
6. **Shamrock exclusive identity** — `admin@shamrockbailbonds.biz`, Shamrock2245, brand domains only  

---

## How agents build toward authority

| Priority | Work type | Examples |
|----------|-----------|----------|
| **P0** | Production checklist green | Pay→unlock E2E, env, BB tunnel, cert templates |
| **P1** | Reliability & coverage quality | Scraper health, OSINT worker, discharge/GCal OAuth |
| **P2** | Brand surface polish | Portal pricing, school UX, social (Postiz) truth |
| **P3** | Platform depth | Phase 18 phone→autopilot, CE catalog, multi-state packs |

Every PR should answer: *Does this make Shamrock more trustworthy, faster, or more complete as Florida’s expert brand?*

---

## Related

- Production checklist: `docs/ECOSYSTEM_PROD_CHECKLIST.md`  
- Four-repo roles: `docs/ECOSYSTEM.md`  
- Super CRM: `docs/SUPER_CRM.md`  
- Brand culture: `BRAND.md`  
- Per-repo truth: each repo’s `STATUS.md`
