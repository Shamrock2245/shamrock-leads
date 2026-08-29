# ShamrockLeads OSINT Worker

Internal service that runs **Maigret**, **Sherlock**, **Blackbird**, **SpiderFoot**,
**Ignorant**, **Holehe**, **Toutatis**, **GHunt**, and **h8mail** on a **writable**
filesystem.

The dashboard (`shamrock-dashboard`) stays read-only and calls this worker over
the Docker network. No host ports are published.

## Engines

| Engine | Input | Purpose |
|--------|-------|---------|
| **Maigret** | username | 1000s of site username claims |
| **Sherlock** | username | Cross-check username sites |
| **Blackbird** | username / email | Second-opinion username + email |
| **SpiderFoot** | email / phone / name | Correlation / entity harvest |
| **Ignorant** | **phone** | Passive phone registration on IG / Snap / Amazon ([megadose/ignorant](https://github.com/megadose/ignorant)) |
| **Holehe** | **email** | Passive email registration on 120+ sites incl. Instagram ([megadose/holehe](https://github.com/megadose/holehe)) |
| **HIBF** | **plate** | Public Flock LE search audit logs ([haveibeenflocked.com](https://haveibeenflocked.com)) — not live camera hits |
| **Toutatis** | **username** | Instagram enrichment: public/obfuscated email & phone ([megadose/toutatis](https://github.com/megadose/toutatis)) |
| **GHunt** | **email** | Google account: GAIA, Maps reviews, photos, services ([mxrch/GHunt](https://github.com/mxrch/GHunt)). Needs worker login. Not a phone ping. |
| **h8mail** | **email** | Breach/dump sources ([khast3x/h8mail](https://github.com/khast3x/h8mail)). Optional local files + Hunter.io. Does not email the target. |

### Ignorant notes

Does **not** message the target phone. Results use `source: "ignorant"` and
`profile_data.phone_registered: true`.

### Holehe notes

Does **not** email the target. Results use `source: "holehe"` and
`profile_data.email_registered: true`. Recovery hints stay on `profile_data`
for staff and are not logged. Auto-selected when an email is present.

### Toutatis notes

Requires a live Instagram **`sessionid` cookie** on the worker:

```bash
# VPS / .env — never commit
INSTAGRAM_SESSION_ID=...   # DevTools → Application → Cookies → sessionid
# alias accepted:
TOUTATIS_SESSION_ID=...
```

Without the cookie, the Toutatis status pill is unavailable and scans fail closed.
Rotate the cookie if it leaks; do not log it.

### GHunt notes

Does **not** ping phones or convert IPs to street addresses. Email hunt can
surface Google Maps review locations when the Google account has public reviews.

One-time login — **do this from the dashboard**, not `docker exec`:

1. Chrome → `chrome://extensions` → Developer mode → **Load unpacked** →
   `tools/ghunt-companion` in this repo (MV3; Chrome Web Store listing is gone).
2. Sign into a **dedicated research Google account** in that Chrome profile.
3. Click the Companion icon → **Synchronize to GHunt** → finish Google sign-in → **Copy blob**.
4. ShamrockLeads → OSINT → Engines → GHunt card → paste → **Save GHunt session**.

The worker exchanges that blob for `creds.m` on volume `osint-ghunt-creds`. Method 1 (listener on port 60067) does **not** work in Docker.

Alternate: put the blob in `.env` as `GHUNT_COMPANION_B64=...` and restart `osint-worker`.

Without login, GHunt is `package_installed` but not `available` (fail closed).

### h8mail notes

Does **not** email the target. Passwords are never written to application logs;
staff see source names on the finding, full dump lines only on the raw report.

Optional:

| Env / volume | Purpose |
|--------------|---------|
| `HUNTER_API_KEY` | Uses the existing Hunter.io key if set |
| `H8MAIL_HIBP_KEY` | Have I Been Pwned key (optional) |
| volume `osint-h8mail-breaches` → `/opt/h8mail-breaches` | Local dump files you are allowed to use |

Auto-selected when an email is present (same as Holehe).

## Defaults (policy)

| Setting | Default |
|---------|---------|
| Maigret | **ON** |
| Sherlock | **ON** (UI default chip) |
| Blackbird | **OFF** |
| Blackbird + email | **ON** (email-focused recon) |
| SpiderFoot | **OFF** unless selected |
| Ignorant | **OFF** unless selected; UI auto-selects when a 10+ digit phone is entered |
| Holehe | **OFF** unless selected; UI auto-selects when an email is entered |
| GHunt | **OFF** unless selected; UI auto-selects when an email is entered (needs login) |
| h8mail | **OFF** unless selected; UI auto-selects when an email is entered |
| HIBF | **OFF** unless selected; UI auto-selects when a license plate is entered |
| Toutatis | **OFF** unless selected; UI auto-selects when usernames present **and** session configured |
| Recursion | **Disabled** (noise control) |
| Risk score | **Advisory only** — not auto-applied to bond risk |

## API

- `GET /health` — liveness
- `GET /status` — tool probe (optional `X-Worker-Key`)
- `POST /v1/scan` — legacy Maigret + Blackbird
- `POST /v2/scan` — multi-engine (dashboard uses this)

```json
{
  "usernames": ["handle"],
  "full_name": "Jane Doe",
  "email": null,
  "phone": "+12395550100",
  "deep_scan": false,
  "engines": ["maigret", "sherlock", "ignorant", "holehe", "toutatis"]
}
```

## Env

| Variable | Purpose |
|----------|---------|
| `OSINT_WORKER_KEY` | Shared secret with dashboard (`X-Worker-Key`) |
| `OSINT_MAIGRET_TIMEOUT` | Max seconds per Maigret run (default 180) |
| `OSINT_BLACKBIRD_TIMEOUT` | Max seconds per Blackbird run (default 150) |
| `OSINT_IGNORANT_TIMEOUT` | Max seconds for Ignorant phone check (default 45) |
| `OSINT_HOLEHE_TIMEOUT` | Max seconds for Holehe email check (default 90) |
| `OSINT_TOUTATIS_TIMEOUT` | Max seconds for Toutatis (default 60) |
| `INSTAGRAM_SESSION_ID` | Instagram `sessionid` cookie for Toutatis (**required**) |
| `TOUTATIS_SESSION_ID` | Alias for `INSTAGRAM_SESSION_ID` |
| `OSINT_QUICK_TOP_SITES` | Default 250 |
| `OSINT_DEEP_TOP_SITES` | Default 800 |

## Compose

```bash
docker compose build osint-worker
docker compose up -d osint-worker dashboard
```

Dashboard env: `OSINT_WORKER_URL=http://osint-worker:5065`
