# ShamrockLeads OSINT Worker

Internal service that runs **Maigret**, **Sherlock**, **Blackbird**, **SpiderFoot**,
and **Ignorant** on a **writable** filesystem.

The dashboard (`shamrock-dashboard`) stays read-only and calls this worker over
the Docker network. No host ports are published.

## Engines

| Engine | Input | Purpose |
|--------|-------|---------|
| **Maigret** | username | 1000s of site username claims |
| **Sherlock** | username | Cross-check username sites |
| **Blackbird** | username / email | Second-opinion username + email |
| **SpiderFoot** | email / phone / name | Correlation / entity harvest |
| **Ignorant** | **phone** | PassivePassive** check if number is registered on Instagram, Snapchat, Amazon ([megadose/ignorant](https://github.com/megadose/ignorant)) |

Ignorant **does not message or alert the target phone**. It only hits public
registration/login endpoints. Results map to accounts with
`source: "ignorant"` and `profile_data.phone_registered: true`.

## Defaults (policy)

| Setting | Default |
|---------|---------|
| Maigret | **ON** |
| Sherlock | **ON** (UI default chip) |
| Blackbird | **OFF** |
| Blackbird + email | **ON** (email-focused recon) |
| SpiderFoot | **OFF** unless selected |
| Ignorant | **OFF** unless selected; UI auto-selects when a 10+ digit phone is entered |
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
  "engines": ["maigret", "sherlock", "ignorant"]
}
```

## Env

| Variable | Purpose |
|----------|---------|
| `OSINT_WORKER_KEY` | Shared secret with dashboard (`X-Worker-Key`) |
| `OSINT_MAIGRET_TIMEOUT` | Max seconds per Maigret run (default 180) |
| `OSINT_BLACKBIRD_TIMEOUT` | Max seconds per Blackbird run (default 150) |
| `OSINT_IGNORANT_TIMEOUT` | Max seconds for Ignorant phone check (default 45) |
| `OSINT_QUICK_TOP_SITES` | Default 250 |
| `OSINT_DEEP_TOP_SITES` | Default 800 |

## Compose

```bash
docker compose build osint-worker
docker compose up -d osint-worker dashboard
```

Dashboard env: `OSINT_WORKER_URL=http://osint-worker:5065`
