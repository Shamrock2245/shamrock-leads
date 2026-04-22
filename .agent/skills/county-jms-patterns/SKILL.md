---
name: county-jms-patterns
description: JMS vendor reverse-engineering guide. Covers Odyssey, JailTracker, New World, and custom jail roster systems used across Florida counties.
---

# County JMS Patterns

> Reverse-engineering guide for Florida Jail Management Systems.

## When to Use
- Investigating a new county's jail roster system
- Debugging a scraper that broke due to JMS changes
- User says "what system does [county] use", "can't scrape [county]"
- Building a scraper for a county with an unknown JMS

---

## Vendor Identification Checklist

### Step 1: View Page Source
```bash
curl -s https://[roster-url] | head -100
```

### Step 2: Look for Fingerprints

| Fingerprint | Vendor |
|-------------|--------|
| `Tyler Technologies` in footer | Odyssey |
| `Powered by JailTracker` | JailTracker |
| `New World Systems` in meta | New World |
| `__VIEWSTATE` hidden field | ASP.NET (often New World) |
| `CentralSquare` in source | Superion |
| `api/inmates` JSON endpoint | Odyssey REST API |
| `inmateSearch.aspx` | New World |
| `JailTracker.com` in URL | JailTracker (hosted) |

### Step 3: Check Network Tab
Open DevTools → Network → search for inmates:
- JSON responses → REST API (likely Odyssey)
- HTML responses → Server-rendered (New World or Custom)
- XHR to JailTracker domain → Hosted JailTracker

---

## Odyssey (Tyler Technologies)

### Characteristics
- **Modern REST API** — JSON responses
- **Public inmate search** — no auth required
- **Pagination** — offset-based (`?page=1&size=50`)
- **Charges** — often separate endpoint per booking
- **Mugshots** — direct image URLs available

### API Patterns
```
GET /api/inmates?status=IN_CUSTODY&page=1&size=50
GET /api/inmates/{bookingId}/charges
GET /api/inmates/{bookingId}/mugshot
```

### Scraper Strategy
```python
def scrape(self):
    records = []
    page = 1
    while True:
        resp = requests.get(f"{self.base_url}/api/inmates", params={
            "status": "IN_CUSTODY",
            "page": page,
            "size": 50
        })
        data = resp.json()
        if not data.get("inmates"):
            break
        for inmate in data["inmates"]:
            charges = self._fetch_charges(inmate["bookingId"])
            records.append(self._to_record(inmate, charges))
        page += 1
    return records
```

### Known Odyssey Counties
Lee, Collier, Sarasota, Polk, Brevard, Escambia, Leon

---

## JailTracker (Black Creek ISC)

### Characteristics
- **HTML table rendering** — server-side rendered
- **Hosted or self-hosted** — URL varies
- **Pagination** — page links in HTML
- **Anti-bot** — occasional CAPTCHA, rate limiting
- **No API** — must scrape HTML

### URL Patterns
```
# Hosted
https://omsweb.public-safety-cloud.com/jtclientweb/jailtracker/index/[CountyID]

# Self-hosted
https://[county]sheriff.org/jailtracker
```

### Scraper Strategy
```python
from bs4 import BeautifulSoup

def scrape(self):
    records = []
    soup = BeautifulSoup(requests.get(self.url).text, "html.parser")
    rows = soup.select("table.inmateTable tbody tr")
    for row in rows:
        cells = row.find_all("td")
        records.append(ArrestRecord(
            First_Name=cells[1].text.strip(),
            Last_Name=cells[0].text.strip(),
            Booking_Number=cells[3].text.strip(),
            # ...
        ))
    return records
```

### Anti-Bot Countermeasures
1. Rotate User-Agent strings
2. Add 1-3 second delay between page requests
3. If CAPTCHA detected, switch to DrissionPage (headless browser)

### Known JailTracker Counties
DeSoto, Hendry, Pinellas, Citrus, Martin, St. Lucie, Indian River, Marion, Putnam, Highlands, Glades

---

## New World (Tyler Technologies)

### Characteristics
- **ASP.NET WebForms** — ViewState, postbacks
- **HTML tables** — server-rendered
- **Pagination** — postback-based (tricky)
- **No API** — HTML scraping required

### Scraper Strategy
```python
def scrape(self):
    session = requests.Session()
    # First GET to obtain ViewState
    page = session.get(self.url)
    soup = BeautifulSoup(page.text, "html.parser")
    viewstate = soup.find("input", {"name": "__VIEWSTATE"})["value"]
    
    # POST with ViewState to paginate
    # ... ASP.NET postback pattern
```

### Known New World Counties
Manatee, Hillsborough

---

## Custom / In-House Systems

### Characteristics
- **Every county is different** — no standard pattern
- **Simple HTML** — often basic tables
- **No pagination** — all records on one page (small counties)
- **Fragile** — can change without notice

### Strategy
1. Use `requests` + `BeautifulSoup` for simple HTML
2. Use `DrissionPage` for JavaScript-heavy sites
3. Fall back to `regex` for very irregular HTML

### Known Custom Counties
Charlotte, Pasco, Hernando, Palm Beach, Broward, Alachua, Volusia, Seminole, Orange, Duval, many others

---

## DrissionPage (Headless Browser) Fallback

When `requests` won't cut it (JavaScript rendering, anti-bot):

```python
from DrissionPage import ChromiumPage

def scrape(self):
    page = ChromiumPage()
    page.get(self.url)
    
    # Wait for table to load
    page.wait.ele_loaded("table.inmateTable")
    
    rows = page.eles("table.inmateTable tbody tr")
    records = []
    for row in rows:
        cells = row.eles("td")
        records.append(ArrestRecord(
            First_Name=cells[1].text,
            Last_Name=cells[0].text,
            # ...
        ))
    
    page.quit()
    return records
```

**Note:** DrissionPage requires Chrome/Chromium installed in the Docker container.
Add to Dockerfile:
```dockerfile
RUN apt-get install -y chromium chromium-driver
```
