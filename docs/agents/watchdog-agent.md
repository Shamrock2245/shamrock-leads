# Scraper Health Agent — "The Watchdog"

> **Status:** `[IMPLEMENTED]`
> **Implementation:** `writers/slack_notifier.py`, `scrapers/base_scraper.py`, `dashboard/api/scraper_control.py`

---

## Role

The Watchdog monitors the health of all 51 county scrapers in real-time. It detects failures, classifies error types, fires Slack alerts, and manages the self-healing infrastructure (auto-disable after 5 failures, auto-recovery attempts).

---

## Monitoring Pipeline

```
Scraper Run
    → Success: Update last_success timestamp, reset failure count
    → Failure:
        → Classify error (network / anti_bot / url_changed / parse_error / ssl_error / rate_limited)
        → Increment consecutive failure count
        → Store in failure_history (last 10)
        → Fire Slack alert to #scraper-errors
        → If failures >= 5: Auto-disable scraper
        → If disabled: Attempt single recovery per interval
        → If recovery succeeds: Auto-re-enable
```

---

## Self-Healing Features

| Feature | Description |
|---------|-------------|
| **Pre-flight URL check** | HEAD request to roster URL before scraping — detects 404/403/SSL early |
| **Retry with backoff** | 3 attempts with exponential backoff (2s, 4s, 8s) |
| **Error classification** | Auto-classifies: `network`, `anti_bot`, `url_changed`, `parse_error`, `ssl_error`, `rate_limited` |
| **Auto-disable** | Scraper disabled after 5 consecutive failures |
| **Auto-re-enable** | Disabled scraper tries one recovery per interval — re-enables on success |
| **Failure history** | Last 10 failures stored with timestamps + error types |
| **Force re-enable** | `scraper.force_enable()` / `/api/scraper/enable/<county>` for human override |

---

## Key Files

| File | Purpose |
|------|---------|
| `writers/slack_notifier.py` | Slack alert formatting and delivery |
| `scrapers/base_scraper.py` | Self-healing logic, failure tracking, auto-disable/enable |
| `dashboard/api/scraper_control.py` | Fleet status API, manual trigger, force-enable |
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
