# Add County Scraper Workflow

> Add a new Florida county scraper from zero to production.

## Prerequisites
- County name and roster URL identified
- JMS vendor known (see `.agent/skills/county-jms-patterns/SKILL.md`)

---

## Steps

### 1. Recon the Roster
// turbo
```bash
curl -sI https://[ROSTER_URL] | head -20
```
Identify response type (HTML, JSON, redirect) and status code.

### 2. Create Scraper File
Copy the closest template:
```bash
cp scrapers/counties/lee.py scrapers/counties/<county>.py
```

### 3. Implement the Scraper
- Update `county` property
- Update `scrape()` method with county-specific parsing
- Handle pagination
- Handle charge extraction

### 4. Test Locally
// turbo
```bash
python main.py --county <county_name> --once
```

### 5. Verify Data Quality
Check that:
- Records have `First_Name`, `Last_Name`, `Booking_Number`
- `Bond_Amount` parses correctly
- `Lead_Score` is being calculated
- No duplicate records on re-run

### 6. Register in Scheduler
Add to `core/scheduler.py` COUNTY_CONFIGS dict.

### 7. Update County Registry
Mark as ✅ Active in `docs/COUNTY_REGISTRY.md`.

### 8. Commit & Deploy
```bash
git add scrapers/counties/<county>.py core/scheduler.py docs/COUNTY_REGISTRY.md
git commit -m "feat: add <county> county scraper"
git push
```

### 9. Deploy to Hetzner
```bash
ssh shamrock-hetzner "cd /opt/shamrock-leads && git pull && docker-compose build && docker-compose up -d"
```

### 10. Monitor First Run
```bash
ssh shamrock-hetzner "docker logs shamrock-leads --tail 50"
```
Verify scraper runs successfully and Slack alerts fire.
