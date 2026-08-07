# DocuSeal Packet Automation — Audit Report

**Date:** 2026-08-07  
**Scope:** `docuseal_service.py`, `webhooks.py` DocuSeal handler, `paperwork.py` finalize path, unit tests  
**Host:** `sign.shamrockbailbonds.biz` · Webhook: `POST /api/webhooks/docuseal`

---

## 1. Architecture (confirmed)

| Item | Value |
|------|--------|
| DocuSeal | Self-hosted Docker `:5300` → nginx TLS |
| Template OSI | Env `DOCUSEAL_TEMPLATE_ID_OSI` / `DOCUSEAL_TEMPLATE_ID` (prod often `1`) |
| Roles | `Indemnitor`, `Co-Indemnitor`, `Defendant`, optional `Bondsman` |
| Prefill | `prefill_values_from_bond` → submitter `values` |
| Archive | Completed Bonds / {OSI\|PALMETTO} / {Defendant_YYYYMMDD}/ |

---

## 2. Edge-case safety

| Risk | Status | Notes |
|------|--------|--------|
| Money parse (`$1,000`, None, garbage) | **Hardened** | `_safe_money()` never raises |
| Amount words | **Hardened** | `_amount_to_words` / `_number_to_words` safe on 0/None/large ints |
| Missing bond keys | **OK** | All `.get()` with defaults; empty strings stripped before send |
| `defendant` not a dict | **OK** | Type-checked before use |
| Empty indemnitor list | **Hardened** | `_nonempty_party` filters empty dicts |
| Premium 10% / $100 min per charge | **OK** | Sums charge rows; falls back to total bond |
| 4-row charge grid | **OK** | `offense_1..4`, `case_number_*`, `poa_*`, `bond_amount_*` |
| Drive OAuth missing | **OK** | Returns `{ok: false}` without raising |
| Nested Drive folder fail | **Hardened** | Falls back to surety folder then Completed Bonds root |

---

## 3. Async / HTTP

| Item | Status |
|------|--------|
| `create_submission` / `list_templates` / etc. | All `async` + `await self._request` |
| httpx client | Per-request `AsyncClient` (simple, no shared-pool race). Optimization: shared client if volume spikes. |
| Webhook | Fully async; Drive file is sync helper (acceptable; wrap in thread if slow). |

---

## 4. Roles & submitters

| Case | Roles generated |
|------|-----------------|
| 1 cosigner | `Indemnitor`, `Defendant` |
| 2 cosigners | `Indemnitor`, `Co-Indemnitor`, `Defendant` |
| 3+ | `Indemnitor`, `Co-Indemnitor`, `Indemnitor 3`, … + Defendant |
| Bondsman | Only if `DOCUSEAL_INCLUDE_BONDSMAN=true` or `include_bondsman` in bond_data |

Sign links preserved via `normalize_create_response` (`embed_src` or `/s/{slug}`).

---

## 5. Webhooks

| Event | Action |
|-------|--------|
| `form.completed` | Party recorded; `partially_signed` |
| `submission.completed` / bare `completed` | Download PDF + Drive + `signed` |
| `form.declined` / `submission.expired` | Status update + audit |
| `submission.created` / `form.started` / `form.viewed` | Lifecycle log |

HMAC via `DOCUSEAL_WEBHOOK_SECRET` (warn-accept if unset for first boot).

---

## 6. MCP / env configuration

| Var | Purpose |
|-----|---------|
| `DOCUSEAL_URL` | Public API base |
| `DOCUSEAL_INTERNAL_URL` | Optional Docker DNS (`http://docuseal:3000`) |
| `DOCUSEAL_API_KEY` | REST `X-Auth-Token` |
| `DOCUSEAL_MCP_TOKEN` | Optional; same key for HTTP MCP clients |
| `DOCUSEAL_TEMPLATE_ID` / `_OSI` / `_PALMETTO` | Template IDs (OSI combined often `1`) |
| `DEFAULT_ESIGN_PROVIDER=docuseal` | Finalize default |

Project note: `.grok/config.toml` — self-hosted uses app env, not cloud MCP by default.

---

## 7. Remaining ops (not code)

1. VPS: DocuSeal up, API key in production `.env`  
2. Template field names must match prefill keys (or alias in DocuSeal UI)  
3. Webhook URL + secret in DocuSeal admin  
4. Google Drive OAuth with Completed Bonds folder access  
5. Palmetto combined template ID when uploaded  

---

## 8. Recommendations

1. Shared `httpx.AsyncClient` lifespan on dashboard for connection reuse under load.  
2. Run Drive upload in `asyncio.to_thread` to avoid blocking the event loop.  
3. Add Palmetto template mirror of OSI field map when Palmetto PDF is live.  
4. Portal PIN gate (S3) before handing `sign_url` to parties.

**Verdict:** Hydration stack is complete for OSI combined packet fields described in product spec; roles, money math, and webhooks are production-hardened for missing/None inputs. Remaining risk is **ops** (template field naming alignment + secrets on VPS), not missing prefill logic.
