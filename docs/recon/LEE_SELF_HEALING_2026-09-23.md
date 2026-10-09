# Lee FL — self-healing / reliability (2026-09-23)

Branch: `fix/lee-self-healing` (follows merged PR #45 cooldown→error visibility).

## Root causes (prod empty vs Mac OK)

1. **Silent empty on cooldown** — VPS `/32` public-api throttle tripped; early-return `[]` became Health `status=empty` with `duration≈0`. Fixed in PR #45 (`RuntimeError` → `status=error`).
2. **Silent empty on fetch failure** — pagination broke on `None` / non-200 / bad JSON and still returned `[]` → Health looked like “no arrests”. Now raises when `ok_pages==0`.
3. **Process-local cooldown** — PM2/process restart forgot the cooldown and re-hammered Lee’s 480k/12h bucket. Cooldown is now durable under `/tmp/shamrock_lee_public_api_cooldown.json` (override `LEE_RATE_LIMIT_STATE_PATH`; disable with `LEE_RATE_LIMIT_PERSIST=false`).
4. **Egress (updated 2026-10-08)** — Lee uses **plain direct HTTPS only** (`requests`, honest User-Agent, normal DNS, `trust_env=False`). The APE StealthSession / Scrapfly / SOCKS / origin-pin path was removed from `lee.py` (Manatee/Charlotte pattern). The VPS must be able to read `/public-api/bookings` without stealth; if it cannot, say so and do not reintroduce proxies. `scripts/lee_direct_smoke.py` is the read-only check.

## What “self-fixing” means now

| Signal | Behavior |
|--------|----------|
| Active cooldown / HTTP 429 | Raise → Health `error` with remaining seconds; **no** scrape traffic; durable file keeps other processes cold |
| Cooldown window ends | Next `is_cooled_down()` auto-clears memory + file; next scheduled run proceeds (no manual reset) |
| Transient fetch failure (connect/5xx, no ok pages) | Outer scrape retries with exponential backoff; still raises if all attempts fail |
| True empty roster (HTTP 200, empty arrays) | Health `empty` — honest “no bookings in window” |
| Partial bookings then mid-run 429 | Keep recovered rows; skip enrichment; do **not** invent booking keys |

## Env (existing + new)

- `LEE_RATE_LIMIT_COOLDOWN_S` (default 10800)
- `LEE_RATE_LIMIT_STATE_PATH` — durable cooldown JSON path
- `LEE_RATE_LIMIT_PERSIST` — set `false` to keep memory-only (tests)
- Lean defaults: `LEE_DAYS_BACK`, `LEE_MAX_PAGES`, `LEE_MAX_ENRICH`, delays — raise only with monitoring
- Removed (2026-10-08): `LEE_PREFER_RESIDENTIAL`, `LEE_ALLOW_DIRECT`, SOCKS/APE/Scrapfly knobs for Lee

## Prod blockers (ops, not code)

- VPS must reach `https://www.sheriffleefl.org/public-api/bookings` with a plain GET (no proxy, no stealth). Run `python scripts/lee_direct_smoke.py` and expect exit 0 with `NNNNNNN` booking shapes before declaring the path mergeable.
- Confirm `LEE_RATE_LIMIT_*` env on VPS if custom paths desired
- Do **not** reintroduce StealthSession, Scrapfly, SOCKS, curl_cffi impersonation or CAPTCHA solving for Lee
