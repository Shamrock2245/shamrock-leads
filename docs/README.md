# ShamrockLeads Documentation Index

> **Start here** if you're new to the codebase. This page maps every document to its purpose.

---

## Required Reading Order (For Agents)

1. **[BRAND.md](../BRAND.md)** — Identity, vision, design standards, non-negotiable rules
2. **[PLATFORM.md](PLATFORM.md)** — Shamrock’s Platform thesis (FL / U.S. brand authority)
3. **[ECOSYSTEM_PROD_CHECKLIST.md](ECOSYSTEM_PROD_CHECKLIST.md)** — Single production checklist across the ecosystem
4. **[AGENTS.md](../AGENTS.md)** — Digital workforce, scoring, safety rules, escalation
5. **[DATA_MODEL.md](../DATA_MODEL.md)** — MongoDB collections, entity relationships, dedup keys
6. **[ROADMAP.md](../ROADMAP.md)** / **[STATUS.md](../STATUS.md)** — Implemented vs live truth

---

## Architecture & Technical Reference

| Document | Purpose |
|----------|---------|
| [PLATFORM.md](PLATFORM.md) | Platform north star — one brand, two businesses, authority thesis |
| [ECOSYSTEM_PROD_CHECKLIST.md](ECOSYSTEM_PROD_CHECKLIST.md) | Production cutover checklist (P0–P3) |
| [ECOSYSTEM.md](ECOSYSTEM.md) | Repo roles, secrets, handoffs |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture, Docker services, data flows, integrations |
| [API_REFERENCE.md](API_REFERENCE.md) | REST API endpoints (200+ across 61 modules) |
| [SCHEMAS.md](SCHEMAS.md) | MongoDB collection field-level schemas |
| [COUNTY_REGISTRY.md](COUNTY_REGISTRY.md) | All 67 FL counties: JMS vendor, scraper status, URLs |
| [policies/gas-url-policy.md](policies/gas-url-policy.md) | Stable GAS Web App URL law |

---

## Agent Documentation (15 agents → 15 docs)

| Agent | Doc | Role |
|-------|-----|------|
| The Clerk | [scraper-agent.md](agents/scraper-agent.md) | Jail roster scraping, anti-bot evasion |
| The Analyst | [analyst-agent.md](agents/analyst-agent.md) | Lead scoring (0–100), risk classification |
| The Watchdog | [watchdog-agent.md](agents/watchdog-agent.md) | Scraper health monitoring, self-healing |
| The Matcher | [matching-agent.md](agents/matching-agent.md) | Indemnitor ↔ defendant matching |
| The Paperwork Agent | [paperwork-agent.md](agents/paperwork-agent.md) | SignNow packet generation |
| The Signature Agent | [signature-agent.md](agents/signature-agent.md) | E-signature orchestration |
| The Payment Agent | [payment-agent.md](agents/payment-agent.md) | Premium payment tracking |
| The Auditor | [audit-agent.md](agents/audit-agent.md) | Immutable audit event logging |
| The Finder | [contact-finder-agent.md](agents/contact-finder-agent.md) | OSINT contact discovery |
| The Closer | [outreach-agent.md](agents/outreach-agent.md) | iMessage drip campaigns |
| The Court Clerk | [court-clerk-agent.md](agents/court-clerk-agent.md) | Court reminders + calendar sync |
| The Discharge Monitor | [discharge-monitor-agent.md](agents/discharge-monitor-agent.md) | Gmail discharge scanning |
| Shannon | [shannon-agent.md](agents/shannon-agent.md) | AI auto-reply iMessage agent |
| The Sentinel | [rearrest-detector-agent.md](agents/rearrest-detector-agent.md) | Re-arrest detection |
| The Janitor | [data-retention-agent.md](agents/data-retention-agent.md) | Data retention / M0 management |

---

## Policies (Business Rules)

| Document | When To Read |
|----------|-------------|
| [surety-policy.md](policies/surety-policy.md) | Bond writing, POA assignment, surety selection |
| [matching-policy.md](policies/matching-policy.md) | Indemnitor → defendant matching logic |
| [signature-policy.md](policies/signature-policy.md) | SignNow packet binding, signing workflow |

---

## Runbooks (How-To)

| Document | When To Read |
|----------|-------------|
| [intake-to-signature.md](runbooks/intake-to-signature.md) | End-to-end bond workflow |
| [bluebubbles-tunnel.md](runbooks/bluebubbles-tunnel.md) | iMessage bridge setup + troubleshooting |

---

## Specs (Technical Schemas)

| Document | When To Read |
|----------|-------------|
| [bond-case-schema.md](specs/bond-case-schema.md) | BondCase entity detailed schema |
| [surety-config-schema.md](specs/surety-config-schema.md) | Surety configuration object spec |

---

## Operations & Deployment

| Document | Purpose |
|----------|---------|
| [DEPLOYMENT.md](DEPLOYMENT.md) | Docker, SSH, Nginx, health checks, troubleshooting |
| [NGINX_PROXY_SETUP.md](NGINX_PROXY_SETUP.md) | Nginx reverse proxy configuration |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Development workflow, code conventions, PR process |
| [../SECURITY.md](../SECURITY.md) | Secrets, PII, auth, audit, incident response |
| [../CHANGELOG.md](../CHANGELOG.md) | Version history |
