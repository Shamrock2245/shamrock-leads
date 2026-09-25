# Scraper self-healing, fail-loud alerts, and auto-disable

> **Status:** implemented in `BaseScraper.run()` (branch `feat/scraper-self-heal-fail-loud`).
> **Code:** `scrapers/scraper_resilience.py` (pure policy), `scrapers/base_scraper.py` (wiring),
> `writers/mongo_writer.py` (`scraper_status` persistence), `writers/slack_notifier.py` (alerts),
> `dashboard/routers/stats.py` + `dashboard/sl-health.js` (Health), `scripts/scraper_reenable.py` (CLI).

Before this change, `AGENTS.md`, `README.md`, `docs/ARCHITECTURE.md`, and the Watchdog agent doc
described retries, error classes, and auto-disable, but none of it was in the code. This file
describes what the code does now. Anything not listed here (URL pre-flight HEAD check,
10-entry failure history, `force_enable()`) **does not exist**.

## Order of checks in `BaseScraper.run()`

1. **Source-contract guard** (runs first, not changed): `SOURCE_CONTRACT_VALIDATED=False` or a Health
   `fail_closed` registry entry ⇒ the scraper does not fetch and gets status `guarded`. Resilience
   never overrides this.
2. **Auto-disable gate:** reads `consecutive_failures` / `auto_disabled` / `auto_disabled_at` from
   Mongo `scraper_status`. Disabled ⇒ status `auto_disabled`, no fetch, until the canary window
   opens (see re-enable below).
3. **`scrape()` with transient retry** (`_scrape_with_retry`).
4. **Booking-key filter** (not changed), then the **schema-drift check**.
5. Score / dedup / write / persist status (+ resilience fields).

## Retry policy (self-healing)

- Up to 3 retries after the first attempt, sleeping **2s, 4s, 8s**, only for the `network` class:
  connection errors, timeouts, DNS/SSL/socket errors, and HTTP 5xx.
- **Never retried:** HTTP 429, 401/403/Cloudflare/captcha (`anti_bot`), 404/410/redirect-to-home
  (`url_changed`), parse drift, unknown errors, and `SourceCooldownActive`.
- **Cooldowns:** `in_source_cooldown()` is checked before and after every backoff sleep. A scraper
  in an active cooldown is never retried. Lee overrides it with
  `scrapers/lee_rate_limit.is_cooled_down()`, and Lee also sets `BASE_RETRY_ENABLED=False` so the
  base layer adds no extra calls against the /32 quota (Lee keeps its own cooldown-aware logic).
- Cooldown skips (`SourceCooldownActive` or a "cooldown active" message) are logged as status
  `error`, class `anti_bot`, `cooldown_active=true`. They are **not** counted toward auto-disable.
  A plain 429 without a cooldown module counts as an `anti_bot` failure.

## Error classes (fixed set)

| Class | Meaning |
|---|---|
| `network` | connection/timeout/DNS/SSL/5xx: the only class that gets retried |
| `anti_bot` | 401/403/429, Cloudflare/Turnstile/captcha/"access denied" pages, `AntiBotBlocked` |
| `url_changed` | 404/410, redirect to a non-roster page, `SourceUrlChanged` |
| `parse_drift` | **added:** selector/schema drift. `ParseDriftError`, parser `KeyError`/`IndexError`/`AttributeError`, or the drift detector below |
| `unknown` | **added:** anything else. Kept separate so it is not mislabelled as a source problem |

## Fail loud: `#scraper-errors`

All alerts go through `writers/slack_notifier.py` using **`SLACK_WEBHOOK_ERRORS`** (env only; the URL
is never logged, and exceptions are logged by class name only).

- **Schema drift** ⇒ `notify_parse_drift` right away. "Total" drift = rows were parsed but **none**
  has a booking key. "Partial" drift = ≥50% of ≥10 rows lack a booking key or name. Total drift is
  treated as a run failure (`parse_drift`). Partial drift alerts but still writes the good rows.
  Drift alerts are throttled to one per county every 30 minutes.
- Other failures ⇒ `notify_scraper_error` with `error_class` and `consecutive_failures`.
- Crossing the threshold ⇒ `notify_scraper_auto_disabled`. Recovery ⇒ `notify_scraper_reenabled`.
- `ErrorTracker` no longer posts a second Slack message for the same failure.

## Auto-disable after 5 consecutive failures

- The counter is persisted on the scraper's `scraper_status` row (`consecutive_failures`,
  `error_class`, `auto_disabled`, `auto_disabled_at`, `auto_disabled_reason`). Any successful run
  (including a verified empty run) resets it to 0.
- At `SCRAPER_AUTO_DISABLE_THRESHOLD` (default 5) the row status becomes **`auto_disabled`**. Health
  shows it as "⛔ Auto-disabled" with the error class and has its own KPI counter. It is never
  shown as ok/empty.
- **KEY FL counties** (Lee, Sarasota, Collier, Charlotte, Manatee, DeSoto, Hendry) are exempt from
  being skipped. They still count failures and send one "threshold reached (exempt)" alert.

### Re-enable paths

1. **Automatic canary:** once `SCRAPER_AUTO_DISABLE_CANARY_MINUTES` (default 360) have passed since
   `auto_disabled_at`, the next scheduled run is allowed as a canary. It re-enables **only if it
   returns ≥1 record**. A failed canary stays disabled, restarts the window, and makes no Slack noise.
2. **Manual run-now** (dashboard "Run now" / `ScraperScheduler.run_now`) runs a forced canary. The
   same rule applies: records > 0 are needed to re-enable.
3. **Manual flag:** Health "▶ Re-enable" button → `POST /api/scraper/enable` clears
   `auto_disabled` and resets the counter. CLI: `python scripts/scraper_reenable.py "Lee (FL)" --by brendan`
   (`--list` shows the auto-disabled rows). The CLI reads `MONGODB_URI` from env.

Re-enabling never lifts a `fail_closed` or code-level source-contract guard.

## Obscura (CDP stealth browser) routing

- The infra exists: docker-compose service `obscura` (CDP on :9222, egress through the office
  residential SOCKS proxy) and `BaseScraper._get_obscura_browser[_sync]()` (`OBSCURA_CDP_URL`).
- New policy: `OBSCURA_ROUTE_COUNTIES` (comma-separated labels, **default empty**) is an opt-in used
  only for reliability on counties that are already `verified_public`. `OBSCURA_HARD_DENY_LABELS` (Hampton,
  Marlboro, Richland, Sumter SC; TnCIS TN; Sarasota FL; St. Clair AL; all FL JailTracker wrappers) are always
  refused, and so is any `fail_closed` scope (`ObscuraRoutingRefused`).
- No county is routed. `OBSCURA_ROUTE_COUNTIES` ships empty, and enabling one requires a `verified_public`
  source contract first.
- **TnCIS (TN): the Obscura fallback is OFF (owner decision 2026-09-25).** The portal
  (`lgc-tn.com/tncis-web-inquiry/`) sits behind Cloudflare and has no proven public contract. The former
  fallback chain (curl_cffi + residential proxy → curl_cffi + mobile proxy → Patchright stealth → Obscura)
  was removed from `scrapers/counties/tennessee_tncis_v2_ape.py`. TnCIS now fails closed in three places:
  1. `SOURCE_CONTRACT_VALIDATED = False` (`BaseScraper.run()` stops before any fetch, and Health shows `fail_closed`);
  2. `SCRAPER_SOURCE_STATES["TnCIS (TN)"] = "fail_closed"` plus a `hold` row in `live_emitter_evidence.json`;
  3. `TnCIS (TN)` is on `OBSCURA_HARD_DENY_LABELS`, so `_get_obscura_browser[_sync]()` raises `ObscuraRoutingRefused`.
  If `scrape()` is ever reached, it makes one ordinary direct request (no proxy, `trust_env=False`,
  no impersonation). A Cloudflare or anti-bot answer raises `AntiBotBlocked` (error class `anti_bot`),
  which is never retried and has no alternate route. `tests/test_tncis_fail_closed.py` enforces this.

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `SLACK_WEBHOOK_ERRORS` | *(unset ⇒ alerts are skipped with a warning)* | `#scraper-errors` webhook |
| `SCRAPER_AUTO_DISABLE_THRESHOLD` | `5` | consecutive failures before disable |
| `SCRAPER_AUTO_DISABLE_CANARY_MINUTES` | `360` | wait before an automatic canary |
| `SCRAPER_AUTO_DISABLE_ENABLED` | `true` | kill switch for the gate (counting continues) |
| `SCRAPER_BASE_RETRY_ENABLED` | `true` | kill switch for base-level retry |
| `OBSCURA_ROUTE_COUNTIES` | *(empty)* | opt-in reliability routing (verified_public only) |
| `OBSCURA_CDP_URL` | `ws://obscura:9222` | existing CDP endpoint |

## Known limits

- Scrapers that catch their own exceptions and return `[]` look like successful empty runs, so they
  can't be retried or counted. Those scrapers need to raise (or raise `ParseDriftError`) to take part.
- The `scraper_config.enabled` flag written by `/api/scraper/disable` is still not read by the
  scheduler. Only `auto_disabled` on `scraper_status` gates runs.

## Matrix drift gate

`docs/recon/COUNTY_SOURCE_CONTRACT_MATRIX.md` is generated. Do not edit it by hand. Change
`docs/recon/county_source_contract_evidence.json` / `docs/recon/live_emitter_evidence.json` /
`dashboard/extensions.py`, then run `python scripts/build_recon_matrix.py` (or `--check` to verify only).
`tests/test_source_state_drift.py` fails if the matrix is not byte-identical to the builder output,
or if the registry and the code-level guards disagree.

**CI wiring:** the "Syntax + contract suite" job lists test modules explicitly in
`.github/workflows/ci.yml`. The automation token cannot edit workflows (it lacks `workflow` scope),
so for now `tests/test_source_contract_run_guard.py::test_self_heal_and_matrix_drift_suites_pass`
runs the self-heal and drift suites in a subprocess. Follow-up for an owner with workflow scope:
add these modules to the pytest list and remove the bridge:

```
tests/test_dashboard_source_states.py
tests/test_scraper_resilience.py
tests/test_base_scraper_self_heal.py
tests/test_source_state_drift.py
tests/test_lee_rate_limit.py
```
