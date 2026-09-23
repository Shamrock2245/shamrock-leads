# Lee FL — self-healing / reliability (2026-09-23)

Branch: `fix/lee-self-healing` (follows merged PR #45 cooldown→error visibility).

## Root causes (prod empty vs Mac OK)

1. **Silent empty on cooldown** — VPS `/32` public-api throttle tripped; early-return `[]` became Health `status=empty` with `duration≈0`. Fixed in PR #45 (`RuntimeError` → `status=error`).
2. **Silent empty on fetch failure** — pagination broke on `None` / non-200 / bad JSON and still returned `[]` → Health looked like “no arrests”. Now raises when `ok_pages==0`.
3. **Process-local cooldown** — PM2/process restart forgot the cooldown and re-hammered Lee’s 480k/12h bucket. Cooldown is now durable under `/tmp/shamrock_lee_public_api_cooldown.json` (override `LEE_RATE_LIMIT_STATE_PATH`; disable with `LEE_RATE_LIMIT_PERSIST=false`).
4. **Egress** — Lee prefers residential via APE (`LEE_PREFER_RESIDENTIAL=true` default) so VPS datacenter IP does not share the `/32` quota. Mac smoke often succeeds on direct/origin-pin without Warren; prod reliability still wants Warren/residential healthy on the VPS.

## What “self-fixing” means now

| Signal | Behavior |
|--------|----------|
| Active cooldown / HTTP 429 | Raise → Health `error` with remaining seconds; **no** scrape traffic; durable file keeps other processes cold |
| Cooldown window ends | Next `is_cooled_down()` auto-clears memory + file; next scheduled run proceeds (no manual reset) |
| Transient fetch failure (connect/5xx, no ok pages) | Outer scrape retries with exponential backoff + origin-pin invalidate; still raises if all attempts fail |
| True empty roster (HTTP 200, empty arrays) | Health `empty` — honest “no bookings in window” |
| Partial bookings then mid-run 429 | Keep recovered rows; skip enrichment; do **not** invent booking keys |

## Env (existing + new)

- `LEE_PREFER_RESIDENTIAL` (default true) — use APE residential when available
- `LEE_ALLOW_DIRECT` (default true) — allow origin-pinned direct when not cooled down
- `LEE_RATE_LIMIT_COOLDOWN_S` (default 10800)
- `LEE_RATE_LIMIT_STATE_PATH` — durable cooldown JSON path
- `LEE_RATE_LIMIT_PERSIST` — set `false` to keep memory-only (tests)
- Lean defaults: `LEE_DAYS_BACK`, `LEE_MAX_PAGES`, `LEE_MAX_ENRICH`, delays — raise only with residential + monitoring

## Prod blockers (ops, not code)

- VPS must have working Warren/APE residential if datacenter `/32` is near quota
- Confirm `LEE_RATE_LIMIT_*` env on VPS if custom paths desired
- Do **not** implement illicit WAF/residential bypass beyond existing APE mechanism
