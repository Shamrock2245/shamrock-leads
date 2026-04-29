---
name: scraper-debugger
description: Systematic debugging and self-healing for county scrapers. Covers URL changes, anti-bot blocks, HTML structure changes, timeout issues, and automated recovery procedures.
---

# Scraper Debugger — Self-Healing Edition

> When a scraper stops returning data, follow this skill BEFORE guessing.
> The BaseScraper now auto-classifies errors and auto-disables after 5 consecutive failures.

## When to Use
- A county scraper returns 0 records unexpectedly
- Slack fires a `#scraper-errors` alert
- MongoDB shows stale data for a county (no new records in 2+ intervals)
- Dashboard shows scraper status as "Stale" / "Warning" / "Offline"
- BaseScraper auto-disabled a county (check `health_check()`)
- User says "Lee scraper is broken", "no data from Charlotte", etc.

## The Iron Rule

```
DO NOT CHANGE SCRAPER CODE UNTIL YOU IDENTIFY THE ROOT CAUSE.
```

---

## Phase 0: Auto-Diagnosis (Check Self-Heal Data First)

Before doing anything manual, check the BaseScraper's built-in diagnostics:

### Step 0.1 — Check Health Endpoint
```bash
curl http://localhost:5050/api/scraper-health | python -m json.tool
```

Look for:
- `"status": "offline"` → County hasn't reported in 24+ hours
- `"status": "stale"` → Last report 2-6 hours ago (possible issue)
- `"hours_since_update"` → How stale the data is

### Step 0.2 — Check Container Logs for Auto-Classified Errors
```bash
docker logs shamrock-leads --tail 200 | grep -E "❌|🚫|⚠️|AUTO-DISABLED"
```

The BaseScraper now classifies every error:
| Log Pattern | Error Type | Jump To |
|------------|-----------|---------|
| `AUTO-DISABLED` | 5+ consecutive failures | Phase 1A (Recovery) |
| `[anti_bot]` | 403 / CAPTCHA | Phase 2B |
| `[url_changed]` | 404 / redirect | Phase 2C |
| `[network]` | Connection/Timeout | Phase 2A |
| `[ssl_error]` | Certificate issue | Phase 2E |
| `[parse_error]` | HTML structure changed | Phase 2D |
| `[response_format_changed]` | JSON/API schema changed | Phase 2F |
| `[rate_limited]` | 429 Too Many Requests | Phase 2G |

### Step 0.3 — Check Failure History
```python
# In Python console or via API
scraper = LeeCountyScraper()
print(scraper.failure_history)  # Last 10 failures with timestamps + types
print(scraper.consecutive_failures)  # Current streak
print(scraper.is_disabled)  # Auto-disabled?
```

---

## Phase 1A: Recovery (Auto-Disabled Scraper)

If a scraper has been auto-disabled after 5 consecutive failures:

1. **Check the failure history** — what error type keeps recurring?
2. **Test the roster URL manually:**
   ```bash
   curl -I https://[roster-url]
   ```
3. **If URL works** — the issue was transient. Force re-enable:
   ```python
   scraper.force_enable()  # Resets consecutive_failures, clears is_disabled
   ```
4. **If URL is dead** — follow the appropriate Phase 2 section below
5. **After fixing**, the BaseScraper will auto-re-enable on next successful run

---

## Phase 1: Triage (< 2 minutes)

### Step 1.1 — Check the Error
```bash
docker logs shamrock-leads --tail 100 | grep -A5 "❌"
```

### Step 1.2 — Classify the Error

| Error Pattern | Category | Jump To |
|--------------|----------|---------|
| `ConnectionError` / `Timeout` | 🔌 Network | Phase 2A |
| `403 Forbidden` | 🛡️ Anti-bot | Phase 2B |
| `404 Not Found` | 🔗 URL Changed | Phase 2C |
| `KeyError` / `IndexError` | 📐 HTML Changed | Phase 2D |
| `SSLError` | 🔒 Certificate | Phase 2E |
| `JSONDecodeError` | 📄 Response Changed | Phase 2F |
| `429 Too Many Requests` | ⏱️ Rate Limited | Phase 2G |

---

## Phase 2A: Network Issues

1. **Test connectivity from the server:**
   ```bash
   curl -I https://[roster-url]
   ```
2. **Check if the county site is down** (try from a browser too)
3. **Check if our IP is blocked** (common with JailTracker)
4. **Fix**: If temporarily down, wait. If blocked, implement proxy rotation.
5. **Self-heal note**: BaseScraper retries 3x with exponential backoff (2s, 4s, 8s). If it still fails, it's a real outage.

## Phase 2B: Anti-Bot Detection

**Symptoms:** 403, CAPTCHA redirect, empty responses

1. **Rotate User-Agent:**
   ```python
   headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ..."}
   ```
2. **Add request delays** between pages (1-3 seconds)
3. **Check for Cloudflare** — look for `cf-ray` header
4. **Check for rate limiting** — reduce scrape frequency in scheduler
5. **Last resort**: Use `DrissionPage` (headless browser) instead of `requests`

**Common anti-bot patterns by vendor:**
- **JailTracker**: Rate limits after ~50 requests/min. Add 2s delay.
- **Odyssey**: Rarely blocks. If blocked, rotate UA.
- **Custom**: Varies wildly. Check `robots.txt` first.

## Phase 2C: URL Changed

**Symptoms:** 404, redirect to homepage

1. **Google the new URL:**
   ```
   site:[county]sheriff.org inmate search
   ```
2. **Check for subdomain changes** (e.g., `www.` added/removed)
3. **Check for HTTPS migration** (HTTP→HTTPS)
4. **Check for domain migration** (e.g., `sheriff.org` → `sheriff.gov`)
5. **Update the URL** in the scraper and test

**Common URL change patterns:**
| Pattern | Fix |
|---------|-----|
| HTTP → HTTPS | Update scheme |
| Added `www.` | Update hostname |
| New port (`:8443`) | Add port to URL |
| Domain changed | Search for new domain |
| Path restructured | Check sitemap or Google |

## Phase 2D: HTML Structure Changed

**Symptoms:** `KeyError`, `IndexError`, empty fields

1. **Fetch the raw HTML** and compare to what the scraper expects
2. **Look for:**
   - Changed CSS class names
   - Changed table column order
   - New wrapper div added
   - AJAX-loaded content replacing static HTML
3. **Update selectors** in the scraper's `_parse_page()` method

## Phase 2E: SSL Certificate Issues

**Symptoms:** `SSLError`, `CERTIFICATE_VERIFY_FAILED`

1. **Check cert expiry:**
   ```bash
   openssl s_client -connect [host]:443 2>/dev/null | openssl x509 -noout -dates
   ```
2. **If self-signed**: Add `verify=False` (with warning suppression)
3. **If expired**: Wait for county IT to fix, or use HTTP if available

## Phase 2F: Response Format Changed

**Symptoms:** `JSONDecodeError`, unexpected response body

1. **Check if API switched from JSON to HTML** (login page redirect)
2. **Check if JSON schema changed** (new field names, nested objects)
3. **Check for pagination changes** (different page params)

## Phase 2G: Rate Limited

**Symptoms:** 429 status code, `Retry-After` header

1. **Increase scrape interval** in `main.py` registration
2. **Add delay between page requests** in the scraper
3. **Check `Retry-After` header** for server-suggested wait time
4. **Consider off-peak scheduling** (scrape at 2-4 AM instead of peak hours)

---

## Phase 3: Fix & Verify

1. **Apply the fix** in `scrapers/counties/<county>.py`
2. **Test locally:**
   ```bash
   python main.py <county_name>
   ```
3. **Verify output** — check record count, data quality, lead scores
4. **Push & deploy:**
   ```bash
   git commit -m "fix: <county> scraper — <root cause>"
   git push
   # On Hetzner:
   docker-compose build && docker-compose up -d
   ```

---

## Phase 4: Post-Mortem

After fixing, update:
- [ ] `docs/COUNTY_REGISTRY.md` — if URL changed, update "Last Verified" date
- [ ] Error pattern in this skill's Knowledge Base — if new failure mode discovered
- [ ] Slack channel — confirm scraper is healthy
- [ ] `self-improving-agent` — log the fix pattern for future auto-diagnosis

---

## Common Root Causes (Knowledge Base)

| County | Date | Issue | Error Type | Fix |
|--------|------|-------|-----------|-----|
| Lee | 2026-04 | API empty response for `?type=ALL` | response_format_changed | Added variant queries |
| Charlotte | 2026-03 | HTTPS required (was HTTP) | url_changed | Updated URL scheme |
| Charlotte | 2026-04 | Revize CMS charge table headers don't match hardcoded selectors (`Statute`, `Bond Amt`) | parse_error | Universal table scanner — detect by content patterns (statute regex, $ amounts) |
| Charlotte | 2026-04 | Detail page shows person profile, charges on sub-arrest page | parse_error | Added sub-arrest link detection + click before extraction |
| Charlotte | 2026-04 | Cloudflare WAF blocking datacenter IPs on Revize CMS | anti_bot | `--disable-blink-features=AutomationControlled` + navigator.webdriver stealth |
| Hendry | 2026-04 | OCV SPA loads charges asynchronously — `time.sleep(2)` too short | parse_error | Dynamic polling loop (up to 15s) waiting for charge data in DOM |
| Osceola | 2026-04 | ASP.NET detail pages hydrate slower than 1s sleep | parse_error | Polling loop checking for HTML markers before extraction |
| Collier | 2026-04 | Sheriff roster has no bond amounts — data lives on Clerk of Court (ShowCaseWeb) | response_format_changed | Cannot fix with current scraper — needs Phase 2 Clerk enrichment worker |
| Taylor | 2026-04 | Non-standard port :8443 | url_changed | Updated URL with port |
| Dixie | 2026-04 | HTTP-only (HTTPS not supported) | ssl_error | Use HTTP |
| Sumter | 2026-04 | Redirect to HTTPS required | url_changed | Follow redirect |

*Add new entries as issues are resolved.*

---

## Self-Healing Decision Tree

```
Scraper Error Detected
    │
    ├─ Is it auto-disabled? (5+ consecutive failures)
    │   ├─ YES → Check failure_history for pattern
    │   │         ├─ Same error type every time → Root cause is persistent
    │   │         └─ Mixed error types → Transient issue, try force_enable()
    │   └─ NO → Continue to classification
    │
    ├─ Error type = "url_changed"?
    │   └─ YES → Google for new URL → Update scraper → Test → Deploy
    │
    ├─ Error type = "anti_bot"?
    │   └─ YES → Rotate UA → Add delays → Consider DrissionPage
    │
    ├─ Error type = "network" or "timeout"?
    │   └─ YES → BaseScraper already retried 3x → Check if county site is down
    │
    ├─ Error type = "parse_error"?
    │   └─ YES → HTML changed → Fetch raw page → Update selectors
    │
    └─ Error type = "ssl_error"?
        └─ YES → Check cert → Try HTTP fallback → Add verify=False
```
