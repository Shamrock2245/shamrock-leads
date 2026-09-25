# Scraper Health Agent — "The Watchdog"

> **Status:** `[IMPLEMENTED]`
> **Implementation:** `writers/slack_notifier.py`, `scrapers/base_scraper.py`, `dashboard/routers/scraper_control.py`

---

## Role

The Watchdog monitors the health of all registered county scrapers (361 scopes). It detects failures, classifies error types, fires Slack alerts, and manages the self-healing infrastructure (auto-disable after 5 consecutive failures, canary re-enable).

---

## Monitoring Pipeline

```
Scraper Run (BaseScraper.run)
    → Source-contract guard (fail_closed / unvalidated ⇒ guarded, no fetch)
    → Auto-disable gate (auto_disabled ⇒ skip until canary window)
    → scrape() with transient retry (network/5xx: 2s, 4s, 8s; never 429/anti-bot/cooldown)
    → Booking-key filter → schema-drift check (drift ⇒ immediate #scraper-errors alert)
    → Success: reset consecutive_failures; canary success ⇒ auto re-enable + Slack
    → Failure:
        → Classify (network / anti_bot / url_changed / parse_drift / unknown)
        → Increment consecutive_failures on scraper_status (cooldowns not counted)
        → Slack #scraper-errors (classified)
        → If failures >= 5: status auto_disabled + Slack (KEY FL: alert only)
```

---

## Self-Healing Features

Implemented in `BaseScraper.run()`. Full runbook: [`docs/ops/SCRAPER_SELF_HEALING.md`](../ops/SCRAPER_SELF_HEALING.md).

| Feature | Description |
|---------|-------------|
| **Retry with backoff** | Transient `network` failures (connection/timeout/5xx) retried after 2s, 4s, 8s. Never retries 429, anti-bot, 404, parse drift, or an active per-county cooldown (Lee opts out; it has its own cooldown-aware logic) |
| **Error classification** | Fixed set: `network`, `anti_bot`, `url_changed`, `parse_drift`, `unknown` (persisted as `error_class` on `scraper_status`) |
| **Fail loud** | Schema/parse drift alerts `#scraper-errors` immediately (`SLACK_WEBHOOK_ERRORS`, throttled to 30 min per county). Classified failure alerts on every error |
| **Auto-disable** | `auto_disabled` after 5 consecutive counted failures (`SCRAPER_AUTO_DISABLE_THRESHOLD`). Shown as ⛔ on Health. KEY FL counties count and alert but are never skipped |
| **Re-enable** | Automatic canary every 6h (`SCRAPER_AUTO_DISABLE_CANARY_MINUTES`) that must return ≥1 record. Dashboard Run-now = forced canary. Health "Re-enable" button, `POST /api/scraper/enable`, or `scripts/scraper_reenable.py` |
| **Source-contract guard** | Runs before all of the above. Re-enable never lifts `fail_closed` |
| **Not implemented** | URL pre-flight HEAD check, failure-history list, `force_enable()` (earlier docs claimed these) |

---

## Key Files

| File | Purpose |
|------|---------|
| `writers/slack_notifier.py` | Slack alert formatting and delivery |
| `scrapers/scraper_resilience.py` | Pure policy: error classes, retry, drift detection, auto-disable state machine, Obscura routing policy |
| `scrapers/base_scraper.py` | Wires resilience into `run()`; persists state via `MongoWriter.upsert_scraper_status` |
| `scripts/scraper_reenable.py` | Manual re-enable CLI |
| `dashboard/routers/scraper_control.py` | Fleet status API, manual trigger, force-enable |
| `dashboard/sl-health.js` | Scraper Health tab frontend |

---

## Dashboard Integration

The **Scraper Health** tab (tab 5) shows:
- Fleet overview: total scrapers, active, disabled, error count
- Per-county status cards with last run time, record count, error details
- Manual trigger buttons for each county
- Force re-enable for disabled scrapers
- Error drill-down with classified failure history

---

## Constraints

- **Fail Loudly** — Every scraper error fires a Slack alert (Prime Directive #4)
- **Self-Heal First** — Retry 3x before alerting (Prime Directive #5)
- **Document Everything** — Every fix updates `COUNTY_REGISTRY.md` (Prime Directive #8)
- Never DDoS a county server — rate-limited requests with minimum 1s delay
