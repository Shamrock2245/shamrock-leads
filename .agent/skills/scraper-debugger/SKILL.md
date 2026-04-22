---
name: scraper-debugger
description: Systematic debugging for when a county scraper breaks. Covers URL changes, anti-bot blocks, HTML structure changes, and timeout issues.
---

# Scraper Debugger

> When a scraper stops returning data, follow this skill BEFORE guessing.

## When to Use
- A county scraper returns 0 records unexpectedly
- Slack fires a `#scraper-errors` alert
- MongoDB shows stale data for a county (no new records in 2+ intervals)
- User says "Lee scraper is broken", "no data from Charlotte", etc.

## The Iron Rule

```
DO NOT CHANGE SCRAPER CODE UNTIL YOU IDENTIFY THE ROOT CAUSE.
```

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

---

## Phase 2A: Network Issues

1. **Test connectivity from the server:**
   ```bash
   curl -I https://[roster-url]
   ```
2. **Check if the county site is down** (try from a browser too)
3. **Check if our IP is blocked** (common with JailTracker)
4. **Fix**: If temporarily down, wait. If blocked, implement proxy rotation.

## Phase 2B: Anti-Bot Detection

**Symptoms:** 403, CAPTCHA redirect, empty responses

1. **Rotate User-Agent:**
   ```python
   headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ..."}
   ```
2. **Add request delays** between pages (1-3 seconds)
3. **Check for Cloudflare** — look for `cf-ray` header
4. **Check for rate limiting** — reduce scrape frequency
5. **Last resort**: Use `DrissionPage` (headless browser) instead of `requests`

## Phase 2C: URL Changed

**Symptoms:** 404, redirect to homepage

1. **Google the new URL:**
   ```
   site:[county]sheriff.org inmate search
   ```
2. **Check for subdomain changes** (e.g., `www.` added/removed)
3. **Check for HTTPS migration** (HTTP→HTTPS)
4. **Update the URL** in the scraper and test

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

---

## Phase 3: Fix & Verify

1. **Apply the fix** in `scrapers/counties/<county>.py`
2. **Test locally:**
   ```bash
   python main.py --county <county> --once
   ```
3. **Verify output** — check record count, data quality, lead scores
4. **Push & deploy:**
   ```bash
   git commit -m "fix: <county> scraper — <root cause>"
   docker-compose build && docker-compose up -d
   ```

---

## Phase 4: Post-Mortem

After fixing, update:
- [ ] `docs/COUNTY_REGISTRY.md` — if URL changed
- [ ] Error pattern in this skill — if new failure mode discovered
- [ ] Slack channel — confirm scraper is healthy

---

## Common Root Causes (Knowledge Base)

| County | Date | Issue | Fix |
|--------|------|-------|-----|
| Lee | 2026-04 | API empty response for `?type=ALL` | Added variant queries |
| Charlotte | 2026-03 | HTTPS required (was HTTP) | Updated URL scheme |
| Taylor | 2026-04 | Non-standard port :8443 | Updated URL with port |
| Dixie | 2026-04 | HTTP-only (HTTPS not supported) | Use HTTP |
| Sumter | 2026-04 | Redirect to HTTPS required | Follow redirect |

*Add new entries as issues are resolved.*
