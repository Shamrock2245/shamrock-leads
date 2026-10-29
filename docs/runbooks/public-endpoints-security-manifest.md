# Public Endpoints & Security Gate Manifest (`PinAuthMiddleware`)

> **Source of Truth:** `dashboard/auth/pin_middleware.py`
> **Contract Tests:** `tests/test_prod_auth_allowlist.py`, `tests/test_auth_no_pin_fail_closed.py`, `tests/test_lee_county_hq_auth.py`, `tests/test_bb_config_key_fail_closed.py`

---

## 1. Default Posture (Fail-Closed)

Every route on `shamrock-dashboard` (`https://leads.shamrockbailbonds.biz`) requires a signed staff session cookie (`sl_session`, signed with `SECRET_KEY` via `itsdangerous.URLSafeTimedSerializer`) or a constant-time verified machine key (`GAS_API_KEY` / `LEADS_INTERNAL_TOKEN`) **unless** it is explicitly listed below.

- When `DASHBOARD_PIN` or `SECRET_KEY` is unset outside `ENV=development`, protected routes fail closed with HTTP `503`.
- Unauthenticated requests to `/api/*` or `/openapi.json` return `401 {"error": "Authentication required"}`.
- Unauthenticated browser requests to HTML pages redirect (`302`) to `/login`.
- `recovery` role sessions are evaluated **before** `OPEN_PATHS`/`OPEN_PREFIXES` and denied every route outside `dashboard/auth/recovery_scope.py`.

---

## 2. Deliberate Public Exact Paths (`OPEN_PATHS`)

| Path | Why It Is Public | Secondary Protection |
|---|---|---|
| `/health`, `/health/live` | Load balancer, Docker healthcheck, and `scripts/prod_uptime_watchdog.py` liveness probes. | Read-only status booleans; zero secrets or PII returned. |
| `/login` | Staff and sub-agent PIN/email sign-in screen. | Rate-limited POST; constant-time PIN check; requires `DASHBOARD_PIN` and `SECRET_KEY`. |
| `/kiosk` | Neutral in-office lobby tablet home screen (idle reset target). | Static shell containing zero arrestee or case data. |
| `/paperwork`, `/done` | Public indemnitor paperwork completion landing pages (`paperwork.shamrockbailbonds.biz`). | Requires OTP PIN verification or signed token before loading any case data. |
| `/robots.txt` | Search engine crawl disallow file (`Disallow: /`). | Exact path only (does not open other `.txt` files). |
| `/manifest.json`, `/favicon.ico`, `/favicon.png`, `/apple-touch-icon.png`, `/shamrock-logo.png` | Browser PWA/icon assets needed on `/login` and `/paperwork`. | Static branding assets only. |

---

## 3. Deliberate Public Prefixes (`OPEN_PREFIXES` & Special Routes)

| Prefix / Route | Purpose | Secondary Gate / Authentication |
|---|---|---|
| `/c/{token}`, `/cosigner/{token}`, `/api/cosigner/*` | Co-signer mobile flow (ID scan → DocuSeal → SwipeSimple down payment). | 256-bit random token (`cosigner_links`), TTL expiration, tenant scope, per-IP rate limiting, and 10 MB upload cap. |
| `/g/{code}`, `/sign/{code}`, `/track/*` | Short-link redirects for signing/portal links and skip-trace redirects. | Random opaque short code lookup; expired/unknown codes fail closed (`404`). |
| `/api/portal/*`, `/paperwork/*` | Indemnitor self-service portal APIs. | 6-digit OTP PIN bound to phone/case + rate limiting (`dashboard/routers/portal.py`). |
| `/paperwork/shannon/id/{token}`, `POST /api/paperwork/shannon/id/{token}` | Caller ID photo upload during active Shannon voice calls. | Single-case `secrets.token_urlsafe(18)` token with TTL (`_TOKEN_TTL_HOURS`) and file size cap (`_MAX_BYTES`). |
| `POST /api/paperwork/shannon/id-link`, `POST /api/paperwork/shannon/id-status`, `POST /api/paperwork/shannon/email` | Shannon voice tool callbacks from GAS / Netlify Edge proxy. | Route-level `_require_control_auth(request, allow_machine=True)` requiring `GAS_API_KEY` or `LEADS_INTERNAL_TOKEN`. |
| `POST /api/imessage/shannon/send`, `POST /api/imessage/wix/send` | Machine-triggered BlueBubbles sends from Shannon voice and Wix clipboard. | Route-level `_require_control_auth(request, allow_machine=True)` + BlueBubbles consent-ledger gate (`bb_consent_ledger_gate`). |
| `POST /api/agent-brain/memory/lookup`, `GET /api/agent-brain/memory/status` | Shannon voice Mem0 caller context lookup and non-secret readiness check. | `lookup` requires `_require_control_auth(request, allow_machine=True)`; `status` returns boolean config state only. |
| `POST /api/config/bluebubbles-url` | Office iMac tunnel sync script (`scripts/bb-url-sync.sh`). | Requires constant-time verified `BB_CONFIG_API_KEY`, machine auth, or admin session; fails closed (`503`) if unconfigured. |
| `/traccar/setup/*`, `GET /api/traccar/device-status/{id}` | Client GPS check-in setup guide and device pairing check. | `device-status` requires an HMAC-signed token bound to `device_id` (`verify_device_status_token`). |
| `/signup`, `POST /api/public/signup` | Multi-tenant bail agency onboarding. | Returns `404` unless `SAAS_MULTI_TENANT` is enabled; rate-limited and never echoes secrets. |
| `POST /api/webhooks/*`, `POST /api/traccar/webhook` | Inbound provider webhooks (DocuSeal, SwipeSimple, Wix, Twilio, BlueBubbles, Traccar). | `GET`/`DELETE` and `/api/webhooks/bluebubbles/register` are **not** exempt (require staff session). Every `POST` webhook verifies its provider secret/HMAC (`WIX_WEBHOOK_SECRET`/`GAS_API_KEY`, `DOCUSEAL_WEBHOOK_SECRET`, `PAYMENT_WEBHOOK_SECRET`, `TWILIO_AUTH_TOKEN`, `TRACCAR_WEBHOOK_SECRET`) and fails closed (`503`/`401`) outside `ENV=development`. |
