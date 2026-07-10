# ShamrockLeads OSINT Worker

Internal service that runs **Maigret** and **Blackbird** on a **writable** filesystem.

The dashboard (`shamrock-dashboard`) stays read-only and calls this worker over
the Docker network. No host ports are published.

## Defaults (policy)

| Setting | Default |
|---------|---------|
| Maigret | **ON** |
| Blackbird | **OFF** |
| Blackbird + email | **ON** (email-focused recon) |
| Second opinion | Forces Maigret **+** Blackbird |
| Recursion | **Disabled** (noise control) |
| Quick scan | Top ~250 sites |
| Deep scan | Top ~800 sites (still not full `-a`) |
| Risk score | **Advisory only** — not auto-applied to bond risk |

## API

- `GET /health` — liveness
- `GET /status` — tool probe (optional `X-Worker-Key`)
- `POST /v1/scan` — synchronous scan (30–180s)

```json
{
  "usernames": ["handle"],
  "full_name": "Jane Doe",
  "email": null,
  "deep_scan": false,
  "run_maigret": null,
  "run_blackbird": null,
  "second_opinion": false
}
```

`null` tool flags mean “apply policy defaults”.

## Env

| Variable | Purpose |
|----------|---------|
| `OSINT_WORKER_KEY` | Shared secret with dashboard (`X-Worker-Key`) |
| `OSINT_MAIGRET_TIMEOUT` | Max seconds per Maigret run (default 180) |
| `OSINT_BLACKBIRD_TIMEOUT` | Max seconds per Blackbird run (default 150) |
| `OSINT_QUICK_TOP_SITES` | Default 250 |
| `OSINT_DEEP_TOP_SITES` | Default 800 |

## Compose

```bash
docker compose build osint-worker
docker compose up -d osint-worker dashboard
```

Dashboard env: `OSINT_WORKER_URL=http://osint-worker:5065`
