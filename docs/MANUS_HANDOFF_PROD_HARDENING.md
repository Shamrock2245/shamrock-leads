# Manus Handoff — ShamrockLeads Production Hardening (continue Grok session)

> **Written:** 2026-07-24  
> **Repo:** `Shamrock2245/shamrock-leads` · branch `main`  
> **Product:** `https://leads.shamrockbailbonds.biz`  
> **Authoritative status:** root `STATUS.md` · prod checklist `docs/ECOSYSTEM_PROD_CHECKLIST.md` · agent rules `AGENTS.md`  
> **Read first:** `BRAND.md` → `AGENTS.md` → `STATUS.md` → this file  

---

## Your mission

Continue **production hardening and bugfix** for ShamrockLeads (bond Auto-CRM + multi-state scrapers). Prior work (Grok session 2026-07-23→24) fixed critical P0 ops and several scrapers. **Do not re-do completed work.** Pick up remaining errors, deepen quality, and keep the fleet healthy.

**Tone:** Fail closed. No guessing identity/legal facts. Minimize PII in logs. Prefer thin platform wrappers over one-off scrapers. Deploy carefully; VPS docker recreate can race — verify health after every deploy.

---

## Environment & access

| Item | Value / note |
|------|----------------|
| VPS | `root@178.156.179.237` (Hetzner) |
| SSH key that works | `~/.ssh/id_ed25519` (ed25519 `shamrockbailoffice@…`) |
| App path on VPS | `/opt/shamrock-leads` |
| Containers | `shamrock-dashboard` (UI :8088→5050), `shamrock-leads` (scheduler/scrapers), `shamrock-frps` (BB tunnel :12434), etc. |
| Public URL | `https://leads.shamrockbailbonds.biz` |
| Dashboard PIN | in VPS + local `.env` as `DASHBOARD_PIN` (do not print) |
| Session cookie | `sl_session` — signed with `SECRET_KEY` |
| Local secrets check | `python3 scripts/check_ecosystem_secrets.py --strict` |

**Deploy pattern (safe):**
```bash
ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes root@178.156.179.237
cd /opt/shamrock-leads
git fetch origin main && git reset --hard origin/main
docker compose build shamrock-leads dashboard   # as needed
# If recreate races ("No such container"):
docker rm -f shamrock-leads shamrock-dashboard 2>/dev/null || true
docker compose up -d shamrock-leads dashboard
docker ps --filter name=shamrock-leads --filter name=shamrock-dashboard
docker logs shamrock-leads --tail 40
curl -sS http://127.0.0.1:8088/health
```

**One-shot scraper:**
```bash
docker exec shamrock-leads python main.py <county>     # FL bare: lee, bradford
docker exec shamrock-leads python main.py nc_mecklenburg
docker exec shamrock-leads python main.py sc_jasper
```

**Auth smoke (from laptop, no secret print):**
```python
# POST /login JSON {pin, email} → cookie sl_session
# GET /api/crm/health, /api/bond-intelligence?days=7, /api/imessage/status, /api/ops/scraper-registry
```

---

## What was already completed (DO NOT redo)

### Ops / CRM
- [x] Local ecosystem secrets critical keys present (`check_ecosystem_secrets.py --strict` green)
- [x] VPS: set `SECRET_KEY` (64-char) + `ENV=production` (was missing → CRM `degraded`)
- [x] CRM health now **`ok`** with `secret_key: true`, integrations (GAS, Wix, SignNow, Twilio, Slack, BB, PIN)
- [x] GAS health `?action=health` → success (version ~V409)
- [x] Live scale: ~128k+ arrests; scrapers ~230 ok range
- [x] Lee one-shot works (may see 429 → proxy rotation recovers)
- [x] BlueBubbles: frp `http://178.156.179.237:12434` live; BB 1.9.9; `/api/imessage/status` **connected** + private_api
- [x] Bug: `init_bluebubbles()` re-bound `BB_SERVERS = {}` so importers kept empty dict → fixed (mutate in place). Tests: `tests/test_bb_servers_init.py`
- [x] Bond Intelligence 401 after SECRET_KEY rotate: session invalid; UI now redirects to `/login?reason=session_expired`. **User must re-login once.**
- [x] Defendants batch normalize started: ~594 defendants (was 0). Coverage still tiny vs 128k arrests.
- [x] SignNow bearer token validates against `/user` (env present)

### Commits on `main` (approx order — verify with `git log`)
| Commit | Summary |
|--------|---------|
| `e76b126` | BB_SERVERS shared after init + STATUS/checklist prod notes |
| `1071548` | Monroe `verify=False` for incomplete TLS chain |
| `f170055` | Bradford/Dixie/Taylor URL fixes + defendants seed notes |
| `5c6b34b` | Dashboard 401 → re-login (sl-core + bond-intel + login UX) |
| `3466bc6` | SmartWEB card parse attempt + multi-state USPS filter |
| `ce7006e` | **CRITICAL:** restore `scrape_smartweb` in `smartweb_parser.py` (crash-loop fix) |
| `614dfd3` | Taylor → `scrape_smartweb` on `:8989` |

### Scraper URL/status (as of handoff)

| County | Fix | Live result (last verified) |
|--------|-----|-----------------------------|
| **Bradford** | Host `http://smartweb.bradfordsheriff.org/...` (not smartcop.*); direct-first; `smartweb_card_parser` | **10 records**, 3 hot |
| **Dixie** | HTTPS `https://smartcop.dixiecountysheriff.com/...`; direct-first; card parser | **20 records**, 8 hot |
| **Taylor** | `http://smartcop.taylorsheriff.org:8989/SmartWEBClient/Jail.aspx` (entry `http://jail.taylorsheriff.org/`); `scrape_smartweb` | **10 records** |
| **Lee** | Working | 42 records (example) |

### Architecture notes Manus must know
1. **Two SmartWEB modules:**
   - `scrapers/smartweb_parser.py` → **`scrape_smartweb(...)`** (full GET/POST + parse). Used by Escambia etc. **Do not overwrite this file carelessly.**
   - `scrapers/smartweb_card_parser.py` → **`parse_smartweb_cards(html, county=, facility=, detail_url=, ...)`** for HTML fragments.
2. SmartWEB submit button is often misspelled **`btnSumit`**.
3. Prefer **direct HTTP first**, then APE proxy — many SmartCOP hosts fail CONNECT with **502**.
4. Dashboard container is often **`read_only: true`** — must rebuild image for code changes (not just `docker cp` unless leads container allows it).
5. Multi-state job IDs: non-FL = `scraper_<st>_<county>`; CLI `python main.py sc_lee` / `ga_lee`. Labels `County (ST)`.

---

## Current live error scrapers (next work)

Run:
```bash
# After PIN login cookie:
GET /api/ops/scraper-registry
# Filter status == "error"
```

**Last known error set (~9):**

| County | Error class | Suggested approach |
|--------|-------------|--------------------|
| **Bay** | 0 records / layout / session | Recon roster; check UniGUI / anti-bot; see existing `bay.py` + scratch tests |
| **Gadsden** | 0 records — needs recon | JS-gated or blank; recon public URL |
| **Gilchrist** | DNS dead / proxy 502 | Hosts like `smartcop.gilchristsheriff.com` **NXDOMAIN**. Find real portal or mark scaffold + quiet |
| **Hillsborough** | SOCKS proxy failure | Direct-first or sticky session; HCSO may need credentials in env (`HCSO_*`) |
| **Lake** | HTTP 400 | Request shape/CAPTCHA; recon |
| **Marion** | 403 | WAF/Cloudflare — residential proxy / browser path |
| **Monroe** | SSL incomplete chain (and may 403 after) | `verify=False` added in monroe.py; **confirm deployed**; may still hit 403 on disclaimer POST |
| **Okeechobee** | No records from JSON/HTML | Parse/endpoint drift |
| **Suwannee** | Upstream **500** on SmartCOP POST | Their server; retry later; GET page may work — check if wildcard `%` or form fields changed |

Also: **~8 never_run** scrapers — first production scrape via Multi-State Ops / one-shot.

---

## Explicit backlog (ordered)

### A. Staff / product smokes (human-gated — prepare, don’t spam clients)
1. User **re-login** after SECRET_KEY (Bond Intelligence works with auth — verified 200).
2. Outbound **iMessage test** to Brendan’s number only if approved (`/api/imessage/send` or UI).
3. SignNow **test packet** on disposable case (token OK).
4. SwipeSimple pay link smoke.
5. Live write-bond → GAS once (GAS health already OK).

### B. Scraper recovery (engineering)
1. Fix remaining FL error counties (table above).
2. Monroe: verify `verify=False` path live; handle disclaimer 403.
3. Hillsborough: remove forced broken SOCKS when direct works.
4. Suwannee: when portal 200, align with `scrape_smartweb` / wildcard `%` like historical path.
5. Gilchrist: deep recon or disable with documented reason in `docs/COUNTY_REGISTRY.md`.
6. Avoid false “ok” with only 1–3 junk rows — prefer SmartWEB card parsers.

### C. Data quality
1. **Defendants backfill:** `POST /api/defendants/normalize/batch` with `{limit: 500–2000}` in loops (Lee/Collier first, then ALL). Monitor errors (had ~49/300 earlier).
2. Optional: hook normalize after mongo upsert for **new** arrests only (sync writer vs async motor — careful).
3. Multi-state KPI filter already drops non-USPS codes in `dashboard/routers/stats.py` (`_US_STATES`). Optionally clean bad `state` values in Mongo for known junk.

### D. Stability
1. Document docker recreate race; prefer `docker rm -f` then `up -d` when recreate fails.
2. Watch `docker logs shamrock-leads` for import errors after any parser refactor.
3. Never replace `smartweb_parser.scrape_smartweb` without grepping all importers:
   ```bash
   rg "from scrapers.smartweb_parser|scrape_smartweb|smartweb_card_parser" scrapers/
   ```

### E. Out of scope unless asked
- Phase 18 phone→autopilot state machine  
- Full GA 159 / NC 100 expansion  
- Bail School LMS (separate repo)  
- Minting new GAS Web App URL (**forbidden** without human + Wix Secrets update)

---

## Safety non-negotiables (from AGENTS.md)

1. No guessing identity, POA, case numbers, payment/signature status.  
2. Fail closed on ambiguous match.  
3. No paperwork before validated bond case chain.  
4. Minimize PII in Slack/logs.  
5. GAS URL stability — re-deploy existing deployment only.  
6. Shamrock exclusive identity (no WTF / foreign entities).  
7. Score everything that enters Mongo.  
8. Idempotent writes: `state + county + booking_number`.

---

## Verification checklist after each change

```text
[ ] git push origin main
[ ] VPS git reset --hard origin/main
[ ] rebuild affected containers; health = healthy
[ ] docker logs shamrock-leads — no ImportError / crash loop
[ ] GET /health → 200
[ ] PIN login → GET /api/crm/health → status ok
[ ] GET /api/bond-intelligence?days=7 → 200 (not 401)
[ ] GET /api/imessage/status → connected true (if BB expected)
[ ] GET /api/ops/scraper-registry → error count not worse
[ ] one-shot fixed county → records_scraped > previous junk baseline
[ ] update STATUS.md + COUNTY_REGISTRY if county URL/parser changed
```

---

## Suggested first tasks for Manus (copy this checklist)

1. **Confirm fleet health** on VPS (`shamrock-leads` + `shamrock-dashboard` healthy; no crash loop).  
2. **Confirm Bond Intelligence** after re-login (if user still sees 401 → clear cookies / hard refresh).  
3. **Monroe:** deploy/verify SSL fix; one-shot `python main.py monroe`; if 403, recon disclaimer flow.  
4. **Hillsborough + Lake + Marion:** direct-first / proxy strategy; one-shot each.  
5. **Bay + Okeechobee + Gadsden:** recon roster HTML; fix parse or mark needs-recon honestly.  
6. **Suwannee:** probe portal; if still 500, document and skip until upstream recovers.  
7. **Defendants:** run 3–5 more normalize batches (`limit: 1000`) for SWFL counties; report coverage %.  
8. **Update** `STATUS.md` “Live prod verification” with date + results.  
9. Prefer **small commits** with deploy + one-shot proof in message body.

---

## Key file map

| Area | Path |
|------|------|
| Agent rules | `AGENTS.md`, `BRAND.md` |
| Status / roadmap | `STATUS.md`, `ROADMAP.md` |
| Prod checklist | `docs/ECOSYSTEM_PROD_CHECKLIST.md` |
| Scraper bases | `scrapers/base_scraper.py`, `scrapers/smartcop_base.py` |
| SmartWEB helpers | `scrapers/smartweb_parser.py`, `scrapers/smartweb_card_parser.py` |
| PIN / session | `dashboard/auth/pin_middleware.py` |
| Bond Intelligence UI | `dashboard/sl-bond-intelligence.js` |
| Bond Intelligence API | `dashboard/routers/stats.py` → `GET /api/bond-intelligence` |
| Multi-state ops API | `dashboard/routers/multi_state_ops.py` |
| BB config | `dashboard/extensions.py` → `init_bluebubbles` |
| Defendants normalize | `POST /api/defendants/normalize/batch` · `dashboard/services/defendant_normalizer.py` |
| County registry | `docs/COUNTY_REGISTRY.md` |

---

## Prompt for Manus (paste as system/user task)

```text
You are continuing production hardening on ShamrockLeads (repo Shamrock2245/shamrock-leads, branch main).

Read in order: BRAND.md, AGENTS.md, STATUS.md, docs/MANUS_HANDOFF_PROD_HARDENING.md (this handoff), docs/ECOSYSTEM_PROD_CHECKLIST.md.

Context: A prior session already:
- Set VPS SECRET_KEY + ENV=production; CRM health is ok
- Fixed BlueBubbles BB_SERVERS rebind bug; BB connected via frp :12434
- Fixed Bond Intelligence 401 UX (user must re-login once after SECRET_KEY change)
- Fixed Bradford/Dixie/Taylor SmartWEB hosts and parsers; restored scrape_smartweb after a bad overwrite
- Seeded ~594 defendants via normalize/batch
- Deployed to Hetzner root@178.156.179.237:/opt/shamrock-leads

Your job NOW:
1) Verify fleet healthy (no ImportError crash loops).
2) Reduce FL scraper_status errors for: Monroe, Hillsborough, Lake, Marion, Bay, Okeechobee, Gadsden, Gilchrist, Suwannee (document if blocked on upstream).
3) Do not overwrite scrapers/smartweb_parser.py scrape_smartweb API; use smartweb_card_parser for HTML cards.
4) Prefer direct HTTP before APE proxies for SmartCOP/SmartWEB (CONNECT 502 is common).
5) Run defendants normalize/batch in safe chunks; report coverage.
6) After each fix: one-shot scrape, update STATUS.md / COUNTY_REGISTRY.md, commit+push, deploy, prove health.
7) Follow AGENTS.md safety rules (PII, fail closed, no new GAS URL).

Start by SSH health check + scraper-registry error list, then attack Monroe and Hillsborough first.
Do not claim production-hardened Stage 2 until checklist B/D items are truly green.
```

---

## Contact / human gates

- **Human required:** outbound client iMessage, real payment, destructive bond status, GAS URL change, Wix Secrets.  
- **Super-admin email:** `admin@shamrockbailbonds.biz`  
- Keep revenue automations in **`review`** until BB reliable ~7 days (policy).

---

*End of handoff. When Manus finishes a chunk, append a short “Session results” section to STATUS.md with date, error count before/after, and commits.*
