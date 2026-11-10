# Lee County (FL) Scraper Failure & Silence Runbook

> **Scope:** `Lee (FL)` (`scrapers/counties/lee.py`, `core/lee_freshness.py`, `core/scheduler.py`)
> **Cadence:** Every 30 minutes (`scraper_lee`)
> **Freshness Monitor:** `dashboard/cron.py` (`lee_freshness`) via `core/lee_freshness.py`

---

## 1. Why Lee (FL) Is Mission-Critical

Lee County is Shamrock's home county. The public listing (`https://www.sheriffleefl.org/public-api/bookings`) exposes a short rolling window (~14 hours of bookings) and ignores `startBooking`, `endBooking`, `inCustody`, and `limit` query parameters. Any booking that rolls off the public listing before a successful scrape is permanently missed unless caught within the window.

### Non-Negotiable Constraints
1. **Plain, honest HTTPS only** (`ShamrockLeads/1.0 (+https://www.shamrockbailbonds.biz)`). Never enable stealth browsers, CAPTCHA solving, residential proxy bypasses, or Scrapfly for Lee County.
2. **Published `bookingNumber` only** (6–9 digits). Never fall back to `id` (14-digit internal row ID) or `permId`.
3. **Fail closed on unpublished fields:** If charges or bond amounts are not published on the detail endpoint, keep `Bond_Amount` as `""` (unknown), never invented `0`.

---

## 2. Automated Alerts & Freshness Findings (`core/lee_freshness.py`)

The freshness monitor inspects `scraper_status` (`county_label: "Lee (FL)"`) and alerts `#alerts` (`SLACK_WEBHOOK_ALERTS`) on five conditions:

| Finding Code | Trigger Condition | Default Threshold | Meaning |
|---|---|---|---|
| `no_status` | No `scraper_status` document exists for `Lee (FL)` | Immediate | Scraper has never reported to MongoDB. |
| `stale` | `now - last_success_at > LEE_FRESHNESS_MAX_AGE_MIN` | `75` min (2.5 cycles) | Scheduler stopped, container hung, or every run in the last 75 minutes failed. |
| `records_write_failed` | `records_write_failed_at > last_success_at` | Immediate | Listing was fetched, but `MongoWriter.write_records` failed; heartbeat was intentionally not advanced. |
| `repeated_errors` | `consecutive_failures >= LEE_FRESHNESS_MAX_FAILURES` | `3` runs | Upstream HTTP/TLS/parse failure on 3+ consecutive runs. |
| `zero_rows_site_has_bookings` | Last run stored `0` rows (`status` in `ok`/`empty`), but `probe_listing_count()` sees `>0` keyed rows on `sheriffleefl.org` | Immediate | Parser or filter regression dropped live bookings. |
| `window_gap` | `cur_min_bn > prev_max_bn + 1` in `source_window_gap` | Immediate | Listing rolled faster than successful runs; `unseen_numbers` bookings between `prev_max_bn` and `cur_min_bn` were not observed. |
| Scheduler `max_instances` / missed run | `EVENT_JOB_MISSED` or `ExecutionPool`/`max_instances` skip on `scraper_lee` (`core/scheduler.py`) | Immediate | Previous `scraper_lee` run exceeded 30m or hung (`MAX_EXECUTION_S = 330`). |

---

## 3. Triage Steps (Read-Only First)

### Step 1: Inspect `scraper_status` and Container Health on Hetzner VPS (`178.156.179.237`)
```bash
ssh root@178.156.179.237 "docker ps --filter name=shamrock-leads --filter name=shamrock-dashboard"
ssh root@178.156.179.237 "docker logs --tail 80 shamrock-leads | grep -iE 'lee|freshness|error'"
```

### Step 2: Check Persisted `Lee (FL)` Status (No PII)
```bash
ssh root@178.156.179.237 "docker exec shamrock-dashboard python3 -c '
import os, pymongo
c = pymongo.MongoClient(os.environ[\"MONGODB_URI\"])
doc = c[\"ShamrockBailDB\"][\"scraper_status\"].find_one(
    {\"county_label\": \"Lee (FL)\"},
    {\"_id\": 0, \"county_label\": 1, \"status\": 1, \"records\": 1, \"last_run_iso\": 1,
     \"last_success_at\": 1, \"records_write_failed_at\": 1, \"consecutive_failures\": 1,
     \"error_class\": 1, \"cooldown_active\": 1, \"source_window\": 1, \"source_window_gap\": 1}
)
print(doc)
'"
```

### Step 3: Probe Upstream Public API Directly (Plain HTTPS, Count Only)
```bash
ssh root@178.156.179.237 "docker exec shamrock-leads python3 -c '
from core.lee_freshness import probe_listing_count
print({\"live_keyed_rows\": probe_listing_count()})
'"
```

---

## 4. Recovery Playbook by Failure Mode

### A. `stale` or Scheduler Missed Run (`max_instances`)
1. Check if `shamrock-leads` is stuck on a hung network socket (despite `MAX_EXECUTION_S = 330`).
2. Restart the scraper container cleanly:
   ```bash
   ssh root@178.156.179.237 "cd /opt/shamrock-leads && docker compose restart shamrock-leads"
   ```
3. Trigger a single supervised Lee run and verify `last_success_at` advances:
   ```bash
   ssh root@178.156.179.237 "docker exec shamrock-leads python3 main.py lee"
   ```

### B. `records_write_failed`
1. Check MongoDB Atlas connectivity and M0/cluster storage quota (`GET /api/db-health` or `MongoWriter` ping).
2. Verify `MONGODB_URI` is valid and the database user has write permissions on `ShamrockBailDB.arrests` and `ShamrockBailDB.scraper_status`.
3. Once MongoDB writes are healthy, run `python3 main.py lee` once to persist the current rolling window and clear `records_write_failed_at`.

### C. `zero_rows_site_has_bookings` or `parse_drift`
1. Do **not** loop retries against the county server.
2. Inspect the schema of `https://www.sheriffleefl.org/public-api/bookings` (field names only — never log arrestee names or DOBs).
3. Update `scrapers/counties/lee.py` and `tests/test_lee_harden_spof.py` on a feature branch, verify CI passes, and deploy via standard PR merge.

### D. `anti_bot` / `429` Cooldown
1. Respect the cooldown window (`cooldown_active: True`). Never bypass rate limits or blocks.
2. Wait for the cooldown window to expire before running a single plain-HTTPS probe (`probe_listing_count()`).
