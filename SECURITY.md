# SECURITY.md — ShamrockLeads Security Policy

> **Last Updated:** 2026-05-15
> **Compliance Target:** SOC II Type II readiness
> **Contact:** `admin@shamrockbailbonds.biz`

---

## Reporting Vulnerabilities

If you discover a security vulnerability, please email `admin@shamrockbailbonds.biz` immediately. Do not open a public GitHub issue for security vulnerabilities.

---

## Secrets Management

| Environment | Storage | Access |
|-------------|---------|--------|
| **Local dev** | `.env` file (git-ignored) | Developer machine only |
| **Docker** | `env_file: .env` in docker-compose.yml | Container runtime only |
| **GitHub Actions** | Repository secrets | CI/CD pipeline only |
| **MongoDB Atlas** | Connection string in `.env` | Authenticated connections only |
| **SignNow** | Bearer token + Basic Auth in `.env` | Server-side API calls only |

### Rules

- ❌ **Never** commit secrets to version control
- ❌ **Never** log secrets in console output, Slack, or error messages
- ❌ **Never** hardcode API keys, passwords, or tokens in source code
- ✅ All secrets stored as environment variables via `.env`
- ✅ `.env` is listed in `.gitignore`
- ✅ `.env.example` documents all required variables (values redacted)

---

## PII Protection

### What We Collect

| Data Type | Storage | Retention |
|-----------|---------|-----------|
| Defendant names | MongoDB `arrests`, `defendants` | Lead-tier based (7d–180d) |
| Dates of birth | MongoDB `arrests`, `defendants` | Lead-tier based |
| Arrest records | MongoDB `arrests` | Public record, lead-tier based |
| Indemnitor contact info | MongoDB `indemnitors` | Indefinite (compliance) |
| Phone numbers | MongoDB `indemnitors`, `defendants` | Indefinite |
| Addresses | MongoDB `arrests` (if available) | Lead-tier based |

### Protection Rules

- ❌ **Never** log full PII (phone numbers, SSNs, full addresses) to Slack or console
- ❌ **Never** include PII in error messages or stack traces
- ✅ Slack alerts show county, bond amount, score — never full PII together
- ✅ MongoDB Atlas uses encryption at rest
- ✅ Mugshot URLs are public record links (not stored locally)
- ✅ Client-facing URLs use `shamrockbailbonds.biz` — never `leads.shamrockbailbonds.biz`

---

## Authentication & Authorization

| Component | Method |
|-----------|--------|
| **Dashboard** | PIN-based authentication + encrypted session cookie |
| **Session** | `SECRET_KEY` env var for cookie signing (persists across restarts) |
| **MongoDB** | Authenticated connection string (username + password) |
| **SignNow** | ROPC OAuth2 flow (Basic Auth + username/password) |
| **BlueBubbles** | API password via `BLUEBUBBLES_PASSWORD_0178` |
| **Twilio** | Account SID + Auth Token |
| **Gmail/GCal** | OAuth2 refresh token flow |

---

## Network Security

| Layer | Implementation |
|-------|---------------|
| **SSL/TLS** | Nginx reverse proxy with Let's Encrypt certificate |
| **Domain** | `leads.shamrockbailbonds.biz` → Nginx → `localhost:8088` |
| **iMessage tunnel** | ngrok permanent tunnel (static domain) → office iMac :1234 |
| **Docker networking** | Services communicate via `shamrock-net` bridge (internal only) |
| **DNS** | Custom DNS (8.8.8.8, 1.1.1.1) to bypass VPS resolver issues |
| **MongoDB** | Atlas network access list (currently 0.0.0.0/0 — restrict in production) |

---

## Audit Trail

- **`audit_events` collection** — Immutable record of every state change
- Each event records: `entity_type`, `entity_id`, `action`, `actor`, `old_value`, `new_value`, `metadata`, `created_at`
- TTL index: 90 days (`expireAfterSeconds: 7776000`)
- Events **never** updated or deleted (append-only)
- Every bond status transition, POA assignment, match confirmation, and paperwork generation creates an audit event

---

## Scraping Ethics

1. **Rate-limit** all requests (minimum 1s delay between pages)
2. **Respect robots.txt** where applicable
3. **Rotate User-Agent** strings to avoid fingerprinting
4. **Public data only** — all scraped data is from public county jail rosters
5. **Auto-disable** after 5 consecutive failures (prevents hammering broken sites)
6. **No DDoS** — never more than 1 concurrent request per county
7. **Reasonable intervals** — minimum 20 minutes between scrapes (Lee County fastest)

---

## Data Retention

| Collection | Retention Policy |
|------------|-----------------|
| Active bonds | Indefinite (compliance) |
| Defendants | Indefinite |
| Indemnitors | Indefinite |
| Hot leads | 180 days |
| Warm leads | 90 days |
| Cold leads | 30 days |
| Disqualified leads | 7 days |
| Audit events | 90 days (TTL) |
| Notifications | 30 days |
| Court reminders | 30 days post-court |

---

## Incident Response

1. **Identify** — Detect via Slack alerts, dashboard health, or user report
2. **Contain** — Disable affected scraper/service via dashboard or Docker
3. **Investigate** — Check logs (`docker logs`), audit events, MongoDB
4. **Fix** — Deploy patch via GitHub Actions or manual SSH
5. **Review** — Update SECURITY.md, AGENTS.md, or runbooks as needed

---

*Maintained by: Brendan / Shamrock Active Software LLC*
