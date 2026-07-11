---
name: county-jms-patterns
description: JMS vendor reverse-engineering guide. Covers Odyssey, JailTracker, New World, EAS, Zuercher, and custom jail roster systems used across Florida and Georgia counties.
---

# County JMS Patterns

> Reverse-engineering guide for Florida and Georgia Jail Management Systems.

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
| `offenderindex.com` | Eagle Advantage Solutions (EAS) |
| `Zuercher Technologies` | Zuercher Portal |
| `Southern Software` | Southern Software Citizen Connect |
| `SmartWebClient` | P2C (CentralSquare) |
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

## Eagle Advantage Solutions (EAS)

### Characteristics
- **Hosted Platform** — `offenderindex.com`
- **Widespread in GA** — Used by 27+ Georgia counties
- **Simple HTML** — Easy to parse tables
- **Predictable URLs** — `https://offenderindex.com/[county]coga/`

### Scraper Strategy
- Use the `EASBaseScraper` class
- All EAS counties can be run efficiently in a single batch process using `eas_batch_runner.py`

### Known EAS Counties
Bacon, Barrow, Bryan, Bulloch, Camden, Carroll, Columbia, Coweta, Dawson, Effingham, Elbert, Emanuel, Gordon, Habersham, Haralson, Jackson, Jones, Laurens, Lumpkin, Monroe, Morgan, Newton, Paulding, Troup, Upson, Walker, Ware

---

## Zuercher Technologies

### Characteristics
- **Single Page Application (SPA)** — React/Angular frontend
- **JSON API** — Hidden behind the SPA
- **CSRF Tokens** — Requires extracting a token from the initial HTML load
- **POST Requests** — API requires specific POST payloads

### Scraper Strategy
- Use the `ZuercherBaseScraper` class
- Fetch initial HTML to extract CSRF token
- Send POST request to `/api/PublicPortal/Inmates/Search`

### Known Zuercher Counties
Douglas, Houston, Floyd, Catoosa

---

## Southern Software Citizen Connect

### Characteristics
- **ASP.NET Application**
- **Query String Routing** — `AgencyID` parameter controls which county is shown
- **Predictable HTML** — Table structure is consistent across counties
- **Pagination** — Required for larger counties

### Scraper Strategy
- Use the `SouthernSWBaseScraper` class
- Extract data from the standard `.grid` tables

### Known Southern Software Counties
Banks, Decatur, Lee, Oglethorpe

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
Orange, Pasco, Hernando, Broward, Alachua, Volusia, Seminole, Duval, many others

---

## Revize CMS (County-Hosted)

### Characteristics
- **Server-rendered HTML** — Django/CMS-like templates
- **Cloudflare protected** — requires stealth flags
- **Two-level detail navigation** — roster → person profile → arrest detail
- **Variable table headers** — `Statute`/`Charge`/`Bond Amt` labels vary per installation
- **Bond data on sub-page** — charge/bond table often on a separate arrest detail page

### Navigation Pattern
```
Roster page (list) → Person detail page → Click most recent arrest → Arrest detail page (charges + bonds)
```

### Scraper Strategy
```python
def _extract_detail(self, page, url):
    page.get(url)
    time.sleep(2)
    
    # Check if arrest history table exists (two-level navigation needed)
    has_arrests = page.run_js('''
        const tables = document.querySelectorAll('table');
        for (const t of tables) {
            const headers = [...t.querySelectorAll('th, thead td')].map(h => h.textContent.toLowerCase());
            if (headers.some(h => h.includes('arrest') || h.includes('book'))) {
                const links = t.querySelectorAll('a[href]');
                if (links.length > 0) { links[0].click(); return true; }
            }
        }
        return false;
    ''')
    if has_arrests:
        time.sleep(2)  # Wait for sub-page
    
    # Universal table scanner — detect by content, not headers
    data = page.run_js('''
        const tables = document.querySelectorAll('table');
        for (const t of tables) {
            const text = t.textContent;
            if (/\\$[\\d,]+/.test(text) || /\\d{3}\\.\\d{2}/.test(text)) {
                // Found charge/bond table by content pattern
                return extract_table_data(t);
            }
        }
    ''')
```

### Known Revize CMS Counties
Charlotte, Manatee, Sarasota

---

## DrissionPage (Headless Browser) Fallback

When `requests` won't cut it (JavaScript rendering, anti-bot):

```python
from DrissionPage import ChromiumPage, ChromiumOptions

def _get_stealth_page(self):
    """Create a stealth ChromiumPage that bypasses Cloudflare."""
    opts = ChromiumOptions()
    opts.set_argument('--disable-blink-features=AutomationControlled')
    opts.set_argument('--no-sandbox')
    opts.set_argument('--disable-dev-shm-usage')
    opts.set_argument('--headless=new')
    page = ChromiumPage(addr_or_opts=opts)
    # Override navigator properties
    page.run_js('Object.defineProperty(navigator, "webdriver", {get: () => false})')
    return page

def scrape(self):
    page = self._get_stealth_page()
    page.get(self.url)
    
    # Content-based wait (preferred over time.sleep):
    for _ in range(15):  # Max 15 seconds
        time.sleep(1)
        if page.run_js("return document.querySelector('table.inmateTable') !== null"):
            break
    
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

### Cloudflare Stealth Checklist
1. `--disable-blink-features=AutomationControlled`
2. Override `navigator.webdriver` → `false`
3. Random/realistic User-Agent string
4. Wait 30s+ for Cloudflare challenge on first visit
5. Establish referer chain (visit main site first, then roster)

### Content-Based Wait Pattern
Prefer polling for expected content over fixed `time.sleep()`:
```python
for _ in range(max_seconds):
    time.sleep(1)
    if page.run_js("return document.querySelector('expected_selector') !== null"):
        break
```
This avoids both under-waiting (data not loaded) and over-waiting (wasted time).

**Note:** DrissionPage requires Chrome/Chromium installed in the Docker container.
Add to Dockerfile:
```dockerfile
RUN apt-get install -y chromium chromium-driver
```
