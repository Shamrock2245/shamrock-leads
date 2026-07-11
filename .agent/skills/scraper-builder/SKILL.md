---
name: scraper-builder
description: Step-by-step guide to add a new county scraper to ShamrockLeads. Use when adding any of the 67 Florida or 159 Georgia counties (Currently 100 active).
---

# Scraper Builder

> Build a new county scraper from recon to production in one session.

## When to Use
- Adding a new county scraper
- Porting a scraper from the legacy `swfl-arrest-scrapers` repo
- User says "add [county name]", "build scraper for [county]", "enable [county]"

## Prerequisites
- County roster URL (from `docs/COUNTY_REGISTRY.md` or `docs/GEORGIA_COUNTY_REGISTRY.md`)
- JMS vendor identified (Odyssey, JailTracker, New World, Custom)

---

## Phase 1: Reconnaissance

### Step 1.1 — Find the Roster
```bash
# Search for the county sheriff's inmate search
# Common URL patterns:
# - https://www.[county]sheriff.org/inmate-search
# - https://[county]so.org/inmate-search
# - https://jailtracker.com/[county]fl
# - https://[county].sheriff.lcso.org  (Odyssey)
```

### Step 1.2 — Identify the JMS Vendor
Open browser DevTools → Network tab → look for:
- **Odyssey**: API calls to `/api/inmates` or `/api/bookings` returning JSON
- **JailTracker**: HTML tables with class `jailtracker-table` or `inmateTable`
- **New World**: HTML tables with ASP.NET ViewState
- **Custom**: Any other pattern

### Step 1.3 — Map the Data Fields
Create a mapping from the source fields to our 39-column ArrestRecord:

```python
# Example: Odyssey API mapping
field_map = {
    "firstName": "First_Name",
    "lastName": "Last_Name",
    "dateOfBirth": "Date_of_Birth",
    "bookingNumber": "Booking_Number",
    "bookingDate": "Booking_Date",
    "charges": "Charges",  # needs transformation
    "totalBond": "Bond_Amount",
}
```

---

## Phase 2: Build the Scraper

### Step 2.1 — Create the File
```bash
# File: scrapers/counties/<county_name>.py
# Use lowercase, underscore-separated
# Example: scrapers/counties/palm_beach.py
```

### Step 2.2 — Copy the Template
Choose the closest template based on JMS vendor:

| Vendor | Template |
|--------|----------|
| Odyssey | `scrapers/counties/lee.py` |
| JailTracker | `scrapers/counties/hendry.py` |
| New World | `scrapers/counties/manatee.py` |
| Zuercher | `scrapers/counties_ga/douglas.py` |
| Southern Software | `scrapers/counties_ga/banks.py` |
| Custom | `scrapers/base_scraper.py` (start fresh) |

### Step 2.2b — Stealth Package Selection (When DrissionPage Fails)

If a county blocks DrissionPage, try these alternatives **in order of preference**:

| Package | Use Case | Install |
|---------|----------|---------|
| **curl_cffi** | TLS/JA3 fingerprint impersonation, fastest | `pip install curl_cffi` |
| **Stealth-Requests** | High-level curl_cffi wrapper, simplest API | `pip install stealth-requests` |
| **nodriver** | Full stealth headless browser (replaces undetected-chromedriver) | `pip install nodriver` |
| **patchright-python** | Undetected Playwright patch, most feature-rich | `pip install patchright` |

### Step 2.2c — Mandatory ArrestRecord Fields

**Every new scraper MUST populate these fields:**

| Field | Default | Notes |
|-------|---------|-------|
| `Detail_URL` | Scraper's `BASE_URL` | Link to county inmate search. Use individual detail page URL if available. |
| `DOB` | `""` | Extract from API/HTML if available. Safe to leave empty. |
| `Release_Date` | `""` | Extract from API/HTML. If populated, set `Status="Released"`. |
| `Status` | Dynamic | `"Released"` if `Release_Date` is set, otherwise `"In Custody"`. **Never hardcode.** |

### Step 2.3 — Implement Required Methods

```python
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

class [County]Scraper(BaseScraper):
    
    @property
    def county(self) -> str:
        return "[County Name]"  # Title case, no "County" suffix
    
    def scrape(self) -> list[ArrestRecord]:
        # 1. Fetch data from roster URL
        # 2. Parse into ArrestRecord instances
        # 3. Return list
        ...
```

### Step 2.4 — Handle Pagination
Most rosters are paginated. Implement a loop:

```python
def scrape(self):
    records = []
    page = 1
    while True:
        data = self._fetch_page(page)
        if not data:
            break
        records.extend(self._parse_page(data))
        page += 1
    return records
```

### Step 2.5 — Handle Charges
Charges are the trickiest field. Common patterns:
- Single string: `"BATTERY - DOMESTIC VIOLENCE; DUI"`
- Array of objects: `[{"description": "Battery", "statute": "784.03"}]`
- Separate API call needed (Odyssey)

Always output as semicolon-delimited string.

---

## Phase 3: Register & Test

### Step 3.1 — Register in Scheduler
Add to `core/scheduler.py`:

```python
from scrapers.counties.<county> import <County>Scraper

COUNTY_CONFIGS = {
    # ...existing...
    "<county>": {
        "scraper": <County>Scraper,
        "interval_minutes": 30,  # Adjust based on volume
        "enabled": True,
    },
}
```

### Step 3.2 — Test Locally
```bash
python main.py --county <county_name> --once
```

Expected output:
```
═══════════════════════════════════════
🚦 Starting [County] County scraper (run #1)
═══════════════════════════════════════
✅ [County]: scraped 47 records in 8.3s
📊 [County]: Scored → 🔥 3 Hot | 🟡 12 Warm | ❌ 28 Disqualified
```

### Step 3.3 — Validate Data Quality
Check the first few records:
```python
# In test mode, verify:
# - No null First_Name or Last_Name
# - Booking_Number is populated
# - Bond_Amount parses to a number
# - Lead_Score is 0-100
# - County field matches
```

---

## Phase 4: Deploy

### Step 4.1 — Update County Registry
Mark the county as ✅ Active in `docs/COUNTY_REGISTRY.md` or `docs/GEORGIA_COUNTY_REGISTRY.md`

### Step 4.2 — Push & Deploy
```bash
git add scrapers/counties/<county>.py
git commit -m "feat: add <county> county scraper"
git push

# On Hetzner:
docker-compose build && docker-compose up -d
```

### Step 4.3 — Monitor First Run
Watch logs for 2 full intervals to confirm stability.

---

## Checklist

- [ ] Roster URL confirmed
- [ ] JMS vendor identified
- [ ] Field mapping complete
- [ ] Scraper class created
- [ ] Pagination handled
- [ ] Charges parsed correctly
- [ ] Registered in scheduler
- [ ] Local test passed
- [ ] MongoDB writes verified
- [ ] Slack alerts firing
- [ ] County Registry updated
- [ ] Committed and deployed
