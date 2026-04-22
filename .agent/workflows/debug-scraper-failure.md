# Debug Scraper Failure Workflow

> When a county scraper breaks, follow these steps IN ORDER.

## Trigger
- Slack `#scraper-errors` alert
- User reports stale data for a county
- `docker logs` shows scraper errors

---

## Steps

### 1. Identify the Error
```bash
docker logs shamrock-leads --tail 100 | grep -B2 -A5 "❌"
```

### 2. Classify
Look at the error message and classify:
- **Network** (ConnectionError, Timeout): Check if county site is up
- **Anti-bot** (403, CAPTCHA): Rotate UA, add delays
- **URL changed** (404): Find new URL
- **HTML changed** (KeyError, IndexError): Update selectors
- **SSL** (SSLError): Check certificate

### 3. Test Connectivity
```bash
curl -sI "https://[COUNTY_ROSTER_URL]" | head -5
```

### 4. Reproduce Locally
```bash
python main.py --county <county_name> --once
```

### 5. Apply Fix
Edit `scrapers/counties/<county>.py` based on root cause.

### 6. Verify Fix
```bash
python main.py --county <county_name> --once
```
Must show `✅` with records scraped.

### 7. Deploy
```bash
git add scrapers/counties/<county>.py
git commit -m "fix: <county> scraper — <root cause>"
git push
ssh shamrock-hetzner "cd /opt/shamrock-leads && git pull && docker-compose build && docker-compose up -d"
```

### 8. Post-Mortem
Update `.agent/skills/scraper-debugger/SKILL.md` with the new failure pattern.
