# ShamrockLeads OSINT Worker

Internal service that runs **Maigret**, **Sherlock**, **Blackbird**, **SpiderFoot**,
**Ignorant**, **Holehe**, and **Toutatis** on a **writable** filesystem.

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
| **Toutatis** | **username** | Instagram enrichment: public/obfuscated email & phone ([megadose/toutatis](https://github.com/megadose/toutatis)) |

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
